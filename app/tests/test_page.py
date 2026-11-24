"""The page in a real browser (page.js, and the pages without it), run with `pytest --browser`: the calendar, the
part of a running search put in place without a reload, the language, a browser without scripts, two tabs and the
back button, and a Discover map in the app. The searches are stood in for (test_search.StandIn): no network but
this computer's own address, and unpkg for the map's Leaflet."""
import time
from datetime import date, timedelta

import pytest

from app import server
from app.site import Site
from app.tests import browser as browsers
from app.tests.test_search import StandIn
from settings import SETTINGS

@pytest.fixture(scope="module")
def browser(request):
    if not request.config.getoption("--browser"):
        pytest.skip("the page is tested in a browser only with --browser")
    path = browsers.found()
    if path is None:
        pytest.skip("no Edge or Chrome on this computer")
    opened = browsers.Browser(path)
    yield opened
    opened.close()

@pytest.fixture
def app():
    """The app served, its searches stood in for: app.started holds them."""
    started = []
    site = Site(SETTINGS, start=lambda first: started.append(StandIn(first)) or started[-1], stale=list)
    site.started = started
    running = server.start(site)
    site.address = server.address_of(running)
    yield site
    server.stop(running)

@pytest.fixture
def tab(browser, app):
    opened = browser.tab()
    opened.open(app.address)
    yield opened
    opened.call("Page.close")

def written(day: date) -> str:
    return f"{day:%d/%m/%Y}"

def calendar_of(tab, field: str = "dates", value: str = "") -> None:
    tab.js(f"document.querySelector('input[name={field}]').value = {value!r}")
    tab.js(f"document.querySelector('input[name={field}]').click()")
    tab.wait("!!document.querySelector('.calendar')")

def click_day(tab, number: int) -> None:
    tab.js(f"[...document.querySelectorAll('.calendar td button')].find(b => b.textContent === '{number}').click()")

def turn(tab, months: int) -> None:
    for _ in range(months):
        tab.js("document.querySelectorAll('.calendar .month button')[1].click()")

def field(tab, name: str = "dates") -> str:
    return tab.js(f"document.querySelector('input[name={name}]').value")

# ---------------------------------------------------------------- the calendar

def test_calendar_stays_open(tab):
    calendar_of(tab)
    assert tab.js("document.querySelectorAll('.calendar .month button')[0].disabled")   # no month before this one
    turn(tab, 13)
    assert tab.js("!!document.querySelector('.calendar')")
    assert tab.js("document.querySelector('.calendar .month span').textContent").endswith(str(date.today().year + 1))

def test_calendar_closes_outside(tab):
    calendar_of(tab)
    tab.js("document.querySelector('h1').click()")
    assert tab.js("!document.querySelector('.calendar')")
    calendar_of(tab)
    tab.js("document.querySelector('input[name=dates]').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert tab.js("!document.querySelector('.calendar')")

def test_click_toggles_day(tab):
    calendar_of(tab)
    turn(tab, 1)
    click_day(tab, 10)
    click_day(tab, 12)
    first = date.today().replace(day=1) + timedelta(days=32)
    assert field(tab) == f"{written(first.replace(day=10))}, {written(first.replace(day=12))}"
    click_day(tab, 10)
    assert field(tab) == written(first.replace(day=12))

def test_seven_days_at_most(tab):
    calendar_of(tab)
    turn(tab, 1)
    for number in range(1, 11):
        click_day(tab, number)
    assert len(field(tab).split(", ")) == 7

def test_unsearchable_days_disabled(tab):
    calendar_of(tab)
    today = date.today()
    if today.day > 1:
        assert tab.js("[...document.querySelectorAll('.calendar td button')].find(b => b.textContent === '1').disabled")
    turn(tab, 12)   # past the days airlines sell
    assert tab.js("[...document.querySelectorAll('.calendar td button')].pop().disabled")

def test_return_not_before_outbound(tab):
    going = date.today().replace(day=1) + timedelta(days=62)
    going = going.replace(day=15)
    tab.js(f"document.querySelector('input[name=dates]').value = '{written(going)}'")
    calendar_of(tab, "return_dates")
    assert tab.js("document.querySelector('.calendar .month span').textContent").endswith(str(going.year))
    assert tab.js("[...document.querySelectorAll('.calendar td button')].find(b => b.textContent === '14').disabled")
    assert not tab.js("[...document.querySelectorAll('.calendar td button')].find(b => b.textContent === '15').disabled")

def test_clear_keeps_worded_days(tab):
    calendar_of(tab, value="sexta")
    turn(tab, 1)
    click_day(tab, 3)
    assert field(tab).endswith(", sexta") and field(tab).startswith("03/")
    tab.js("document.querySelector('.calendar button.quiet').click()")
    assert field(tab) == "sexta"

# ---------------------------------------------------------------- a running search

def start_search(tab, app) -> StandIn:
    tab.js("document.querySelector('input[name=dates]').value = '17/12/2026'")
    tab.submit("form.search")
    assert tab.js("location.pathname") == "/search"
    return app.started[-1]

def test_enter_searches(tab, app):
    tab.js(f"document.querySelector('input[name=dates]').value = '{written(date.today() + timedelta(days=30))}'")
    tab.js("document.querySelector('input[name=origin]').focus()")
    for kind in ("keyDown", "keyUp"):
        tab.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                 **({"text": "\r"} if kind == "keyDown" else {}))
    tab.wait("location.pathname === '/search'")
    assert len(app.started) == 1

