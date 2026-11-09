"""Discover in the app (maps.py, map_job.py): the form, the map in a process of its own, its page served with what it
may do, and a trip of the map opening the planner's form filled in. No network: the searches are stood in for, and
the map's process is run here with discover's Session stood in for."""
import io
import json
import re
import sys
import threading
from datetime import date, timedelta
from urllib.parse import urlencode

from app import job, maps
from app.tests.test_search import StandIn
from app.site import Site
from discover import finder
from discover.view import page
from discover.flights.sources import Days
from settings import SETTINGS
from trip_planner.texts import tr
from trip_planner.trip.places import Place

DAY = date.today() + timedelta(days=30)
WRITTEN = f"{DAY:%d/%m/%Y}"

def site_and_jobs():
    started = []
    site = Site(SETTINGS, start=lambda first: started.append(StandIn(first)) or started[-1], stale=list)
    return site, started

def form(site, **changes) -> bytes:
    return urlencode({"token": site.token, "city": "Coimbra", "dates": WRITTEN, "source": "light", "look_up": "on", **changes}).encode()

def test_the_form_starts_as_the_window_does_and_a_map_starts_in_a_process_of_its_own():
    site, started = site_and_jobs()
    page_html = site.get("/discover").html
    assert 'name="city" value="' + SETTINGS.origin + '"' in page_html and 'value="light" checked' in page_html
    back = f"{DAY + timedelta(days=3):%d/%m/%Y}"
    assert site.post("/discover", form(site, return_dates=back)).location == "/discover"
    asked = started[0].first
    assert asked["task"] == "map" and asked["city"] == "Coimbra" and asked["look_up"] is True
    assert asked["planner_link"] == "/discover/map/1/plan/" and asked["dates"] == WRITTEN and asked["return_dates"] == back

def test_a_city_or_days_that_cannot_be_used_are_answered_on_the_form_and_nothing_starts():
    site, started = site_and_jobs()
    for changes in ({"city": ""}, {"dates": "01/01/2020"}, {"dates": "32/13/2026"},
                    {"return_dates": f"{DAY - timedelta(days=3):%d/%m/%Y}"}):
        reply = site.post("/discover", form(site, **changes))
        assert reply.status == 400 and 'role="alert"' in reply.html
    assert started == []

def test_a_map_and_a_search_run_at_the_same_time_and_a_new_map_waits_for_the_last_to_stop():
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    assert site.post("/search", urlencode({"token": site.token, "origin": "Coimbra", "destination": "Antwerp",
                                           "dates": WRITTEN}).encode()).location == "/search"
    assert [job_started.first.get("task", "search") for job_started in started] == ["map", "search"]
    site.post("/discover", form(site, city="Lisbon"))
    site.post("/discover", form(site, city="Porto"))   # asked again while the first stops: the form as it is now
    assert started[0].answers == [{"stop": True}] and started[0].alive and len(started) == 2   # asked, never killed
    assert tr("discover_win_stopping") in site.get("/discover").html and len(started) == 2
    started[0].alive = False   # it saved what it found and ended
    site.get("/discover/part")
    assert not started[1].stopped and [job_started.first.get("city") for job_started in started[2:]] == ["Porto"]
    assert started[2].map_link == "/discover/map/2/" and started[2].first["planner_link"] == "/discover/map/2/plan/"

def test_a_map_that_does_not_stop_in_time_is_killed(monkeypatch):
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    site.post("/discover/stop", form(site))
    assert started[0].answers == [{"stop": True}] and started[0].alive
    monkeypatch.setattr(maps, "STOP_SECONDS", -1)
    site.get("/discover")
    assert not started[0].alive and len(started) == 1   # stopped, and no map asked for next

def test_closing_the_program_gives_the_map_a_while_to_save_what_it_found(monkeypatch):
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    monkeypatch.setattr(maps, "STOP_SECONDS", 0.5)
    site.close()
    assert started[0].answers == [{"stop": True}] and not started[0].alive

def test_nothing_of_discover_changes_without_the_token():
    site, started = site_and_jobs()
    for path in ("/discover", "/discover/stop"):
        assert site.post(path, form(site, token="another")).status == 403
    assert started == []

