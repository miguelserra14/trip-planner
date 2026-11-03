"""The rules of the arrival leg on small made-up inputs: no service, no checkpoint."""
import asyncio
import math
from datetime import date, timedelta

import httpx

from trip_planner.trip import places
from trip_planner.trip.trip_settings import TripSettings
from trip_planner.trip.places import choose_airports, Place, read_road_hours, settlement
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

def test_a_town_gives_its_name_to_the_municipality_around_it():
    municipality = {"addresstype": "municipality", "name": "Voss", "importance": 0.5, "lat": "60.70", "lon": "6.42"}
    town = {"addresstype": "town", "name": "Vossavangen", "importance": 0.3, "lat": "60.63", "lon": "6.42"}
    assert settlement([municipality, town]) is town

def test_the_town_of_a_province_is_found_however_unimportant():
    province = {"addresstype": "county", "name": "Oristano", "importance": 0.6, "lat": "40.0", "lon": "8.75"}
    town = {"addresstype": "town", "name": "Oristano", "importance": 0.3, "lat": "39.9", "lon": "8.59"}
    assert settlement([province, town]) is town

def test_a_name_written_without_accents_is_the_town_of_that_name():
    other = {"addresstype": "town", "name": "Bor", "importance": 0.45, "lat": "44.07", "lon": "22.1"}
    meant = {"addresstype": "city", "name": "Borås", "importance": 0.5, "lat": "57.72", "lon": "12.94"}
    assert settlement([other, meant], "Boras") is meant
    assert settlement([other, meant], "Bor") is other

def test_a_name_in_another_language_keeps_the_first_result():
    city = {"addresstype": "city", "name": "Antwerpen", "namedetails": {"name": "Antwerpen", "name:en": "Antwerp"}, "importance": 0.7}
    village = {"addresstype": "village", "name": "Antuérpia", "importance": 0.65}
    assert settlement([city, village], "Antwerp") is city

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
    """The airports of a trip from Antwerp to Bari, with made-up hours by road: Dubrovnik is 198 km from Bari.
    The near scale at the destination: its three airports are the ones the tests expect."""
    settings.setdefault("arrival_scale", "near")
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

def result(addresstype: str, importance: float, lat: float, lon: float, display_name: str) -> dict:
    return {"addresstype": addresstype, "importance": importance, "lat": str(lat), "lon": str(lon), "category": "place",
            "name": display_name.split(",")[0], "display_name": display_name, "address": {"country": display_name.split(", ")[-1]}}

NAZARE = [result("municipality", 0.522, -13.03, -39.01, "Nazaré, Bahia, Northeast Region, 44400-000, Brazil"),
          result("town", 0.484, 39.60, -9.07, "Nazaré, Leiria, Portugal"),
          result("village", 0.449, -6.37, -47.66, "Nazaré, Tocantins, North Region, 77895-000, Brazil"),
          result("village", 0.147, 2.65, 11.0, "Nazare, Mvangan, Mvila, South, Cameroon")]

def test_places_of_the_same_name_and_about_as_important_are_all_offered(monkeypatch):
    monkeypatch.setitem(places.SEARCHES, "nazare", NAZARE)
    labels = [label for label, place in places.meanings("Nazaré")]
    assert labels == ["Nazaré, Bahia, Brazil", "Nazaré, Leiria, Portugal", "Nazaré, Tocantins, Brazil"], "not Cameroon: far less important"

def test_the_place_chosen_is_the_one_the_name_means_from_then_on(monkeypatch):
    monkeypatch.setitem(places.SEARCHES, "nazare", NAZARE)
    monkeypatch.setattr(places, "PLACES", {})
    label, leiria = places.meanings("Nazaré")[1]
    places.choose_place(label, leiria)
    assert places.geocode(label).country == "Portugal" and len(places.meanings(label)) == 1

