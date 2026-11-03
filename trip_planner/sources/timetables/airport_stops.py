"""The airport's end of a connection: where its passenger terminal is (OpenStreetMap), and which stops of the
timetables travellers use there."""
import json
import time
from datetime import date, timedelta
from typing import Any

import httpx

from trip_planner.trip.trip_settings import ROOT, USER_AGENT
from trip_planner.trip.ground_choice import is_rail
from trip_planner.trip.places import distance_km
from trip_planner.sources.timetables.modes import LIFT_MODES
from trip_planner.utils import logged, plain, say, write_whole

# Where the passenger terminal of each airport is, or null where OpenStreetMap has none. Worked out once for every
# airport (python main.py --terminals) and kept in the repository: a search only asks for an airport that is not here.
TERMINALS_FILE = ROOT / "resources" / "terminals.json"
TERMINALS_BATCH = 25             # airports asked about in one request when the whole file is worked out
OVERPASS_SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                    "https://overpass.private.coffee/api/interpreter")
TERMINALS: dict[str, list[float] | None] = json.loads(TERMINALS_FILE.read_text(encoding="utf-8")) if TERMINALS_FILE.exists() else {}
NOT_A_PASSENGER_TERMINAL = ("general aviation", "executive", "gat", "cargo", "private", "vip", "pier", "connector", "freight")

OVERPASS_ROUNDS = 3              # times the servers are gone through: a busy one answers a moment later
SILENT_SERVERS: set[str] = set() # mirrors that did not answer in time. Not asked again in this run: each silence costs the whole wait

def ask_overpass(query: str, seconds: int = 25) -> list[dict[str, Any]] | None:
    """The elements OpenStreetMap has for a query, or None when no server answers. These public servers are often
    busy: the main one refuses for a few seconds and then answers, the mirrors can be silent for days."""
    for attempt in range(OVERPASS_ROUNDS):
        for server in OVERPASS_SERVERS:
            if server in SILENT_SERVERS:
                continue
            try:
                with logged("Overpass", f"airport terminals, {server.split('/')[2]}"):
                    response = httpx.post(server, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=seconds)
                    response.raise_for_status()
                if response.json().get("remark"):
                    continue   # it ran out of time or memory, and what it sends then is not the whole answer
                return response.json()["elements"]
            except httpx.TimeoutException:
                if server != OVERPASS_SERVERS[0]:
                    SILENT_SERVERS.add(server)
            except Exception:
                continue
        time.sleep(3 * (attempt + 1))
    return None

