"""Discover in the app: where to fly cheaply from a city, on a map. The form asks for a city and the days, the map is
made in a process of its own (map_job.py) and its page is Discover's (discover/view/page.py), served here.

A map takes minutes, the cheapest destinations being looked up in the timetables after it opens, so it has a process
of its own, apart from the planner's searches: a trip clicked on the map opens the planner's form in the app, filled
in, and can be searched while the map goes on. The click only fills the form: nothing is searched until the traveller
asks, and what it fills in comes from the map's own trips, by key, never from the address."""
import base64
import hashlib
import html
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.fields import calendar_data
from discover import confirm
from discover.view.page import languages, page_texts, SCRIPT, STYLE
from trip_planner import current
from trip_planner.sources import flight_search
from trip_planner.texts import tr
from trip_planner.trip.trip_settings import MAPS_KEY

SOURCES = ("light", "full", "fast", "ground")   # the ways to search the flights, as python -m discover has them (discover_win_source_...)
COACH_CHANGES = (0, 1, 2)   # the changes of coach the form offers (TripSettings.max_coach_changes), each a chip
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
MAX_KEY = 200        # the longest key of a city the page may ask the price of: a city's name and coordinates

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

def values_of(city: str, today: date | None = None, coach_changes: int = 0) -> dict[str, str]:
    """What the form starts with: the city and the changes of coach of settings.py, and a few days in a row three
    weeks ahead."""
    first = (today or date.today()) + timedelta(days=DEFAULT_DAYS_AHEAD)
    return {"city": city, "dates": ", ".join(f"{first + timedelta(days=offset):%d/%m/%Y}" for offset in range(DEFAULT_DAYS_SPAN)),
            "return_dates": "", "source": "light", "look_up": "on", "coach_changes": str(coach_changes)}

def read(sent: dict[str, str]) -> dict[str, Any]:
    """What the form sent, as the map's process takes it. A tick not sent is a tick off."""
    return {"city": sent.get("city", "").strip(), "dates": sent.get("dates", "").strip(), "return_dates": sent.get("return_dates", "").strip(),
            "source": sent.get("source") if sent.get("source") in SOURCES else "light", "look_up": "look_up" in sent,
            "coach_changes": sent.get("coach_changes", "0").strip()}

def refusal_of(asked: dict[str, Any]) -> str:
    """Why the form cannot make a map, or nothing. The process reads the days the same way."""
    from discover.flights.sources import read_days
    if not asked["city"]:
        return tr("discover_win_no_city")
    try:
        read_days(asked["dates"], asked["return_dates"])
    except ValueError as error:
        return tr("discover_win_days_wrong", error=error)
    try:
        coach_settings(asked)
    except ValueError as error:   # SettingError says which setting and why, in the traveller's language
        return str(error)
    return ""

def coach_settings(asked: dict[str, Any]):
    """The settings of the moment with the changes of coach the form asks for, checked as every setting is: a value
    out of RANGES raises SettingError."""
    import dataclasses
    from trip_planner import current
    return dataclasses.replace(current.SETTINGS, max_coach_changes=asked.get("coach_changes", "0"))

