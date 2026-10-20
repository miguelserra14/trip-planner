"""From the arrival airport to the destination, for a city and a small town of every country of the European Union.

Each test is one stage of that leg, so a failure says where a country breaks: locating the place, the terminal,
the airport's stops, the connections after landing, or choosing the leg of the itinerary.

Without --live the stages run on the checkpoints saved in checkpoints/: what the services answered when they
were recorded goes through today's code. With --live the services are asked again and the checkpoints replaced."""
import pytest

from tests.cases import CASES
from tests.conftest import check_city, check_covered, check_stops, check_terminal, expect_gap, needs, reasonable_minutes, recorded
from trip_planner import current
from trip_planner.itineraries.legs import from_airport_leg
from trip_planner.sources import timetables
from trip_planner.utils import to_minutes

@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def record(request, live, transit):
    return request.param, recorded(request.param, "arrival", live, transit)

def test_1_place_is_located(record):
    check_city(*record)

def test_2_terminal_is_at_the_airport(record):
    check_terminal(*record)

def test_3_airport_has_stops(record):
    check_stops(*record)

def test_4_connections_after_landing(record):
    """Real connections leave after the traveller is out of the terminal, the same day, and arrive that day or
    in the first hours of the night after it."""
    case, found = record
    answer = check_covered(case, found)
    ready = to_minutes(found["flight"]["arrival_time"]) + current.SETTINGS.min_landing_minutes
    for option in answer["options"]:
        assert ready <= option["leave"] < 24 * 60 and option["leave"] < option["arrive"] < 24 * 60 + current.SETTINGS.night_minutes
        assert option["steps"] and option["grade"] in "AB"

def test_5_leg_of_the_itinerary(record):
    """The leg the itinerary gets is a real connection, with a schedule read from a timetable."""
    case, found = record
    needs(found, "connections")
    if not found["answer"]["options"]:
        expect_gap(case, found)
    leg = from_airport_leg(found["flight"], {found["flight"]["id"]: {"from_airport": found["answer"]}}, None)
    assert leg["kind"] == "timetable" and leg["grade"] in "AB"
    assert leg["steps"] and leg["price_eur"] >= 0
    assert leg["arrive"] - leg["leave"] <= reasonable_minutes(case, found), f"{leg['arrive'] - leg['leave']} minutes for {found['airport']['km']} km"
