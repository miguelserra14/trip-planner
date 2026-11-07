"""The page while a search runs, in the way of Routavio's: the trip on a map, the steps of the search with what each
one found, and about how long is left. Drawn from what the planner says as data (utils.progress), which the search's
process passes on (job.py) and Job.progress keeps, one entry per way of the trip.

The map is drawn here, with no service and no script: the land of Natural Earth's 1:110m outline (public domain, kept
in land.json as rings of longitude and latitude) around the two cities and their airports. How long is left comes
from how long the last searches took (full.past_durations); without them, a search is taken to last FIRST_GUESS."""
import html
import json
import math
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

from app.icons import icon
from trip_planner.texts import tr
from trip_planner.report.summary import span

LAND = Path(__file__).with_name("land.json")   # Natural Earth 1:110m land, rings of [longitude, latitude] to 0.1 degree
WIDTH = 800             # the map's width in its own units; its height follows the trip's shape
SHAPE = (1.6, 2.6)      # the narrowest and the widest the map may be, width over height: a trip north to south is not a strip
MARGIN = 0.25           # room around the places, as a share of their span on the map
LEAST_DEGREES = 5.0     # the smallest span drawn, in degrees: two towns next to each other still have a country around them
MOST_AIRPORTS = 12      # departure airports drawn: the closest are first in the trip
LABEL_ROOM = 34         # a name closer than this (in the map's units) to one already written is left to its dot: the cities first
# How long one way is taken to last with no search to compare: four minutes with a flight (the user's choice of
# 2026-11-03), one without, which asks no flight and no model
FIRST_GUESS = {False: 240, True: 60}
LAST_SEARCHES = 10      # searches of the same kind the estimate is the median of
# The steps of a search as the page names them, in order: (the planner's step, its name, how what it found is said)
STEPS = {
    False: [("search_flights", "wait_step_flights", "wait_count_flights"),
            ("search_ground_transport", "wait_step_ground", "wait_count_checked"),
            ("build_itineraries", "wait_step_rank", "wait_count_options"),
            ("recommend", "wait_step_recommend", "")],
    True: [("search_by_ground_only", "wait_step_ground_only", "wait_count_trips"),
           ("rank_by_ground_only", "wait_step_rank", "wait_count_options")],
}
esc = lambda text: html.escape(str(text))

@lru_cache(maxsize=1)
def land() -> list[list[list[float]]]:
    """The rings of land, each with its box (west, south, east, north) first, read once."""
    rings = json.loads(LAND.read_text(encoding="utf-8"))
    return [[[min(lon for lon, _ in ring), min(lat for _, lat in ring), max(lon for lon, _ in ring), max(lat for _, lat in ring)], *ring]
            for ring in rings]

def frame(points: list[tuple[float, float]]) -> tuple:
    """The part of the world the map shows around the points: (west, south, east, north, the factor that makes a degree
    of longitude as long as one of latitude there). Wider or taller to keep within SHAPE, with MARGIN around."""
    lons, lats = [lon for lon, _ in points], [lat for _, lat in points]
    squeeze = math.cos(math.radians((min(lats) + max(lats)) / 2))
    middle_x, middle_y = (min(lons) + max(lons)) / 2 * squeeze, (min(lats) + max(lats)) / 2
    span_x = max((max(lons) - min(lons)) * squeeze, LEAST_DEGREES) * (1 + 2 * MARGIN)
    span_y = max(max(lats) - min(lats), LEAST_DEGREES) * (1 + 2 * MARGIN)
    span_x, span_y = max(span_x, span_y * SHAPE[0]), max(span_y, span_x / SHAPE[1])
    return ((middle_x - span_x / 2) / squeeze, middle_y - span_y / 2, (middle_x + span_x / 2) / squeeze, middle_y + span_y / 2, squeeze)

