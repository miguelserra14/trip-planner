"""The ways to and from the airports (access.py) and the map looked up in the real timetables (confirm.py). No network:
the timetables answer what each test says.

- A way not looked up is an estimate, grade C; one looked up is remembered for the next maps, B at best there.
- A city is located once, and a place of its name far from its airport is not taken for it.
- The dot of a city is its cheapest airport once the way into the city is counted.
- The cities are looked up until no destination left can change the cheapest ones.
- A way looked up replaces its estimate, an impossible one takes its flight off the map, one no timetable covers
  keeps its estimate."""
import asyncio
from datetime import date, timedelta

import httpx
import pytest

from discover import access, confirm, finder
from discover.finder import Found
from discover.sources import Days
from trip_planner import current
from trip_planner.trip.trip_settings import TripSettings
from trip_planner.itineraries import legs
from trip_planner.trip.places import load_airports, Place
from trip_planner.sources import timetables

TODAY = date.today()
DAY = (TODAY + timedelta(days=30)).isoformat()
HOME = Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43)
MADRID = Place("Madrid", "Madrid", "Madrid", "Spain", 40.4168, -3.7038)

@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    """The files of what is remembered, empty, and a trip whose traveller leaves at any time."""
    monkeypatch.setattr(access, "ACCESS", tmp_path / "airport_access.json")
    monkeypatch.setattr(access, "CENTRES", tmp_path / "city_centres.json")
    monkeypatch.setattr(access.transfers, "TRANSFERS", tmp_path / "airport_transfers.json")
    monkeypatch.setattr(access.transfers, "PAGES", tmp_path / "wikivoyage_by_plane.json")
    monkeypatch.setattr(access, "ask_wikidata", lambda codes: [])   # Wikidata knows nothing: Nominatim's turn
    settings = TripSettings(origin="Coimbra", destination="Madrid", dates=[f"{TODAY + timedelta(days=30):%d/%m/%Y}"],
                            earliest_leave="", latest_leave="")
    monkeypatch.setattr(current, "SETTINGS", settings)

def kiwi_leg(start: str, end: str, leaves: str, lands: str, minutes: int) -> dict:
    return {"from": start, "to": end, "date": DAY, "time": leaves, "arrival_date": DAY, "arrival_time": lands, "minutes": minutes,
            "airline": "easyJet", "stops": 0, "segments": []}

# ---------------------------------------------------------------- the ways to and from the airports
def test_a_way_not_looked_up_is_an_estimate_and_one_looked_up_is_remembered_for_the_next_maps():
    first = access.leg("OPO", True, HOME, TODAY)
    assert first["grade"] == first["price_grade"] == "C" and first["how"].startswith("estimated") and not first["looked_up"]
    access.remember("OPO", True, HOME, {"price_eur": 7.5, "minutes": 80, "grade": "A", "price_grade": "A", "for": "a flight leaving at 10:00"}, TODAY)
    again = access.leg("OPO", True, HOME, TODAY)
    assert (again["price_eur"], again["grade"], again["price_grade"]) == (7.5, "B", "B")   # read for another flight: typical at best
    assert again["how"].startswith("looked up on") and not again["looked_up"]
    assert access.leg("OPO", False, HOME, TODAY)["how"].startswith("estimated")   # the way back home is another way
    assert access.leg("OPO", True, HOME, TODAY + timedelta(days=access.ACCESS_DAYS))["how"].startswith("estimated")   # too old

def test_a_city_is_located_once_and_a_place_of_its_name_far_from_its_airport_is_not_taken(monkeypatch):
    asked = []

    def find(name: str, country: str) -> Place:
        asked.append(name)
        return MADRID if name == "Madrid" else Place("Paris", "Paris", "Paris", "United States", 33.66, -95.55)   # Paris, Texas
    monkeypatch.setattr(access, "find_centre", find)
    airports = load_airports()
    found = access.centres(["MAD", "ORY"], airports, TODAY, say=lambda text: None)
    assert found["MAD"].latitude == MADRID.latitude and "ORY" not in found and sorted(asked) == ["Madrid", "Paris"]
    access.centres(["MAD", "ORY"], airports, TODAY, say=lambda text: None)
    assert len(asked) == 2   # kept, the city not found too
    access.centres(["MAD", "ORY"], airports, TODAY + timedelta(days=access.MISSING_DAYS + 1), say=lambda text: None)
    assert asked[2:] == ["Paris"]   # the one not found is asked about again, a while later

def test_the_name_of_a_city_leaves_out_where_its_airport_is():
    assert access.city_name({"city": "Paris (Orly, Val-de-Marne)"}) == "Paris" and access.city_name({"city": "London, Essex"}) == "London"
    assert access.city_key({"country": "FR", "city": "Paris (Orly, Val-de-Marne)"}) == access.city_key({"country": "FR", "city": "Paris"})

