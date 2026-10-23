"""The published tariffs of city networks (fares.TARIFFS): which vehicles each one prices, and at what price.
The exchange rates are fixed here, so no service is asked."""
from datetime import date, timedelta

import pytest

from trip_planner.itineraries.legs import price_with_tickets
from trip_planner.sources import fares
from trip_planner.sources.operators import base

DAY = (date.today() + timedelta(days=30)).isoformat()
AIRPORT_PRAGUE, CENTRE_PRAGUE, KLADNO = [50.1008, 14.2600], [50.0830, 14.4350], [50.1473, 14.1028]

@pytest.fixture(autouse=True)
def rates(monkeypatch):
    monkeypatch.setattr(base, "RATES", {"CZK": 25.0, "HUF": 400.0})
    monkeypatch.setattr(fares, "CACHE", {})

def step(agency: str, line: str = "1", time: str = "10:00", arrive: str = "10:20", start: list = (), end: list = (), rail: bool = False) -> dict:
    return {"time": time, "arrive": arrive, "line": line, "agency": agency, "from": "A", "to": "B", "rail": rail, "coach": False, "km": 5,
            "from_id": "", "to_id": "", "from_at": list(start), "to_at": list(end)}

def priced(steps: list) -> list:
    return [(ticket["operator"].split(" (")[0], ticket["positions"], ticket["price_eur"]) for ticket in fares.tickets(steps, DAY, None)]

def test_every_tariff_says_when_it_was_read_and_none_is_too_old():
    assert fares.TARIFFS and all(source.tariff and not source.expired for source in fares.TARIFFS)

def test_a_tariff_read_long_ago_is_no_longer_used(monkeypatch):
    old = (date.today() - timedelta(days=fares.TARIFF_DAYS + 1)).isoformat()
    stale = fares.FareSource("Old", "XX", lambda s: True, lambda trip: 1.0, checked=old)
    monkeypatch.setattr(fares, "FARE_SOURCES", [stale])
    assert stale.expired and fares.source_of(step("anything")) is None

def test_prague_one_ticket_covers_the_bus_and_the_metro_within_its_time():
    bus = step("Pražská integrovaná doprava", "59", "10:00", "10:17", AIRPORT_PRAGUE, CENTRE_PRAGUE)
    metro = step("Pražská integrovaná doprava", "A", "10:20", "10:28", CENTRE_PRAGUE, CENTRE_PRAGUE)
    assert priced([bus, metro]) == [("PID Prague", [0, 1], 1.56)]     # 30 minutes, 39 CZK
    metro["arrive"] = "10:50"
    assert priced([bus, metro]) == [("PID Prague", [0, 1], 2.0)]      # 90 minutes, 50 CZK

def test_prague_a_bus_out_of_the_city_is_another_zone_and_not_priced():
    assert priced([step("Pražská integrovaná doprava", "322", start=AIRPORT_PRAGUE, end=KLADNO)]) == []

def test_budapest_each_vehicle_is_a_ticket_and_the_airport_bus_has_its_own():
    assert priced([step("BKK", "100E"), step("BKK", "M3")]) == [("BKK Budapest", [0], 6.25), ("BKK Budapest", [1], 1.25)]

def test_brussels_changes_within_the_hour_are_one_ticket_but_not_the_airport_line():
    assert priced([step("STIB", "1", "10:00", "10:15"), step("STIB", "25", "10:20", "10:40")]) == [("STIB Brussels", [0, 1], 2.40)]
    assert priced([step("STIB", "12")]) == []

def test_charleroi_only_the_airport_bus_is_priced():
    assert priced([step("LETEC", "A1")]) == [("TEC Charleroi Airport", [0], 6.0)]
    assert priced([step("LETEC", "68")]) == []

def test_lisbon_the_metro_and_the_bus_share_a_ticket_but_not_the_suburban_buses():
    assert priced([step("Metropolitano de Lisboa, E.P.E.", "Vm", "10:00", "10:20"), step("Carris", "728", "10:25", "10:45")]) == [("Carris/Metro Lisbon", [0, 1], 1.90)]
    assert priced([step("Carris Metropolitana", "4403")]) == []

def test_a_ride_longer_than_the_ticket_lasts_is_left_to_the_estimate():
    assert priced([step("De Lijn", "T1", "10:00", "11:30")]) == []
    assert priced([step("De Lijn", "T1", "10:00", "10:40")]) == [("De Lijn", [0], 3.0)]

def test_a_published_tariff_is_a_typical_price_and_not_a_confirmed_one():
    leg = {"kind": "timetable", "steps": [step("De Lijn", "T1", "10:00", "10:40")], "service_day": None, "price_kind": "estimate", "price_eur": 2.0}
    price_with_tickets(leg, DAY, False)
    assert (leg["price_eur"], leg["price_kind"], leg["confirmed_eur"]) == (3.0, "tariff", 0)
