"""The rules of the arrival leg on small made-up inputs: no service, no checkpoint."""
from trip_planner.places import settlement
from trip_planner.sources import timetables
from trip_planner.sources.timetables import choose_airport_stops

AIRPORT = {"code": "XXX", "latitude": 50.9000, "longitude": 4.4800}
TERMINAL = [50.8980, 4.4840]

def stop(name: str, modes: list[str], north_km: float, identity: str = "") -> dict:
    """A stop a number of km north of the terminal."""
    return {"name": name, "stopId": identity or name, "modes": modes, "lat": TERMINAL[0] + north_km / 111, "lon": TERMINAL[1]}

def test_region_of_the_same_name_gives_way_to_the_city():
    province = {"addresstype": "county", "importance": 0.72, "lat": "45.04", "lon": "9.14"}
    city = {"addresstype": "city", "importance": 0.66, "lat": "45.19", "lon": "9.16"}
    assert settlement([province, city]) is city

def test_a_far_less_important_namesake_does_not_replace_the_first_result():
    country = {"addresstype": "country", "importance": 0.9}
    village = {"addresstype": "village", "importance": 0.3}
    assert settlement([country, village]) is country

def test_a_city_first_is_kept():
    city = {"addresstype": "city", "importance": 0.6}
    assert settlement([city, {"addresstype": "town", "importance": 0.9}]) is city

def test_trains_come_before_buses_and_coaches():
    stops = [stop("Bus", ["BUS"], 0.1), stop("Station", ["REGIONAL_RAIL"], 0.3), stop("Coach", ["COACH"], 0.05)]
    assert [identity for identity, walk in choose_airport_stops(stops, AIRPORT, TERMINAL)] == ["Station", "Bus"]

def test_stops_far_from_the_terminal_are_not_the_airports():
    stops = [stop("Village", ["BUS"], 1.5), stop("Next town station", ["REGIONAL_RAIL"], 3.0)]
    assert choose_airport_stops(stops, AIRPORT, TERMINAL) == []

def test_a_stop_only_planes_use_is_ignored():
    assert choose_airport_stops([stop("Aeroporto (XXX)", ["AIRPLANE"], 0.0)], AIRPORT, TERMINAL) == []

def test_without_a_terminal_the_stops_named_after_the_airport_are_used():
    stops = [stop("Wahnheide", ["BUS"], 0.2), stop("Flughafen Bf", ["SUBURBAN"], 2.0), stop("Airport Terminal 1", ["BUS"], 2.1)]
    chosen = choose_airport_stops(stops, AIRPORT, None)
    assert [identity for identity, walk in chosen] == ["Flughafen Bf", "Airport Terminal 1"]
    assert all(walk == timetables.AIRFIELD_WALK_MINUTES for identity, walk in chosen)

def test_the_walk_to_a_stop_grows_with_the_distance():
    near, far = choose_airport_stops([stop("Station", ["REGIONAL_RAIL"], 0.05), stop("Bus", ["BUS"], 0.65)], AIRPORT, TERMINAL)
    assert near[1] == timetables.MIN_STOP_WALK_MINUTES and far[1] > near[1]
