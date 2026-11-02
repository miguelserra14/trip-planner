"""A search from the browser: the form and its token (site.py), the process and what it says (search.py, job.py).
No network, and no model asked: the planner is stood in for, or the search is refused before it starts."""
import io
import json
import subprocess
import sys
import time
from datetime import date, timedelta
from urllib.parse import urlencode

from app import job, search
from app.site import Site
from settings import SETTINGS
from trip_planner import utils

DAY = f"{date.today() + timedelta(days=30):%d/%m/%Y}"

class StandIn:
    """A search that runs until it is told it ended."""

    def __init__(self, asked):
        self.asked, self.running, self.lines, self.shown, self.reports = asked, True, [], [], []
        self.failure, self.stopped = "", False

    def stop(self):
        self.running, self.stopped = False, True

def site_and_searches():
    started = []
    site = Site(SETTINGS, start=lambda asked: started.append(StandIn(asked)) or started[-1])
    return site, started

def form(site, **fields):
    return urlencode({"token": site.token, "origin": "Coimbra", "destination": "Antwerp", "dates": DAY, **fields}).encode()

def wait_for(found: search.Job) -> search.Job:
    found.reader.join(timeout=60)
    assert not found.running
    return found

def test_a_search_without_the_token_of_the_page_is_not_started():
    site, started = site_and_searches()
    for body in (form(site, token="another"), form(site, token=""), b"origin=Coimbra"):
        assert site.post("/search", body).status == 403
    assert started == [] and site.token in site.get("/").html

def test_a_setting_that_is_not_valid_is_answered_on_the_form_and_nothing_starts():
    site, started = site_and_searches()
    reply = site.post("/search", form(site, destination="Coimbra"))
    assert reply.status == 400 and 'role="alert"' in reply.html and 'value="Coimbra"' in reply.html and started == []

def test_one_search_runs_at_a_time_and_can_be_stopped():
    site, started = site_and_searches()
    assert site.post("/search", form(site)).location == "/search"
    assert site.post("/search", form(site, destination="Ghent")).location == "/search"
    assert [found.asked["destination"] for found in started] == ["Antwerp"]
    assert site.post("/stop", form(site)).location == "/search" and started[0].stopped
    site.post("/search", form(site, destination="Ghent"))
    assert len(started) == 2

def test_the_page_of_a_running_search_refreshes_itself_until_it_ends():
    site, started = site_and_searches()
    assert site.get("/search").location == "/"   # no search yet
    site.post("/search", form(site))
    started[0].lines.append("Looking up the flights")
    running = site.get("/search").html
    assert 'http-equiv="refresh"' in running and "Looking up the flights" in running and site.token in running
    started[0].running = False
    assert 'http-equiv="refresh"' not in site.get("/search").html

def test_what_the_process_says_is_read_line_by_line():
    said = [{"kind": "say", "text": "Looking up the flights"}, {"kind": "show", "text": "# Final result"},
            {"kind": "report", "text": "reports/trip_x/trip_x.md"}]
    script = ";".join(f"print({json.dumps(json.dumps(event))})" for event in said) + ";print('a warning of a library')"
    found = wait_for(search.Job({"origin": "Coimbra", "destination": "Antwerp"}, [sys.executable, "-c", script]))
    assert found.lines == ["Looking up the flights", "a warning of a library"] and found.shown == ["# Final result"]
    assert [path.name for path in found.reports] == ["trip_x.md"] and found.failure == ""

def test_a_process_that_ends_badly_without_saying_why_is_a_failure():
    found = wait_for(search.Job({}, [sys.executable, "-c", "import sys; sys.exit(3)"]))
    assert "3" in found.failure

def test_a_stopped_search_is_not_a_failure():
    found = search.Job({}, [sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.2)
    found.stop()
    assert found.stopped and not found.running and found.failure == ""

def test_the_process_refuses_a_trip_that_is_not_valid_before_it_searches():
    asked = json.dumps({"origin": "Coimbra", "destination": "Antwerp", "dates": "01/01/2020"})
    done = subprocess.run(search.COMMAND, cwd=search.ROOT, input=asked, capture_output=True, encoding="utf-8", timeout=60)
    events = [json.loads(line) for line in done.stdout.splitlines()]
    assert done.returncode == 1 and [event["kind"] for event in events] == ["failed"] and "2020" in events[0]["text"]

def test_the_process_sends_the_progress_the_result_and_the_report(monkeypatch, tmp_path):
    from trip_planner import planner
    from trip_planner.sources import flight_search

    async def plan_trip(settings):
        assert settings.origin == "Coimbra" and settings.include_airports == [] and not settings.confirm_airports
        utils.say("Looking up the flights")
        utils.show("# Final result")
        print("printed by a library")
        planner.PLANNED.append({"report": tmp_path / "trip.md"})

    async def connect():
        pass
    monkeypatch.setattr(utils, "LISTENERS", {})
    monkeypatch.setattr(planner, "PLANNED", [])
    monkeypatch.setattr(planner, "plan_trip", plan_trip)
    monkeypatch.setattr(flight_search, "connect", connect)
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    assert job.main(json.dumps({"origin": "Coimbra", "destination": "Antwerp", "dates": DAY}), events) == 0
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [event["kind"] for event in said] == ["say", "show", "say", "report"]
    assert said[2]["text"] == "printed by a library" and said[3]["text"].endswith("trip.md")
