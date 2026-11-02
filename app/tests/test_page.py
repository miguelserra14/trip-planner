"""The page in a real browser (page.js, and the pages without it), run with `pytest --browser`: the calendar, the
part of a running search put in place without a reload, the language, a browser without scripts, two tabs and the
back button. The searches are stood in for (test_search.StandIn): no network but this computer's own address."""
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

def test_the_calendar_opens_on_a_click_and_stays_open_while_its_months_are_turned(tab):
    calendar_of(tab)
    assert tab.js("document.querySelectorAll('.calendar .month button')[0].disabled")   # no month before this one
    turn(tab, 13)
    assert tab.js("!!document.querySelector('.calendar')")
    assert tab.js("document.querySelector('.calendar .month span').textContent").endswith(str(date.today().year + 1))

def test_the_calendar_closes_on_a_click_outside_or_escape(tab):
    calendar_of(tab)
    tab.js("document.querySelector('h1').click()")
    assert tab.js("!document.querySelector('.calendar')")
    calendar_of(tab)
    tab.js("document.querySelector('input[name=dates]').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert tab.js("!document.querySelector('.calendar')")

def test_a_click_writes_the_day_and_a_second_one_takes_it_away(tab):
    calendar_of(tab)
    turn(tab, 1)
    click_day(tab, 10)
    click_day(tab, 12)
    first = date.today().replace(day=1) + timedelta(days=32)
    assert field(tab) == f"{written(first.replace(day=10))}, {written(first.replace(day=12))}"
    click_day(tab, 10)
    assert field(tab) == written(first.replace(day=12))

def test_at_most_seven_days_are_chosen(tab):
    calendar_of(tab)
    turn(tab, 1)
    for number in range(1, 11):
        click_day(tab, number)
    assert len(field(tab).split(", ")) == 7

def test_days_that_cannot_be_searched_cannot_be_clicked(tab):
    calendar_of(tab)
    today = date.today()
    if today.day > 1:
        assert tab.js("[...document.querySelectorAll('.calendar td button')].find(b => b.textContent === '1').disabled")
    turn(tab, 12)   # past the days airlines sell
    assert tab.js("[...document.querySelectorAll('.calendar td button')].pop().disabled")

def test_the_way_back_cannot_start_before_the_way_there(tab):
    going = date.today().replace(day=1) + timedelta(days=62)
    going = going.replace(day=15)
    tab.js(f"document.querySelector('input[name=dates]').value = '{written(going)}'")
    calendar_of(tab, "return_dates")
    assert tab.js("document.querySelector('.calendar .month span').textContent").endswith(str(going.year))
    assert tab.js("[...document.querySelectorAll('.calendar td button')].find(b => b.textContent === '14').disabled")
    assert not tab.js("[...document.querySelectorAll('.calendar td button')].find(b => b.textContent === '15').disabled")

def test_days_written_in_words_are_kept_when_a_day_is_clicked_and_clear_takes_only_the_clicked(tab):
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

def test_a_running_search_is_put_in_place_without_a_reload_and_then_left_alone(tab, app):
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

def test_the_progress_stays_open_or_closed_as_the_traveller_left_it(tab, app):
    search = start_search(tab, app)
    search.shown.append("# Final result")   # with a result the progress starts closed
    tab.wait("!!document.querySelector('#live article')")
    tab.js("document.querySelector('#live details').open = true")
    search.lines.append("Ground transport: 16 of 16")
    tab.wait("document.querySelector('#live').textContent.includes('16 of 16')")
    assert tab.js("document.querySelector('#live details').open")

def test_a_question_asked_by_the_search_appears_without_a_reload(tab, app):
    search = start_search(tab, app)
    search.asked = ("airports", {"groups": [{"title": "Arrival", "airports": [
        {"code": "BRU", "km": 36, "name": "Brussels", "city": "Zaventem", "country": "BE"}]}], "notes": []})
    tab.wait("!!document.querySelector('form[action=\"/airports\"]')")
    assert tab.js("window.sameLoad === true")
    tab.submit("form[action='/airports']")
    assert search.answers[-1] == {"dropped": []}

# ---------------------------------------------------------------- the language, a browser without scripts, tabs

def test_the_language_chosen_is_used_at_once(tab):
    tab.js("const choice = document.querySelector('select[data-submit]'); choice.value = 'en'; choice.dispatchEvent(new Event('change'))")
    tab.loaded()
    assert tab.js("document.documentElement.lang") == "en" and tab.js("document.querySelector('h1').textContent") == "Plan a trip"

def test_without_scripts_the_form_works_and_a_running_search_refreshes_itself(tab, app):
    tab.call("Emulation.setScriptExecutionDisabled", value=True)
    tab.open(app.address)
    assert tab.js("!document.querySelector('.calendar')")
    start_search(tab, app).lines.append("Flights: 26 found")
    began = tab.js("performance.timeOrigin")
    time.sleep(4.5)   # the page asks for itself again after 3 s
    assert tab.js("performance.timeOrigin") != began and "Flights: 26 found" in tab.js("document.body.textContent")
    tab.call("Emulation.setScriptExecutionDisabled", value=False)

def test_a_second_tab_sees_the_search_running_and_can_stop_it(tab, app, browser):
    search = start_search(tab, app)
    other = browser.tab()
    try:
        other.open(app.address)
        assert other.js("!document.querySelector('form.search button[type=submit]')")   # one search at a time
        other.open(app.address + "search")
        other.submit("form[action='/stop']")
        assert search.stopped
    finally:
        other.call("Page.close")

def test_back_from_a_running_search_shows_the_form_busy(tab, app):
    start_search(tab, app)
    tab.js("history.back()")
    tab.loaded()
    assert tab.js("location.pathname") == "/"
    assert tab.js("!!document.querySelector('form.search a[href=\"/search\"]')")
