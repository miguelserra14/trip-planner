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
from trip_planner.trip.trip_settings import TripSettings
from trip_planner.trip.places import load_airports

ROOT = Path(__file__).parents[1]

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
    """The links of one trip: where to book it, where to compare it, how to reach the airport and how to get home."""
    name = lambda code: f"{(airports.get(code) or {}).get('name', code)} ({code})"
    found_links = []
    if flight.get("url", "").startswith("https://"):
        found_links.append({"label": f"Book on {'Ryanair' if flight['source'] == 'Ryanair' else 'Kiwi.com'}", "url": flight["url"]})
    found_links.append({"label": "Compare on Google Flights", "url": google_flights(flight)})
    found_links.append({"label": f"From {found.place.label} to {name(flight['from'])} by public transport",
                        "url": transit(found.place.label, airport_place(flight["from"], airports))})
    if flight.get("back"):
        found_links.append({"label": f"From {name(flight['back']['to'])} back to {found.place.label} by public transport",
                            "url": transit(airport_place(flight["back"]["to"], airports), found.place.label)})
    return found_links

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
            "coverage": found.coverage}

def map_page(found: Found, sources: str, sections: list[dict[str, Any]], airport_minutes: int = 120, token: str = "",
             running: bool = False, max_lookups: int = 0, landing_minutes: int = TripSettings.min_landing_minutes) -> str:
    """airport_minutes is how long before a flight the traveller is at the airport (the planner's setting). token
    is the served page's (serve.py): without it the page shows the command to open the planner instead of a button,
    and does not ask for the destinations again. running says the timetables are still being looked up."""
    data = {"origin": {"name": found.place.label, "lat": found.place.latitude, "lon": found.place.longitude},
            "days": str(found.days), "round_trip": found.days.round_trip, "sources": sources, "airport_minutes": airport_minutes,
            "long_wait": LONG_WAIT_MINUTES, "early_hour": EARLY_AIRPORT_HOUR, "late_hour": LATE_LANDING_HOUR, "token": token,
            "max_lookups": max_lookups, "kiwi_why": found.kiwi_why, "state": map_state(found, sections, running, 0, airport_minutes, landing_minutes)}
    title = f"Discover from {found.place.english_name}"
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
  #status strong { margin-right: 6px; } #status .pulse { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #e0a100;
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
</style></head><body>
<header><h1 id="title"></h1><p id="about"></p><p id="status"></p><p class="legend" id="legend"></p></header>
<div id="map"></div>
<p id="tiles" hidden></p>
<section id="detail" hidden></section>
<div class="wrap"><table><thead><tr id="head"></tr></thead><tbody id="rows"></tbody></table></div>
<div id="discarded"></div>
<h2>What was searched</h2>
<div id="sections"></div>
<script>
const data = __DATA__;
let state = data.state;
let picked = null;   // the destination whose trip is open, by its airport
const colours = ["#2e9e4f", "#8fbf3f", "#e6b422", "#e67e22", "#d0453a"];
const eur = value => value.toFixed(0) + " €";
const add = (parent, tag, text, kind) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (kind) element.className = kind; parent.append(element); return element; };
const place = code => (state.names[code] || code) + " (" + code + ")";
const day = text => new Date(text + "T12:00:00").toLocaleDateString("en-GB", {weekday: "short", day: "numeric", month: "short"});
const when = (date, time) => time + " on " + day(date);
const hours = minutes => (minutes >= 1440 ? Math.floor(minutes / 1440) + "d " : "") + Math.floor(minutes % 1440 / 60) + "h" + String(minutes % 60).padStart(2, "0");
// The time in the city of a return trip, as 48h (2 nights); "about" when a way in or out was not looked up
const stayText = s => s ? (s.estimated ? "about " : "") + Math.round(s.minutes / 60) + "h (" + s.nights + (s.nights === 1 ? " night)" : " nights)") : "";
const between = (date1, time1, date2, time2) => Math.round((new Date(date2 + "T" + time2) - new Date(date1 + "T" + time1)) / 60000);
const before = (date, time, minutes) => { const moment = new Date(new Date(date + "T" + time) - minutes * 60000);
  const two = number => String(number).padStart(2, "0");
  return [moment.getFullYear() + "-" + two(moment.getMonth() + 1) + "-" + two(moment.getDate()), two(moment.getHours()) + ":" + two(moment.getMinutes())]; };
