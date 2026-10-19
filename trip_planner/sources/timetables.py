"""Real timetables. A model searching the web can find a travel time and a price, but not whether something
actually leaves at the right time on the travel date. So the ground transport of every flight is looked up
by code in two kinds of sources, and what a model says is only used where neither covers the route.

1. RIDE_SOURCES (fares.py): an operator's own booking system. Real rides and real prices for the day of
   the trip (grade A), to the departure airport and from the arrival airport.
2. Transitous: a free journey planner built on the timetables that operators publish, worldwide. Trains,
   metros, buses, coaches and airport shuttles of every operator that publishes one. No prices. Timetables are
   usually published only a few weeks ahead, so a far travel date is looked up on the same weekday of a nearer
   week (grade B, "typical timetable")."""
import asyncio
import importlib.resources
import json
import math
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from trip_planner import current
from trip_planner.config import NIGHT_MINUTES, ROOT, USER_AGENT
from trip_planner.places import distance_km
from trip_planner.sources import fares, web
from trip_planner.utils import logged, plain, say, to_minutes, to_time

TRANSITOUS_API = "https://api.transitous.org/api/v1"
TIMETABLE_SOURCES = "Transitous (published timetables of all operators) + " + ", ".join(fares.RIDE_SOURCES)
# Transitous is a free community service. It answers about one request every 1.4 seconds per user, however fast
# it is asked, so the time this step takes is the number of requests. Requests are shared wherever one answer
# serves several flights (see Timetables.onward).
# Tried on 2026-10-14 with 4 and 6 requests at a time: none was refused, but each took that much longer (a route
# of 6 seconds took 12, then 18) and the search took the same time. Only asking less makes this step faster.
TIMETABLE_CONCURRENCY = 2
TIMETABLE_SPACING_SECONDS = 0.5  # leave this long between two requests
PAGE_SIZE = 8                    # connections asked for per request
# People movers, funiculars and cable cars count: the link between a terminal and its station is often one of them
# (Bologna, Pisa), and so is the last stretch to a mountain village.
LIFT_MODES = "FUNICULAR,AERIAL_LIFT,OTHER"
GROUND_MODES = "RAIL,SUBURBAN,SUBWAY,TRAM,BUS,COACH,FERRY," + LIFT_MODES   # everything but planes: the planner also flies, and those answers are no use here
NO_COACH_MODES = "RAIL,SUBURBAN,SUBWAY,TRAM,BUS,FERRY," + LIFT_MODES       # ... and but long-distance coaches
ROAD_MODES = "COACH,BUS,TRAM,SUBWAY,FERRY"                   # ... and but trains
CITY_WALK_SECONDS = 2400         # walking allowed between the city centre and a stop. It is not counted: the traveller is somewhere in the city
WIDE_CITY_WALK_SECONDS = 5400    # ... tried when nothing is found: where the timetables lack the city's own transport (Athens), the station is further than that on foot
AIRPORT_WALK_SECONDS = 1200      # walking allowed between the terminal and a stop. It is counted
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
LAST_RIDES_MINUTES = 120         # how long before the latest time the traveller leaves at the last connections are asked from
MIN_AIRPORT_FLOOR = 45           # the closest alternatives may cut the time at the airport, but never below this
MAX_FLIGHTS_CHECKED = 16         # flights whose ground transport is looked up: the cheapest ones
MAX_FLIGHTS_PER_AIRPORT_DAY = 3  # ... with at most this many per departure airport and date
TIMEZONES_FILE = ROOT / "resources" / "timezones.json"   # found once per place, then reused
TIMEZONES: dict[str, str] = json.loads(TIMEZONES_FILE.read_text(encoding="utf-8")) if TIMEZONES_FILE.exists() else {}

def is_rail(mode: str) -> bool:
    return "RAIL" in mode or mode in ("SUBURBAN", "METRO", "LONG_DISTANCE")

def landing_day(flight: dict[str, Any]) -> str:
    """The day a flight lands, which is the day of everything at the destination. Later than its date for a night flight."""
    return flight.get("arrival_date") or flight["date"]

