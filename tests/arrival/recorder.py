"""Follows one trip from an arrival airport to a destination through the real services, and saves what each
stage received as a checkpoint. A stage that fails leaves the checkpoints of the stages before it, and its error.

The stages, in the order the program goes through them:
1. place        the destination is located (Nominatim)
2. terminal     the passenger terminal of the airport is located (OpenStreetMap)
3. stops        the stops around the airport are listed (Transitous)
4. connections  the connections after landing are looked up (Transitous)"""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trip_planner.places import airport_from, as_place, distance_km, search_places
from trip_planner.sources import timetables

CHECKPOINTS = Path(__file__).parent / "checkpoints"
STAGES = ("place", "terminal", "stops", "connections")
LANDING = "11:00"
DAYS_AHEAD = 70   # far enough for the travel date not to be published: the typical timetable is what is tested

def checkpoint_file(case) -> Path:
    return CHECKPOINTS / f"{case.name}.json"

def load(case) -> dict[str, Any] | None:
    path = checkpoint_file(case)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

def save(case, record: dict[str, Any]) -> None:
    CHECKPOINTS.mkdir(exist_ok=True)
    # One line per stage: short enough to keep in the repository, and a change shows as the line of its stage
    lines = [f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}" for key, value in record.items()]
    checkpoint_file(case).write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")

def trimmed_place(result: dict[str, Any]) -> dict[str, Any]:
    """What the program reads of a Nominatim result."""
    names = result.get("namedetails") or {}
    return {"name": result.get("name"), "display_name": result.get("display_name"), "addresstype": result.get("addresstype"),
            "importance": result.get("importance"), "lat": result["lat"], "lon": result["lon"],
            "namedetails": {key: names[key] for key in ("name", "name:en") if key in names},
            "address": {"country": (result.get("address") or {}).get("country", "")}}

async def record_case(case, transit: timetables.Timetables) -> dict[str, Any]:
    """Go through the stages for one case, saving a checkpoint after each."""
    flight = {"id": case.name, "date": (date.today() + timedelta(days=DAYS_AHEAD)).isoformat(),
              "arrival_airport": case.airport, "arrival_time": LANDING}
    record: dict[str, Any] = {"case": vars(case), "recorded": date.today().isoformat(), "flight": flight, "reached": []}

    def reached(stage: str) -> None:
        record["reached"].append(stage)
        save(case, record)

    try:
        results = await asyncio.to_thread(search_places, case.city)
        await asyncio.sleep(1.1)   # Nominatim asks for one request a second at most
        record["place_results"] = [trimmed_place(result) for result in results]
        place = as_place(case.city, record["place_results"])
        record["airport"] = airport = airport_from(place, case.airport)
        reached("place")

        record["terminal"] = terminal = (await asyncio.to_thread(timetables.find_terminals, [airport])).get(case.airport)
        reached("terminal")

        centre = terminal or (airport["latitude"], airport["longitude"])
        around = await transit.stops_around(airport, terminal)
        record["stops_around"] = [
            {key: stop.get(key) for key in ("name", "stopId", "lat", "lon", "modes")} for stop in around
            if distance_km(centre[0], centre[1], stop["lat"], stop["lon"]) <= timetables.AIRPORT_STOP_KM]
        stops = timetables.choose_airport_stops(record["stops_around"], airport, terminal)
        reached("stops")

        spot, end = tuple(centre), (place.latitude, place.longitude)
        record["zones"] = zones = [await transit.zone(*spot), await transit.zone(*end)]
        record["answer"] = await transit.from_airport(spot, end, flight, tuple(zones), terminal is not None, stops)
        reached("connections")
    except Exception as error:
        record["error"] = {"stage": STAGES[len(record["reached"])], "message": f"{type(error).__name__}: {error}"[:300]}
        save(case, record)
    return record
