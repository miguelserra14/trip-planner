"""The map page: one dot per destination, its colour by the total price, the list below, and under it what was
searched (breakdown.py). Clicking a dot or a row explains that trip: when to be at the airport, each flight with
the day and time it leaves and lands, the connections, the way back, the prices, and the links to book it, to
compare it and to reach the airports. And the way to check it in the planner: a button when the page is served
(serve.py), the command to run when it is opened as a file. The map is OpenStreetMap's, drawn by Leaflet, whose
files are checked against their known hashes. Every text is put in the page as text, never as markup, and every
link is https.

The prices carry the planner's grades: the flights A, read by code from the source when the map was made (the
booking page has the one that counts), each way to and from the airports its own (access.py). While the cheapest
destinations are looked up in the timetables (confirm.py), the served page asks for them again and redraws itself."""
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from discover.access import city_name
from discover.finder import Found
from discover.plan import command, trips
from trip_planner.gui.style import LIGHT_COLOURS
from trip_planner import current
from trip_planner.texts import TEXTS, REFERENCE, text_in, tr
from trip_planner.trip.trip_settings import TripSettings
from trip_planner.trip.places import load_airports

ROOT = Path(__file__).parents[1]
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

def shown_command(trip: list[str]) -> str:
    """The command that opens the planner on a trip, as the traveller would type it in the repository's folder."""
    arguments = command(trip)
    try:
        python = os.path.relpath(arguments[0], ROOT)
        arguments[0] = arguments[0] if python.startswith("..") else python
    except ValueError:   # another drive
        pass
    return subprocess.list2cmdline(arguments)

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
    plan = lambda key: {"key": key, "command": shown_command(planned[key])} if key in planned else None
    name = lambda code: found.centres[code].english_name if code in found.centres else city_name(airports[code]) or airports[code]["name"]
    points = [{**flight, "name": name(flight["to"]), "country": airports[flight["to"]]["country"],
               "lat": airports[flight["to"]]["latitude"], "lon": airports[flight["to"]]["longitude"], "links": links(flight, found, airports),
               "plan": {"out": plan(f"{flight['to']}-out"), "back": plan(f"{flight['to']}-back")},
               "confirmed": all(leg["looked_up"] for leg in flight.get("access") or [{"looked_up": False}]),
               "stay": stay(flight, airport_minutes, landing_minutes)}
              for flight in found.destinations if flight["to"] in airports]
    codes = {code for flight in points for leg in (flight, flight.get("back")) if leg
             for segment in [leg, *leg.get("segments", [])] for code in (segment["from"], segment["to"])}
    codes |= {other["to"] for flight in points for other in flight.get("also", [])} | {gone["to"] for gone in found.discarded}
    return {"version": version, "running": running, "points": points, "names": {code: airports[code]["name"] for code in codes if code in airports},
            "discarded": found.discarded, "lookups": found.lookups, "confirm_stop": found.confirm_stop, "sections": sections,
            "coverage": found.coverage, "by_ground": found.by_ground, "fare_lookups": found.fare_lookups, "fare_stop": found.fare_stop, "phase": found.phase}

