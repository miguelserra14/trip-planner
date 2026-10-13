"""Flights that land on a later day than they leave: night flights and long ones. No network.

Everything at the destination is counted on the clock of the day the flight lands: the timetable that is read,
the arrival times the traveller asked for, and the tickets. Only the duration of the trip knows a night went by."""
import dataclasses
from datetime import date, timedelta

import pytest

from trip_planner import current
from trip_planner.config import TripSettings
from trip_planner.itineraries.ranking import check_itinerary
from trip_planner.places import Place, TripPlaces
from trip_planner.report.formatting import at_destination
from trip_planner.sources import kiwi
from trip_planner.sources.timetables import landing_day, Timetables

LEAVES = date.today() + timedelta(days=60)
LANDS = LEAVES + timedelta(days=1)

def found_by_search(departure: str, arrival: str, minutes: int) -> dict:
    """A flight from Lisbon to Rio as the flight search returns it, times as hh:mm on each airport's clock."""
    lands = LANDS if arrival < departure else LEAVES
    return {"price": 480, "bookingUrl": "https://example.org", "outbound": {
        "from": "LIS", "to": "GIG", "departureTime": f"{LEAVES}T{departure}", "arrivalTime": f"{lands}T{arrival}",
        "stops": 0, "route": ["LIS", "GIG"], "durationSeconds": minutes * 60,
        "segments": [{"carrier": "TP", "flightNumber": "TP75", "carrierName": "TAP Air Portugal"}]}}

NIGHT_FLIGHT = found_by_search("23:20", "06:30", 610)   # Lisbon is three hours ahead of Rio in winter

@pytest.fixture(autouse=True)
def trip(monkeypatch):
    lisbon = Place("Lisboa", "Lisboa", "Lisbon", "Portugal", 38.72, -9.14)
    rio = Place("Rio de Janeiro", "Rio de Janeiro", "Rio de Janeiro", "Brazil", -22.91, -43.21)
    airport = lambda code: {"code": code, "name": code, "city": code, "country": "", "large": True, "latitude": 0.0, "longitude": 0.0, "km": 10}
    monkeypatch.setattr(current, "SETTINGS", TripSettings(origin="Lisboa", destination="Rio de Janeiro", dates=[f"{LEAVES:%d/%m/%Y}"],
                                                          earliest_leave="", latest_leave=""))
    monkeypatch.setattr(current, "TRIP", TripPlaces(lisbon, rio, [airport("LIS")], [], [airport("GIG")]))
    monkeypatch.setattr(kiwi, "FLIGHTS", {})

def accept(**changes) -> None:
    current.SETTINGS = dataclasses.replace(current.SETTINGS, **changes)

def test_a_night_flight_is_kept_with_the_day_it_lands():
    flight = kiwi.register_flight(NIGHT_FLIGHT)
    assert flight["date"] == LEAVES.isoformat() and flight["arrival_date"] == LANDS.isoformat()
    assert flight["arrival_day_offset"] == 1 and landing_day(flight) == LANDS.isoformat()
    assert flight["timezone_shift_minutes"] == 180   # the difference between the two clocks, not a day and three hours

def test_a_flight_of_one_day_is_unchanged():
    flight = kiwi.register_flight(found_by_search("10:00", "17:10", 610))
    assert flight["arrival_day_offset"] == 0 and landing_day(flight) == flight["date"]
    assert flight["timezone_shift_minutes"] == 180

def test_it_is_left_out_when_the_traveller_does_not_accept_it():
    accept(next_day_arrivals=False)
    assert kiwi.flight_problems(NIGHT_FLIGHT["outbound"]) == ["next_day"]
    assert kiwi.register_flight(NIGHT_FLIGHT) is None
    assert kiwi.register_flight(NIGHT_FLIGHT, closest=True) is None   # not even among the closest alternatives

def test_the_arrival_time_asked_for_is_read_on_the_day_of_arrival():
    accept(latest_arrival="06:00")
    assert kiwi.flight_problems(NIGHT_FLIGHT["outbound"]) == ["too_late"]
    accept(latest_arrival="12:00")
    assert kiwi.flight_problems(NIGHT_FLIGHT["outbound"]) == []

def connection(leave: int, arrive: int, day: str) -> dict:
    step = {"time": "", "arrive": "", "line": "1", "agency": "", "from": "a", "to": "b", "rail": False, "coach": False, "km": 10}
    return {"covered": True, "service_day": day, "grade": "A", "options": [
        {"leave": leave, "arrive": arrive, "steps": [step], "urls": [], "grade": "A", "source": "timetable", "service_day": day, "price_eur": None}]}

def test_the_duration_counts_the_night():
    flight = kiwi.register_flight(NIGHT_FLIGHT)
    ground = {flight["id"]: {"to_airport": connection(19 * 60, 20 * 60, flight["date"]),             # leaves Lisbon at 19:00
                             "from_airport": connection(7 * 60 + 30, 8 * 60 + 30, flight["arrival_date"])}}   # in Rio at 08:30
    itinerary = check_itinerary(flight, ground, None, None)
    assert itinerary["leave_time"] == "19:00" and itinerary["arrival_time"] == "08:30"
    assert itinerary["total_minutes"] == 16 * 60 + 30   # 08:30 in Rio is 11:30 in Lisbon, the day after
    assert itinerary["wait_after_landing"] == 60 and itinerary["breaches"] == []

def test_the_timetable_of_the_day_of_arrival_is_confirmed_by_that_day():
    flight = kiwi.register_flight(NIGHT_FLIGHT)
    timetables = Timetables()
    assert timetables.answer([], flight["arrival_date"], landing_day(flight))["grade"] == "A"
    assert timetables.answer([], flight["date"], landing_day(flight))["grade"] == "B"

def test_the_report_says_which_day():
    flight = kiwi.register_flight(NIGHT_FLIGHT)
    assert at_destination("06:30", flight) == f"06:30 ({LANDS:%d/%m})"
    assert at_destination("17:10", kiwi.register_flight(found_by_search("10:00", "17:10", 610))) == "17:10"
