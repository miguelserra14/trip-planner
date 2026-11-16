"""The rules of the arrival leg on small made-up inputs: no service, no checkpoint."""
import asyncio
import math
import sys
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

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

def test_city_beats_namesake_region():
    province = {"addresstype": "county", "importance": 0.72, "lat": "45.04", "lon": "9.14"}
    city = {"addresstype": "city", "importance": 0.66, "lat": "45.19", "lon": "9.16"}
    assert settlement([province, city]) is city

def test_minor_namesake_ignored():
    country = {"addresstype": "country", "importance": 0.9}
    village = {"addresstype": "village", "importance": 0.3}
    assert settlement([country, village]) is country

def test_town_beats_its_municipality():
    municipality = {"addresstype": "municipality", "importance": 0.53, "lat": "46.356", "lon": "13.614"}
    town = {"addresstype": "town", "importance": 0.50, "lat": "46.338", "lon": "13.552"}
    assert settlement([municipality, town]) is town

def test_distant_town_keeps_municipality():
    municipality = {"addresstype": "municipality", "importance": 0.53, "lat": "46.356", "lon": "13.614"}
    namesake = {"addresstype": "town", "importance": 0.50, "lat": "48.0", "lon": "16.0"}
    assert settlement([municipality, namesake]) is municipality

def test_town_names_its_municipality():
    municipality = {"addresstype": "municipality", "name": "Voss", "importance": 0.5, "lat": "60.70", "lon": "6.42"}
    town = {"addresstype": "town", "name": "Vossavangen", "importance": 0.3, "lat": "60.63", "lon": "6.42"}
    assert settlement([municipality, town]) is town

def test_province_town_found():
    province = {"addresstype": "county", "name": "Oristano", "importance": 0.6, "lat": "40.0", "lon": "8.75"}
    town = {"addresstype": "town", "name": "Oristano", "importance": 0.3, "lat": "39.9", "lon": "8.59"}
    assert settlement([province, town]) is town

def test_unaccented_name_matches_town():
    other = {"addresstype": "town", "name": "Bor", "importance": 0.45, "lat": "44.07", "lon": "22.1"}
    meant = {"addresstype": "city", "name": "Borås", "importance": 0.5, "lat": "57.72", "lon": "12.94"}
    assert settlement([other, meant], "Boras") is meant
    assert settlement([other, meant], "Bor") is other

def test_foreign_name_keeps_first():
    city = {"addresstype": "city", "name": "Antwerpen", "namedetails": {"name": "Antwerpen", "name:en": "Antwerp"}, "importance": 0.7}
    village = {"addresstype": "village", "name": "Antuérpia", "importance": 0.65}
    assert settlement([city, village], "Antwerp") is city

def test_first_city_kept():
    city = {"addresstype": "city", "importance": 0.6}
    assert settlement([city, {"addresstype": "town", "importance": 0.9}]) is city

def test_road_hours_read():
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

def test_airport_across_sea_excluded(monkeypatch):
    trip = bari_trip(monkeypatch, 18.3)
    assert [airport["code"] for airport in trip.arrival] == ["BRI", "BDS", "QSR"], "Salerno, by land, takes the place of Dubrovnik"
    assert trip.too_far == {"DBV": 18.3}

def test_added_airport_always_used(monkeypatch):
    trip = bari_trip(monkeypatch, 18.3, include_airports=["DBV"])
    assert "DBV" in [airport["code"] for airport in trip.arrival] and not trip.too_far

def test_straight_line_fallback(monkeypatch):
    trip = bari_trip(monkeypatch, None)
    assert "DBV" in [airport["code"] for airport in trip.arrival] and not trip.too_far

def test_trains_before_buses():
    stops = [stop("Bus", ["BUS"], 0.1), stop("Station", ["REGIONAL_RAIL"], 0.3), stop("Coach", ["COACH"], 0.05)]
    assert [identity for identity, walk in choose_airport_stops(stops, AIRPORT, TERMINAL)] == ["Station", "Bus"]

def test_far_stops_excluded():
    stops = [stop("Village", ["BUS"], 1.5), stop("Next town station", ["REGIONAL_RAIL"], 3.0)]
    assert choose_airport_stops(stops, AIRPORT, TERMINAL) == []

def test_plane_only_stop_ignored():
    assert choose_airport_stops([stop("Aeroporto (XXX)", ["AIRPLANE"], 0.0)], AIRPORT, TERMINAL) == []