def test_search_shown_without_reload(tab, app):
    search = start_search(tab, app)
    search.lines.append("Flights: 26 found")
    tab.wait("document.querySelector('#live').textContent.includes('Flights: 26 found')")
    search.shown.append("# Final result")
    tab.wait("!!document.querySelector('#live article h1')")
    assert tab.js("window.sameLoad === true")
    search.alive = False
    tab.wait("!document.querySelector('#live[data-working]')")
    search.lines.append("never asked for")
    time.sleep(3)
    assert not tab.js("document.querySelector('#live').textContent.includes('never asked for')")
    assert tab.js("window.sameLoad === true")

def test_progress_keeps_open_state(tab, app):
    search = start_search(tab, app)
    search.shown.append("# Final result")   # with a result the progress starts closed
    tab.wait("!!document.querySelector('#live article')")
    tab.js("document.querySelector('#live details').open = true")
    search.lines.append("Ground transport: 16 of 16")
    tab.wait("document.querySelector('#live').textContent.includes('16 of 16')")
    assert tab.js("document.querySelector('#live details').open")

def test_search_question_appears(tab, app):
    search = start_search(tab, app)
    search.asked = ("airports", {"groups": [{"title": "Arrival", "airports": [
        {"code": "BRU", "km": 36, "name": "Brussels", "city": "Zaventem", "country": "BE"}]}], "notes": []})
    tab.wait("!!document.querySelector('form[action=\"/airports\"]')")
    assert tab.js("window.sameLoad === true")
    tab.submit("form[action='/airports']")
    assert search.answers[-1] == {"dropped": []}

# ---------------------------------------------------------------- the language, a browser without scripts, tabs

def test_language_applies_at_once(tab):
    tab.js("const choice = document.querySelector('select[data-submit]'); choice.value = 'en'; choice.dispatchEvent(new Event('change'))")
    tab.loaded()
    assert tab.js("document.documentElement.lang") == "en" and tab.js("document.querySelector('h1').textContent") == "Plan a trip"

def test_works_without_scripts(tab, app):
    tab.call("Emulation.setScriptExecutionDisabled", value=True)
    tab.open(app.address)
    assert tab.js("!document.querySelector('.calendar')")
    start_search(tab, app).lines.append("Flights: 26 found")
    began = tab.js("performance.timeOrigin")
    time.sleep(4.5)   # the page asks for itself again after 3 s
    assert tab.js("performance.timeOrigin") != began and "Flights: 26 found" in tab.js("document.body.textContent")
    tab.call("Emulation.setScriptExecutionDisabled", value=False)

def test_second_tab_can_stop(tab, app, browser):
    search = start_search(tab, app)
    other = browser.tab()
    try:
        other.open(app.address)
        assert other.js("document.querySelector('form.search button[type=submit]').disabled")   # one search at a time
        other.open(app.address + "search")
        other.submit("form[action='/stop']")
        assert search.stopped
    finally:
        other.call("Page.close")

def test_back_shows_form_busy(tab, app):
    start_search(tab, app)
    tab.js("history.back()")
    tab.loaded()
    assert tab.js("location.pathname") == "/"
    assert tab.js("!!document.querySelector('form.search a[href=\"/search\"]')")

# ---------------------------------------------------------------- the form's extras

