"""The window holds every setting: the main page a few, as the first page of a flight search site, and the advanced
settings all the others. No window is opened.

- Every setting of TripSettings can be changed in the window, and only in one place (the main page's ticks hold the
  same variables as the advanced settings, so they are the same place).
- Every label and hint of the advanced settings exists in the texts.
- What the window shows of a setting is read back as the same setting."""
import dataclasses
from datetime import date, timedelta

from trip_planner.trip.trip_settings import TripSettings
from trip_planner.gui import AdvancedSettings, CHANGES, MAIN_SETTINGS, NOT_IN_WINDOW, shown
from trip_planner.texts import REFERENCE, TEXTS

IN_TABS = [name for title, names in AdvancedSettings.TABS for name in names]

def test_every_setting_is_somewhere_in_the_window_and_once_in_the_tabs():
    shown_anywhere = set(IN_TABS) | set(MAIN_SETTINGS) | set(CHANGES) | set(NOT_IN_WINDOW)
    assert {setting.name for setting in dataclasses.fields(TripSettings)} == shown_anywhere
    assert len(IN_TABS) == len(set(IN_TABS))

def test_every_label_and_hint_has_its_text():
    keys = [AdvancedSettings.LABELS.get(name, f"set_{name}") for name in IN_TABS] + list(AdvancedSettings.HINTS.values())
    keys += [title for title, names in AdvancedSettings.TABS]
    for language in (REFERENCE, "pt-PT"):
        assert [key for key in keys if key not in TEXTS[language]] == [], language

def test_what_the_window_shows_is_read_back_as_the_same_setting():
    day = f"{date.today() + timedelta(days=30):%d/%m/%Y}"
    settings = TripSettings(origin="Coimbra", destination="Antwerp", dates=[day], exclude_airports=["LIS", "Faro"],
                            earliest_leave="", latest_leave="", tiebreak_eur_per_hour=7.5)
    values = {setting.name: shown(setting.name, getattr(settings, setting.name))
              for setting in dataclasses.fields(TripSettings) if setting.name not in NOT_IN_WINDOW}
    assert values["exclude_airports"] == "LIS, Faro" and values["earliest_leave"] == "" and values["tiebreak_eur_per_hour"] == "7.5"
    assert dataclasses.replace(settings, **values) == settings