def test_named_stops_without_terminal():
    stops = [stop("Wahnheide", ["BUS"], 0.2), stop("Flughafen Bf", ["SUBURBAN"], 2.0), stop("Airport Terminal 1", ["BUS"], 2.1)]
    chosen = choose_airport_stops(stops, AIRPORT, None)
    assert [identity for identity, walk in chosen] == ["Flughafen Bf", "Airport Terminal 1"]
    assert all(walk == timetables.AIRFIELD_WALK_MINUTES for identity, walk in chosen)

def test_walk_grows_with_distance():
    near, far = choose_airport_stops([stop("Station", ["REGIONAL_RAIL"], 0.05), stop("Bus", ["BUS"], 0.65)], AIRPORT, TERMINAL)
    assert near[1] == timetables.MIN_STOP_WALK_MINUTES and far[1] > near[1]

def result(addresstype: str, importance: float, lat: float, lon: float, display_name: str) -> dict:
    return {"addresstype": addresstype, "importance": importance, "lat": str(lat), "lon": str(lon), "category": "place",
            "name": display_name.split(",")[0], "display_name": display_name, "address": {"country": display_name.split(", ")[-1]}}

NAZARE = [result("municipality", 0.522, -13.03, -39.01, "Nazaré, Bahia, Northeast Region, 44400-000, Brazil"),
          result("town", 0.484, 39.60, -9.07, "Nazaré, Leiria, Portugal"),
          result("village", 0.449, -6.37, -47.66, "Nazaré, Tocantins, North Region, 77895-000, Brazil"),
          result("village", 0.147, 2.65, 11.0, "Nazare, Mvangan, Mvila, South, Cameroon")]

def test_similar_namesakes_all_offered(monkeypatch):
    monkeypatch.setitem(places.SEARCHES, "nazare", NAZARE)
    labels = [label for label, place in places.meanings("Nazaré")]
    assert labels == ["Nazaré, Bahia, Brazil", "Nazaré, Leiria, Portugal", "Nazaré, Tocantins, Brazil"], "not Cameroon: far less important"

def test_chosen_place_remembered(monkeypatch):
    monkeypatch.setitem(places.SEARCHES, "nazare", NAZARE)
    monkeypatch.setattr(places, "PLACES", {})
    label, leiria = places.meanings("Nazaré")[1]
    places.choose_place(label, leiria)
    assert places.geocode(label).country == "Portugal" and len(places.meanings(label)) == 1

def test_dominant_place_not_asked(monkeypatch):
    paris = [result("city", 0.88, 48.85, 2.35, "Paris, Ile-de-France, France"), result("city", 0.55, 33.66, -95.55, "Paris, Lamar County, Texas, United States")]
    monkeypatch.setitem(places.SEARCHES, "paris", paris)
    assert len(places.meanings("Paris")) == 1

def test_town_in_municipality_same(monkeypatch):
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

def test_refused_stop_keeps_point():
    transit = refusing_the_stop(bus=True)

    async def work() -> bool:
        try:
            return await transit.covers((0.0, 0.0), (0.1, 0.1), [("stop", 5)], DAY, ("UTC", "UTC"), True, True)
        finally:
            await transit.close()
    assert asyncio.run(work())

def test_refused_stop_not_covered():
    transit = refusing_the_stop(bus=False)
    flight = {"id": "F", "date": DAY, "departure_airport": "XXX", "departure_time": "15:00"}

    async def work() -> dict:
        try:
            return await transit.to_airport((0.0, 0.0), (0.1, 0.1), flight, ("UTC", "UTC"), True, [("stop", 5)])
        finally:
            await transit.close()
    assert asyncio.run(work()) == timetables.NOT_COVERED

def test_cache_written_atomically(tmp_path, monkeypatch):
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

def test_cache_merges_other_writes(tmp_path):
    from trip_planner import utils
    cache = tmp_path / "timezones.json"
    utils.write_whole(cache, '{"40.21,-8.43": "Europe/Lisbon"}')
    search = utils.read_cache(cache)
    utils.save_cache(cache, utils.read_cache(cache) | {"51.22,4.40": "Europe/Brussels"})   # a map running at the same time
    search["40.21,-8.43"], search["48.86,2.35"] = "Europe/Madrid", "Europe/Paris"
    utils.save_cache(cache, search)
    assert utils.read_cache(cache) == {"40.21,-8.43": "Europe/Madrid", "48.86,2.35": "Europe/Paris", "51.22,4.40": "Europe/Brussels"}
    utils.save_cache(cache, {"FAO": [37.01, -7.97]}, one_per_line=True)
    assert cache.read_text(encoding="utf-8").splitlines()[:3] == ["{", '"40.21,-8.43": "Europe/Madrid",', '"48.86,2.35": "Europe/Paris",']