def test_swap_button_swaps(tab):
    tab.js("document.querySelector('input[name=origin]').value = 'Coimbra'; document.querySelector('input[name=destination]').value = 'Ghent'")
    assert tab.js("!document.querySelector('button[data-swap]').hidden")
    tab.js("document.querySelector('button[data-swap]').click()")
    assert tab.js("[document.querySelector('input[name=origin]').value, document.querySelector('input[name=destination]').value]") == ["Ghent", "Coimbra"]

def test_changes_counted_live(tab):
    summary = "document.querySelector('details.advanced summary').textContent"
    assert "(" not in tab.js(summary)
    tab.js("{ const box = document.querySelector('input[name=options_shown]'); box.value = '9'; box.dispatchEvent(new Event('input', {bubbles: true})) }")
    tab.js("{ const tick = document.querySelector('details.advanced input[type=checkbox]'); tick.click() }")
    assert "2" in tab.js(summary)
    tab.js("{ const box = document.querySelector('input[name=options_shown]'); box.value = box.dataset.default; box.dispatchEvent(new Event('input', {bubbles: true})) }")
    assert "1" in tab.js(summary)

def test_trip_kind_follows_return(tab):
    kind = "document.querySelector('.trip-kind').textContent"
    one_way = tab.js(kind)
    hidden = "getComputedStyle(document.querySelector('input[name=return_dates]')).display === 'none' || !document.querySelector('input[name=return_dates]').offsetParent"
    assert tab.js(hidden)   # one way: there are no days to come back
    tab.js("document.querySelector('input[name=trip_kind][value=round]').click()")
    assert not tab.js(hidden) and tab.js("document.activeElement.name") == "return_dates"
    calendar_of(tab, "return_dates")
    turn(tab, 2)
    click_day(tab, 5)
    assert tab.js(kind) != one_way
    tab.js("{ const back = document.querySelector('input[name=return_dates]'); back.value = ''; back.dispatchEvent(new Event('input', {bubbles: true})) }")
    assert tab.js(kind) == one_way

# ---------------------------------------------------------------- a Discover map in the app

def test_app_map_opens_form(tab, app, monkeypatch):
    """The one test that needs more than this computer: Leaflet comes from unpkg. The map's tiles are not asked for."""
    from discover import finder
    from discover.view import page
    from discover.flights.sources import Days
    from trip_planner.trip.places import Place
    airports = {"BHX": {"city": "Birmingham", "name": "Birmingham Airport", "country": "GB", "latitude": 52.4, "longitude": -1.7}}
    monkeypatch.setattr(page, "load_airports", lambda: airports)
    leaving = date.today() + timedelta(days=30)
    leg = {"from": "OPO", "to": "BHX", "date": leaving.isoformat(), "time": "07:00", "arrival_date": leaving.isoformat(),
           "arrival_time": "09:45", "minutes": 165, "airline": "Ryanair", "stops": 0}
    flight = {**leg, "segments": [dict(leg)], "price_eur": 10, "carrier": "FR", "source": "Ryanair", "back": None,
              "ground_eur": 5.0, "total_eur": 15.0}
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(leaving.isoformat(), leaving.isoformat()), [], [flight], [], [])
    app.post("/discover", f"token={app.token}&city=Coimbra&dates={written(leaving)}&source=light".encode())
    made = app.started[-1]
    made.read_event("map", page.map_page(found, "Ryanair", [], planner_link=made.map_link + "plan/"))
    made.read_event("trips", page.trips(found, airports))
    made.read_event("state", page.map_state(found, []))
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.call("Page.addScriptToEvaluateOnNewDocument", source="window.violations = []; window.errors = []; addEventListener('error', event => errors.push(event.message));"
             "document.addEventListener('securitypolicyviolation', event => violations.push(event.violatedDirective + ' ' + event.blockedURI));")
    tab.open(app.address + "discover/map/1/")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("document.getElementById('title').textContent !== ''")
    assert tab.js("violations") == []   # the page's own script and style are files: nothing inline was needed
    tab.js("document.querySelector('#rows li').click()")
    tab.wait("!!document.querySelector('#detail a[href$=\"/plan/BHX-out\"]')")
    assert tab.js("errors") == []
    tab.js("document.querySelector('#detail a[href$=\"/plan/BHX-out\"]').click()")
    tab.wait("location.pathname === '/' && document.readyState === 'complete'")
    assert tab.js("document.querySelector('input[name=destination]').value") == "Birmingham"
    assert len(app.started) == 1   # the form filled in, nothing searched

