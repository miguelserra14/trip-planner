"""The cheapest destinations of the map checked in the real timetables, with the planner's own code: the ways to
and from the airports of each flight, at the hour of that flight (sources/timetables/, fares.py, legs.py). No model is
asked: what no timetable covers keeps its estimate, grade C.

A way checked can end three ways:
- found: a real connection fits the flight. Its price and times replace the estimate, with their grades, and it is
  remembered for the next maps (access.py);
- impossible: the timetables cover the way and nothing fits the flight. The flight is left out of the map,
  whatever the estimate said, as the planner leaves it out;
- not covered: no timetable knows the way. The estimate stays.

Looking up is slow: the timetables answer about one request every 1.4 seconds, shared with the planner. So the
cities are looked up one at a time, and only the airports that can still change the map's cheapest TOP
destinations, as the planner looks up only the flights that can still win (itineraries/selection.py):
- a destination not looked up yet costs at least its flight, plus for each way the cheapest seen looked up for
  that airport and side, or nothing when none was;
- it is looked up when that least is below both the dearest of the TOP cheapest destinations looked up, and the
  cheapest way into its own city looked up.
The lookups stop when no destination is left that can, or after MAX_LOOKUPS cities. The one assumption, stated
because the result is only as sure as it: no way to an airport is cheaper than the cheapest seen for that airport."""
import dataclasses
import re
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from discover import access, transfers
from discover import finder
from discover.finder import Found, one_per_city, options_of, with_access
from trip_planner import current
from trip_planner.itineraries import legs
from trip_planner.trip.places import airport_from, load_airports, Place, TripPlaces
from trip_planner.sources import timetables
from trip_planner.utils import plain, say, to_minutes, to_time

TOP = 10              # the cheapest destinations of the map that are made sure of...
MAX_LOOKUPS = 15      # ... looking up at most this many cities: each one takes a minute or two of timetables
MAX_PER_CITY = 4      # airports of one city looked up at once

def zone_of(code: str, airports: dict[str, Any]) -> str:
    airport = airports.get(code) or {}
    return timetables.zone_of_country(airport.get("country", ""), airport.get("latitude", 0.0), airport.get("longitude", 0.0)) if airport else ""

def planner_flight(leg: dict[str, Any], price: float, airports: dict[str, Any]) -> dict[str, Any] | None:
    """One flight of the map as the planner describes it (flight_search.register_flight), or None when its times
    are not all known. Ryanair does not give the duration: it is worked out from the two airports' time zones."""
    if not leg.get("arrival_date") or not leg.get("arrival_time"):
        return None
    leaves = datetime.fromisoformat(f"{leg['date']}T{leg['time']}")
    lands = datetime.fromisoformat(f"{leg['arrival_date']}T{leg['arrival_time']}")
    minutes = leg.get("minutes") or 0
    if not minutes:
        start, end = zone_of(leg["from"], airports), zone_of(leg["to"], airports)
        if not start or not end:
            return None
        minutes = round((lands.replace(tzinfo=ZoneInfo(end)) - leaves.replace(tzinfo=ZoneInfo(start))).total_seconds() / 60)
    if minutes <= 0:
        return None
    days_later = (lands.date() - leaves.date()).days
    on_the_clocks = to_minutes(leg["arrival_time"]) + days_later * 24 * 60 - to_minutes(leg["time"])
    return {"id": f"{leg['from']}-{leg['to']}-{leg['date'][8:10]}{leg['date'][5:7]}-{leg['time'].replace(':', '')}",
            "date": leg["date"], "departure_airport": leg["from"], "departure_time": leg["time"],
            "arrival_airport": leg["to"], "arrival_time": leg["arrival_time"], "arrival_date": leg["arrival_date"],
            "arrival_day_offset": days_later, "stops": leg.get("stops", 0), "airline": leg.get("airline", ""),
            "duration_minutes": minutes, "timezone_shift_minutes": minutes - on_the_clocks, "price_eur": price}