def test_damaged_cache_read_empty(tmp_path):
    from trip_planner import utils
    cache = tmp_path / "airport_stops.json"
    cache.write_text('{"OPO": {"read": "2026-', encoding="utf-8")   # cut short
    assert utils.read_cache(cache) == {} and utils.read_cache(tmp_path / "missing.json") == {}
    cache.write_text("[1, 2]", encoding="utf-8")   # not entries by key
    assert utils.read_cache(cache) == {}
    utils.save_cache(cache, {"OPO": {"stops": []}})
    assert utils.read_cache(cache) == {"OPO": {"stops": []}}

# ---------------------------------------------------------------- how often the timetables are asked
def test_concurrent_flights_share_stations(monkeypatch):
    from trip_planner.sources.timetables import client
    monkeypatch.setattr(client, "by_road", lambda point, hubs: hubs)

    async def asked(at_once: int) -> int:
        transit, count = timetables.Timetables(), []

        async def get(path, params):
            count.append(path)
            await asyncio.sleep(0)
            return []   # no station near the place: every radius is asked
        transit.get = get
        await asyncio.gather(*(transit.hubs((40.0, -8.0)) for _ in range(at_once)))
        await transit.close()
        return len(count)
    assert asyncio.run(asked(3)) == asyncio.run(asked(1)) > 0

def test_failed_question_asked_again():
    asked = []

    async def ask():
        asked.append(1)
        if len(asked) == 1:
            raise httpx.ConnectError("no answer")
        return ["found"]

    async def work():
        transit, answers = timetables.Timetables(), {}
        with pytest.raises(httpx.ConnectError):
            await transit.shared(answers, ("route",), ask)
        found = await transit.shared(answers, ("route",), ask)
        again = await transit.shared(answers, ("route",), ask)
        await transit.close()
        return found, again
    assert asyncio.run(work()) == (["found"], ["found"]) and len(asked) == 2

def test_flights_go_first():
    async def work():
        transit, order = timetables.Timetables(), []
        for _ in range(timetables.client.TIMETABLE_CONCURRENCY):
            await transit.turn(False)   # every turn taken

        async def take(name: str, later: bool):
            await transit.turn(later)
            order.append(name)
        waiting = [asyncio.ensure_future(take("without a plane", True))]
        await asyncio.sleep(0)   # it is waiting for its turn when the flight's request comes
        waiting.append(asyncio.ensure_future(take("flight", False)))
        await asyncio.sleep(0)
        transit.semaphore.release()
        await asyncio.sleep(0.01)
        first = list(order)
        transit.semaphore.release()
        await asyncio.gather(*waiting)
        await transit.close()
        return first, order
    assert asyncio.run(work()) == (["flight"], ["flight", "without a plane"])

VEHICLE = {"mode": "BUS", "legGeometry": {"points": "a long shape"}, "steps": [{"walk": "directions"}]}

def answering(transit: timetables.Timetables, asked: list) -> None:
    """The timetables, answering every request with one connection of one vehicle and counting them."""
    def answer(request):
        asked.append(str(request.url))
        return httpx.Response(200, json={"itineraries": [{"legs": [VEHICLE]}]})
    transit.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))

def plan_at(hours_ahead: float) -> dict:
    moment = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return {"fromPlace": "40.2,-8.4", "toPlace": "51.2,4.4", "time": moment.strftime("%Y-%m-%dT%H:00:00Z"), "arriveBy": "false",
            "transitModes": timetables.GROUND_MODES}

@pytest.fixture
def kept_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(timetables.client, "KEPT_DIR", tmp_path / "timetables")
    return tmp_path / "timetables"

def test_answer_kept_between_searches(kept_dir):
    async def search(keep: bool) -> tuple:
        transit, asked = timetables.Timetables(keep=keep), []
        answering(transit, asked)
        found = await transit.get("plan", plan_at(30))
        await transit.close()
        return found, len(asked), transit.requests
    asked = {"itineraries": [{"legs": [VEHICLE]}]}
    assert asyncio.run(search(True)) == (asked, 1, 1)
    # The next search asks nothing, and gets the vehicle without what nothing reads
    assert asyncio.run(search(True)) == ({"itineraries": [{"legs": [{"mode": "BUS"}]}]}, 0, 0)
    assert asyncio.run(search(False)) == (asked, 1, 1)  # a client that does not keep always asks

