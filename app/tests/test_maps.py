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

def test_map_runs_in_process():
    site, started = site_and_jobs()
    page_html = site.get("/discover").html
    assert 'name="city" value="' + SETTINGS.origin + '"' in page_html and 'value="light" checked' in page_html
    back = f"{DAY + timedelta(days=3):%d/%m/%Y}"
    assert site.post("/discover", form(site, return_dates=back)).location == "/discover"
    asked = started[0].first
    assert asked["task"] == "map" and asked["city"] == "Coimbra" and asked["look_up"] is True
    assert asked["planner_link"] == "/discover/map/1/plan/" and asked["dates"] == WRITTEN and asked["return_dates"] == back

def test_unusable_map_input_refused():
    site, started = site_and_jobs()
    for changes in ({"city": ""}, {"dates": "01/01/2020"}, {"dates": "32/13/2026"},
                    {"return_dates": f"{DAY - timedelta(days=3):%d/%m/%Y}"}):
        reply = site.post("/discover", form(site, **changes))
        assert reply.status == 400 and 'role="alert"' in reply.html
    assert started == []

def test_new_map_waits_old():
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

def test_stuck_map_killed(monkeypatch):
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    site.post("/discover/stop", form(site))
    assert started[0].answers == [{"stop": True}] and started[0].alive
    monkeypatch.setattr(maps, "STOP_SECONDS", -1)
    site.get("/discover")
    assert not started[0].alive and len(started) == 1   # stopped, and no map asked for next

def test_closing_lets_map_save(monkeypatch):
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    monkeypatch.setattr(maps, "STOP_SECONDS", 0.5)
    site.close()
    assert started[0].answers == [{"stop": True}] and not started[0].alive

def test_discover_needs_token():
    site, started = site_and_jobs()
    for path in ("/discover", "/discover/stop"):
        assert site.post(path, form(site, token="another")).status == 403
    assert started == []

def test_map_policy_separate():
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

def test_map_keeps_its_script(monkeypatch, tmp_path):
    # Its page holds the texts of its time: a newer script would ask for texts it has not, and show their keys
    script = tmp_path / "map.js"
    script.write_text("// the script of then", encoding="utf-8")
    monkeypatch.setitem(maps.FILES, "map.js", (script, "text/javascript; charset=utf-8"))
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    started[0].read_event("map", "<!doctype html><title>map</title>")
    script.write_text("// the script of now", encoding="utf-8")
    assert site.get("/discover/map/1/map.js").html == "// the script of then"

def test_map_trip_fills_form():
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

# A destination of a map there and back: the flight's price is the round trip's, each way has its two ways to and from the airports
BACK = DAY + timedelta(days=3)
DUBLIN = {"to": "DUB", "from": "BRU", "date": DAY.isoformat(), "time": "09:35", "price_eur": 59.98, "total_eur": 91.98,
          "back": {"from": "DUB", "to": "CRL", "date": BACK.isoformat(), "time": "06:35"},
          "access": [{"airport": "BRU", "to_airport": True, "price_eur": 7.0, "grade": "B", "price_grade": "C", "looked_up": True},
                     {"airport": "DUB", "to_airport": False, "price_eur": 8.0, "grade": "B", "price_grade": "B", "looked_up": True},
                     {"airport": "DUB", "to_airport": True, "price_eur": 8.0, "grade": "C", "price_grade": "B", "looked_up": False},
                     {"airport": "CRL", "to_airport": False, "price_eur": 9.0, "grade": "B", "price_grade": "A", "looked_up": True}]}

def test_estimate_uses_flight_id():
    there = maps.estimate_of({"points": [DUBLIN]}, "DUB-out", ["Antwerp", "Dublin", WRITTEN], "/discover/map/1/")
    assert there["flight_id"] == f"BRU-DUB-{DAY:%d%m}-0935" and there["ground_eur"] == 15.0 and there["looked_up"] and there["round_trip"]
    assert [(leg["airport"], leg["to_airport"]) for leg in there["legs"]] == [("BRU", True), ("DUB", False)]
    back = maps.estimate_of({"points": [DUBLIN]}, "DUB-back", ["Dublin", "Antwerp", f"{BACK:%d/%m/%Y}"], "/discover/map/1/")
    assert back["flight_id"] == f"DUB-CRL-{BACK:%d%m}-0635" and back["ground_eur"] == 17.0 and not back["looked_up"]   # one way was estimated
    assert maps.estimate_of({"points": [DUBLIN]}, "MAD-out", [], "") is None and maps.estimate_of({"points": [DUBLIN]}, "DUB-x", [], "") is None

