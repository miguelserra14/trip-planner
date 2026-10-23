"""The operators' rides between the traveller's city and its airports, sold for the day of each flight: FlixBus
anywhere, Rede Expressos in Portugal (the planner's fares.py, which reads their own booking systems). These are
real prices for that day, grade A, at hours that fit the flight: the way to the airport arrives in time for it,
the way home leaves after the landing.

They are asked once per airport, day and direction, for every flight of the map at once: the traveller's side is
the same for every destination, so this is what makes every total on the map start from a real price. A day with
no ride that fits says nothing about the other ways there are: the trip keeps what else is known (access.py)."""
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from trip_planner import current
from trip_planner.trip.places import Place
from trip_planner.sources import fares, timetables
from trip_planner.utils import logged, to_minutes, to_time

AT_ONCE = 6   # requests to the operators at the same time: each is a different site, or a different day

def look_up(home: Place, departures: list[dict[str, Any]], wanted: set, say=print) -> dict[tuple, list[dict[str, Any]]]:
    """The rides of every operator for each (airport code, day, towards the airport) wanted, by that key."""
    airports = {airport["code"]: airport for airport in departures}
    asked = [(name, source, code, day, to_airport) for name, source in fares.RIDE_SOURCES.items()
             for code, day, to_airport in sorted(wanted) if code in airports]
    if asked:
        say(f"Asking FlixBus and Rede Expressos for their rides between {home.english_name} and its airports, on {len({key[1] for key in wanted})} days")

    def one(entry: tuple) -> tuple:
        name, source, code, day, to_airport = entry
        try:
            with logged(name, f"rides {'to' if to_airport else 'from'} {code} on {day}") as log:
                found = source(home, airports[code], day, to_airport)
                log["result"] = "ok: no answer" if found is None else f"ok: {len(found)} rides"
            return (code, day, to_airport), [{**ride, "source": ride.get("source") or name} for ride in found or []]
        except Exception:
            return (code, day, to_airport), []
    rides: dict[tuple, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(AT_ONCE) as pool:
        for key, found in pool.map(one, asked):
            rides.setdefault(key, []).extend(found)
    return rides

def wanted_for(flights: list[dict[str, Any]], departures: list[dict[str, Any]]) -> set:
    """The (airport, day, towards the airport) whose rides the flights need: the day each leaves, the day the
    return lands."""
    mine = {airport["code"] for airport in departures}
    wanted = set()
    for flight in flights:
        if flight["from"] in mine:
            wanted.add((flight["from"], flight["date"], True))
        back = flight.get("back")
        if back and back["to"] in mine:
            wanted.add((back["to"], back.get("arrival_date") or back["date"], False))
    return wanted

def best_ride(rides: list[dict[str, Any]], leg: dict[str, Any], to_airport: bool) -> dict[str, Any] | None:
    """The ride a traveller would take with this flight, as the planner chooses (legs.py): towards the airport, of
    the rides that arrive the time asked for before the flight, the cheapest once every hour spent waiting at the
    airport is priced (a coach at 03:00 to save a euro on one at 07:00 is not the better way); home, of the rides
    that leave after the landing, the cheapest once every hour of arriving later is priced. None when none fits."""
    settings = current.SETTINGS
    hours_cost = lambda minutes: settings.extra_hours_cost(settings.tiebreak_eur_per_hour, minutes / 60)
    if to_airport:
        latest = to_minutes(leg["time"]) - settings.min_airport_minutes
        earliest = to_minutes(settings.earliest_leave)
        fits = [ride for ride in rides if earliest <= ride["leave"] < ride["arrive"] <= latest]
        last = max((ride["leave"] for ride in fits), default=0)
        chosen = min(fits, key=lambda ride: (ride["price_eur"] + hours_cost(last - ride["leave"]), -ride["leave"]), default=None)
    else:
        if not leg.get("arrival_time"):
            return None
        ready = to_minutes(leg["arrival_time"]) + settings.min_landing_minutes + timetables.MIN_STOP_WALK_MINUTES
        fits = [ride for ride in rides if ready <= ride["leave"] < ride["arrive"]]
        first = min((ride["arrive"] for ride in fits), default=0)
        chosen = min(fits, key=lambda ride: (ride["price_eur"] + hours_cost(ride["arrive"] - first), ride["arrive"]), default=None)
    if chosen is None:
        return None
    return {"price_eur": round(chosen["price_eur"], 2), "leave": chosen["leave"], "arrive": chosen["arrive"],
            "leave_time": to_time(chosen["leave"]), "arrive_time": to_time(chosen["arrive"]), "source": chosen["source"],
            "url": next((url for url in chosen.get("urls") or [] if url.startswith("https://")), "")}
