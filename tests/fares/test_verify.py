"""python main.py --verify on small made-up inputs: no service, no checkpoint."""
import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest

from trip_planner.tools import verify
from trip_planner.sources import fares
from trip_planner.sources.fares import FareSource, NotRunning, Trip
from trip_planner.sources.timetables import local_to_utc

SOON = (date.today() + timedelta(days=20)).isoformat()
PAST = (date.today() - timedelta(days=2)).isoformat()

def step(time: str, arrive: str, agency: str = "Coach") -> dict:
    return {"time": time, "arrive": arrive, "line": "X", "agency": agency, "from": "A", "to": "B", "rail": False, "coach": True,
            "mode": "COACH", "km": 100, "from_id": "", "to_id": "", "from_at": [], "to_at": []}

def test_vehicle_keeps_own_day():
    day = date.fromisoformat(SOON)
    after_midnight = verify.vehicles({"leave": 23 * 60, "steps": [step("23:00", "23:40"), step("00:10", "01:00")]}, SOON)
    assert [vehicle["day"] for vehicle in after_midnight] == [SOON, (day + timedelta(days=1)).isoformat()]
    evening_before = verify.vehicles({"leave": -60, "steps": [step("23:00", "23:50"), step("00:30", "01:30")]}, SOON)
    assert [vehicle["day"] for vehicle in evening_before] == [(day - timedelta(days=1)).isoformat(), SOON]

class NoTimetable:
    """The published timetables, which these tests must not need."""
    async def get(self, *args):
        pytest.fail("the timetables were asked")
    async def zone(self, *args):
        pytest.fail("the timetables were asked")

@pytest.fixture
def coach(monkeypatch):
    """A coach operator that runs, on its days, only the departures in the returned list."""
    running: list = []

    def fare(trip: Trip) -> float | None:
        if trip.leave not in running:
            raise NotRunning
        return 15.0

    monkeypatch.setattr(fares, "FARE_SOURCES", [FareSource("Coach", "XX", lambda s: s["agency"] == "Coach", fare, confirms=True)])
    monkeypatch.setattr(fares, "CACHE", {})
    monkeypatch.setattr(fares, "NOT_RUNNING", set())
    return running

def checked(found: list) -> list:
    asyncio.run(verify.check_vehicles(found, NoTimetable()))
    return [vehicle.get("outcome") for vehicle in found]

def test_operator_says_vehicle_ran(coach):
    coach.append("08:00")
    found = verify.vehicles({"leave": 8 * 60, "steps": [step("08:00", "09:00"), step("10:00", "11:00")]}, SOON)
    assert checked(found) == ["ran", "did_not_run"]
    assert found[0]["how"] == "Coach"

def test_past_day_not_asked(coach):
    found = verify.vehicles({"leave": 8 * 60, "steps": [step("08:00", "09:00")]}, PAST)
    assert checked(found) == [None]

def test_settled_answer_kept(coach):
    found = verify.vehicles({"leave": 8 * 60, "steps": [step("08:00", "09:00")]}, SOON)
    found[0]["outcome"] = "ran"
    coach.clear()   # it would now say it does not run
    assert checked(found) == ["ran"]

def test_far_timetable_unpublished():
    vehicle = verify.vehicles({"leave": 8 * 60, "steps": [step("08:00", "09:00", agency="Local bus") | {"from_id": "x", "to_id": "y", "from_at": [1, 2]}]}, SOON)[0]
    assert asyncio.run(verify.in_timetable(vehicle, NoTimetable())) is None   # 20 days ahead: not asked

def test_count_by_light_source():
    vehicle = lambda outcome, how: {"day": SOON, "time": "08:00", "line": "X", "from": "A", "to": "B", "agency": "", "rail": False,
                                    "from_id": "", "to_id": "", "outcome": outcome, "how": how}
    kept = {"trip": "A -> B", "options": [{"number": 1, "flight": "F", "legs": [
        {"side": "to", "grade": "B", "kind": "timetable", "day": SOON, "vehicles": [vehicle("ran", "FlixBus"), vehicle("did_not_run", "FlixBus")]},
        {"side": "from", "grade": "A", "kind": "timetable", "day": SOON, "vehicles": [vehicle(None, None)]}]}]}
    text = verify.summary({Path("trip_a_b.json"): kept})
    assert "Did not run as shown:" in text and "trip_a_b.json, option 1" in text
    row = next(line for line in text.splitlines() if line.startswith("🟢 B"))
    assert row.split()[2:] == ["1", "1", "0", "0"]

class Timetable:
    """The timetable of one day, with one ride between the two stops at a given time (UTC)."""
    def __init__(self, leaves_utc: str):
        self.leaves_utc = leaves_utc
    async def zone(self, *args):
        return "Europe/Brussels"
    async def get(self, path, params):
        day = params["time"][:10]
        return {"itineraries": [{"legs": [{"mode": "WALK"}, {"mode": "REGIONAL_RAIL", "startTime": f"{day}T{self.leaves_utc}:00Z"}]}]}

def test_same_day_timetable_decides():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()   # within the week: published
    train = step("10:19", "10:50", agency="SNCB") | {"from_id": "x", "to_id": "y", "from_at": [51.2, 4.4], "mode": "REGIONAL_RAIL"}
    vehicle = verify.vehicles({"leave": 10 * 60, "steps": [train]}, tomorrow)[0]
    utc = lambda local: local_to_utc(tomorrow, int(local[:2]) * 60 + int(local[3:]), "Europe/Brussels")[11:16]
    assert asyncio.run(verify.in_timetable(vehicle, Timetable(utc("10:19")))) == "ran"
    assert asyncio.run(verify.in_timetable(vehicle, Timetable(utc("09:49")))) == "not_in_timetable"