def reference_day(day: str) -> str:
    """The same weekday as a travel date, in the week that starts today: the days every operator has published.
    Some publish no further than six days ahead (Zadar, Thessaloniki), so a day a week from now finds them empty."""
    target, soon = date.fromisoformat(day), date.today()
    soon += timedelta(days=(target.weekday() - soon.weekday()) % 7)
    return min(soon, target).isoformat() if target >= date.today() else day

def local_to_utc(day: str, minutes: int, zone: str) -> str:
    local = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(zone)) + timedelta(minutes=minutes)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")

def utc_to_local(moment: str, zone: str) -> tuple:
    """A UTC time from the timetable as (yyyy-mm-dd, minutes since midnight) on the local clock."""
    local = datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
    return local.date().isoformat(), local.hour * 60 + local.minute

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
    TERMINALS_FILE.parent.mkdir(exist_ok=True)
    TERMINALS_FILE.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")

def fill_terminals(again: bool = False) -> int:
    """Work out the terminal of every airport airlines fly from, a few airports per request, and save them.
    Only the airports not yet in the file, unless again: once a year is enough, terminals do not move.
    Returns how many airports could not be asked about."""
    from trip_planner.places import airfields, load_airports
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
    AIRPORT_STOPS_FILE.parent.mkdir(exist_ok=True)
    AIRPORT_STOPS_FILE.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")

def zone_of_country(country: str, latitude: float, longitude: float) -> str:
    """The time zone of a point from its country alone: of the zones of that country, the one whose city is the
    nearest (Portugal has three). From the list that comes with the time zone database. Empty when not known."""
    try:
        table = importlib.resources.files("tzdata.zoneinfo").joinpath("zone.tab").read_text(encoding="utf-8")
    except Exception:
        return ""
    zones = []
    for line in table.splitlines():
        code, where, name = (line.split("\t") + ["", ""])[:3]
        place = re.fullmatch(r"([+-])(\d\d)(\d\d)(?:\d\d)?([+-])(\d\d\d)(\d\d)(?:\d\d)?", where)   # degrees and minutes
        if code == country and place:
            north = (int(place[2]) + int(place[3]) / 60) * (-1 if place[1] == "-" else 1)
            east = (int(place[5]) + int(place[6]) / 60) * (-1 if place[4] == "-" else 1)
            zones.append((distance_km(latitude, longitude, north, east), name))
    return min(zones)[1] if zones else ""

NOT_COVERED = {"covered": False, "service_day": None, "grade": "C", "options": []}

def asked(path: str, params: dict[str, Any]) -> str:
    """A request to the timetables in a few words, for the log of the run."""
    if path != "plan":
        return f"{path} " + " ".join(str(value) for value in params.values())
    return (f"plan {params['fromPlace']} -> {params['toPlace']}, {'arriving by' if params['arriveBy'] == 'true' else 'leaving from'} "
            f"{params['time']}" + ("" if params["transitModes"] == GROUND_MODES else f", only {params['transitModes']}"))

