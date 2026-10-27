"""The discover feature, apart from the planner: pytest discover. No network.

- The answers of the two sources are read, one way and there and back, and an answer that is not one is told apart.
- The cheapest way to each destination counts the way to the airport, and back home from the one the return lands at.
- The breakdown counts every request, says which answers were full, and explains the known routes not found.
- The map page cannot be broken by a name, and the arguments are checked.
- Kiwi is asked politely: never again after a refusal, within a daily limit, without Ryanair only where Ryanair answered.
- A city is one dot, and a trip of the map opens the planner only when its own page asks.
- The planner does not depend on this folder."""
import asyncio
import json
import re
from datetime import date
from pathlib import Path

import pytest

from discover import __main__ as command, breakdown, finder, page, plan, serve, sources
from discover import routes as routes_module
from discover.sources import Days
from trip_planner.trip.places import Place

ROOT = Path(__file__).parents[2]
ONE_WAY = Days("2026-11-12", "2026-11-19")
RETURN = Days("2026-11-12", "2026-11-14", "2026-11-15", "2026-11-17")
PLACE = Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43)

def flight(start: str, end: str, price: float, airline: str = "Ryanair", back: dict | None = None, carrier: str = "FR") -> dict:
    return {"from": start, "to": end, "price_eur": price, "date": "2026-11-12", "time": "07:00", "airline": airline, "carrier": carrier,
            "stops": 0, "source": "Kiwi", "back": back}

def back_to(start: str, end: str) -> dict:
    return {"from": start, "to": end, "date": "2026-11-16", "time": "12:00", "airline": "Ryanair", "stops": 0}

# ---------------------------------------------------------------- the sources
def airport(code: str) -> dict:
    return {"iataCode": code}

def ryanair(start: str, end: str, leaves: str, lands: str, price: float, number: str = "FR8513") -> dict:
    return {"departureAirport": {"iataCode": start, "name": start}, "arrivalAirport": {"iataCode": end, "name": end}, "price": {"value": price},
            "departureDate": leaves, "arrivalDate": lands, "flightNumber": number}

def test_an_answer_of_ryanair_is_read_one_way_and_there_and_back():
    out = ryanair("OPO", "FAO", "2026-11-17T07:55:00", "2026-11-17T08:55:00", 14.99)
    found = sources.ryanair_flight({"outbound": out}, "OPO")
    assert (found["from"], found["to"], found["price_eur"], found["date"], found["time"], found["arrival_date"], found["arrival_time"]) == \
        ("OPO", "FAO", 14.99, "2026-11-17", "07:55", "2026-11-17", "08:55")
    assert found["segments"][0]["flight"] == "FR8513" and found["minutes"] == 0 and found["back"] is None   # two clocks: the duration is not known
    assert found["url"].startswith("https://www.ryanair.com/") and "isReturn=false" in found["url"]
    back = ryanair("FAO", "OPO", "2026-11-19T10:05:00", "2026-11-19T11:05:00", 20.0, "FR8514")
    found = sources.ryanair_flight({"outbound": out, "inbound": back}, "OPO")
    assert found["price_eur"] == 34.99 and (found["back"]["from"], found["back"]["date"], found["back"]["arrival_time"]) == ("FAO", "2026-11-19", "11:05")
    assert "dateIn=2026-11-19" in found["url"] and "isReturn=true" in found["url"]

def kiwi_leg(start: str, end: str, airline: str = "easyJet", carrier: str = "U2", via: str = "") -> dict:
    segment = lambda a, b, leaves, lands: {"from": a, "to": b, "fromName": a, "toName": b, "departureTime": leaves, "arrivalTime": lands,
                                           "carrierName": airline, "carrier": carrier, "flightNumber": carrier + "1"}
    segments = ([segment(start, via, "2026-11-13T06:10:00", "2026-11-13T08:00:00"), segment(via, end, "2026-11-13T09:30:00", "2026-11-13T12:10:00")]
                if via else [segment(start, end, "2026-11-13T06:10:00", "2026-11-13T09:40:00")])
    return {"from": start, "to": end, "departureTime": segments[0]["departureTime"], "arrivalTime": segments[-1]["arrivalTime"],
            "durationSeconds": 12600, "stops": len(segments) - 1, "segments": segments}

