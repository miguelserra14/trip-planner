"""Combine a flight with its ground legs into a door-to-door itinerary, check every hard constraint, add up the
cost and rank the itineraries by the traveller's preferences."""
from typing import Any, Dict, List

from trip_planner import current
from trip_planner.itineraries.legs import from_airport_leg, to_airport_leg
from trip_planner.sources.airlines import verify_with_airline
from trip_planner.texts import tr
from trip_planner.utils import as_duration, to_minutes, to_time

def check_itinerary(flight: Dict[str, Any], ground: Dict[str, Any], transfer: Dict[str, Any] | None,
                    route: Dict[str, Any] | None) -> Dict[str, Any]:
    """Combine a flight with its ground transport and check every hard constraint.
    Returns the itinerary, or raises ValueError with the reason it is rejected."""
    if flight["stops"] > current.SETTINGS.max_stopovers:
        raise ValueError(tr("reject_stopovers", stops=flight["stops"], maximum=current.SETTINGS.max_stopovers))
    if flight["arrival_airport"] not in {airport["code"] for airport in current.TRIP.arrival}:
        raise ValueError(tr("reject_arrival_airport", airport=flight["arrival_airport"]))
    to_leg = to_airport_leg(flight, ground, transfer)
    from_leg = from_airport_leg(flight, ground, route)
    return {
        "flight": flight,
        "to": to_leg,
        "from": from_leg,
        "notes": to_leg.get("notes", []) + from_leg.get("notes", []),
        "leave_time": to_time(to_leg["leave"]),
        "at_airport_time": to_time(to_leg["arrive"]),
        "airport_wait": to_minutes(flight["departure_time"]) - to_leg["arrive"],
        "wait_after_landing": from_leg["leave"] - to_minutes(flight["arrival_time"]),
        "arrival_time": to_time(from_leg["arrive"]),
        # The two clocks may differ, the flight tells by how much
        "total_minutes": from_leg["arrive"] + flight["timezone_shift_minutes"] - to_leg["leave"],
        # Flight times come from Kiwi for the travel date (A). The ground legs decide the grade of the schedule.
        "schedule_grade": max(to_leg["grade"], from_leg["grade"]),
    }

def price_itinerary(itinerary: Dict[str, Any]) -> None:
    """Confirm the flight on the airline's own site when possible, then add up the cost and how much of it is confirmed."""
    flight = itinerary["flight"]
    booking = verify_with_airline(flight)   # the traveller books on the airline's site: that price counts when it is known
    itinerary["booking"] = booking
    itinerary["flight_price"] = booking["price_eur"]
    itinerary["total_cost"] = booking["price_eur"] + itinerary["to"]["price_eur"] + itinerary["from"]["price_eur"]
    itinerary["confirmed_eur"] = (
        (booking["price_eur"] if booking["verified"] else 0.0)
        + (itinerary["to"]["price_eur"] if itinerary["to"]["price_kind"] == "operator" else 0.0)
    )

def rank_itineraries(itineraries: List[Dict[str, Any]]) -> tuple:
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
