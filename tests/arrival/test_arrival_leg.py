"""From the arrival airport to the destination, for an airport of every country of the European Union.

Each test is one stage of that leg, so a failure says where a country breaks: locating the city, the terminal,
the airport's stops, the connections after landing, or choosing the leg of the itinerary.

Without --live the stages run on the checkpoints saved in checkpoints/: what the services answered when they
were recorded goes through today's code. With --live the services are asked again and the checkpoints replaced."""
import asyncio

import pytest

from tests.arrival import recorder
from tests.arrival.cases import CASES
from trip_planner import current
from trip_planner.itineraries.legs import from_airport_leg
from trip_planner.places import as_place, distance_km, SETTLEMENTS
from trip_planner.sources import timetables
from trip_planner.utils import to_minutes

@pytest.fixture(scope="module")
def transit(live):
    """One connection to the timetables for the whole run, on one event loop."""
    if not live:
        yield None
        return
    loop = asyncio.new_event_loop()
    client = timetables.Timetables()
    yield loop, client
    loop.run_until_complete(client.close())
    loop.close()

@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def record(request, live, transit, settings_for_recording):
    case = request.param
    if live:
        loop, client = transit
        found = loop.run_until_complete(recorder.record_case(case, client))
    else:
        found = recorder.load(case)
        if found is None:
            pytest.skip("no checkpoint saved: run pytest --live")
    return case, found

@pytest.fixture(scope="module")
def settings_for_recording():
    """The recording reads the trip's limits (time to leave the airport), before the per-test settings exist."""
    from datetime import date, timedelta
    from trip_planner.config import TripSettings
    day = date.today() + timedelta(days=recorder.DAYS_AHEAD)
    current.SETTINGS = TripSettings(origin="Coimbra", destination="Antwerp", dates=[f"{day:%d/%m/%Y}"], earliest_leave="", latest_leave="")

def needs(found: dict, stage: str) -> None:
    """Stop a test whose stage was never reached, saying which earlier stage failed."""
    if stage not in found["reached"]:
        error = found.get("error") or {}
        pytest.fail(f"stage '{stage}' was not reached: stage '{error.get('stage')}' failed with {error.get('message')}")

def expect_gap(case) -> None:
    if case.known_gap:
        pytest.xfail(case.known_gap)

def test_1_city_is_located(record):
    """The destination is a city, town or village, not the region or country of the same name."""
    case, found = record
    needs(found, "place")
    place = as_place(case.city, found["place_results"])
    chosen = next(result for result in found["place_results"] if float(result["lat"]) == place.latitude)
    assert chosen["addresstype"] in SETTLEMENTS, f"{case.city} was located as a {chosen['addresstype']}: {chosen['display_name']}"
    airport = found["airport"]
    assert distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"]) < 300

def test_2_terminal_is_at_the_airport(record):
    """When OpenStreetMap gives a terminal, it is on the airfield. Without one the stops are chosen by their name."""
    case, found = record
    needs(found, "terminal")
    if found["terminal"] is None:
        pytest.skip("terminal not known: the airport's stops are found by name")
    airport = found["airport"]
    assert distance_km(found["terminal"][0], found["terminal"][1], airport["latitude"], airport["longitude"]) <= 4

def test_3_airport_has_stops(record):
    """The timetables know a stop of the airport, a walk away from the terminal."""
    case, found = record
    needs(found, "stops")
    stops = timetables.choose_airport_stops(found["stops_around"], found["airport"], found["terminal"])
    if not stops and found.get("answer", {}).get("options"):
        pytest.skip("no stop of the airport is known, the connections come from the point on the map")
    if not stops:
        expect_gap(case)
    assert stops, f"none of the {len(found['stops_around'])} stops around {case.airport} is the airport's"
    assert all(timetables.MIN_STOP_WALK_MINUTES <= walk <= timetables.AIRFIELD_WALK_MINUTES for stop, walk in stops)

def test_4_connections_after_landing(record):
    """Real connections leave after the traveller is out of the terminal and arrive the same day."""
    case, found = record
    needs(found, "connections")
    answer = found["answer"]
    if not answer["options"]:
        expect_gap(case)
    assert answer["covered"] and answer["options"], f"no timetable covers {case.airport} -> {case.city}"
    assert not case.known_gap, f"{case.name} is listed as a known gap but connections were found: remove known_gap"
    ready = to_minutes(found["flight"]["arrival_time"]) + current.SETTINGS.min_landing_minutes
    for option in answer["options"]:
        assert ready <= option["leave"] < option["arrive"] < 24 * 60
        assert option["steps"] and option["grade"] in "AB"

def test_5_leg_of_the_itinerary(record):
    """The leg the itinerary gets is a real connection, with a schedule read from a timetable."""
    case, found = record
    needs(found, "connections")
    if not found["answer"]["options"]:
        expect_gap(case)
    leg = from_airport_leg(found["flight"], {found["flight"]["id"]: {"from_airport": found["answer"]}}, None)
    assert leg["kind"] == "timetable" and leg["grade"] in "AB"
    assert leg["steps"] and leg["price_eur"] >= 0
    # A direct line to a city an hour away is not a five-hour trip: the destination was located in the wrong place
    km = found["airport"]["km"]
    assert leg["arrive"] - leg["leave"] <= 90 + km * 2.5, f"{leg['arrive'] - leg['leave']} minutes for {km} km"
