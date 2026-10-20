"""The map page: one dot per destination, its colour by the total price, the list below, and under it what was
searched (breakdown.py). Clicking a dot or a row explains that trip: when to be at the airport, each flight with
the day and time it leaves and lands, the connections, the way back, the prices, and the links to book it, to
compare it and to reach the airports. One HTML file, opened in the browser. The map is OpenStreetMap's, drawn by
Leaflet. Every text is put in the page as text, never as markup, and every link is https."""
import json
from typing import Any
from urllib.parse import quote, urlencode

from discover.finder import Found
from trip_planner.places import load_airports

def google_flights(flight: dict[str, Any]) -> str:
    """Google Flights, searching the same trip on the same days: to compare the price and see every airline."""
    asked = f"Flights from {flight['from']} to {flight['to']} on {flight['date']}"
    if flight.get("back"):
        asked += f" returning {flight['back']['date']}" + (f" from {flight['back']['from']}" if flight["back"]["from"] != flight["to"] else "")
    return "https://www.google.com/travel/flights?q=" + quote(asked)

LONG_WAIT_MINUTES = 6 * 60   # a connection this long, or through the night, is shown as a warning

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

def map_page(found: Found, sources: str, sections: list[dict[str, Any]], airport_minutes: int = 120) -> str:
    """airport_minutes is how long before a flight the traveller is at the airport (the planner's setting)."""
    airports = load_airports()
    points = [{**flight, "name": airports[flight["to"]]["city"] or airports[flight["to"]]["name"], "country": airports[flight["to"]]["country"],
               "lat": airports[flight["to"]]["latitude"], "lon": airports[flight["to"]]["longitude"], "links": links(flight, found, airports)}
              for flight in found.destinations if flight["to"] in airports]
    names = {code: airports[code]["name"] for flight in points for leg in (flight, flight.get("back")) if leg
             for segment in [leg, *leg.get("segments", [])] for code in (segment["from"], segment["to"]) if code in airports}
    mine = {airport["code"]: {"km": airport["km"], "ground_eur": airport["ground_eur"]} for airport in found.departures}
    data = json.dumps({"origin": {"name": found.place.label, "lat": found.place.latitude, "lon": found.place.longitude},
                       "points": points, "days": str(found.days), "round_trip": found.days.round_trip, "sources": sources,
                       "sections": sections, "names": names, "mine": mine, "airport_minutes": airport_minutes,
                       "long_wait": LONG_WAIT_MINUTES},
                      ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__DATA__", data).replace("__TITLE__", f"Discover from {found.place.english_name}")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --bg: #ffffff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; --panel: #f5f5f7; --link: #0a66c2; --pick: #fff6d6; }
  @media (prefers-color-scheme: dark) { :root { --bg: #1c1c1e; --fg: #f2f2f7; --muted: #a1a1a6; --line: #3a3a3c; --panel: #2c2c2e; --link: #6cb4ff; --pick: #3d3519; } }
  body { margin: 0; font: 14px/1.4 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
  a { color: var(--link); }
  header { padding: 12px 16px; } h1 { font-size: 18px; margin: 0 0 4px; } header p { margin: 0; color: var(--muted); }
  h2 { font-size: 16px; margin: 28px 16px 4px; } h3 { font-size: 14px; margin: 20px 16px 4px; } p.note { margin: 4px 16px 8px; color: var(--muted); max-width: 70em; }
  #map { height: 58vh; } .legend span { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin: 0 4px 0 12px; }
  #detail { background: var(--panel); padding: 12px 16px 16px; border-bottom: 1px solid var(--line); }
  #detail h2 { margin: 0 0 2px; } #detail h4 { margin: 14px 0 4px; font-size: 14px; } #detail p { margin: 2px 0; }
  #detail .muted { color: var(--muted); } #detail ol { margin: 4px 0; padding-left: 20px; } #detail li { margin: 2px 0; }
  #detail ul.links { margin: 6px 0 0; padding-left: 20px; } #detail .close { float: right; }
  #detail .warn { color: #b3261e; } @media (prefers-color-scheme: dark) { #detail .warn { color: #ff8a80; } }
  table { border-collapse: collapse; width: 100%; } td, th { text-align: left; padding: 6px 16px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; } td.n { text-align: right; font-variant-numeric: tabular-nums; }
  #rows tr { cursor: pointer; } #rows tr:hover, #rows tr.picked { background: var(--pick); }
  .wrap { overflow-x: auto; }
</style></head><body>
<header><h1 id="title"></h1><p id="about"></p><p class="legend" id="legend"></p></header>
<div id="map"></div>
<section id="detail" hidden></section>
<div class="wrap"><table><thead><tr id="head"></tr></thead><tbody id="rows"></tbody></table></div>
<h2>What was searched</h2>
<div id="sections"></div>
<script>
const data = __DATA__;
const colours = ["#2e9e4f", "#8fbf3f", "#e6b422", "#e67e22", "#d0453a"];
const totals = data.points.map(p => p.total_eur).sort((a, b) => a - b);
const band = total => Math.min(colours.length - 1, Math.floor(colours.length * totals.filter(t => t < total).length / Math.max(1, totals.length)));
const eur = value => value.toFixed(0) + " €";
const add = (parent, tag, text, kind) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (kind) element.className = kind; parent.append(element); return element; };
const place = code => (data.names[code] || code) + " (" + code + ")";
const day = text => new Date(text + "T12:00:00").toLocaleDateString("en-GB", {weekday: "short", day: "numeric", month: "short"});
const when = (date, time) => time + " on " + day(date);
const hours = minutes => (minutes >= 1440 ? Math.floor(minutes / 1440) + "d " : "") + Math.floor(minutes % 1440 / 60) + "h" + String(minutes % 60).padStart(2, "0");
const between = (date1, time1, date2, time2) => Math.round((new Date(date2 + "T" + time2) - new Date(date1 + "T" + time1)) / 60000);
const before = (date, time, minutes) => { const moment = new Date(new Date(date + "T" + time) - minutes * 60000);
  const two = number => String(number).padStart(2, "0");
  return [moment.getFullYear() + "-" + two(moment.getMonth() + 1) + "-" + two(moment.getDate()), two(moment.getHours()) + ":" + two(moment.getMinutes())]; };

document.getElementById("title").textContent = (data.round_trip ? "Cheapest return trips from " : "Cheapest flights from ") + data.origin.name;
document.getElementById("about").textContent = data.days + " · " + data.points.length + " destinations · " + data.sources + " · click a dot or a row for the trip";
const legend = document.getElementById("legend");
colours.forEach((colour, i) => { const dot = document.createElement("span"); dot.style.background = colour; legend.append(dot, i === 0 ? "cheapest" : i === colours.length - 1 ? "dearest" : ""); });
const map = L.map("map").setView([data.origin.lat, data.origin.lon], 4);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 12, attribution: "© OpenStreetMap"}).addTo(map);
L.circleMarker([data.origin.lat, data.origin.lon], {radius: 8, color: "#1f6fd0", fillOpacity: 1}).addTo(map).bindTooltip(data.origin.name);
let line = null;

// One leg of the trip, flight by flight, with the connections between them. What the traveller should know about a
// connection before booking it goes into warnings.
function describeLeg(parent, title, leg, first, warnings) {
  add(parent, "h4", title + " — " + leg.airline + (leg.stops ? ", " + leg.stops + " stop" + (leg.stops > 1 ? "s" : "") : ", direct"));
  if (first) {
    const [date, time] = before(leg.date, leg.time, data.airport_minutes);
    const mine = data.mine[leg.from] || {};
    add(parent, "p", "Be at " + place(leg.from) + " by " + when(date, time) + " (" + data.airport_minutes + " min before the flight). "
        + (mine.km ? "It is " + mine.km + " km from " + data.origin.name + ", about " + eur(mine.ground_eur) + " by public transport (an estimate)." : ""));
  }
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
  add(parent, "p", "You arrive at " + place(leg.to) + " at " + (leg.arrival_time ? when(leg.arrival_date, leg.arrival_time) : "a time not given")
      + (leg.minutes ? ", " + hours(leg.minutes) + " after take-off" : "") + ". Times are local, at each airport.");
}

function explain(p) {
  const panel = document.getElementById("detail");
  panel.replaceChildren();
  panel.hidden = false;
  const close = add(panel, "button", "Close", "close");
  close.onclick = () => { panel.hidden = true; if (line) line.remove(); };
  add(panel, "h2", data.origin.name + " → " + p.name + " (" + p.to + ", " + p.country + ")");
  add(panel, "p", "Total " + eur(p.total_eur) + ": " + (p.back ? "flights there and back " : "flight ") + eur(p.price_eur) + " (" + p.source
      + ", when the map was made) and about " + eur(p.ground_eur) + " to reach the airport" + (p.back ? " and to get home" : "") + ".", "muted");
  const warnings = [];
  const warning = add(panel, "div");
  describeLeg(panel, "Out", p, true, warnings);
  if (p.back) {
    add(panel, "p", hours(between(p.arrival_date || p.date, p.arrival_time || p.time, p.back.date, p.back.time)) + " at the destination.", "muted");
    describeLeg(panel, "Back", p.back, false, warnings);
    const mine = data.mine[p.back.to] || {};
    if (mine.km) add(panel, "p", "Then " + mine.km + " km home to " + data.origin.name + ", about " + eur(mine.ground_eur) + " by public transport (an estimate).");
  }
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
  if (line) line.remove();
  line = L.polyline([[data.origin.lat, data.origin.lon], [p.lat, p.lon]], {color: "#1f6fd0", weight: 2, dashArray: "4 6"}).addTo(map);
  document.querySelectorAll("#rows tr").forEach(row => row.classList.toggle("picked", row.dataset.to === p.to));
  panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

const head = document.getElementById("head");
for (const [title, number] of [["Destination"], ["Out"], ["Back"], ["Airline"], [data.round_trip ? "Flights" : "Flight", 1], ["To and from the airport (est.)", 1], ["Total", 1]])
  add(head, "th", title, number ? "n" : "");
const leg = (flight, from) => from + " " + flight.date + " " + flight.time + (flight.stops ? " +" + flight.stops : "");
const rows = document.getElementById("rows");
for (const p of data.points) {
  L.circleMarker([p.lat, p.lon], {radius: 7, color: colours[band(p.total_eur)], fillOpacity: 0.85, weight: 1})
    .addTo(map).bindTooltip(p.name + " · " + eur(p.total_eur)).on("click", () => explain(p));
  const row = rows.insertRow();
  row.dataset.to = p.to;
  row.onclick = () => { map.setView([p.lat, p.lon], Math.max(map.getZoom(), 5)); explain(p); };
  for (const [value, number] of [[p.name + " (" + p.to + ")"], [leg(p, p.from)], [p.back ? leg(p.back, p.back.from) + (p.back.to !== p.from ? " → " + p.back.to : "") : "-"],
                                 [p.airline + (p.back && p.back.airline !== p.airline ? " / " + p.back.airline : "")],
                                 [eur(p.price_eur), 1], [eur(p.ground_eur), 1], [eur(p.total_eur), 1]]) {
    const cell = row.insertCell(); cell.textContent = value; if (number) cell.className = "n";
  }
}
const sections = document.getElementById("sections");
for (const part of data.sections) {
  add(sections, "h3", part.title);
  if (part.note) add(sections, "p", part.note, "note");
  if (!part.header.length) continue;
  const table = add(add(sections, "div", undefined, "wrap"), "table");
  const top = table.createTHead().insertRow();
  part.header.forEach(title => add(top, "th", title));
  const body = table.createTBody();
  for (const values of part.rows) { const row = body.insertRow(); values.forEach(value => { row.insertCell().textContent = value; }); }
}
</script></body></html>
"""