class Timetables:
    """Looks connections up in the published timetables, a few requests at a time."""

    def __init__(self):
        self.http = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=40)
        self.semaphore = asyncio.Semaphore(TIMETABLE_CONCURRENCY)
        self.probes: dict[tuple, Any] = {}       # routes already asked about in the middle of the day
        self.last_rides: dict[tuple, Any] = {}   # last connections to an airport before the latest time the traveller leaves at
        self.first_rides: dict[tuple, Any] = {}  # first connections of the day to an airport
        self.windows: dict[tuple, list[tuple]] = {}   # connections onward from an airport, by the time they were asked from
        self.locks: dict[tuple, Any] = {}
        self.requests = 0
        self.next_slot = 0.0

    async def get(self, path: str, params: dict[str, Any]) -> Any:
        """One request, in its turn, tried again when the service says it is receiving too many, when it fails
        on its side (a 5xx answer) or when it does not answer in time."""
        for attempt in range(6):
            queued = time.time()
            async with self.semaphore:
                # Requests leave at a steady pace: a burst gets refused and costs more time than it saves
                wait = self.next_slot - time.time()
                self.next_slot = max(time.time(), self.next_slot) + TIMETABLE_SPACING_SECONDS
                if wait > 0:
                    await asyncio.sleep(wait)
                self.requests += 1
                try:
                    with logged("Transitous", asked(path, params)) as entry:
                        entry["waited"] = time.time() - queued
                        response = await self.http.get(f"{TRANSITOUS_API}/{path}", params=params)
                        if response.status_code != 200:
                            entry["result"] = str(response.status_code)
                except httpx.TransportError:
                    if attempt == 5:
                        raise
                    response = None
            if response is not None and response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response.json()
            await asyncio.sleep(1.5 * (attempt + 1) + random.random())
        response.raise_for_status()

    async def zone(self, latitude: float, longitude: float, country: str = "") -> str:
        """The time zone of a place, such as 'Europe/Lisbon'. country is its two-letter code, when it is known:
        the planner's map has nothing around some airfields (Paphos), and then the country says the zone."""
        key = f"{latitude:.2f},{longitude:.2f}"
        if key not in TIMEZONES:
            found = await self.get("reverse-geocode", {"place": f"{latitude},{longitude}"})
            zone = next((place["tz"] for place in found if place.get("tz")), "") or zone_of_country(country, latitude, longitude)
            if not zone:
                return ""
            TIMEZONES[key] = zone
            TIMEZONES_FILE.parent.mkdir(exist_ok=True)
            TIMEZONES_FILE.write_text(json.dumps(TIMEZONES), encoding="utf-8")
        return TIMEZONES[key]

    async def stops_around(self, airport: dict[str, Any], terminal: list[float] | None) -> list[dict[str, Any]]:
        """Every stop the timetables know within AIRPORT_STOP_KM of an airport's terminal, or of its airfield."""
        centre = terminal or (airport["latitude"], airport["longitude"])
        reach = AIRPORT_STOP_KM / 111   # in degrees of latitude. A degree of longitude is shorter away from the equator
        wide = reach / max(0.2, math.cos(math.radians(centre[0])))
        return await self.get("map/stops", {"min": f"{centre[0] - reach},{centre[1] - wide}", "max": f"{centre[0] + reach},{centre[1] + wide}"})

    async def airport_stops(self, airport: dict[str, Any], terminal: list[float] | None) -> list[tuple]:
        """The stops travellers use at an airport, see choose_airport_stops. Asked for once, then kept."""
        stops = known_airport_stops(airport["code"], terminal)
        if stops is None:
            stops = choose_airport_stops(await self.stops_around(airport, terminal), airport, terminal)
            keep_airport_stops(airport["code"], terminal, stops)
        return stops

    async def plan(self, start: tuple | str, end: tuple | str, day: str, minutes: int, arrive_by: bool, zones: tuple,
                   to_airport: bool, terminal_known: bool, modes: str = "", stop_walk: int = 0,
                   city_walk: int = CITY_WALK_SECONDS) -> list[dict[str, Any]]:
        """Connections between two points on one day, leaving after a time or arriving before it. The time is
        on the clock of the end point when arrive_by, otherwise of the start point. modes limits the kinds of
        vehicle, empty for everything on the ground.
        The times returned are minutes since the midnight that day starts with. Up to NIGHT_MINUTES across a
        midnight are accepted: below 0 a connection to the airport leaves the evening before, and above 24 hours
        one from the airport arrives in the night after (only if the traveller accepts arriving on a later day).
        One end is the city and the other the airport terminal (to_airport says which way). On the city side the
        time returned is that of the vehicle, on the airport side it includes the walk to or from the terminal.
        The airport end (start, or end when to_airport) may be a stop of the airport instead of a point (see
        airport_stops): stop_walk is then the minutes on foot between the terminal and it."""
        from_stop, to_stop = isinstance(start, str), isinstance(end, str)
        # The time asked for is at the terminal: at the stop it is the walk later when leaving, earlier when arriving
        at_stop = minutes + stop_walk if from_stop else minutes - stop_walk if to_stop and arrive_by else minutes
        airport_walk = AIRPORT_WALK_SECONDS if terminal_known else AIRFIELD_WALK_SECONDS
        walks = (city_walk, airport_walk) if to_airport else (airport_walk, city_walk)
        params = {
            "fromPlace": start if from_stop else f"{start[0]},{start[1]}", "toPlace": end if to_stop else f"{end[0]},{end[1]}",
            "time": local_to_utc(day, at_stop, zones[1] if arrive_by else zones[0]),
            "arriveBy": "true" if arrive_by else "false", "numItineraries": PAGE_SIZE,
            "maxPreTransitTime": walks[0], "maxPostTransitTime": walks[1],
            "transitModes": modes or GROUND_MODES,
        }
        found = await self.get("plan", params)
        connections = []
        for itinerary in found.get("itineraries") or []:
            legs = [leg for leg in itinerary["legs"] if leg["mode"] != "WALK"]
            if not legs or any(leg["mode"] == "AIRPLANE" for leg in legs):
                continue
            # Vehicle time on the city side. On the airport side the door of the terminal: the walk the planner
            # found when the terminal is known, a fixed time otherwise.
            first = itinerary["startTime"] if (terminal_known or from_stop) and not to_airport else legs[0]["startTime"]
            last = itinerary["endTime"] if (terminal_known or to_stop) and to_airport else legs[-1]["endTime"]
            start_day, leave = utc_to_local(first, zones[0])
            end_day, arrive = utc_to_local(last, zones[1])
            leave += (date.fromisoformat(start_day) - date.fromisoformat(day)).days * 24 * 60
            arrive += (date.fromisoformat(end_day) - date.fromisoformat(day)).days * 24 * 60
            if from_stop:
                leave -= stop_walk
            elif to_stop:
                arrive += stop_walk
            elif not terminal_known:
                leave, arrive = (leave, arrive + AIRFIELD_WALK_MINUTES) if to_airport else (leave - AIRFIELD_WALK_MINUTES, arrive)
            first_leave = -NIGHT_MINUTES if to_airport else 0
            last_arrive = 24 * 60 + (NIGHT_MINUTES if not to_airport and current.SETTINGS.next_day_arrivals else 0)
            if not first_leave <= leave < arrive < last_arrive or leave >= 24 * 60:
                continue
            connections.append({
                "leave": leave, "arrive": arrive,
                "steps": [{
                    "time": to_time(utc_to_local(leg["startTime"], leg["from"].get("tz") or zones[0])[1]),
                    "arrive": to_time(utc_to_local(leg["endTime"], leg["to"].get("tz") or zones[1])[1]),
                    "line": web.clean_text(leg.get("displayName") or leg.get("routeShortName") or leg["mode"].replace("_", " ").title(), 40),
                    "agency": web.clean_text(leg.get("agencyName") or "", 60),
                    "from": web.clean_text(leg["from"]["name"], 60), "to": web.clean_text(leg["to"]["name"], 60),
                    "rail": is_rail(leg["mode"]), "coach": leg["mode"] == "COACH",
                    # What an operator needs to recognise the two stops when it is asked for the price (fares.py)
                    "from_id": leg["from"].get("stopId") or "", "to_id": leg["to"].get("stopId") or "",
                    "from_at": [leg["from"]["lat"], leg["from"]["lon"]], "to_at": [leg["to"]["lat"], leg["to"]["lon"]],
                    "km": round(distance_km(leg["from"]["lat"], leg["from"]["lon"], leg["to"]["lat"], leg["to"]["lon"])),
                } for leg in legs],
                # Operator links come from the timetable feed the operator published
                "urls": list(dict.fromkeys(url for url in (web.secure_url(leg.get("agencyUrl")) for leg in legs) if url)),
            })
        if not connections and not modes and city_walk < WIDE_CITY_WALK_SECONDS:
            return await self.plan(start, end, day, minutes, arrive_by, zones, to_airport, terminal_known, modes, stop_walk, WIDE_CITY_WALK_SECONDS)
        return connections

    async def is_covered(self, start: tuple | str, end: tuple | str, day: str, zones: tuple, to_airport: bool, terminal_known: bool) -> bool:
        """Whether any timetable covers a route: is there a connection in the middle of the day?"""
        key = (start, end, day)
        if key not in self.probes:   # several flights ask about the same route at once: they share the answer
            self.probes[key] = asyncio.ensure_future(self.plan(start, end, day, 11 * 60, False, zones, to_airport, terminal_known))
        return bool(await self.probes[key])

    def answer(self, options: list[dict[str, Any]], service: str, day: str) -> dict[str, Any]:
        """What was found for one side of a flight. day is the day the traveller is there, service the day read."""
        grade = "A" if service == day else "B"
        unique = {(option["leave"], option["arrive"], len(option["steps"])): option for option in options}
        options = [{**option, "grade": grade, "source": "timetable", "service_day": service, "price_eur": None}
                   for option in unique.values()]
        return {"covered": True, "service_day": service, "grade": grade, "options": options}

    async def to_airport(self, start: tuple, airport: tuple, flight: dict[str, Any], zones: tuple, known: bool,
                         stops: list[tuple] = ()) -> dict[str, Any]:
        """Connections from the origin city that reach the terminal in time for a flight.
        Asked for up to the airport's stops (see airport_stops), and up to the point on the map when they give nothing."""
        service = reference_day(flight["date"])
        at_terminal = to_minutes(flight["departure_time"]) - current.SETTINGS.min_airport_minutes
        # Free trains: look for trains first, a paid coach is only worth it when no train fits
        modes = NO_COACH_MODES if current.SETTINGS.free_trains_at_origin else ""
        earliest, latest = to_minutes(current.SETTINGS.earliest_leave), to_minutes(current.SETTINGS.latest_leave)
        fits = lambda c: earliest <= c["leave"] <= latest and c["arrive"] <= at_terminal

        async def reaching(target: tuple | str, walk: int) -> list[dict[str, Any]]:
            """The connections that reach one point or one stop of the airport."""
            # The connections that arrive last before the limit. If none of them leaves within the times the traveller
            # accepts, also the ones that leave first after the earliest of those times and the ones that leave in the
            # last LAST_RIDES_MINUTES before the latest, to be sure nothing suitable exists.
            found = list(await self.plan(start, target, service, at_terminal, True, zones, True, known, modes, walk))
            if modes and not any(map(fits, found)):
                found += await self.plan(start, target, service, at_terminal, True, zones, True, known, "", walk)
            if not any(map(fits, found)):
                key = (start, target, service)   # the same question for every flight of that airport and day: asked once
                if key not in self.first_rides:
                    self.first_rides[key] = asyncio.ensure_future(self.plan(start, target, service, earliest, False, zones, True, known, "", walk))
                found += await self.first_rides[key]
                if latest < at_terminal:
                    # A flight late in the day: the connections that arrive last all leave after the traveller's latest
                    # time, and the first ones of the day would leave the traveller waiting for hours at the airport
                    if key not in self.last_rides:
                        self.last_rides[key] = asyncio.ensure_future(
                            self.plan(start, target, service, max(earliest, latest - LAST_RIDES_MINUTES), False, zones, True, known, "", walk))
                    found += await self.last_rides[key]
            return found

        found: list[dict[str, Any]] = []
        for target, walk in [*stops, (airport, 0)]:
            try:
                found = await reaching(target, walk)
            except httpx.HTTPStatusError:
                continue   # a stop the planner no longer knows: the next one, or the point on the map
            if found:
                break
        if not found:
            for target, walk in [*stops[:1], (airport, 0)]:
                if await self.is_covered(start, target, service, zones, True, known):
                    break
            else:
                return dict(NOT_COVERED)
        # Kept up to the shortest time at the airport ever offered: the ones that arrive after the time asked for
        # only serve the closest alternatives, when nothing respects every requirement
        last_call = to_minutes(flight["departure_time"]) - MIN_AIRPORT_FLOOR
        return self.answer([c for c in found if c["arrive"] <= max(at_terminal, last_call)], service, flight["date"])

    async def onward(self, airport: tuple | str, end: tuple, service: str, ready: int, zones: tuple, known: bool,
                     modes: str = "", stop_walk: int = 0) -> list[dict[str, Any]]:
        """The connections leaving an airport from a time on. One request serves every flight that lands while
        its answer still applies: several flights land at the same airport within a couple of hours."""
        key = (airport, end, service, modes)
        async with self.locks.setdefault(key, asyncio.Lock()):
            for asked_from, good_until, found in self.windows.setdefault(key, []):
                if asked_from <= ready <= good_until:
                    return [c for c in found if c["leave"] >= ready]
            found = await self.plan(airport, end, service, ready, False, zones, False, known, modes, stop_walk)
            leaves = sorted(c["leave"] for c in found)
            # A full answer stops somewhere, and later connections exist beyond it: it is only reused up to its
            # second-to-last connection. A short answer is everything left that day, so it serves any later time.
            good_until = leaves[-2] if len(found) >= PAGE_SIZE else 24 * 60
            self.windows[key].append((ready, good_until, found))
            return [c for c in found if c["leave"] >= ready]

    async def leaving(self, start: tuple | str, walk: int, end: tuple, service: str, ready: int, zones: tuple,
                      known: bool) -> list[dict[str, Any]]:
        """The connections that leave one point or one stop of an airport from a time on."""
        # Free trains: look for trains first, a paid coach is only worth it when no train is left that day
        if current.SETTINGS.free_trains_at_destination:
            return (await self.onward(start, end, service, ready, zones, known, NO_COACH_MODES, walk)
                    or await self.onward(start, end, service, ready, zones, known, "", walk))
        # An answer is the first connections to leave, and trains leave so often that they fill it: the coaches,
        # a few a day, are asked for on their own. Chains of local buses are left out, a coach is what is looked for.
        found, by_road = await asyncio.gather(
            self.onward(start, end, service, ready, zones, known, "", walk),
            self.onward(start, end, service, ready, zones, known, ROAD_MODES, walk))
        return found + [c for c in by_road if any(step["coach"] for step in c["steps"])]

    async def from_airport(self, airport: tuple, end: tuple, flight: dict[str, Any], zones: tuple, known: bool,
                           stops: list[tuple] = ()) -> dict[str, Any]:
        """Connections from the arrival airport to the destination that the traveller can catch after landing.
        Asked for from the airport's stops (see airport_stops), and from the point on the map when they give nothing."""
        service = reference_day(landing_day(flight))
        ready = to_minutes(flight["arrival_time"]) + current.SETTINGS.min_landing_minutes   # earliest time walking out of the terminal
        found: list[dict[str, Any]] = []
        for start, walk in [*stops, (airport, 0)]:
            try:
                found = await self.leaving(start, walk, end, service, ready, zones, known)
            except httpx.HTTPStatusError:
                continue   # a stop the planner no longer knows: the next one, or the point on the map
            if found:
                break
        if not found:
            for start, walk in [*stops[:1], (airport, 0)]:
                if await self.is_covered(start, end, service, zones, False, known):
                    break
            else:
                return dict(NOT_COVERED)
        return self.answer(found, service, landing_day(flight))

    async def close(self) -> None:
        await self.http.aclose()

