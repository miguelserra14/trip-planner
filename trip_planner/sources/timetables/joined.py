"""An operator's coach joined to a published connection between the airport and the coach station it leaves from."""
import asyncio
from typing import Any

import httpx

from trip_planner import current
from trip_planner.itineraries.estimates import estimated_price, ROAD_FACTOR, step_price
from trip_planner.places import distance_km
from trip_planner.sources.timetables.client import by_road, Timetables
from trip_planner.sources.timetables.days import landing_day, reference_day
from trip_planner.texts import tr
from trip_planner.utils import say, to_minutes, to_time

# An operator's coach leaves from its city's station, and the way between the airport and that station was estimated
# (Obilet, in sources/operators): it is looked for in the published timetables, and the two joined. Where none is
# published, the estimate is kept, measured by road to the station itself instead of to the middle of the city.
TRANSFER_MINUTES = 10        # between a published connection and an operator's coach at the same station
STATION_WALK_SECONDS = 600   # the walk accepted between the last stop of a published connection and the coach station
STATION_WAIT_MINUTES = 20    # an estimated way also takes the time to find the bus or the taxi
JOINED_COACHES = 3           # towards the airport, the last coaches that still fit are joined, one request each

async def join_published_ends(timetables: Timetables, ground: dict[str, Any], flights: list[dict[str, Any]], spots: dict[str, tuple],
                              zones: dict[str, str], known: dict[str, bool], airports: dict[str, dict[str, Any]]) -> None:
    """For every flight, the operators' rides with an estimated way at the airport's end, joined to a published
    connection between the airport and the coach station when one fits: added to the rides of that airport and
    day, graded by the connection's timetable. The estimated ones stay, measured by road when the station is found."""

    def coach_of(ride: dict[str, Any], to_airport: bool) -> dict[str, Any]:
        return ride["steps"][0] if to_airport else ride["steps"][-1]

    def station_of(ride: dict[str, Any], to_airport: bool) -> str:
        return coach_of(ride, to_airport)["to" if to_airport else "from"]

    async def join(flight: dict[str, Any], to_airport: bool) -> None:
        code = flight["departure_airport"] if to_airport else flight["arrival_airport"]
        day = flight["date"] if to_airport else landing_day(flight)
        rides = ((ground.get("operators" if to_airport else "operators_from") or {}).get(code) or {}).get(day) or []
        estimated = [ride for ride in rides if ride.get("estimated_km")]
        if not estimated or not zones.get(code):
            return
        service, country = reference_day(day), airports[code]["country"]
        added = []
        for station in dict.fromkeys(station_of(ride, to_airport) for ride in estimated):
            point = await timetables.station_point(station, spots[code])
            if point is None:
                continue
            mine = [ride for ride in estimated if station_of(ride, to_airport) == station]
            # The estimate, by road to this very station
            road = await asyncio.to_thread(by_road, spots[code], [{"lat": point[0], "lon": point[1], "km": distance_km(*spots[code], *point)}])
            if road:
                for ride in mine:
                    remeasure(ride, road[0], to_airport, estimated_price(road[0]["road_km"] / ROAD_FACTOR, country))
            zone_pair = (zones[code], zones[code])
            if to_airport:
                # The first connection after each of the last coaches that arrive in time: asked from each one's arrival
                at_terminal = to_minutes(flight["departure_time"]) - current.SETTINGS.min_airport_minutes
                arriving = sorted((ride for ride in mine if to_minutes(coach_of(ride, True)["arrive"]) + TRANSFER_MINUTES < at_terminal),
                                  key=lambda ride: to_minutes(coach_of(ride, True)["arrive"]))[-JOINED_COACHES:]
                pairs = []
                for ride in arriving:
                    after = to_minutes(coach_of(ride, True)["arrive"]) + TRANSFER_MINUTES
                    try:
                        found = await timetables.plan(point, spots[code], service, after, arrive_by=False, zones=zone_pair, to_airport=True,
                                                      terminal_known=known[code], city_walk=STATION_WALK_SECONDS)
                    except httpx.HTTPError:
                        continue
                    fit = [c for c in found if c["leave"] >= after and c["arrive"] <= at_terminal]
                    pairs.append((ride, min(fit, key=lambda c: c["arrive"], default=None)))
            else:
                ready = to_minutes(flight["arrival_time"]) + current.SETTINGS.min_landing_minutes
                try:
                    connections = await timetables.plan(spots[code], point, service, ready, arrive_by=False, zones=zone_pair, to_airport=False,
                                                        terminal_known=known[code], city_walk=STATION_WALK_SECONDS)
                except httpx.HTTPError:
                    continue
                # Before each coach, the published connection that leaves the airport last and still reaches it
                pairs = [(ride, max((c for c in connections if c["arrive"] + TRANSFER_MINUTES <= to_minutes(coach_of(ride, False)["time"])),
                                    key=lambda c: c["leave"], default=None)) for ride in mine]
            for ride, chosen in pairs:
                coach = coach_of(ride, to_airport)
                if chosen is None:
                    continue
                local = sum(step_price(step, False, country) for step in chosen["steps"])
                joined = {key: value for key, value in ride.items() if key not in ("estimated_km", "notes")}
                joined.update(steps=[coach, *chosen["steps"]] if to_airport else [*chosen["steps"], coach],
                              leave=ride["leave"] if to_airport else chosen["leave"], arrive=chosen["arrive"] if to_airport else ride["arrive"],
                              grade="A" if service == day else "B", service_day=service, price_eur=round(ride["confirmed_eur"] + local, 2), notes=[])
                added.append(joined)
        rides.extend(added)

    for failure in await asyncio.gather(*(join(flight, to_airport) for flight in flights for to_airport in (True, False)), return_exceptions=True):
        if isinstance(failure, Exception):
            say(f"[timetables] joining an operator's ride: {type(failure).__name__}")

def remeasure(ride: dict[str, Any], road: dict[str, Any], to_airport: bool, price: float) -> None:
    """An operator's ride with its estimated way at the airport's end measured by road (by_road) instead of by the
    straight line to the middle of the city."""
    stretch = ride["steps"][-1] if to_airport else ride["steps"][0]
    coach = ride["steps"][0] if to_airport else ride["steps"][-1]
    minutes = round(road["road_minutes"]) + STATION_WAIT_MINUTES
    if to_airport:
        start = to_minutes(coach["arrive"])
        stretch.update(time=to_time(start), arrive=to_time(start + minutes))
        ride["arrive"] = start + minutes
    else:
        start = to_minutes(coach["time"]) - minutes
        stretch.update(time=to_time(start), arrive=coach["time"])
        ride["leave"] = start
    stretch.update(km=round(road["road_km"]), minutes=minutes)
    ride.update(estimated_km=round(road["road_km"]), price_eur=round(ride["confirmed_eur"] + price, 2),
                notes=[tr("note_estimated_stretch", km=round(road["road_km"]), start=stretch["from"], end=stretch["to"])])
