"""The map page: one dot per destination, its colour by the total price, each destination as a card beside the map
(below it on a phone), and under them what was searched (breakdown.py). Clicking a dot or a row explains that trip: when to be at the airport, each flight with
the day and time it leaves and lands, the connections, the way back, the prices, and the links to book it, to
compare it and to reach the airports. And, in the app (app/maps.py), a link that opens the planner's form on it;
a map opened as a file has no planner to open. The map is OpenStreetMap's, drawn by Leaflet, whose
files are checked against their known hashes. The page's script is map.js and its look map.css, beside this module:
files the app serves beside the page, put inside a page saved as a file. What the page shows is JSON, read by
map.js; every text is put in the page as text, never as markup, and every link is https.

The prices carry the planner's grades: the flights A, read by code from the source when the map was made (the
booking page has the one that counts), each way to and from the airports its own (access.py). While the cheapest
destinations are looked up in the timetables (confirm.py), the served page asks for them again and redraws itself."""
import html
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from discover.airports.access import city_name
from discover.finder import Found
from discover.view.breakdown import source_health
from trip_planner import current, utils
from trip_planner.report.icons import sprite
from trip_planner.texts import TEXTS, REFERENCE, text_in, tr
from trip_planner.trip.trip_settings import LIGHT_COLOURS, TripSettings
from trip_planner.trip.places import load_airports

ROOT = Path(__file__).parents[2]
HERE = Path(__file__).parent
STYLE, SCRIPT = HERE / "map.css", HERE / "map.js"   # the page's look and its script, served by the app or put in the file
PAGE_TEXTS = "discover_page_"   # the texts the page's script writes, in the traveller's language (texts/languages)

def google_flights(flight: dict[str, Any]) -> str:
    """Google Flights, searching the same trip on the same days: to compare the price and see every airline."""
    asked = f"Flights from {flight['from']} to {flight['to']} on {flight['date']}"
    if flight.get("back"):
        asked += f" returning {flight['back']['date']}" + (f" from {flight['back']['from']}" if flight["back"]["from"] != flight["to"] else "")
    return "https://www.google.com/travel/flights?q=" + quote(asked)

LONG_WAIT_MINUTES = 6 * 60   # a connection this long, or through the night, is shown as a warning
# Being at the departure airport before this hour, or landing from this hour on, is shown as a warning: public
# transport may not run then, and the map does not know. The planner, opened on the trip, does.
EARLY_AIRPORT_HOUR = 6
LATE_LANDING_HOUR = 23

def airport_place(code: str, airports: dict[str, Any]) -> str:
    """An airport as a place to look up in a map: its name, with "airport" when the name does not say it."""
    name = (airports.get(code) or {}).get("name", code)
    return name if "airport" in name.lower() or "aeroporto" in name.lower() else f"{name} airport"

def transit(origin: str, destination: str) -> str:
    """Google Maps, by public transport between two places."""
    return "https://www.google.com/maps/dir/?" + urlencode({"api": 1, "origin": origin, "destination": destination, "travelmode": "transit"})

def links(flight: dict[str, Any], found: Found, airports: dict[str, Any]) -> list[dict[str, str]]:
    """The links of one trip: where to book it ("kind": "book"), where to compare it, how to reach the airport and
    how to get home."""
    name = lambda code: f"{(airports.get(code) or {}).get('name', code)} ({code})"
    found_links = []
    if flight.get("url", "").startswith("https://"):
        found_links.append({"kind": "book", "label": tr("discover_link_book", site="Ryanair" if flight["source"] == "Ryanair" else "Kiwi.com"),
                            "url": flight["url"]})
    found_links.append({"kind": "compare", "label": tr("discover_link_compare"), "url": google_flights(flight)})
    found_links.append({"kind": "transit", "label": tr("discover_link_to_airport", place=found.place.label, airport=name(flight["from"])),
                        "url": transit(found.place.label, airport_place(flight["from"], airports))})
    if flight.get("back"):
        found_links.append({"kind": "transit", "label": tr("discover_link_home", airport=name(flight["back"]["to"]), place=found.place.label),
                            "url": transit(airport_place(flight["back"]["to"], airports), found.place.label)})
    return found_links

def page_texts() -> dict[str, str]:
    """Every text of the page's script, in the traveller's language, its {values} left for the script to fill."""
    language = current.SETTINGS.language
    return {key[len(PAGE_TEXTS):]: text_in(language, key) for key in TEXTS[REFERENCE] if key.startswith(PAGE_TEXTS)}