def test_a_name_far_more_important_in_one_place_is_not_asked_about(monkeypatch):
    paris = [result("city", 0.88, 48.85, 2.35, "Paris, Ile-de-France, France"), result("city", 0.55, 33.66, -95.55, "Paris, Lamar County, Texas, United States")]
    monkeypatch.setitem(places.SEARCHES, "paris", paris)
    assert len(places.meanings("Paris")) == 1

def test_the_town_inside_a_municipality_of_its_name_is_not_another_place(monkeypatch):
    bovec = [result("municipality", 0.53, 46.356, 13.614, "Bovec, Slovenia"), result("town", 0.50, 46.338, 13.552, "Bovec, 5230, Slovenia")]
    monkeypatch.setitem(places.SEARCHES, "bovec", bovec)
    assert [label for label, place in places.meanings("Bovec")] == ["Bovec, Slovenia"]

# ---------------------------------------------------------------- a stop the timetables no longer know
DAY = (date.today() + timedelta(days=3)).isoformat()

def refusing_the_stop(bus: bool) -> timetables.Timetables:
    """The timetables, refusing every request about the airport's stop as one they no longer know. Between points on
    the map they answer with one bus at midday when bus, with nothing otherwise."""
    transit = timetables.Timetables()

    async def get(path, params):
        if path != "plan":
            return []   # no station near either point: no mixed leg either
        if "," not in params["fromPlace"] or "," not in params["toPlace"]:
            raise httpx.HTTPStatusError("unknown stop", request=httpx.Request("GET", "https://api.transitous.org"), response=httpx.Response(400))
        leg = {"mode": "BUS", "startTime": f"{DAY}T11:00:00Z", "endTime": f"{DAY}T12:00:00Z",
               "from": {"name": "A", "lat": 0.0, "lon": 0.0}, "to": {"name": "B", "lat": 0.1, "lon": 0.1}}
        return {"itineraries": [{"startTime": leg["startTime"], "endTime": leg["endTime"], "legs": [leg]}] if bus else []}
    transit.get = get
    return transit

def test_a_stop_the_timetables_refuse_does_not_hide_the_point_on_the_map():
    transit = refusing_the_stop(bus=True)

    async def work() -> bool:
        try:
            return await transit.covers((0.0, 0.0), (0.1, 0.1), [("stop", 5)], DAY, ("UTC", "UTC"), True, True)
        finally:
            await transit.close()
    assert asyncio.run(work())

def test_a_refused_stop_with_nothing_at_the_point_is_a_way_no_timetable_covers():
    transit = refusing_the_stop(bus=False)
    flight = {"id": "F", "date": DAY, "departure_airport": "XXX", "departure_time": "15:00"}

    async def work() -> dict:
        try:
            return await transit.to_airport((0.0, 0.0), (0.1, 0.1), flight, ("UTC", "UTC"), True, [("stop", 5)])
        finally:
            await transit.close()
    assert asyncio.run(work()) == timetables.NOT_COVERED

def test_a_cache_is_written_whole_or_left_as_it_was(tmp_path, monkeypatch):
    from trip_planner import utils
    cache = tmp_path / "cache" / "kept.json"
    utils.write_whole(cache, '{"old": 1}')
    real_write = type(cache).write_text

    def cut_short(path, text, *arguments, **named):   # the process stopped halfway through writing
        real_write(path, text[:3], *arguments, **named)
        raise KeyboardInterrupt
    monkeypatch.setattr(type(cache), "write_text", cut_short)
    try:
        utils.write_whole(cache, '{"new": 2}')
    except KeyboardInterrupt:
        pass
    monkeypatch.undo()
    assert cache.read_text(encoding="utf-8") == '{"old": 1}'   # the half-written file is beside it, never in its place
    utils.write_whole(cache, '{"new": 2}')
    assert cache.read_text(encoding="utf-8") == '{"new": 2}' and [path.name for path in cache.parent.iterdir()] == ["kept.json"]
