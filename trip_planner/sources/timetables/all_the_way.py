"""The whole trip without a plane: from the origin city to the destination city by train or coach, on each travel
date. Read from the published timetables (Transitous) at several times of the day, and from FlixBus, which sells
from city to city with its real price. What these connections cost, vehicle by vehicle, is asked of the same
operators as the ground legs of the flights (fares.py); itineraries/ground_only.py keeps and ranks them."""
import asyncio
from datetime import date, datetime
from typing import Any

from trip_planner import current
from trip_planner.sources import web
from trip_planner.sources.operators import flixbus
from trip_planner.sources.timetables.client import PAGE_SIZE, step_of, Timetables
from trip_planner.sources.timetables.days import local_to_utc, minutes_since, reference_day
from trip_planner.sources.timetables.modes import GROUND_MODES
from trip_planner.utils import logged, to_minutes

WINDOW_HOURS = 3          # the timetables are asked from the earliest time the traveller leaves, then every this many hours:
                          # each answer is the fastest few after that time, so one request does not cover a whole day
MAX_HOURS = 36            # a trip longer than this on the ground is not compared with flying
FLIXBUS_CITY_KM = 25      # FlixBus's city is the traveller's when its centre is this close

def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))

def connection(service: str, day: str, itinerary: dict[str, Any], zones: tuple) -> dict[str, Any] | None:
    """One connection of the timetables as the planner keeps a ground-only trip, or None when it is not one.
    leave is on the origin's clock and arrive on the destination's, in minutes since the midnight the day read
    starts with: past 24 hours it arrives on a later day. minutes is the real time it takes."""
    legs = [leg for leg in itinerary["legs"] if leg["mode"] != "WALK"]
    if not legs or any(leg["mode"] == "AIRPLANE" for leg in legs):
        return None
    leave, arrive = minutes_since(service, legs[0]["startTime"], zones[0]), minutes_since(service, legs[-1]["endTime"], zones[1])
    return {"day": day, "service_day": service, "source": "timetable", "leave": leave, "arrive": arrive,
            "minutes": round((moment(legs[-1]["endTime"]) - moment(legs[0]["startTime"])).total_seconds() / 60),
            "steps": [step_of(leg, zones) for leg in legs],
            "urls": list(dict.fromkeys(url for url in (web.secure_url(leg.get("agencyUrl")) for leg in legs) if url))}

async def published(timetables: Timetables, start: tuple, end: tuple, day: str, zones: tuple) -> list[dict[str, Any]]:
    """The connections of the timetables on one travel date, read for that day or, when it is not published yet,
    for the same weekday of this week (reference_day): every vehicle on the ground, any operator."""
    settings, service = current.SETTINGS, reference_day(day)
    earliest, latest = to_minutes(settings.earliest_leave), to_minutes(settings.latest_leave)
    walk = settings.city_walk_minutes * 60

    async def window(minutes: int) -> list[dict[str, Any]]:
        found = await timetables.get("plan", {
            "fromPlace": f"{start[0]},{start[1]}", "toPlace": f"{end[0]},{end[1]}", "time": local_to_utc(service, minutes, zones[0]),
            "arriveBy": "false", "numItineraries": PAGE_SIZE, "maxPreTransitTime": walk, "maxPostTransitTime": walk,
            "transitModes": GROUND_MODES})
        return [made for made in (connection(service, day, itinerary, zones) for itinerary in found.get("itineraries") or []) if made]
    answers = await asyncio.gather(*(window(minutes) for minutes in range(earliest, latest + 1, WINDOW_HOURS * 60)), return_exceptions=True)
    return [found for answer in answers if isinstance(answer, list) for found in answer]

def sold_by_flixbus(origin, destination, day: str) -> list[dict[str, Any]]:
    """What FlixBus sells from the origin city to the destination city on the travel date, with its price: the
    date itself is on sale, so these are confirmed for it (A)."""
    start = flixbus.flixbus_city(origin.english_name, origin.latitude, origin.longitude, FLIXBUS_CITY_KM)
    end = flixbus.flixbus_city(destination.english_name, destination.latitude, destination.longitude, FLIXBUS_CITY_KM)
    if not start or not end or start == end:
        return []
    with logged("FlixBus", f"all the way {origin.english_name} -> {destination.english_name}, {day}"):
        rides, stations = flixbus.flixbus_search(start, end, day, "city")
    found = []
    for ride in rides:
        first, last = moment(ride["departure"]["date"]), moment(ride["arrival"]["date"])
        if first.date().isoformat() != day:
            continue
        legs = ride.get("legs") or [ride]
        found.append({
            "day": day, "service_day": day, "source": "FlixBus", "grade": "A", "leave": first.hour * 60 + first.minute,
            "arrive": last.hour * 60 + last.minute + (last.date() - date.fromisoformat(day)).days * 24 * 60,
            "minutes": round((last - first).total_seconds() / 60), "price_eur": flixbus.flixbus_price(ride),
            "steps": [{"time": moment(leg["departure"]["date"]).strftime("%H:%M"), "arrive": moment(leg["arrival"]["date"]).strftime("%H:%M"),
                       "line": "FlixBus", "agency": "FlixBus", "from": stations.get(leg["departure"]["station_id"], ""),
                       "to": stations.get(leg["arrival"]["station_id"], ""), "rail": False, "coach": True, "km": 0} for leg in legs],
            "urls": [f"https://shop.flixbus.pt/search?departureCity={start}&arrivalCity={end}&rideDate={day[8:10]}.{day[5:7]}.{day[0:4]}&adult=1"],
        })
    return found

async def all_the_way(days: list[str]) -> dict[str, Any]:
    """Every way found from the origin to the destination on the ground, on each travel date: {"connections": ...,
    "requests": ...}. A source that fails only leaves out what it would have found."""
    origin, destination = current.TRIP.origin, current.TRIP.destination
    start, end = (origin.latitude, origin.longitude), (destination.latitude, destination.longitude)
    timetables = Timetables()
    try:
        zones = tuple(await asyncio.gather(timetables.zone(*start), timetables.zone(*end)))
        answers = await asyncio.gather(*(published(timetables, start, end, day, zones) for day in days),
                                       *(asyncio.to_thread(sold_by_flixbus, origin, destination, day) for day in days), return_exceptions=True)
        found = [one for answer in answers if isinstance(answer, list) for one in answer]
        return {"connections": [one for one in found if one["minutes"] <= MAX_HOURS * 60], "requests": timetables.requests,
                "zones": zones}
    finally:
        await timetables.close()
