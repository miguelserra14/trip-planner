"""A mistake of the program is not passed off as a rejected flight, and a run leaves a log. No network.

- A flight that does not fit is rejected with its reason. A flight the program fails on is left out too, so that
  the search goes on, but it is said, counted in the report and written in the log.
- Every request to a service is kept with how long it took and how it ended, and saved next to the report.
- The stops of an airport are asked for once and then kept."""
import asyncio
from datetime import date, timedelta

import pytest

from trip_planner import utils
from trip_planner.agents import steps
from trip_planner.itineraries.legs import Rejected
from trip_planner.report import full
from trip_planner.sources import timetables
from trip_planner.sources.timetables import airport_stops

FLIGHT = {"id": "F1", "price_eur": 50.0, "departure_airport": "OPO", "arrival_airport": "BRU"}

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(steps, "FAULTS", {})
    monkeypatch.setattr(utils, "REQUESTS", [])
    monkeypatch.setattr(steps, "REQUESTS", utils.REQUESTS)
    monkeypatch.setattr(full, "REQUESTS", utils.REQUESTS)

def failing_with(monkeypatch, error: Exception) -> None:
    def check(*arguments):
        raise error
    monkeypatch.setattr(steps, "check_itinerary", check)

# ---------------------------------------------------------------- rejections and mistakes
def test_a_flight_that_does_not_fit_is_rejected_with_its_reason(monkeypatch):
    failing_with(monkeypatch, Rejected("no connection reaches the airport in time"))
    assert steps.itinerary_or_reason(FLIGHT, {}, {}, {}) == (None, "no connection reaches the airport in time")
    assert steps.FAULTS == {} and utils.REQUESTS == []

def test_a_mistake_of_the_program_is_not_a_rejection(monkeypatch):
    failing_with(monkeypatch, KeyError("to_airport"))
    said = []
    monkeypatch.setitem(utils.LISTENERS, "say", said.append)
    itinerary, reason = steps.itinerary_or_reason(FLIGHT, {}, {}, {})
    assert itinerary is None and "KeyError" in reason
    assert steps.FAULTS == {"F1": "KeyError: 'to_airport'"} and "F1" in said[0]
    logged = utils.REQUESTS[0]
    assert logged["service"] == "program" and "check" in logged["what"] and utils.failed(logged)   # where it happened

def test_a_mistake_is_said_once_however_often_the_flight_is_looked_at(monkeypatch):
    failing_with(monkeypatch, TypeError("unsupported operand"))
    said = []
    monkeypatch.setitem(utils.LISTENERS, "say", said.append)
    for _ in range(3):   # the rounds, the ranking and the closest alternatives all look at the same flight
        steps.itinerary_or_reason(FLIGHT, {}, {}, {})
    assert len(said) == 1 and len(utils.REQUESTS) == 1

def test_the_report_counts_the_flights_the_program_failed_on(monkeypatch):
    failing_with(monkeypatch, KeyError("to_airport"))
    monkeypatch.setitem(utils.LISTENERS, "say", lambda text: None)
    found = steps.build_itineraries({"flights": [FLIGHT], "checked": ["F1"], "ground": {}, "transfers": [], "trains": []})
    assert found["recommended"] == 0 and "KeyError" in found["rejected"][0]
    assert any("1" in warning and "program" in warning for warning in found["warnings"])

# ---------------------------------------------------------------- the log of the run
def test_a_request_is_logged_with_how_it_ended():
    with utils.logged("Transitous", "plan a -> b") as entry:
        entry["result"] = "ok: 3 rides"
    with pytest.raises(TimeoutError):
        with utils.logged("FlixBus", "rides to OPO"):
            raise TimeoutError("no answer in 20 s")
    answered, timed_out = utils.REQUESTS
    assert not utils.failed(answered) and utils.failed(timed_out) and timed_out["result"].startswith("TimeoutError")
    assert answered["seconds"] >= 0 and timed_out["service"] == "FlixBus"

def test_a_request_given_up_on_is_logged_too():
    async def slow():
        with utils.logged("model", "coordinator"):
            await asyncio.sleep(5)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(slow(), 0.01))
    assert utils.failed(utils.REQUESTS[0])

def test_the_log_is_saved_with_the_report_and_goes_with_it(tmp_path, monkeypatch):
    monkeypatch.setattr(full, "REPORTS", tmp_path)
    monkeypatch.setattr(full, "SAVED_REPORTS", tmp_path / "saved")
    monkeypatch.setattr(full, "render_full_report", lambda response, recommendation: "the report")
    monkeypatch.setattr(full.current, "SETTINGS", type("Settings", (), {"origin": "Coimbra", "destination": "Antwerp", "recent_reports": 5}))
    with utils.logged("Transitous", "plan a -> b") as entry:
        entry["waited"] = 1.5
    report = full.save_report({}, "")
    log = full.log_of(report)
    assert "Transitous" in log.read_text(encoding="utf-8") and "plan a -> b" in log.read_text(encoding="utf-8")
    assert utils.REQUESTS == []   # the next run starts a new log
    kept = full.keep_report(report, True)
    assert full.log_of(kept).exists() and not log.exists()

def test_the_services_of_a_run_are_summed_up(settings):
    for seconds, result in ((0.2, "ok"), (6.0, "ok"), (1.0, "429")):
        utils.REQUESTS.append({"at": "", "service": "Transitous", "what": "plan", "seconds": seconds, "result": result})
    row = full.services_table()[-1]
    assert [cell.strip() for cell in row.strip("|").split("|")] == ["Transitous", "3", "1", "7", "6.0"]

# ---------------------------------------------------------------- the stops of an airport
class Asked(timetables.Timetables):
    def __init__(self):
        self.times = 0

    async def stops_around(self, airport, terminal):
        self.times += 1
        return [{"name": "Airport station", "stopId": "station", "lat": 41.24, "lon": -8.67, "modes": ["RAIL"]}]

AIRPORT = {"code": "XXX", "name": "Somewhere", "latitude": 41.24, "longitude": -8.67}
TERMINAL = [41.24, -8.67]

@pytest.fixture
def stops_file(tmp_path, monkeypatch):
    monkeypatch.setattr(airport_stops, "AIRPORT_STOPS_FILE", tmp_path / "airport_stops.json")
    monkeypatch.setattr(airport_stops, "AIRPORT_STOPS", {})

def test_the_stops_of_an_airport_are_asked_for_once(stops_file):
    transit = Asked()
    first = asyncio.run(transit.airport_stops(AIRPORT, TERMINAL))
    again = asyncio.run(transit.airport_stops(AIRPORT, TERMINAL))
    assert first == again == [("station", 5)] and transit.times == 1
    assert '"XXX"' in airport_stops.AIRPORT_STOPS_FILE.read_text(encoding="utf-8")

def test_old_stops_and_stops_of_another_terminal_are_asked_for_again(stops_file):
    transit = Asked()
    asyncio.run(transit.airport_stops(AIRPORT, TERMINAL))
    airport_stops.AIRPORT_STOPS["XXX"]["read"] = (date.today() - timedelta(days=airport_stops.AIRPORT_STOPS_DAYS + 1)).isoformat()
    asyncio.run(transit.airport_stops(AIRPORT, TERMINAL))
    assert transit.times == 2
    asyncio.run(transit.airport_stops(AIRPORT, [41.25, -8.66]))   # the terminal was located somewhere else since
    assert transit.times == 3
