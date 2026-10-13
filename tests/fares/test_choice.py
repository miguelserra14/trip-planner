"""What the operators' answers change beyond the price of a leg, on small made-up inputs. No network.

- The connection of a leg is chosen on real prices: the tickets of the ones in front are read before choosing.
- An operator that sells the very departure on the day of the trip confirms the timetable for that day, on
  either side of the flight."""
from datetime import date, timedelta

import pytest

from trip_planner import current
from trip_planner.itineraries.legs import from_airport_leg, price_with_tickets, to_airport_leg
from trip_planner.places import Place, TripPlaces
from trip_planner.sources import fares
from trip_planner.sources.fares import FareSource

TRAVEL = (date.today() + timedelta(days=70)).isoformat()
SERVICE = (date.today() + timedelta(days=3)).isoformat()

def step(agency: str, time: str, arrive: str, rail: bool = True, km: int = 100) -> dict:
    return {"time": time, "arrive": arrive, "line": "X", "agency": agency, "from": "A", "to": "B", "rail": rail, "coach": not rail,
            "km": km, "from_id": "", "to_id": "", "from_at": [48.0, 16.0], "to_at": [48.3, 14.3]}

def connection(leave: int, arrive: int, *steps: dict) -> dict:
    return {"leave": leave, "arrive": arrive, "steps": list(steps), "urls": [], "grade": "B", "source": "timetable",
            "service_day": SERVICE, "price_eur": None}

@pytest.fixture
def sells(monkeypatch):
    """Operators that answer from a table instead of the network: {(operator, day, leave): price}.
    "Rail" and "Coach" only answer for a departure they sell. "City" is a tariff: one price, whatever the departure."""
    prices: dict = {}

    def source(name: str, **kind) -> FareSource:
        return FareSource(name, "XX", lambda s: s["agency"] == name, lambda wanted: prices.get((name, wanted.day, wanted.leave)), **kind)

    monkeypatch.setattr(fares, "FARE_SOURCES", [source("Rail", confirms=True), source("Coach", confirms=True),
                                                source("City", checked=date.today().isoformat())])
    monkeypatch.setattr(fares, "CACHE", {})
    place = Place("a", "a", "a", "", 48.0, 16.0)
    airport = {"code": "XXX", "name": "XXX", "city": "XXX", "country": "", "km": 100}
    monkeypatch.setattr(current, "TRIP", TripPlaces(place, place, [airport], [], [airport]))
    return prices

def priced(leg: dict) -> dict:
    leg = {**leg, "kind": "timetable", "price_kind": "estimate", "price_eur": 10.0}
    price_with_tickets(leg, TRAVEL, False)
    return leg

# ---------------------------------------------------------------- confirmed by the operator
def test_an_operator_that_sells_the_departure_on_the_day_confirms_it(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    leg = priced(connection(600, 660, step("Rail", "10:00", "11:00")))
    assert leg["grade"] == "A" and leg["confirmed_by"] == ["Rail"] and leg["price_kind"] == "operator"

def test_sold_only_for_the_day_the_timetable_was_read_it_stays_typical(sells):
    sells[("Rail", SERVICE, "10:00")] = 20.0
    leg = priced(connection(600, 660, step("Rail", "10:00", "11:00")))
    assert leg["grade"] == "B" and "confirmed_by" not in leg and leg["price_kind"] == "operator_typical"

def test_every_vehicle_must_be_confirmed(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    leg = priced(connection(600, 720, step("Rail", "10:00", "11:00"), step("Nobody", "11:10", "12:00", rail=False)))
    assert leg["grade"] == "B" and "confirmed_by" not in leg

def test_a_published_tariff_confirms_nothing(sells):
    sells[("City", TRAVEL, "10:00")] = 2.0
    leg = priced(connection(600, 640, step("City", "10:00", "10:40")))
    assert leg["grade"] == "B" and leg["price_kind"] == "tariff"

# ---------------------------------------------------------------- chosen on real prices
def landing(*options: dict) -> tuple:
    flight = {"id": "X", "date": TRAVEL, "arrival_airport": "XXX", "arrival_time": "09:00"}
    return flight, {"X": {"from_airport": {"covered": True, "service_day": SERVICE, "grade": "B", "options": list(options)}}}

def test_the_real_price_decides_between_two_connections(sells):
    """By the distance a coach is the cheaper way. Here the train is in fact a third of the coach's price."""
    sells[("Rail", TRAVEL, "10:00")] = 9.0
    sells[("Coach", TRAVEL, "10:05")] = 30.0
    train = connection(600, 690, step("Rail", "10:00", "11:30"))
    coach = connection(605, 690, step("Coach", "10:05", "11:30", rail=False))
    flight, ground = landing(train, coach)
    by_estimate = from_airport_leg(flight, ground, None)
    assert by_estimate["steps"][0]["agency"] == "Coach" and by_estimate["price_kind"] == "estimate"
    chosen = from_airport_leg(flight, ground, None, priced=True)
    assert chosen["steps"][0]["agency"] == "Rail" and chosen["price_eur"] == 9.0
    assert chosen["grade"] == "A" and chosen["other"]["steps"][0]["agency"] == "Coach"

def test_without_any_operator_the_choice_is_the_one_by_estimate(sells):
    first = connection(600, 690, step("Nobody", "10:00", "11:30"))
    second = connection(660, 750, step("Nobody", "11:00", "12:30"))
    flight, ground = landing(first, second)
    assert from_airport_leg(flight, ground, None, priced=True)["leave"] == from_airport_leg(flight, ground, None)["leave"] == 600

def test_towards_the_airport_too(sells):
    """Both reach the airport in time. The later one would win on its guessed price, the earlier one is in fact cheaper."""
    sells[("Rail", TRAVEL, "11:00")] = 60.0
    sells[("Coach", TRAVEL, "10:30")] = 8.0
    train = connection(660, 750, step("Rail", "11:00", "12:30"))
    coach = connection(630, 740, step("Coach", "10:30", "12:20", rail=False))
    flight = {"id": "X", "date": TRAVEL, "departure_airport": "XXX", "departure_time": "15:00"}
    ground = {"X": {"to_airport": {"covered": True, "service_day": SERVICE, "grade": "B", "options": [train, coach]}}}
    current.SETTINGS.earliest_leave, current.SETTINGS.latest_leave = "00:00", "23:59"
    assert to_airport_leg(flight, ground, None)["steps"][0]["agency"] == "Rail"
    chosen = to_airport_leg(flight, ground, None, priced=True)
    assert chosen["steps"][0]["agency"] == "Coach" and chosen["grade"] == "A" and chosen["confirmed_by"] == ["Coach"]
