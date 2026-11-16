"""The map's code over what the real services answered (recorder.py): replayed with no network, recorded again
with `pytest --live discover -k <case>`. The other tests of discover give made-up answers, so a service that
changes what it answers is caught here, on the next recording, and nowhere else.

Each case holds a fact of the real world a sound answer must show: Coimbra has trains to Lisbon and Porto, Antwerp
to Brussels and Amsterdam; Ryanair flies from Porto and Charleroi to many places."""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from discover.flights import sources
from discover.overland import ground, sold
from discover.tests import recorder
from trip_planner.trip.places import distance_km, Place

LISBON, PORTO = (38.72, -9.14), (41.15, -8.61)
BRUSSELS, AMSTERDAM = (50.85, 4.35), (52.37, 4.90)

@dataclass(frozen=True)
class GroundCase:
    name: str
    place: Place
    reaches: tuple       # (city, (latitude, longitude), at most these minutes) a train or bus must reach straight
    trains_to: tuple     # the cities of reaches a train goes to straight, as the operator's own timetable says
    known_gap: str = ""  # why the trains are not found yet, when they are not

GROUND_CASES = [
    GroundCase("Coimbra", Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
               (("Lisbon", LISBON, 150), ("Porto", PORTO, 120)), ("Lisbon", "Porto")),
    GroundCase("Antwerp", Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.22, 4.40),
               (("Brussels", BRUSSELS, 75), ("Amsterdam", AMSTERDAM, 150)), ("Brussels", "Amsterdam")),
]

@dataclass(frozen=True)
class SoldCase:
    name: str
    place: Place
    country: str     # whose operator is asked: Rede Expressos in Portugal, Movelia in Spain
    reaches: tuple   # (city, (latitude, longitude)) a direct coach of the day must reach

SOLD_CASES = [
    SoldCase("Coimbra_coaches", GROUND_CASES[0].place, "PT", (("Lisbon", LISBON), ("Porto", PORTO))),
    SoldCase("Madrid_coaches", Place("Madrid", "Madrid", "Madrid", "Spain", 40.42, -3.70), "ES",
             (("Valencia", (39.47, -0.38)), ("Salamanca", (40.97, -5.66)))),
]

@dataclass(frozen=True)
class RyanairCase:
    name: str
    airport: str
    at_least: int   # destinations with a fare over the days asked: both are bases with dozens of routes

RYANAIR_CASES = [RyanairCase("OPO", "OPO", 15), RyanairCase("CRL", "CRL", 20)]

TIME = re.compile(r"^\d\d:\d\d$")

RECORDED: dict[tuple, dict] = {}   # the cases recorded in this run: each is asked of the services once, not once per test

def live_option(request) -> bool:
    return request.config.getoption("--live", default=False)

def recorded(request, kind: str, name: str, record) -> dict:
    """The checkpoint of a case: recorded now with --live, once per run, otherwise the saved one."""
    if live_option(request):
        if (kind, name) not in RECORDED:
            RECORDED[kind, name] = record()
        return RECORDED[kind, name]
    found = recorder.load(kind, name)
    if found is None:
        pytest.skip(f"no checkpoint saved: run pytest --live discover/tests -k {name}")
    return found

@pytest.fixture(params=GROUND_CASES, ids=lambda case: case.name)
def ground_case(request):
    """The stations reached from the case's city: replayed from its checkpoint, or asked again with --live."""
    case = request.param
    record = recorded(request, "ground", case.name, lambda: recorder.record_ground(case))
    replayed = recorder.Replayed(record["answers"])
    ground.GROUND.unlink(missing_ok=True)   # what a recording kept: the replay must read the answers, not that
    reached = ground.from_city(case.place, date.fromisoformat(record["recorded"]), say=lambda *_: None, transitous=replayed)
    return case, reached

@pytest.fixture(params=SOLD_CASES, ids=lambda case: case.name)
def sold_case(request, monkeypatch):
    """The cities the operator sells a direct coach to from the case's city: replayed, or asked again with --live."""
    case = request.param
    record = recorded(request, "sold", case.name, lambda: recorder.record_sold(case, monkeypatch))
    recorder.replay_sold(record, monkeypatch)
    sold.SOLD.unlink(missing_ok=True)   # what a recording kept: the replay must read the answers, not that
    reached = sold.from_city(case.place, date.fromisoformat(record["recorded"]), say=lambda *_: None)
    return case, record, reached

