"""Combine a flight with its ground legs into a door-to-door itinerary, check every hard constraint, add up the
cost and rank the itineraries by the traveller's preferences."""
from datetime import date, timedelta
from typing import Any

from trip_planner import current
from concurrent.futures import ThreadPoolExecutor

from trip_planner.itineraries.legs import country_of, from_airport_leg, not_running_reason, price_with_tickets, Rejected, to_airport_leg
from trip_planner.sources import airlines
from trip_planner.sources.timetables import landing_day
from trip_planner.texts import tr
from trip_planner.utils import as_duration, to_minutes, to_time

# When no itinerary respects every requirement, the closest ones are shown instead. These are the requirements that
# may give way, the most important first. An itinerary that breaks one of them is always behind an itinerary that
# only breaks the ones after it, however small the first breach and however large the others:
#   arrival    arriving within the window asked for: someone or something is usually waiting
#   airport    the minimum time at the airport: below it the flight may be missed (never under airport_floor_minutes)
#   stopovers  the number of stopovers: a longer trip and one more thing that can go wrong
#   leave      the times the traveller leaves at: a matter of comfort
# Among itineraries that break the same requirement, the smaller breach comes first, then the cheaper itinerary.
# The dates, the allowed airports and what the real timetables say are never given up.
REQUIREMENTS = ("arrival", "airport", "stopovers", "leave")

