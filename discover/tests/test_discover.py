"""The discover feature, apart from the planner: pytest discover. No network.

- The answers of the two sources are read, and an answer that is not one is told apart.
- The cheapest way to each destination counts the way to the departure airport.
- The map page cannot be broken by a name.
- The planner does not depend on this folder."""
import json
import re
from pathlib import Path

from discover import finder, page, sources
from trip_planner.places import Place

ROOT = Path(__file__).parents[2]

def flight(start: str, end: str, price: float, airline: str = "Ryanair") -> dict:
    return {"from": start, "to": end, "price_eur": price, "date": "2026-11-12", "time": "07:00", "airline": airline, "stops": 0, "source": "Kiwi"}

def test_an_answer_of_ryanair_is_read():
    answer = {"fares": [{"outbound": {"arrivalAirport": {"iataCode": "FAO"}, "price": {"value": 14.99}, "departureDate": "2026-11-17T07:55:00"}}]}
    assert sources.ryanair_flights(answer, "OPO") == [{"from": "OPO", "to": "FAO", "price_eur": 14.99, "date": "2026-11-17", "time": "07:55",
                                                       "airline": "Ryanair", "stops": 0, "source": "Ryanair"}]

def test_an_answer_of_kiwi_is_read_and_a_refusal_is_not_taken_for_one():
    leg = {"from": "OPO", "to": "LGW", "departureTime": "2026-11-13T06:10:00", "stops": 0, "segments": [{"carrierName": "easyJet"}]}
    found = sources.kiwi_flights(json.dumps({"itineraries": [{"price": 31.0, "outbound": leg}]}))
    assert found == [{"from": "OPO", "to": "LGW", "price_eur": 31.0, "date": "2026-11-13", "time": "06:10", "airline": "easyJet", "stops": 0, "source": "Kiwi"}]
    assert sources.kiwi_flights("Tool call failed after 5 attempts: 503") is None

def test_the_cheapest_way_counts_the_way_to_the_airport():
    flights = [flight("OPO", "MAD", 20), flight("LIS", "MAD", 15), flight("OPO", "LIS", 10), flight("BCN", "MAD", 1)]
    best = finder.cheapest_by_destination(flights, {"OPO": 12.0, "LIS": 20.0})
    assert [(f["to"], f["from"], f["total_eur"]) for f in best] == [("MAD", "OPO", 32.0)]   # LIS is a departure airport, BCN is not one

def test_a_name_cannot_break_the_page(monkeypatch):
    monkeypatch.setattr(page, "load_airports", lambda: {"XXX": {"city": "</script><b>x", "name": "", "country": "PT", "latitude": 1.0, "longitude": 2.0}})
    place = Place("a", "a", "a", "", 0.0, 0.0)
    html = page.map_page(place, [{**flight("OPO", "XXX", 10), "ground_eur": 5.0, "total_eur": 15.0}], "2026-11-12", "2026-11-19", "Ryanair")
    script = html[html.index("const data"):]
    assert "</script><b>" not in script[:script.index("\n")]   # the data cannot end the script it is in

def test_the_planner_does_not_depend_on_this_folder():
    code = [path for path in (ROOT / "trip_planner").rglob("*.py")] + [ROOT / "main.py"]
    assert not [path.name for path in code if re.search(r"^\s*(from|import) discover", path.read_text(encoding="utf-8"), re.M)]
