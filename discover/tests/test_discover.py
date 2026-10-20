"""The discover feature, apart from the planner: pytest discover. No network.

- The answers of the two sources are read, one way and there and back, and an answer that is not one is told apart.
- The cheapest way to each destination counts the way to the airport, and back home from the one the return lands at.
- The breakdown counts every request, says which answers were full, and explains the known routes not found.
- The map page cannot be broken by a name, and the arguments are checked.
- The planner does not depend on this folder."""
import json
import re
from pathlib import Path

import pytest

from discover import __main__ as command, breakdown, finder, page, sources
from discover import routes as routes_module
from discover.sources import Days
from trip_planner.places import Place

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
    searches = [{"source": "Kiwi", "from": "OPO", "to": "ES", "answered": True, "flights": [flight("OPO", "MAD", 20), flight("OPO", "BCN", 25)]},
                {"source": "Kiwi", "from": "OPO", "to": "GB", "answered": False, "flights": []}]
    requests = breakdown.requests_section(found_with(searches))
    assert requests["rows"] == [["Kiwi", "OPO", "2", "1", "1", "2", "2"]]

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
