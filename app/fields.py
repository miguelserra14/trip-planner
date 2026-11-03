"""The form of a trip, as trip/form.py lays it out: the main card (where from, where to, when, and three ticks) and
every other setting behind "Advanced settings", in its tabs, with its labels and hints. What
the form sends is read back into the values TripSettings takes; TripSettings stays the only check.

The page works without its script: the dates are typed as TripSettings reads them ("17/12/2026, 17-19/12, Friday"),
and the script (page.js) only adds the calendar to click them in."""
import dataclasses
import html
from typing import Any

from trip_planner.trip.form import CHANGES, HINTS, MAIN_SETTINGS, TABS as FORM_TABS, label, same, shown
from trip_planner.texts import LANGUAGES, REFERENCE, TEXTS, tr
from trip_planner.trip.ground_choice import CHOICES, NO_CHANGE_LIMIT
from trip_planner.trip.trip_settings import GRADES, LIGHTS, MAX_DATES, MAX_DAYS_AHEAD, RANGES, TripSettings

# The settings of the trip the form sends, besides the advanced ones: the calendars and the language hold these
TRIP = ("origin", "destination", "dates", "return_dates", "language")
# Every setting behind "Advanced settings", tab by tab. The main card's ticks hold the two scales, near or wide. The
# stopovers are in both: the tick for direct flights only is 0, and without it the number is used
SCALES_TICKED = ("departure_scale", "arrival_scale")
TABS = [(title, [name for name in names if name not in SCALES_TICKED]) for title, names in FORM_TABS]
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

def changed(values: dict[str, Any], defaults: TripSettings) -> list[str]:
    """The advanced settings the form holds that differ from settings.py."""
    start = values_of(defaults)
    return [name for name in ADVANCED if not same(values.get(name), start.get(name))]

def as_default(value: Any) -> str:
    """A value as the form's field holds it, to tell, in the page, whether the field still has it (page.js)."""
    return ("on" if value else "") if isinstance(value, bool) else str(value)

def hint(name: str) -> str:
    key = HINTS.get(name)
    return tr(key) if key else ""

def choice(name: str, value: str, options: dict[str, str], extra: str = "") -> str:
    items = "".join(f'<option value="{html.escape(option)}"{" selected" if option == value else ""}>{html.escape(words)}</option>'
                    for option, words in options.items())
    return f'<select name="{name}"{extra}>{items}</select>'

def field(name: str, value: Any, defaults: TripSettings) -> str:
    """One advanced setting: its label, the way to change it, and what it means under it. Each holds the value of
    settings.py too, for the page to count the settings changed while they are changed (page.js)."""
    escape = lambda text: html.escape(str(text))
    default = f' data-default="{escape(as_default(values_of(defaults).get(name)))}"'
    if kind_of(name) is bool:
        widget = f'<input type="checkbox" name="{name}"{" checked" if value else ""}{default}>'
        return f'<label class="tick">{widget} {escape(label(name))}{f"<small>{escape(hint(name))}</small>" if hint(name) else ""}</label>'
    if name in ("to_airport_by", "from_airport_by"):
        widget = choice(name, value, {option: tr(f"ui_choice_{option}") for option in CHOICES}, default)
    elif name in CHANGES:
        widget = choice(name, str(value), {**{str(number): str(number) for number in range(NO_CHANGE_LIMIT)}, "any": tr("ui_changes_any")}, default)
    elif name == "min_reliability":   # by its colour, as the reports show it, and its words for who does not see colours
        widget = choice(name, value, {grade: f"{LIGHTS[grade]} {tr('grade_' + grade.lower() + '_long')}" for grade in GRADES}, default)
    elif name in RANGES:
        lowest, highest = RANGES[name]
        step = 1 if kind_of(name) is int else "any"
        widget = f'<input type="number" name="{name}" value="{escape(value)}" min="{lowest}" max="{highest}" step="{step}"{default}>'
    else:   # a time, a list of airports, or free text
        widget = f'<input name="{name}" value="{escape(value)}"{default}>'
    return f'<label>{escape(label(name))}{widget}{f"<small>{escape(hint(name))}</small>" if hint(name) else ""}</label>'

