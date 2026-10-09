"""`pytest` replays the checkpoints saved in the tests folders: no network, a few seconds.
`pytest --live` asks the real services again and saves new checkpoints: several minutes.
`pytest --live -k BRU` does that for one airport, `pytest --live tests/departure` for one direction."""
import asyncio
from datetime import date, timedelta

import pytest

from tests import recorder
from trip_planner import current
from trip_planner.config import TripSettings
from trip_planner.places import as_place, distance_km, SETTLEMENTS
from trip_planner.sources import timetables

def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="ask the real services and save new checkpoints")

def trip_settings() -> TripSettings:
    """A trip far enough ahead to be looked up in a typical timetable, leaving at any time, with the default limits."""
    day = date.today() + timedelta(days=recorder.DAYS_AHEAD)
    return TripSettings(origin="Coimbra", destination="Antwerp", dates=[f"{day:%d/%m/%Y}"], earliest_leave="", latest_leave="")

@pytest.fixture(scope="session")
def live(request) -> bool:
    return request.config.getoption("--live")

@pytest.fixture(autouse=True)
def settings() -> TripSettings:
    current.SETTINGS = trip_settings()
    return current.SETTINGS

@pytest.fixture(scope="session")
def transit(live):
    """One connection to the timetables for the whole run, on one event loop. None when replaying."""
    if not live:
        yield None
        return
    current.SETTINGS = trip_settings()   # the lookups read the trip's limits, before the per-test settings exist
    loop = asyncio.new_event_loop()
    client = timetables.Timetables()
    yield loop, client
    loop.run_until_complete(client.close())
    loop.close()

def recorded(case, direction: str, live: bool, transit) -> dict:
    """The checkpoints of one case: recorded now with --live, otherwise the saved ones."""
    if live:
        loop, client = transit
        return loop.run_until_complete(recorder.record_case(case, direction, client))
    found = recorder.load(case, direction)
    if found is None:
        pytest.skip("no checkpoint saved: run pytest --live")
    return found

def needs(found: dict, stage: str) -> None:
    """Stop a test whose stage was never reached, saying which earlier stage failed."""
    if stage not in found["reached"]:
        error = found.get("error") or {}
        pytest.fail(f"stage '{stage}' was not reached: stage '{error.get('stage')}' failed with {error.get('message')}")

def gap(case, found: dict) -> str:
    """Why this case cannot work in the direction recorded. Empty when it must."""
    return case.known_gap if case.gap_in in ("", found["direction"]) else ""

def expect_gap(case, found: dict) -> None:
    if gap(case, found):
        pytest.xfail(gap(case, found))

# ---- the stages that are the same in both directions

def check_city(case, found: dict) -> None:
    """The place is a city, town or village, not the region or country of the same name."""
    needs(found, "place")
    place = as_place(case.city, found["place_results"])
    chosen = next(result for result in found["place_results"] if float(result["lat"]) == place.latitude)
    assert chosen["addresstype"] in SETTLEMENTS, f"{case.city} was located as a {chosen['addresstype']}: {chosen['display_name']}"
    airport = found["airport"]
    assert distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"]) < 400

def check_terminal(case, found: dict) -> None:
    """When OpenStreetMap gives a terminal, it is on the airfield. Without one the stops are chosen by their name."""
    needs(found, "terminal")
    if found["terminal"] is None:
        pytest.skip("terminal not known: the airport's stops are found by name")
    airport = found["airport"]
    assert distance_km(found["terminal"][0], found["terminal"][1], airport["latitude"], airport["longitude"]) <= 4

def check_stops(case, found: dict) -> None:
    """The timetables know a stop of the airport, a walk away from the terminal."""
    needs(found, "stops")
    stops = timetables.choose_airport_stops(found["stops_around"], found["airport"], found["terminal"])
    if not stops and found.get("answer", {}).get("options"):
        pytest.skip("no stop of the airport is known, the connections come from the point on the map")
    if not stops:
        expect_gap(case, found)
    assert stops, f"none of the {len(found['stops_around'])} stops around {case.airport} is the airport's"
    assert all(timetables.MIN_STOP_WALK_MINUTES <= walk <= timetables.AIRFIELD_WALK_MINUTES for stop, walk in stops)

def check_covered(case, found: dict) -> dict:
    """Real connections were found, unless the case is a known gap. Returns the answer of the timetables."""
    needs(found, "connections")
    answer = found["answer"]
    if not answer["options"]:
        expect_gap(case, found)
    assert answer["covered"] and answer["options"], f"no timetable covers {case.airport} and {case.city}"
    assert not gap(case, found), f"{case.name} is listed as a known gap but connections were found: remove known_gap"
    return answer

def reasonable_minutes(case, found: dict) -> float:
    """The longest a trip may take before it means something else is wrong, such as the place being located
    in the wrong spot. A small town far away takes changes and waiting."""
    km = found["airport"]["km"]
    return 180 + km * 4 if case.remote else 90 + km * 2.5
