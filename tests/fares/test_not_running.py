"""An operator's answer for the travel date that leaves out a departure, and prices that are partly a guess, on small
made-up inputs: no service, no checkpoint."""
from datetime import date, timedelta

import pytest

from trip_planner.itineraries import legs
from trip_planner.itineraries.legs import best_on_real_prices, Rejected
from trip_planner.itineraries.ranking import price_range, tied_on_price
from trip_planner.sources import fares
from trip_planner.sources.fares import FareSource, NotRunning, Trip

TRAVEL = (date.today() + timedelta(days=70)).isoformat()
SERVICE = (date.today() + timedelta(days=3)).isoformat()

def ride(leave: str, arrive: str, status: str = "available", day: str = TRAVEL) -> dict:
    return {"departure": {"date": f"{day}T{leave}:00+01:00", "station_id": "a"}, "arrival": {"date": f"{day}T{arrive}:00+01:00", "station_id": "b"},
            "status": status, "price": {"total": 9.99}}

def flixbus_trip(leave: str = "00:55", arrive: str = "03:05") -> Trip:
    return Trip("Coimbra", "Lisbon (Oriente)", "eu-flixbus_a", "eu-flixbus_b", (), (), TRAVEL, leave, arrive)

@pytest.fixture
def flixbus(monkeypatch):
    """FlixBus answering from a list of rides instead of the network."""
    rides: list = []
    monkeypatch.setattr(fares, "flixbus_search", lambda *args, **options: (rides, {}))
    return rides

def test_flixbus_selling_that_departure_gives_its_price(flixbus):
    flixbus += [ride("00:20", "02:30"), ride("00:55", "03:05")]
    assert fares.flixbus_fare(flixbus_trip()) == 9.99

def test_flixbus_running_other_rides_that_day_says_this_one_does_not_run(flixbus):
    flixbus += [ride("00:20", "02:30"), ride("01:45", "04:10")]
    with pytest.raises(NotRunning):
        fares.flixbus_fare(flixbus_trip())

def test_flixbus_with_no_ride_that_day_says_nothing(flixbus):
    assert fares.flixbus_fare(flixbus_trip()) is None   # not on sale yet, or no ride at all: nothing is known

def test_a_sold_out_ride_still_runs(flixbus):
    flixbus += [ride("00:20", "02:30"), ride("00:55", "03:05", status="full")]
    assert fares.flixbus_fare(flixbus_trip()) is None   # no price, but it is not said not to run

def step(time: str, arrive: str) -> dict:
    return {"time": time, "arrive": arrive, "line": "X", "agency": "Coach", "from": "A", "to": "B", "rail": False, "coach": True,
            "km": 200, "from_id": "", "to_id": "", "from_at": [], "to_at": []}

def candidate(time: str, arrive: str, price: float) -> dict:
    return {"kind": "timetable", "steps": [step(time, arrive)], "service_day": SERVICE, "price_kind": "estimate", "price_eur": price,
            "leave": int(time[:2]) * 60 + int(time[3:]), "arrive": int(arrive[:2]) * 60 + int(arrive[3:]), "grade": "B"}

@pytest.fixture
def runs_only(monkeypatch):
    """A coach operator that runs, on the travel date, only the departures in the returned list."""
    running: list = []

    def fare(trip: Trip) -> float | None:
        if trip.day != TRAVEL:
            return None
        if trip.leave not in running:
            raise NotRunning
        return 12.0

    monkeypatch.setattr(fares, "FARE_SOURCES", [FareSource("Coach", "XX", lambda s: s["agency"] == "Coach", fare, confirms=True)])
    monkeypatch.setattr(fares, "CACHE", {})
    monkeypatch.setattr(fares, "NOT_RUNNING", set())
    return running

def test_a_connection_that_does_not_run_that_day_gives_way_to_the_next(runs_only):
    runs_only.append("09:00")
    early, later = candidate("07:00", "09:00", 10.0), candidate("09:00", "11:00", 11.0)
    best = best_on_real_prices([early, later], lambda c: c["price_eur"], TRAVEL, False)
    assert best is later and best["grade"] == "A"
    assert early["not_running"] == ["Coach"]

def test_no_connection_running_that_day_rejects_the_flight(runs_only):
    with pytest.raises(Rejected, match="Coach"):
        best_on_real_prices([candidate("07:00", "09:00", 10.0)], lambda c: c["price_eur"], TRAVEL, False)

def test_a_leg_across_midnight_is_not_judged_on_one_day(runs_only):
    night = candidate("23:30", "01:10", 10.0) | {"arrive": 24 * 60 + 70}
    legs.price_with_tickets(night, TRAVEL, False)
    assert "not_running" not in night   # its vehicles after midnight run on the next day

def itinerary(total: float, guessed: float) -> dict:
    return {"total_cost": total, "guessed_eur": guessed}

def test_a_guessed_price_may_be_half_or_double(settings):
    settings.unverified_price_factor = 2.0
    assert price_range(itinerary(78, 38)) == (59, 116)
    assert price_range(itinerary(83, 0)) == (83, 83)

def test_options_whose_guesses_overlap_the_cheapest_are_tied(settings):
    settings.unverified_price_factor = 2.0
    # The cheapest may really cost up to 116: 82 with 6 guessed may cost 79, 160 with 20 guessed no less than 150
    options = [itinerary(78, 38), itinerary(82, 6), itinerary(150, 0), itinerary(160, 20)]
    assert tied_on_price(options) == [1, 2]

def test_a_cheapest_option_that_stands_out_is_not_tied(settings):
    settings.unverified_price_factor = 2.0
    assert tied_on_price([itinerary(50, 0), itinerary(90, 10)]) == []
