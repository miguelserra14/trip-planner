"""Mixed legs, on made-up answers of the timetables: no network. Where no published timetable joins the airport
and the place (the buses of Campogalliano are not published), the published part goes to the nearest station and
only the rest, at most max_estimate_km by road, is estimated."""
import asyncio
from datetime import date, timedelta

from trip_planner import current
from trip_planner.sources import timetables
from trip_planner.sources.timetables import client

AIRPORT, CITY, STATION = (44.53, 11.29), (44.69, 10.84), (44.65, 10.93)   # Bologna airport, Campogalliano, Modena
DAY = (date.today() + timedelta(days=3)).isoformat()

def bus(start: str, end: str) -> dict:
    """What the planner answers: one train from the airport to the station, between two UTC times of DAY."""
    at = lambda point, name: {"name": name, "lat": point[0], "lon": point[1], "tz": "UTC"}
    leg = {"mode": "REGIONAL_RAIL", "startTime": f"{DAY}T{start}:00Z", "endTime": f"{DAY}T{end}:00Z",
           "from": at(AIRPORT, "Bologna Aeroporto"), "to": at(STATION, "Modena"), "routeShortName": "R"}
    return {"itineraries": [{"startTime": leg["startTime"], "endTime": leg["endTime"], "legs": [leg]}]}

def lookup(road_km: float, monkeypatch) -> dict:
    async def get(path, params):
        if path == "map/stops":
            return [{"name": "Modena", "stopId": "it-trenitalia_1", "lat": STATION[0], "lon": STATION[1], "modes": ["REGIONAL_RAIL"]}]
        reaches_station = params["toPlace"] == f"{STATION[0]},{STATION[1]}"
        return bus("12:10", "12:40") if reaches_station else {"itineraries": []}   # nothing reaches the town itself
    monkeypatch.setattr(client, "by_road", lambda point, hubs: [
        {**hub, "road_km": road_km, "road_minutes": 15} for hub in hubs if road_km <= current.SETTINGS.max_estimate_km])

    async def work() -> dict:
        transit = timetables.Timetables()
        transit.get = get
        flight = {"id": "F", "date": DAY, "arrival_airport": "BLQ", "arrival_time": "11:00"}
        found = await transit.from_airport(AIRPORT, CITY, flight, ("UTC", "UTC"), True)
        await transit.close()
        return found
    monkeypatch.setattr(client, "reference_day", lambda day: day)
    return asyncio.run(work())

def test_the_published_part_goes_to_the_station_and_the_rest_is_estimated(monkeypatch):
    answer = lookup(11, monkeypatch)
    option = answer["options"][0]
    assert answer["covered"] and option["grade"] == "C" and option["estimated_km"] == 11
    *published, stretch = option["steps"]
    assert [step["to"] for step in published] == ["Modena"] and stretch["estimated"] and stretch["from"] == "Modena"
    assert option["leave"] == 12 * 60 + 10 and option["arrive"] == 12 * 60 + 40 + stretch["minutes"]
    assert stretch["minutes"] == 15 + 20, "the road's time and the time getting a bus or a taxi"
    assert option["notes"] and "11 km" in option["notes"][0]

def test_no_mixed_leg_when_the_station_is_too_far_by_road(monkeypatch):
    answer = lookup(current.SETTINGS.max_estimate_km + 1, monkeypatch)
    assert not answer["covered"] and answer["options"] == []