def test_map_turns_language_theme(tab, app, monkeypatch):
    """The map's own buttons: light or dark, and the language, which the page comes back in. Leaflet comes from
    unpkg, as in the test above."""
    from discover import finder
    from discover.view import page
    from discover.flights.sources import Days
    from trip_planner import current
    from trip_planner.trip.places import Place
    monkeypatch.setattr(current, "SETTINGS", current.SETTINGS)   # put back after the language is changed
    airports = {"BHX": {"city": "Birmingham", "name": "Birmingham Airport", "country": "GB", "latitude": 52.4, "longitude": -1.7}}
    monkeypatch.setattr(page, "load_airports", lambda: airports)
    leaving = date.today() + timedelta(days=30)
    leg = {"from": "OPO", "to": "BHX", "date": leaving.isoformat(), "time": "07:00", "arrival_date": leaving.isoformat(),
           "arrival_time": "09:45", "minutes": 165, "airline": "Ryanair", "stops": 0}
    flight = {**leg, "segments": [dict(leg)], "price_eur": 10, "carrier": "FR", "source": "Ryanair", "back": None,
              "ground_eur": 5.0, "total_eur": 15.0}
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(leaving.isoformat(), leaving.isoformat()), [], [flight], [], [])
    app.post("/discover", f"token={app.token}&city=Coimbra&dates={written(leaving)}&source=light".encode())
    made = app.started[-1]
    made.read_event("map", page.map_page(found, "Ryanair", [], planner_link=made.map_link + "plan/"))
    made.read_event("state", page.map_state(found, []))
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.open(app.address + "discover/map/1/")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("!!document.querySelector('header .controls select')")
    assert tab.js("document.documentElement.dataset.theme") == "dark"
    tab.js("document.querySelector('header .controls .theme').click()")
    tab.wait("document.documentElement.dataset.theme === 'light'")
    for _ in range(50):   # the app is told, in the background
        if app.theme == "light":
            break
        time.sleep(0.1)
    assert app.theme == "light"
    tab.js("const s = document.querySelector('header .controls select'); s.value = 'pt-PT'; s.dispatchEvent(new Event('change'))")
    tab.wait("document.documentElement.lang === 'pt-PT' && document.getElementById('searched-title').textContent !== ''")
    assert tab.js("document.querySelector('header .controls select').value") == "pt-PT"
    assert tab.js("document.documentElement.dataset.theme") == "light"

def test_typed_limit_keeps_slider(tab, app, monkeypatch):
    """Leaflet comes from unpkg, as in the test above."""
    from discover import finder
    from discover.view import page
    from discover.flights.sources import Days
    from trip_planner.trip.places import Place
    airports = {code: {"city": city, "name": city + " Airport", "country": "GB", "latitude": 52.4, "longitude": longitude}
                for code, city, longitude in (("BHX", "Birmingham", -1.7), ("STN", "London", 0.2))}
    monkeypatch.setattr(page, "load_airports", lambda: airports)
    leaving = date.today() + timedelta(days=30)
    flights = []
    for code, total in (("BHX", 15.0), ("STN", 480.0)):
        leg = {"from": "OPO", "to": code, "date": leaving.isoformat(), "time": "07:00", "arrival_date": leaving.isoformat(),
               "arrival_time": "09:45", "minutes": 165, "airline": "Ryanair", "stops": 0}
        flights.append({**leg, "segments": [dict(leg)], "price_eur": total - 5, "carrier": "FR", "source": "Ryanair", "back": None,
                        "ground_eur": 5.0, "total_eur": total})
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(leaving.isoformat(), leaving.isoformat()), [], flights, [], [])
    app.post("/discover", f"token={app.token}&city=Coimbra&dates={written(leaving)}&source=light".encode())
    made = app.started[-1]
    made.read_event("map", page.map_page(found, "Ryanair", [], planner_link=made.map_link + "plan/"))
    made.read_event("trips", page.trips(found, airports))
    made.read_event("state", page.map_state(found, []))
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.open(app.address + "discover/map/1/")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("document.querySelectorAll('#rows li').length === 2")
    where = "Math.round(document.querySelector('#filters .limit input[type=range]').getBoundingClientRect().left)"
    left = tab.js(where)
    for value in (480, 100, 10):   # no limit, three digits, two: the words around the value stay as they are
        tab.js(f"{{ const range = document.querySelector('#filters .limit input[type=range]'); range.value = {value}; range.dispatchEvent(new Event('input')) }}")
        assert tab.js(where) == left
    tab.js("{ const typed = document.querySelector('#filters .limit input[type=number]'); typed.value = '17'; typed.dispatchEvent(new Event('change')) }")
    tab.wait("document.querySelectorAll('#rows li').length === 1")
    assert tab.js("document.querySelector('#filters .limit input[type=range]').value") == "20"   # the slider follows, to its nearest step
    assert tab.js("document.querySelector('#filters .limit input[type=number]').value") == "17"   # and the value typed is the one kept
    assert tab.js(where) == left