def day_after(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()

def breaches(itinerary: dict[str, Any]) -> list[dict[str, Any]]:
    """The requirements an itinerary does not respect, in the order of REQUIREMENTS. Each one says by how much
    (amount), what the itinerary does (value), what was asked (limit) and the setting that asks it."""
    settings, flight, found = current.SETTINGS, itinerary["flight"], []
    # In minutes of the day the traveller is there, not on the clock: 00:20 in the night after the landing is later
    # than 23:00, and 23:30 on the evening before the flight is earlier than 06:00
    arrive, leave = itinerary["from"]["arrive"], itinerary["to"]["leave"]
    if arrive > settings.latest_arrival_minutes:
        found.append({"kind": "arrival", "side": "after", "amount": arrive - settings.latest_arrival_minutes,
                      "value": itinerary["arrival_time"], "limit": settings.latest_arrival, "setting": "latest_arrival"})
    elif arrive < settings.earliest_arrival_minutes:
        found.append({"kind": "arrival", "side": "before", "amount": settings.earliest_arrival_minutes - arrive,
                      "value": itinerary["arrival_time"], "limit": settings.earliest_arrival, "setting": "earliest_arrival"})
    if itinerary["airport_wait"] < settings.min_airport_minutes:
        found.append({"kind": "airport", "amount": settings.min_airport_minutes - itinerary["airport_wait"],
                      "value": itinerary["airport_wait"], "limit": settings.min_airport_minutes, "setting": "min_airport_minutes"})
    if flight["stops"] > settings.max_stopovers:
        found.append({"kind": "stopovers", "amount": flight["stops"] - settings.max_stopovers,
                      "value": flight["stops"], "limit": settings.max_stopovers, "setting": "max_stopovers"})
    if leave < to_minutes(settings.earliest_leave):
        found.append({"kind": "leave", "side": "before", "amount": to_minutes(settings.earliest_leave) - leave,
                      "value": itinerary["leave_time"], "limit": settings.earliest_leave, "setting": "earliest_leave"})
    elif leave > to_minutes(settings.latest_leave):
        found.append({"kind": "leave", "side": "after", "amount": leave - to_minutes(settings.latest_leave),
                      "value": itinerary["leave_time"], "limit": settings.latest_leave, "setting": "latest_leave"})
    return found

def closeness(itinerary: dict[str, Any]) -> tuple:
    """How far an itinerary is from what was asked, to sort the closest first: the breach of each requirement in
    the order of REQUIREMENTS, then the cost, then the duration."""
    amounts = {breach["kind"]: breach["amount"] for breach in itinerary["breaches"]}
    return (*(amounts.get(kind, 0) for kind in REQUIREMENTS), itinerary["total_cost"], itinerary["total_minutes"])

def check_itinerary(flight: dict[str, Any], ground: dict[str, Any], transfer: dict[str, Any] | None,
                    route: dict[str, Any] | None, closest: bool = False) -> dict[str, Any]:
    """Combine a flight with its ground transport and check every hard constraint.
    Returns the itinerary, or raises Rejected with the reason it is rejected.
    closest=True lets the requirements of REQUIREMENTS give way and lists the ones that did under "breaches"."""
    if flight["stops"] > current.SETTINGS.max_stopovers and not closest:
        raise Rejected(tr("reject_stopovers", stops=flight["stops"], maximum=current.SETTINGS.max_stopovers))
    if flight["arrival_airport"] not in {airport["code"] for airport in current.TRIP.arrival}:
        raise Rejected(tr("reject_arrival_airport", airport=flight["arrival_airport"]))
    to_leg = to_airport_leg(flight, ground, transfer, closest, priced=True)
    from_leg = from_airport_leg(flight, ground, route, closest, priced=True)
    # Where each leg runs, for the vehicles no operator prices (price_with_tickets)
    to_leg["country"], from_leg["country"] = country_of(flight["departure_airport"]), country_of(flight["arrival_airport"])
    itinerary = {
        "flight": flight,
        "to": to_leg,
        "from": from_leg,
        "notes": to_leg.get("notes", []) + from_leg.get("notes", []),
        "leave_time": to_time(to_leg["leave"]),
        # The days the traveller leaves and arrives on. They are the day of the flight and the day it lands, unless
        # a ground leg crosses midnight: the evening before an early flight, the night after a late landing.
        "leave_date": day_after(flight["date"], to_leg["leave"] // (24 * 60)),
        "arrival_date": day_after(landing_day(flight), from_leg["arrive"] // (24 * 60)),
        "at_airport_time": to_time(to_leg["arrive"]),
        "airport_wait": to_minutes(flight["departure_time"]) - to_leg["arrive"],
        "wait_after_landing": from_leg["leave"] - to_minutes(flight["arrival_time"]),
        "arrival_time": to_time(from_leg["arrive"]),
        # The two clocks may differ, the flight tells by how much. The destination's clock is that of the day the
        # flight lands, which a night flight makes a later day than the one the traveller left on.
        "total_minutes": from_leg["arrive"] + flight.get("arrival_day_offset", 0) * 24 * 60 + flight["timezone_shift_minutes"] - to_leg["leave"],
        # Flight times come from Kiwi for the travel date (A). The ground legs decide the grade of the schedule.
        "schedule_grade": max(to_leg["grade"], from_leg["grade"]),
    }
    itinerary["breaches"] = breaches(itinerary)
    return itinerary

def price_itinerary(itinerary: dict[str, Any]) -> None:
    """Confirm the flight on the airline's own site when possible, then add up the cost and how much of it is confirmed."""
    flight = itinerary["flight"]
    price_with_tickets(itinerary["to"], flight["date"], current.SETTINGS.free_trains_at_origin)
    price_with_tickets(itinerary["from"], landing_day(flight), current.SETTINGS.free_trains_at_destination)
    # An operator that sells the very departure of a leg confirms its timetable for the day (price_with_tickets)
    itinerary["schedule_grade"] = max(itinerary["to"]["grade"], itinerary["from"]["grade"])
    # ... and one that runs other rides that day but not this one says it does not run: the reason, or ""
    stopped = [(leg["not_running"], day) for leg, day in ((itinerary["to"], flight["date"]), (itinerary["from"], landing_day(flight)))
               if leg.get("not_running")]
    itinerary["not_running"] = not_running_reason(*stopped[0]) if stopped else ""
    booking = airlines.verify_with_airline(flight)   # the traveller books on the airline's site: that price counts when it is known
    itinerary["booking"] = booking
    itinerary["flight_price"] = booking["price_eur"]
    itinerary["total_cost"] = booking["price_eur"] + itinerary["to"]["price_eur"] + itinerary["from"]["price_eur"]
    itinerary["confirmed_eur"] = (
        (booking["price_eur"] if booking["verified"] else 0.0)
        + sum(leg.get("confirmed_eur", leg["price_eur"] if leg["price_kind"] == "operator" else 0.0) for leg in (itinerary["to"], itinerary["from"]))
    )
    itinerary["guessed_eur"] = guessed_eur(itinerary["to"]) + guessed_eur(itinerary["from"])

def guessed_eur(leg: dict[str, Any]) -> float:
    """How much of the price of a ground leg is a guess: a model's price or an estimate from the distance, for the
    whole leg or for the vehicles no operator priced. A price read for another day or from a tariff is not a guess."""
    if leg["price_kind"] in ("model", "estimate"):
        return leg["price_eur"]
    if leg["price_kind"] == "partly":
        return leg["price_eur"] - sum(ticket["price_eur"] for ticket in leg.get("tickets") or [])
    return 0.0

def price_range(itinerary: dict[str, Any]) -> tuple:
    """What an itinerary may really cost, (lowest, highest): each guessed EUR may be unverified_price_factor times
    less or more than guessed."""
    factor, guessed = current.SETTINGS.unverified_price_factor, itinerary.get("guessed_eur", 0.0)
    known = itinerary["total_cost"] - guessed
    return known + guessed / factor, known + guessed * factor

def tied_on_price(itineraries: list[dict[str, Any]]) -> list[int]:
    """The numbers (from 1) of the itineraries that may cost as little as the cheapest one, itself included: their
    guessed prices make them impossible to tell apart. Empty when the cheapest stands out."""
    cheapest = min(itineraries, key=lambda itinerary: itinerary["total_cost"])
    highest = price_range(cheapest)[1]
    tied = [number for number, itinerary in enumerate(itineraries, start=1) if price_range(itinerary)[0] <= highest]
    return tied if len(tied) > 1 else []

FARE_THREADS = 6   # operators asked at the same time: each ticket is one or two requests to a different site

def price_itineraries(itineraries: list[dict[str, Any]]) -> None:
    """price_itinerary for several itineraries. The operators are asked for their tickets at the same time first,
    and every answer is remembered, so the itineraries that share a connection ask once."""
    settings = current.SETTINGS
    legs = [(itinerary[side], day(itinerary["flight"]), free) for itinerary in itineraries
            for side, day, free in (("to", lambda flight: flight["date"], settings.free_trains_at_origin),
                                    ("from", landing_day, settings.free_trains_at_destination))]
    with ThreadPoolExecutor(FARE_THREADS) as pool:
        list(pool.map(lambda leg: price_with_tickets(*leg), legs))
    for itinerary in itineraries:
        price_itinerary(itinerary)

def rank_itineraries(itineraries: list[dict[str, Any]]) -> tuple:
    """Apply the traveller's two preferences, both expressed as EUR per extra hour.
    Returns (itineraries worth showing sorted by cost, the recommended one, reasons for the ones not worth it)."""
    primary = {airport["code"] for airport in current.TRIP.primary}
    from_primary = [itinerary for itinerary in itineraries if itinerary["flight"]["departure_airport"] in primary]
    kept, not_worth_it = [], []
    # A fallback airport is only worth it if it saves enough for every extra hour it takes,
    # compared with the cheapest itinerary from a primary airport.
    reference = min(from_primary, key=lambda itinerary: itinerary["total_cost"], default=None)
    for itinerary in itineraries:
        flight = itinerary["flight"]
        if reference is not None and flight["departure_airport"] not in primary:
            extra_hours = (itinerary["total_minutes"] - reference["total_minutes"]) / 60
            saving = reference["total_cost"] - itinerary["total_cost"]
            required = current.SETTINGS.extra_hours_cost(current.SETTINGS.fallback_saving_eur_per_hour, extra_hours)
            if extra_hours > 0 and saving < required:
                not_worth_it.append(f"{flight['id']}: " + tr(
                    "reject_not_worth_it", total=f"{itinerary['total_cost']:.0f}", saving=f"{saving:.0f}",
                    extra=as_duration(round(extra_hours * 60)), reference=reference["flight"]["departure_airport"],
                    required=f"{required:.0f}"))
                continue
        kept.append(itinerary)
    kept.sort(key=lambda itinerary: (itinerary["total_cost"], itinerary["total_minutes"]))
    # The recommended itinerary is the cheapest once the hours it takes beyond the fastest one are priced.
    # Those hours cost more and more, so one extra hour is easily accepted and three or four rarely are.
    fastest = min(itinerary["total_minutes"] for itinerary in kept)
    for itinerary in kept:
        itinerary["extra_minutes"] = itinerary["total_minutes"] - fastest
        itinerary["time_cost"] = current.SETTINGS.extra_hours_cost(current.SETTINGS.tiebreak_eur_per_hour, itinerary["extra_minutes"] / 60)
    recommended = min(kept, key=lambda itinerary: (itinerary["total_cost"] + itinerary["time_cost"], itinerary["total_cost"]))
    return kept, recommended, not_worth_it

def cheapest_by_date(itineraries: list[dict[str, Any]], days: list[str]) -> list[tuple[str, dict[str, Any] | None]]:
    """The cheapest itinerary of each travel date (yyyy-mm-dd), or None for a date none is left on: what going on
    each date would cost, the faster one first when two cost the same."""
    on = lambda day: [itinerary for itinerary in itineraries if itinerary["flight"]["date"] == day]
    return [(day, min(on(day), key=lambda itinerary: (itinerary["total_cost"], itinerary["total_minutes"]), default=None)) for day in days]