def form(token: str, values: dict[str, Any], refusal: str = "") -> str:
    """Discover's form, as the planner's: one bar with the city, the days and the button, and the choices under it
    as chips: how the flights are searched, and whether the cheapest destinations are looked up in the timetables."""
    escape = lambda text: html.escape(str(text))
    sources = "".join(f'<label class="chip"><input type="radio" name="source" value="{source}"'
                      f'{" checked" if values.get("source") == source else ""}> {escape(tr("discover_win_source_" + source))}</label>'
                      for source in SOURCES)
    error = f'<p class="refusal" role="alert">{escape(refusal)}</p>' if refusal else ""
    look_up = " checked" if values.get("look_up") in ("on", True) else ""
    changes = "".join(f'<label class="chip"><input type="radio" name="coach_changes" value="{count}"'
                      f'{" checked" if str(values.get("coach_changes", "0")) == str(count) else ""}> '
                      f'{escape(tr("discover_win_coach_changes_" + str(count)))}</label>' for count in COACH_CHANGES)
    return (f'<form method="post" action="/discover" class="search"{calendar_data()}><h1>{escape(tr("discover_win_title"))}</h1>'
            f'<p class="muted">{escape(tr("discover_win_intro"))}</p>{error}'
            f'<input type="hidden" name="token" value="{escape(token)}">'
            f'<div class="bar three"><label>{escape(tr("discover_win_from"))}<input name="city" value="{escape(values.get("city", ""))}" required></label>'
            f'<label>{escape(tr("discover_win_leave_on"))}<input name="dates" value="{escape(values.get("dates", ""))}" data-calendar="dates" required></label>'
            f'<label>{escape(tr("discover_win_back_on"))}<input name="return_dates" value="{escape(values.get("return_dates", ""))}" '
            f'data-calendar="return_dates"><small>{escape(tr("app_return_dates_hint"))}</small></label>'
            f'<div class="go"><button type="submit">{escape(tr("discover_win_open"))}</button></div></div>'
            f'<fieldset class="chips"><legend>{escape(tr("discover_win_flights"))}</legend>{sources}</fieldset>'
            f'<fieldset class="chips"><legend>{escape(tr("discover_win_coach_changes"))}</legend>{changes}</fieldset>'
            f'<div class="chips"><label class="chip"><input type="checkbox" name="look_up"{look_up}> '
            f'{escape(tr("discover_win_look_up", cities=confirm.MAX_LOOKUPS))}</label></div>'
            f'<p class="muted">{escape(tr("discover_win_shared"))}</p></form>')

# ---------------------------------------------------------------- the maps made before (discover/maps)
# Each map made is kept as a page of its own, its script and its data inside, as the planner keeps its reports: listed
# under the form, newest first, and opened by its name. Such a page has no planner beside it, as a file opened by hand.
SAVED = "/discover/saved/"
# Where the maps are kept (discover/session.MAPS): not imported, since discover/session.py reads settings.py as it is
# imported, which only a map's own process does (map_job.py)
SAVED_MAPS = Path(os.environ[MAPS_KEY]) if os.environ.get(MAPS_KEY) else Path(confirm.__file__).with_name("maps")
SAVED_LISTED = 30   # the newest maps listed under the form
SAVED_INFO: dict[Path, tuple[float, dict[str, Any] | None]] = {}   # what each page says of itself, by its file and its time

def saved_maps() -> list[Path]:
    """The maps made, newest first."""
    return sorted(SAVED_MAPS.glob("*.html"), key=lambda path: path.stat().st_mtime, reverse=True) if SAVED_MAPS.exists() else []

DATA = re.compile(r'<script type="application/json" id="data">(.*?)</script>', re.S)   # where a map's page keeps its data

def page_data(page: str) -> dict[str, Any] | None:
    """The data a map's page holds (discover/view/page.py), or None for a page that is not a map's."""
    found = DATA.search(page)
    try:
        data = json.loads(found[1]) if found else None
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("state"), dict) else None

def with_data(page: str, data: dict[str, Any]) -> str:
    """The page with this data in place of its own, written as page.as_script writes it: nothing in it can end the tag."""
    text = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return DATA.sub(lambda found: f'<script type="application/json" id="data">{text}</script>', page, count=1)

def saved_path(name: str) -> Path | None:
    """A map made before, by its name; None for a name that is not one of them, never a path built from it."""
    return next((path for path in saved_maps() if path.stem == name), None)

def days_of_saved(path: Path):
    """The days a map was made for, from its name, as discover/session.py writes it:
    city_first_last[_dd-dd...][_back_first_last[_dd-dd...]], the days of the month clicked when not all in a row."""
    from discover.flights.sources import Days
    out, _, back = path.stem.partition("_back_")

    def read(part: str) -> tuple[str, str, tuple[str, ...]] | None:
        found = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})(?:_(\d{2}(?:-\d{2})*))?$", part)
        if not found:
            return None
        first, last, clicked = found[1], found[2], (found[3] or "").split("-") if found[3] else []
        span = [(date.fromisoformat(first) + timedelta(days=offset)).isoformat()
                for offset in range((date.fromisoformat(last) - date.fromisoformat(first)).days + 1)]
        return first, last, tuple(day for day in span if day[8:] in clicked)
    leaving, coming = read(out), read(back) if back else None
    if leaving is None:
        return None
    return Days(leaving[0], leaving[1], *(coming[:2] if coming else ()), out_days=leaving[2], back_days=coming[2] if coming else ())

