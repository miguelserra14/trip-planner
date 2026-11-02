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
from discover import finder, page
from discover.sources import Days
from settings import SETTINGS
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
    assert asked["planner_link"] == maps.PLANNER_LINK and asked["dates"] == WRITTEN and asked["return_dates"] == back

def test_a_city_or_days_that_cannot_be_used_are_answered_on_the_form_and_nothing_starts():
    site, started = site_and_jobs()
    for changes in ({"city": ""}, {"dates": "01/01/2020"}, {"dates": "32/13/2026"},
                    {"return_dates": f"{DAY - timedelta(days=3):%d/%m/%Y}"}):
        reply = site.post("/discover", form(site, **changes))
        assert reply.status == 400 and 'role="alert"' in reply.html
    assert started == []

def test_a_map_and_a_search_run_at_the_same_time_and_a_new_map_replaces_the_last():
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    assert site.post("/search", urlencode({"token": site.token, "origin": "Coimbra", "destination": "Antwerp",
                                           "dates": WRITTEN}).encode()).location == "/search"
    assert [job_started.first.get("task", "search") for job_started in started] == ["map", "search"]
    site.post("/discover", form(site, city="Porto"))
    assert started[0].stopped and not started[1].stopped and started[2].first["city"] == "Porto"

def test_nothing_of_discover_changes_without_the_token():
    site, started = site_and_jobs()
    for path in ("/discover", "/discover/stop"):
        assert site.post(path, form(site, token="another")).status == 403
    assert started == []

def test_the_map_is_served_with_what_it_may_do_and_the_apps_pages_keep_theirs():
    site, started = site_and_jobs()
    assert site.get("/discover/map").location == "/discover"   # no map yet
    site.post("/discover", form(site))
    assert site.get("/discover/map").location == "/discover"   # its flights are still being searched
    started[0].read_event("map", "<!doctype html><title>map</title>")
    started[0].read_event("state", {"version": 3, "running": True})
    shown = site.get("/discover/map")
    assert shown.html.startswith("<!doctype html>") and shown.policy == maps.MAP_POLICY
    assert shown.referrer == "strict-origin-when-cross-origin" and site.get("/").referrer == "no-referrer"   # the tiles
    assert "connect-src 'self'" in maps.MAP_POLICY and "unsafe-eval" not in maps.MAP_POLICY
    assert json.loads(site.get("/discover/state").html) == {"version": 3, "running": True}
    assert site.get("/discover").policy == "" and 'href="/discover/map"' in site.get("/discover/part").html

def test_a_trip_of_the_map_fills_the_planners_form_by_its_key_and_searches_nothing():
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    started[0].read_event("trips", {"BCN-out": ["Coimbra", "Barcelona", WRITTEN]})
    assert site.get("/discover/plan/BCN-out").location == "/"
    home = site.get("/").html
    assert 'name="destination" value="Barcelona"' in home and f'name="dates" value="{WRITTEN}"' in home
    assert len(started) == 1   # only the map: the traveller searches when ready
    for key in ("XXX-out", "..%2F..%2Fsettings", ""):
        assert site.get(f"/discover/plan/{key}").status == 404

def test_the_page_opens_the_planner_in_the_app_and_asks_for_its_destinations_where_it_is():
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43), Days("2026-11-12", "2026-11-19"), [], [], [], [])
    html = page.map_page(found, "Ryanair", [], planner_link=maps.PLANNER_LINK, running=True)
    data = json.loads(re.search(r"const data = (.*);\n", html).group(1))
    assert data["planner_link"] == maps.PLANNER_LINK and data["token"] == ""
    assert 'fetch("state")' in html and 'fetch("/state")' not in html   # relative: /discover/state in the app, /state served alone

def test_the_maps_process_tells_the_app_the_page_the_trips_and_each_change(monkeypatch):
    import discover.page as discover_page
    import discover.plan as discover_plan
    import discover.session as discover_session
    import trip_planner.trip.places as places

    class Session:
        def __init__(self, city, days, fast, light, max_stops):
            assert city == "Coimbra" and days.first == DAY.isoformat() and light and not fast
            self.found = type("Found", (), {"kiwi_why": ""})()
            self.live, self.lock, self.sources, self.sections, self.path = {"state": {}}, threading.Lock(), "Ryanair", [], "discover/maps/x.html"

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
    monkeypatch.setattr(discover_plan, "trips", lambda found, airports: {"BCN-out": ["Coimbra", "Barcelona", WRITTEN]})
    monkeypatch.setattr(places, "load_airports", dict)
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    asked = {"task": "map", "language": "en", "planner_link": maps.PLANNER_LINK, "city": "Coimbra", "dates": WRITTEN,
             "return_dates": "", "source": "light", "look_up": True}
    assert job.main(iter([json.dumps(asked)]), events) == 0
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    kinds = [event["kind"] for event in said]
    assert kinds[:3] == ["trips", "map", "state"] and kinds[-1] == "done" and kinds.count("state") >= 3
    assert said[1]["text"] == f"<page link={maps.PLANNER_LINK}>" and said[-2]["text"] == {"version": 2, "running": True}

def test_days_the_map_cannot_use_are_refused_by_its_process_too(monkeypatch):
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    job.main(iter([json.dumps({"task": "map", "language": "en", "city": "Coimbra", "dates": "01/01/2020"})]), events)
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [event["kind"] for event in said] == ["failed"]
