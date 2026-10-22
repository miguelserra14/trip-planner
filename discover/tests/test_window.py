"""The discover window (window.py): the days of its form are checked, and what the map's code prints reaches it. No network,
no window opened."""
import queue
from datetime import date

import pytest

from discover import window
from discover.sources import Days

TODAY = date(2026, 10, 22)

def test_the_days_of_the_form_are_read_and_put_in_order():
    assert window.read_days("19/11/2026", "12/11/2026", today=TODAY) == Days("2026-11-12", "2026-11-19")
    assert window.read_days("12/11/2026", "", today=TODAY) == Days("2026-11-12", "2026-11-12")   # one day: that day only
    assert window.read_days("12/11/2026", "14/11/2026", "17/11/2026", "15/11/2026", today=TODAY) == \
        Days("2026-11-12", "2026-11-14", "2026-11-15", "2026-11-17")

def test_a_day_that_is_wrong_says_what_is_wrong():
    for wrong, why in ((("2026-11-12", ""), "dd/mm/yyyy"), (("01/10/2026", ""), "already past"),
                       (("12/11/2026", "14/11/2026", "01/11/2026", ""), "before the days to leave on")):
        with pytest.raises(ValueError, match=why):
            window.read_days(*wrong, today=TODAY)

def test_what_the_maps_code_prints_becomes_lines_of_the_window():
    lines = queue.Queue()
    writer = window.LineWriter(lines)
    writer.write("Kiwi: 20 countries")
    assert lines.empty()   # not a whole line yet
    writer.write(" for each airport\n\nRyanair answered\n")
    assert [lines.get_nowait()[1] for _ in range(lines.qsize())] == ["Kiwi: 20 countries for each airport", "Ryanair answered"]
