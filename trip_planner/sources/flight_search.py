"""The flight search. Every departure airport is searched against every arrival airport on every date, and flight
data is always copied from the source by code, never retyped by a model.

Two sources, each in its own module and answering in the same shape: Kiwi first (kiwi.py), and Google Flights
(google_flights.py) when Kiwi is not available or does not answer. What they find is kept here, checked against
the traveller's limits."""
import asyncio
from datetime import date
from typing import Any

from trip_planner import current
from trip_planner.sources import google_flights, kiwi
from trip_planner.texts import tr
from trip_planner.utils import logged, say, to_minutes, to_time

async def connect() -> None:
    """Connect to Kiwi if that was not done yet. Without it the flights come from Google Flights."""
    if kiwi.kiwi_search is None:
        await kiwi.connect_kiwi()
        if kiwi.kiwi_search is None:
            say(tr("kiwi_unavailable", backup=google_flights.NAME))

# Every flight found, by flight id. Flight data is always copied from the source by code and never retyped by a model.
FLIGHTS: dict[str, dict[str, Any]] = {}
SEARCHED: set = set()            # (departure airport, arrival airport, yyyy-mm-dd) a source answered for
FLIGHT_GAPS: list[str] = []      # what may be missing from the flight list, for the report
MAX_SPLIT_SEARCHES = 12          # most searches one departure airport is split into, by arrival airport and date

PROBE_STOPOVERS = 2   # stopovers allowed when asking Kiwi what exists without the traveller's limits

def flight_problems(leg: dict[str, Any]) -> list[str]:
    """Which of the traveller's limits a Kiwi flight breaks. Empty if the flight can be used."""
    settings, problems = current.SETTINGS, []
    departure_date, departure_time = leg["departureTime"][:10], leg["departureTime"][11:16]
    arrival_date, arrival_time = leg["arrivalTime"][:10], leg["arrivalTime"][11:16]
    if leg["stops"] > settings.max_stopovers:
        problems.append("stopovers")
    if to_minutes(departure_time) < settings.earliest_flight_minutes:
        problems.append("too_early")    # cannot be caught: earliest time leaving plus the minimum time at the airport
    if arrival_date != departure_date and not settings.next_day_arrivals:
        problems.append("next_day")     # lands on a later day, and the traveller does not accept that
    elif settings.latest_arrival and to_minutes(arrival_time) > settings.latest_arrival_minutes:
        problems.append("too_late")     # lands after the time the traveller must already be at the destination
    return problems

def register_flight(itinerary: dict[str, Any], closest: bool = False) -> dict[str, Any] | None:
    """Copy one Kiwi result into FLIGHTS. Returns the flight, or None if it breaks one of the traveller's limits.
    closest=True also keeps a flight that breaks them, for the closest alternatives, unless it lands on a later
    day and the traveller does not accept that."""
    leg = itinerary["outbound"]
    departure_date, departure_time = leg["departureTime"][:10], leg["departureTime"][11:16]
    arrival_date, arrival_time = leg["arrivalTime"][:10], leg["arrivalTime"][11:16]
    days_later = (date.fromisoformat(arrival_date) - date.fromisoformat(departure_date)).days
    problems = flight_problems(leg)
    if departure_date not in current.SETTINGS.iso_dates or "next_day" in problems or (problems and not closest):
        return None
    if not current.TRIP.airport(leg["from"]) or not current.TRIP.airport(leg["to"]):
        return None   # Kiwi reads a code as its city: asked for BRU it also answers with CRL, which is not in the trip
    duration_minutes = leg["durationSeconds"] // 60
    flight_id = f"{leg['from']}-{leg['to']}-{departure_date[8:10]}{departure_date[5:7]}-{departure_time.replace(':', '')}"
    flight = {
        "id": flight_id,
        "date": departure_date,
        "departure_airport": leg["from"],
        "departure_time": departure_time,
        "arrival_airport": leg["to"],
        "arrival_time": arrival_time,
        # Everything at the destination is counted on the clock of the day the flight lands
        "arrival_date": arrival_date,
        "arrival_day_offset": days_later,
        "stops": leg["stops"],
        "route": " - ".join(leg["route"]),
        "flights": " + ".join(f"{s['carrierName']} {s['flightNumber']}" for s in leg["segments"]),
        "airline": leg["segments"][0]["carrierName"],
        "carrier": leg["segments"][0]["carrier"],
        "flight_number": leg["segments"][0]["flightNumber"],
        "duration_minutes": duration_minutes,
        # How far the departure clock is ahead of the arrival clock, deduced from the local times
        "timezone_shift_minutes": duration_minutes - (to_minutes(arrival_time) + days_later * 24 * 60 - to_minutes(departure_time)),
        "price_eur": float(itinerary["price"]),
        "cabin_bag_included": bool((itinerary.get("baggage") or {}).get("cabinBag")),
        "booking_url": itinerary["bookingUrl"],
        "source": itinerary.get("source", kiwi.NAME),
    }
    if flight_id not in FLIGHTS or FLIGHTS[flight_id]["price_eur"] > flight["price_eur"]:
        FLIGHTS[flight_id] = flight
    return FLIGHTS[flight_id]

