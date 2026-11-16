"""The map's code over what the real services answered (recorder.py): replayed with no network, recorded again
with `pytest --live discover -k <case>`. The other tests of discover give made-up answers, so a service that
changes what it answers is caught here, on the next recording, and nowhere else.

Each case holds a fact of the real world a sound answer must show: Coimbra has trains to Lisbon and Porto, Antwerp
to Brussels and Amsterdam; Ryanair flies from Porto and Charleroi to many places; Marignane's airport serves
Marseille. OpenFlights (routes.py) has none: its files were last changed in 2014, and rows_of checks each one."""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from discover.airports import access
from discover.flights import flown, sources
from discover.overland import ground, sold
from discover.tests import recorder
from trip_planner.trip.places import distance_km, load_airports, Place

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

@dataclass(frozen=True)
class FlownCase:
    name: str
    airport: str
    at_least: int      # destinations of the table: both airports fly to dozens
    flies_to: tuple    # routes flown for years, by the airline that flies them
    airlines: tuple

FLOWN_CASES = [FlownCase("OPO_routes", "OPO", 40, (("LIS", "TAP Air Portugal"), ("STN", "Ryanair")), ("Ryanair", "TAP Air Portugal")),
               FlownCase("CRL_routes", "CRL", 60, (("FCO", "Ryanair"), ("OPO", "Ryanair")), ("Ryanair", "Wizz Air"))]

@dataclass(frozen=True)
class WikidataCase:
    name: str
    serves: dict       # IATA code: the city travellers go to from that airport
    not_cities: dict   # IATA code: a place Wikidata gives that is not a city and must be left out

WIKIDATA_CASES = [WikidataCase("airport_cities", {"MRS": "Marseille", "BGY": "Bergamo", "STN": "London", "OPO": "Porto", "RBA": "Rabat"},
                               {"HHN": "Rhineland-Palatinate"})]

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

@pytest.fixture(params=FLOWN_CASES, ids=lambda case: case.name)
def flown_case(request, monkeypatch, tmp_path):
    """The routes flown from the case's airport, read from its Wikipedia table: replayed, or read again with --live."""
    case = request.param
    monkeypatch.setattr(flown, "ROUTES", tmp_path / "airport_routes.json")
    record = recorded(request, "flown", case.name, lambda: recorder.record_flown(case, monkeypatch))
    recorder.replay_sparql(record["answers"], monkeypatch)
    flown.ROUTES.unlink(missing_ok=True)   # what a recording kept: the replay must read the answers, not that
    extract = lambda url: record["articles"][url]
    found = flown.routes([case.airport], date.fromisoformat(record["recorded"]), say=lambda *_: None, extract=extract)
    return case, record, found.get(case.airport, {}).get("routes", [])

@pytest.fixture(params=WIKIDATA_CASES, ids=lambda case: case.name)
def wikidata_case(request, monkeypatch):
    """Wikidata's answer about the case's airports: replayed, or asked again with --live."""
    case = request.param
    record = recorded(request, "wikidata", case.name, lambda: recorder.record_wikidata(
        WikidataCase(case.name, {**case.serves, **case.not_cities}, {}), monkeypatch))
    recorder.replay_sparql(record["answers"], monkeypatch)
    return case, access.ask_wikidata(sorted({**case.serves, **case.not_cities}))

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

def test_city_reaches_neighbours(ground_case):
    case, reached = ground_case
    for city, (latitude, longitude), most in case.reaches:
        found = near(reached, latitude, longitude)
        assert found, f"no train or bus from {case.name} reaches {city}"
        quickest = min(found, key=lambda stop: stop["minutes"])
        assert quickest["minutes"] <= most, f"{case.name} to {city} takes {quickest['minutes']} minutes by {quickest['operator']}"

def test_neighbour_trains_found(ground_case):
    case, reached = ground_case
    missing = [city for city, (latitude, longitude), _ in case.reaches if city in case.trains_to
               and not [stop for stop in near(reached, latitude, longitude) if stop["mode"] == "train"]]
    if missing and case.known_gap:
        pytest.xfail(case.known_gap)
    assert not missing, f"no train from {case.name} to {', '.join(missing)}"
    assert not case.known_gap, f"the trains of {case.name} are found now: remove known_gap"

def test_stations_say_how_when(ground_case):
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

def test_national_coaches_sold(sold_case):
    case, record, reached = sold_case
    for city, (latitude, longitude) in case.reaches:
        assert near(reached, latitude, longitude), f"no direct coach sold from {case.place.written} to {city}"

def test_coaches_say_when_who(sold_case):
    case, record, reached = sold_case
    assert reached == record["reached"], "the replay reads the answers otherwise than the recording did"
    for stop in reached:
        where = f"{stop['from_stop']} -> {stop['stop']}"
        assert stop["mode"] == "bus" and stop["operator"] and stop["source"] in sold.OPERATORS, where
        assert 0 < stop["minutes"] <= 24 * 60 and stop["times"] and all(TIME.match(time) for time in stop["times"]), where
        assert stop["weekdays"] and set(stop["weekdays"]) <= set(ground.WEEKDAYS), where

def test_wikipedia_routes_read(flown_case):
    case, record, routes = flown_case
    assert routes == record["routes"][case.airport]["routes"], "the replay reads the table otherwise than the recording did"
    assert len({route["to"] for route in routes}) >= case.at_least
    airlines = {route["airline"] for route in routes}
    assert set(case.airlines) <= airlines, f"airlines read: {sorted(airlines)[:20]}"
    for code, airline in case.flies_to:
        assert any(route["to"] == code and route["airline"] == airline for route in routes), f"{case.airport} -> {code} by {airline}"

def test_routes_are_known_airports(flown_case):
    case, record, routes = flown_case
    airports = load_airports()
    assert all(route["to"] in airports and route["to"] != case.airport for route in routes)
    assert any(route["seasonal"] for route in routes) and not all(route["seasonal"] for route in routes)

def test_served_city_is_real(wikidata_case):
    case, rows = wikidata_case
    airports = load_airports()
    for code, city in case.serves.items():
        chosen = access.served([row for row in rows if row["iata"] == code], airports[code])
        assert chosen and chosen["name"] == city, f"{code} serves {chosen and chosen['name']}"
    for code, region in case.not_cities.items():
        assert region not in {row["cityLabel"] for row in rows if row["iata"] == code}, f"{code}: {region} is no city"

def test_ryanair_bases_fly_widely(ryanair_case):
    case, days, flights = ryanair_case
    assert len({flight["to"] for flight in flights}) >= case.at_least

def test_ryanair_fares_match_days(ryanair_case):
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