def test_hour_filters_hide_flights(tab, app, monkeypatch):
    """Leaflet comes from unpkg, as in the tests above."""
    from discover import finder
    from discover.view import page
    from discover.flights.sources import Days
    from trip_planner.trip.places import Place
    airports = {code: {"city": city, "name": city + " Airport", "country": "GB", "latitude": 52.4, "longitude": longitude}
                for code, city, longitude in (("BHX", "Birmingham", -1.7), ("STN", "London", 0.2))}
    monkeypatch.setattr(page, "load_airports", lambda: airports)
    leaving = date.today() + timedelta(days=30)
    flights = []
    for code, leaves, arrives, landing_day in (("BHX", "07:00", "09:45", leaving), ("STN", "21:30", "00:15", leaving + timedelta(days=1))):
        leg = {"from": "OPO", "to": code, "date": leaving.isoformat(), "time": leaves, "arrival_date": landing_day.isoformat(),
               "arrival_time": arrives, "minutes": 165, "airline": "Ryanair", "stops": 0}
        flights.append({**leg, "segments": [dict(leg)], "price_eur": 10.0, "carrier": "FR", "source": "Ryanair", "back": None,
                        "ground_eur": 5.0, "total_eur": 15.0})
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(leaving.isoformat(), leaving.isoformat()), [], flights, [], [])
    app.post("/discover", f"token={app.token}&city=Coimbra&dates={written(leaving)}&source=light".encode())
    made = app.started[-1]
    made.read_event("map", page.map_page(found, "Ryanair", [], planner_link=made.map_link + "plan/"))
    made.read_event("state", page.map_state(found, []))
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.open(app.address + "discover/map/1/")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("document.querySelectorAll('#rows li').length === 2")
    shown = "[...document.querySelectorAll('#rows li')].map(row => row.dataset.to).join()"
    choose = "{{ const typed = document.querySelectorAll('#filters .limit input[type=number]')[{0}]; typed.value = '{1}'; typed.dispatchEvent(new Event('change')) }}"
    assert tab.js("document.querySelectorAll('#filters .limit input[type=number]')[2].value") == "0"   # from 0 h: any hour
    tab.js(choose.format(2, 8))   # leaving from 08:00: the flight leaving at 07:00 is hidden
    tab.wait(f"{shown} === 'STN'")
    tab.js(choose.format(2, 0))   # 0: any hour again
    tab.wait("document.querySelectorAll('#rows li').length === 2")
    tab.js(choose.format(3, 23))   # arriving by 23:00: the one landing after midnight is hidden
    tab.wait(f"{shown} === 'BHX'")