def trip_map(trip: dict[str, Any]) -> str:
    """The trip on a map: the land around it, the two cities, the airports of each side and the ways between them,
    the flights' ways moving while the search runs. Without a plane, one way from city to city."""
    origin, destination = trip["origin"], trip["destination"]
    departures = [] if trip.get("ground_only") else trip.get("departure", [])[:MOST_AIRPORTS]
    arrivals = [] if trip.get("ground_only") else trip.get("arrival", [])
    places = [origin, destination, *departures, *arrivals]
    west, south, east, north, squeeze = frame([(place["lon"], place["lat"]) for place in places])
    scale = WIDTH / ((east - west) * squeeze)
    height = round((north - south) * scale)
    at = lambda lon, lat: (round((lon - west) * squeeze * scale, 1), round((north - lat) * scale, 1))
    xy = lambda place: at(place["lon"], place["lat"])
    shapes = []
    for box, *ring in land():
        if box[2] < west - 1 or box[0] > east + 1 or box[3] < south - 1 or box[1] > north + 1:
            continue
        shapes.append("M" + "L".join("{},{}".format(*at(lon, lat)) for lon, lat in ring) + "Z")
    drawn = [f'<path class="land" d="{"".join(shapes)}"/>']

    def way(start: dict, end: dict, kind: str) -> str:
        (x1, y1), (x2, y2) = xy(start), xy(end)
        bend = 0.18 if kind == "flight" else 0.0   # a flight arcs, a way on the ground goes straight
        cx, cy = (x1 + x2) / 2 - (y2 - y1) * bend, (y1 + y2) / 2 + (x2 - x1) * bend
        return f'<path class="{kind}" d="M{x1},{y1}Q{cx:.1f},{cy:.1f} {x2},{y2}"/>'
    if departures and arrivals:
        drawn += [way(origin, airport, "road") for airport in departures]
        drawn += [way(start, end, "flight") for start in departures for end in arrivals]
        drawn += [way(airport, destination, "road") for airport in arrivals]
    else:
        drawn.append(way(origin, destination, "flight ground"))
    written: list[tuple[float, float]] = []

    def name(text: str, x: float, y: float) -> str:
        """The name at (x, y), unless another one was written too close to it: then the dot alone, with the name on hover."""
        if any(abs(x - other_x) < LABEL_ROOM * 2 and abs(y - other_y) < LABEL_ROOM / 1.5 for other_x, other_y in written):
            return f"<title>{esc(text)}</title>"
        written.append((x, y))
        return f'<text x="{x}" y="{y}">{esc(text)}</text>'
    cities = []
    for place, other in ((origin, destination), (destination, origin)):
        x, y = xy(place)
        above = place["lat"] > other["lat"]   # the northern city's name above it, the other's below: two towns close by stay apart
        cities.append(f'<g class="city"><circle cx="{x}" cy="{y}" r="7"/>{name(place["name"], x, y - 14 if above else y + 24)}</g>')
    for airport in departures + arrivals:
        x, y = xy(airport)
        drawn.append(f'<g class="airport"><circle cx="{x}" cy="{y}" r="4"/>{name(airport["code"], x, y - 8)}</g>')
    drawn += cities   # over the airports
    label = tr("wait_map", origin=origin["name"], destination=destination["name"])
    return f'<svg class="trip-map" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{esc(label)}">{"".join(drawn)}</svg>'

def steps_list(way: dict[str, Any]) -> str:
    """The steps of one way of the search: done (with what each found), the one running, the ones to come."""
    ground_only = bool(way["trip"].get("ground_only"))
    trip = way["trip"]
    rows = [("done", tr("wait_step_places"), tr("wait_count_cities") if ground_only else
             tr("wait_count_airports", departure=len(trip.get("departure") or []), arrival=len(trip.get("arrival") or [])))]
    for name, key, count_key in STEPS[ground_only]:
        if name in way["counts"] or (name == "recommend" and way["step"] == "done"):
            state = "done"
        elif way["step"] == name:
            state = "running"
        else:
            state = "next"
        said = tr(count_key, count=way["counts"][name]) if count_key and name in way["counts"] else ""
        rows.append((state, tr(key), said))
    marks = {"done": "✓", "running": "", "next": ""}
    return '<ol class="wait-steps">' + "".join(
        f'<li class="{state}"><span class="mark" aria-hidden="true">{marks[state]}</span><span>{esc(name)}'
        + (f' <small class="muted">{esc(said)}</small>' if said else "") + "</span></li>" for state, name, said in rows) + "</ol>"

def expected_seconds(ground_only: bool, history: list[dict[str, Any]]) -> tuple[float, int]:
    """How long one way is expected to take: the median of the last searches of the same kind, and how many there were.
    None to compare with: FIRST_GUESS."""
    same = [entry["seconds"] for entry in history if bool(entry.get("ground_only")) == ground_only][-LAST_SEARCHES:]
    return (median(same), len(same)) if same else (FIRST_GUESS[ground_only], 0)

def time_left(ways: list[dict[str, Any]], round_trip: bool, elapsed: float, history: list[dict[str, Any]]) -> str:
    """About how long the search has left, and what that is based on, with a bar of how far it is."""
    ground_only = bool(ways[-1]["trip"].get("ground_only"))
    each, searches = expected_seconds(ground_only, history)
    total = each * (2 if round_trip else 1)
    left = total - elapsed
    said = (tr("wait_left", time=span(max(1, round(left / 60)))) if left >= 60 else
            tr("wait_last_minute") if left > 0 else tr("wait_longer"))
    basis = tr("wait_basis_history", count=searches) if searches else tr("wait_basis_guess", time=span(round(each / 60)))
    share = min(95, max(3, round(100 * elapsed / total))) if total else 50
    return (f'<div class="wait-time"><p><b>{esc(said)}</b> <small class="muted">{esc(basis)}</small></p>'
            f'<div class="wait-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{share}">'
            f'<i style="width: {share}%"></i></div></div>')

def waiting_part(ways: list[dict[str, Any]], round_trip: bool, elapsed: float, history: list[dict[str, Any]]) -> str:
    """The card shown while the search runs: the way being searched (there or back), its map, its steps and the time."""
    way = ways[-1]
    trip = way["trip"]
    title = tr("wait_title", origin=trip["origin"]["name"], destination=trip["destination"]["name"])
    side = (f'<p class="muted">{esc(tr("wait_way", number=len(ways)))}</p>' if round_trip else "")
    kind = icon("train" if trip.get("ground_only") else "plane")
    return (f'<section class="card waiting" aria-live="polite"><h2>{kind} {esc(title)}</h2>{side}'
            f'<div class="wait-body">{trip_map(trip)}<div>{steps_list(way)}{time_left(ways, round_trip, elapsed, history)}</div></div></section>')