def test_an_answer_of_kiwi_is_read_and_a_refusal_is_not_taken_for_one():
    found = sources.kiwi_flights(json.dumps({"itineraries": [{"price": 31.0, "outbound": kiwi_leg("OPO", "LGW"), "bookingUrl": "https://kiwi.com/u/abc"}]}))
    flight = found[0]
    assert (flight["to"], flight["price_eur"], flight["airline"], flight["carrier"], flight["arrival_time"], flight["minutes"], flight["back"]) == \
        ("LGW", 31.0, "easyJet", "U2", "09:40", 210, None)
    assert flight["url"] == "https://kiwi.com/u/abc"
    assert sources.kiwi_flights("Tool call failed after 5 attempts: 503") is None

def test_a_connection_keeps_both_flights_and_a_link_elsewhere_is_dropped():
    answer = {"itineraries": [{"price": 80.0, "outbound": kiwi_leg("OPO", "IST", via="MAD"), "bookingUrl": "https://kiwi.com.evil.example/x"}]}
    flight = sources.kiwi_flights(json.dumps(answer))[0]
    assert [(s["from"], s["to"], s["time"]) for s in flight["segments"]] == [("OPO", "MAD", "06:10"), ("MAD", "IST", "09:30")]
    assert flight["stops"] == 1 and flight["url"] == ""

def test_a_return_of_kiwi_may_leave_from_another_airport():
    found = sources.kiwi_flights(json.dumps({"itineraries": [{"price": 45.0, "outbound": kiwi_leg("OPO", "BHX"), "inbound": kiwi_leg("MAN", "OPO")}]}))
    assert found[0]["to"] == "BHX" and found[0]["back"]["from"] == "MAN" and found[0]["price_eur"] == 45.0

def test_every_link_of_a_trip_is_https_and_goes_where_it_says(monkeypatch):
    out = {**flight("OPO", "BHX", 45, back=back_to("MAN", "LIS")), "url": "https://kiwi.com/u/abc"}
    found = finder.Found(PLACE, RETURN, [{"code": "OPO", "km": 117, "ground_eur": 12.0}], [], [], [])
    airports = {code: {"name": name} for code, name in (("OPO", "Porto"), ("BHX", "Birmingham"), ("MAN", "Manchester"), ("LIS", "Lisbon"))}
    found_links = page.links(out, found, airports)
    assert [link["label"].split()[0] for link in found_links] == ["Book", "Compare", "From", "From"]
    assert all(link["url"].startswith("https://") for link in found_links)
    assert "MAN" in found_links[1]["url"] and "returning" in found_links[1]["url"]   # Google Flights, back from Manchester
    assert "Lisbon" in found_links[3]["url"] and "Coimbra" in found_links[3]["url"]   # home from the airport the return lands at

# ---------------------------------------------------------------- the cheapest way
def test_the_cheapest_way_counts_the_way_to_the_airport():
    flights = [flight("OPO", "MAD", 20), flight("LIS", "MAD", 15), flight("OPO", "LIS", 10), flight("BCN", "MAD", 1)]
    best = finder.cheapest_by_destination(flights, {"OPO": 12.0, "LIS": 20.0})
    assert [(f["to"], f["from"], f["total_eur"]) for f in best] == [("MAD", "OPO", 32.0)]   # LIS is a departure airport, BCN is not one

def test_a_return_counts_the_way_home_from_the_airport_it_lands_at():
    flights = [flight("OPO", "MAD", 40, back=back_to("MAD", "LIS")), flight("OPO", "BCN", 30, back=back_to("BCN", "FAO"))]
    best = finder.cheapest_by_destination(flights, {"OPO": 12.0, "LIS": 20.0})
    assert [(f["to"], f["ground_eur"], f["total_eur"]) for f in best] == [("MAD", 32.0, 72.0)]   # FAO is not one of the traveller's airports

# ---------------------------------------------------------------- the breakdown
def found_with(searches: list[dict], **more) -> finder.Found:
    flights = [f for s in searches for f in s["flights"]]
    departures = [{"code": "OPO", "ground_eur": 12.0}]
    return finder.Found(PLACE, ONE_WAY, departures, finder.cheapest_by_destination(flights, {"OPO": 12.0}), flights, searches, **more)

AIRPORTS = {code: {"code": code, "city": code, "name": code, "country": country, "latitude": 40.0 + 3 * number, "longitude": 0.0, "large": True}
            for number, (code, country) in enumerate((("OPO", "PT"), ("MAD", "ES"), ("BCN", "ES"), ("LGW", "GB"), ("STN", "GB"), ("ORY", "FR"), ("KEF", "IS")))}

