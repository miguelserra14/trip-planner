"""From the origin to the departure airport, for a city and a small town of every country of the European Union.
The same places and stages as tests/arrival, in the other direction: the connections must reach the terminal in
time for the flight."""
import pytest

from tests.cases import CASES
from tests.conftest import check_city, check_covered, graded, check_stops, check_terminal, expect_gap, needs, reasonable_minutes, recorded, too_many_rides
from trip_planner import current
from trip_planner.itineraries.legs import Rejected, to_airport_leg
from trip_planner.trip.places import as_place, TripPlaces
from trip_planner.sources import timetables
from trip_planner.utils import to_minutes

@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def record(request, live, transit):
    return request.param, recorded(request.param, "departure", live, transit)

def test_1_place_is_located(record):
    check_city(*record)

def test_2_terminal_at_airport(record):
    check_terminal(*record)

def test_3_airport_has_stops(record):
    check_stops(*record)

def test_4_connections_in_time(record):
    """Real connections reach the terminal before the flight closes. They leave that day, or on the evening before."""
    case, found = record
    answer = check_covered(case, found)
    take_off = to_minutes(found["flight"]["departure_time"])
    for option in answer["options"]:
        assert -current.SETTINGS.night_minutes <= option["leave"] < option["arrive"] <= take_off - current.SETTINGS.airport_floor_minutes
        assert option["steps"] and graded(option)
    at_terminal = take_off - current.SETTINGS.min_airport_minutes
    assert any(option["arrive"] <= at_terminal for option in answer["options"]), "none leaves the time asked for at the airport"

def test_5_itinerary_leg(record):
    """The leg the itinerary gets is a real connection that leaves the time asked for at the airport."""
    case, found = record
    needs(found, "connections")
    if not found["answer"]["options"]:
        expect_gap(case, found)
    place = as_place(case.city, found["place_results"])
    current.TRIP = TripPlaces(place, place, [found["airport"]], [], [found["airport"]])
    if too_many_rides(found):
        with pytest.raises(Rejected) as refused:
            to_airport_leg(found["flight"], {found["flight"]["id"]: {"to_airport": found["answer"]}}, None)
        assert refused.value.kind == "ground_choice"
        return
    leg = to_airport_leg(found["flight"], {found["flight"]["id"]: {"to_airport": found["answer"]}}, None)
    assert leg["kind"] == "timetable" and graded(leg)
    assert leg["steps"] and leg["price_eur"] >= 0
    assert leg["arrive"] <= to_minutes(found["flight"]["departure_time"]) - current.SETTINGS.min_airport_minutes
    assert leg["arrive"] - leg["leave"] <= reasonable_minutes(case, found), f"{leg['arrive'] - leg['leave']} minutes for {found['airport']['km']} km"