def day(text: str) -> str:
    """yyyy-mm-dd as the planner reads it, dd/mm/yyyy."""
    return date.fromisoformat(text).strftime("%d/%m/%Y")

def city(code: str, airports: dict[str, Any], centres: dict | None = None) -> str:
    """The city an airport serves, by the name the planner will look it up by: Marseille, not Marignane."""
    if centres and code in centres:
        return centres[code].english_name
    airport = airports.get(code) or {}
    return city_name(airport) or airport.get("name") or code

def trips(found: Found, airports: dict[str, Any]) -> dict[str, list[str]]:
    """Every trip of the map the planner can be opened on, by key: "<code>-out" for the way there and "<code>-back"
    for the way back, each [origin, destination, day]. The page sends only the key: what the planner is opened on
    comes from here, never from the page."""
    home = found.place.written
    found_trips = {}
    for flight in found.destinations:
        there = city(flight["to"], airports, found.centres)
        found_trips[f"{flight['to']}-out"] = [home, there, day(flight["date"])]
        if flight.get("back"):
            found_trips[f"{flight['to']}-back"] = [city(flight["back"]["from"], airports, found.centres), home, day(flight["back"]["date"])]
    return found_trips

def stay(flight: dict[str, Any], airport_minutes: int = TripSettings.min_airport_minutes,
         landing_minutes: int = TripSettings.min_landing_minutes) -> dict[str, Any] | None:
    """The time in the city of a return trip: from reaching its centre (landing, leaving the airport, the way in) to
    leaving it (the way out to the airport, being there before the flight), with the nights in between. None for a
    one-way trip, or when a time is missing. estimated says a way in or out was not looked up for these flights."""
    back = flight.get("back")
    if not back or not flight.get("arrival_time") or not back.get("time"):
        return None
    access = flight.get("access") or []
    into, out = (access[1] if len(access) > 1 else {}), (access[2] if len(access) > 2 else {})
    arrive = (datetime.fromisoformat(f"{flight.get('arrival_date') or flight['date']}T{flight['arrival_time']}")
              + timedelta(minutes=landing_minutes + into.get("minutes", 0)))
    leave = datetime.fromisoformat(f"{back['date']}T{back['time']}") - timedelta(minutes=airport_minutes + out.get("minutes", 0))
    if leave <= arrive:
        return {"minutes": 0, "nights": 0, "estimated": True}
    return {"minutes": round((leave - arrive).total_seconds() / 60), "nights": (leave.date() - arrive.date()).days,
            "estimated": not (into.get("looked_up") and out.get("looked_up"))}

def as_script(value: Any) -> str:
    """A value as JavaScript in the page: JSON without any "<", so that nothing in it can end or open a tag."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")

def map_state(found: Found, sections: list[dict[str, Any]], running: bool = False, version: int = 0,
              airport_minutes: int = TripSettings.min_airport_minutes, landing_minutes: int = TripSettings.min_landing_minutes) -> dict[str, Any]:
    """What the page shows that changes while the timetables are looked up (confirm.py): the destinations, the
    flights left out, the cities looked up and the sections. The served page asks for it again while running.
    airport_minutes and landing_minutes are the planner's, for the time in the city (stay)."""
    airports = load_airports()
    planned = trips(found, airports)
    plan = lambda key: {"key": key} if key in planned else None
    name = lambda code: found.centres[code].english_name if code in found.centres else city_name(airports[code]) or airports[code]["name"]
    # The dot of a city is at its centre once located: an airport such as Beauvais or Schwechat is far enough from
    # Paris or Vienna to put the dot in another place. Its airports stay in places, for the route
    where = lambda code: ((found.centres[code].latitude, found.centres[code].longitude) if code in found.centres
                          else (airports[code]["latitude"], airports[code]["longitude"]))
    points = [{**flight, "name": name(flight["to"]), "country": airports[flight["to"]]["country"],
               "lat": where(flight["to"])[0], "lon": where(flight["to"])[1], "links": links(flight, found, airports),
               "plan": {"out": plan(f"{flight['to']}-out"), "back": plan(f"{flight['to']}-back")},
               "confirmed": all(leg["looked_up"] for leg in flight.get("access") or [{"looked_up": False}]),
               # the centre of each city the trip reaches, for its route on the map: the one flown to, and the one flown back from
               "centres": {code: [found.centres[code].latitude, found.centres[code].longitude]
                           for code in {flight["to"], (flight.get("back") or {}).get("from")} if code in found.centres},
               "stay": stay(flight, airport_minutes, landing_minutes)}
              for flight in found.destinations if flight["to"] in airports]
    codes = {code for flight in points for leg in (flight, flight.get("back")) if leg
             for segment in [leg, *leg.get("segments", [])] for code in (segment["from"], segment["to"])}
    codes |= {other["to"] for flight in points for other in flight.get("also", [])} | {gone["to"] for gone in found.discarded}
    return {"version": version, "running": running, "points": points, "names": {code: airports[code]["name"] for code in codes if code in airports},
            "places": {code: [airports[code]["latitude"], airports[code]["longitude"]] for code in codes if code in airports},   # for the routes
            "discarded": found.discarded, "lookups": found.lookups, "confirm_stop": found.confirm_stop, "sections": sections,
            "coverage": found.coverage, "by_ground": found.by_ground, "fare_lookups": found.fare_lookups, "fare_stop": found.fare_stop, "phase": found.phase,
            "asked_prices": found.asked_prices, "sources": source_health(found, utils.REQUESTS)}

