"""The page while a search runs (waiting.py): the trip on a map, the steps with what each found, and about how long is
left, from what the planner says as data (utils.progress) and the searches before. Made-up trips, no network."""
import sys

from app import search
from app.waiting import expected_seconds, FIRST_GUESS, frame, SHAPE, steps_list, time_left, trip_map, waiting_part
from trip_planner.report import full

ANTWERP, COIMBRA = {"name": "Antwerp", "lat": 51.22, "lon": 4.40}, {"name": "Coimbra", "lat": 40.21, "lon": -8.43}
BRUSSELS = {"name": "Brussels", "lat": 50.85, "lon": 4.35}
TRIP = {"origin": ANTWERP, "destination": COIMBRA, "ground_only": False,
        "departure": [{"code": "BRU", "lat": 50.90, "lon": 4.48}, {"code": "CRL", "lat": 50.46, "lon": 4.45}],
        "arrival": [{"code": "OPO", "lat": 41.24, "lon": -8.68}]}
BY_GROUND = {"origin": ANTWERP, "destination": BRUSSELS, "ground_only": True, "departure": [], "arrival": []}

def way(trip: dict = TRIP, step: str = "", counts: dict | None = None) -> dict:
    return {"trip": trip, "step": step, "counts": counts or {}}

# ---------------------------------------------------------------- the map
def test_the_map_holds_every_place_and_keeps_a_shape_a_page_can_show():
    for points in ([(4.4, 51.2), (-8.4, 40.2)], [(4.4, 51.2), (4.35, 50.85)], [(4.4, 60.0), (4.4, 36.0)]):
        west, south, east, north, squeeze = frame(points)
        assert all(west < lon < east and south < lat < north for lon, lat in points)
        assert SHAPE[0] - 0.01 <= (east - west) * squeeze / (north - south) <= SHAPE[1] + 0.01

def test_the_map_draws_the_land_the_cities_the_airports_and_the_flights_between_them():
    drawn = trip_map(TRIP)
    assert 'class="land" d="M' in drawn and drawn.count('class="city"') == 2 and drawn.count('class="airport"') == 3
    assert drawn.count('class="flight"') == 2 and drawn.count('class="road"') == 3   # two airports to one, and to and from them
    assert ">Antwerp</text>" in drawn and ">Coimbra</text>" in drawn and 'aria-label="' in drawn

def test_without_a_plane_the_map_has_one_way_from_city_to_city():
    drawn = trip_map(BY_GROUND)
    assert drawn.count('class="flight ground"') == 1 and 'class="airport"' not in drawn

def test_a_name_too_close_to_another_is_said_on_hover():
    crowded = {**TRIP, "departure": [{"code": "BRU", "lat": 50.90, "lon": 4.48}, {"code": "ANR", "lat": 51.19, "lon": 4.46}]}
    drawn = trip_map(crowded)
    assert "<title>ANR</title>" in drawn and ">Antwerp</text>" in drawn   # the city keeps its name, the airport by it its dot

def test_a_name_of_a_place_is_text_on_the_map_never_markup():
    drawn = trip_map({**BY_GROUND, "destination": {**BRUSSELS, "name": "<script>x</script>"}})
    assert "<script>" not in drawn and "&lt;script&gt;" in drawn

# ---------------------------------------------------------------- the steps
def test_each_step_is_done_running_or_to_come_with_what_it_found():
    listed = steps_list(way(step="search_ground_transport", counts={"search_flights": 73}))
    assert listed.count('class="done"') == 2 and listed.count('class="running"') == 1 and listed.count('class="next"') == 2
    assert "73 flights found" in listed and "2 to leave from, 1 to land at" in listed

def test_a_trip_without_a_plane_has_its_own_steps():
    listed = steps_list(way(BY_GROUND, "rank_by_ground_only", {"search_by_ground_only": 4}))
    assert "Trains and coaches all the way" in listed and "4 trips priced" in listed and "Flights" not in listed

def test_the_recommendation_is_done_once_the_way_is():
    assert steps_list(way(step="done", counts={"search_flights": 9, "search_ground_transport": 8, "build_itineraries": 5})).count('class="done"') == 5

# ---------------------------------------------------------------- how long is left
def test_the_time_expected_is_the_median_of_the_last_searches_of_the_same_kind():
    history = [{"seconds": 100, "ground_only": False}, {"seconds": 400, "ground_only": False}, {"seconds": 300, "ground_only": False},
               {"seconds": 5, "ground_only": True}]
    assert expected_seconds("plane", history) == (300, 3) and expected_seconds("ground", history) == (5, 1)
    assert expected_seconds("discover_light", history + [{"seconds": 90, "kind": "discover_light"}]) == (90, 1)

def test_with_no_search_to_compare_the_first_guess_is_said_to_be_one():
    assert expected_seconds("plane", []) == (FIRST_GUESS["plane"], 0)
    said = time_left([way()], False, 60, [])
    assert "About 3 min left" in said and "first guess of 4 min" in said

def test_a_round_trip_takes_both_ways_and_a_long_search_says_it_is_nearly_done():
    history = [{"seconds": 120, "ground_only": False}]
    assert "About 3 min left" in time_left([way()], True, 60, history)
    assert "longer than usual" in time_left([way()], False, 500, history)

def test_the_durations_are_kept_beside_the_reports_the_newest_only():
    for seconds in range(full.DURATIONS_KEPT + 5):
        full.remember_duration(seconds, "plane")
    kept = full.past_durations()
    assert len(kept) == full.DURATIONS_KEPT and kept[-1]["seconds"] == full.DURATIONS_KEPT + 4
    full.durations_file().write_text("not json", encoding="utf-8")
    assert full.past_durations() == []

# ---------------------------------------------------------------- the page
def test_the_search_keeps_where_each_way_is_and_shows_it_while_it_works():
    found = search.Job({"settings": {"return_dates": ""}}, [sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        found.read_event("progress", {"trip": TRIP})
        found.read_event("progress", {"step": "search_flights"})
        found.read_event("progress", {"found": "search_flights", "count": 12})
        assert found.ways == [way(step="search_flights", counts={"search_flights": 12})] and found.started
        page = search.live_part(found, "token", str, str)
        assert 'class="card waiting"' in page and "12 flights found" in page and 'action="/stop"' in page
        found.read_event("progress", {"step": "done"})
        assert 'class="card waiting"' not in search.live_part(found, "token", str, str)   # the way is searched: the result comes
    finally:
        found.stop()

def test_the_second_way_of_a_round_trip_is_said_to_be_the_second():
    assert "Way 2 of 2" in waiting_part([way(), way({**TRIP, "origin": COIMBRA, "destination": ANTWERP})], True, 10, [])

def test_a_discover_map_has_its_own_steps_and_no_destination_yet():
    trip = {"discover": True, "source": "ground", "ground_only": True, "departure": [], "arrival": [], "origin": COIMBRA}
    listed = steps_list(way(trip, "discover_ground"))
    assert "Trains and coaches from the city" in listed and "Flights" not in listed and listed.count("<li") == 1
    drawn = trip_map({**trip, "source": "light", "ground_only": False, "departure": TRIP["arrival"]})
    assert drawn.count('class="city"') == 1 and drawn.count('class="airport"') == 1 and ">Coimbra</text>" in drawn