def test_map_line_three_parts(tab, app, monkeypatch):
    """Leaflet comes from unpkg, as in the tests above."""
    from discover import finder
    from discover.view import page
    from discover.flights.sources import Days
    from trip_planner.trip.places import Place
    airports = {"BHX": {"city": "Birmingham", "name": "Birmingham Airport", "country": "GB", "latitude": 52.4, "longitude": -1.7},
                "OPO": {"city": "Porto", "name": "Porto Airport", "country": "PT", "latitude": 41.2, "longitude": -8.7}}
    monkeypatch.setattr(page, "load_airports", lambda: airports)
    leaving = date.today() + timedelta(days=30)
    leg = {"from": "OPO", "to": "BHX", "date": leaving.isoformat(), "time": "12:00", "arrival_date": leaving.isoformat(),
           "arrival_time": "14:45", "minutes": 165, "airline": "Ryanair", "stops": 0}
    vehicles = [{"kind": "train", "time": "08:00", "arrive": "08:50", "from": "Coimbra-B", "to": "Aveiro"},
                {"kind": "train", "time": "09:00", "arrive": "09:50", "from": "Aveiro", "to": "Porto"},
                {"kind": "tram", "time": "10:00", "arrive": "10:30", "from": "Porto", "to": "Aeroporto"}]
    way = {"airport": "OPO", "to_airport": True, "place": "Coimbra", "price_eur": 12.0, "minutes": 150, "grade": "B", "price_grade": "B",
           "how": "", "looked_up": True, "leave": "08:00", "arrive": "10:30", "vehicles": vehicles}
    published = [{"operator": "National Express", "mode": "bus", "to": "", "from_price": False, "price_text": "£4", "price_eur": 5.0,
                  "source": "Wikivoyage", "url": "https://en.wikivoyage.org/wiki/Birmingham"},
                 {"operator": "West Midlands Railway", "mode": "train", "to": "", "from_price": True, "price_text": "£5", "price_eur": 6.0,
                  "source": "wmr.uk", "url": "https://www.wmr.uk"}]
    into = {"airport": "BHX", "to_airport": False, "place": "Birmingham", "price_eur": 5.0, "minutes": 30, "grade": "C",
            "price_grade": "B", "how": "£4 as published by Wikivoyage for the bus", "looked_up": False,
            "url": "https://en.wikivoyage.org/wiki/Birmingham", "quote": "The bus costs £4.", "options": published}
    flight = {**leg, "segments": [dict(leg)], "price_eur": 10, "carrier": "FR", "source": "Ryanair", "back": None,
              "ground_eur": 17.0, "total_eur": 27.0, "access": [way, into]}
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(leaving.isoformat(), leaving.isoformat()), [], [flight], [], [],
                         centres={"BHX": Place("Birmingham", "Birmingham", "Birmingham", "United Kingdom", 52.48, -1.9)})
    app.post("/discover", f"token={app.token}&city=Coimbra&dates={written(leaving)}&source=light".encode())
    made = app.started[-1]
    made.read_event("map", page.map_page(found, "Ryanair", [], planner_link=made.map_link + "plan/"))
    made.read_event("trips", page.trips(found, airports))
    made.read_event("state", page.map_state(found, []))
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.call("Page.addScriptToEvaluateOnNewDocument", source="window.errors = []; addEventListener('error', event => errors.push(event.message));")
    tab.open(app.address + "discover/map/1/")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("!!document.querySelector('#rows li')")
    tab.js("document.querySelector('#rows li').click()")
    tab.wait("!!document.querySelector('#detail .line svg')")
    icons = tab.js("[...document.querySelectorAll('#detail .line svg use')].map(use => use.getAttribute('href'))")
    assert icons == ["#i-train", "#i-train", "#i-tram"]   # the app's icons, drawn by the sprite in the page
    assert tab.js("!!document.querySelector('body > svg symbol#i-train')")
    assert tab.js("[...document.querySelectorAll('#detail .line svg circle title')].map(title => title.textContent)") == [
        "08:50 Aveiro", "09:00 Aveiro", "09:50 Porto", "10:00 Porto"]   # the changes, said on hover
    # The trip from top to bottom, as the planner's option: the places, each way with its vehicles, the flight
    steps = tab.js("[...document.querySelectorAll('#detail ol.steps > li')].map(item => item.className + '|' + item.querySelector('.at').textContent)")
    assert steps == ["|08:00", "ride|", "|10:30", "ride|12:00", "|14:45", "ride|~15:30", "|~16:00"]
    assert tab.js("document.querySelectorAll('#detail ol.steps ul.vehicles li:not(.change)').length") == 3
    assert tab.js("document.querySelectorAll('#detail ol.steps ul.vehicles li.change').length") == 2
    assert tab.js("document.querySelectorAll('#detail ol.steps details.others li').length") == 2   # the published ways, closed
    assert not tab.js("document.querySelector('#detail ol.steps details.others').open")
    # Its route on the map: home to Porto in the colour of that way, the flight, and Birmingham's airport into the city
    colours = tab.js("[...document.querySelectorAll('.leaflet-overlay-pane path')].map(path => path.getAttribute('stroke'))")
    grades = tab.js("data.grade_colours")
    assert [grades["B"], "#1f6fd0", grades["C"]] == [colour for colour in colours if colour != "#fff"][-3:]
    # ... and on each part, its vehicle where it is: the train of the way there, the plane, and a way whose vehicles are not known
    assert tab.js("[...document.querySelectorAll('.route-icon use')].map(use => use.getAttribute('href'))") == ["#i-train", "#i-plane", "#i-route"]
    # A way of a few kilometres has no room for its icon until the map is brought closer: it would hide its own ends
    shown = "[...document.querySelectorAll('.route-icon')].map(icon => getComputedStyle(icon).display !== 'none')"
    tab.wait(f"JSON.stringify({shown}) === JSON.stringify([false, true, false])")
    tab.js("map.setView([40.7, -8.5], 9, {animate: false}); 0")
    tab.wait(f"{shown}[0]")   # close to Coimbra, the train of the way to Porto
    assert tab.js("errors") == []