def calendar_data() -> str:
    """What the calendar of a form's "dates" and "return_dates" fields needs (page.js): its words and its limits."""
    calendar = {"months": tr("cal_months"), "weekdays": tr("cal_weekdays"), "clear": tr("ui_calendar_clear"),
                "hint": tr("ui_calendar_hint", maximum=MAX_DATES), "return-hint": tr("ui_calendar_return_hint", maximum=MAX_DATES),
                "today-limit": MAX_DAYS_AHEAD, "most-dates": MAX_DATES}
    return "".join(f' data-{name}="{html.escape(str(value))}"' for name, value in calendar.items())

def trip_form(token: str, values: dict[str, Any], defaults: TripSettings, refusal: str = "", busy: bool = False,
              note: str = "") -> str:
    """The whole form: the main card first, with the ↔ that swaps the two places (page.js) and the
    kind of trip under it; the advanced settings folded under it, with how many differ from settings.py and the
    button that brings them all back. note is said at the top, as refusal is, when nothing went wrong."""
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
    error = (f'<p class="refusal" role="alert">{escape(refusal)}</p>' if refusal else
             f'<p role="status">{escape(note)}</p>' if note else "")
    # The search button comes before Restore, which also sends the form: Enter in a field presses the first one.
    # While another search runs it is there but off, so that Enter does nothing, beside the way to that search
    button = (f'<p><button type="submit" disabled>{escape(tr("ui_search"))}</button> '
              f'<a href="/search">{escape(tr("app_search_running"))}</a></p>' if busy
              else f'<button type="submit">{escape(tr("ui_search"))}</button>')
    count = len(changed(values, defaults))
    summary = (f'<summary data-changed="{escape(tr("ui_advanced_changed", count="{count}"))}" data-none="{escape(tr("ui_advanced"))}">'
               f'{escape(tr("ui_advanced_changed", count=count) if count else tr("ui_advanced"))}</summary>')
    kind = (f'<p class="muted trip-kind" data-one="{escape(tr("ui_trip_kind"))}" data-round="{escape(tr("ui_trip_kind_return"))}">'
            f'{escape(tr("ui_trip_kind_return" if values.get("return_dates") else "ui_trip_kind"))}</p>')
    swap = f'<button type="button" class="quiet swap" data-swap hidden title="{escape(tr("app_swap"))}" aria-label="{escape(tr("app_swap"))}">↔</button>'
    restore = f'<button type="submit" formaction="/restore" formnovalidate class="quiet">{escape(tr("ui_restore"))}</button>'
    return (f'<form method="post" action="/search" class="search"{calendar_data()}>'
            f'<h1>{escape(tr("app_plan_title"))}</h1>{error}'
            f'<input type="hidden" name="token" value="{escape(token)}">'
            f'<div class="pair">{entry("origin", "ui_from", " required")}{swap}{entry("destination", "ui_to", " required")}</div>'
            f'<div class="pair">{dates("dates", "ui_dates", "app_dates_hint")}{dates("return_dates", "ui_return_dates", "app_return_dates_hint")}</div>'
            f'<div class="ticks">{tick("departure_scale", "app_nearby_origin", values.get("departure_scale") == "wide")}'
            f'{tick("arrival_scale", "app_nearby_destination", values.get("arrival_scale") == "wide")}'
            f'{tick(DIRECT, "ui_direct", str(values.get("max_stopovers")) == "0")}</div>'
            f'<div class="actions">{button}{kind}<label class="inline">{escape(tr("ui_language"))}'
            f'{choice("language", values.get("language", REFERENCE), languages)}</label></div>'
            f'<details class="advanced">{summary}<p class="muted">{escape(tr("app_advanced_hint"))}</p>'
            f"{tabs}{restore}</details></form>")