def map_page(found: Found, sources: str, sections: list[dict[str, Any]], airport_minutes: int = 120,
             running: bool = False, max_lookups: int = 0, landing_minutes: int = TripSettings.min_landing_minutes,
             planner_link: str = "", token: str = "") -> str:
    """airport_minutes is how long before a flight the traveller is at the airport (the planner's setting).
    planner_link is the page in the app (app/) that the planner's form opens at, filled in with a trip, followed by
    its key: with it each trip has a link there, and the page asks for the destinations again while running, which
    says the timetables are still being looked up. Without it, as a file, the page shows what it holds. token is
    the app's, for the page to ask it for the price of a city (app/site.py, post_map_price): given only to the page
    the app serves, never to the file saved."""
    data = {"origin": {"name": found.place.label, "short": found.place.english_name, "lat": found.place.latitude, "lon": found.place.longitude},
            "days": str(found.days), "round_trip": found.days.round_trip, "sources": sources, "airport_minutes": airport_minutes,
            "long_wait": LONG_WAIT_MINUTES, "early_hour": EARLY_AIRPORT_HOUR, "late_hour": LATE_LANDING_HOUR,
            "planner_link": planner_link, **({"token": token} if token else {}),
            "max_lookups": max_lookups, "kiwi_why": found.kiwi_why, "texts": page_texts(), "locale": current.SETTINGS.language, "landing_minutes": landing_minutes, "grade_colours": LIGHT_COLOURS, "state": map_state(found, sections, running, 0, airport_minutes, landing_minutes)}
    title = tr("discover_page_tab_title", origin=found.place.english_name)
    # In the app the style and the script are files beside the page, so that it may run no script of its own
    # (app/maps.py, MAP_POLICY); a page saved as a file carries them, to open with nothing else
    parts = {"__TITLE__": html.escape(title), "__DATA__": as_script({**data, "title": title}), "__ICONS__": sprite(),
             "__STYLE__": ('<link rel="stylesheet" href="map.css">' if planner_link else
                           f"<style>\n{STYLE.read_text(encoding='utf-8')}</style>"),
             "__SCRIPT__": ('<script src="map.js"></script>' if planner_link else
                            f"<script>\n{SCRIPT.read_text(encoding='utf-8')}</script>")}
    return re.sub("|".join(parts), lambda found: parts[found[0]], PAGE)   # once each: nothing put in is read again

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
__STYLE__</head><body>__ICONS__
<header><a id="back" hidden></a><h1 id="title"></h1><p id="about"></p><div id="status"></div><p class="legend" id="legend"></p></header>
<div id="filters"></div>
<div class="split"><ol id="rows" class="cards"></ol>
<div class="side"><div id="map"></div><p id="tiles" hidden></p><section id="detail" hidden></section></div></div>
<div id="by-ground" hidden><h2 id="by-ground-title"></h2>
<p class="note" id="by-ground-note"></p>
<div class="wrap"><table><thead><tr id="ground-head"></tr></thead><tbody id="ground-rows"></tbody></table></div></div>
<div id="discarded"></div>
<h2 id="searched-title"></h2>
<div id="sections"></div>
<script type="application/json" id="data">__DATA__</script>
__SCRIPT__</body></html>
"""