def test_the_map_is_served_with_what_it_may_do_and_the_apps_pages_keep_theirs():
    site, started = site_and_jobs()
    assert site.get("/discover/map/1/").location == "/discover"   # no map yet
    site.post("/discover", form(site))
    assert site.get("/discover/map/1/").location == "/discover"   # its flights are still being searched
    started[0].read_event("map", "<!doctype html><title>map</title>")
    started[0].read_event("state", {"version": 3, "running": True})
    shown = site.get("/discover/map/1/")
    assert shown.html.startswith("<!doctype html>") and shown.policy == maps.MAP_POLICY
    assert shown.referrer == "strict-origin-when-cross-origin" and site.get("/").referrer == "no-referrer"   # the tiles
    assert "connect-src 'self'" in maps.MAP_POLICY and "unsafe" not in maps.MAP_POLICY
    script = site.get("/discover/map/1/map.js")
    assert script.status == 200 and script.kind.startswith("text/javascript") and "JSON.parse" in script.html
    assert site.get("/discover/map/1/map.css").kind.startswith("text/css") and site.get("/discover/map/1/page.py").status == 404
    assert json.loads(site.get("/discover/map/1/state").html) == {"version": 3, "running": True}
    assert site.get("/discover").policy == "" and 'href="/discover/map/1/"' in site.get("/discover/part").html

def test_a_map_keeps_the_script_it_was_made_with_when_the_program_changes_while_the_app_is_open(monkeypatch, tmp_path):
    # Its page holds the texts of its time: a newer script would ask for texts it has not, and show their keys
    script = tmp_path / "map.js"
    script.write_text("// the script of then", encoding="utf-8")
    monkeypatch.setitem(maps.FILES, "map.js", (script, "text/javascript; charset=utf-8"))
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    started[0].read_event("map", "<!doctype html><title>map</title>")
    script.write_text("// the script of now", encoding="utf-8")
    assert site.get("/discover/map/1/map.js").html == "// the script of then"

def test_a_trip_of_the_map_fills_the_planners_form_by_its_key_and_searches_nothing():
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    started[0].read_event("trips", {"BCN-out": ["Coimbra", "Barcelona", WRITTEN]})
    assert site.get("/discover/map/1/plan/BCN-out").location == "/"
    home = site.get("/").html
    assert 'name="destination" value="Barcelona"' in home and f'name="dates" value="{WRITTEN}"' in home
    assert len(started) == 1   # only the map: the traveller searches when ready
    for path in ("/discover/map/1/plan/XXX-out", "/discover/map/1/plan/..%2F..%2Fsettings", "/discover/map/1/plan/",
                 "/discover/map/2/plan/BCN-out", "/discover/map/x/plan/BCN-out", "/discover/map/1/elsewhere"):
        assert site.get(path).status == 404

def test_the_page_of_a_map_another_one_replaced_keeps_its_own_destinations_and_trips_never_the_others():
    site, started = site_and_jobs()
    for number, city in enumerate(["Coimbra", "Porto"] + ["Faro"] * (maps.MAPS_KEPT - 1)):
        site.post("/discover", form(site, city=city))
        started[number].read_event("trips", {"BCN-out": [city, "Barcelona", WRITTEN]})
        started[number].read_event("state", {"version": number, "running": False})
        started[number].alive = False
    # The second map's page, still open in a tab, asks at its own address: what that map found
    assert json.loads(site.get("/discover/map/2/state").html)["version"] == 1
    assert site.get("/discover/map/2/plan/BCN-out").location == "/" and 'value="Porto"' in site.get("/").html
    # The first is one too many to keep: its page says the map is gone
    assert site.get("/discover/map/1/state").status == 404 and site.get("/discover/map/1/plan/BCN-out").status == 404

def test_the_page_opens_the_planner_in_the_app_and_asks_for_its_destinations_where_it_is():
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43), Days("2026-11-12", "2026-11-19"), [], [], [], [])
    html = page.map_page(found, "Ryanair", [], planner_link="/discover/map/1/plan/", running=True)
    data = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', html).group(1))
    assert data["planner_link"] == "/discover/map/1/plan/"
    assert '<script src="map.js"></script>' in html and "<style>" not in html   # files beside the page: no script of its own
    script = page.SCRIPT.read_text(encoding="utf-8")
    assert 'fetch("state")' in script and 'fetch("/state")' not in script   # relative: under the map's address in the app