def test_estimate_follows_unchanged_form():
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    started[0].read_event("trips", {"DUB-out": ["Antwerp", "Dublin", WRITTEN]})
    started[0].read_event("state", {"version": 1, "running": False, "points": [DUBLIN]})
    search = lambda **changes: urlencode({"token": site.token, "origin": "Antwerp", "destination": "Dublin", "dates": WRITTEN, **changes}).encode()
    site.get("/discover/map/1/plan/DUB-out")
    site.post("/search", search())
    assert started[-1].first["from_map"]["flight_id"] == f"BRU-DUB-{DAY:%d%m}-0935" and started[-1].first["from_map"]["map"] == "/discover/map/1/"
    site.get("/discover/map/1/plan/DUB-out")
    started[-1].alive = False
    site.post("/search", search(dates=f"{DAY + timedelta(days=1):%d/%m/%Y}"))   # another day: the map said nothing of it
    assert "from_map" not in started[-1].first
    started[-1].alive = False
    site.post("/search", search())   # used once: a search typed in later is not the map's
    assert "from_map" not in started[-1].first

def test_replaced_map_keeps_own():
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

def test_map_page_relative_links():
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43), Days("2026-11-12", "2026-11-19"), [], [], [], [])
    html = page.map_page(found, "Ryanair", [], planner_link="/discover/map/1/plan/", running=True)
    data = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', html).group(1))
    assert data["planner_link"] == "/discover/map/1/plan/"
    assert '<script src="map.js"></script>' in html and "<style>" not in html   # files beside the page: no script of its own
    script = page.SCRIPT.read_text(encoding="utf-8")
    # relative: under the map's address in the app; a map made before is given its own (maps.saved_page)
    assert 'fetch(data.state_link || "state")' in script and 'fetch("/state")' not in script

def test_map_process_reports_changes(monkeypatch):
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
    # done, then it waits for prices asked for (ready) until stdin closes, and the last state is sent as it ends
    assert kinds[:3] == ["trips", "map", "state"] and kinds[-3:] == ["done", "ready", "state"] and kinds.count("state") >= 3
    assert said[1]["text"] == "<page link=/discover/map/1/plan/>" and said[-1]["text"] == {"version": 2, "running": True}

def test_stopped_map_ends_lookups(monkeypatch):
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
    assert [json.loads(line)["kind"] for line in out.getvalue().splitlines()][-3:] == ["done", "ready", "state"]

def test_process_refuses_bad_days(monkeypatch):
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    job.main(iter([json.dumps({"task": "map", "language": "en", "city": "Coimbra", "dates": "01/01/2020"})]), events)
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [event["kind"] for event in said] == ["failed"]

def test_map_progress_with_stop():
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

def a_saved_map(folder, name: str = "coimbra_2026-11-12_2026-11-14", monkeypatch=None):
    """A map as discover/session.py saves it: its page with its script and its style inside."""
    leg = {"from": "OPO", "to": "BCN", "date": DAY.isoformat(), "time": "07:00", "arrival_date": DAY.isoformat(),
           "arrival_time": "09:45", "minutes": 105, "airline": "Ryanair", "stops": 0}
    flight = {**leg, "segments": [dict(leg)], "price_eur": 20, "carrier": "FR", "source": "Ryanair", "back": None,
              "ground_eur": 5.0, "total_eur": 25.0}
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(DAY.isoformat(), DAY.isoformat()), [], [flight], [], [])
    path = folder / f"{name}.html"
    path.write_text(page.map_page(found, "Ryanair", []), encoding="utf-8")
    return path