const worst = legs => legs.reduce((grade, leg) => [grade, leg.grade, leg.price_grade].sort().pop(), "A");

document.getElementById("title").textContent = (data.round_trip ? "Cheapest return trips from " : "Cheapest flights from ") + data.origin.name;
const legend = document.getElementById("legend");
colours.forEach((colour, i) => { const dot = document.createElement("span"); dot.style.background = colour; legend.append(dot, i === 0 ? "cheapest" : i === colours.length - 1 ? "dearest" : ""); });
legend.append(" · a dark ring: every way to and from the airports looked up in the timetables");
const map = L.map("map").setView([data.origin.lat, data.origin.lon], 4);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 12, attribution: "© OpenStreetMap"}).addTo(map)
  .once("tileerror", () => { const note = document.getElementById("tiles"); note.hidden = false;
    note.textContent = "The map's background could not be loaded from OpenStreetMap, which may refuse a page opened as a file: "
      + "the dots, the list and the trips are all there. python -m discover serves the page, which OpenStreetMap accepts."; });
L.circleMarker([data.origin.lat, data.origin.lon], {radius: 8, color: "#1f6fd0", fillOpacity: 1}).addTo(map).bindTooltip(data.origin.name);
const markers = L.layerGroup().addTo(map);
let line = null;

// One leg of the trip, flight by flight, with the connections between them. What the traveller should know about a
// connection before booking it goes into warnings. toAirport and fromAirport are the ways to and from its airports:
// once looked up in the timetables, the warnings about the hour are theirs to give, not the map's.
function describeLeg(parent, title, leg, warnings, toAirport, fromAirport) {
  add(parent, "h4", title + " — " + leg.airline + (leg.stops ? ", " + leg.stops + " stop" + (leg.stops > 1 ? "s" : "") : ", direct"));
  const [date, time] = before(leg.date, leg.time, data.airport_minutes);
  if (!(toAirport && toAirport.looked_up) && (date !== leg.date || Number(time.slice(0, 2)) < data.early_hour))
    warnings.push("Being at " + place(leg.from) + " by " + when(date, time) + " may take the night before or a taxi: "
                  + "the timetables were not looked up for it. The planner, below, does.");
  add(parent, "p", "Be at " + place(leg.from) + " by " + when(date, time) + " (" + data.airport_minutes + " min before the flight).");
  const steps = add(parent, "ol");
  leg.segments.forEach((segment, index) => {
    if (index) {
      const previous = leg.segments[index - 1];
      const wait = between(previous.arrival_date, previous.arrival_time, segment.date, segment.time);
      const night = previous.arrival_date !== segment.date;
      add(steps, "li", "Change at " + place(segment.from) + (previous.to !== segment.from ? " (you arrive at " + place(previous.to) + ": another airport)" : "")
          + ": " + hours(wait) + " between the two flights" + (night ? ", through the night" : ""), "muted");
      if (previous.to !== segment.from)
        warnings.push("At " + place(previous.to) + " you must get to " + place(segment.from) + " by yourself, another airport, and check in again.");
      if (night || wait >= data.long_wait)
        warnings.push(hours(wait) + " waiting at " + place(segment.from) + (night ? ", through the night" : "") + ": a hotel may cost more than the saving.");
      if ((previous.airline || "") !== (segment.airline || ""))
        warnings.push(previous.airline + " and " + segment.airline + " at " + place(segment.from) + ": probably two separate tickets. If the first flight is late, "
                      + "the second airline does not have to wait or rebook you; the booking page says whether Kiwi guarantees it.");
    }
    add(steps, "li", "Leave " + place(segment.from) + " at " + when(segment.date, segment.time) + ", land at " + place(segment.to) + " at "
        + (segment.arrival_time ? when(segment.arrival_date, segment.arrival_time) : "a time not given") + " — " + (segment.airline || leg.airline) + (segment.flight ? " " + segment.flight : ""));
  });
  const landing = Number((leg.arrival_time || "12").slice(0, 2));
  if (!(fromAirport && fromAirport.looked_up) && leg.arrival_time && (landing >= data.late_hour || landing < data.early_hour))
    warnings.push("Landing at " + place(leg.to) + " at " + when(leg.arrival_date, leg.arrival_time) + ": public transport may have stopped by then.");
  add(parent, "p", "You arrive at " + place(leg.to) + " at " + (leg.arrival_time ? when(leg.arrival_date, leg.arrival_time) : "a time not given")
      + (leg.minutes ? ", " + hours(leg.minutes) + " after take-off" : "") + ". Times are local, at each airport.");
}

