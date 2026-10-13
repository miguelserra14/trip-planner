"""Which flights get their ground transport looked up. Looking it up is the slow part of a search, so it is done
a few flights at a time, and only for the flights that can still become the recommended itinerary.

A flight cannot win when its price, plus the cheapest transport seen so far to and from its two airports, plus
what its shortest possible duration is worth to the traveller, is already above the best itinerary found. The
search stops when no flight is left that can: the flights never looked at could not have changed the result.

Two things are assumed, and stated here because the result is only as sure as they are:
- an airline's own price is not below AIRLINE_FLOOR times the price the flight search gave
- no connection to an airport is cheaper or faster than the best one seen for the flights already looked at"""
from typing import Any

from trip_planner import current

BATCH = 8                       # flights looked up in one round: the timetables are asked for all of them at once
MAX_FLIGHTS_CHECKED = 40        # however many could still win, no more than this: the search has to end
STOP_WITHOUT_ITINERARY = 16     # flights looked up before giving up when not one of them made an itinerary
FAILURES_PER_AIRPORT_DAY = 3    # flights of a departure airport and date that failed, with none working, before the rest are left out
AIRLINE_FLOOR = 0.8             # the airline's own site was up to 12% cheaper than the flight search in the trips checked

def counted(itineraries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The itineraries the recommendation is chosen among: an itinerary below the reliability asked for only
    counts when nothing reaches it, as in build_itineraries."""
    reliable = [itinerary for itinerary in itineraries if itinerary["schedule_grade"] <= current.SETTINGS.min_reliability]
    return reliable or itineraries

def worth(cost: float, minutes: float, fastest: float) -> float:
    """What an itinerary costs with the value of the time it takes beyond the fastest one, the way rank_itineraries
    recommends: the lowest is the recommended one."""
    settings = current.SETTINGS
    return cost + settings.extra_hours_cost(settings.tiebreak_eur_per_hour, max(0, minutes - fastest) / 60)

def could_win(cost: float, minutes: float, among: list[dict[str, Any]]) -> bool:
    """Whether an itinerary of this cost and duration would be the recommended one among the ones found.
    A faster itinerary changes what the time of all the others is worth, so they are all weighed again with it."""
    if not among:
        return True
    fastest = min(minutes, min(itinerary["total_minutes"] for itinerary in among))
    return worth(cost, minutes, fastest) < min(worth(itinerary["total_cost"], itinerary["total_minutes"], fastest) for itinerary in among)

def cheapest_seen(itineraries: list[dict[str, Any]]) -> dict[tuple, tuple]:
    """The cheapest and the shortest ground leg seen for each airport: {("to" or "from", airport): (EUR, minutes)}."""
    seen: dict[tuple, tuple] = {}
    for itinerary in itineraries:
        for side, end in (("to", "departure_airport"), ("from", "arrival_airport")):
            leg, key = itinerary[side], (side, itinerary["flight"][end])
            price, minutes = seen.get(key, (float("inf"), float("inf")))
            seen[key] = (min(price, leg["price_eur"]), min(minutes, leg["arrive"] - leg["leave"]))
    return seen

def least_possible(flight: dict[str, Any], seen: dict[tuple, tuple]) -> tuple:
    """The least an itinerary with this flight can cost and take: (EUR, minutes). An airport not seen yet may turn
    out to cost nothing and take no time."""
    settings = current.SETTINGS
    to_price, to_minutes = seen.get(("to", flight["departure_airport"]), (0, 0))
    from_price, from_minutes = seen.get(("from", flight["arrival_airport"]), (0, 0))
    minutes = to_minutes + settings.min_airport_minutes + flight["duration_minutes"] + settings.min_landing_minutes + from_minutes
    return flight["price_eur"] * AIRLINE_FLOOR + to_price + from_price, minutes

def next_flights(flights: list[dict[str, Any]], looked_up: dict[str, dict[str, Any] | None]) -> tuple:
    """The flights to look up next, and why there are none when there are none.
    looked_up is every flight looked up so far: its itinerary, or None when it could not make one.
    Returns (flights, reason): reason is "" while flights are returned, then
      "all"    every flight was looked up
      "beaten" the ones left cannot beat the best itinerary found
      "limit"  MAX_FLIGHTS_CHECKED was reached
      "none"   STOP_WITHOUT_ITINERARY flights were looked up and none made an itinerary"""
    itineraries = [itinerary for itinerary in looked_up.values() if itinerary]
    left = [flight for flight in flights if flight["id"] not in looked_up]
    if not left:
        return [], "all"
    if len(looked_up) >= MAX_FLIGHTS_CHECKED:
        return [], "limit"
    if not itineraries and len(looked_up) >= STOP_WITHOUT_ITINERARY:
        return [], "none"
    among = counted(itineraries)
    seen = cheapest_seen(itineraries)
    # A departure airport and date whose flights all failed: the connections to it do not fit, more of the same would not
    tried: dict[tuple, list] = {}
    for flight in flights:
        if flight["id"] in looked_up:
            tried.setdefault((flight["departure_airport"], flight["date"]), []).append(looked_up[flight["id"]])
    hopeless = {key for key, results in tried.items() if len(results) >= FAILURES_PER_AIRPORT_DAY and not any(results)}
    able = sorted((flight for flight in left
                   if (flight["departure_airport"], flight["date"]) not in hopeless and could_win(*least_possible(flight, seen), among)),
                  key=lambda flight: (least_possible(flight, seen)[0], flight["price_eur"]))
    if not able:
        return [], "beaten"
    # One flight of every departure airport not seen yet first: what it costs to reach an airport is what rules out
    # the dearer flights of that airport
    room = min(BATCH, MAX_FLIGHTS_CHECKED - len(looked_up))
    chosen: list[dict[str, Any]] = []
    for flight in able:
        new_airport = ("to", flight["departure_airport"]) not in seen
        if new_airport and flight["departure_airport"] not in {picked["departure_airport"] for picked in chosen}:
            chosen.append(flight)
    chosen += [flight for flight in able if flight not in chosen]
    return chosen[:room], ""
