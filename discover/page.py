"""The map page: one dot per destination, its colour by the total price, the details on a click, and the list
below. One HTML file, opened in the browser. The map is OpenStreetMap's, drawn by Leaflet."""
import json
from typing import Any

from trip_planner.places import load_airports, Place

def map_page(place: Place, destinations: list[dict[str, Any]], first_day: str, last_day: str, sources: str) -> str:
    """The map: one dot per destination, its colour by the total price, the details on a click, and the list below."""
    airports = load_airports()
    points = [{**flight, "name": airports[flight["to"]]["city"] or airports[flight["to"]]["name"], "country": airports[flight["to"]]["country"],
               "lat": airports[flight["to"]]["latitude"], "lon": airports[flight["to"]]["longitude"]}
              for flight in destinations if flight["to"] in airports]
    data = json.dumps({"origin": {"name": place.label, "lat": place.latitude, "lon": place.longitude}, "points": points,
                       "days": f"{first_day} – {last_day}", "sources": sources}, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__DATA__", data).replace("__TITLE__", f"Discover from {place.english_name}")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --bg: #ffffff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; }
  @media (prefers-color-scheme: dark) { :root { --bg: #1c1c1e; --fg: #f2f2f7; --muted: #a1a1a6; --line: #3a3a3c; } }
  body { margin: 0; font: 14px/1.4 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
  header { padding: 12px 16px; } h1 { font-size: 18px; margin: 0 0 4px; } header p { margin: 0; color: var(--muted); }
  #map { height: 62vh; } .legend span { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin: 0 4px 0 12px; }
  table { border-collapse: collapse; width: 100%; } td, th { text-align: left; padding: 6px 16px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 600; } td.n { text-align: right; font-variant-numeric: tabular-nums; }
  .wrap { overflow-x: auto; }
</style></head><body>
<header><h1 id="title"></h1><p id="about"></p><p class="legend" id="legend"></p></header>
<div id="map"></div>
<div class="wrap"><table><thead><tr><th>Destination</th><th>From</th><th>Day</th><th>Airline</th>
<th class="n">Flight</th><th class="n">To the airport (est.)</th><th class="n">Total</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>
const data = __DATA__;
const colours = ["#2e9e4f", "#8fbf3f", "#e6b422", "#e67e22", "#d0453a"];
const totals = data.points.map(p => p.total_eur).sort((a, b) => a - b);
const band = total => Math.min(colours.length - 1, Math.floor(colours.length * totals.filter(t => t < total).length / Math.max(1, totals.length)));
const eur = value => value.toFixed(0) + " €";
document.getElementById("title").textContent = "Cheapest flights from " + data.origin.name;
document.getElementById("about").textContent = data.days + " · " + data.points.length + " destinations · " + data.sources;
const legend = document.getElementById("legend");
colours.forEach((colour, i) => { const dot = document.createElement("span"); dot.style.background = colour; legend.append(dot, i === 0 ? "cheapest" : i === colours.length - 1 ? "dearest" : ""); });
const map = L.map("map").setView([data.origin.lat, data.origin.lon], 4);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 12, attribution: "© OpenStreetMap"}).addTo(map);
L.circleMarker([data.origin.lat, data.origin.lon], {radius: 8, color: "#1f6fd0", fillOpacity: 1}).addTo(map).bindTooltip(data.origin.name);
const rows = document.getElementById("rows");
for (const p of data.points) {
  const text = p.name + " (" + p.to + ", " + p.country + "): " + eur(p.total_eur) + " — flight " + eur(p.price_eur) + " from " + p.from
             + " on " + p.date + " " + p.time + ", " + p.airline + (p.stops ? ", " + p.stops + " stop(s)" : "") + "; to the airport about " + eur(p.ground_eur);
  L.circleMarker([p.lat, p.lon], {radius: 7, color: colours[band(p.total_eur)], fillOpacity: 0.85, weight: 1})
    .addTo(map).bindPopup(() => { const div = document.createElement("div"); div.textContent = text; return div; });
  const row = rows.insertRow();
  for (const [value, number] of [[p.name + " (" + p.to + ")"], [p.from], [p.date + " " + p.time], [p.airline + (p.stops ? " +" + p.stops : "")],
                                 [eur(p.price_eur), 1], [eur(p.ground_eur), 1], [eur(p.total_eur), 1]]) {
    const cell = row.insertCell(); cell.textContent = value; if (number) cell.className = "n";
  }
}
</script></body></html>
"""