// The ways to and from the airports, each with its grades and where it comes from
function describeAccess(parent, p) {
  add(parent, "h4", "To and from the airports");
  const list = add(parent, "ul", undefined, "links");
  for (const leg of p.access || []) {
    const where = leg.to_airport ? (leg.place || "the city") + " to " + place(leg.airport) : place(leg.airport) + " to " + (leg.place || "the city");
    const times = leg.looked_up && leg.leave ? ", leaving at " + leg.leave + " and arriving at " + leg.arrive : "";
    const item = add(list, "li", where + ": " + eur(leg.price_eur) + (leg.minutes ? " and " + hours(leg.minutes) : "") + times
        + " (timetable " + leg.grade + ", price " + leg.price_grade + "). "
        + (leg.looked_up ? "Looked up for this flight: " + leg.how : leg.how.charAt(0).toUpperCase() + leg.how.slice(1)) + ".",
        leg.looked_up ? "" : "muted");
    if (leg.url && leg.url.startsWith("https://")) {
      item.append(" ");
      const source = add(item, "a", "Source");
      source.href = leg.url; source.target = "_blank"; source.rel = "noopener noreferrer";
    }
    if (leg.quote) add(item, "div", "“" + leg.quote + "”", "muted");
    // Every published way from that airport, so the traveller sees the choice and where each price was read
    if (leg.options && leg.options.length > 1) {
      const others = add(item, "ul");
      for (const option of leg.options) {
        const line = add(others, "li", [option.operator, option.mode].filter(Boolean).join(" ") + (option.to ? " to " + option.to : "")
                         + ": " + (option.from_price ? "from " : "") + option.price_text + " (" + eur(option.price_eur) + "), " + option.source + " ", "muted");
        if (option.url && option.url.startsWith("https://")) {
          const link = add(line, "a", "source");
          link.href = option.url; link.target = "_blank"; link.rel = "noopener noreferrer";
        }
      }
    }
  }
}

