"""How a form shows the settings of a trip (TripSettings): which are on its main page and which are advanced, in
which tab, under which label, with which hint, and a value as the form writes it. The app's form (app/fields.py)
reads it.

The main page holds what the first page of a flight search site holds: where from, where to, when, and three ticks
(airports near each city, direct flights only). Every other setting is advanced."""
import re
from typing import Any

from trip_planner.texts import REFERENCE, TEXTS, tr

MAIN_SETTINGS = ("origin", "destination", "departure_scale", "arrival_scale", "max_stopovers")
CHANGES = ("max_changes_to_airport", "max_changes_from_airport")   # shown with how to travel on each side
NOT_IN_TABS = ("dates", "return_dates", "language")   # the two calendars and the list of languages hold these
TABS = (
    ("ui_tab_times", ("earliest_leave", "latest_leave", "earliest_arrival", "latest_arrival", "next_day_arrivals",
                      "max_stopovers", "min_airport_minutes", "ideal_airport_minutes", "min_landing_minutes")),
    ("ui_tab_airports", ("departure_scale", "arrival_scale", "confirm_airports", "primary_radius_km", "fallback_radius_km",
                         "arrival_radius_km", "max_departure_airports", "max_arrival_airports", "max_road_hours")),
    ("ui_tab_airport_lists", ("include_airports", "exclude_airports", "primary_airports", "fallback_airports", "arrival_airports")),
    ("ui_tab_ground", ("to_airport_by", "max_changes_to_airport", "from_airport_by", "max_changes_from_airport",
                       "free_trains_at_origin", "free_trains_at_destination")),
    ("ui_tab_estimates", ("allow_estimated_prices", "max_estimate_km", "small_estimate_eur", "unverified_price_factor",
                          "city_walk_minutes", "wide_city_walk_minutes", "airport_walk_minutes")),
    ("ui_tab_preferences", ("min_reliability", "tiebreak_eur_per_hour", "fallback_saving_eur_per_hour", "extra_hour_growth",
                            "options_shown", "extra_wishes")),
    ("ui_tab_program", ("airport_floor_minutes", "night_minutes", "min_trip_km", "city_airport_radius_km",
                        "ambiguous_importance", "recent_reports")),
)
# The settings with a label of their own; the others are called set_<name> in the texts
LABELS = {"earliest_leave": "ui_earliest_leave", "latest_leave": "ui_latest_leave", "latest_arrival": "ui_latest_arrival",
          "max_stopovers": "ui_stopovers", "min_reliability": "ui_reliability", "departure_scale": "ui_departure_scale",
          "arrival_scale": "ui_arrival_scale", "free_trains_at_origin": "ui_free_trains_origin",
          "free_trains_at_destination": "ui_free_trains", "to_airport_by": "ui_to_airport_by", "from_airport_by": "ui_from_airport_by"}
HINTS = {**dict.fromkeys(("earliest_leave", "latest_leave", "earliest_arrival", "latest_arrival"), "hint_times"),
         **dict.fromkeys(("include_airports", "exclude_airports", "primary_airports", "fallback_airports", "arrival_airports"), "hint_airport_list"),
         **dict.fromkeys(("to_airport_by", "from_airport_by", *CHANGES), "hint_ground_choice"),
         **{name: f"hint_{name}" for name in ("min_reliability", "tiebreak_eur_per_hour", "fallback_saving_eur_per_hour",
                                              "extra_hour_growth", "unverified_price_factor", "max_estimate_km", "extra_wishes")}}

def label(name: str) -> str:
    """A setting as the form names it, in the traveller's language. Its own name when it has no label."""
    key = LABELS.get(name, f"set_{name}")
    return tr(key) if key in TEXTS[REFERENCE] else name

def named(reason: str) -> str:
    """Why a setting was refused, with each setting named as the form names it: SettingError names it as settings.py
    does, `fallback_radius_km`."""
    return re.sub(r"`([a-z_]+)`", lambda name: f"«{label(name[1])}»", reason)

def shown(name: str, value: Any) -> Any:
    """A setting as the form shows it: a list as text with commas, a whole day without its times, 5.0 as 5."""
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, float):
        return f"{value:g}"
    if (name, value) in (("earliest_leave", "00:00"), ("latest_leave", "23:59")):
        return ""
    return str(value)

def same(first: Any, second: Any) -> bool:
    """Whether two values of a setting are the same, "5" and "5.0" included."""
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return first == second
