"""The rules of the arrival leg on small made-up inputs: no service, no checkpoint."""
import math

from trip_planner import places
from trip_planner.config import TripSettings
from trip_planner.places import choose_airports, Place, read_road_hours, settlement
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

def test_a_municipality_gives_way_to_the_town_inside_it():
    municipality = {"addresstype": "municipality", "importance": 0.53, "lat": "46.356", "lon": "13.614"}
    town = {"addresstype": "town", "importance": 0.50, "lat": "46.338", "lon": "13.552"}
    assert settlement([municipality, town]) is town

def test_a_municipality_is_kept_when_the_town_of_its_name_is_elsewhere():
    municipality = {"addresstype": "municipality", "importance": 0.53, "lat": "46.356", "lon": "13.614"}
    namesake = {"addresstype": "town", "importance": 0.50, "lat": "48.0", "lon": "16.0"}
    assert settlement([municipality, namesake]) is municipality

def test_a_city_first_is_kept():
    city = {"addresstype": "city", "importance": 0.6}
    assert settlement([city, {"addresstype": "town", "importance": 0.9}]) is city

def test_hours_by_road_are_read_from_the_answer():
    airports = [{"code": "BRI", "km": 9}, {"code": "DBV", "km": 198}, {"code": "ISL", "km": 120}, {"code": "FEZ", "km": 239}]
    table = {"durations": [[0, 720, 65880, None, 360]], "distances": [[0, 11000, 1658000, None, 1000]]}
    hours = read_road_hours(airports, table)
    assert hours["BRI"] == 0.2 and round(hours["DBV"], 1) == 18.3
    assert hours["ISL"] == math.inf, "no road or ferry reaches it"
    assert "FEZ" not in hours, "a road of 1 km to an airport 239 km away says nothing about it"

def bari_trip(monkeypatch, hours_to_dubrovnik: float | None, **settings) -> places.TripPlaces:
    """The airports of a trip from Antwerp to Bari, with made-up hours by road: Dubrovnik is 198 km from Bari."""
    monkeypatch.setitem(places.PLACES, "antwerp", Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.2211, 4.3997))
    monkeypatch.setitem(places.PLACES, "bari", Place("Bari", "Bari", "Bari", "Italy", 41.1258, 16.8621))
    known = lambda airport: 1.0 if airport["code"] != "DBV" else hours_to_dubrovnik
    monkeypatch.setattr(places, "road_hours", lambda place, airports: {a["code"]: known(a) for a in airports if known(a) is not None})
    return choose_airports(TripSettings(origin="Antwerp", destination="Bari", dates=["tomorrow"], **settings))

def test_an_airport_across_the_sea_is_left_out(monkeypatch):
    trip = bari_trip(monkeypatch, 18.3)
    assert [airport["code"] for airport in trip.arrival] == ["BRI", "BDS", "QSR"], "Salerno, by land, takes the place of Dubrovnik"
    assert trip.too_far == {"DBV": 18.3}

def test_an_airport_the_traveller_adds_is_used_however_far_by_road(monkeypatch):
    trip = bari_trip(monkeypatch, 18.3, include_airports=["DBV"])
    assert "DBV" in [airport["code"] for airport in trip.arrival] and not trip.too_far

def test_without_the_hours_by_road_the_straight_line_decides(monkeypatch):
    trip = bari_trip(monkeypatch, None)
    assert "DBV" in [airport["code"] for airport in trip.arrival] and not trip.too_far

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