def test_old_maps_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(page, "load_airports", lambda: {"BCN": {"city": "Barcelona", "name": "Barcelona Airport", "country": "ES",
                                                                "latitude": 41.3, "longitude": 2.1}})
    monkeypatch.setattr(maps, "SAVED_MAPS", tmp_path)
    site, _ = site_and_jobs()
    assert tr("discover_win_no_earlier") in site.get("/discover").html
    a_saved_map(tmp_path)
    (tmp_path / "not_a_map.html").write_text("<!doctype html><p>something else", encoding="utf-8")
    shown = site.get("/discover").html
    assert 'href="/discover/saved/coimbra_2026-11-12_2026-11-14"' in shown and "not_a_map" not in shown
    assert tr("discover_win_destinations", count=1) in shown and tr("discover_win_from_price", price="25") in shown

def test_old_map_script_only(monkeypatch, tmp_path):
    monkeypatch.setattr(page, "load_airports", lambda: {"BCN": {"city": "Barcelona", "name": "Barcelona Airport", "country": "ES",
                                                                "latitude": 41.3, "longitude": 2.1}})
    monkeypatch.setattr(maps, "SAVED_MAPS", tmp_path)
    a_saved_map(tmp_path)
    site, _ = site_and_jobs()
    shown = site.get("/discover/saved/coimbra_2026-11-12_2026-11-14")
    assert shown.status == 200 and "<script>" in shown.html and "'unsafe-inline'" not in shown.policy
    assert shown.policy.count("'sha256-") == 2 and "default-src 'none'" in shown.policy   # its script and its style, nothing else
    for path in ("/discover/saved/elsewhere", "/discover/saved/..%2F..%2Fsettings", "/discover/saved/"):
        assert site.get(path).status == 404

def test_late_price_request_served(monkeypatch):
    import discover.view.page as discover_page
    import discover.session as discover_session
    import trip_planner.trip.places as places
    asked = []

    class Session:
        def __init__(self, city, days, fast, light, max_stops, ground_only):
            self.found = type("Found", (), {"kiwi_why": ""})()
            self.live, self.lock, self.sources, self.sections, self.path = {"state": {}}, threading.Lock(), "Ryanair", [], "x.html"
            self.stopping = threading.Event()

        def make(self):
            pass

        def look_up(self, timetables):
            pass

        def price_asked(self, key):
            asked.append(key)
            with self.lock:
                self.live["state"] = {"version": 5, "asked_prices": {key: "priced"}}
            priced.set()

    ready, priced = threading.Event(), threading.Event()
    never = []   # what the app's side waited for in vain: said here, since an assert in the reading thread is lost

    def lines():
        # As the app does: the price is asked once the lookups ended and the process said it is ready, then stop
        yield json.dumps({"task": "map", "language": "en", "city": "Coimbra", "dates": WRITTEN, "token": "secret"})
        if not ready.wait(5):
            never.append("ready")
        yield json.dumps({"price": "~FR|Paris|48.85"})
        if not priced.wait(5):
            never.append("priced")
        yield json.dumps({"stop": True})
        threading.Event().wait()
    monkeypatch.setattr(discover_session, "Session", Session)
    monkeypatch.setattr(discover_page, "map_page", lambda *arguments, token="", **more: f"<page token={token}>")
    monkeypatch.setattr(discover_page, "map_state", lambda *arguments, **more: {})
    monkeypatch.setattr(discover_page, "trips", lambda found, airports: {})
    monkeypatch.setattr(places, "load_airports", dict)
    out = io.StringIO()
    events = job.Events(out)
    real_send = events.send
    monkeypatch.setattr(events, "send", lambda kind, text="": real_send(kind, text) or (kind == "ready" and ready.set()))
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    assert job.main(lines(), events) == 0
    assert never == []
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert asked == ["~FR|Paris|48.85"] and said[1]["text"] == "<page token=secret>"   # the served page carries the app's token
    assert said[-1]["text"] == {"version": 5, "asked_prices": {"~FR|Paris|48.85": "priced"}}

def test_price_request_needs_token():
    site, started = site_and_jobs()
    site.post("/discover", form(site))
    current_map = started[-1]
    assert current_map.first["token"] == site.token   # given to the map's process, for its page
    current_map.told = current_map.answers   # what the process is told
    asking = lambda **changes: site.post("/discover/map/1/price", urlencode({"token": site.token, "city": "~FR|Paris|48.85", **changes}).encode())
    reply = asking()
    assert reply.status == 202 and current_map.told == [{"price": "~FR|Paris|48.85"}]
    assert asking(token="wrong").status == 403 and len(current_map.told) == 1
    assert site.post("/discover/map/9/price", urlencode({"token": site.token, "city": "x"}).encode()).status == 404
    current_map.stopped = True   # stopping, or ended: no process to ask
    assert asking().status == 410 and len(current_map.told) == 1

