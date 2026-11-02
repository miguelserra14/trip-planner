"""The form of a trip, as the window has it (trip_planner/gui): the main card (where from, where to, when, and three
ticks) and every other setting behind "Advanced settings", in the same tabs, with the same labels and hints. What
the form sends is read back into the values TripSettings takes; TripSettings stays the only check.

The page works without its script: the dates are typed as TripSettings reads them ("17/12/2026, 17-19/12, Friday"),
and the script (page.js) only adds the calendar to click them in."""
import dataclasses
import html
from typing import Any

from trip_planner.gui.advanced import AdvancedSettings, CHANGES, MAIN_SETTINGS, shown
from trip_planner.texts import LANGUAGES, REFERENCE, TEXTS, tr
from trip_planner.trip.ground_choice import CHOICES, NO_CHANGE_LIMIT
from trip_planner.trip.trip_settings import GRADES, LIGHTS, MAX_DATES, MAX_DAYS_AHEAD, RANGES, SCALES, TripSettings

# The settings of the trip the form sends, besides the advanced ones: the calendars and the language hold these
TRIP = ("origin", "destination", "dates", "return_dates", "language")
# Every setting behind "Advanced settings", tab by tab. The main card's ticks hold the two scales, near or wide. The
# stopovers are in both, as in the window: the tick for direct flights only is 0, and without it the number is used
SCALES_TICKED = ("departure_scale", "arrival_scale")
TABS = [(title, [name for name in names if name not in SCALES_TICKED]) for title, names in AdvancedSettings.TABS]
ADVANCED = [name for _, names in TABS for name in names]
DIRECT = "direct"   # the tick for direct flights only

def stopovers(sent: dict[str, str]) -> Any:
    """The stopovers a form sent: none with the tick for direct flights, otherwise the number, which is then at least
    one (unticking direct flights allows a stopover). Anything else goes to TripSettings as it is, to be refused."""
    if DIRECT in sent:
        return 0
    number = sent.get("max_stopovers", "").strip()
    return 1 if number in ("", "0") else number

def kind_of(name: str) -> type:
    return {setting.name: setting.type for setting in dataclasses.fields(TripSettings)}[name]

def values_of(settings: TripSettings) -> dict[str, Any]:
    """What the form starts with: every setting as the form shows it."""
    values = {name: shown(name, getattr(settings, name)) for name in (*TRIP, *ADVANCED, *MAIN_SETTINGS)}
    values["language"] = settings.language
    for name in CHANGES:   # no limit is said in words
        if int(getattr(settings, name)) >= NO_CHANGE_LIMIT:
            values[name] = "any"
    return values

def read(sent: dict[str, str], defaults: TripSettings) -> dict[str, Any]:
    """The settings a form sent, as TripSettings takes them. A tick not sent is a tick off: the browser sends only
    the ticked ones. A setting the form does not hold keeps the default."""
    values: dict[str, Any] = {name: sent.get(name, "").strip() for name in TRIP}
    for name in ADVANCED:
        if kind_of(name) is bool:
            values[name] = name in sent
        elif name in CHANGES:
            values[name] = int(sent[name]) if sent.get(name, "").isdigit() else NO_CHANGE_LIMIT
        elif name in sent:
            values[name] = sent[name].strip()
    for name in SCALES_TICKED:
        values[name] = "wide" if name in sent else "near"
    values["max_stopovers"] = stopovers(sent)
    values["language"] = values["language"] or defaults.language
    return values

def label(name: str) -> str:
    return AdvancedSettings.label(name)

def hint(name: str) -> str:
    key = AdvancedSettings.HINTS.get(name)
    return tr(key) if key else ""

def choice(name: str, value: str, options: dict[str, str]) -> str:
    items = "".join(f'<option value="{html.escape(option)}"{" selected" if option == value else ""}>{html.escape(words)}</option>'
                    for option, words in options.items())
    return f'<select name="{name}">{items}</select>'