def covered(flight: dict[str, Any], ground: dict[str, Any], to_airport: bool) -> bool:
    """Whether real timetables or an operator cover a way, as legs.py decides it: then a way that does not fit the
    flight cannot be made."""
    side = "to_airport" if to_airport else "from_airport"
    timetable = (ground.get(flight["id"]) or {}).get(side) or timetables.NOT_COVERED
    if to_airport:
        rides = ((ground.get("operators") or {}).get(flight["departure_airport"]) or {}).get(flight["date"])
        return bool(rides) or timetable["covered"]
    ready = to_minutes(flight["arrival_time"]) + current.SETTINGS.min_landing_minutes + timetables.MIN_STOP_WALK_MINUTES
    rides = ((ground.get("operators_from") or {}).get(flight["arrival_airport"]) or {}).get(timetables.landing_day(flight)) or []
    return timetable["covered"] or any(ready <= ride["leave"] < ride["arrive"] for ride in rides)

def described(leg: dict[str, Any]) -> str:
    """A connection in a few words: each vehicle with its times."""
    steps = [f"{step['line']} {step['time']}-{step['arrive']}" for step in leg.get("steps") or []]
    return ", then ".join(steps) or f"leaves {to_time(leg['leave'])}, arrives {to_time(leg['arrive'])}"

# The kinds of vehicle of the timetables (Transitous), as the published ways name them (transfers.py)
FAMILIES = {"bus": "bus", "coach": "bus", "shuttle": "bus", "train": "train", "metro": "metro", "tram": "tram", "ferry": "ferry"}

def family(mode: str) -> str:
    mode = mode.upper()
    if "RAIL" in mode or mode in ("SUBURBAN", "LONG_DISTANCE"):
        return "train"
    return {"SUBWAY": "metro", "METRO": "metro", "TRAM": "tram", "FERRY": "ferry", "BUS": "bus", "COACH": "bus"}.get(mode, "")

def published_fare(leg: dict[str, Any], published: dict[str, Any] | None) -> dict[str, Any] | None:
    """The published price for the kind of transport a connection found in the timetables takes, between an
    airport and its city: its longest vehicle, a bus or a train. None when nothing of that kind is published."""
    steps = [step for step in leg.get("steps") or [] if step.get("mode") and not step.get("estimated")]
    if not published or not steps:
        return None
    longest = max(steps, key=lambda step: step.get("km", 0))
    main = family(longest["mode"])
    same = [option for option in published["options"] if FAMILIES.get(option["mode"]) == main and option.get("one_way", True)]
    # The very line the timetable found, when a source names it (Catania's Alibus, not the cheaper city bus)
    line = plain(longest.get("line") or "")
    named = [option for option in same if len(line) >= 2 and re.search(rf"\b{re.escape(line)}", plain(f"{option['operator']} {option['quote']}"))]
    return (transfers.best_option(named) or transfers.best_option(same)) if main else None

def part_estimated(leg: dict[str, Any]) -> bool:
    """Whether part of a connection is an estimate: the timetables know no ride from the airport itself."""
    return any(step.get("estimated") for step in leg.get("steps") or [])

def checked_leg(flight: dict[str, Any], ground: dict[str, Any], to_airport: bool) -> tuple:
    """("found", the leg, "") or ("impossible", None, why) or ("not covered", None, why)."""
    build = legs.to_airport_leg if to_airport else legs.from_airport_leg
    try:
        found = build(flight, ground, None, priced=True)
        day = flight["date"] if to_airport else timetables.landing_day(flight)
        free = current.SETTINGS.free_trains_at_origin if to_airport else current.SETTINGS.free_trains_at_destination
        legs.price_with_tickets(found, day, free)
    except legs.Rejected as reason:
        return ("impossible" if covered(flight, ground, to_airport) else "not covered"), None, str(reason)
    except Exception as error:   # a price the settings do not allow to estimate, or a mistake: the way stays unknown
        return "not covered", None, f"{type(error).__name__}: {error}"
    if found.get("kind") not in ("timetable", "operator"):
        return "not covered", None, "no timetable covers it"
    return "found", found, ""

async def ground_between(origin: Place, destination: Place, flights: list[dict[str, Any]], base) -> dict[str, Any]:
    """The planner's ground lookup for flights between two places, as if they were the trip being planned. A real
    connection whose price is not known is kept with an estimate (grade C): without it the planner would reject it,
    and a way that exists would be taken for one that cannot be made."""
    current.SETTINGS = dataclasses.replace(base, origin=origin.written, destination=destination.written,
                                           dates=sorted({date.fromisoformat(flight["date"]).strftime("%d/%m/%Y") for flight in flights}),
                                           include_airports=[], exclude_airports=[], allow_estimated_prices=True)
    current.TRIP = TripPlaces(origin, destination,
                              primary=[airport_from(origin, code) for code in sorted({flight["departure_airport"] for flight in flights})],
                              fallback=[], arrival=[airport_from(destination, code) for code in sorted({flight["arrival_airport"] for flight in flights})])
    return await timetables.ground_transport_for(flights)