function explain(p, scroll) {
  picked = p.to;
  const panel = document.getElementById("detail");
  panel.replaceChildren();
  panel.hidden = false;
  const close = add(panel, "button", "Close", "close");
  close.onclick = () => { panel.hidden = true; picked = null; if (line) line.remove(); };
  add(panel, "h2", data.origin.name + " → " + p.name + " (" + p.to + ", " + p.country + ")");
  add(panel, "p", "Total " + eur(p.total_eur) + (p.stay ? ", " + stayText(p.stay) + " in the city" : "") + ": " + (p.back ? "flights there and back " : "flight ") + eur(p.price_eur) + " (grade A: " + p.source
      + ", when the map was made) and " + eur(p.ground_eur) + " to and from the airports. "
      + (p.confirmed ? "Every way to and from the airports was looked up in the timetables for these flights."
         : "Not every way to and from the airports was looked up for these flights: the grades below say which."), "muted");
  if (p.also && p.also.length)
    add(panel, "p", "Also to " + p.name + ": " + p.also.map(other => place(other.to) + " from " + other.from + ", " + eur(other.total_eur)
        + (other.access && other.access.every(leg => leg.looked_up) ? " (looked up)" : " (estimated)")).join("; ") + ".", "muted");
  const warnings = [];
  const warning = add(panel, "div");
  const access = p.access || [];
  describeLeg(panel, "Out", p, warnings, access[0], access[1]);
  if (p.back) {
    add(panel, "p", hours(between(p.arrival_date || p.date, p.arrival_time || p.time, p.back.date, p.back.time)) + " from landing to the flight back"
        + (p.stay ? ": " + stayText(p.stay) + " in " + p.name + ", from reaching its centre to leaving it for the airport ("
           + data.airport_minutes + " min before the flight)" + (p.stay.estimated ? ", with a way in or out not looked up for these flights" : "") : "")
        + ".", "muted");
    describeLeg(panel, "Back", p.back, warnings, access[2], access[3]);
  }
  describeAccess(panel, p);
  if (warnings.length) {
    add(warning, "h4", "Before booking", "warn");
    const list = add(warning, "ul", undefined, "links");
    warnings.forEach(text => add(list, "li", text, "warn"));
  }
  add(panel, "h4", "Links");
  const list = add(panel, "ul", undefined, "links");
  for (const link of p.links) {
    if (!link.url.startsWith("https://")) continue;
    const anchor = add(add(list, "li"), "a", link.label);
    anchor.href = link.url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
  }
  add(panel, "p", "The price may have changed since: the booking page has the one that counts.", "muted");
  describePlan(panel, p);
  if (line) line.remove();
  line = L.polyline([[data.origin.lat, data.origin.lon], [p.lat, p.lon]], {color: "#1f6fd0", weight: 2, dashArray: "4 6"}).addTo(map);
  document.querySelectorAll("#rows tr").forEach(row => row.classList.toggle("picked", row.dataset.to === p.to));
  if (scroll !== false) panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

// The way to check the trip in the planner, which looks up the ground transport in real timetables: a button when
// the page is served, the command to run when it was opened as a file.
function describePlan(panel, p) {
  const legs = [["the way there", p.plan.out], ["the way back", p.plan.back]].filter(([, plan]) => plan);
  if (!legs.length) return;
  add(panel, "h4", "Check it in the planner");
  add(panel, "p", "The planner opens with this trip in its form: it compares every airport of both cities, with the transport of each, "
      + "and leaves out a flight that cannot be reached. It searches only when you ask it to.", "muted");
  for (const [label, plan] of legs) {
    if (data.token) {
      const line = add(panel, "p");
      const button = add(line, "button", "Open the planner on " + label);
      const status = add(line, "span", "", "muted");
      button.onclick = () => {
        status.textContent = " ...";
        fetch("/plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: data.token, trip: plan.key})})
          .then(response => response.json().then(answer => { status.textContent = " " + (response.ok ? "The planner is " : "Refused: ") + answer.result + "."; }))
          .catch(() => { status.textContent = " The map's server has stopped. In the repository's folder, run: " + plan.command; });
      };
    } else {
      add(panel, "p", "For " + label + ", run in the repository's folder:");
      add(panel, "pre", plan.command);
    }
  }
}

const head = document.getElementById("head");
for (const [title, number] of [["Destination"], ["Out"], ["Back"], ["Airline"], [(data.round_trip ? "Flights" : "Flight") + " (A)", 1],
                               ["To and from the airports", 1], ["Total", 1], ...(data.round_trip ? [["In the city"]] : []), ["Timetables"]])
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
    add(status, "p", "Only Ryanair's flights are on this map: " + data.kiwi_why + ".", "kiwi");
  document.title = (state.running && !lost ? "⏳ " : "") + data.title;
  if (state.running && !lost) {
    add(status, "span", undefined, "pulse");
    add(status, "strong", "Still searching.");
    add(status, "span", "The flights were all searched: the " + count + " destinations are on the map. The cheapest are being looked up in the "
        + "real timetables (" + looked + " of at most " + data.max_lookups + " cities so far), so their prices and their order can still change. "
        + "The map updates itself: no need to reload it.");
    return;
  }
  add(status, "strong", lost ? "Stopped." : "Finished: nothing more is being searched.");
  add(status, "span", (lost ? "The map's server no longer answers (the Discover window was closed, or the map was made again): this is what it "
                              + "had found, and the buttons that open the planner no longer work. " : "")
      + "Every destination found is on the map: " + count + ". "
      + (looked ? looked + " cities were looked up in the real timetables" + (state.confirm_stop ? "; the lookups " + state.confirm_stop : "") + ". "
                : "None was looked up in the timetables: the ways to and from the airports are published prices, what earlier maps looked up, or estimates. ")
      + (missing.length ? missing.length + " destinations flown from these airports had no flight found on these days: they are listed under What was searched." : ""));
}

function render() {
  const totals = state.points.map(p => p.total_eur).sort((a, b) => a - b);
  const band = total => Math.min(colours.length - 1, Math.floor(colours.length * totals.filter(t => t < total).length / Math.max(1, totals.length)));
  document.getElementById("about").textContent = data.days + " · " + state.points.length + " destinations · " + data.sources + " · click a dot or a row for the trip. "
    + "Flight prices: grade A, as the source gave them when the map was made. To and from the airports: the grade of each way, in the trip.";
  const seen = state.coverage || {};
  if (seen.flown)   // how much of what is flown from here the map found: the honest measure of what it may miss
    document.getElementById("about").textContent += " Found " + seen.found + " of the " + seen.flown + " destinations flown from "
      + seen.airports.join(", ") + (seen.ryanair_no_fare ? ", and " + seen.ryanair_no_fare + " more are Ryanair's with no fare those days" : "")
      + (seen.missing.length ? "; the " + seen.missing.length + " not found are listed under What was searched." : ".");
  describeStatus();
  markers.clearLayers();
  rows.replaceChildren();
  for (const p of state.points) {
    L.circleMarker([p.lat, p.lon], {radius: 7, fillColor: colours[band(p.total_eur)], color: p.confirmed ? "#1d1d1f" : colours[band(p.total_eur)],
                                    fillOpacity: 0.85, weight: p.confirmed ? 2 : 1})
      .addTo(markers).bindTooltip(p.name + " · " + eur(p.total_eur) + (p.stay ? " · " + stayText(p.stay) : "") + (p.confirmed ? " · looked up" : "")).on("click", () => explain(p));
    const row = rows.insertRow();
    row.dataset.to = p.to;
    row.onclick = () => { map.setView([p.lat, p.lon], Math.max(map.getZoom(), 5)); explain(p); };
    for (const [value, number] of [[p.name + " (" + p.to + ")"], [leg(p, p.from)], [p.back ? leg(p.back, p.back.from) + (p.back.to !== p.from ? " → " + p.back.to : "") : "-"],
                                   [p.airline + (p.back && p.back.airline !== p.airline ? " / " + p.back.airline : "")],
                                   [eur(p.price_eur), 1], [eur(p.ground_eur) + " (" + worst(p.access || []) + ")", 1], [eur(p.total_eur), 1], ...(data.round_trip ? [[stayText(p.stay)]] : []),
                                   [p.confirmed ? "looked up" : (p.access || []).some(leg => leg.looked_up || leg.checked) ? "partly" : "estimated"]]) {
      const cell = row.insertCell(); cell.textContent = value; if (number) cell.className = "n";
    }
  }
  const discarded = document.getElementById("discarded");
  discarded.replaceChildren();
  if (state.discarded.length) {
    add(discarded, "h2", "Left out: the timetables show they cannot be made");
    const list = add(add(discarded, "div", undefined, "note"), "ul");
    for (const gone of state.discarded) add(list, "li", place(gone.to) + " from " + gone.from + " on " + gone.date + ": " + gone.reason);
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
  if (open) explain(open, false);
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