def find_terminals(airports: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the passenger terminal of each airport is, from OpenStreetMap, or None when it is not known.
    The airport database only gives the middle of the airfield, which can be kilometres from the stops.
    One request for all the airports."""
    missing = [airport for airport in airports if airport["code"] not in TERMINALS]
    if missing:
        if not ask_terminals(missing):
            say("[timetables] OpenStreetMap did not answer: using the middle of each airfield instead of the terminal")
        save_terminals()
    return {airport["code"]: TERMINALS.get(airport["code"]) for airport in airports}

def ask_terminals(missing: list[dict[str, Any]], seconds: int = 25) -> bool:
    """Ask OpenStreetMap for the terminals of some airports and put them in TERMINALS, with None for the airports
    it has no terminal for. False, and nothing changed, when it did not answer."""
    query = f"[out:json][timeout:{seconds}];(" + "".join(
        f"nwr[aeroway=terminal](around:3500,{airport['latitude']},{airport['longitude']});" for airport in missing
    ) + ");out center tags;"
    elements = ask_overpass(query, seconds + 5)
    if elements is None:
        return False
    best: dict[str, tuple] = {}
    for element in elements:
        point = element.get("center") or element
        name = plain((element.get("tags") or {}).get("name", ""))
        # A terminal belongs to every airport it is at: one building can have several codes (Basel and Mulhouse)
        for airport in missing:
            if distance_km(airport["latitude"], airport["longitude"], point["lat"], point["lon"]) > 3.5:
                continue
            # Passenger terminals first, then the ones called "terminal 1" or "main", then named ones
            score = (
                not any(word in name.split() or word in name for word in NOT_A_PASSENGER_TERMINAL),
                "terminal 1" in name or "main" in name or name == plain(airport["name"]),
                bool(name),
                element["type"] == "relation",
            )
            if airport["code"] not in best or score > best[airport["code"]][0]:
                best[airport["code"]] = (score, (point["lat"], point["lon"]))
    for airport in missing:
        found = best.get(airport["code"])
        TERMINALS[airport["code"]] = [round(found[1][0], 5), round(found[1][1], 5)] if found else None
    return True

def save_terminals() -> None:
    """One airport per line, in order: a change shows as the line of its airport."""
    lines = [f"{json.dumps(code)}: {json.dumps(TERMINALS[code])}" for code in sorted(TERMINALS)]
    write_whole(TERMINALS_FILE, "{\n" + ",\n".join(lines) + "\n}\n", newline="\n")

def fill_terminals(again: bool = False) -> int:
    """Work out the terminal of every airport airlines fly from, a few airports per request, and save them.
    Only the airports not yet in the file, unless again: once a year is enough, terminals do not move.
    Returns how many airports could not be asked about."""
    from trip_planner.trip.places import airfields, load_airports
    skipped = airfields()
    airports = [{"code": code, **airport} for code, airport in load_airports().items()
                if code not in skipped and (again or not TERMINALS.get(code))]   # the ones without a terminal are asked again
    print(f"{len(airports)} airports to ask about, {TERMINALS_BATCH} per request. {len(TERMINALS)} are already known.", flush=True)
    batches = [airports[start:start + TERMINALS_BATCH] for start in range(0, len(airports), TERMINALS_BATCH)]
    failed: list[list[dict[str, Any]]] = []
    for attempt in range(2):   # the requests that got no answer are made once more at the end
        for number, batch in enumerate(batches, start=1):
            if ask_terminals(batch, seconds=60):
                save_terminals()
            else:
                failed.append(batch)
            with_terminal = sum(1 for airport in batch if TERMINALS.get(airport["code"]))
            print(f"  {number}/{len(batches)}: " + (f"{with_terminal} of {len(batch)} have a terminal on the map"
                                                    if batch not in failed else "no answer, asked again at the end"), flush=True)
            time.sleep(2)   # these servers are shared: leave room for others
        batches, failed = failed, []
        if not batches:
            break
    left = sum(len(batch) for batch in batches)
    known = sum(1 for place in TERMINALS.values() if place)
    print(f"{len(TERMINALS)} airports in {TERMINALS_FILE}: {known} with a terminal, {len(TERMINALS) - known} without one on the map."
          + (f" {left} could not be asked about: run it again." if left else ""), flush=True)
    return left

AIRFIELD_WALK_SECONDS = 3600     # terminal not known: the planner walks from the middle of the airfield, allow for it...
AIRFIELD_WALK_MINUTES = 15       # ... and count this fixed time between the stop and the terminal instead
# The stops of an airport. The planner walks badly around terminals (35 minutes to a station under the building),
# so the connections that leave an airport are asked for from its stops, not from a point on the map.
TERMINAL_STOP_KM = 0.7           # a stop this close to the terminal is one of the airport's
AIRPORT_STOP_KM = 4.0            # terminal not known: a stop this close to the airfield that is named after the airport
WALK_KMH = 4.5                   # walking speed between the terminal and a stop, on a path 1.3 times the straight line
MIN_STOP_WALK_MINUTES = 5
MAX_AIRPORT_STOPS = 2            # stops tried per airport: the nearest by rail, then by bus, then by coach
AIRPORT_WORDS = ("airport", "aeroport", "aeropuerto", "aerodrom", "flughafen", "luchthaven", "lufthavn", "flygplats", "flyplass",
                 "lotnisko", "letiste", "letisko", "letalisce", "zracna luka", "repuloter", "lentoasema", "lennujaam",
                 "lidosta", "oro uostas", "αεροδρομιο", "летище", "terminal")

def airport_stop_candidates(stops: list[dict[str, Any]], airport: dict[str, Any], terminal: list[float] | None) -> list[dict[str, Any]]:
    """The stops around an airport that can be the airport's own: next to its terminal, or named after it."""
    centre = terminal or (airport["latitude"], airport["longitude"])
    km = lambda stop: distance_km(centre[0], centre[1], stop["lat"], stop["lon"])
    named = lambda stop: any(word in plain(stop.get("name") or "") for word in AIRPORT_WORDS)
    return [stop for stop in stops if set(stop.get("modes") or []) - {"AIRPLANE"}
            and (km(stop) <= TERMINAL_STOP_KM and terminal or km(stop) <= AIRPORT_STOP_KM and named(stop))]

def choose_airport_stops(stops: list[dict[str, Any]], airport: dict[str, Any], terminal: list[float] | None) -> list[tuple]:
    """The stops travellers use at an airport, among the stops around it, as (stop id, minutes on foot from the
    terminal): the nearest with trains, then with buses, then with coaches. Empty when none is the airport's."""
    centre = terminal or (airport["latitude"], airport["longitude"])
    ground = airport_stop_candidates(stops, airport, terminal)
    km = lambda stop: distance_km(centre[0], centre[1], stop["lat"], stop["lon"])
    named = lambda stop: any(word in plain(stop.get("name") or "") for word in AIRPORT_WORDS)
    chosen = [stop for stop in ground if km(stop) <= TERMINAL_STOP_KM] if terminal else []
    by_name = not chosen   # no terminal, or none of the stops is next to it: the ones named after the airport
    if by_name:
        chosen = [stop for stop in ground if km(stop) <= AIRPORT_STOP_KM and named(stop)]
    kinds = (lambda modes: any(is_rail(mode) for mode in modes), lambda modes: bool(modes & {"BUS", "TRAM", "SUBWAY", *LIFT_MODES.split(",")}),
             lambda modes: "COACH" in modes)
    best: list[dict[str, Any]] = []
    for kind in kinds:
        of_kind = [stop for stop in chosen if kind(set(stop["modes"])) and stop not in best]
        if of_kind:
            best.append(min(of_kind, key=km))
    walk = lambda stop: AIRFIELD_WALK_MINUTES if by_name else max(MIN_STOP_WALK_MINUTES, round(km(stop) * 1.3 / WALK_KMH * 60))
    return [(stop["stopId"], walk(stop)) for stop in best[:MAX_AIRPORT_STOPS]]

# The stops chosen for each airport, kept between searches: they change as rarely as the timetables' own list of
# stops, and asking for them was a third of the requests of a search. Read again after AIRPORT_STOPS_DAYS, and
# a stop the planner no longer knows is skipped anyway (see to_airport and from_airport).
AIRPORT_STOPS_FILE = ROOT / "resources" / "airport_stops.json"
AIRPORT_STOPS_DAYS = 30
AIRPORT_STOPS: dict[str, dict[str, Any]] = json.loads(AIRPORT_STOPS_FILE.read_text(encoding="utf-8")) if AIRPORT_STOPS_FILE.exists() else {}

def known_airport_stops(code: str, terminal: list[float] | None) -> list[tuple] | None:
    """The stops kept for an airport, or None when they have to be asked for: never read, read too long ago, or
    read for another place of its terminal."""
    kept = AIRPORT_STOPS.get(code)
    if not kept or kept["terminal"] != terminal or kept["read"] < (date.today() - timedelta(days=AIRPORT_STOPS_DAYS)).isoformat():
        return None
    return [tuple(stop) for stop in kept["stops"]]

def keep_airport_stops(code: str, terminal: list[float] | None, stops: list[tuple]) -> None:
    """One airport per line, in order, as in the file of the terminals."""
    AIRPORT_STOPS[code] = {"read": date.today().isoformat(), "terminal": terminal, "stops": [list(stop) for stop in stops]}
    lines = [f"{json.dumps(code)}: {json.dumps(AIRPORT_STOPS[code], ensure_ascii=False)}" for code in sorted(AIRPORT_STOPS)]
    write_whole(AIRPORT_STOPS_FILE, "{\n" + ",\n".join(lines) + "\n}\n", newline="\n")