async def search(fly_from: str, fly_to: str, first_day: str, last_day: str,
                       stopovers: int | None = None) -> list[dict[str, Any]] | None:
    """One flight search. fly_to may list several airports, the days are yyyy-mm-dd. Returns None if nobody answered.
    Giving stopovers asks for what exists at any time of the day with at most that many stopovers, instead of
    what fits the traveller's limits.
    Kiwi is asked first. If it is not available or does not answer, the same is asked of Google Flights."""
    route = f"{fly_from} -> {fly_to}, {first_day}" + (f" to {last_day}" if last_day != first_day else "")
    answer = lambda found: "no answer" if found is None else f"ok: {len(found)} flights"
    found = None
    if kiwi.kiwi_search is not None:
        with logged(kiwi.NAME, route) as entry:
            found = await kiwi.ask_kiwi(fly_from, fly_to, first_day, last_day, stopovers)
            entry["result"] = answer(found)
    if found is None:
        days = [day for day in current.SETTINGS.iso_dates if first_day <= day <= last_day] or [first_day]
        with logged(google_flights.NAME, route) as entry:
            found = await google_flights.flights(fly_from, fly_to.split(","), days,
                                                 current.SETTINGS.max_stopovers if stopovers is None else stopovers)
            entry["result"] = answer(found)
    return found

async def search_airport_flights(departure: str, arrivals: list[str], days: list[str]) -> None:
    """Every flight from one departure airport. Starts with one search for all arrival airports and all dates.
    Kiwi returns at most kiwi.RESULT_CAP flights per search, so a full answer is split into smaller searches
    until nothing can have been cut off.
    With many arrival airports that is dozens of searches per departure airport, and Kiwi refuses a client that
    asks that much. Then the split is by date only. A full answer for a date holds the cheapest flights of that
    date, and only the cheapest few per airport and date are looked at further (see timetables.flights_to_check)."""
    # An answer from Google Flights was asked route by route: nothing was cut, and some routes may be missing
    backup = lambda found: isinstance(found, google_flights.FullAnswer)
    missed: set = set()
    found = await search(departure, ",".join(arrivals), min(days), max(days))
    searches = [(arrivals, days)] if found is not None and (backup(found) or len(found) < kiwi.RESULT_CAP) else []
    if backup(found):
        missed.update(found.missing)
    groups = [[arrival] for arrival in arrivals] if len(arrivals) * len(days) <= MAX_SPLIT_SEARCHES else [arrivals]
    if not searches and found is not None and len(groups) == 1 and len(days) == 1:
        # Nothing smaller to ask for: the same search again would give the same answer
        FLIGHT_GAPS.append(tr("gap_capped", route=f"{departure} -> {','.join(arrivals)}", day=days[0], cap=kiwi.RESULT_CAP))
        searches.append((arrivals, days))
    elif not searches:
        # Failed or possibly cut off: one search per arrival airport (or for all of them) and per date
        for group in groups:
            for day in days:
                route = f"{departure} -> {','.join(group)}"
                part = await search(departure, ",".join(group), day, day)
                if part is None:
                    FLIGHT_GAPS.append(tr("gap_no_answer", route=route, day=day))
                    continue
                if backup(part):
                    missed.update(part.missing)
                elif len(part) >= kiwi.RESULT_CAP:
                    FLIGHT_GAPS.append(tr("gap_capped", route=route, day=day, cap=kiwi.RESULT_CAP))
                found = list(found or []) + part
                searches.append((group, [day]))
    for itinerary in found or []:
        register_flight(itinerary)
    FLIGHT_GAPS.extend(tr("gap_no_answer", route=f"{departure} -> {arrival}", day=day) for arrival, day in sorted(missed))
    for searched_arrivals, searched_days in searches:
        SEARCHED.update((departure, arrival, day) for arrival in searched_arrivals for day in searched_days
                        if (arrival, day) not in missed)