def test_the_dot_of_a_city_is_its_cheapest_airport_once_the_way_into_the_city_is_counted(monkeypatch):
    into_paris = {"CDG": 15.0, "ORY": 2.0, "OPO": 10.0}
    monkeypatch.setattr(access, "leg", lambda code, to_airport, place, today, label="", ride=None: {
        "airport": code, "to_airport": to_airport, "place": label, "price_eur": into_paris[code], "minutes": 30, "grade": "C",
        "price_grade": "C", "how": "estimated", "looked_up": False})
    flights = [{**kiwi_leg("OPO", "CDG", "07:00", "10:00", 120), "price_eur": 20.0}, {**kiwi_leg("OPO", "ORY", "07:00", "10:00", 120), "price_eur": 30.0}]
    priced = [finder.with_access(flight, HOME, {"CDG": MADRID, "ORY": MADRID}, TODAY) for flight in flights]
    airports = {"CDG": {"country": "FR", "city": "Paris"}, "ORY": {"country": "FR", "city": "Paris (Orly, Val-de-Marne)"}}
    paris = finder.one_per_city(priced, airports)
    assert [(city["to"], city["total_eur"]) for city in paris] == [("ORY", 42.0)]   # the dearer flight, and the cheaper way in
    assert [(other["to"], other["total_eur"]) for other in paris[0]["also"]] == [("CDG", 45.0)]

# ---------------------------------------------------------------- the rounds
def way(code: str, to_airport: bool, price: float, looked_up: bool = False) -> dict:
    return {"airport": code, "to_airport": to_airport, "price_eur": price, "looked_up": looked_up}

def option(code: str, price: float, ground: list) -> dict:
    return {"to": code, "price_eur": price, "access": ground, "total_eur": price + sum(leg["price_eur"] for leg in ground)}

def test_the_cities_are_looked_up_until_no_destination_left_can_change_the_cheapest(monkeypatch):
    monkeypatch.setattr(confirm, "TOP", 1)
    airports = {code: {"code": code, "country": "XX", "city": city} for code, city in (("AAA", "A"), ("AA2", "A"), ("BBB", "B"), ("CCC", "C"))}
    a = option("AAA", 20, [way("OPO", True, 10), way("AAA", False, 10)])
    a2 = option("AA2", 31, [way("OPO", True, 10), way("AA2", False, 1)])
    b = option("BBB", 35, [way("OPO", True, 10), way("BBB", False, 5)])
    c = option("CCC", 25, [way("OPO", True, 10), way("CCC", False, 40)])
    options = [a, a2, b, c]
    assert confirm.next_city(options, airports) == [a, a2]   # the cheapest first, with the other airports of its city
    a.update(option("AAA", 20, [way("OPO", True, 12, True), way("AAA", False, 10, True)]), checked=True)   # 42 once looked up
    a2.update(checked=True)
    # Leaving from OPO costs at least 12 now: BBB costs at least 35 + 12, more than AAA. CCC, 25 + 12, could still beat it.
    assert confirm.next_city(options, airports) == [c]
    c.update(option("CCC", 25, [way("OPO", True, 12, True), way("CCC", False, 40, True)]), checked=True)
    assert confirm.next_city(options, airports) == []

def test_a_way_looked_up_replaces_its_estimate_and_an_impossible_one_takes_its_flight_off_the_map(monkeypatch):
    monkeypatch.setattr(legs, "price_with_tickets", lambda *arguments: None)   # no operator is asked for its tickets
    bus = {"leave": 6 * 60, "arrive": 7 * 60 + 30, "grade": "B", "service_day": DAY, "urls": [],
           "steps": [{"line": "Coach 4", "time": "06:00", "arrive": "07:30", "rail": False, "coach": True, "mode": "COACH", "km": 100,
                      "from": "Coimbra", "to": "the airport"}]}

    async def ground_transport_for(flights: list) -> dict:
        found = {flight["id"]: {"to_airport": {"covered": True, "service_day": DAY, "grade": "B", "options": [dict(bus)]},
                                "from_airport": dict(timetables.NOT_COVERED)} for flight in flights}
        return {**found, "operators": {}, "operators_from": {}, "requests": 3}
    monkeypatch.setattr(timetables, "ground_transport_for", ground_transport_for)
    fits = {**kiwi_leg("OPO", "MAD", "10:00", "12:15", 75), "price_eur": 20.0, "back": None}        # at the airport by 07:30: in time
    too_early = {**kiwi_leg("LIS", "MAD", "07:00", "09:10", 70), "price_eur": 15.0, "back": None}   # the coach arrives too late for it
    centres = {"MAD": MADRID}
    madrid = finder.one_per_city([finder.with_access(flight, HOME, centres, TODAY) for flight in (fits, too_early)], load_airports())
    found = Found(HOME, Days(DAY, DAY), [], madrid, [], [], centres=centres)
    asyncio.run(confirm.confirm(found))
    assert [(gone["to"], gone["from"]) for gone in found.discarded] == [("MAD", "LIS")]
    [city] = found.destinations
    to_airport, into_madrid = city["access"]
    assert city["from"] == "OPO" and to_airport["looked_up"] and "Coach 4 06:00-07:30" in to_airport["how"]
    assert (to_airport["grade"], to_airport["price_grade"], to_airport["leave"]) == ("B", "C", "06:00")   # a timetable with no price
    assert into_madrid["checked"] and not into_madrid["looked_up"] and "no timetable covers it" in into_madrid["how"]
    assert (found.lookups[0]["found"], found.lookups[0]["impossible"], found.lookups[0]["not covered"]) == (1, 1, 2)
    assert access.leg("OPO", True, HOME, TODAY)["how"].startswith("looked up on")   # remembered for the next maps
    assert "no destination left" in found.confirm_stop