def test_imminent_and_old_unkept(kept_dir, monkeypatch):
    async def asked_for(params: dict) -> int:
        transit, asked = timetables.Timetables(keep=True), []
        answering(transit, asked)
        await transit.get("plan", params)
        await transit.close()
        return len(asked)
    soon = plan_at(timetables.client.KEPT_FROM_HOURS - 1)
    assert asyncio.run(asked_for(soon)) == 1 and asyncio.run(asked_for(soon)) == 1
    asyncio.run(asked_for(plan_at(30)))
    later = timetables.client.time.time() + (timetables.client.KEPT_HOURS + 1) * 3600
    monkeypatch.setattr(timetables.client.time, "time", lambda: later)
    assert asyncio.run(asked_for(plan_at(30))) == 1

def test_coach_station_found_once(tmp_path, monkeypatch):
    monkeypatch.setattr(timetables.client, "STATIONS_FILE", tmp_path / "stations.json")
    monkeypatch.setattr(timetables.client, "STATIONS", {})
    asked = []

    async def look_up(at_once: int, near: tuple) -> list:
        transit = timetables.Timetables()

        async def get(path, params):
            asked.append(path)
            await asyncio.sleep(0)
            return [{"lat": 1.0, "lon": 1.0}, {"lat": 42.43, "lon": 19.26}]   # a namesake far away, then the station
        transit.get = get
        found = await asyncio.gather(*(transit.station_point("Podgorica Bus Station", near) for _ in range(at_once)))
        await transit.close()
        return found
    assert asyncio.run(look_up(3, (42.36, 19.25))) == [(42.43, 19.26)] * 3 and asked == ["geocode"]
    assert asyncio.run(look_up(1, (42.36, 19.25))) == [(42.43, 19.26)] and asked == ["geocode"]   # the next search
    assert '"Podgorica Bus Station|42.3600,19.2500"' in (tmp_path / "stations.json").read_text(encoding="utf-8")