async def search_all_flights() -> None:
    """Search every departure airport against every arrival airport on every date, in code."""
    arrivals = [airport["code"] for airport in current.TRIP.arrival]
    await asyncio.gather(*(search_airport_flights(airport["code"], arrivals, current.SETTINGS.iso_dates) for airport in current.TRIP.departure))

async def why_no_flights() -> tuple:
    """Called when no flight fits the traveller's limits. Asks Kiwi again without them and says, in Markdown and
    in the traveller's language, which limits the flights that exist break. A model could only guess this.
    Returns that text and the flights Kiwi answered with, as it returns them."""
    settings = current.SETTINGS
    stops = max(settings.max_stopovers, PROBE_STOPOVERS)
    arrivals = ",".join(airport["code"] for airport in current.TRIP.arrival)
    # Kiwi returns the cheapest flights of each search, and those are the ones with stopovers. So each airport is
    # asked twice: with the stopovers the traveller accepts, to see what the time limits cut, and with a few more.
    answers = await asyncio.gather(*(
        search(airport["code"], arrivals, min(settings.iso_dates), max(settings.iso_dates), stopovers=allowed)
        for airport in current.TRIP.departure for allowed in sorted({settings.max_stopovers, stops})))
    lines = [f"## {tr('no_flights_title')}", ""]
    if all(answer is None for answer in answers):
        return "\n".join(lines + [tr("no_flights_unknown")]), []
    legs = {(leg["from"], leg["to"], leg["departureTime"], leg["arrivalTime"], leg["stops"]): leg
            for answer in answers for leg in (itinerary["outbound"] for itinerary in answer or [])
            if leg["departureTime"][:10] in settings.iso_dates}   # the same flight is sold at several prices
    broken = [problems for problems in map(flight_problems, legs.values()) if problems]
    if not broken:
        return "\n".join(lines + [tr("no_flights_none", stops=stops)]), []
    lines += [tr("no_flights_intro", stops=stops, count=len(broken)), ""]
    details = {
        "stopovers": {"maximum": settings.max_stopovers},
        "too_early": {"time": to_time(settings.earliest_flight_minutes), "earliest": settings.earliest_leave,
                      "minutes": settings.min_airport_minutes},
        "too_late": {"latest": settings.latest_arrival},
        "next_day": {},
    }
    for problem, values in details.items():
        count = sum(problem in problems for problems in broken)
        if count:
            # A flight that breaks only this limit comes back when this limit alone is relaxed
            alone = sum(problems == [problem] for problems in broken)
            lines.append(f"- {tr('no_flights_' + problem, count=count, **values)}; {tr('no_flights_alone', alone=alone)}")
    return "\n".join(lines), [itinerary for answer in answers for itinerary in answer or []]

def describe_flight(flight: dict[str, Any]) -> str:
    """One line with everything a specialist needs to know about a flight."""
    return (
        f"{flight['id']} | {flight['date']} | {flight['departure_airport']} {flight['departure_time']} -> "
        f"{flight['arrival_airport']} {flight['arrival_time']}{' on ' + flight['arrival_date'] if flight['arrival_day_offset'] else ''} (local times) | route {flight['route']} | "
        f"{flight['stops']} stops | {flight['duration_minutes']} min | {flight['price_eur']:.0f} EUR"
    )