@pytest.fixture
def known(monkeypatch):
    """The airports, and the routes known to be flown from Porto."""
    monkeypatch.setattr(breakdown, "load_airports", lambda: AIRPORTS)
    routes = [["FR", "", "OPO", "", "MAD", "", "", "0"], ["U2", "", "OPO", "", "LGW", "", "", "0"], ["U2", "", "OPO", "", "STN", "", "", "0"],
              ["TO", "", "OPO", "", "ORY", "", "", "0"], ["FI", "", "OPO", "", "KEF", "", "", "0"], ["TP", "", "OPO", "", "BCN", "", "Y", "0"]]
    monkeypatch.setattr(routes_module, "openflights", lambda name: routes if name == "routes.dat" else [])

def test_the_requests_say_which_answers_were_full(known, monkeypatch):
    monkeypatch.setattr(breakdown.kiwi, "RESULT_CAP", 2)
    searches = [{"source": "Kiwi", "from": "OPO", "to": "ES", "answered": True, "flights": [flight("OPO", "MAD", 20), flight("OPO", "BCN", 25)],
                 "kept": "2026-10-27T09:30"},
                {"source": "Kiwi", "from": "OPO", "to": "GB", "answered": False, "flights": []}]
    requests = breakdown.requests_section(found_with(searches))
    assert requests["rows"] == [["Kiwi", "OPO", "2", "1", "1", "2", "2", "1 (read from 2026-10-27 09:30)"]]
    assert "1 answers of Kiwi were given to the same request by an earlier map" in requests["note"]

def test_the_radar_gives_each_known_route_not_found_its_reason(known, monkeypatch):
    monkeypatch.setattr(breakdown.kiwi, "RESULT_CAP", 1)
    searches = [{"source": "Ryanair", "from": "OPO", "to": "all its destinations", "answered": True, "flights": []},
                {"source": "Kiwi", "from": "OPO", "to": "GB", "answered": True, "flights": [flight("OPO", "LGW", 30, "easyJet", carrier="U2")]},
                {"source": "Kiwi", "from": "OPO", "to": "ES", "answered": True, "flights": []}]
    radar = breakdown.radar_section(found_with(searches, countries_left_out=["FR"]), AIRPORTS)
    reasons = {row[0]: row[4] for row in radar["rows"]}
    assert set(reasons) == {"MAD", "STN", "ORY", "KEF"}   # LGW was found; BCN is only a codeshare
    assert "Ryanair's own answer" in reasons["MAD"] and "full" in reasons["STN"]
    assert "left out" in reasons["ORY"] and "not among the ones asked" in reasons["KEF"]
    assert "TO" in radar["note"] and "FI" in radar["note"]   # airlines known there that no answer mentions

# ---------------------------------------------------------------- the page and the arguments
def test_a_name_cannot_break_the_page(monkeypatch):
    monkeypatch.setattr(page, "load_airports", lambda: {"XXX": {"city": "</script><b>x", "name": "", "country": "PT", "latitude": 1.0, "longitude": 2.0}})
    found = finder.Found(PLACE, RETURN, [], [{**flight("OPO", "XXX", 10), "ground_eur": 5.0, "total_eur": 15.0}], [], [])
    html = page.map_page(found, "Ryanair", [breakdown.section("</script>", ["a"], [["</script>"]])])
    script = html[html.index("const data"):]
    assert "</script>" not in script[:script.index("\n")]   # the data cannot end the script it is in

def test_the_days_are_checked():
    asked = command.read_arguments(["Coimbra", "14/11/2026", "12/11/2026", "--back", "17/11/2026"])
    assert asked.days == ["2026-11-12", "2026-11-14"] and asked.back == ["2026-11-17"]
    for wrong in (["Coimbra", "2026-11-12"], ["Coimbra", "12/11/2026", "--back", "01/11/2026"], ["Coimbra", "1/11/2026", "2/11/2026", "3/11/2026"]):
        with pytest.raises(SystemExit):
            command.read_arguments(wrong)

def test_the_planner_does_not_depend_on_this_folder():
    code = [path for path in (ROOT / "trip_planner").rglob("*.py")] + [ROOT / "main.py"]
    assert not [path.name for path in code if re.search(r"^\s*(from|import) discover", path.read_text(encoding="utf-8"), re.M)]

