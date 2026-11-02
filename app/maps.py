"""Discover in the app: where to fly cheaply from a city, on a map. The form is the one of Discover's window, the map is
made in a process of its own (map_job.py) and its page is Discover's (discover/page.py), served here.

A map takes minutes, the cheapest destinations being looked up in the timetables after it opens, so it has a process
of its own, apart from the planner's searches: a trip clicked on the map opens the planner's form in the app, filled
in, and can be searched while the map goes on. The click only fills the form: nothing is searched until the traveller
asks, and what it fills in comes from the map's own trips, by key, never from the address."""
import html
from datetime import date, timedelta
from typing import Any

from app.fields import calendar_data
from discover import confirm
from trip_planner.texts import tr

SOURCES = ("light", "full", "fast")   # the ways to search the flights, as in Discover's window (discover_win_source_...)
DEFAULT_DAYS_AHEAD = 21     # the form starts with a few days in a row to leave on, this far ahead, as the window's
DEFAULT_DAYS_SPAN = 3
PLANNER_LINK = "/discover/plan/"   # a trip of the map, by its key: the planner's form filled in with it
# What the map's page may do. It is Discover's page, which has its script inline and draws the map with Leaflet
# (checked against its hash) on OpenStreetMap's tiles; it asks this app for its destinations again, and nothing else
MAP_POLICY = ("default-src 'none'; script-src 'unsafe-inline' https://unpkg.com; style-src 'unsafe-inline' https://unpkg.com; "
              "img-src https://tile.openstreetmap.org https://unpkg.com data:; connect-src 'self'; base-uri 'none'; "
              "form-action 'none'; frame-ancestors 'none'")
# OpenStreetMap's tile servers want to know the page their tiles are drawn on: without it they answer a tile that
# says "Access blocked". They are told this app's address only (http://127.0.0.1:port/), never a path
MAP_REFERRER = "strict-origin-when-cross-origin"

def values_of(city: str, today: date | None = None) -> dict[str, str]:
    """What the form starts with: the city of settings.py, and a few days in a row three weeks ahead."""
    first = (today or date.today()) + timedelta(days=DEFAULT_DAYS_AHEAD)
    return {"city": city, "dates": ", ".join(f"{first + timedelta(days=offset):%d/%m/%Y}" for offset in range(DEFAULT_DAYS_SPAN)),
            "return_dates": "", "source": "light", "look_up": "on"}

def read(sent: dict[str, str]) -> dict[str, Any]:
    """What the form sent, as the map's process takes it. A tick not sent is a tick off."""
    return {"city": sent.get("city", "").strip(), "dates": sent.get("dates", "").strip(), "return_dates": sent.get("return_dates", "").strip(),
            "source": sent.get("source") if sent.get("source") in SOURCES else "light", "look_up": "look_up" in sent}

def refusal_of(asked: dict[str, Any]) -> str:
    """Why the form cannot make a map, as the window says it, or nothing. The process reads the days the same way."""
    from discover.window import read_days
    if not asked["city"]:
        return tr("discover_win_no_city")
    try:
        read_days(asked["dates"], asked["return_dates"])
    except ValueError as error:
        return tr("discover_win_days_wrong", error=error)
    return ""

def form(token: str, values: dict[str, Any], refusal: str = "") -> str:
    escape = lambda text: html.escape(str(text))
    sources = "".join(f'<label class="tick"><input type="radio" name="source" value="{source}"'
                      f'{" checked" if values.get("source") == source else ""}> {escape(tr("discover_win_source_" + source))}</label>'
                      for source in SOURCES)
    error = f'<p class="refusal" role="alert">{escape(refusal)}</p>' if refusal else ""
    look_up = " checked" if values.get("look_up") in ("on", True) else ""
    return (f'<form method="post" action="/discover" class="search"{calendar_data()}><h1>{escape(tr("discover_win_title"))}</h1>'
            f'<p class="muted">{escape(tr("discover_win_intro"))}</p>{error}'
            f'<input type="hidden" name="token" value="{escape(token)}">'
            f'<label>{escape(tr("discover_win_from"))}<input name="city" value="{escape(values.get("city", ""))}" required></label>'
            f'<label>{escape(tr("discover_win_leave_on"))}<input name="dates" value="{escape(values.get("dates", ""))}" data-calendar="dates" required></label>'
            f'<label>{escape(tr("discover_win_back_on"))}<input name="return_dates" value="{escape(values.get("return_dates", ""))}" '
            f'data-calendar="return_dates"><small>{escape(tr("app_return_dates_hint"))}</small></label>'
            f'<fieldset class="ticks"><legend>{escape(tr("discover_win_flights"))}</legend>{sources}</fieldset>'
            f'<label class="tick"><input type="checkbox" name="look_up"{look_up}> {escape(tr("discover_win_look_up", cities=confirm.MAX_LOOKUPS))}</label>'
            f'<p class="muted">{escape(tr("discover_win_shared"))}</p>'
            f'<button type="submit">{escape(tr("discover_win_open"))}</button></form>')

def planner_values(trip: list[str], values: dict[str, Any]) -> dict[str, Any]:
    """The planner's form filled in with a trip of the map, [origin, destination, day]: one way, on that day. Its
    other settings stay as they are."""
    origin, destination, day = trip
    return {**values, "origin": origin, "destination": destination, "dates": day, "return_dates": ""}