def test_old_map_opens_listed(tab, app, monkeypatch, tmp_path):
    """Leaflet comes from unpkg, as in the tests above."""
    from app import maps
    from app.tests.test_maps import a_saved_map
    from discover.view import page
    monkeypatch.setattr(page, "load_airports", lambda: {"BCN": {"city": "Barcelona", "name": "Barcelona Airport", "country": "ES",
                                                                "latitude": 41.3, "longitude": 2.1}})
    monkeypatch.setattr(maps, "SAVED_MAPS", tmp_path)
    a_saved_map(tmp_path)
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.call("Page.addScriptToEvaluateOnNewDocument", source="window.violations = []; window.errors = []; addEventListener('error', event => errors.push(event.message));"
             "document.addEventListener('securitypolicyviolation', event => violations.push(event.violatedDirective + ' ' + event.blockedURI));")
    tab.open(app.address + "discover")
    tab.js("document.querySelector('a[href^=\"/discover/saved/\"]').click()")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("document.querySelectorAll('#rows li').length === 1")
    assert tab.js("violations") == [] and tab.js("errors") == []

def test_route_follows_filters(tab, app, monkeypatch):
    """Leaflet comes from unpkg, as in the tests above."""
    from discover import finder
    from discover.view import page
    from discover.flights.sources import Days
    from trip_planner.trip.places import Place
    airports = {"BHX": {"city": "Birmingham", "name": "Birmingham Airport", "country": "GB", "latitude": 52.4, "longitude": -1.7},
                "OPO": {"city": "Porto", "name": "Porto Airport", "country": "PT", "latitude": 41.2, "longitude": -8.7}}
    monkeypatch.setattr(page, "load_airports", lambda: airports)
    leaving = date.today() + timedelta(days=30)
    leg = {"from": "OPO", "to": "BHX", "date": leaving.isoformat(), "time": "07:00", "arrival_date": leaving.isoformat(),
           "arrival_time": "09:45", "minutes": 165, "airline": "Ryanair", "stops": 0}
    coach = {"stop": "Birmingham Coach Station", "stop_id": "s", "lat": 52.47, "lon": -1.88, "minutes": 1500, "from_stop": "Coimbra",
             "from_stop_id": "c", "from_lat": 40.22, "from_lon": -8.44, "operator": "FlixBus", "site": "https://www.flixbus.pt",
             "sold": "flixbus", "modes": "COACH", "mode": "bus", "times": ["08:00"], "weekdays": ["Mon"], "grade": "B", "km": 0}
    flight = {**leg, "segments": [dict(leg)], "price_eur": 10, "carrier": "FR", "source": "Ryanair", "back": None,
              "ground_eur": 5.0, "total_eur": 15.0, "ground": [coach]}
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(leaving.isoformat(), leaving.isoformat()), [], [flight], [], [])
    app.post("/discover", f"token={app.token}&city=Coimbra&dates={written(leaving)}&source=light".encode())
    made = app.started[-1]
    made.read_event("map", page.map_page(found, "Ryanair", [], planner_link=made.map_link + "plan/"))
    made.read_event("state", page.map_state(found, []))
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.call("Page.addScriptToEvaluateOnNewDocument", source="window.errors = []; addEventListener('error', event => errors.push(event.message));")
    tab.open(app.address + "discover/map/1/")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("!!document.querySelector('#rows li')")
    icons = "[...document.querySelectorAll('.route-icon use')].map(use => use.getAttribute('href')).filter(href => href !== '#i-route').sort().join()"
    tab.js("document.querySelector('#rows li').click()")
    tab.wait(f"{icons} === '#i-plane'")   # both shown: the trip open is the flight's, and so is its route
    tab.js("document.querySelector('#filters input[type=checkbox]').click()")   # flights hidden
    tab.wait(f"{icons} === '#i-bus'")   # the trip open follows the filters at once
    tab.js("document.querySelector('#ground-rows tr').click()")   # opened again from the trains and buses
    tab.wait("document.querySelector('#detail h2').textContent.endsWith('Birmingham') && !!document.querySelector('#detail h4.ground-title')")
    assert tab.js(icons) == "#i-bus" and tab.js("errors") == []

