"""Discover in the app: where to fly cheaply from a city, on a map. The form asks for a city and the days, the map is
made in a process of its own (map_job.py) and its page is Discover's (discover/view/page.py), served here.

A map takes minutes, the cheapest destinations being looked up in the timetables after it opens, so it has a process
of its own, apart from the planner's searches: a trip clicked on the map opens the planner's form in the app, filled
in, and can be searched while the map goes on. The click only fills the form: nothing is searched until the traveller
asks, and what it fills in comes from the map's own trips, by key, never from the address."""
import html
from datetime import date, timedelta
from typing import Any

from app.fields import calendar_data
from discover import confirm
from discover.view.page import SCRIPT, STYLE
from trip_planner.texts import tr

SOURCES = ("light", "full", "fast", "ground")   # the ways to search the flights, as python -m discover has them (discover_win_source_...)
DEFAULT_DAYS_AHEAD = 21     # the form starts with a few days in a row to leave on, this far ahead
DEFAULT_DAYS_SPAN = 3
# Each map has an address of its own, its number in this run of the app, with what its page asks for under it: its
# destinations as they change ("state") and its trips ("plan/<key>", the planner's form filled in with one). The page
# of a map another one replaced asks there still, and is told the map is gone: never the other map's destinations
MAPS = "/discover/map/"
MAPS_KEPT = 5        # the last maps served: a page left open in a tab keeps working while the next ones are made
# A map asked to stop ends at its next city, its files saved (discover/session.py); killed halfway through writing,
# a cache of discover/cache would be cut short and read as empty. Killed only if it has not ended in this time
STOP_SECONDS = 90

def address_of(number: int) -> str:
    return f"{MAPS}{number}/"
# What the map's page may do. It is Discover's page, whose script and style are files served beside it (FILES), and
# which draws the map with Leaflet (checked against its hash) on OpenStreetMap's tiles; it asks this app for its
# destinations again, and nothing else. No script of its own runs: its data is JSON, read by its script
MAP_POLICY = ("default-src 'none'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com; "
              "img-src https://tile.openstreetmap.org https://unpkg.com data:; connect-src 'self'; base-uri 'none'; "
              "form-action 'none'; frame-ancestors 'none'")
# The files of the map's page, beside it at each map's address: what each is, and the type it is served as
FILES = {"map.js": (SCRIPT, "text/javascript; charset=utf-8"), "map.css": (STYLE, "text/css; charset=utf-8")}
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
    """Why the form cannot make a map, or nothing. The process reads the days the same way."""
    from discover.flights.sources import read_days
    if not asked["city"]:
        return tr("discover_win_no_city")
    try:
        read_days(asked["dates"], asked["return_dates"])
    except ValueError as error:
        return tr("discover_win_days_wrong", error=error)
    return ""

def form(token: str, values: dict[str, Any], refusal: str = "") -> str:
    """Discover's form, as the planner's: one bar with the city, the days and the button, and the choices under it
    as chips: how the flights are searched, and whether the cheapest destinations are looked up in the timetables."""
    escape = lambda text: html.escape(str(text))
    sources = "".join(f'<label class="chip"><input type="radio" name="source" value="{source}"'
                      f'{" checked" if values.get("source") == source else ""}> {escape(tr("discover_win_source_" + source))}</label>'
                      for source in SOURCES)
    error = f'<p class="refusal" role="alert">{escape(refusal)}</p>' if refusal else ""
    look_up = " checked" if values.get("look_up") in ("on", True) else ""
    return (f'<form method="post" action="/discover" class="search"{calendar_data()}><h1>{escape(tr("discover_win_title"))}</h1>'
            f'<p class="muted">{escape(tr("discover_win_intro"))}</p>{error}'
            f'<input type="hidden" name="token" value="{escape(token)}">'
            f'<div class="bar three"><label>{escape(tr("discover_win_from"))}<input name="city" value="{escape(values.get("city", ""))}" required></label>'
            f'<label>{escape(tr("discover_win_leave_on"))}<input name="dates" value="{escape(values.get("dates", ""))}" data-calendar="dates" required></label>'
            f'<label>{escape(tr("discover_win_back_on"))}<input name="return_dates" value="{escape(values.get("return_dates", ""))}" '
            f'data-calendar="return_dates"><small>{escape(tr("app_return_dates_hint"))}</small></label>'
            f'<div class="go"><button type="submit">{escape(tr("discover_win_open"))}</button></div></div>'
            f'<fieldset class="chips"><legend>{escape(tr("discover_win_flights"))}</legend>{sources}</fieldset>'
            f'<div class="chips"><label class="chip"><input type="checkbox" name="look_up"{look_up}> '
            f'{escape(tr("discover_win_look_up", cities=confirm.MAX_LOOKUPS))}</label></div>'
            f'<p class="muted">{escape(tr("discover_win_shared"))}</p></form>')

def part_of(path: str) -> tuple[str, str]:
    """A path under the maps, /discover/map/<number>/<what>, as (the map's address, what): ("", "") for any other."""
    number, slash, what = path[len(MAPS):].partition("/") if path.startswith(MAPS) else ("", "", "")
    return (address_of(int(number)), what) if number.isdigit() and slash else ("", "")

def planner_values(trip: list[str], values: dict[str, Any]) -> dict[str, Any]:
    """The planner's form filled in with a trip of the map, [origin, destination, day]: one way, on that day. Its
    other settings stay as they are."""
    origin, destination, day = trip
    return {**values, "origin": origin, "destination": destination, "dates": day, "return_dates": ""}