def least(option: dict[str, Any], seen: dict[tuple, float]) -> float:
    """The least a destination can cost once looked up (see the top of this module)."""
    return option["price_eur"] + sum(leg["price_eur"] if leg["looked_up"] or leg.get("checked") else seen.get((leg["airport"], leg["to_airport"]), 0.0)
                                     for leg in option["access"])

def city_of(option: dict[str, Any], airports: dict[str, Any], centres: dict[str, Place] | None = None) -> str:
    return finder.city_of(option["to"], airports, centres)

def next_city(options: list[dict[str, Any]], airports: dict[str, Any], centres: dict[str, Place] | None = None) -> list[dict[str, Any]]:
    """The airports of the next city to look up, or none when no destination left can change the cheapest TOP."""
    seen: dict[tuple, float] = {}
    best: dict[str, float] = {}
    for option in options:
        for leg in option["access"]:
            if leg["looked_up"]:
                key = (leg["airport"], leg["to_airport"])
                seen[key] = min(seen.get(key, leg["price_eur"]), leg["price_eur"])
        if option.get("checked"):
            city = city_of(option, airports, centres)
            best[city] = min(best.get(city, option["total_eur"]), option["total_eur"])
    cheapest = sorted(best.values())
    threshold = cheapest[TOP - 1] if len(cheapest) >= TOP else float("inf")
    able = [option for option in options if not option.get("checked")
            and least(option, seen) < min(best.get(city_of(option, airports, centres), float("inf")), threshold)]
    if not able:
        return []
    first = min(able, key=lambda option: (option["total_eur"], option["price_eur"]))
    city = city_of(first, airports, centres)
    return sorted((option for option in able if city_of(option, airports, centres) == city), key=lambda option: option["total_eur"])[:MAX_PER_CITY]

def cannot_look_up(option: dict[str, Any], found: Found, airports: dict[str, Any]) -> str:
    """Why a destination cannot be looked up in the timetables, or nothing when it can."""
    back = option.get("back")
    if option["to"] not in found.centres or (back and back["from"] not in found.centres):
        return "its city is not located yet"
    if planner_flight(option, 0.0, airports) is None or (back and planner_flight(back, 0.0, airports) is None):
        return "the times of its flights are not all known"
    return ""