def test_old_map_named_days(tmp_path):
    days = maps.days_of_saved(tmp_path / "antwerp_2026-12-05_2026-12-06_back_2026-12-07_2026-12-09_07-09.html")
    assert (days.first, days.last, days.back_first, days.back_last) == ("2026-12-05", "2026-12-06", "2026-12-07", "2026-12-09")
    assert days.out_days == () and days.back_days == ("2026-12-07", "2026-12-09")   # the days clicked, not all in a row
    assert not maps.days_of_saved(tmp_path / "sao-paulo_2026-12-05_2026-12-05.html").round_trip
    assert maps.days_of_saved(tmp_path / "not_a_map.html") is None

def test_old_map_awaits_price(monkeypatch, tmp_path):
    monkeypatch.setattr(page, "load_airports", lambda: {"BCN": {"city": "Barcelona", "name": "Barcelona Airport", "country": "ES",
                                                                "latitude": 41.3, "longitude": 2.1}})
    monkeypatch.setattr(maps, "SAVED_MAPS", tmp_path)
    path = a_saved_map(tmp_path)
    assert '"token"' not in path.read_text(encoding="utf-8")   # never saved with the page
    site, started = site_and_jobs()
    address = "/discover/saved/coimbra_2026-11-12_2026-11-14"
    shown = site.get(address)
    data = maps.page_data(shown.html)
    assert data["token"] == site.token and data["price_link"] == address + "/price" and data["state_link"] == address + "/state"
    assert shown.policy.count("'sha256-") == 2   # its data changed, its script and its style as they were
    asking = lambda city, token=site.token: site.post(address + "/price", urlencode({"token": token, "city": city}).encode())
    assert asking("~ES|Madrid|40.42", token="wrong").status == 403 and not started
    assert asking("~ES|Madrid|40.42").status == 202 and asking("~ES|Lyon|45.76").status == 202
    assert started[0].first == {"task": "price_saved", "language": current_language(), "name": "coimbra_2026-11-12_2026-11-14",
                                "keys": ["~ES|Madrid|40.42"], "coach_changes": "0"}   # one job at a time: Lyon waits for it
    state = json.loads(site.get(address + "/state").html)
    assert state["asked_prices"] == {"~ES|Madrid|40.42": "waiting", "~ES|Lyon|45.76": "waiting"}
    started[0].alive = False   # its job ended: the next one prices Lyon
    site.get(address + "/state")
    assert len(started) == 2 and started[1].first["keys"] == ["~ES|Lyon|45.76"]
    assert site.post("/discover/saved/elsewhere/price", urlencode({"token": site.token, "city": "x"}).encode()).status == 404

def current_language():
    from trip_planner import current
    return current.SETTINGS.language

def test_old_map_page_repriced(monkeypatch, tmp_path):
    from discover.overland import ground_fares
    monkeypatch.setattr(page, "load_airports", lambda: {"BCN": {"city": "Barcelona", "name": "Barcelona Airport", "country": "ES",
                                                                "latitude": 41.3, "longitude": 2.1}})
    monkeypatch.setattr(maps, "SAVED_MAPS", tmp_path)
    monkeypatch.setattr(ground_fares, "RATES", tmp_path / "rates.json")
    path = a_saved_map(tmp_path)
    data = maps.page_data(path.read_text(encoding="utf-8"))
    bus = {"operator": "FlixBus", "mode": "bus", "modes": "COACH", "sold": "EU", "from_stop": "Coimbra", "from_stop_id": "eu-flixbus_a",
           "stop": "Madrid", "stop_id": "eu-flixbus_b", "lat": 40.42, "lon": -3.70, "times": ["08:00"], "weekdays": ["Thu"], "minutes": 400, "grade": "B"}
    data["state"]["by_ground"] = [{"key": "ES|Madrid|40.42", "name": "Madrid", "country": "ES", "lat": 40.42, "lon": -3.70,
                                   "minutes": 400, "modes": ["bus"], "ground": [bus]}]
    from app.map_job import price_saved
    from trip_planner.utils import write_whole
    write_whole(path, maps.with_data(path.read_text(encoding="utf-8"), data))
    import os
    os.utime(path, (1_700_000_000, 1_700_000_000))
    monkeypatch.setattr(ground_fares, "price_of", lambda option, days, today, transitous, towns=None:
                        {"out": {"price_eur": 30.0}, "back": None, "total_eur": 30.0, "stay": None})
    said = []
    price_saved({"name": path.stem, "keys": ["~ES|Madrid|40.42", "~ES|Nowhere|0.00"]}, type("Events", (), {"send": lambda self, *what: said.append(what)})())
    state = maps.page_data(path.read_text(encoding="utf-8"))["state"]
    assert state["by_ground"][0]["fare_eur"] == 30.0 and state["by_ground"][0]["ground"][0]["price"]["total_eur"] == 30.0
    assert state["asked_prices"] == {"~ES|Madrid|40.42": "priced", "~ES|Nowhere|0.00": "none"} and state["version"] == 1
    assert path.stat().st_mtime == 1_700_000_000 and said[-1][0] == "done"   # still where it was in the list

