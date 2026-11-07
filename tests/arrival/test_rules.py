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

def test_a_cache_keeps_what_another_process_wrote_since_it_was_read(tmp_path):
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

def test_a_damaged_cache_is_read_as_empty_instead_of_stopping_the_program(tmp_path):
    from trip_planner import utils
    cache = tmp_path / "airport_stops.json"
    cache.write_text('{"OPO": {"read": "2026-', encoding="utf-8")   # cut short
    assert utils.read_cache(cache) == {} and utils.read_cache(tmp_path / "missing.json") == {}
    cache.write_text("[1, 2]", encoding="utf-8")   # not entries by key
    assert utils.read_cache(cache) == {}
    utils.save_cache(cache, {"OPO": {"stops": []}})
    assert utils.read_cache(cache) == {"OPO": {"stops": []}}

# ---------------------------------------------------------------- how often the timetables are asked
def test_flights_asking_about_the_same_place_at_once_share_its_stations(monkeypatch):
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

def test_a_question_that_failed_is_asked_again_by_the_next_flight():
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

def test_the_flights_ask_the_timetables_before_the_trip_without_a_plane():
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

def test_an_answer_is_kept_for_the_next_search_and_not_asked_again(kept_dir):
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

def test_connections_of_the_next_hours_and_old_answers_are_not_kept(kept_dir, monkeypatch):
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

def test_a_coach_station_is_found_once_for_every_search_and_flight(tmp_path, monkeypatch):
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

def test_a_ticket_several_itineraries_share_is_asked_for_once(monkeypatch):
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
def test_a_full_answer_is_split_by_date_then_by_airport_only_for_a_date_still_full(monkeypatch):
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

def test_dates_that_have_passed_are_moved_by_whole_weeks_only_while_settings_py_is_read(monkeypatch):
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

def test_the_program_still_opens_once_the_dates_of_settings_py_have_passed(monkeypatch):
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