@pytest.fixture(params=RYANAIR_CASES, ids=lambda case: case.name)
def ryanair_case(request, monkeypatch):
    """Ryanair's fares from the case's airport, read by the map's code: replayed, or asked again with --live."""
    case = request.param
    record = recorded(request, "ryanair", case.name, lambda: recorder.record_ryanair(case, monkeypatch))
    days = sources.Days(*record["days"])
    monkeypatch.setattr(sources, "httpx", SimpleNamespace(get=lambda *_, **__: recorder.Answer({"fares": record["answers"]})))
    return case, days, sources.ryanair_destinations(case.airport, days)

def near(reached: list, latitude: float, longitude: float) -> list:
    return [stop for stop in reached if distance_km(latitude, longitude, stop["lat"], stop["lon"]) <= ground.MATCH_KM]

def test_a_city_reaches_its_neighbours_straight(ground_case):
    case, reached = ground_case
    for city, (latitude, longitude), most in case.reaches:
        found = near(reached, latitude, longitude)
        assert found, f"no train or bus from {case.name} reaches {city}"
        quickest = min(found, key=lambda stop: stop["minutes"])
        assert quickest["minutes"] <= most, f"{case.name} to {city} takes {quickest['minutes']} minutes by {quickest['operator']}"

def test_the_trains_between_neighbours_are_found(ground_case):
    case, reached = ground_case
    missing = [city for city, (latitude, longitude), _ in case.reaches if city in case.trains_to
               and not [stop for stop in near(reached, latitude, longitude) if stop["mode"] == "train"]]
    if missing and case.known_gap:
        pytest.xfail(case.known_gap)
    assert not missing, f"no train from {case.name} to {', '.join(missing)}"
    assert not case.known_gap, f"the trains of {case.name} are found now: remove known_gap"

def test_every_station_reached_says_how_and_when(ground_case):
    case, reached = ground_case
    assert reached, f"nothing reached from {case.name}"
    for stop in reached:
        where = f"{stop['from_stop']} -> {stop['stop']}"
        assert 0 < stop["minutes"] <= 36 * 60, where
        assert stop["operator"] and stop["mode"] in ("train", "bus"), where
        assert stop["times"] and all(TIME.match(time) for time in stop["times"]), where
        assert stop["weekdays"] and set(stop["weekdays"]) <= set(ground.WEEKDAYS), where
        assert not stop["site"] or stop["site"].startswith("https://"), where
        assert distance_km(case.place.latitude, case.place.longitude, stop["from_lat"], stop["from_lon"]) <= ground.STOP_KM, where

def test_a_national_coach_network_sells_from_a_city_to_the_big_ones(sold_case):
    case, record, reached = sold_case
    for city, (latitude, longitude) in case.reaches:
        assert near(reached, latitude, longitude), f"no direct coach sold from {case.place.written} to {city}"

def test_every_coach_sold_says_when_and_by_whom(sold_case):
    case, record, reached = sold_case
    assert reached == record["reached"], "the replay reads the answers otherwise than the recording did"
    for stop in reached:
        where = f"{stop['from_stop']} -> {stop['stop']}"
        assert stop["mode"] == "bus" and stop["operator"] and stop["source"] in sold.OPERATORS, where
        assert 0 < stop["minutes"] <= 24 * 60 and stop["times"] and all(TIME.match(time) for time in stop["times"]), where
        assert stop["weekdays"] and set(stop["weekdays"]) <= set(ground.WEEKDAYS), where

def test_ryanair_flies_from_its_bases_to_many_places(ryanair_case):
    case, days, flights = ryanair_case
    assert len({flight["to"] for flight in flights}) >= case.at_least

def test_every_ryanair_fare_is_a_flight_of_the_days_asked(ryanair_case):
    case, days, flights = ryanair_case
    for flight in flights:
        where = f"{flight['from']} -> {flight['to']} on {flight['date']}"
        assert flight["from"] == case.airport and re.match(r"^[A-Z]{3}$", flight["to"]), where
        assert days.first <= flight["date"] <= days.last, where
        assert flight["price_eur"] > 0 and flight["back"] is None, where
        assert TIME.match(flight["time"]) and TIME.match(flight["arrival_time"]), where
        # Times are on two local clocks: a flight of Ryanair lands at most a day after it leaves
        assert flight["arrival_date"] in (flight["date"], (date.fromisoformat(flight["date"]) + timedelta(days=1)).isoformat()), where
        assert flight["segments"][0]["flight"], where
        assert flight["url"].startswith(sources.RYANAIR_BOOKING), where