def field(name: str, value: Any, defaults: TripSettings) -> str:
    """One advanced setting: its label, the way to change it, and what it means under it."""
    escape = lambda text: html.escape(str(text))
    if kind_of(name) is bool:
        widget = f'<input type="checkbox" name="{name}"{" checked" if value else ""}>'
        return f'<label class="tick">{widget} {escape(label(name))}{f"<small>{escape(hint(name))}</small>" if hint(name) else ""}</label>'
    if name in ("to_airport_by", "from_airport_by"):
        widget = choice(name, value, {option: tr(f"ui_choice_{option}") for option in CHOICES})
    elif name in CHANGES:
        widget = choice(name, str(value), {**{str(number): str(number) for number in range(NO_CHANGE_LIMIT)}, "any": tr("ui_changes_any")})
    elif name == "min_reliability":   # by its colour, as the reports show it, and its words for who does not see colours
        widget = choice(name, value, {grade: f"{LIGHTS[grade]} {tr('grade_' + grade.lower() + '_long')}" for grade in GRADES})
    elif name in RANGES:
        lowest, highest = RANGES[name]
        step = 1 if kind_of(name) is int else "any"
        widget = f'<input type="number" name="{name}" value="{escape(value)}" min="{lowest}" max="{highest}" step="{step}">'
    else:   # a time, a list of airports, or free text
        widget = f'<input name="{name}" value="{escape(value)}">'
    return f'<label>{escape(label(name))}{widget}{f"<small>{escape(hint(name))}</small>" if hint(name) else ""}</label>'

def calendar_data() -> str:
    """What the calendar of a form's "dates" and "return_dates" fields needs (page.js): its words and its limits."""
    calendar = {"months": tr("cal_months"), "weekdays": tr("cal_weekdays"), "clear": tr("ui_calendar_clear"),
                "hint": tr("ui_calendar_hint", maximum=MAX_DATES), "return-hint": tr("ui_calendar_return_hint", maximum=MAX_DATES),
                "today-limit": MAX_DAYS_AHEAD, "most-dates": MAX_DATES}
    return "".join(f' data-{name}="{html.escape(str(value))}"' for name, value in calendar.items())

def trip_form(token: str, values: dict[str, Any], defaults: TripSettings, refusal: str = "", busy: bool = False) -> str:
    """The whole form: the main card first, the advanced settings folded under it."""
    escape = lambda text: html.escape(str(text))
    entry = lambda name, key, extra="": (f'<label>{escape(tr(key))}<input name="{name}" value="{escape(values.get(name, ""))}"{extra}>'
                                         f"</label>")
    tick = lambda name, key, on: f'<label class="tick"><input type="checkbox" name="{name}"{" checked" if on else ""}> {escape(tr(key))}</label>'
    dates = lambda name, key, hint_key: (
        f'<label>{escape(tr(key))}<input name="{name}" value="{escape(values.get(name, ""))}" data-calendar="{name}"'
        f'{" required" if name == "dates" else ""}><small>{escape(tr(hint_key, maximum=MAX_DATES))}</small></label>')
    languages = {code: TEXTS[code].get("language_name", code) for code in LANGUAGES}
    tabs = "".join(f'<fieldset><legend>{escape(tr(title))}</legend>{"".join(field(name, values.get(name), defaults) for name in names)}</fieldset>'
                   for title, names in TABS if names)
    error = f'<p class="refusal" role="alert">{escape(refusal)}</p>' if refusal else ""
    button = (f'<a href="/search">{escape(tr("app_search_running"))}</a>' if busy
              else f'<button type="submit">{escape(tr("ui_search"))}</button>')
    return (f'<form method="post" action="/search" class="search"{calendar_data()}>'
            f'<h1>{escape(tr("app_plan_title"))}</h1>{error}'
            f'<input type="hidden" name="token" value="{escape(token)}">'
            f'{entry("origin", "ui_from", " required")}{entry("destination", "ui_to", " required")}'
            f'{dates("dates", "ui_dates", "app_dates_hint")}{dates("return_dates", "ui_return_dates", "app_return_dates_hint")}'
            f'<div class="ticks">{tick("departure_scale", "app_nearby_origin", values.get("departure_scale") == "wide")}'
            f'{tick("arrival_scale", "app_nearby_destination", values.get("arrival_scale") == "wide")}'
            f'{tick(DIRECT, "ui_direct", str(values.get("max_stopovers")) == "0")}</div>'
            f'<label>{escape(tr("ui_language"))}{choice("language", values.get("language", REFERENCE), languages)}</label>'
            f'<details class="advanced"><summary>{escape(tr("ui_advanced"))}</summary><p class="muted">{escape(tr("app_advanced_hint"))}</p>'
            f"{tabs}</details>{button}</form>")
