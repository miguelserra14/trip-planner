"""The ground prices of a report asked again before booking (Refresh prices, --prices), on small made-up inputs:
no service, no checkpoint."""
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta

import pytest

from trip_planner.report import full
from trip_planner.sources import fares
from trip_planner.sources.fares import FareSource, NotRunning
from trip_planner.tools import prices, verify
from trip_planner.trip.places import Place

SOON = (date.today() + timedelta(days=20)).isoformat()
PAST = (date.today() - timedelta(days=2)).isoformat()
TWO_DAYS_AGO = (datetime.now() - timedelta(days=2)).isoformat(timespec="minutes")
AN_HOUR_AGO = (datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes")

def vehicle(day: str = SOON) -> dict:
    return {"time": "08:00", "arrive": "09:00", "line": "X", "agency": "Coach", "from": "A", "to": "B", "from_id": "", "to_id": "",
            "from_at": [], "to_at": [], "rail": False, "coach": True, "mode": "COACH", "km": 100, "day": day}

def timetable_leg(price: float, day: str = SOON, tariff: bool = False) -> dict:
    """A connection of one coach found in a timetable, priced by its operator's booking system, or by a published tariff."""
    return {"side": "to", "grade": "A", "kind": "timetable", "day": day, "vehicles": [vehicle(day)], "price_eur": price,
            "price_kind": "tariff" if tariff else "operator", "confirmed_eur": price,
            "tickets": [{"positions": [0], "operator": "Coach", "price_eur": price, "travel_day": True, "tariff": tariff}]}

def model_leg(price: float) -> dict:
    return {"side": "from", "grade": "C", "kind": "model", "day": SOON, "vehicles": [], "price_eur": price, "price_kind": "model",
            "confirmed_eur": 0.0, "tickets": []}

@pytest.fixture
def report(tmp_path, monkeypatch):
    """The folders of the reports, empty, and a way to write one report of one option in its trip's folder: its .md
    and its .json."""
    monkeypatch.setattr(full, "REPORTS", tmp_path)
    monkeypatch.setattr(full, "SAVED_REPORTS", tmp_path / "saved")
    folder = tmp_path / "trip_a_b"
    folder.mkdir()

    def write(legs: list, read: str = TWO_DAYS_AGO) -> dict:
        kept = {"trip": "A -> B", "prices_read": read, "options": [{
            "number": 1, "flight": "F", "route": "OPO → BRU", "date": SOON, "flight_price": 50.0,
            "total": 50.0 + sum(leg["price_eur"] for leg in legs), "legs": legs}]}
        (folder / "trip_a_b.md").write_text("# The report\n", encoding="utf-8")
        (folder / "trip_a_b.json").write_text(json.dumps(kept), encoding="utf-8")
        return kept
    write.json = lambda: json.loads((folder / "trip_a_b.json").read_text(encoding="utf-8"))
    write.md = lambda: (folder / "trip_a_b.md").read_text(encoding="utf-8")
    return write

@pytest.fixture
def coach(monkeypatch):
    """A coach operator that sells its 08:00 at the price in the returned dict, and no longer runs it once it is not there."""
    price = {"08:00": 19.0}

    def fare(trip):
        if trip.leave not in price:
            raise NotRunning
        return price[trip.leave]
    monkeypatch.setattr(fares, "FARE_SOURCES", [FareSource("Coach", "XX", lambda step: step["agency"] == "Coach", fare, confirms=True)])
    monkeypatch.setattr(fares, "CACHE", {})
    monkeypatch.setattr(fares, "NOT_RUNNING", set())
    return price

def test_only_prices_read_more_than_a_day_ago_for_a_trip_to_come_are_asked_again(report):
    report([timetable_leg(15.0)])
    assert [path.name for path in prices.stale_reports()] == ["trip_a_b.json"]
    report([timetable_leg(15.0)], read=AN_HOUR_AGO)
    assert prices.stale_reports() == []
    report([timetable_leg(15.0, day=PAST)])
    assert prices.stale_reports() == []   # the trip has passed: nothing left to book

def test_a_new_price_of_the_operator_changes_the_leg_the_total_and_the_report(report, coach):
    report([timetable_leg(15.0), model_leg(10.0)])
    markdown = prices.refresh()
    assert "15.00 → 19.00 EUR (Coach)" in markdown and "75.00 → 79.00 EUR" in markdown
    kept = report.json()
    assert kept["options"][0]["total"] == 79.0 and kept["options"][0]["legs"][0]["price_eur"] == 19.0
    assert "15.00 → 19.00 EUR" in report.md() and prices.stale_reports() == []   # read now: good for another day

def test_a_price_kept_from_earlier_in_the_session_is_not_used(report, coach):
    leg = timetable_leg(15.0)
    report([leg])
    fares.CACHE[("Coach", fares.trip_of(leg["vehicles"], [0], SOON))] = 15.0
    assert "15.00 → 19.00 EUR" in prices.refresh()

def test_a_departure_no_longer_sold_is_said_and_its_price_stays(report, coach):
    report([timetable_leg(15.0)])
    coach.clear()
    markdown = prices.refresh()
    assert "Coach no longer sells this departure" in markdown and "Option 1: Coach no longer sells" in markdown
    assert report.json()["options"][0]["legs"][0]["price_eur"] == 15.0

def test_a_tariff_and_a_models_price_are_not_asked_again(report, coach):
    report([timetable_leg(15.0, tariff=True), model_leg(10.0)])
    markdown = prices.refresh()
    assert markdown.count("not from a booking system") == 2 and "| 75.00 EUR |" in markdown and "EUR →" not in markdown

def test_a_ride_an_operator_sold_is_asked_of_that_operator_again(report, monkeypatch):
    asked = []

    def rides(city, airport, day, to_airport):
        asked.append((city.english_name, airport["code"], day, to_airport))
        return [{"leave": 480, "arrive": 540, "price_eur": 12.0}, {"leave": 600, "arrive": 660, "price_eur": 8.0}]
    monkeypatch.setitem(fares.RIDE_SOURCES, "FlixBus", rides)
    city = asdict(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.2, -8.4))
    ride = {"side": "to", "grade": "A", "kind": "operator", "day": SOON, "vehicles": [vehicle()], "price_eur": 9.0, "price_kind": "operator",
            "confirmed_eur": 9.0, "tickets": [], "source": "FlixBus", "leave": 480, "arrive": 540, "city": city, "airport": {"code": "OPO"}}
    report([ride])
    assert "9.00 → 12.00 EUR (FlixBus)" in prices.refresh()
    assert asked == [("Coimbra", "OPO", SOON, True)]

def test_a_report_keeps_its_prices_and_when_they_were_read():
    leg = {"grade": "A", "kind": "timetable", "leave": 480, "steps": [vehicle()], "price_eur": 15.0, "price_kind": "operator",
           "tickets": [{"positions": [0], "operator": "Coach", "price_eur": 15.0, "travel_day": True, "tariff": False, "confirms": True}]}
    flight = {"id": "F", "date": SOON, "route": "OPO → BRU"}
    kept = verify.record([{"flight": flight, "to": leg, "from": leg, "flight_price": 50.0, "total_cost": 80.0}])
    option = kept["options"][0]
    assert option["total"] == 80.0 and option["legs"][0]["price_eur"] == 15.0 and option["legs"][0]["tickets"][0]["operator"] == "Coach"
    assert datetime.now() - datetime.fromisoformat(kept["prices_read"]) < timedelta(minutes=2)