def test_a_lookup_is_the_planners_trip_only_while_it_runs_even_when_it_fails(monkeypatch):
    seen = []

    async def failing(flights: list) -> dict:
        seen.append((current.SETTINGS.destination, [airport["code"] for airport in current.TRIP.arrival]))
        raise RuntimeError("the timetables are down")
    monkeypatch.setattr(timetables, "ground_transport_for", failing)
    the_map, its_places = current.SETTINGS, current.TRIP
    flight = {**kiwi_leg("OPO", "MAD", "10:00", "12:15", 75), "price_eur": 20.0, "back": None}
    centres = {"MAD": MADRID}
    found = Found(HOME, Days(DAY, DAY), [], finder.one_per_city([finder.with_access(flight, HOME, centres, TODAY)], load_airports()), [], [],
                  centres=centres)
    asyncio.run(confirm.confirm(found))
    assert seen == [("Madrid", ["MAD"])] and current.SETTINGS is the_map and current.TRIP is its_places

def test_the_duration_of_a_ryanair_flight_comes_from_the_time_zones_of_its_airports():
    flight = confirm.planner_flight(kiwi_leg("OPO", "MAD", "07:00", "09:15", 0), 20.0, load_airports())
    assert flight["duration_minutes"] == 75 and flight["timezone_shift_minutes"] == -60   # Madrid is an hour ahead of Porto
    assert confirm.planner_flight({**kiwi_leg("OPO", "MAD", "07:00", "", 0), "arrival_time": ""}, 20.0, load_airports()) is None

def test_a_city_not_located_yet_is_not_counted_free():
    unknown = access.leg("BCN", False, None, TODAY)
    assert unknown["price_eur"] >= 2 and unknown["grade"] == "C" and "not located yet" in unknown["how"]

def test_when_openstreetmap_asks_to_wait_the_other_cities_wait_for_the_next_map(monkeypatch):
    asked = []

    def busy(name: str, country: str) -> Place:
        asked.append(name)
        request = httpx.Request("GET", "https://nominatim.openstreetmap.org/search")
        raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))
    monkeypatch.setattr(access, "find_centre", busy)
    assert access.centres(["MAD", "BCN", "AGP"], load_airports(), TODAY, say=lambda text: None) == {}
    assert len(asked) == 1   # not asked again for the two others

def test_a_city_that_cannot_be_looked_up_does_not_use_up_a_lookup(monkeypatch):
    async def never(flights: list) -> dict:
        raise AssertionError("the timetables were asked")
    monkeypatch.setattr(timetables, "ground_transport_for", never)
    lost = {**kiwi_leg("OPO", "BCN", "10:00", "12:00", 60), "price_eur": 20.0, "back": None}
    found = Found(HOME, Days(DAY, DAY), [], [finder.with_access(lost, HOME, {}, TODAY)], [], [])
    asyncio.run(confirm.confirm(found))
    assert found.lookups == [] and found.destinations[0]["not_looked_up"] == "its city is not located yet"

def test_a_map_locates_only_a_few_new_cities_the_cheapest_first(monkeypatch):
    monkeypatch.setattr(access, "MAX_NEW_CITIES", 2)
    asked = []
    monkeypatch.setattr(access, "find_centre", lambda name, country: asked.append(name) or MADRID)
    access.centres(["MAD", "BCN", "AGP"], load_airports(), TODAY, say=lambda text: None)
    assert asked == ["Madrid", "Barcelona"]
