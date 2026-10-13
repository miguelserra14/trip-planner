"""The rules of the operators' prices on small made-up inputs: no service, no checkpoint."""
from datetime import date, timedelta

import pytest

from trip_planner.itineraries.legs import estimated_price, price_with_tickets
from trip_planner.sources import fares
from trip_planner.sources.fares import FareSource, Trip

TRAVEL = (date.today() + timedelta(days=70)).isoformat()
SERVICE = (date.today() + timedelta(days=3)).isoformat()

def step(agency: str = "", rail: bool = True, time: str = "10:00", arrive: str = "11:00", from_id: str = "", to_id: str = "", km: int = 50) -> dict:
    return {"time": time, "arrive": arrive, "line": "X", "agency": agency, "from": "A", "to": "B", "rail": rail, "coach": not rail,
            "km": km, "from_id": from_id, "to_id": to_id, "from_at": [48.0, 16.0], "to_at": [48.3, 14.3]}

def trip(leave: str = "10:28", arrive: str = "11:43") -> Trip:
    return Trip("A", "B", "", "", (), (), TRAVEL, leave, arrive)

@pytest.fixture
def sells(monkeypatch):
    """Operators that answer from a table instead of the network: {(operator, day, leave): price}."""
    prices: dict = {}
    asked: list = []

    def source(name: str, through: bool) -> FareSource:
        def fare(wanted: Trip) -> float | None:
            asked.append((name, wanted.day, wanted.leave, wanted.arrive))
            return prices.get((name, wanted.day, wanted.leave))
        return FareSource(name, "XX", lambda s: s["agency"] == name, fare, through)

    monkeypatch.setattr(fares, "FARE_SOURCES", [source("Rail", True), source("Coach", False)])
    monkeypatch.setattr(fares, "CACHE", {})
    prices["asked"] = asked
    return prices

def test_a_departure_is_the_same_within_a_few_minutes():
    assert trip().matches("2026-10-29T10:28:00.000", "2026-10-29T11:43:00.000")
    assert trip().matches("10:29", "11:47")            # the two sources do not agree on the minute a train gets in
    assert not trip().matches("10:33", "11:43")        # another train
    assert not trip().matches("10:28", "12:10")        # the same departure, a slower way

def test_each_operator_is_recognised_by_its_vehicles():
    name = lambda s: getattr(fares.source_of(s), "name", None)
    assert name(step("OEBB Personenverkehr AG Kundenservice")) == "ÖBB"
    assert name(step("Trenord")) == name(step("TRENITALIA")) == "Trenitalia"
    assert name(step("MÁV", from_id="hu-mav_2121")) == "MÁV"
    assert name(step("Elron")) == "Elron"
    assert name(step("STUDENT AGENCY k.s.", rail=False)) == "RegioJet"
    assert name(step("FlixBus-eu", rail=False, from_id="eu-flixbus_a", to_id="eu-flixbus_b")) == "FlixBus"
    assert name(step("Chemins de Fer Luxembourgeois", from_id="lu-x_1", to_id="lu-x_2")).startswith("Luxembourg")
    assert name(step("NMBS/SNCB", from_id="be-sncb_8200100", to_id="be-sncb_8200120")).startswith("Luxembourg")
    assert name(step("NMBS/SNCB", from_id="be-sncb_8200100", to_id="be-sncb_8866001")) is None   # leaves Luxembourg
    assert name(step("NS")) is None

def test_trains_in_a_row_of_one_railway_are_one_ticket(sells):
    steps = [step("Rail", time="10:00", arrive="10:30"), step("Rail", time="10:40", arrive="11:30"), step("City bus", rail=False),
             step("Rail", time="12:00", arrive="12:30"), step("Coach", rail=False), step("Coach", rail=False)]
    assert [(source.name, positions) for source, positions in fares.runs(steps)] == [
        ("Rail", [0, 1]), ("Rail", [3]), ("Coach", [4]), ("Coach", [5])]

def test_the_travel_date_is_asked_first_then_the_day_of_the_timetable(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    sells[("Coach", SERVICE, "12:00")] = 7.0
    steps = [step("Rail"), step("Coach", rail=False, time="12:00", arrive="13:00")]
    found = fares.tickets(steps, TRAVEL, SERVICE)
    assert found == [{"positions": [0], "operator": "Rail", "price_eur": 20.0, "travel_day": True, "tariff": False, "confirms": False},
                     {"positions": [1], "operator": "Coach", "price_eur": 7.0, "travel_day": False, "tariff": False, "confirms": False}]
    assert ("Rail", SERVICE, "10:00", "11:00") not in sells["asked"]   # found for the travel date: not asked again

def test_an_operator_is_asked_once_for_the_same_ticket(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    fares.tickets([step("Rail")], TRAVEL, SERVICE)
    fares.tickets([step("Rail")], TRAVEL, SERVICE)
    assert len(sells["asked"]) == 1

def test_an_operator_that_fails_leaves_no_ticket(monkeypatch):
    def broken(wanted: Trip) -> float | None:
        raise RuntimeError("the site changed")
    monkeypatch.setattr(fares, "FARE_SOURCES", [FareSource("Rail", "XX", lambda s: True, broken)])
    monkeypatch.setattr(fares, "CACHE", {})
    assert fares.tickets([step("Rail")], TRAVEL, SERVICE) == []

def leg(steps: list, kind: str = "estimate") -> dict:
    return {"kind": "timetable", "steps": steps, "service_day": SERVICE, "price_kind": kind,
            "price_eur": sum(estimated_price(s["km"]) for s in steps)}

FLIGHT = {"date": TRAVEL}

def test_every_vehicle_priced_for_the_travel_date_is_a_confirmed_price(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail")])
    price_with_tickets(priced, FLIGHT["date"], False)
    assert (priced["price_eur"], priced["price_kind"], priced["confirmed_eur"]) == (20.0, "operator", 20.0)

def test_a_price_of_another_day_is_typical_and_not_confirmed(sells):
    sells[("Rail", SERVICE, "10:00")] = 20.0
    priced = leg([step("Rail")])
    price_with_tickets(priced, FLIGHT["date"], False)
    assert (priced["price_eur"], priced["price_kind"], priced["confirmed_eur"]) == (20.0, "operator_typical", 0)

def test_vehicles_nobody_priced_keep_their_estimate(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail"), step("City bus", rail=False, km=10)])
    price_with_tickets(priced, FLIGHT["date"], False)
    assert priced["price_kind"] == "partly" and priced["price_eur"] == 20.0 + estimated_price(10) and priced["confirmed_eur"] == 20.0

def test_free_trains_are_not_asked_for(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail")], kind="free")
    price_with_tickets(priced, FLIGHT["date"], True)
    assert sells["asked"] == [] and "tickets" not in priced

def test_a_models_price_for_the_whole_way_is_kept_when_only_part_is_sold(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail"), step("City bus", rail=False)], kind="model")
    before = priced["price_eur"]
    price_with_tickets(priced, FLIGHT["date"], False)
    assert (priced["price_eur"], priced["price_kind"]) == (before, "model")

def test_a_leg_without_vehicles_is_left_alone(sells):
    model_leg = {"kind": "model", "steps": [], "price_kind": "model", "price_eur": 9.0}
    price_with_tickets(model_leg, FLIGHT["date"], False)
    assert model_leg["price_eur"] == 9.0
