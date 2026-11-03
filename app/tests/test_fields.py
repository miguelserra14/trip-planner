"""The form of a trip (fields.py, reading trip/form.py). No network.

- Every setting of TripSettings is on the form, and only in one place; every label and hint has its text.
- Whatever a setting is changed to, what the browser sends is that setting, as TripSettings has it."""
import dataclasses
from datetime import date, timedelta

import pytest

from app import fields, job
from settings import SETTINGS
from trip_planner.texts import REFERENCE, TEXTS
from trip_planner.trip import form
from trip_planner.trip.ground_choice import NO_CHANGE_LIMIT
from trip_planner.trip.trip_settings import TripSettings

DAY = f"{date.today() + timedelta(days=30):%d/%m/%Y}"
NEXT = f"{date.today() + timedelta(days=31):%d/%m/%Y}"
BACK = f"{date.today() + timedelta(days=40):%d/%m/%Y}"
# A value for every setting of the form other than the one settings.py has, each within its range
CHANGED = dict(
    earliest_leave="06:00", latest_leave="22:00", earliest_arrival="08:00", latest_arrival="23:00", next_day_arrivals=False,
    min_airport_minutes=100, ideal_airport_minutes=150, min_landing_minutes=30, confirm_airports=False,
    primary_radius_km=80, fallback_radius_km=250, arrival_radius_km=150, max_departure_airports=8, max_arrival_airports=4,
    max_road_hours=4.5, include_airports=["Porto"], exclude_airports=["BJZ", "SLM"], primary_airports=[], fallback_airports=["SCQ"],
    arrival_airports=["BRU"], to_airport_by="train", max_changes_to_airport=2, from_airport_by="bus", max_changes_from_airport=0,
    free_trains_at_origin=True, free_trains_at_destination=True, allow_estimated_prices=False, max_estimate_km=30,
    small_estimate_eur=3.5, unverified_price_factor=2.5, city_walk_minutes=30, wide_city_walk_minutes=60, airport_walk_minutes=15,
    min_reliability="A", tiebreak_eur_per_hour=7.5, fallback_saving_eur_per_hour=12.0, extra_hour_growth=2.0, options_shown=7,
    extra_wishes="a window seat, and no night bus", airport_floor_minutes=40, night_minutes=200, min_trip_km=60,
    city_airport_radius_km=40, ambiguous_importance=0.3, recent_reports=8, use_flight_agent=True,
    departure_scale="near", arrival_scale="near", max_stopovers=2,
)

def as_browser_sends(values: dict) -> dict[str, str]:
    """The form showing these values, as a browser sends it: a ticked box as "on", an unticked one not at all."""
    sent = {name: str(value) for name, value in values.items() if not isinstance(value, bool) and name not in fields.SCALES_TICKED}
    sent.update({name: "on" for name, value in values.items() if value is True})
    sent.update({name: "on" for name in fields.SCALES_TICKED if values[name] == "wide"})
    if str(values["max_stopovers"]) == "0":
        sent[fields.DIRECT] = "on"
    return sent

def read_back(settings):
    return job.settings_for(fields.read(as_browser_sends(fields.values_of(settings)), SETTINGS), SETTINGS)

IN_TABS = [name for title, names in form.TABS for name in names]

def test_every_setting_is_somewhere_on_the_form_and_once_in_the_tabs():
    shown_anywhere = set(IN_TABS) | set(form.MAIN_SETTINGS) | set(form.CHANGES) | set(form.NOT_IN_TABS)
    assert {setting.name for setting in dataclasses.fields(TripSettings)} == shown_anywhere
    assert len(IN_TABS) == len(set(IN_TABS))

def test_every_label_and_hint_has_its_text():
    keys = [form.LABELS.get(name, f"set_{name}") for name in IN_TABS] + list(form.HINTS.values())
    keys += [title for title, names in form.TABS]
    for language in (REFERENCE, "pt-PT"):
        assert [key for key in keys if key not in TEXTS[language]] == [], language

def test_what_the_form_shows_is_read_back_as_the_same_setting():
    settings = TripSettings(origin="Coimbra", destination="Antwerp", dates=[DAY], exclude_airports=["LIS", "Faro"],
                            earliest_leave="", latest_leave="", tiebreak_eur_per_hour=7.5)
    values = {setting.name: form.shown(setting.name, getattr(settings, setting.name))
              for setting in dataclasses.fields(TripSettings) if setting.name not in form.NOT_IN_TABS}
    assert values["exclude_airports"] == "LIS, Faro" and values["earliest_leave"] == "" and values["tiebreak_eur_per_hour"] == "7.5"
    assert dataclasses.replace(settings, **values) == settings

def test_every_setting_of_the_form_is_changed_by_it():
    assert set(CHANGED) == {*fields.ADVANCED, *fields.SCALES_TICKED}   # a setting added to the form is added here
    wanted = dataclasses.replace(SETTINGS, origin="Coimbra", destination="Antwerp", dates=[DAY, NEXT], return_dates=[BACK],
                                 language="en", **CHANGED)
    assert {name: getattr(wanted, name) for name in CHANGED} != {name: getattr(SETTINGS, name) for name in CHANGED}
    assert vars(read_back(wanted)) == vars(wanted)

@pytest.mark.parametrize("stopovers", [0, 1, 3])
def test_the_stopovers_are_read_whether_direct_flights_are_ticked_or_not(stopovers):
    wanted = dataclasses.replace(SETTINGS, origin="Coimbra", destination="Antwerp", dates=[DAY], max_stopovers=stopovers)
    assert read_back(wanted).max_stopovers == stopovers

def test_unticking_direct_flights_allows_a_stopover():
    assert fields.stopovers({"max_stopovers": "0"}) == 1 and fields.stopovers({"max_stopovers": "0", fields.DIRECT: "on"}) == 0

def test_no_limit_of_changes_is_kept_as_no_limit():
    wanted = dataclasses.replace(SETTINGS, origin="Coimbra", destination="Antwerp", dates=[DAY],
                                 max_changes_to_airport=NO_CHANGE_LIMIT, max_changes_from_airport=NO_CHANGE_LIMIT)
    settings = read_back(wanted)
    assert settings.max_changes_to_airport == settings.max_changes_from_airport == NO_CHANGE_LIMIT

def test_days_written_in_words_are_read_as_the_window_reads_them():
    sent = as_browser_sends(fields.values_of(SETTINGS))
    sent.update(origin="Coimbra", destination="Antwerp", dates="17-19/12")
    settings = job.settings_for(fields.read(sent, SETTINGS), SETTINGS)
    assert [day[:5] for day in settings.dates] == ["17/12", "18/12", "19/12"]

@pytest.mark.parametrize("name, value", [("min_airport_minutes", "10"), ("max_stopovers", "9"), ("earliest_leave", "25:00"),
                                         ("max_road_hours", "many"), ("min_reliability", "Z")])
def test_a_value_out_of_range_is_refused_by_the_settings_with_the_reason(name, value):
    sent = as_browser_sends(fields.values_of(SETTINGS))
    sent.update(origin="Coimbra", destination="Antwerp", dates=DAY, **{name: value})
    sent.pop(fields.DIRECT, None)
    with pytest.raises(ValueError) as refused:
        job.settings_for(fields.read(sent, SETTINGS), SETTINGS)
    assert str(refused.value)   # in words, not a key
