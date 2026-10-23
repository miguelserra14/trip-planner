"""The window: the trip is typed on a main page like the first page of a flight search site (where from, where to,
when, and three ticks), every other setting is behind the advanced settings button, the airports can be reviewed in
a list, and the progress, the result and the follow-up questions all happen in the same window. Nothing is asked in
the terminal.

The planner itself does not change. It runs on its own thread (an asyncio loop), and what it says and shows
(`say` and `show` in utils.py) is handed to the window through a queue, because only the main thread may touch it.

The package, part by part:
- window.py: TripWindow, the main window, and run, which opens it.
- output.py: the text where the progress and the result are written, with the Markdown of the reports.
- advanced.py: the advanced settings, and how the window shows a setting.
- dialogs.py: the small windows: which place a name means, the calendar, the languages.
- style.py: the colours, the fonts and the styles of the widgets."""
from trip_planner.gui.advanced import AdvancedSettings, CHANGES, MAIN_SETTINGS, NOT_IN_WINDOW, shown
from trip_planner.gui.dialogs import DatePicker
from trip_planner.gui.style import style
from trip_planner.gui.window import run

# What the planner, the draft in discover/ and the tests read as gui.<name>
__all__ = ["AdvancedSettings", "CHANGES", "DatePicker", "MAIN_SETTINGS", "NOT_IN_WINDOW", "run", "shown", "style"]