def test_the_radar_knows_a_city_found_by_another_airport(known, monkeypatch):
    """Kiwi gives one airport per city: Stansted on the map is London, so Gatwick is not missing."""
    monkeypatch.setitem(AIRPORTS, "STN", {**AIRPORTS["STN"], "latitude": 51.88, "longitude": 0.24})
    monkeypatch.setitem(AIRPORTS, "LGW", {**AIRPORTS["LGW"], "latitude": 51.15, "longitude": -0.19})
    searches = [{"source": "Kiwi", "from": "OPO", "to": "GB", "answered": True, "flights": [flight("OPO", "STN", 30, "easyJet", carrier="U2")]}]
    reasons = {row[0]: row[4] for row in breakdown.radar_section(found_with(searches), AIRPORTS)["rows"]}
    assert "another airport (STN" in reasons["LGW"]

# ---------------------------------------------------------------- asking Kiwi politely
def fake_leg(code: str, to_airport: bool, place, today, label: str = "", ride=None, price: float = 10.0) -> dict:
    """A way between an airport and a city, as access.leg gives it, estimated at price."""
    return {"airport": code, "to_airport": to_airport, "place": label, "price_eur": price, "minutes": 30, "grade": "C", "price_grade": "C",
            "how": "estimated from the distance", "looked_up": False}

@pytest.fixture
def services(monkeypatch, tmp_path):
    """Two departure airports and three countries, Ryanair down at Lisbon, and Kiwi refusing its fourth request.
    Returns the requests Kiwi was sent: (airport, country, airlines left out)."""
    monkeypatch.setattr(finder, "KIWI_COUNT", tmp_path / "kiwi_requests.json")
    monkeypatch.setattr(finder, "KIWI_AT_ONCE", 1)
    monkeypatch.setattr(finder, "KIWI_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(finder, "airports_near", lambda place, km, airlines_only: [{"code": "OPO", "km": 117, "country": "PT"},
                                                                                   {"code": "LIS", "km": 190, "country": "PT"}])
    monkeypatch.setattr(finder.access, "leg", fake_leg)
    monkeypatch.setattr(finder.access, "centres", lambda codes, airports, today, say: {})
    monkeypatch.setattr(finder.transfers, "look_up", lambda *arguments: {})
    monkeypatch.setattr(finder.operator_rides, "look_up", lambda *arguments: {})
    monkeypatch.setattr(finder, "load_airports", lambda: AIRPORTS)
    monkeypatch.setattr(finder, "countries_near", lambda place, departures: ["ES", "GB", "FR"])
    monkeypatch.setattr(finder, "route_counts", lambda: {})
    monkeypatch.setattr(finder.flown, "routes", lambda codes, today, say=print, extract=None: {})
    monkeypatch.setattr(finder.regions, "requests_for", lambda *arguments: [])   # the regions have their own tests

    def ryanair(code: str, days: Days) -> list:
        if code == "LIS":
            raise ConnectionError("Ryanair is down")
        return [flight("OPO", "MAD", 20)]

    async def connect() -> None:
        pass
    sent = []

    async def kiwi(code: str, country: str, days: Days, stops: int, excluded: str) -> list:
        sent.append((code, country, excluded))
        if len(sent) == 4:
            raise RuntimeError("Client error '403 Forbidden'")
        return [flight(code, "LGW", 30, "easyJet", carrier="U2")]
    monkeypatch.setattr(finder, "ryanair_destinations", ryanair)
    monkeypatch.setattr(finder, "kiwi_destinations", kiwi)
    monkeypatch.setattr(finder.kiwi, "connect_kiwi", connect)
    monkeypatch.setattr(finder.kiwi, "kiwi_search", object())
    return sent

def test_after_a_refusal_kiwi_is_not_asked_again(services):
    found = asyncio.run(finder.discover(PLACE, ONE_WAY))
    assert len(services) == 4 and "403" in found.kiwi_refused
    skipped = [s for s in found.searches if "not asked" in s["error"]]
    assert [(s["from"], s["to"]) for s in skipped] == [("LIS", "GB"), ("LIS", "FR")]
    assert finder.kiwi_used(date.today().isoformat()) == 4   # the ones sent, not the ones skipped
    errors = breakdown.errors_section(found)
    assert "403" in errors["note"] and any("ConnectionError" in row[2] for row in errors["rows"])

