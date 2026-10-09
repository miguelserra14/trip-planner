"""Real timetables. A model searching the web can find a travel time and a price, but not whether something
actually leaves at the right time on the travel date. So the ground transport of every flight is looked up
by code in two kinds of sources, and what a model says is only used where neither covers the route.

1. OPERATOR_CHECKERS (operators.py): an operator's own booking system. Real rides and real prices for the
   travel date (grade A).
2. Transitous: a free journey planner built on the timetables that operators publish, worldwide. Trains,
   metros, buses, coaches and airport shuttles of every operator that publishes one. No prices. Timetables are
   usually published only a few weeks ahead, so a far travel date is looked up on the same weekday of a nearer
   week (grade B, "typical timetable")."""
import asyncio
import json
import math
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from trip_planner import current
from trip_planner.config import ROOT, USER_AGENT
from trip_planner.places import distance_km
from trip_planner.sources import operators, web
from trip_planner.utils import plain, say, to_minutes, to_time

TRANSITOUS_API = "https://api.transitous.org/api/v1"
TIMETABLE_SOURCES = "Transitous (published timetables of all operators) + " + ", ".join(operators.OPERATOR_CHECKERS)
# Transitous is a free community service. It answers about one request every 1.4 seconds per user, however fast
# it is asked, so the time this step takes is the number of requests. Requests are shared wherever one answer
# serves several flights (see Timetables.onward).
TIMETABLE_CONCURRENCY = 2
TIMETABLE_SPACING_SECONDS = 0.5  # leave this long between two requests
PAGE_SIZE = 8                    # connections asked for per request
GROUND_MODES = "RAIL,SUBURBAN,SUBWAY,TRAM,BUS,COACH,FERRY"   # everything but planes: the planner also flies, and those answers are no use here
NO_COACH_MODES = "RAIL,SUBURBAN,SUBWAY,TRAM,BUS,FERRY"       # ... and but long-distance coaches
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
    return "RAIL" in mode or mode in ("SUBURBAN", "METRO")

def reference_day(day: str) -> str:
    """The same weekday as a travel date, in the first week that timetables are sure to be published for."""
    target, soon = date.fromisoformat(day), date.today() + timedelta(days=2)
    soon += timedelta(days=(target.weekday() - soon.weekday()) % 7)
    return min(soon, target).isoformat() if target >= date.today() else day

def local_to_utc(day: str, minutes: int, zone: str) -> str:
    local = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(zone)) + timedelta(minutes=minutes)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")

def utc_to_local(moment: str, zone: str) -> tuple:
    """A UTC time from the timetable as (yyyy-mm-dd, minutes since midnight) on the local clock."""
    local = datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
    return local.date().isoformat(), local.hour * 60 + local.minute

TERMINALS_FILE = ROOT / "resources" / "terminals.json"   # found once per airport, then reused
OVERPASS_SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                    "https://overpass.private.coffee/api/interpreter")
TERMINALS: dict[str, list[float]] = json.loads(TERMINALS_FILE.read_text(encoding="utf-8")) if TERMINALS_FILE.exists() else {}
NOT_A_PASSENGER_TERMINAL = ("general aviation", "executive", "gat", "cargo", "private", "vip", "pier", "connector", "freight")