def saved_info(path: Path) -> dict[str, Any] | None:
    """What a map says of itself: its city, its days, its destinations and the cheapest. Read from the data in its
    page once, until the file changes: a page is a megabyte or two. None for a page that is not a map's."""
    when = path.stat().st_mtime
    if SAVED_INFO.get(path, (None, None))[0] != when:
        data = page_data(path.read_text(encoding="utf-8"))
        info = None
        if data:
            state = data.get("state") or {}
            prices = [point["total_eur"] for point in state.get("points") or [] if point.get("total_eur") is not None]
            info = {"city": data["origin"].get("short") or data["origin"]["name"], "count": len(state.get("points") or []),
                    "by_ground": len(state.get("by_ground") or []), "cheapest": min(prices) if prices else None}
        SAVED_INFO[path] = (when, info)
    return SAVED_INFO[path][1]

def saved_days(path: Path) -> str:
    """The days of a map, from its name (discover/session.py writes them there: city_first_last[_back_first_last]), as
    the traveller reads them: 12/11–14/11 · back 15/11–17/11, in the language of the page now."""
    out, _, back = path.stem.partition("_back_")
    span = lambda part: [f"{day[8:10]}/{day[5:7]}" for day in re.findall(r"\d{4}-\d{2}-\d{2}", part)[:2]]
    written = lambda days: "–".join(dict.fromkeys(days))   # one day said once
    return tr("discover_win_days_back", out=written(span(out)), back=written(span(back))) if back else written(span(out))

def saved_list() -> str:
    """The maps made before, as the planner lists its reports: a card each, newest first."""
    cards = []
    for path in saved_maps()[:SAVED_LISTED]:
        info = saved_info(path)
        if not info:
            continue
        made = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
        found = tr("discover_win_destinations", count=info["count"]) + (
            " · " + tr("discover_win_by_ground", count=info["by_ground"]) if info["by_ground"] else "")
        price = (f'<span class="price"><b>{tr("discover_win_from_price", price=f"{info["cheapest"]:.0f}")}</b>'
                 f'<small>{html.escape(found)}</small></span>' if info["cheapest"] is not None
                 else f'<span class="price"><small>{html.escape(found)}</small></span>')
        cards.append(f'<li><a class="trip" href="{SAVED}{quote(path.stem)}"><span class="route">{html.escape(info["city"])}</span>'
                     f'<small>{html.escape(saved_days(path))} · {html.escape(tr("discover_win_made", when=made))}</small>{price}</a></li>')
    body = f'<ul class="trips">{"".join(cards)}</ul>' if cards else f'<p class="muted">{html.escape(tr("discover_win_no_earlier"))}</p>'
    return f'<h2>{html.escape(tr("discover_win_earlier"))}</h2>{body}'

def dressed(page: str, theme: str, token: str) -> str:
    """A map's page as the app serves it: its script's texts in the app's language, which the traveller may have
    changed since the map was made (the page's own list changes it, site.post_language), with the languages to
    choose from, and light or dark as the app's pages are (site.theme). The token is the app's, for the page to say
    which it chose: "app_token", apart from the one that asks for prices, which a demo does not. The file of the map
    keeps its own."""
    data = page_data(page)
    if data:
        page = with_data(page, {**data, "texts": page_texts(), "locale": current.SETTINGS.language,
                                "languages": languages(), "app_token": token})
    if theme in ("light", "dark"):
        page = page.replace('<html lang="en">', f'<html lang="en" data-theme="{theme}">', 1)
    return page

def saved_page(name: str, token: str = "", waiting: list[str] = ()) -> tuple[str, str] | None:
    """A map made before, by its name, with what it may do: (its page, its policy). None for a name that is not one
    of the maps. The page carries its script and its style inside: the policy lets those run, by their hash, and
    nothing else inline, as a new map's page (MAP_POLICY). With the app's token, the page can ask for the price of a
    city and follow it (saved_state), at its own address: its data is given them as it is served, never saved with
    them; waiting are the cities being priced now."""
    path = saved_path(name)
    if path is None:
        return None
    page = path.read_text(encoding="utf-8")
    data = page_data(page)
    if data:
        # Shown with today's script, style and texts, not the ones it was saved with: a map made before works and
        # reads as a new one does. Its file keeps its own, to open with nothing else
        data["texts"] = page_texts()
        if token:
            address = SAVED + quote(name)
            data.update(token=token, price_link=address + "/price", state_link=address + "/state")
            data["state"] = waiting_in(data["state"], waiting)
        page = with_data(page, data)
        page = re.sub(r"<script>\n.*?</script>", lambda _: f"<script>\n{SCRIPT.read_text(encoding='utf-8')}</script>", page, count=1, flags=re.S)
        page = re.sub(r"<style>\n.*?</style>", lambda _: f"<style>\n{STYLE.read_text(encoding='utf-8')}</style>", page, count=1, flags=re.S)
    digest = lambda text: "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"
    scripts = " ".join(digest(code) for code in re.findall(r"<script>(.*?)</script>", page, re.S))
    styles = " ".join(digest(code) for code in re.findall(r"<style>(.*?)</style>", page, re.S))
    policy = (MAP_POLICY.replace("script-src 'self'", f"script-src 'self' {scripts}")
              .replace("style-src 'self'", f"style-src 'self' {styles}"))
    return page, policy