def test_kiwi_leaves_ryanair_out_only_where_ryanair_answered(services):
    found = asyncio.run(finder.discover(PLACE, ONE_WAY))
    assert [excluded for _, _, excluded in services] == ["FR", "FR", "FR", ""]   # Lisbon's Ryanair request failed
    assert "FR too from LIS" in found.kiwi_options

def test_the_maps_of_one_day_share_a_limit_of_requests_to_kiwi(services):
    (finder.KIWI_COUNT).write_text(json.dumps({"day": date.today().isoformat(), "requests": finder.KIWI_DAILY - 4}), encoding="utf-8")
    found = asyncio.run(finder.discover(PLACE, ONE_WAY))
    assert found.kiwi_allowed == 4 and found.countries_left_out == ["FR"]   # two countries for each of the two airports
    (finder.KIWI_COUNT).write_text(json.dumps({"day": "2020-01-01", "requests": 999}), encoding="utf-8")
    assert finder.kiwi_used(date.today().isoformat()) == 0   # another day's count is not today's

def test_a_light_map_asks_kiwi_fewer_times(services):
    found = asyncio.run(finder.discover(PLACE, ONE_WAY, kiwi_requests=2))
    assert found.kiwi_allowed == 2 and len(services) == 2 and found.countries_left_out == ["GB", "FR"]

def test_kiwi_saying_something_else_than_an_answer_is_an_error_with_its_words(monkeypatch):
    class Refusing:
        async def ainvoke(self, asked):
            return "Tool call failed: 403 Forbidden"
    monkeypatch.setattr(sources.kiwi, "kiwi_search", Refusing())
    with pytest.raises(ValueError, match="403"):
        asyncio.run(sources.kiwi_destinations("OPO", "GB", ONE_WAY))

# ---------------------------------------------------------------- one dot per city
def test_the_airports_of_one_city_are_one_dot():
    airports = {"STN": {"country": "GB", "city": "London"}, "LGW": {"country": "GB", "city": "London"}, "MAD": {"country": "ES", "city": "Madrid"},
                "XYZ": {"country": "GB", "city": ""}}
    destinations = [{"to": "STN", "from": "OPO", "total_eur": 30.0}, {"to": "MAD", "from": "OPO", "total_eur": 35.0},
                    {"to": "LGW", "from": "LIS", "total_eur": 40.0}, {"to": "XYZ", "from": "OPO", "total_eur": 50.0}]
    grouped = finder.one_per_city(destinations, airports)
    assert [d["to"] for d in grouped] == ["STN", "MAD", "XYZ"]
    assert grouped[0]["also"] == [{"to": "LGW", "from": "LIS", "total_eur": 40.0}]

# ---------------------------------------------------------------- from the map to the planner
def test_each_trip_of_the_map_opens_the_planner_on_its_own_cities_and_day():
    out = {**flight("OPO", "BHX", 45, back=back_to("MAN", "LIS"))}
    found = finder.Found(PLACE, RETURN, [], [out], [], [])
    airports = {"BHX": {"city": "Birmingham"}, "MAN": {"city": "Manchester"}}
    assert plan.trips(found, airports) == {"BHX-out": ["Coimbra", "Birmingham", "12/11/2026"],
                                           "BHX-back": ["Manchester", "Coimbra", "16/11/2026"]}

def test_the_server_opens_only_a_trip_of_its_map_asked_by_its_page():
    opened = []
    planner = serve.Planner({"BHX-out": ["Coimbra", "Birmingham", "12/11/2026"]}, "secret", opened.append)
    ask = lambda body, now=0.0: planner.ask(json.dumps(body).encode(), now)
    assert ask({"token": "guess", "trip": "BHX-out"})[0] == 403
    assert ask({"token": "secret", "trip": "Coimbra & del C:"})[0] == 404
    assert planner.ask(b"<html>", 0.0)[0] == 400
    assert ask({"token": "secret", "trip": "BHX-out"}) == (200, "opening")
    assert ask({"token": "secret", "trip": "BHX-out"}, 1.0) == (200, "already opening")   # a double click is one window
    assert len(opened) == 1 and opened[0][-4:] == ["discover.plan", "Coimbra", "Birmingham", "12/11/2026"]