def find_terminals(airports: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the passenger terminal of each airport is, from OpenStreetMap, or None when it is not known.
    The airport database only gives the middle of the airfield, which can be kilometres from the stops.
    One request for all the airports."""
    missing = [airport for airport in airports if airport["code"] not in TERMINALS]
    if missing:
        query = "[out:json][timeout:25];(" + "".join(
            f"nwr[aeroway=terminal](around:3500,{airport['latitude']},{airport['longitude']});" for airport in missing
        ) + ");out center tags;"
        elements = None
        for server in OVERPASS_SERVERS * 2:   # these public servers are often busy: each one is asked twice
            try:
                response = httpx.post(server, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=25)
                response.raise_for_status()
                elements = response.json()["elements"]
                break
            except Exception:
                continue
        if elements is None:
            say("[timetables] OpenStreetMap did not answer: using the middle of each airfield instead of the terminal")
            return {airport["code"]: TERMINALS.get(airport["code"]) for airport in airports}
        best: dict[str, tuple] = {}
        for element in elements:
            point = element.get("center") or element
            name = plain((element.get("tags") or {}).get("name", ""))
            airport = min(missing, key=lambda a: distance_km(a["latitude"], a["longitude"], point["lat"], point["lon"]))
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
            if airport["code"] in best:
                TERMINALS[airport["code"]] = list(best[airport["code"]][1])
        TERMINALS_FILE.parent.mkdir(exist_ok=True)
        TERMINALS_FILE.write_text(json.dumps(TERMINALS), encoding="utf-8")
    return {airport["code"]: TERMINALS.get(airport["code"]) for airport in airports}

NOT_COVERED = {"covered": False, "service_day": None, "grade": "C", "options": []}

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
            async with self.semaphore:
                # Requests leave at a steady pace: a burst gets refused and costs more time than it saves
                wait = self.next_slot - time.time()
                self.next_slot = max(time.time(), self.next_slot) + TIMETABLE_SPACING_SECONDS
                if wait > 0:
                    await asyncio.sleep(wait)
                self.requests += 1
                try:
                    response = await self.http.get(f"{TRANSITOUS_API}/{path}", params=params)
                except httpx.TransportError:
                    if attempt == 5:
                        raise
                    response = None
            if response is not None and response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response.json()
            await asyncio.sleep(1.5 * (attempt + 1) + random.random())
        response.raise_for_status()

    async def zone(self, latitude: float, longitude: float) -> str:
        """The time zone of a place, such as 'Europe/Lisbon'."""
        key = f"{latitude:.2f},{longitude:.2f}"
        if key not in TIMEZONES:
            found = await self.get("reverse-geocode", {"place": f"{latitude},{longitude}"})
            zone = next((place["tz"] for place in found if place.get("tz")), "")
            if not zone:
                return ""
            TIMEZONES[key] = zone
            TIMEZONES_FILE.parent.mkdir(exist_ok=True)
            TIMEZONES_FILE.write_text(json.dumps(TIMEZONES), encoding="utf-8")
        return TIMEZONES[key]

    async def airport_stops(self, airport: dict[str, Any], terminal: list[float] | None) -> list[tuple]:
        """The stops travellers use at an airport, as (stop id, minutes on foot from the terminal): the nearest
        with trains, then with buses, then with coaches. Empty when the timetables know none."""
        centre = terminal or (airport["latitude"], airport["longitude"])
        reach = AIRPORT_STOP_KM / 111   # in degrees of latitude. A degree of longitude is shorter away from the equator
        wide = reach / max(0.2, math.cos(math.radians(centre[0])))
        stops = await self.get("map/stops", {"min": f"{centre[0] - reach},{centre[1] - wide}", "max": f"{centre[0] + reach},{centre[1] + wide}"})
        ground = [stop for stop in stops if set(stop.get("modes") or []) - {"AIRPLANE"}]
        km = lambda stop: distance_km(centre[0], centre[1], stop["lat"], stop["lon"])
        named = lambda stop: any(word in plain(stop.get("name") or "") for word in AIRPORT_WORDS)
        chosen = [stop for stop in ground if km(stop) <= TERMINAL_STOP_KM] if terminal else []
        by_name = not chosen   # no terminal, or none of the stops is next to it: the ones named after the airport
        if by_name:
            chosen = [stop for stop in ground if km(stop) <= AIRPORT_STOP_KM and named(stop)]
        kinds = (lambda modes: any(is_rail(mode) for mode in modes), lambda modes: bool(modes & {"BUS", "TRAM", "SUBWAY"}),
                 lambda modes: "COACH" in modes)
        best: list[dict[str, Any]] = []
        for kind in kinds:
            of_kind = [stop for stop in chosen if kind(set(stop["modes"])) and stop not in best]
            if of_kind:
                best.append(min(of_kind, key=km))
        walk = lambda stop: AIRFIELD_WALK_MINUTES if by_name else max(MIN_STOP_WALK_MINUTES, round(km(stop) * 1.3 / WALK_KMH * 60))
        return [(stop["stopId"], walk(stop)) for stop in best[:MAX_AIRPORT_STOPS]]

    async def plan(self, start: tuple | str, end: tuple, day: str, minutes: int, arrive_by: bool, zones: tuple,
                   to_airport: bool, terminal_known: bool, modes: str = "", stop_walk: int = 0,
                   city_walk: int = CITY_WALK_SECONDS) -> list[dict[str, Any]]:
        """Connections between two points on one day, leaving after a time or arriving before it. The time is
        on the clock of the end point when arrive_by, otherwise of the start point. modes limits the kinds of
        vehicle, empty for everything on the ground.
        One end is the city and the other the airport terminal (to_airport says which way). On the city side the
        time returned is that of the vehicle, on the airport side it includes the walk to or from the terminal.
        start may be a stop of the airport instead of a point (see airport_stops): stop_walk is then the minutes
        on foot from the terminal to it."""
        from_stop = isinstance(start, str)
        airport_walk = AIRPORT_WALK_SECONDS if terminal_known else AIRFIELD_WALK_SECONDS
        walks = (city_walk, airport_walk) if to_airport else (airport_walk, city_walk)
        params = {
            "fromPlace": start if from_stop else f"{start[0]},{start[1]}", "toPlace": f"{end[0]},{end[1]}",
            "time": local_to_utc(day, minutes + (stop_walk if from_stop else 0), zones[1] if arrive_by else zones[0]),
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
            last = itinerary["endTime"] if terminal_known and to_airport else legs[-1]["endTime"]
            start_day, leave = utc_to_local(first, zones[0])
            end_day, arrive = utc_to_local(last, zones[1])
            if from_stop:
                leave -= stop_walk
            elif not terminal_known:
                leave, arrive = (leave, arrive + AIRFIELD_WALK_MINUTES) if to_airport else (leave - AIRFIELD_WALK_MINUTES, arrive)
            if start_day != day or end_day != day or not 0 <= leave < arrive < 24 * 60:
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
                    "km": round(distance_km(leg["from"]["lat"], leg["from"]["lon"], leg["to"]["lat"], leg["to"]["lon"])),
                } for leg in legs],
                # Operator links come from the timetable feed the operator published
                "urls": list(dict.fromkeys(url for url in (web.secure_url(leg.get("agencyUrl")) for leg in legs) if url)),
            })
        if not connections and not modes and city_walk < WIDE_CITY_WALK_SECONDS:
            return await self.plan(start, end, day, minutes, arrive_by, zones, to_airport, terminal_known, modes, stop_walk, WIDE_CITY_WALK_SECONDS)
        return connections

    async def is_covered(self, start: tuple | str, end: tuple, day: str, zones: tuple, to_airport: bool, terminal_known: bool) -> bool:
        """Whether any timetable covers a route: is there a connection in the middle of the day?"""
        key = (start, end, day)
        if key not in self.probes:   # several flights ask about the same route at once: they share the answer
            self.probes[key] = asyncio.ensure_future(self.plan(start, end, day, 11 * 60, False, zones, to_airport, terminal_known))
        return bool(await self.probes[key])

    def answer(self, options: list[dict[str, Any]], service: str, flight: dict[str, Any]) -> dict[str, Any]:
        grade = "A" if service == flight["date"] else "B"
        unique = {(option["leave"], option["arrive"], len(option["steps"])): option for option in options}
        options = [{**option, "grade": grade, "source": "timetable", "service_day": service, "price_eur": None}
                   for option in unique.values()]
        return {"covered": True, "service_day": service, "grade": grade, "options": options}

    async def to_airport(self, start: tuple, airport: tuple, flight: dict[str, Any], zones: tuple, known: bool) -> dict[str, Any]:
        """Connections from the origin city that reach the terminal in time for a flight."""
        service = reference_day(flight["date"])
        at_terminal = to_minutes(flight["departure_time"]) - current.SETTINGS.min_airport_minutes
        # The connections that arrive last before the limit. If none of them leaves within the times the traveller
        # accepts, also the ones that leave first after the earliest of those times and the ones that leave in the
        # last LAST_RIDES_MINUTES before the latest, to be sure nothing suitable exists.
        # Free trains: look for trains first, a paid coach is only worth it when no train fits
        modes = NO_COACH_MODES if current.SETTINGS.free_trains_at_origin else ""
        earliest, latest = to_minutes(current.SETTINGS.earliest_leave), to_minutes(current.SETTINGS.latest_leave)
        fits = lambda c: earliest <= c["leave"] <= latest and c["arrive"] <= at_terminal
        found = list(await self.plan(start, airport, service, at_terminal, True, zones, True, known, modes))
        if modes and not any(map(fits, found)):
            found += await self.plan(start, airport, service, at_terminal, True, zones, True, known)
        if not any(map(fits, found)):
            key = (start, airport, service)   # the same question for every flight of that airport and day: asked once
            if key not in self.first_rides:
                self.first_rides[key] = asyncio.ensure_future(self.plan(start, airport, service, earliest, False, zones, True, known))
            if latest < at_terminal:
                # A flight late in the day: the connections that arrive last all leave after the traveller's latest
                # time, and the first ones of the day would leave the traveller waiting for hours at the airport
                if key not in self.last_rides:
                    self.last_rides[key] = asyncio.ensure_future(
                        self.plan(start, airport, service, max(earliest, latest - LAST_RIDES_MINUTES), False, zones, True, known))
                found += await self.last_rides[key]
        if not found and not await self.is_covered(start, airport, service, zones, True, known):
            return dict(NOT_COVERED)
        # Kept up to the shortest time at the airport ever offered: the ones that arrive after the time asked for
        # only serve the closest alternatives, when nothing respects every requirement
        last_call = to_minutes(flight["departure_time"]) - MIN_AIRPORT_FLOOR
        return self.answer([c for c in found if c["arrive"] <= max(at_terminal, last_call)], service, flight)

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
        service = reference_day(flight["date"])
        ready = to_minutes(flight["arrival_time"]) + current.SETTINGS.min_landing_minutes   # earliest time walking out of the terminal
        found: list[dict[str, Any]] = []
        for start, walk in [*stops, (airport, 0)]:
            found = await self.leaving(start, walk, end, service, ready, zones, known)
            if found:
                break
        if not found:
            for start, walk in [*stops[:1], (airport, 0)]:
                if await self.is_covered(start, end, service, zones, False, known):
                    break
            else:
                return dict(NOT_COVERED)
        return self.answer(found, service, flight)

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
            timetables.zone(*origin), timetables.zone(*destination), *(timetables.zone(*spots[code]) for code in airports),
            return_exceptions=True,
        )
        zones = dict(zip(["origin", "destination", *airports], [zone if isinstance(zone, str) else "" for zone in zone_list]))
        landed = sorted({flight["arrival_airport"] for flight in flights})
        found_stops = await asyncio.gather(*(timetables.airport_stops(airports[code], terminals.get(code)) for code in landed), return_exceptions=True)
        stops = {code: found if isinstance(found, list) else [] for code, found in zip(landed, found_stops)}

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
                leg(lambda: timetables.to_airport(origin, spots[departure], flight, (zones["origin"], zones[departure]), known[departure]),
                    not zones["origin"] or not zones[departure]),
                leg(lambda: timetables.from_airport(spots[arrival], destination, flight, (zones[arrival], zones["destination"]), known[arrival], stops[arrival]),
                    not zones["destination"] or not zones[arrival]),
            )
            return flight["id"], {"to_airport": to_airport, "from_airport": from_airport}

        in_landing_order = sorted(flights, key=lambda flight: (flight["arrival_airport"], flight["date"], flight["arrival_time"]))
        ground: dict[str, Any] = dict(await asyncio.gather(*(one(flight) for flight in in_landing_order)))

        # Operators with their own booking system: real rides and prices for the travel date
        async def rides(name: str, checker, code: str, day: str) -> tuple:
            try:
                return code, day, await asyncio.to_thread(checker, current.TRIP.origin, airports[code], day)
            except Exception as e:
                say(f"[timetables] {name} {code} {day}: {type(e).__name__}")
                return code, day, None
        wanted = sorted({(flight["departure_airport"], flight["date"]) for flight in flights})
        found = await asyncio.gather(*(rides(name, checker, code, day) for name, checker in operators.OPERATOR_CHECKERS.items() for code, day in wanted))
        operator_rides: dict[str, Any] = {}
        for code, day, rides_found in found:
            if rides_found is not None:
                operator_rides.setdefault(code, {}).setdefault(day, []).extend(rides_found)
        ground["operators"] = operator_rides
        ground["requests"] = timetables.requests
        return ground
    finally:
        await timetables.close()