def test_map_opens_when_ready(tab, app):
    leaving = date.today() + timedelta(days=30)
    tab.open(app.address + "discover")
    tab.js(f"document.querySelector('input[name=city]').value = 'Coimbra'; document.querySelector('input[name=dates]').value = '{written(leaving)}'")
    tab.js("document.querySelector('form[action=\"/discover\"]').requestSubmit()")
    tab.wait("location.pathname === '/discover' && !!document.querySelector('#live[data-working]')")
    made = app.started[-1]
    time.sleep(1)
    assert tab.js("location.pathname") == "/discover"   # its flights are still searched: nothing to open yet
    made.read_event("map", "<!doctype html><title>the map</title><p>the map")
    tab.wait("location.pathname === '/discover/map/1/'", seconds=10)
    tab.open(app.address + "discover")   # back to the form later: the map is not opened again
    time.sleep(1)
    assert tab.js("location.pathname") == "/discover" and tab.js("!!document.querySelector('a[data-open-map]')")

def test_ground_city_gets_card(tab, app, monkeypatch):
    """Leaflet comes from unpkg, as in the test above."""
    from discover import finder
    from discover.view import page
    from discover.flights.sources import Days
    from trip_planner.trip.places import Place
    airports = {"BHX": {"city": "Birmingham", "name": "Birmingham Airport", "country": "GB", "latitude": 52.4, "longitude": -1.7}}
    monkeypatch.setattr(page, "load_airports", lambda: airports)
    leaving = date.today() + timedelta(days=30)
    leg = {"from": "OPO", "to": "BHX", "date": leaving.isoformat(), "time": "07:00", "arrival_date": leaving.isoformat(),
           "arrival_time": "09:45", "minutes": 165, "airline": "Ryanair", "stops": 0}
    flight = {**leg, "segments": [dict(leg)], "price_eur": 10, "carrier": "FR", "source": "Ryanair", "back": None,
              "ground_eur": 5.0, "total_eur": 15.0}
    option = {"stop": "Salamanca", "stop_id": "s", "lat": 40.96, "lon": -5.66, "minutes": 269, "from_stop": "Coimbra", "from_stop_id": "c",
              "operator": "FlixBus", "site": "https://www.flixbus.pt", "sold": "flixbus", "modes": "COACH", "mode": "bus",
              "times": ["08:00", "12:00"], "weekdays": ["Mon", "Fri"], "grade": "B", "km": 0, "from_lat": 40.22, "from_lon": -8.44}
    salamanca = {"key": "ES|Salamanca|40.96", "name": "Salamanca", "country": "ES", "lat": 40.96, "lon": -5.66, "minutes": 269,
                 "modes": ["bus"], "ground": [option], "fare_eur": 9.0}
    found = finder.Found(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43),
                         Days(leaving.isoformat(), leaving.isoformat()), [], [flight], [], [], by_ground=[salamanca])
    app.post("/discover", f"token={app.token}&city=Coimbra&dates={written(leaving)}&source=light".encode())
    made = app.started[-1]
    made.read_event("map", page.map_page(found, "Ryanair", [], planner_link=made.map_link + "plan/"))
    made.read_event("state", page.map_state(found, []))
    tab.call("Network.enable")
    tab.call("Network.setBlockedURLs", urls=["*tile.openstreetmap.org*"])
    tab.call("Page.addScriptToEvaluateOnNewDocument", source="window.errors = []; addEventListener('error', event => errors.push(event.message));")
    tab.open(app.address + "discover/map/1/")
    try:
        tab.wait("typeof L === 'object'", seconds=15)
    except AssertionError:
        pytest.skip("Leaflet could not be fetched from unpkg")
    tab.wait("document.querySelectorAll('#rows li').length === 2")
    assert tab.js("[...document.querySelectorAll('#rows li strong')].map(name => name.textContent)")[0] == "Salamanca"   # 9 EUR before 15
    tab.js("document.querySelector('#rows li').click()")
    assert tab.js("errors") == []
    tab.wait("document.querySelector('#rows li').classList.contains('picked') && !document.getElementById('detail').hidden")
    # Its route on the map: from Coimbra's coach station to Salamanca's in the colour of its grade, with a bus on it
    assert tab.js("data.grade_colours.B") in tab.js("[...document.querySelectorAll('.leaflet-overlay-pane path')].map(path => path.getAttribute('stroke'))")
    assert tab.js("[...document.querySelectorAll('.route-icon use')].map(use => use.getAttribute('href'))") == ["#i-bus"]
