"""The discover window (window.py): the days of its form are checked, and what the map's code prints reaches it. No network,
no window opened."""
import queue
import threading
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from discover import confirm, ground, ground_fares, session, sources, window
from discover.sources import Days

TODAY = date(2026, 10, 22)

def test_the_days_of_the_form_are_read_and_put_in_order():
    assert window.read_days("14/11/2026, 12/11/2026, 13/11/2026", today=TODAY) == Days("2026-11-12", "2026-11-14")   # all in a row
    assert window.read_days("12/11/2026", today=TODAY) == Days("2026-11-12", "2026-11-12")
    assert window.read_days("12/11/2026, 14/11/2026", "17/11/2026, 15/11/2026, 16/11/2026", today=TODAY) ==         Days("2026-11-12", "2026-11-14", "2026-11-15", "2026-11-17", out_days=("2026-11-12", "2026-11-14"))

def test_a_day_that_is_wrong_says_what_is_wrong():
    for wrong, why in ((("2026-11-12",), "dd/mm/yyyy"), (("01/10/2026",), "already past"), (("",), "at least one day"),
                       (("12/11/2026, 14/11/2026", "01/11/2026"), "before the days to leave on")):
        with pytest.raises(ValueError, match=why):
            window.read_days(*wrong, today=TODAY)

def test_what_the_maps_code_prints_becomes_lines_of_the_window():
    lines = queue.Queue()
    writer = window.LineWriter(lines)
    writer.write("Kiwi: 20 countries")
    assert lines.empty()   # not a whole line yet
    writer.write(" for each airport\n\nRyanair answered\n")
    assert [lines.get_nowait()[1] for _ in range(lines.qsize())] == ["Kiwi: 20 countries for each airport", "Ryanair answered"]

def test_days_not_in_a_row_are_asked_a_run_at_a_time_and_the_others_left_out():
    days = Days.clicked(["2026-11-12", "2026-11-13", "2026-11-20"], ["2026-11-16", "2026-11-24"])
    assert days.runs() == [Days("2026-11-12", "2026-11-13", "2026-11-16", "2026-11-16"), Days("2026-11-12", "2026-11-13", "2026-11-24", "2026-11-24"),
                           Days("2026-11-20", "2026-11-20", "2026-11-24", "2026-11-24")]   # not back on the 16th from the 20th
    assert days.allows({"date": "2026-11-13", "back": {"date": "2026-11-24"}})
    assert not days.allows({"date": "2026-11-15", "back": {"date": "2026-11-24"}})   # a day between the ones clicked
    assert not days.allows({"date": "2026-11-13", "back": {"date": "2026-11-20"}})

def test_beyond_two_runs_the_closest_are_joined():
    assert sources.runs_of(["2026-11-01", "2026-11-03", "2026-11-04", "2026-11-10"]) == [("2026-11-01", "2026-11-04"), ("2026-11-10", "2026-11-10")]

def test_every_map_made_stays_served_until_the_window_closes():
    stopped = []

    class Map:
        def stop(self):
            stopped.append(self)
    shown = object.__new__(window.DiscoverWindow)
    shown.sessions, shown.root = [Map(), Map()], type("Root", (), {"destroy": lambda self: None})()
    shown.close()
    assert stopped == shown.sessions

def test_a_map_asked_for_while_the_last_one_is_at_work_starts_once_that_one_stops():
    shown = object.__new__(window.DiscoverWindow)
    form = lambda value: SimpleNamespace(get=lambda: value)
    leaving = f"{date.today() + timedelta(days=10):%d/%m/%Y}"
    shown.city, shown.out_days, shown.back_days, shown.round_trip = form("Porto"), form(leaving), form(""), form(False)
    shown.sources, shown.look_up = form("fast"), form(True)
    said, started = [], []
    shown.say = lambda text, error=False: said.append(text)
    shown.start = lambda session, look_up: started.append(session)
    last = SimpleNamespace(stopping=threading.Event())
    shown.session, shown.working, shown.waiting = last, True, None
    shown.open_map()
    assert last.stopping.is_set() and not started   # asked to stop, the new map not started yet
    shown.finish("")
    assert [session.city for session in started] == ["Porto"] and shown.waiting is None and not shown.working

def test_a_map_asked_to_stop_ends_its_lookups_at_the_next_city_and_keeps_what_it_found(monkeypatch):
    made = object.__new__(session.Session)
    made.found, made.planner, made.days, made.sections = SimpleNamespace(phase=""), None, None, []
    made.live, made.lock, made.stopping = {"state": {}}, threading.Lock(), threading.Event()
    saved, cities = [], []
    made.save = lambda: saved.append(made.found.phase)

    def price(found, on_change):
        for city in ("Madrid", "Paris"):
            cities.append(city)
            made.stopping.set()   # another map is asked for while the first city is priced
            on_change(found)
    monkeypatch.setattr(ground_fares, "price", price)
    monkeypatch.setattr(confirm, "confirm", lambda *args: pytest.fail("the timetables were looked up after the map was asked to stop"))
    monkeypatch.setattr(ground, "ways_back", lambda *args: pytest.fail("the ways back were looked up after the map was asked to stop"))
    monkeypatch.setattr(session, "map_state", lambda *args, **kwargs: {"running": kwargs["running"]})
    monkeypatch.setattr(session, "breakdown", lambda found: [])
    monkeypatch.setattr(session, "load_airports", lambda: {})
    made.look_up()
    assert cities == ["Madrid"] and saved == [""] and made.live["state"] == {"running": False}