async def look_up_city(found: Found, options: list[dict[str, Any]], airports: dict[str, Any], base, today: date) -> dict[str, Any]:
    """Look up the ways of a city's airports and put what was found into them. Returns the record of the lookup."""
    home = found.place
    # Each trip of a destination is one or two flights, each a trip of the planner: out from home, back home
    trips: dict[tuple, list] = {}   # (origin key, destination key) -> [(origin, destination, option, flight, first leg)]
    for option in options:
        option["checked"] = True
        out = planner_flight(option, option["price_eur"] if not option.get("back") else 0.0, airports)
        there = found.centres.get(option["to"])
        if out and there:
            trips.setdefault((access.place_key(home), access.place_key(there)), []).append((home, there, option, out, 0))
        back = option.get("back")
        leaving = found.centres.get(back["from"]) if back else None
        flight_back = planner_flight(back, 0.0, airports) if back else None
        if flight_back and leaving:
            trips.setdefault((access.place_key(leaving), access.place_key(home)), []).append((leaving, home, option, flight_back, 2))
    requests, outcome = 0, {}
    for entries in trips.values():
        origin, destination = entries[0][0], entries[0][1]
        flights = list({flight["id"]: flight for _, _, _, flight, _ in entries}.values())
        try:
            ground = await ground_between(origin, destination, flights, base)
        except Exception as error:
            say(f"[discover] the timetables could not be read for {origin.english_name} to {destination.english_name}: {type(error).__name__}")
            continue
        requests += ground.get("requests", 0)
        for _, _, option, flight, first in entries:
            for offset, to_airport in ((0, True), (1, False)):
                result, leg, why = checked_leg(flight, ground, to_airport)
                position = first + offset
                old = option["access"][position]
                outcome[result] = outcome.get(result, 0) + 1
                if result == "found":
                    when = flight["departure_time"] if to_airport else flight["arrival_time"]
                    summary = {"price_eur": round(leg["price_eur"], 2), "minutes": leg["arrive"] - leg["leave"], "grade": leg["grade"],
                               "price_grade": access.PRICE_GRADES.get(leg.get("price_kind", ""), "C"),
                               "for": f"a flight {'leaving' if to_airport else 'landing'} at {when}"}
                    how, extra = described(leg), {}
                    city = origin if to_airport else destination
                    published = access.published_for(old["airport"], city, today)
                    fare = published_fare(leg, published) if summary["price_grade"] == "C" else None
                    if part_estimated(leg) and published and published.get("best") and summary["price_grade"] == "C":
                        # Part of the way is a guess, and the airport's own bus or train is published: that way has a source
                        best = published["best"]
                        summary.update(price_eur=best["price_eur"], price_grade="B", minutes=best.get("minutes") or summary["minutes"], grade="C")
                        how = access.described(best) + "; the timetables know no ride from the airport itself"
                        extra = {"url": best["url"], "quote": best["quote"]}
                        fare = None
                    if fare:   # the timetable has no price: the one published for that kind of transport has a source
                        summary.update(price_eur=fare["price_eur"], price_grade="B")
                        how += f"; {'from ' if fare['from_price'] else ''}{fare['price_text']} as published by {fare['source']} for the {fare['mode']}"
                        extra = {"url": fare["url"], "quote": fare["quote"]}
                    option["access"][position] = {**old, **summary, **extra, "looked_up": True, "how": how,
                                                  "leave": to_time(leg["leave"]), "arrive": to_time(leg["arrive"])}
                    access.remember(old["airport"], to_airport, origin if to_airport else destination, summary, today)
                elif result == "impossible":
                    option.setdefault("impossible", why)
                else:
                    option["access"][position] = {**old, "checked": True, "how": old["how"] + "; no timetable covers it"}
    for option in options:
        option.update(with_access(option, home, found.centres, today, found.rides))
    there = found.centres.get(options[0]["to"])
    return {"city": there.english_name if there else access.city_name(airports.get(options[0]["to"]) or {}) or options[0]["to"], "airports": [option["to"] for option in options],
            "requests": requests, "found": outcome.get("found", 0), "impossible": outcome.get("impossible", 0),
            "not covered": outcome.get("not covered", 0)}

async def confirm(found: Found, on_change: Callable[[Found], None] = lambda found: None) -> Found:
    """Look up the cheapest destinations of the map in rounds, a city at a time, and put the map together again
    after each: the destinations, cheapest first, without the flights that cannot be made."""
    airports, today, base = load_airports(), date.today(), current.SETTINGS
    options = options_of(found.destinations)
    try:
        while True:
            if len(found.lookups) >= MAX_LOOKUPS:
                found.confirm_stop = f"stopped after {MAX_LOOKUPS} cities, the most a map looks up"
                break
            city = next_city(options, airports, found.centres)
            if not city:
                found.confirm_stop = (f"no destination left could be among the {TOP} cheapest, or cheaper than its city's: "
                                      "the ones not looked up would not change them")
                break
            # A city not located, or a flight whose times are not all known, cannot be looked up: it keeps its
            # estimate, and does not use up a lookup
            for option in city:
                reason = cannot_look_up(option, found, airports)
                if reason:
                    option.update(checked=True, not_looked_up=reason)
            city = [option for option in city if not option.get("not_looked_up")]
            if not city:
                continue
            say(f"Timetables: {', '.join(option['to'] for option in city)} ({len(found.lookups) + 1} of at most {MAX_LOOKUPS} cities)")
            record = await look_up_city(found, city, airports, base, today)
            found.lookups.append(record)
            found.discarded += [{"to": option["to"], "from": option["from"], "date": option["date"], "reason": option["impossible"]}
                                for option in city if option.get("impossible")]
            options = [option for option in options if not option.get("impossible")]
            found.destinations = one_per_city(options, airports, found.centres)
            on_change(found)
    finally:
        current.SETTINGS = base
    found.destinations = one_per_city(options, airports, found.centres)
    say(f"Timetables: {len(found.lookups)} cities looked up, {len(found.discarded)} flights left out; {found.confirm_stop}")
    on_change(found)
    return found