def flights_to_check(flights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The flights whose ground transport is looked up: the cheapest, a few per departure airport and date.
    When the flights are the ones that break the traveller's limits (see search_flights), the ones that break the
    most important limits by the least come first: landing late, then stopovers, then the price."""
    settings = current.SETTINGS
    breaks = lambda flight: (max(0, to_minutes(flight["arrival_time"]) - settings.latest_arrival_minutes),
                             max(0, flight["stops"] - settings.max_stopovers))
    chosen, seen = [], {}
    for flight in sorted(flights, key=lambda flight: (*breaks(flight), flight["price_eur"])):
        key = (flight["departure_airport"], flight["date"])
        if len(chosen) < MAX_FLIGHTS_CHECKED and seen.get(key, 0) < MAX_FLIGHTS_PER_AIRPORT_DAY:
            seen[key] = seen.get(key, 0) + 1
            chosen.append(flight)
    return chosen

async def ground_transport_for(flights: list[dict[str, Any]]) -> dict[str, Any]:
    """Real ground transport for every flight: to its departure airport and from its arrival airport.
    Returns {flight id: {"to_airport": ..., "from_airport": ...}}, the operator rides under "operators"
    and the number of timetable requests under "requests"."""
    timetables = Timetables()
    origin, destination = (current.TRIP.origin.latitude, current.TRIP.origin.longitude), (current.TRIP.destination.latitude, current.TRIP.destination.longitude)
    try:
        used = {flight["departure_airport"] for flight in flights} | {flight["arrival_airport"] for flight in flights}
        airports = {code: current.TRIP.airport(code) for code in used}
        terminals = await asyncio.to_thread(find_terminals, list(airports.values()))
        known = {code: terminals.get(code) is not None for code in airports}
        spots = {code: tuple(terminals[code]) if known[code] else (airport["latitude"], airport["longitude"])
                 for code, airport in airports.items()}
        zone_list = await asyncio.gather(
            timetables.zone(*origin), timetables.zone(*destination), *(timetables.zone(*spots[code], airport["country"]) for code, airport in airports.items()),
            return_exceptions=True,
        )
        zones = dict(zip(["origin", "destination", *airports], [zone if isinstance(zone, str) else "" for zone in zone_list]))
        found_stops = await asyncio.gather(*(timetables.airport_stops(airports[code], terminals.get(code)) for code in airports), return_exceptions=True)
        stops = {code: found if isinstance(found, list) else [] for code, found in zip(airports, found_stops)}

        async def leg(lookup, missing_zone: bool) -> dict[str, Any]:
            if missing_zone:
                return dict(NOT_COVERED)
            try:
                return await lookup()
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", "")
                say(f"[timetables] {type(e).__name__} {status}: one leg is treated as not covered")
                return dict(NOT_COVERED)

        async def one(flight: dict[str, Any]) -> tuple:
            departure, arrival = flight["departure_airport"], flight["arrival_airport"]
            to_airport, from_airport = await asyncio.gather(
                leg(lambda: timetables.to_airport(origin, spots[departure], flight, (zones["origin"], zones[departure]), known[departure], stops[departure]),
                    not zones["origin"] or not zones[departure]),
                leg(lambda: timetables.from_airport(spots[arrival], destination, flight, (zones[arrival], zones["destination"]), known[arrival], stops[arrival]),
                    not zones["destination"] or not zones[arrival]),
            )
            return flight["id"], {"to_airport": to_airport, "from_airport": from_airport}

        in_landing_order = sorted(flights, key=lambda flight: (flight["arrival_airport"], landing_day(flight), flight["arrival_time"]))
        ground: dict[str, Any] = dict(await asyncio.gather(*(one(flight) for flight in in_landing_order)))

        # Operators with their own booking system: real rides and prices for the day, on both sides of the flight
        async def rides(name: str, source, code: str, day: str, to_airport: bool) -> tuple:
            city = current.TRIP.origin if to_airport else current.TRIP.destination
            try:
                with logged(name, f"rides {'to' if to_airport else 'from'} {code} on {day}") as entry:
                    found_rides = await asyncio.to_thread(source, city, airports[code], day, to_airport)
                    entry["result"] = "ok: no answer" if found_rides is None else f"ok: {len(found_rides)} rides"
                return to_airport, code, day, found_rides
            except Exception as e:
                say(f"[timetables] {name} {code} {day}: {type(e).__name__}")
                return to_airport, code, day, None
        wanted = sorted({(flight["departure_airport"], flight["date"], True) for flight in flights}
                        | {(flight["arrival_airport"], landing_day(flight), False) for flight in flights})
        found = await asyncio.gather(*(rides(name, source, *asked) for name, source in fares.RIDE_SOURCES.items() for asked in wanted))
        ground["operators"], ground["operators_from"] = {}, {}
        for to_airport, code, day, rides_found in found:
            if rides_found is not None:
                ground["operators" if to_airport else "operators_from"].setdefault(code, {}).setdefault(day, []).extend(rides_found)
        ground["requests"] = timetables.requests
        return ground
    finally:
        await timetables.close()