def waiting_in(state: dict[str, Any], waiting: list[str]) -> dict[str, Any]:
    """The state of a map with the cities being priced now marked so, as a map being made marks them."""
    return {**state, "asked_prices": {**(state.get("asked_prices") or {}), **{key: "waiting" for key in waiting}}} if waiting else state

def saved_state(name: str, waiting: list[str] = ()) -> dict[str, Any] | None:
    """What a map made before shows now, for its page to follow while a price it asked for comes."""
    path = saved_path(name)
    data = page_data(path.read_text(encoding="utf-8")) if path else None
    return waiting_in(data["state"], waiting) if data else None

def part_of(path: str) -> tuple[str, str]:
    """A path under the maps, /discover/map/<number>/<what>, as (the map's address, what): ("", "") for any other."""
    number, slash, what = path[len(MAPS):].partition("/") if path.startswith(MAPS) else ("", "", "")
    return (address_of(int(number)), what) if number.isdigit() and slash else ("", "")

def estimate_of(state: dict[str, Any], key: str, trip: list[str], address: str) -> dict[str, Any] | None:
    """What a map says of one of its trips, by its key ("DUB-out", "DUB-back"), when it opens the planner on it: the
    flight, by the planner's own id for it (sources/flight_search.py), and the ways to and from its airports with
    their prices and grades, to be compared with what the planner then finds (report/metrics.py, tools/funnel.py).
    The flight's price is the round trip's on a map there and back: only the ways to and from the airports can be
    compared one way at a time. None when the map has no such trip."""
    code, _, way = key.rpartition("-")
    point = next((point for point in state.get("points") or [] if point.get("to") == code), None)
    flight = (point if way == "out" else (point or {}).get("back")) if way in ("out", "back") else None
    if not point or not flight or not flight.get("time"):
        return None
    ends = ((flight["from"], True), (flight["to"], False))   # the way to its first airport, and from its last
    legs = [leg for end in ends for leg in point.get("access") or [] if (leg.get("airport"), leg.get("to_airport")) == end]
    return {"map": address, "key": key, "trip": trip, "clicked": datetime.now().isoformat(timespec="minutes"),
            "flight_id": flight_search.flight_id(flight["from"], flight["to"], flight["date"], flight["time"]),
            "round_trip": bool(point.get("back")), "flight_eur": point.get("price_eur"), "total_eur": point.get("total_eur"),
            "ground_eur": round(sum(leg.get("price_eur") or 0 for leg in legs), 2), "looked_up": bool(legs) and all(leg.get("looked_up") for leg in legs),
            "legs": [{key: leg.get(key) for key in ("airport", "to_airport", "price_eur", "grade", "price_grade", "looked_up")} for leg in legs]}

def same_trip(estimate: dict[str, Any], settings) -> bool:
    """Whether the planner's form, as sent, still asks for the trip a map opened it on: the same places, that one
    day, one way. Changed in between, the map's estimate is not for this search."""
    origin, destination, day = estimate["trip"]
    return (settings.origin == origin and settings.destination == destination and not settings.return_dates
            and not settings.ground_only and settings.iso_dates == [datetime.strptime(day, "%d/%m/%Y").date().isoformat()])

def planner_values(trip: list[str], values: dict[str, Any]) -> dict[str, Any]:
    """The planner's form filled in with a trip of the map, [origin, destination, day]: one way, on that day. Its
    other settings stay as they are."""
    origin, destination, day = trip
    return {**values, "origin": origin, "destination": destination, "dates": day, "return_dates": ""}