def test_the_maps_process_tells_the_app_the_page_the_trips_and_each_change(monkeypatch):
    import discover.view.page as discover_page
    import discover.session as discover_session
    import trip_planner.trip.places as places

    class Session:
        def __init__(self, city, days, fast, light, max_stops, ground_only):
            assert city == "Coimbra" and days.first == DAY.isoformat() and light and not fast
            self.found = type("Found", (), {"kiwi_why": ""})()
            self.live, self.lock, self.sources, self.sections, self.path = {"state": {}}, threading.Lock(), "Ryanair", [], "discover/maps/x.html"
            self.stopping = threading.Event()

        def make(self):
            pass

        def look_up(self, timetables):
            assert timetables
            with self.lock:
                self.live["state"] = {"version": 2, "running": True}
            import time
            time.sleep(1.5)   # the app is told of the change while the lookups go on

    monkeypatch.setattr(discover_session, "Session", Session)
    monkeypatch.setattr(discover_page, "map_page", lambda *arguments, planner_link="", **more: f"<page link={planner_link}>")
    monkeypatch.setattr(discover_page, "map_state", lambda *arguments, **more: {"version": 1, "running": True})
    monkeypatch.setattr(discover_page, "trips", lambda found, airports: {"BCN-out": ["Coimbra", "Barcelona", WRITTEN]})
    monkeypatch.setattr(places, "load_airports", dict)
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    asked = {"task": "map", "language": "en", "planner_link": "/discover/map/1/plan/", "city": "Coimbra", "dates": WRITTEN,
             "return_dates": "", "source": "light", "look_up": True}
    assert job.main(iter([json.dumps(asked)]), events) == 0
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    kinds = [event["kind"] for event in said]
    assert kinds[:3] == ["trips", "map", "state"] and kinds[-1] == "done" and kinds.count("state") >= 3
    assert said[1]["text"] == "<page link=/discover/map/1/plan/>" and said[-2]["text"] == {"version": 2, "running": True}

def test_a_map_told_to_stop_ends_its_lookups_at_the_next_step(monkeypatch):
    import discover.view.page as discover_page
    import discover.session as discover_session
    import trip_planner.trip.places as places

    class Session:
        def __init__(self, city, days, fast, light, max_stops, ground_only):
            self.found = type("Found", (), {"kiwi_why": ""})()
            self.live, self.lock, self.sources, self.sections, self.path = {"state": {}}, threading.Lock(), "Ryanair", [], "x.html"
            self.stopping = threading.Event()

        def make(self):
            pass

        def look_up(self, timetables):
            assert self.stopping.wait(5), "the map was not told to stop"

    def lines():
        yield json.dumps({"task": "map", "language": "en", "city": "Coimbra", "dates": WRITTEN, "look_up": True})
        yield json.dumps({"question": "not for a map"})   # read and passed by
        yield json.dumps({"stop": True})
        threading.Event().wait()   # stdin stays open: only the line stops it
    monkeypatch.setattr(discover_session, "Session", Session)
    monkeypatch.setattr(discover_page, "map_page", lambda *arguments, **more: "<page>")
    monkeypatch.setattr(discover_page, "map_state", lambda *arguments, **more: {})
    monkeypatch.setattr(discover_page, "trips", lambda found, airports: {})
    monkeypatch.setattr(places, "load_airports", dict)
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    assert job.main(lines(), events) == 0
    assert json.loads(out.getvalue().splitlines()[-1])["kind"] == "done"

def test_days_the_map_cannot_use_are_refused_by_its_process_too(monkeypatch):
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    job.main(iter([json.dumps({"task": "map", "language": "en", "city": "Coimbra", "dates": "01/01/2020"})]), events)
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [event["kind"] for event in said] == ["failed"]

def test_a_map_being_made_shows_its_steps_on_a_map_and_one_button_that_stops_it():
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    made = started[0]
    made.read_event("progress", {"trip": {"discover": True, "source": "light", "ground_only": False, "departure": [], "arrival": [],
                                          "origin": {"name": "Coimbra", "lat": 40.21, "lon": -8.43}}})
    made.read_event("progress", {"step": "discover_airports"})
    made.read_event("progress", {"found": "discover_airports", "count": 2, "departure": [{"code": "OPO", "lat": 41.24, "lon": -8.68},
                                                                                          {"code": "LIS", "lat": 38.77, "lon": -9.13}]})
    made.read_event("progress", {"step": "discover_flights"})
    shown = site.get("/discover").html
    assert 'class="card waiting"' in shown and "Finding destinations from Coimbra" in shown and "2 airports" in shown
    assert shown.count('class="airport"') == 2 and shown.count(f'>{tr("app_stop")}</button>') == 1
    assert 'action="/discover/stop"' in shown and 'action="/stop"' not in shown   # the map's own, not the planner's
    made.read_event("progress", {"step": "done"})
    assert 'class="card waiting"' not in site.get("/discover").html   # its page is ready: the map opens