def test_old_map_file_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(page, "load_airports", lambda: {"BCN": {"city": "Barcelona", "name": "Barcelona Airport", "country": "ES",
                                                                "latitude": 41.3, "longitude": 2.1}})
    monkeypatch.setattr(maps, "SAVED_MAPS", tmp_path)
    path = a_saved_map(tmp_path)
    old = path.read_text(encoding="utf-8").replace("// The map page's script", "// an older script", 1)
    old = maps.with_data(old, {**maps.page_data(old), "texts": {"close": "an older text"}})
    path.write_text(old, encoding="utf-8")
    site, _ = site_and_jobs()
    shown = site.get("/discover/saved/coimbra_2026-11-12_2026-11-14")
    assert page.SCRIPT.read_text(encoding="utf-8") in shown.html and "// an older script" not in shown.html
    assert maps.page_data(shown.html)["texts"] == page.page_texts() and shown.policy.count("'sha256-") == 2
    assert path.read_text(encoding="utf-8") == old

def test_map_follows_app_choices(monkeypatch, tmp_path):
    """A map made in English, read once the app was turned to Portuguese and light by the map's own buttons: its
    script's texts, its links and what was searched in Portuguese, and the page light."""
    from trip_planner import current
    monkeypatch.setattr(current, "SETTINGS", current.SETTINGS)   # put back after the language is changed
    monkeypatch.setattr(page, "load_airports", lambda: {"BCN": {"city": "Barcelona", "name": "Barcelona Airport", "country": "ES",
                                                                "latitude": 41.3, "longitude": 2.1}})
    monkeypatch.setattr(maps, "SAVED_MAPS", tmp_path)
    a_saved_map(tmp_path)
    site, _ = site_and_jobs()
    assert site.post("/language", urlencode({"token": site.token, "language": "pt-PT", "from": "map"}).encode()).status == 204
    assert site.post("/theme", urlencode({"token": site.token, "theme": "light"}).encode()).status == 204
    shown = site.get("/discover/saved/coimbra_2026-11-12_2026-11-14")
    data = maps.page_data(shown.html)
    assert '<html lang="en" data-theme="light">' in shown.html and data["app_token"] == site.token
    assert data["locale"] == "pt-PT" and data["texts"]["link_compare"] == "Comparar no Google Flights"
    compare = next(link for link in data["state"]["points"][0]["links"] if link["kind"] == "compare")
    assert compare["text"] == ["link_compare", {}] and data["state"]["sections_in"]["pt-PT"]

def test_coach_changes_setting_offered():
    import pytest
    from trip_planner.trip.trip_settings import SettingError
    assert maps.coach_settings({"coach_changes": "2"}).max_coach_changes == 2
    with pytest.raises(SettingError):
        maps.coach_settings({"coach_changes": "7"})
    assert maps.refusal_of({"city": "Antwerp", "dates": "12/11/2030", "return_dates": "", "coach_changes": "x"})
    shown = maps.form("t", maps.values_of("Antwerp", coach_changes=1))
    assert shown.count('name="coach_changes"') == len(maps.COACH_CHANGES) and 'value="1" checked' in shown
