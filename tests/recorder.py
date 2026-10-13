"""Follows one trip between an airport and a place through the real services, and saves what each stage received
as a checkpoint. A stage that fails leaves the checkpoints of the stages before it, and its error.

The stages, in the order the program goes through them:
1. place        the city is located (Nominatim)
2. terminal     the passenger terminal of the airport is located (OpenStreetMap)
3. stops        the stops around the airport that can be its own are listed (Transitous)
4. connections  the connections are looked up (Transitous): after landing when the direction is "arrival",
                in time for the flight when it is "departure\""""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trip_planner.places import airport_from, as_place, search_places
from trip_planner.sources import timetables

STAGES = ("place", "terminal", "stops", "connections")
LANDING = "11:00"    # arrival: the traveller has the rest of the day to reach the destination
TAKE_OFF = "17:00"   # departure: the traveller has the day to reach the airport
DAYS_AHEAD = 70      # far enough for the travel date not to be published: the typical timetable is what is tested

def checkpoint_file(case, direction: str) -> Path:
    return Path(__file__).parent / direction / "checkpoints" / f"{case.name}.json"

def load(case, direction: str) -> dict[str, Any] | None:
    path = checkpoint_file(case, direction)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

def save(case, direction: str, record: dict[str, Any]) -> None:
    path = checkpoint_file(case, direction)
    path.parent.mkdir(exist_ok=True)
    # One line per stage: short enough to keep in the repository, and a change shows as the line of its stage
    lines = [f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}" for key, value in record.items()]
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")

def trimmed_place(result: dict[str, Any]) -> dict[str, Any]:
    """What the program reads of a Nominatim result."""
    names = result.get("namedetails") or {}
    return {"name": result.get("name"), "display_name": result.get("display_name"), "addresstype": result.get("addresstype"),
            "importance": result.get("importance"), "lat": result["lat"], "lon": result["lon"],
            "namedetails": {key: names[key] for key in ("name", "name:en") if key in names},
            "address": {"country": (result.get("address") or {}).get("country", "")}}

async def record_case(case, direction: str, transit: timetables.Timetables) -> dict[str, Any]:
    """Go through the stages for one case, saving a checkpoint after each."""
    day = (date.today() + timedelta(days=DAYS_AHEAD)).isoformat()
    flight = ({"id": case.name, "date": day, "arrival_airport": case.airport, "arrival_time": LANDING} if direction == "arrival"
              else {"id": case.name, "date": day, "departure_airport": case.airport, "departure_time": TAKE_OFF})
    record: dict[str, Any] = {"case": vars(case), "direction": direction, "recorded": date.today().isoformat(),
                              "flight": flight, "reached": []}

    def reached(stage: str) -> None:
        record["reached"].append(stage)
        save(case, direction, record)

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
        # Only the stops that can be the airport's are kept: a big airport has hundreds of others around it
        record["stops_around"] = [{key: stop.get(key) for key in ("name", "stopId", "lat", "lon", "modes")}
                                  for stop in timetables.airport_stop_candidates(around, airport, terminal)]
        stops = timetables.choose_airport_stops(record["stops_around"], airport, terminal)
        reached("stops")

        spot, city = tuple(centre), (place.latitude, place.longitude)
        airport_zone, city_zone = await transit.zone(*spot, airport["country"]), await transit.zone(*city)
        record["zones"] = [airport_zone, city_zone]
        if direction == "arrival":
            record["answer"] = await transit.from_airport(spot, city, flight, (airport_zone, city_zone), terminal is not None, stops)
        else:
            record["answer"] = await transit.to_airport(city, spot, flight, (city_zone, airport_zone), terminal is not None, stops)
        reached("connections")
    except Exception as error:
        record["error"] = {"stage": STAGES[len(record["reached"])], "message": f"{type(error).__name__}: {error}"[:300]}
        save(case, direction, record)
    return record