def map_page(found: Found, sources: str, sections: list[dict[str, Any]], airport_minutes: int = 120, token: str = "",
             running: bool = False, max_lookups: int = 0, landing_minutes: int = TripSettings.min_landing_minutes) -> str:
    """airport_minutes is how long before a flight the traveller is at the airport (the planner's setting). token
    is the served page's (serve.py): without it the page shows the command to open the planner instead of a button,
    and does not ask for the destinations again. running says the timetables are still being looked up."""
    data = {"origin": {"name": found.place.label, "short": found.place.english_name, "lat": found.place.latitude, "lon": found.place.longitude},
            "days": str(found.days), "round_trip": found.days.round_trip, "sources": sources, "airport_minutes": airport_minutes,
            "long_wait": LONG_WAIT_MINUTES, "early_hour": EARLY_AIRPORT_HOUR, "late_hour": LATE_LANDING_HOUR, "token": token,
            "max_lookups": max_lookups, "kiwi_why": found.kiwi_why, "texts": page_texts(), "locale": current.SETTINGS.language, "landing_minutes": landing_minutes, "grade_colours": LIGHT_COLOURS, "state": map_state(found, sections, running, 0, airport_minutes, landing_minutes)}
    title = tr("discover_page_tab_title", origin=found.place.english_name)
    return PAGE.replace("__DATA__", as_script({**data, "title": title})).replace("__TITLE__", title)

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<style>
  :root { --bg: #ffffff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; --panel: #f5f5f7; --link: #0a66c2; --pick: #fff6d6; }
  @media (prefers-color-scheme: dark) { :root { --bg: #1c1c1e; --fg: #f2f2f7; --muted: #a1a1a6; --line: #3a3a3c; --panel: #2c2c2e; --link: #6cb4ff; --pick: #3d3519; } }
  body { margin: 0; font: 14px/1.4 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
  a { color: var(--link); }
  header { padding: 12px 16px; } h1 { font-size: 18px; margin: 0 0 4px; } header p { margin: 0; color: var(--muted); }
  h2 { font-size: 16px; margin: 28px 16px 4px; } h3 { font-size: 14px; margin: 20px 16px 4px; } p.note { margin: 4px 16px 8px; color: var(--muted); max-width: 70em; }
  #map { height: 58vh; } .legend span { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin: 0 4px 0 12px; }
  /* Whether the program is still searching, or has seen everything: the first thing to read on the page */
  #status { margin: 8px 0 4px; padding: 8px 12px; border-radius: 8px; border-left: 4px solid; color: var(--fg); max-width: 70em; }
  #status.running { background: #fff4d6; border-color: #e0a100; } #status.done { background: #e6f4ea; border-color: #1e8e3e; }
  #status.lost { background: #fde7e9; border-color: #b3261e; }
  @media (prefers-color-scheme: dark) { #status.running { background: #3d3519; } #status.done { background: #1e3325; } #status.lost { background: #3d1f1f; } }
  #status p.kiwi { margin: 0 0 4px; font-weight: 600; color: #b3261e; } @media (prefers-color-scheme: dark) { #status p.kiwi { color: #ff8a80; } }
  #status p.ground-status { margin: 4px 0 0; } #status strong { margin-right: 6px; } #status .pulse { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #e0a100;
    margin-right: 8px; animation: pulse 1.2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.25; } }
  @media (prefers-reduced-motion: reduce) { #status .pulse { animation: none; } }
  #detail { background: var(--panel); padding: 12px 16px 16px; border-bottom: 1px solid var(--line); }
  #detail h2 { margin: 0 0 2px; } #detail h4 { margin: 14px 0 4px; font-size: 14px; } #detail p { margin: 2px 0; }
  #detail .muted { color: var(--muted); } #detail ol { margin: 4px 0; padding-left: 20px; } #detail li { margin: 2px 0; }
  #detail ul.links { margin: 6px 0 0; padding-left: 20px; } #detail .close { float: right; }
  #detail .warn { color: #b3261e; } @media (prefers-color-scheme: dark) { #detail .warn { color: #ff8a80; } }
  table { border-collapse: collapse; width: 100%; } td, th { text-align: left; padding: 6px 16px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; } td.n { text-align: right; font-variant-numeric: tabular-nums; }
  #rows tr { cursor: pointer; } #rows tr:hover, #rows tr.picked { background: var(--pick); }
  .wrap { overflow-x: auto; } #tiles { margin: 4px 16px; color: var(--muted); }
  #detail pre { white-space: pre-wrap; word-break: break-all; background: var(--bg); padding: 6px 8px; border: 1px solid var(--line); }
  #filters { display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: center; padding: 6px 16px 10px; border-top: 1px solid var(--line); }
  #filters label { white-space: nowrap; } #filters input[type=range] { width: 140px; vertical-align: middle; }
  #filters .count { color: var(--muted); }
  /* The line of each option, as the planner draws it */
  .lines { margin: 8px 0 4px; } .lines .line { overflow-x: auto; } .lines svg { width: 100%; min-width: 560px; height: auto; display: block; font-size: 12px; }
  .lines p.line-title { margin: 10px 0 0; font-weight: 600; }
</style></head><body>
<header><h1 id="title"></h1><p id="about"></p><p id="status"></p><p class="legend" id="legend"></p></header>
<div id="filters"></div>
<div id="map"></div>
<p id="tiles" hidden></p>
<section id="detail" hidden></section>
<div class="wrap"><table><thead><tr id="head"></tr></thead><tbody id="rows"></tbody></table></div>
<div id="by-ground" hidden><h2 id="by-ground-title"></h2>
<p class="note" id="by-ground-note"></p>
<div class="wrap"><table><thead><tr id="ground-head"></tr></thead><tbody id="ground-rows"></tbody></table></div></div>
<div id="discarded"></div>
<h2 id="searched-title"></h2>
<div id="sections"></div>
<script>
const data = __DATA__;
let state = data.state;
let picked = null;   // the destination whose trip is open, by its airport
const colours = ["#2e9e4f", "#8fbf3f", "#e6b422", "#e67e22", "#d0453a"];
// A text of the page in the traveller's language (texts/languages, discover_page_...), its {values} filled in
const t = (key, values) => (data.texts[key] === undefined ? key : data.texts[key]).replace(/\{(\w+)\}/g, (all, name) => (values && name in values ? values[name] : all));
const modeName = mode => (data.texts["mode_" + mode] === undefined ? mode : data.texts["mode_" + mode]);
const eur = value => value.toFixed(0) + " €";
const add = (parent, tag, text, kind) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (kind) element.className = kind; parent.append(element); return element; };
const place = code => (state.names[code] || code) + " (" + code + ")";
const day = text => new Date(text + "T12:00:00").toLocaleDateString(data.locale, {weekday: "short", day: "numeric", month: "short"});
// The weekdays of a timetable (ground.py writes them Mon to Sun) in the page's language, from the 16th of November
// 2026, a Monday
const weekdayNames = Object.fromEntries(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((code, i) =>
  [code, new Date(2026, 10, 16 + i, 12).toLocaleDateString(data.locale, {weekday: "short"})]));
const weekdays = codes => codes.map(code => weekdayNames[code] || code).join(", ");
const when = (date, time) => t("when", {time: time, day: day(date)});
const hours = minutes => (minutes >= 1440 ? Math.floor(minutes / 1440) + "d " : "") + Math.floor(minutes % 1440 / 60) + "h" + String(minutes % 60).padStart(2, "0");
// The time in the city of a return trip, as 48h (2 nights); "about" when a way in or out was not looked up
const stayText = s => {
  if (!s) return "";
  const text = t(s.nights === 1 ? "stay_one" : "stay_many", {hours: Math.round(s.minutes / 60), nights: s.nights});
  return s.estimated ? t("about", {text: text}) : text;
};
// " · also by train" or "by bus" or both, when a train or bus goes there with no flight (ground.py)
const groundText = p => {
  if (!p.ground || !p.ground.length) return "";
  const priced = p.ground.filter(option => option.price && option.price.total_eur !== null).sort((a, b) => a.price.total_eur - b.price.total_eur);
  return " · " + t("also_by", {what: priced.length ? modeName(priced[0].mode) + " " + eur(priced[0].price.total_eur) + (priced[0].price.stay ? ", " + stayText(priced[0].price.stay) : "")
                                                    : [...new Set(p.ground.map(option => option.mode))].sort().reverse().map(modeName).join(t("or"))});
};
// The price of a city with no flight (ground_fares.py), null while none of its trains or buses is priced
const fareOf = g => (g.fare_eur === undefined ? null : g.fare_eur);
// The trains and buses of a city, the ones with a price first, cheapest first; then the others, as they came (quickest first)
const totalOf = option => (option.price && option.price.total_eur !== null ? option.price.total_eur : null);
const byPrice = options => [...options].sort((a, b) => (totalOf(a) === null) - (totalOf(b) === null) || (totalOf(a) || 0) - (totalOf(b) || 0));
// The cheapest train or bus of a city priced in full, or null
const cheapestGround = options => byPrice(options || []).find(option => totalOf(option) !== null) || null;
// A city's price on the map: the cheapest way there, by air or by train or bus, as far as it is known
const cityPrice = p => Math.min(p.total_eur, ...(cheapestGround(p.ground) ? [totalOf(cheapestGround(p.ground))] : []));
const between = (date1, time1, date2, time2) => Math.round((new Date(date2 + "T" + time2) - new Date(date1 + "T" + time1)) / 60000);
const before = (date, time, minutes) => { const moment = new Date(new Date(date + "T" + time) - minutes * 60000);
  const two = number => String(number).padStart(2, "0");
  return [moment.getFullYear() + "-" + two(moment.getMonth() + 1) + "-" + two(moment.getDate()), two(moment.getHours()) + ":" + two(moment.getMinutes())]; };
const worst = legs => legs.reduce((grade, leg) => [grade, leg.grade, leg.price_grade].sort().pop(), "A");
const fromTo = (start, end) => t("from_to", {from: start, to: end});

document.documentElement.lang = data.locale;
document.getElementById("title").textContent = t(data.round_trip ? "title_return" : "title_one_way", {origin: data.origin.name});
document.getElementById("by-ground-title").textContent = t("by_ground_title");
document.getElementById("by-ground-note").textContent = t("by_ground_note");
document.getElementById("searched-title").textContent = t("searched_title");
const legend = document.getElementById("legend");
colours.forEach((colour, i) => { const dot = document.createElement("span"); dot.style.background = colour; legend.append(dot, i === 0 ? t("legend_cheapest") : i === colours.length - 1 ? t("legend_dearest") : ""); });
legend.append(" · " + t("legend_ring"));
const GROUND_COLOUR = "#7a4fd6";   // a city reached by train or bus only: no price, so none of the price colours
if ((state.by_ground || []).length) {
  const ring = document.createElement("span"); ring.style.border = "2px solid " + GROUND_COLOUR; ring.style.width = ring.style.height = "6px";
  legend.append(ring, t("legend_ground"));
}
const map = L.map("map").setView([data.origin.lat, data.origin.lon], 4);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 12, attribution: "© OpenStreetMap"}).addTo(map)
  .once("tileerror", () => { const note = document.getElementById("tiles"); note.hidden = false; note.textContent = t("tiles_failed"); });
L.circleMarker([data.origin.lat, data.origin.lon], {radius: 8, color: "#1f6fd0", fillOpacity: 1}).addTo(map).bindTooltip(data.origin.name);
const markers = L.layerGroup().addTo(map);
let line = null;

// One leg of the trip, flight by flight, with the connections between them. What the traveller should know about a
// connection before booking it goes into warnings. toAirport and fromAirport are the ways to and from its airports:
// once looked up in the timetables, the warnings about the hour are theirs to give, not the map's.
function describeLeg(parent, title, leg, warnings, toAirport, fromAirport) {
  add(parent, "h4", title + " — " + leg.airline + ", " + (leg.stops ? t(leg.stops > 1 ? "stops" : "stop", {count: leg.stops}) : t("direct")));
  const [date, time] = before(leg.date, leg.time, data.airport_minutes);
  if (!(toAirport && toAirport.looked_up) && (date !== leg.date || Number(time.slice(0, 2)) < data.early_hour))
    warnings.push(t("warn_early", {airport: place(leg.from), when: when(date, time)}));
  add(parent, "p", t("be_at", {airport: place(leg.from), when: when(date, time), minutes: data.airport_minutes}));
  const steps = add(parent, "ol");
  leg.segments.forEach((segment, index) => {
    if (index) {
      const previous = leg.segments[index - 1];
      const wait = between(previous.arrival_date, previous.arrival_time, segment.date, segment.time);
      const night = previous.arrival_date !== segment.date;
      add(steps, "li", t("change_at", {airport: place(segment.from)}) + (previous.to !== segment.from ? " " + t("change_other_airport", {airport: place(previous.to)}) : "")
          + ": " + t("change_wait", {hours: hours(wait)}) + (night ? t("through_night") : ""), "muted");
      if (previous.to !== segment.from)
        warnings.push(t("warn_other_airport", {from: place(previous.to), to: place(segment.from)}));
      if (night || wait >= data.long_wait)
        warnings.push(t("warn_long_wait", {hours: hours(wait), airport: place(segment.from), night: night ? t("through_night") : ""}));
      if ((previous.airline || "") !== (segment.airline || ""))
        warnings.push(t("warn_two_tickets", {first: previous.airline, second: segment.airline, airport: place(segment.from)}));
    }
    add(steps, "li", t("leave_land", {from: place(segment.from), leaves: when(segment.date, segment.time), to: place(segment.to),
                                      lands: segment.arrival_time ? when(segment.arrival_date, segment.arrival_time) : t("no_time")})
        + " — " + (segment.airline || leg.airline) + (segment.flight ? " " + segment.flight : ""));
  });
  const landing = Number((leg.arrival_time || "12").slice(0, 2));
  if (!(fromAirport && fromAirport.looked_up) && leg.arrival_time && (landing >= data.late_hour || landing < data.early_hour))
    warnings.push(t("warn_late", {airport: place(leg.to), when: when(leg.arrival_date, leg.arrival_time)}));
  add(parent, "p", t("arrive", {airport: place(leg.to), when: leg.arrival_time ? when(leg.arrival_date, leg.arrival_time) : t("no_time"),
                               after: leg.minutes ? t("after_takeoff", {hours: hours(leg.minutes)}) : ""}));
}

// The ways to and from the airports, each with its grades and where it comes from
function describeAccess(parent, p) {
  add(parent, "h4", t("to_from_airports"));
  const list = add(parent, "ul", undefined, "links");
  for (const leg of p.access || []) {
    const where = leg.to_airport ? fromTo(leg.place || t("the_city"), place(leg.airport)) : fromTo(place(leg.airport), leg.place || t("the_city"));
    const item = add(list, "li", t("access_item", {where: where, price: eur(leg.price_eur), duration: leg.minutes ? t("and_hours", {hours: hours(leg.minutes)}) : "",
                                                   times: leg.looked_up && leg.leave ? t("leaving_arriving", {leave: leg.leave, arrive: leg.arrive}) : "",
                                                   grade: leg.grade, price_grade: leg.price_grade})
        + " " + (leg.looked_up ? t("looked_up_for_flight", {how: leg.how}) : leg.how.charAt(0).toUpperCase() + leg.how.slice(1)) + ".",
        leg.looked_up ? "" : "muted");
    if (leg.url && leg.url.startsWith("https://")) {
      item.append(" ");
      const source = add(item, "a", t("source_link"));
      source.href = leg.url; source.target = "_blank"; source.rel = "noopener noreferrer";
    }
    if (leg.quote) add(item, "div", "“" + leg.quote + "”", "muted");
    // Every published way from that airport, so the traveller sees the choice and where each price was read
    if (leg.options && leg.options.length > 1) {
      const others = add(item, "ul");
      for (const option of leg.options) {
        const line = add(others, "li", t("published_option", {what: [option.operator, option.mode ? modeName(option.mode) : ""].filter(Boolean).join(" "),
                                                             to: option.to ? t("to_place", {place: option.to}) : "", from: option.from_price ? t("from_price") : "",
                                                             price_text: option.price_text, price: eur(option.price_eur), source: option.source}) + " ", "muted");
        if (option.url && option.url.startsWith("https://")) {
          const link = add(line, "a", t("source_link_small"));
          link.href = option.url; link.target = "_blank"; link.rel = "noopener noreferrer";
        }
      }
    }
  }
}

// Times as minutes from midnight of the day the trip starts, and back: a time past midnight says the day it falls on
const minutesOf = time => Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
const clock = minutes => { const two = number => String(number).padStart(2, "0"), days = Math.floor(minutes / 1440), rest = minutes - days * 1440;
  return two(Math.floor(rest / 60)) + ":" + two(rest % 60) + (days > 0 ? " (+" + days + "d)" : days < 0 ? " (" + days + "d)" : ""); };
const SVG = "http://www.w3.org/2000/svg";
const svgAdd = (parent, tag, attributes, text) => { const element = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text; parent.append(element); return element; };

// An option drawn as the planner draws it (gui/output.py): a straight line from the first point to the last, each
// part as long as its share of the time, in the colour of the grade of its times, a wait as a thin grey line, and
// at each point its time above and its place below. points has one more item than parts.
function drawLine(parent, title, points, parts) {
  add(parent, "p", title, "line-title");
  const shortest = 78, edge = 6, y = 30, room = Math.max(760, parts.length * shortest + 2 * edge);
  const svg = svgAdd(add(parent, "div", undefined, "line"), "svg", {viewBox: "0 0 " + room + " 64", role: "img", "aria-label": title});
  const total = parts.reduce((sum, [, minutes]) => sum + Math.max(1, minutes), 0);
  const xs = [edge];
  for (const [, minutes] of parts) xs.push(xs[xs.length - 1] + shortest + (room - 2 * edge - parts.length * shortest) * Math.max(1, minutes) / total);
  parts.forEach(([grade], i) => svgAdd(svg, "line", {x1: xs[i], x2: xs[i + 1], y1: y, y2: y, "stroke-width": grade ? 7 : 3,
                                                     stroke: grade ? data.grade_colours[grade] : "var(--line)"}));
  points.forEach(([time, place], i) => {
    const anchor = i === 0 ? "start" : i === points.length - 1 ? "end" : "middle";
    svgAdd(svg, "circle", {cx: xs[i], cy: y, r: 5, fill: "var(--bg)", stroke: "var(--fg)", "stroke-width": 2});
    svgAdd(svg, "text", {x: xs[i], y: y - 10, "text-anchor": anchor, "font-weight": 700, fill: "var(--fg)"}, time);
    svgAdd(svg, "text", {x: xs[i], y: y + 22, "text-anchor": anchor, fill: "var(--muted)"}, place);
  });
}

// One way of a flight as the planner's line: from the city to the airport, the wait there, the flight, the wait
// after landing and the way into the city. A way to or from an airport looked up for this flight has its own times;
// the others are worked out from the flight and the minutes known, and written with "~".
function flightLine(parent, title, leg, toAirport, fromAirport, start, end) {
  toAirport = toAirport || {grade: "C", minutes: 0}; fromAirport = fromAirport || {grade: "C", minutes: 0};
  const departs = minutesOf(leg.time), atAirport = departs - data.airport_minutes;
  const flying = leg.minutes || Math.max(0, between(leg.date, leg.time, leg.arrival_date || leg.date, leg.arrival_time || leg.time));
  // The landing in the local time of the airport landed at, as the flight gives it: the way into the city is in that time
  const lands = leg.arrival_time ? minutesOf(leg.arrival_time) + 1440 * Math.round(between(leg.date, "12:00", leg.arrival_date || leg.date, "12:00") / 1440)
                                 : departs + flying;
  const known = way => way.looked_up && way.leave && way.arrive;
  const reach = known(toAirport) ? minutesOf(toAirport.arrive) - (minutesOf(toAirport.arrive) > departs ? 1440 : 0) : atAirport;
  const leave = known(toAirport) ? reach - ((minutesOf(toAirport.arrive) - minutesOf(toAirport.leave) + 1440) % 1440) : atAirport - toAirport.minutes;
  let out = lands + data.landing_minutes;
  if (known(fromAirport)) { out = minutesOf(fromAirport.leave); while (out < lands) out += 1440; }
  const arrive = known(fromAirport) ? out + (minutesOf(fromAirport.arrive) - minutesOf(fromAirport.leave) + 1440) % 1440 : out + fromAirport.minutes;
  const guess = (way, minutes) => (known(way) ? "" : "~") + clock(minutes);
  drawLine(parent, title, [[guess(toAirport, leave), start], [guess(toAirport, reach), leg.from], [clock(departs), "✈"], [clock(lands), leg.to],
                           [guess(fromAirport, out), ""], [guess(fromAirport, arrive), end]],
           [[toAirport.grade, reach - leave], [null, departs - reach], ["A", flying], [null, out - lands], [fromAirport.grade, arrive - out]]);
}

// An operator with its mode, unless its name says it already: "ÖBB (train)", "FlixBus", "BlaBlaCar Bus"
const operatorText = option => option.operator + (option.operator.toLowerCase().includes(option.mode) ? "" : " (" + modeName(option.mode) + ")");

// A train or bus straight to a city as the flights are drawn, the way there and, for a return trip, the way back:
// each the ride priced for a day (grade A), or else what the timetables give (grade B): the first departure of the
// week read there, the first straight one back on the days to come back on (ground.ways_back)
function groundLine(parent, option) {
  const out = option.price && option.price.out, back = option.price && option.price.back;
  const what = operatorText(option);
  const ride = (title, leg, from, to) => {
    const leaves = minutesOf(leg.leave), arrives = minutesOf(leg.arrive) + 1440 * Math.round(between(leg.day, "12:00", leg.arrive_day, "12:00") / 1440);
    drawLine(parent, title + ", " + what + ", " + day(leg.day) + ": " + eur(leg.price_eur), [[clock(leaves), from], [clock(arrives), to]], [[leg.grade, arrives - leaves]]);
  };
  if (out) ride(data.round_trip ? t("out") : t("by_mode", {mode: modeName(option.mode)}), out, option.from_stop, option.stop);
  else {
    const leaves = minutesOf(option.times[0]);
    drawLine(parent, (data.round_trip ? t("out") + ", " : "") + t("ground_no_price_line", {what: what, time: option.times[0], weekdays: weekdays(option.weekdays)}),
             [[clock(leaves), option.from_stop], [clock(leaves + option.minutes), option.stop]], [[option.grade, option.minutes]]);
  }
  if (!data.round_trip) return;
  if (back) {
    ride(option.price.stay ? t("back_after", {stay: stayText(option.price.stay)}) : t("back"), back, option.stop, option.from_stop);
    return;
  }
  const timetable = option.back;   // "dd/mm hh:mm" of each straight departure back, in the city's time
  if (timetable && timetable.times.length) {
    const [date, time] = timetable.times[0].split(" ");
    const leaves = minutesOf(time);
    drawLine(parent, t("back_timetable_line", {what: what, why: out ? t("not_sold_days") : t("no_price"), time: time, date: date})
             + (timetable.times.length > 1 ? " " + t("more_count", {count: timetable.times.length - 1}) : ""),
             [[clock(leaves), option.stop], [clock(leaves + timetable.minutes), option.from_stop]], [["B", timetable.minutes]]);
    return;
  }
  add(parent, "p", t("back") + ", " + what + ": " + (timetable === null ? t("back_no_answer")
      : timetable ? t("back_none_straight") : state.running ? t("back_not_yet") : t("back_not_looked_up")), "muted");
}

function describeLines(panel, p, ground) {
  const box = add(panel, "div", undefined, "lines");
  const access = p ? p.access || [] : [];
  if (p) {
    flightLine(box, t(p.back ? "flight_out_line" : "flight_line", {airline: p.airline, day: day(p.date), total: eur(p.total_eur)}),
               p, access[0], access[1], data.origin.short, p.name);
    if (p.back) flightLine(box, t("flight_back_line", {airline: p.back.airline, day: day(p.back.date)}), p.back, access[2], access[3], p.name, data.origin.short);
  }
  for (const option of byPrice(ground || [])) groundLine(box, option);
  const legend = add(box, "p", "", "muted");
  legend.append(t("colours_intro") + " ");
  [["A", t("grade_a")], ["B", t("grade_b")], ["C", t("grade_c")]].forEach(([grade, text]) => {
    const dot = add(legend, "span", "●"); dot.style.color = data.grade_colours[grade]; legend.append(" " + text + " · ");
  });
  legend.append(t("colours_end"));
}

// Where each part of a trip is sold, as the planner's "where to book": the flight, and each way to and from the
// airports that was looked up for these flights and is sold online. A way with no such link says so.
function describeBooking(panel, p) {
  add(panel, "h4", t("where_to_book"));
  const list = add(panel, "ul", undefined, "links");
  const link = (item, text, url) => { const anchor = add(item, "a", text); anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; };
  for (const booking of p.links.filter(link => link.kind === "book" && link.url.startsWith("https://"))) link(add(list, "li"), booking.label, booking.url);
  for (const way of p.access || []) {
    const item = add(list, "li", (way.to_airport ? fromTo(way.place || t("the_city"), place(way.airport)) : fromTo(place(way.airport), way.place || t("the_city"))) + ": ");
    const urls = (way.urls || []).filter(url => url.startsWith("https://"));
    if (urls.length) urls.forEach((url, i) => { if (i) item.append(", "); link(item, url.replace("https://", "").split("/")[0], url); });
    else item.append(way.looked_up ? t("no_seller") : t("not_looked_up_seller"));
  }
}

// The trains and buses that go straight to a city (ground.py): their timetable, the way back, and where to buy
// them, at the price the map does not have
function describeGround(panel, options) {
  const list = add(panel, "ul");
  for (const option of byPrice(options)) {
    const times = option.times.slice(0, 4).join(", ") + (option.times.length > 4 ? " " + t("and_more_count", {count: option.times.length - 4}) : "");
    const item = add(list, "li", t("ground_item", {what: operatorText(option), from: option.from_stop, times: times, weekdays: weekdays(option.weekdays),
                                                   hours: hours(option.minutes), to: option.stop}) + " ");
    if (option.back === null) item.append(t("back_unknown") + " ");
    else if (option.back) item.append((option.back.times.length
        ? t("back_straight", {stop: option.stop, times: option.back.times.slice(0, 4).join(", ") + (option.back.times.length > 4 ? " " + t("and_more") : ""),
                              hours: hours(option.back.minutes)})
        : t("no_straight_back", {operator: option.operator, days: option.back.days > 1 ? t("first_days_back", {count: option.back.days}) : day(option.back.day)})) + " ");
    const priced = option.price, out = priced && priced.out, back = priced && priced.back;
    if (option.back === undefined && data.round_trip && !back) item.append(t("back_not_asked") + " ");
    if (out) {   // read from the operator for that day (ground_fares.py): grade A
      const way = (leg, from, to) => t("ground_way", {price: eur(leg.price_eur), day: day(leg.day), leave: leg.leave, arrive: leg.arrive,
                                                      next_day: leg.arrive_day !== leg.day ? " " + t("next_day") : "", from: from, to: to});
      add(item, "strong", (priced.total_eur !== null ? eur(priced.total_eur) + (back ? " " + t("there_and_back") : "") + ". " : ""));
      item.append(t("sold_by", {seller: out.seller, way: way(out, option.from_stop, option.stop)})
        + (back ? "; " + t("back_way", {way: way(back, option.stop, option.from_stop)}) : data.round_trip ? "; " + t("no_way_back_sold") : "")
        + ", " + t("cheapest_days")
        + (priced.stay ? ": " + t("ground_stay", {stay: stayText(priced.stay), stop: option.stop}) : "") + ". ");
      for (const [label, leg] of [[t("buy_there"), out], [t("buy_back"), back]]) {
        const url = leg && (leg.url || "");
        if (!url.startsWith("https://")) continue;
        const anchor = add(item, "a", label); anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; item.append(" ");
      }
      continue;
    }
    if (priced) item.append(t("not_sold_by", {operator: option.operator}) + " ");
    else if (option.sold) item.append(t("price_not_asked") + " ");
    item.append(t("buy_it_on", {grade: option.grade}) + " ");
    const site = add(item, "a", (option.site || "").replace("https://", "").split("/")[0] || t("operator_site", {operator: option.operator}));
    if ((option.site || "").startsWith("https://")) { site.href = option.site; site.target = "_blank"; site.rel = "noopener noreferrer"; }
    item.append(t("searching_on_day", {from: option.from_stop, to: option.stop}));
  }
}

// A city no flight goes to, reached straight by train or bus: its options only, with no price and no planner
function explainGround(g, scroll) {
  picked = "~" + g.key;
  const panel = document.getElementById("detail");
  panel.replaceChildren();
  panel.hidden = false;
  const close = add(panel, "button", t("close"), "close");
  close.onclick = () => { panel.hidden = true; picked = null; if (line) line.remove(); };
  add(panel, "h2", data.origin.name + " → " + g.name);
  describeLines(panel, null, g.ground);
  add(panel, "p", t("no_flight_here", {modes: g.modes.map(modeName).join(t("and")), origin: data.origin.name, hours: hours(g.minutes)}) + " "
      + (fareOf(g) !== null ? t("ground_ranked", {price: eur(fareOf(g)) + (data.round_trip ? " " + t("there_and_back") : "")}) : t("ground_not_ranked")), "muted");
  describeGround(panel, g.ground);
  add(panel, "h4", t("links"));
  const anchor = add(add(add(panel, "ul", undefined, "links"), "li"), "a", t("by_public_transport", {from: data.origin.name, to: g.name}));
  anchor.href = "https://www.google.com/maps/dir/?" + new URLSearchParams({api: 1, origin: data.origin.name, destination: g.ground[0].stop, travelmode: "transit"});
  anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
  if (line) line.remove();
  line = L.polyline([[data.origin.lat, data.origin.lon], [g.lat, g.lon]], {color: GROUND_COLOUR, weight: 2, dashArray: "4 6"}).addTo(map);
  document.querySelectorAll("#rows tr, #ground-rows tr").forEach(row => row.classList.toggle("picked", row.dataset.to === picked));
  if (scroll !== false) panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

function explain(p, scroll) {
  picked = p.to;
  const panel = document.getElementById("detail");
  panel.replaceChildren();
  panel.hidden = false;
  const close = add(panel, "button", t("close"), "close");
  close.onclick = () => { panel.hidden = true; picked = null; if (line) line.remove(); };
  add(panel, "h2", data.origin.name + " → " + p.name + " (" + p.to + ", " + p.country + ")");
  describeLines(panel, p, p.ground);
  add(panel, "p", t("total_line", {total: eur(p.total_eur), stay: p.stay ? ", " + t("in_the_city", {stay: stayText(p.stay)}) : "",
                                   flights: t(p.back ? "flights_there_back" : "flight_one"), price: eur(p.price_eur), source: p.source, ground: eur(p.ground_eur)})
      + " " + (p.confirmed ? t("all_looked_up") : t("not_all_looked_up")), "muted");
  if (p.also && p.also.length)
    add(panel, "p", t("also_to", {city: p.name, others: p.also.map(other => place(other.to) + " " + t("from_airport", {airport: other.from}) + ", " + eur(other.total_eur)
        + " (" + (other.access && other.access.every(leg => leg.looked_up) ? t("looked_up") : t("estimated")) + ")").join("; ")}), "muted");
  if (p.ground && p.ground.length) {   // trains and buses to the same city, with no flight: timetables only (ground.py), never a price
    add(panel, "h4", t("also_no_flight"));
    describeGround(panel, p.ground);
  }
  const warnings = [];
  const warning = add(panel, "div");
  const access = p.access || [];
  describeLeg(panel, t("out"), p, warnings, access[0], access[1]);
  if (p.back) {
    add(panel, "p", t("landing_to_back", {hours: hours(between(p.arrival_date || p.date, p.arrival_time || p.time, p.back.date, p.back.time))})
        + (p.stay ? ": " + t("stay_detail", {stay: stayText(p.stay), city: p.name, minutes: data.airport_minutes})
           + (p.stay.estimated ? t("stay_estimated") : "") : "") + ".", "muted");
    describeLeg(panel, t("back"), p.back, warnings, access[2], access[3]);
  }
  describeAccess(panel, p);
  if (warnings.length) {
    add(warning, "h4", t("before_booking"), "warn");
    const list = add(warning, "ul", undefined, "links");
    warnings.forEach(text => add(list, "li", text, "warn"));
  }
  describeBooking(panel, p);
  add(panel, "h4", t("links"));
  const list = add(panel, "ul", undefined, "links");
  for (const link of p.links) {
    if (!link.url.startsWith("https://") || link.kind === "book") continue;
    const anchor = add(add(list, "li"), "a", link.label);
    anchor.href = link.url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
  }
  add(panel, "p", t("price_may_change"), "muted");
  describePlan(panel, p);
  if (line) line.remove();
  line = L.polyline([[data.origin.lat, data.origin.lon], [p.lat, p.lon]], {color: "#1f6fd0", weight: 2, dashArray: "4 6"}).addTo(map);
  document.querySelectorAll("#rows tr, #ground-rows tr").forEach(row => row.classList.toggle("picked", row.dataset.to === p.to));
  if (scroll !== false) panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

// The way to check the trip in the planner, which looks up the ground transport in real timetables: a button when
// the page is served, the command to run when it was opened as a file.
function describePlan(panel, p) {
  const legs = [[t("the_way_there"), p.plan.out], [t("the_way_back"), p.plan.back]].filter(([, plan]) => plan);
  if (!legs.length) return;
  add(panel, "h4", t("check_in_planner"));
  add(panel, "p", t("planner_intro"), "muted");
  for (const [label, plan] of legs) {
    if (data.token) {
      const line = add(panel, "p");
      const button = add(line, "button", t("open_planner_on", {way: label}));
      const status = add(line, "span", "", "muted");
      button.onclick = () => {
        status.textContent = " ...";
        fetch("/plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: data.token, trip: plan.key})})
          .then(response => response.json().then(answer => { status.textContent = " " + t(response.ok ? "planner_is" : "planner_refused", {result: answer.result}); }))
          .catch(() => { status.textContent = " " + t("server_stopped_run", {command: plan.command}); });
      };
    } else {
      add(panel, "p", t("run_for", {way: label}));
      add(panel, "pre", plan.command);
    }
  }
}

const head = document.getElementById("head");
for (const [title, number] of [[t("col_destination")], [t("col_out")], [t("col_back")], [t("col_airline")], [t(data.round_trip ? "col_flights" : "col_flight"), 1],
                               [t("col_airports"), 1], [t("col_total"), 1], ...(data.round_trip ? [[t("col_in_city")]] : []), [t("col_timetables")]])
  add(head, "th", title, number ? "n" : "");
const leg = (flight, from) => from + " " + flight.date + " " + flight.time + (flight.stops ? " +" + flight.stops : "");
const rows = document.getElementById("rows");

// Whether the program is still searching or has seen everything. The flights of every destination were all
// searched before the page opened: what can still change while running is the ways to and from the airports, and
// so the prices and their order. lost: the map's server stopped answering, so what is shown is its last state.
let lost = false;
function describeStatus() {
  const status = document.getElementById("status");
  const looked = state.lookups.length, count = state.points.length;
  const missing = (state.coverage || {}).missing || [];
  status.replaceChildren();
  status.className = lost ? "lost" : state.running ? "running" : "done";
  if (data.kiwi_why)   // only Ryanair's flights: the first thing to know about this map
    add(status, "p", t("only_ryanair", {why: data.kiwi_why}), "kiwi");
  document.title = (state.running && !lost ? "⏳ " : "") + data.title;
  const ground = groundCount();
  if (state.running && !lost) {
    add(status, "span", undefined, "pulse");
    add(status, "strong", t("still_searching"));
    add(status, "span", t("flights_all_searched", {count: count}) + " "
        + (state.phase === "timetables" ? t("timetables_running", {looked: looked, most: data.max_lookups}) + " " : ""));
    // The trains and buses, as the flights: what was seen, and what is still being asked
    const train = add(status, "p", "", "ground-status");
    if (ground.reached) train.append(t("ground_reached", {reached: ground.reached, origin: data.origin.short, flown: ground.flown}) + " "
        + (state.phase === "prices" ? t("phase_prices", {priced: ground.priced})
           : state.phase === "timetables" ? t("phase_timetables", {priced: ground.priced})
           : state.phase === "ways back" ? t("phase_ways_back", {priced: ground.priced})
           : t("phase_other", {priced: ground.priced})));
    add(status, "p", t("updates_itself"), "ground-status");
    return;
  }
  add(status, "strong", lost ? t("stopped") : t("finished"));
  add(status, "span", (lost ? t("lost_server") + " " : "")
      + t("all_on_map", {count: count}) + " "
      + (looked ? t("looked_up_count", {count: looked}) + (state.confirm_stop ? "; " + t("lookups_stop", {stop: state.confirm_stop}) : "") + ". "
                : t("none_looked_up") + " ")
      + (missing.length ? t("missing_flown", {count: missing.length}) : ""));
  if (ground.reached)
    add(status, "p", t("ground_reached", {reached: ground.reached, origin: data.origin.short, flown: ground.flown}) + " "
        + t("ground_priced_end", {priced: ground.priced, stop: state.fare_stop ? ": " + t("pricing_stop", {stop: state.fare_stop}) : ""}), "ground-status");
}

// The cities reached by train or bus: with no flight, and with a flight too (flown); priced, those with a price
function groundCount() {
  const flown = state.points.filter(p => p.ground && p.ground.length);
  const all = [...(state.by_ground || []).map(g => g.ground), ...flown.map(p => p.ground)];
  return {reached: all.length, flown: flown.length, priced: all.filter(options => options.some(option => totalOf(option) !== null)).length};
}

// What the map and the lists show, chosen above the map, so that a crowded map can be thinned out: a destination
// left out by a filter is only hidden. price and hours are null for no limit. flights hides the flights; train and
// bus the trains and buses of every city, a flight going there or not: London by bus stays with its flight
// hidden. A train or bus with no price yet is not hidden by the price.
const filter = {flights: true, train: true, bus: true, price: null, hours: null, country: "", looked: false};
const regions = (() => { try { return new Intl.DisplayNames([data.locale, "en"], {type: "region"}); } catch (error) { return null; } })();
const countryName = code => { try { return (regions && regions.of(code)) || code; } catch (error) { return code; } };
const shownFlight = p => filter.flights && (filter.price === null || p.total_eur <= filter.price)
  && (filter.hours === null || !p.minutes || p.minutes <= filter.hours * 60)
  && (!filter.country || p.country === filter.country) && (!filter.looked || p.confirmed);
// The trains and buses of a city, with a flight or not, let through by the boxes of their modes, the hours on board,
// the country, and the price when they have one
const shownByGround = (options, country) => {
  if (!options || !options.length) return false;
  const best = cheapestGround(options), fare = best ? totalOf(best) : null;
  return options.some(option => filter[option.mode]) && (filter.hours === null || Math.min(...options.map(option => option.minutes)) <= filter.hours * 60)
    && (!filter.country || country === filter.country) && (filter.price === null || fare === null || fare <= filter.price);
};
const shownGround = g => shownByGround(g.ground, g.country);

// The filters, made again when the destinations change (render): their choices are kept in filter
function describeFilters() {
  const box = document.getElementById("filters");
  box.replaceChildren();
  const byGround = state.by_ground || [];
  const check = (key, text) => {
    const label = add(box, "label"), input = add(label, "input");
    input.type = "checkbox"; input.checked = filter[key];
    input.onchange = () => { filter[key] = input.checked; draw(); };
    label.append(" " + text);
  };
  // A slider up to the largest value of the map, its last step meaning no limit
  const slider = (key, top, step, text) => {
    const label = add(box, "label"), shown = add(label, "span"), input = add(label, "input");
    input.type = "range"; input.min = step; input.max = top; input.step = step;
    input.value = filter[key] === null ? top : Math.min(filter[key], top);
    const say = () => { shown.textContent = text(filter[key]) + " "; };
    input.oninput = () => { filter[key] = Number(input.value) >= top ? null : Number(input.value); say(); draw(); };
    say();
  };
  check("flights", t("filter_flights"));
  // The trains and buses of every city: the ones no flight goes to, and the ones a flight goes to as well
  const modes = new Set([...byGround, ...state.points].flatMap(city => (city.ground || []).map(option => option.mode)));
  if (modes.has("train")) check("train", t("filter_train"));
  if (modes.has("bus")) check("bus", t("filter_bus"));
  const top = Math.max(10, Math.ceil(Math.max(0, ...state.points.map(p => p.total_eur), ...byGround.map(g => fareOf(g) || 0)) / 10) * 10);
  slider("price", top, 10, value => t("filter_price", {limit: value === null ? t("any_price") : value + " €"}));
  const longest = Math.max(1, ...state.points.map(p => p.minutes || 0), ...byGround.map(g => g.minutes));
  slider("hours", Math.ceil(longest / 60), 1, value => t("filter_hours", {limit: value === null ? t("any_time") : value + " h"}));
  const countries = {};
  for (const p of [...state.points, ...byGround]) if (p.country) countries[p.country] = (countries[p.country] || 0) + 1;
  const label = add(box, "label", t("filter_country") + " ");
  const select = add(label, "select");
  add(select, "option", t("filter_all")).value = "";
  for (const code of Object.keys(countries).sort((a, b) => countryName(a).localeCompare(countryName(b), data.locale)))
    add(select, "option", countryName(code) + " (" + countries[code] + ")").value = code;
  if (!countries[filter.country]) filter.country = "";
  select.value = filter.country;
  select.onchange = () => { filter.country = select.value; draw(); };
  check("looked", t("filter_looked"));
  const reset = add(box, "button", t("show_all"));
  reset.onclick = () => { Object.assign(filter, {flights: true, train: true, bus: true, price: null, hours: null, country: "", looked: false}); describeFilters(); draw(); };
  add(box, "span", "", "count").id = "count";
}

// The dots and the rows that the filters let through. The colours are by the price among every destination, so a
// filter does not change the colour of a dot. A city is coloured by its cheapest way there, by air or by train or
// bus; one reached by train or bus too has a purple ring around its dot.
function draw() {
  const totals = [...state.points.map(cityPrice), ...(state.by_ground || []).map(fareOf).filter(fare => fare !== null)].sort((a, b) => a - b);
  const band = total => Math.min(colours.length - 1, Math.floor(colours.length * totals.filter(t => t < total).length / Math.max(1, totals.length)));
  markers.clearLayers();
  rows.replaceChildren();
  // A city a flight goes to is shown by its flight, by its trains and buses, or both, as the filters let each through
  const byAir = new Set(state.points.filter(shownFlight)), byLand = new Set(state.points.filter(p => shownByGround(p.ground, p.country)));
  for (const p of state.points.filter(p => byLand.has(p) && !byAir.has(p))) {   // its trains and buses only: as a city with no flight
    const best = cheapestGround(p.ground), fare = best ? totalOf(best) : null;
    L.circleMarker([p.lat, p.lon], {radius: 6, color: GROUND_COLOUR, weight: 2, fillColor: fare === null ? GROUND_COLOUR : colours[band(fare)],
                                    fillOpacity: fare === null ? 0.12 : 0.85})
      .addTo(markers).bindTooltip(p.name + " · " + t("by_modes", {modes: [...new Set(p.ground.map(option => option.mode))].map(modeName).join(t("or"))})
                                  + (fare === null ? "" : " · " + eur(fare)) + (best && best.price.stay ? " · " + stayText(best.price.stay) : "") + " " + t("flights_hidden"))
      .on("click", () => explain(p));
  }
  const points = state.points.filter(p => byAir.has(p));
  for (const p of points) {
    const both = byLand.has(p), colour = colours[band(both ? cityPrice(p) : p.total_eur)];
    if (both)
      L.circleMarker([p.lat, p.lon], {radius: 10, color: GROUND_COLOUR, weight: 2, fill: false, interactive: false}).addTo(markers);
    L.circleMarker([p.lat, p.lon], {radius: 7, fillColor: colour, color: p.confirmed ? "#1d1d1f" : colour, fillOpacity: 0.85, weight: p.confirmed ? 2 : 1})
      .addTo(markers).bindTooltip(p.name + " · " + eur(p.total_eur) + (p.stay ? " · " + stayText(p.stay) : "") + (both ? groundText(p) : "") + (p.confirmed ? " · " + t("looked_up") : "")).on("click", () => explain(p));
    const row = rows.insertRow();
    row.dataset.to = p.to;
    row.classList.toggle("picked", picked === p.to);
    row.onclick = () => { map.setView([p.lat, p.lon], Math.max(map.getZoom(), 5)); explain(p); };
    for (const [value, number] of [[p.name + " (" + p.to + ")" + groundText(p)], [leg(p, p.from)], [p.back ? leg(p.back, p.back.from) + (p.back.to !== p.from ? " → " + p.back.to : "") : "-"],
                                   [p.airline + (p.back && p.back.airline !== p.airline ? " / " + p.back.airline : "")],
                                   [eur(p.price_eur), 1], [eur(p.ground_eur) + " (" + worst(p.access || []) + ")", 1], [eur(p.total_eur), 1], ...(data.round_trip ? [[stayText(p.stay)]] : []),
                                   [p.confirmed ? t("looked_up") : (p.access || []).some(leg => leg.looked_up || leg.checked) ? t("partly") : t("estimated")]]) {
      const cell = row.insertCell(); cell.textContent = value; if (number) cell.className = "n";
    }
  }
  const byGround = state.by_ground || [];
  const ground = byGround.filter(shownGround);
  for (const g of ground) {
    const by = [...new Set(g.ground.map(option => option.operator))].join(", ");
    const fare = fareOf(g), best = cheapestGround(g.ground);
    // A city priced has the colour of its price among every destination, in the purple ring of a city with no flight
    L.circleMarker([g.lat, g.lon], {radius: 6, color: GROUND_COLOUR, weight: 2, fillColor: fare === null ? GROUND_COLOUR : colours[band(fare)],
                                    fillOpacity: fare === null ? 0.12 : 0.85})
      .addTo(markers).bindTooltip(g.name + " · " + t("no_flight_modes", {modes: g.modes.map(modeName).join(t("or"))}) + (fare === null ? "" : " · " + eur(fare))
                                  + (best && best.price.stay ? " · " + stayText(best.price.stay) : "") + ", " + hours(g.minutes) + " · " + by)
      .on("click", () => explainGround(g));
  }
  // Every city reached by train or bus, with no flight or with one: the priced ones first, cheapest first, as the
  // flights; then the others, quickest first
  const listed = [...ground.map(g => ({city: g, options: g.ground, flight: null, key: "~" + g.key, open: () => explainGround(g)})),
                  ...state.points.filter(p => byLand.has(p)).map(p => ({city: p, options: p.ground, flight: p, key: p.to, open: () => explain(p)}))];
  const priceOf = entry => (cheapestGround(entry.options) ? totalOf(cheapestGround(entry.options)) : null);
  const quickest = entry => Math.min(...entry.options.map(option => option.minutes));
  listed.sort((a, b) => (priceOf(a) === null) - (priceOf(b) === null) || (priceOf(a) || 0) - (priceOf(b) || 0) || quickest(a) - quickest(b));
  document.getElementById("by-ground").hidden = !listed.length && !byGround.length;
  const groundRows = document.getElementById("ground-rows");
  groundRows.replaceChildren();
  for (const entry of listed) {
    const {city, options, flight} = entry, best = cheapestGround(options), fare = priceOf(entry);
    const row = groundRows.insertRow();
    row.dataset.to = entry.key;
    row.classList.toggle("picked", picked === entry.key);
    row.onclick = () => { map.setView([city.lat, city.lon], Math.max(map.getZoom(), 5)); entry.open(); };
    const back = options.map(option => option.back).filter(Boolean);
    const asked = options.some(option => option.price);
    const first = byPrice(options)[0];
    for (const [value, number] of [[city.name], [fare !== null ? eur(fare) : asked ? t("not_sold_those_days") : "-", 1],
                                   ...(data.round_trip ? [[best && best.price.stay ? stayText(best.price.stay) : ""]] : []),
                                   [flight ? eur(flight.total_eur) : t("none"), 1], [[...new Set(options.map(option => option.mode))].map(modeName).join(", ")],
                                   [[...new Set(options.map(option => option.operator))].join(", ")], [hours(quickest(entry))],
                                   [first.times.slice(0, 3).join(", ") + (first.times.length > 3 ? " …" : "")],
                                   ...(data.round_trip ? [[options.some(option => option.price && option.price.back) ? t("sold_for_days")
                                                           : back.some(way => way.times.length) ? t("straight_back") : back.length ? t("none_straight") : t("not_looked_up")]] : [])]) {
      const cell = row.insertCell(); cell.textContent = value; if (number) cell.className = "n";
    }
  }
  const count = document.getElementById("count");
  const reachable = byGround.length + state.points.filter(p => p.ground && p.ground.length).length;
  if (count) count.textContent = t("showing", {shown: points.length, all: state.points.length})
    + (reachable ? " " + t("showing_ground", {shown: listed.length, all: reachable}) : "");
}

const groundHead = document.getElementById("ground-head");
for (const [title, number] of [[t("col_city")], [t(data.round_trip ? "col_there_back" : "col_price"), 1], ...(data.round_trip ? [[t("col_in_city")]] : []),
                               [t("col_flight_all"), 1], [t("col_by")], [t("col_operators")], [t("col_quickest")], [t("col_leaves")], ...(data.round_trip ? [[t("col_back_timetable")]] : [])])
  add(groundHead, "th", title, number ? "n" : "");

function render() {
  document.getElementById("about").textContent = t("about_line", {days: data.days, count: state.points.length, sources: data.sources});
  const seen = state.coverage || {};
  if (seen.flown)   // how much of what is flown from here the map found: the honest measure of what it may miss
    document.getElementById("about").textContent += " " + t("coverage_line", {found: seen.found, flown: seen.flown, airports: seen.airports.join(", ")})
      + (seen.ryanair_no_fare ? t("coverage_ryanair", {count: seen.ryanair_no_fare}) : "")
      + (seen.missing.length ? t("coverage_missing", {count: seen.missing.length}) : ".");
  describeStatus();
  describeFilters();
  draw();
  const discarded = document.getElementById("discarded");
  discarded.replaceChildren();
  if (state.discarded.length) {
    add(discarded, "h2", t("left_out_title"));
    const list = add(add(discarded, "div", undefined, "note"), "ul");
    for (const gone of state.discarded) add(list, "li", t("left_out_item", {city: place(gone.to), from: gone.from, date: gone.date, reason: gone.reason}));
  }
  const sections = document.getElementById("sections");
  sections.replaceChildren();
  for (const part of state.sections) {
    add(sections, "h3", part.title);
    if (part.note) add(sections, "p", part.note, "note");
    if (!part.header.length) continue;
    const table = add(add(sections, "div", undefined, "wrap"), "table");
    const top = table.createTHead().insertRow();
    part.header.forEach(title => add(top, "th", title));
    const body = table.createTBody();
    for (const values of part.rows) { const row = body.insertRow(); values.forEach(value => { row.insertCell().textContent = value; }); }
  }
  const open = picked && state.points.find(p => p.to === picked);
  const openGround = picked && (state.by_ground || []).find(g => "~" + g.key === picked);
  if (open) explain(open, false);
  else if (openGround) explainGround(openGround, false);
}

// While the timetables are looked up, the served page asks for the destinations again
function follow() {
  if (!data.token || !state.running) return;
  setTimeout(() => fetch("/state").then(response => response.json())
    .then(next => { if (next.version !== state.version) { state = next; render(); } follow(); })
    .catch(() => { lost = true; describeStatus(); }), 4000);
}
render();
follow();
</script></body></html>
"""