def test_a_page_opened_as_a_file_shows_the_command_and_has_no_token(monkeypatch):
    monkeypatch.setattr(page, "load_airports", lambda: {"BHX": {"city": "Birmingham", "name": "Birmingham Airport", "country": "GB",
                                                                "latitude": 52.4, "longitude": -1.7}})
    found = finder.Found(PLACE, ONE_WAY, [], [{**flight("OPO", "BHX", 10), "ground_eur": 5.0, "total_eur": 15.0}], [], [])
    data = json.loads(re.search(r"const data = (.*);\n", page.map_page(found, "Ryanair", [])).group(1))
    assert data["token"] == "" and "discover.plan Coimbra Birmingham 12/11/2026" in data["state"]["points"][0]["plan"]["out"]["command"]
    served = json.loads(re.search(r"const data = (.*);\n", page.map_page(found, "Ryanair", [], token="abc")).group(1))
    assert served["token"] == "abc"

def test_the_page_draws_the_lines_in_the_planners_colours_and_has_the_cities_with_no_flight(monkeypatch):
    from trip_planner.gui.style import LIGHT_COLOURS
    monkeypatch.setattr(page, "load_airports", lambda: {"BHX": {"city": "Birmingham", "name": "Birmingham Airport", "country": "GB",
                                                                "latitude": 52.4, "longitude": -1.7}})
    salamanca = {"key": "ES|Salamanca|40.96", "name": "Salamanca", "country": "ES", "lat": 40.96, "lon": -5.66, "minutes": 269,
                 "modes": ["bus"], "ground": []}
    found = finder.Found(PLACE, ONE_WAY, [], [{**flight("OPO", "BHX", 10), "ground_eur": 5.0, "total_eur": 15.0}], [], [], by_ground=[salamanca])
    data = json.loads(re.search(r"const data = (.*);\n", page.map_page(found, "Ryanair", [], landing_minutes=45)).group(1))
    assert data["grade_colours"] == LIGHT_COLOURS and data["landing_minutes"] == 45 and data["origin"]["short"] == "Coimbra"
    assert data["state"]["by_ground"] == [salamanca]

# ---------------------------------------------------------------- OpenFlights
def test_an_error_page_is_not_kept_as_openflights():
    assert routes_module.rows_of("<html><body>Rate limited</body></html>") is None
    row = "FR,4296,OPO,1636,MAD,1229,,0,738"
    assert len(routes_module.rows_of("\n".join([row] * routes_module.MIN_ROWS))) == routes_module.MIN_ROWS

# ---------------------------------------------------------------- the traveller's airports
def test_the_airports_are_the_ones_with_most_routes_not_only_the_closest(monkeypatch):
    near = [{"code": code, "km": km} for code, km in (("ANR", 3), ("BRU", 40), ("EIN", 80), ("CRL", 95), ("OST", 110), ("LGG", 120), ("MST", 130))]
    monkeypatch.setattr(finder, "airports_near", lambda place, km, airlines_only: near)
    monkeypatch.setattr(finder, "MAX_AIRPORTS", 3)
    monkeypatch.setattr(finder, "route_counts", lambda: {"ANR": 4, "BRU": 180, "EIN": 70, "CRL": 120, "OST": 10})
    monkeypatch.setattr(finder.flown, "routes", lambda codes, today, say=print, extract=None: {})
    assert [airport["code"] for airport in finder.departure_airports(PLACE)] == ["BRU", "EIN", "CRL"]   # Antwerp's own is left out
    # The routes of today, when read, come before the ones of 2014: Ostend flies to more places now
    today = {"OST": {"routes": [{"to": f"X{number:02d}", "airline": "TUI", "seasonal": False} for number in range(90)], "checked": "", "article": ""}}
    monkeypatch.setattr(finder.flown, "routes", lambda codes, day, say=print, extract=None: {code: today[code] for code in codes if code in today})
    monkeypatch.setattr(finder.flown, "within", lambda routes, place, km, airports: routes)   # made-up destinations, all within reach
    assert [airport["code"] for airport in finder.departure_airports(PLACE)] == ["BRU", "CRL", "OST"]
    monkeypatch.setattr(finder, "route_counts", lambda: None)
    monkeypatch.setattr(finder.flown, "routes", lambda codes, day, say=print, extract=None: {})
    assert [airport["code"] for airport in finder.departure_airports(PLACE)] == ["ANR", "BRU", "EIN"]   # without routes: the closest