def test_shared_ticket_asked_once(monkeypatch):
    import threading
    import time
    from trip_planner.sources import fares
    asked = []

    def slow_fare(trip):
        asked.append(trip)
        time.sleep(0.2)
        return 12.5
    source = fares.FareSource("Made up", "EU", lambda step: True, slow_fare)
    trip = fares.Trip("A", "B", "a", "b", (), (), "2026-12-17", "10:00", "11:00")
    monkeypatch.setattr(fares, "CACHE", {})
    found = []
    threads = [threading.Thread(target=lambda: found.append(fares.fare(source, trip))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found == [12.5] * 4 and len(asked) == 1 and not fares.ASKING

# ---------------------------------------------------------------- the flight search split when Kiwi's answer is full
def test_full_answer_split(monkeypatch):
    from trip_planner.sources import flight_search, kiwi
    asked = []

    async def search(fly_from, fly_to, first_day, last_day, stopovers=None):
        asked.append((fly_to, first_day, last_day))
        full = first_day != last_day or (first_day == "2026-12-17" and "," in fly_to)
        return [{"flight": len(asked)}] * (kiwi.RESULT_CAP if full else 3)
    monkeypatch.setattr(flight_search, "search", search)
    monkeypatch.setattr(flight_search, "register_flight", lambda itinerary: None)
    monkeypatch.setattr(flight_search, "SEARCHED", set())
    monkeypatch.setattr(flight_search, "FLIGHT_GAPS", [])
    asyncio.run(flight_search.search_airport_flights("BRU", ["OPO", "LIS", "FAO"], ["2026-12-17", "2026-12-18"]))
    assert sorted(asked) == sorted([("OPO,LIS,FAO", "2026-12-17", "2026-12-18"), ("OPO,LIS,FAO", "2026-12-17", "2026-12-17"),
                                    ("OPO,LIS,FAO", "2026-12-18", "2026-12-18"),
                                    *((code, "2026-12-17", "2026-12-17") for code in ("OPO", "LIS", "FAO"))])
    assert len(flight_search.SEARCHED) == 6 and not flight_search.FLIGHT_GAPS

# ---------------------------------------------------------------- settings.py once its dates have passed
def today_is(monkeypatch, day: date) -> None:
    from trip_planner.trip import trip_settings
    monkeypatch.setattr(trip_settings, "date", type("Later", (date,), {"today": classmethod(lambda cls: day)}))

def test_past_dates_moved_weekly(monkeypatch):
    from trip_planner.trip.trip_settings import past_dates_moved, SettingError
    today_is(monkeypatch, date(2027, 1, 5))
    trip = dict(origin="Coimbra", destination="Antwerp", dates=["17/12/2026", "18/12/2026"], return_dates=["22/12/2026"])
    with pytest.raises(SettingError):
        TripSettings(**trip)
    with past_dates_moved():
        moved = TripSettings(**trip)
    # Three weeks: the first day is the first Thursday from today on, and the five days to come back are kept
    assert moved.moved_weeks == 3 and moved.dates == ["07/01/2027", "08/01/2027"] and moved.return_dates == ["12/01/2027"]
    assert TripSettings(**{**trip, "dates": moved.dates, "return_dates": moved.return_dates}).moved_weeks == 0

def test_opens_after_dates_pass(monkeypatch):
    from trip_planner.trip.trip_settings import settings_file, SettingError
    first = datetime.strptime(settings_file().SETTINGS.dates[0], "%d/%m/%Y").date()
    later = first + timedelta(days=30)
    today_is(monkeypatch, later)
    monkeypatch.delitem(sys.modules, "settings")
    opened = settings_file().SETTINGS
    assert all(datetime.strptime(day, "%d/%m/%Y").date() >= later for day in opened.dates)
    monkeypatch.delitem(sys.modules, "settings")
    try:
        as_written = settings_file(move_past_dates=False).SETTINGS   # main.py --terminal: that very trip
    except SettingError:
        assert opened.moved_weeks > 0
    else:
        assert opened.moved_weeks == 0 and as_written.dates == opened.dates   # its dates are relative ("sexta"): nothing passed

# ---------------------------------------------------------------- the ground lookup of a search made in rounds
def made_up_trip(monkeypatch) -> None:
    """A trip between two made-up places, with one departure and one arrival airport 100 km from each."""
    from types import SimpleNamespace
    from trip_planner import current
    airports = {"AAA": {"code": "AAA", "latitude": 40.1, "longitude": -8.1, "country": "PT"},
                "BBB": {"code": "BBB", "latitude": 51.1, "longitude": 4.1, "country": "BE"}}
    monkeypatch.setattr(current, "TRIP", SimpleNamespace(origin=SimpleNamespace(latitude=40.0, longitude=-8.0),
                                                         destination=SimpleNamespace(latitude=51.0, longitude=4.0),
                                                         airport=airports.get, km=lambda code: 100))

def made_up_flight(departure: str, day: str) -> dict:
    return {"id": f"{departure}-BBB-{day}", "departure_airport": departure, "arrival_airport": "BBB", "date": day,
            "departure_time": "12:00", "arrival_time": "15:00", "arrival_day_offset": 0}

def test_earlier_rides_reused(monkeypatch):
    from trip_planner.sources import fares
    from trip_planner.sources.timetables import lookup
    made_up_trip(monkeypatch)
    asked = []

    def rides(city, airport, day, to_airport):
        asked.append((airport["code"], day, to_airport))
        return [{"source": "Made up", "leave": 480, "arrive": 540}]
    monkeypatch.setattr(fares, "RIDE_SOURCES", {"Made up": rides})
    monkeypatch.setattr(lookup, "find_terminals", lambda airports: {})

    async def nothing(*arguments, **options):
        return None
    monkeypatch.setattr(lookup, "join_published_ends", nothing)

    class Timetables:
        requests = 0

        async def zone(self, *arguments):
            return "Europe/Lisbon"

        async def airport_stops(self, *arguments):
            return []

        async def to_airport(self, *arguments):
            return dict(timetables.NOT_COVERED)
        from_airport = to_airport
    earlier = {"operators": {"AAA": {"2026-12-17": [{"source": "Made up", "leave": 420, "arrive": 470}]}}, "operators_from": {}}
    found = asyncio.run(lookup.ground_transport_for([made_up_flight("AAA", "2026-12-17")], Timetables(), earlier))
    # The way to AAA that day was found by an earlier round: asked again only from BBB, and still there
    assert asked == [("BBB", "2026-12-17", False)]
    assert found["operators"]["AAA"]["2026-12-17"] == [{"source": "Made up", "leave": 420, "arrive": 470}]
    assert found["operators_from"]["BBB"]["2026-12-17"] == [{"source": "Made up", "leave": 480, "arrive": 540}]

# ---------------------------------------------------------------- estimated prices the traveller does not accept
COACH = {"line": "Coach 4", "agency": "Made up", "time": "08:00", "arrive": "09:00", "from": "Origin", "to": "AAA", "rail": False,
         "coach": True, "mode": "COACH", "km": 100}

def leg_to_aaa(monkeypatch, sold: bool) -> dict:
    """The way to AAA with estimated prices refused: one coach in the timetables, sold by its operator or not."""
    import dataclasses
    from trip_planner import current
    from trip_planner.itineraries import legs
    from trip_planner.sources import fares
    made_up_trip(monkeypatch)
    monkeypatch.setattr(current, "SETTINGS", dataclasses.replace(current.SETTINGS, allow_estimated_prices=False))
    ticket = {"positions": [0], "operator": "Made up", "price_eur": 9.5, "travel_day": True, "tariff": False, "confirms": True}
    monkeypatch.setattr(fares, "tickets", lambda steps, day, service_day, skip=None: [ticket] if sold else [])
    monkeypatch.setattr(fares, "not_running", lambda steps, day: [])
    option = {"leave": 480, "arrive": 540, "grade": "B", "service_day": "2026-12-17", "urls": [], "steps": [dict(COACH)]}
    ground = {"AAA-BBB-2026-12-17": {"to_airport": {"covered": True, "service_day": "2026-12-17", "grade": "B", "options": [option]}}}
    return legs.to_airport_leg(made_up_flight("AAA", "2026-12-17"), ground, None, priced=True)

def test_operator_price_replaces_estimate(monkeypatch):
    leg = leg_to_aaa(monkeypatch, sold=True)
    assert (leg["price_eur"], leg["price_kind"], leg["grade"]) == (9.5, "operator", "A")

def test_unpriced_leg_rejected(monkeypatch):
    from trip_planner.itineraries.legs import Rejected
    with pytest.raises(Rejected) as refused:
        leg_to_aaa(monkeypatch, sold=False)
    assert refused.value.kind == "price_unknown"

def test_unfitting_rides_prove_nothing(monkeypatch):
    """Only an operator's rides, none in time for the flight, and no timetable covers the airport: they say nothing
    of the trains or other coaches there, so the flight is not proven impossible."""
    from trip_planner.itineraries import legs
    made_up_trip(monkeypatch)
    flight = made_up_flight("AAA", "2026-12-17")
    late = {"leave": 11 * 60, "arrive": 11 * 60 + 50, "grade": "A", "source": "Made up", "price_eur": 5.0, "steps": [dict(COACH)], "urls": []}
    ground = {"operators": {"AAA": {"2026-12-17": [late]}}, flight["id"]: {"to_airport": dict(timetables.NOT_COVERED)}}
    with pytest.raises(legs.Rejected) as refused:
        legs.to_airport_leg(flight, ground, None)
    assert refused.value.kind not in legs.PROVEN

def test_estimate_names_unfitting_rides(monkeypatch):
    """Near enough for a model's estimate: it is used, says why the operator's rides were not, and is counted once."""
    from collections import Counter
    from trip_planner import current, utils
    from trip_planner.agents import steps
    from trip_planner.itineraries import legs
    made_up_trip(monkeypatch)
    monkeypatch.setattr(current.TRIP, "km", lambda code: 30)
    monkeypatch.setattr(utils, "COUNTS", Counter())
    monkeypatch.setattr(steps, "REQUESTS", [])
    flight = made_up_flight("AAA", "2026-12-17")
    late = {"leave": 11 * 60, "arrive": 11 * 60 + 50, "grade": "A", "source": "Made up", "price_eur": 5.0, "steps": [dict(COACH)], "urls": []}
    ground = {"operators": {"AAA": {"2026-12-17": [late]}}, flight["id"]: {"to_airport": dict(timetables.NOT_COVERED)}}
    transfer = {"reachable": True, "duration_minutes": 40, "price_eur": 4.0, "price_known": True, "description": "Bus 5",
                "operator_url": "", "notes": ""}
    leg = legs.to_airport_leg(flight, ground, transfer)
    assert leg["kind"] == "model" and leg["grade"] == "C" and leg["rides_missed"] == ["Made up"]
    assert any("Made up" in note and "AAA" in note for note in leg["notes"])
    steps.note_rides_missed([{"flight": flight, "to": leg, "from": {}}])
    assert utils.COUNTS["rides_missed"] == 1 and "no ride of Made up fits" in steps.REQUESTS[0]["what"]