def test_kiwi_is_asked_for_all_the_airports_at_once(services, monkeypatch):
    monkeypatch.setattr(finder, "airports_near", lambda place, km, airlines_only: [{"code": "OPO", "km": 117, "country": "PT"},
                                                                                   {"code": "FAO", "km": 300, "country": "PT"},
                                                                                   {"code": "LIS", "km": 190, "country": "PT"}])
    asyncio.run(finder.discover(PLACE, ONE_WAY))
    assert services[:3] == [("OPO,FAO", "ES", "FR"), ("OPO,FAO", "GB", "FR"), ("OPO,FAO", "FR", "FR")]   # Ryanair answered there
    assert services[3] == ("LIS", "ES", "")   # Ryanair did not answer at Lisbon: every airline from there
    groups = finder.kiwi_groups([{"code": "OPO"}, {"code": "LIS"}], {"OPO", "LIS"})
    assert groups == [(["OPO", "LIS"], "FR")]

def test_the_time_in_the_city_counts_from_reaching_its_centre_to_leaving_it():
    flight = {"date": "2026-11-12", "time": "07:00", "arrival_date": "2026-11-12", "arrival_time": "10:15",
              "back": {"date": "2026-11-14", "time": "18:00"},
              "access": [{"minutes": 60, "looked_up": True}, {"minutes": 45, "looked_up": True},
                         {"minutes": 45, "looked_up": False}, {"minutes": 60, "looked_up": True}]}
    # in at 10:15 + 45 + 45 = 11:45 on the 12th, out at 18:00 - 90 - 45 = 15:45 on the 14th
    assert page.stay(flight, airport_minutes=90, landing_minutes=45) == {"minutes": 52 * 60, "nights": 2, "estimated": True}
    assert page.stay({**flight, "back": None}) is None   # one way: no time in the city
    late = {**flight, "back": {"date": "2026-11-12", "time": "12:00"}}
    assert page.stay(late, 90, 45)["minutes"] == 0   # back before having reached the centre

def test_the_regions_with_fewest_cities_found_get_requests_of_their_own(services, monkeypatch):
    monkeypatch.setattr(finder.regions, "requests_for", lambda *arguments: [
        {"region": "Baltic states", "cities": 0, "targets": ["LT", "LV", "RIX,VNO"]}])
    found = asyncio.run(finder.discover(PLACE, ONE_WAY, kiwi_requests=2))
    assert services[2:] == [("OPO,LIS", "LT", "FR"), ("OPO,LIS", "LV", "FR")]   # the 403 came at the 4th: RIX,VNO not sent
    assert [s["to"] for s in found.searches if s["to"].startswith("region")][:2] == ["region Baltic states: LT", "region Baltic states: LV"]

def test_a_request_kiwi_answered_within_the_last_hours_is_not_sent_again(services):
    first = asyncio.run(finder.discover(PLACE, ONE_WAY, kiwi_requests=2))
    assert len(services) == 2 and not any(s.get("kept") for s in first.searches)
    again = asyncio.run(finder.discover(PLACE, ONE_WAY, kiwi_requests=2))
    assert len(services) == 4   # the two kept cost nothing, so the map could ask two more: GB, which the first left out
    kept = [s for s in again.searches if s["source"] == "Kiwi" and s.get("kept")]
    assert [(s["from"], s["to"]) for s in kept] == [("OPO", "ES"), ("LIS", "ES")] and kept[0]["flights"]
    assert finder.kiwi_used(date.today().isoformat()) == 4

def test_a_kept_answer_is_the_same_request_in_any_order_and_too_old_ones_are_forgotten(tmp_path):
    from datetime import datetime, timedelta
    days = Days("2026-11-12", "2026-11-14")
    assert finder.kiwi_key("OPO,LIS", "ES", days, 0, "FR") == finder.kiwi_key("LIS,OPO", "ES", days, 0, "FR")
    assert finder.kiwi_key("OPO", "ES", days, 0, "FR") != finder.kiwi_key("OPO", "ES", days, 1, "FR")
    now = datetime(2026, 10, 27, 12, 0)
    finder.keep_answer("old", [], now - timedelta(hours=finder.KIWI_ANSWER_HOURS + 1))
    finder.keep_answer("new", [{"to": "MAD"}], now)
    assert list(finder.kept_answers(now)) == ["new"]
