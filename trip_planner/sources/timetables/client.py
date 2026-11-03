"""Connections in the published timetables, read through Transitous: between a city and an airport on one day, and
mixed legs where no timetable reaches one of the two ends."""
import asyncio
import hashlib
import json
import math
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from trip_planner import current
from trip_planner.trip import ground_choice
from trip_planner.trip.trip_settings import ROOT, USER_AGENT
from trip_planner.trip.ground_choice import is_rail
from trip_planner.itineraries.estimates import ESTIMATE_FIXED_MINUTES, estimated_minutes, ROAD_FACTOR
from trip_planner.trip.places import distance_km, ROADS_URL
from trip_planner.shapes import Answer, Connection, Step
from trip_planner.sources import web
from trip_planner.sources.timetables.airport_stops import (AIRFIELD_WALK_MINUTES, AIRFIELD_WALK_SECONDS, AIRPORT_STOP_KM,
                                                         choose_airport_stops, keep_airport_stops, known_airport_stops)
from trip_planner.sources.timetables.days import (day_bounds, landing_day, local_to_utc, minutes_since, reference_day, utc_to_local,
                                                zone_of_country)
from trip_planner.sources.timetables.modes import GROUND_MODES, NO_COACH_MODES, ROAD_MODES
from trip_planner.texts import tr
from trip_planner.utils import logged, to_minutes, to_time, write_whole

TRANSITOUS_API = "https://api.transitous.org/api/v1"
# Transitous is a free community service. It answers about one request every 1.4 seconds per user, however fast
# it is asked, so the time this step takes is the number of requests. Requests are shared wherever one answer
# serves several flights (see Timetables.onward).
# Tried on 2026-10-14 with 4 and 6 requests at a time: none was refused, but each took that much longer (a route
# of 6 seconds took 12, then 18) and the search took the same time. Only asking less makes this step faster.
TIMETABLE_CONCURRENCY = 2
TIMETABLE_SPACING_SECONDS = 0.5  # leave this long between two requests
PAGE_SIZE = 8                    # connections asked for per request
# The walks allowed at each end are settings: city_walk_minutes, wide_city_walk_minutes, airport_walk_minutes (trip/trip_settings.py)
LAST_RIDES_MINUTES = 120         # how long before the latest time the traveller leaves at the last connections are asked from
TIMEZONES_FILE = ROOT / "resources" / "timezones.json"   # found once per place, then reused
TIMEZONES: dict[str, str] = json.loads(TIMEZONES_FILE.read_text(encoding="utf-8")) if TIMEZONES_FILE.exists() else {}
STATIONS_FILE = ROOT / "resources" / "stations.json"   # where an operator's coach station is: found once per name and airport, then reused
STATIONS: dict[str, list] = json.loads(STATIONS_FILE.read_text(encoding="utf-8")) if STATIONS_FILE.exists() else {}
# The answers of a search are kept for the next ones (Timetables(keep=True)): the same trip searched again asks
# nothing it asked in the last KEPT_HOURS. One file per request, named by what was asked.
KEPT_DIR = ROOT / "cache" / "timetables"
KEPT_HOURS = 24
KEPT_FROM_HOURS = 6              # connections sooner than this are always asked: the timetables then carry delays and cancellations.
                                 # The typical timetable of a far date is often read for the next days (reference_day): that one is kept
NOT_KEPT = ("legGeometry", "steps")   # what a connection's vehicle carries that nothing here reads: its shape on the map and the
                                      # directions of a walk, four fifths of an answer

NOT_COVERED = {"covered": False, "service_day": None, "grade": "C", "options": []}

def box_around(point: tuple | list, km: float) -> dict[str, str]:
    """The corners of a square of about km around a point, as the timetables' map of stops asks for them. A degree
    of latitude is about 111 km; a degree of longitude is shorter away from the equator."""
    reach = km / 111
    wide = reach / max(0.2, math.cos(math.radians(point[0])))
    return {"min": f"{point[0] - reach},{point[1] - wide}", "max": f"{point[0] + reach},{point[1] + wide}"}

def step_of(leg: dict[str, Any], zones: tuple) -> Step:
    """One vehicle of a connection the timetables found, as the planner keeps it."""
    return {
        "time": to_time(utc_to_local(leg["startTime"], leg["from"].get("tz") or zones[0])[1]),
        "arrive": to_time(utc_to_local(leg["endTime"], leg["to"].get("tz") or zones[1])[1]),
        "line": web.clean_text(leg.get("displayName") or leg.get("routeShortName") or leg["mode"].replace("_", " ").title(), 40),
        "agency": web.clean_text(leg.get("agencyName") or "", 60),
        "from": web.clean_text(leg["from"]["name"], 60), "to": web.clean_text(leg["to"]["name"], 60),
        "rail": is_rail(leg["mode"]), "coach": leg["mode"] == "COACH", "mode": leg["mode"],
        # What an operator needs to recognise the two stops when it is asked for the price (fares.py)
        "from_id": leg["from"].get("stopId") or "", "to_id": leg["to"].get("stopId") or "",
        "from_at": [leg["from"]["lat"], leg["from"]["lon"]], "to_at": [leg["to"]["lat"], leg["to"]["lon"]],
        "km": round(distance_km(leg["from"]["lat"], leg["from"]["lon"], leg["to"]["lat"], leg["to"]["lon"])),
    }

async def unless_refused(asking: Awaitable[list[Connection]]) -> list[Connection]:
    """The connections a request finds, or none when the timetables refuse it: a stop or a station they no longer know."""
    try:
        return await asking
    except httpx.HTTPStatusError:
        return []

async def first_found(ends: list[tuple], ask: Callable[[tuple | str, int], Awaitable[list[Connection]]]) -> list[Connection]:
    """The connections of the first end of an airport that has any: each of its stops (see airport_stops), then its
    point on the map. ends are (stop or point, minutes on foot from the terminal)."""
    for end, walk in ends:
        found = await unless_refused(ask(end, walk))
        if found:
            return found
    return []

def kept_file(path: str, params: dict[str, Any]) -> Path:
    """Where the answer to a request is kept."""
    return KEPT_DIR / f"{hashlib.sha1(json.dumps([path, params], sort_keys=True).encode()).hexdigest()}.json"

def soon(path: str, params: dict[str, Any]) -> bool:
    """Whether a request asks for connections in the next KEPT_FROM_HOURS."""
    if path != "plan":
        return False
    return datetime.fromisoformat(params["time"].replace("Z", "+00:00")) < datetime.now(timezone.utc) + timedelta(hours=KEPT_FROM_HOURS)

def slim(answer: Any) -> Any:
    """An answer as it is kept: without NOT_KEPT."""
    if not isinstance(answer, dict) or "itineraries" not in answer:
        return answer
    return {**answer, "itineraries": [{**itinerary, "legs": [{key: value for key, value in leg.items() if key not in NOT_KEPT}
                                                             for leg in itinerary.get("legs") or []]}
                                      for itinerary in answer["itineraries"] or []]}

def asked(path: str, params: dict[str, Any]) -> str:
    """A request to the timetables in a few words, for the log of the run."""
    if path != "plan":
        return f"{path} " + " ".join(str(value) for value in params.values())
    # A request asked again with a longer walk (see plan) would otherwise read as the same one twice
    walk = max(params.get("maxPreTransitTime", 0), params.get("maxPostTransitTime", 0)) // 60
    return (f"plan {params['fromPlace']} -> {params['toPlace']}, {'arriving by' if params['arriveBy'] == 'true' else 'leaving from'} "
            f"{params['time']}" + ("" if params["transitModes"] == GROUND_MODES else f", only {params['transitModes']}")
            + (f", walking up to {walk} min" if walk >= current.SETTINGS.wide_city_walk_minutes else ""))

# Mixed legs. Where no published timetable joins an airport and its city (the buses of Campogalliano, of Debrecen
# airport, of Nafplio are not published), the published part goes as far as it can: to the station nearest to the
# place it does not reach. Only the rest, at most max_estimate_km, is estimated from the distance.
HUB_RADII_KM = (8, 20)           # where stations are looked for, nearer first; then up to max_estimate_km
HUBS_TRIED = 3                   # stations tried as the end of the published part: two railway stations, then coach or bus
                                 # stops. The one that gives the shortest trip is kept: the nearest station can be across a
                                 # mountain (Kotor)
SAME_HUB_KM = 0.5                # stops this close are one station (each platform and each operator has its own)

def by_road(point: tuple, hubs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stations a mixed leg may end at, with the km and the minutes by road from the place under "road_km" and
    "road_minutes", and only those within max_estimate_km by road: a station near on the map can be across a
    mountain (Danilovgrad is 32 km from Kotor, and 95 by road). One request to OSRM for them all; the straight line
    is kept when it does not answer."""
    if not hubs:
        return hubs
    try:
        with logged("OSRM", f"roads to {len(hubs)} stations"):
            points = [point, *((hub["lat"], hub["lon"]) for hub in hubs)]
            response = httpx.get(ROADS_URL + ";".join(f"{longitude},{latitude}" for latitude, longitude in points),
                                 params={"sources": 0, "annotations": "duration,distance"}, headers={"User-Agent": USER_AGENT}, timeout=15)
            response.raise_for_status()
            table = response.json()
        metres, seconds = table["distances"][0][1:], table["durations"][0][1:]
    except Exception:
        return hubs
    kept = []
    for hub, distance, duration in zip(hubs, metres, seconds):
        if distance is None or duration is None:
            continue   # no road reaches it
        road_km = max(distance / 1000, hub["km"])
        if road_km <= current.SETTINGS.max_estimate_km:
            kept.append({**hub, "road_km": road_km, "road_minutes": duration / 60})
    return kept

def fastest(through: list[list[Connection]], to_airport: bool) -> list[Connection]:
    """Of the connections through several stations, those through the station that gives the shortest trip.
    The estimated stretch makes a connection longer: it must still start and end within the times plan accepts."""
    first_leave, last_arrive = day_bounds(to_airport)
    through =[kept for kept in ([c for c in found if first_leave <= c["leave"] and c["arrive"] < last_arrive] for found in through) if kept]
    return min(through, key=lambda found: min(c["arrive"] - c["leave"] for c in found), default=[])

def estimated_stretch(start: str, end: str, hub: dict[str, Any], start_at: tuple, end_at: tuple) -> Step:
    """The estimated part of a mixed leg, as one step of it but with no times yet (with_stretch gives them): between
    start and end, one of which is the station hub. As long as by road, when the road is known, plus the time getting
    a bus or a taxi; at least what the straight line gives."""
    road_km = hub.get("road_km", hub["km"] * ROAD_FACTOR)
    minutes = max(estimated_minutes(hub["km"]), round(hub.get("road_minutes", 0)) + ESTIMATE_FIXED_MINUTES)
    return {"line": tr("estimated_stretch"), "agency": "", "from": web.clean_text(start, 60), "to": web.clean_text(end, 60),
            "rail": False, "coach": False, "from_id": "", "to_id": "", "from_at": list(start_at), "to_at": list(end_at),
            "km": round(road_km / ROAD_FACTOR), "road_km": round(road_km), "estimated": True, "minutes": minutes}

def with_stretch(found: list[Connection], stretch: Step, before: bool, start: str, end: str) -> list[Connection]:
    """Published connections with the estimated stretch between start and end before or after each one, timed to
    meet it: it ends when the connection leaves, or starts when it arrives."""
    minutes = stretch["minutes"]
    note = tr("note_estimated_stretch", km=stretch["road_km"], start=start, end=end)
    joined = []
    for connection in found:
        if before:
            step = {"time": to_time(connection["leave"] - minutes), "arrive": to_time(connection["leave"]), **stretch}
            joined.append({**connection, "leave": connection["leave"] - minutes, "steps": [step, *connection["steps"]],
                           "estimated_km": stretch["road_km"], "notes": [note]})
        else:
            step = {"time": to_time(connection["arrive"]), "arrive": to_time(connection["arrive"] + minutes), **stretch}
            joined.append({**connection, "arrive": connection["arrive"] + minutes, "steps": [*connection["steps"], step],
                           "estimated_km": stretch["road_km"], "notes": [note]})
    return joined

class Timetables:
    """Looks connections up in the published timetables, a few requests at a time. One is used for a whole search,
    so that what one round of flights asked serves the next ones. keep=True also keeps the answers for the next
    searches (KEPT_HOURS), and uses those of the last ones."""

    def __init__(self, keep: bool = False):
        self.http = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=40)
        self.semaphore = asyncio.Semaphore(TIMETABLE_CONCURRENCY)
        self.waiting = 0                         # requests waiting for their turn that the later ones let go first
        self.nobody_waiting = asyncio.Event()
        self.nobody_waiting.set()
        self.keep = keep
        if keep:
            self.forget_old_answers()
        self.probes: dict[tuple, Any] = {}       # routes already asked about in the middle of the day
        self.last_rides: dict[tuple, Any] = {}   # last connections to an airport before the latest time the traveller leaves at
        self.first_rides: dict[tuple, Any] = {}  # first connections of the day to an airport
        self.windows: dict[tuple, list[tuple]] = {}   # connections onward from an airport, by the time they were asked from
        self.locks: dict[tuple, Any] = {}
        self.requests = 0
        self.hub_cache: dict[tuple, Any] = {}   # stations near each place no timetable reaches
        self.station_cache: dict[tuple, Any] = {}   # the stations being looked for (STATIONS has the ones found)
        self.joins: dict[tuple, Any] = {}           # connections between an airport and a coach station (joined.py)
        self.next_slot = 0.0

    def forget_old_answers(self) -> None:
        """Delete the kept answers older than KEPT_HOURS."""
        try:
            for file in KEPT_DIR.glob("*.json"):
                if time.time() - file.stat().st_mtime > KEPT_HOURS * 3600:
                    file.unlink(missing_ok=True)
        except OSError:
            pass   # OneDrive may hold a file a moment: it is deleted by the next search

    def kept_answer(self, path: str, params: dict[str, Any]) -> Any:
        """The answer an earlier search got to the same request in the last KEPT_HOURS, or None. Not for
        connections that have come within KEPT_FROM_HOURS since."""
        if not self.keep or soon(path, params):
            return None
        try:
            saved = json.loads(kept_file(path, params).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - saved["at"] > KEPT_HOURS * 3600:
            return None
        # In the log, apart from the requests: nothing was asked of the service
        with logged("Transitous (kept)", asked(path, params)) as entry:
            entry["result"] = f"ok: answer of {time.strftime('%d/%m %H:%M', time.localtime(saved['at']))}"
        return saved["answer"]

    async def turn(self, later: bool) -> None:
        """Wait for a request's turn and take it: self.semaphore is then released by the caller. A later request
        also lets every other request waiting go first."""
        while later:
            await self.nobody_waiting.wait()
            await self.semaphore.acquire()
            if self.nobody_waiting.is_set():
                return
            self.semaphore.release()   # another request came while this one waited for its turn: it goes first
        self.waiting += 1
        self.nobody_waiting.clear()
        try:
            await self.semaphore.acquire()
        finally:
            self.waiting -= 1
            if not self.waiting:
                self.nobody_waiting.set()

    async def get(self, path: str, params: dict[str, Any], later: bool = False) -> Any:
        """One request, in its turn, tried again when the service says it is receiving too many, when it fails
        on its side (a 5xx answer) or when it does not answer in time.
        later is for the requests of the trip without a plane: the service answers so many a second, however many
        are sent, and the search waits for the flights' connections, so those go first."""
        kept = self.kept_answer(path, params)
        if kept is not None:
            return kept
        for attempt in range(6):
            queued = time.time()
            await self.turn(later)
            try:
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
            finally:
                self.semaphore.release()
            if response is not None and response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                found = response.json()
                if self.keep and not soon(path, params):
                    write_whole(kept_file(path, params), json.dumps({"at": time.time(), "answer": slim(found)}))
                return found
            await asyncio.sleep(1.5 * (attempt + 1) + random.random())
        response.raise_for_status()

    async def shared(self, answers: dict[tuple, Any], key: tuple, ask: Callable[[], Awaitable[Any]]) -> Any:
        """The answer to a question several flights ask at once, asked once and shared through answers. One that
        failed is asked again by whoever asks next: a failure is not kept for the rounds that follow."""
        if key not in answers or (answers[key].done() and (answers[key].cancelled() or answers[key].exception() is not None)):
            answers[key] = asyncio.ensure_future(ask())
        return await answers[key]

    async def station_point(self, name: str, near: tuple) -> tuple | None:
        """Where a coach station an operator names is ("Zagreb Central Bus Station"), found by name in the
        timetables' places: the first one within max_estimate_km of near. None when there is none: the service
        answers a name with places of that name anywhere (a "Bus Station" in the Philippines for Podgorica's).
        Every place it answers is kept in STATIONS_FILE, in its order: max_estimate_km is a setting, applied here."""
        key = f"{name}|{near[0]:.4f},{near[1]:.4f}"
        if key not in STATIONS:
            try:
                # Several flights ask for the same station at once: they share the answer
                await self.shared(self.station_cache, (key,), lambda: self.find_station(name, near, key))
            except httpx.HTTPError:
                return None   # asked again by the next search
        return next((tuple(place) for place in STATIONS[key]
                     if distance_km(near[0], near[1], *place) <= current.SETTINGS.max_estimate_km), None)

    async def find_station(self, name: str, near: tuple, key: str) -> None:
        """The places the timetables answer for a station's name, kept under key."""
        found = await self.get("geocode", {"text": name, "place": f"{near[0]},{near[1]}"})
        STATIONS[key] = [[place["lat"], place["lon"]] for place in found]
        write_whole(STATIONS_FILE, json.dumps(STATIONS, ensure_ascii=False))

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
            write_whole(TIMEZONES_FILE, json.dumps(TIMEZONES))
        return TIMEZONES[key]

    async def stops_around(self, airport: dict[str, Any], terminal: list[float] | None) -> list[dict[str, Any]]:
        """Every stop the timetables know within AIRPORT_STOP_KM of an airport's terminal, or of its airfield."""
        return await self.get("map/stops", box_around(terminal or (airport["latitude"], airport["longitude"]), AIRPORT_STOP_KM))

    async def airport_stops(self, airport: dict[str, Any], terminal: list[float] | None) -> list[tuple]:
        """The stops travellers use at an airport, see choose_airport_stops. Asked for once, then kept."""
        stops = known_airport_stops(airport["code"], terminal)
        if stops is None:
            stops = choose_airport_stops(await self.stops_around(airport, terminal), airport, terminal)
            keep_airport_stops(airport["code"], terminal, stops)
        return stops

    async def plan(self, start: tuple | str, end: tuple | str, day: str, minutes: int, *, arrive_by: bool, zones: tuple,
                   to_airport: bool, terminal_known: bool, modes: str = "", stop_walk: int = 0,
                   city_walk: int | None = None, any_way: bool = False) -> list[Connection]:
        """Connections between two points on one day, leaving after a time or arriving before it. The time is
        on the clock of the end point when arrive_by, otherwise of the start point. modes limits the kinds of
        vehicle, empty for everything on the ground.
        city_walk is the walk allowed at the city end in seconds, city_walk_minutes of the settings when not given.
        The times returned are minutes since the midnight that day starts with. Up to night_minutes across a
        midnight are accepted: below 0 a connection to the airport leaves the evening before, and above 24 hours
        one from the airport arrives in the night after (only if the traveller accepts arriving on a later day).
        One end is the city and the other the airport terminal (to_airport says which way). On the city side the
        time returned is that of the vehicle, on the airport side it includes the walk to or from the terminal.
        The airport end (start, or end when to_airport) may be a stop of the airport instead of a point (see
        airport_stops): stop_walk is then the minutes on foot between the terminal and it.
        Only the connections the traveller accepts on that side are asked for and returned (ground_choice.py),
        unless any_way: then every connection, to know whether the timetables cover the route at all."""
        settings = current.SETTINGS
        city_walk = settings.city_walk_minutes * 60 if city_walk is None else city_walk
        from_stop, to_stop = isinstance(start, str), isinstance(end, str)
        # Only what the traveller accepts on this side is asked for (ground_choice.py)
        wanted = modes or GROUND_MODES if any_way else ground_choice.allowed_modes(modes or GROUND_MODES, to_airport)
        if not wanted:
            return []
        # The time asked for is at the terminal: at the stop it is the walk later when leaving, earlier when arriving
        at_stop = minutes + stop_walk if from_stop else minutes - stop_walk if to_stop and arrive_by else minutes
        airport_walk = settings.airport_walk_minutes * 60 if terminal_known else AIRFIELD_WALK_SECONDS
        walks = (city_walk, airport_walk) if to_airport else (airport_walk, city_walk)
        params = {
            "fromPlace": start if from_stop else f"{start[0]},{start[1]}", "toPlace": end if to_stop else f"{end[0]},{end[1]}",
            "time": local_to_utc(day, at_stop, zones[1] if arrive_by else zones[0]),
            "arriveBy": "true" if arrive_by else "false", "numItineraries": PAGE_SIZE,
            "maxPreTransitTime": walks[0], "maxPostTransitTime": walks[1],
            "transitModes": wanted,
        }
        if ground_choice.max_transfers(to_airport) is not None and not any_way:
            params["maxTransfers"] = ground_choice.max_transfers(to_airport)
        found = await self.get("plan", params)
        first_leave, last_arrive = day_bounds(to_airport)
        connections = []
        for itinerary in found.get("itineraries") or []:
            legs = [leg for leg in itinerary["legs"] if leg["mode"] != "WALK"]
            if not legs or any(leg["mode"] == "AIRPLANE" for leg in legs):
                continue
            # Vehicle time on the city side. On the airport side the door of the terminal: the walk the planner
            # found when the terminal is known, a fixed time otherwise.
            first = itinerary["startTime"] if (terminal_known or from_stop) and not to_airport else legs[0]["startTime"]
            last = itinerary["endTime"] if (terminal_known or to_stop) and to_airport else legs[-1]["endTime"]
            leave, arrive = minutes_since(day, first, zones[0]), minutes_since(day, last, zones[1])
            if from_stop:
                leave -= stop_walk
            elif to_stop:
                arrive += stop_walk
            elif not terminal_known:
                leave, arrive = (leave, arrive + AIRFIELD_WALK_MINUTES) if to_airport else (leave - AIRFIELD_WALK_MINUTES, arrive)
            if not first_leave <= leave < arrive < last_arrive or leave >= 24 * 60:
                continue
            connections.append({
                "leave": leave, "arrive": arrive, "steps": [step_of(leg, zones) for leg in legs],
                # Operator links come from the timetable feed the operator published
                "urls": list(dict.fromkeys(url for url in (web.secure_url(leg.get("agencyUrl")) for leg in legs) if url)),
            })
        # The answer is checked, not trusted: a connection the traveller does not accept is dropped here
        connections = [connection for connection in connections if any_way or ground_choice.accepts(connection, to_airport)]
        if not connections and not modes and city_walk < settings.wide_city_walk_minutes * 60:
            return await self.plan(start, end, day, minutes, arrive_by=arrive_by, zones=zones, to_airport=to_airport,
                                   terminal_known=terminal_known, modes=modes, stop_walk=stop_walk,
                                   city_walk=settings.wide_city_walk_minutes * 60, any_way=any_way)
        return connections

    async def is_covered(self, start: tuple | str, end: tuple | str, day: str, zones: tuple, to_airport: bool, terminal_known: bool) -> bool:
        """Whether any timetable covers a route: is there a connection in the middle of the day, by any vehicle?"""
        # Several flights ask about the same route at once: they share the answer
        return bool(await self.shared(self.probes, (start, end, day), lambda: self.plan(
            start, end, day, 11 * 60, arrive_by=False, zones=zones, to_airport=to_airport, terminal_known=terminal_known, any_way=True)))

    async def covers(self, city: tuple, airport: tuple, stops: list[tuple], day: str, zones: tuple, to_airport: bool, terminal_known: bool) -> bool:
        """Whether any timetable covers the way between the city and an airport at all: to or from the airport's first
        stop, or its point on the map (is_covered). A stop the timetables refuse says nothing: the point on the map
        still does."""
        for end, _ in [*stops[:1], (airport, 0)]:
            start, finish = (city, end) if to_airport else (end, city)
            try:
                if await self.is_covered(start, finish, day, zones, to_airport, terminal_known):
                    return True
            except httpx.HTTPStatusError:
                if not isinstance(end, str):
                    raise   # the point on the map itself was refused: not a stop that is gone, a failure of the service
        return False

    def answer(self, options: list[Connection], service: str, day: str) -> Answer:
        """What was found for one side of a flight. day is the day the traveller is there, service the day read.
        A connection with a stretch estimated from the distance (a mixed leg) is not verified as a whole (C)."""
        grade = "A" if service == day else "B"
        unique = {(option["leave"], option["arrive"], len(option["steps"])): option for option in options}
        options = [{**option, "grade": "C" if option.get("estimated_km") else grade, "source": "timetable", "service_day": service,
                    "price_eur": None} for option in unique.values()]
        return {"covered": True, "service_day": service, "grade": max(option["grade"] for option in options) if options else grade,
                "options": options}

    async def hubs(self, point: tuple) -> list[dict[str, Any]]:
        """The stations near a place where a published connection can end or start: railway stations first, then
        coach stops, then bus stops, nearest first, within max_estimate_km. As stops of the timetables, each with
        its distance from the place under "km". Railway stations are looked for further before a stop is accepted:
        the stops nearest to a place no timetable reaches are often ones nothing reaches either."""
        # Several flights ask about the same place at once: they share the answer
        return await self.shared(self.hub_cache, (round(point[0], 3), round(point[1], 3)), lambda: self.find_hubs(point))

    async def find_hubs(self, point: tuple) -> list[dict[str, Any]]:
        """The stations of hubs, asked of the timetables."""
        limit = current.SETTINGS.max_estimate_km
        found: list[dict[str, Any]] = []
        for km in [radius for radius in HUB_RADII_KM if radius < limit] + [limit]:
            stops = await self.get("map/stops", box_around(point, km))
            ground = []
            for stop in stops:
                stop = {**stop, "km": distance_km(point[0], point[1], stop["lat"], stop["lon"])}
                if stop["km"] <= km and set(stop.get("modes") or []) - {"AIRPLANE"}:
                    ground.append(stop)
            rail = [stop for stop in ground if any(is_rail(mode) for mode in stop["modes"])]
            if rail or km == limit:
                # The nearest railway stations, and the nearest coach or bus stops: which gives the shorter trip is
                # only known once both are asked (mixed_to_airport and mixed_from_airport keep the fastest)
                road = sorted((stop for stop in ground if stop not in rail), key=lambda stop: ("COACH" not in stop["modes"], stop["km"]))
                for group, room in ((sorted(rail, key=lambda stop: stop["km"]), HUBS_TRIED - 1), (road, HUBS_TRIED)):
                    for stop in group:
                        if len(found) >= room:
                            break
                        if all(distance_km(stop["lat"], stop["lon"], other["lat"], other["lon"]) > SAME_HUB_KM for other in found):
                            found.append(stop)
                break
        return await asyncio.to_thread(by_road, point, found)

    async def mixed_to_airport(self, city: tuple, airport: tuple, flight: dict[str, Any], zones: tuple, known: bool,
                               stops: list[tuple], service: str, at_terminal: int) -> list[Connection]:
        """Mixed connections to the airport: the stretch from the city to the nearest station when the city is the
        place no timetable reaches, or from the station to the terminal when the airport is. One request per station
        (HUBS_TRIED on each side at most), and only for a route no timetable covers."""
        if not ground_choice.road_allowed(True):
            return []   # the estimated stretch is by road: not a trip by train
        origin = current.TRIP.origin.local_name if current.TRIP else tr("origin")
        code = flight["departure_airport"]
        target, walk = (stops or [(airport, 0)])[0]
        options: list[list[dict[str, Any]]] = []   # the connections through each station
        for hub in await self.hubs(city):   # the city first: its own buses are the ones most often not published
            at = (hub["lat"], hub["lon"])
            found = await unless_refused(self.plan(at, target, service, at_terminal, arrive_by=True, zones=zones, to_airport=True,
                                                   terminal_known=known, modes=GROUND_MODES, stop_walk=walk))
            if found:
                options.append(with_stretch(found, estimated_stretch(origin, hub["name"], hub, city, at), True, origin, hub["name"]))
        if options:
            return fastest(options, True)
        for hub in await self.hubs(airport):
            at = (hub["lat"], hub["lon"])
            stretch = estimated_stretch(hub["name"], code, hub, at, airport)
            found = await unless_refused(self.plan(city, at, service, at_terminal - stretch["minutes"], arrive_by=True, zones=zones,
                                                   to_airport=True, terminal_known=True, modes=GROUND_MODES))
            if found:
                options.append(with_stretch(found, stretch, False, hub["name"], code))
        return fastest(options, True)

    async def mixed_from_airport(self, airport: tuple, city: tuple, flight: dict[str, Any], zones: tuple, known: bool,
                                 stops: list[tuple], service: str, ready: int) -> list[Connection]:
        """Mixed connections from the airport: to the station nearest to the city when the city is the place no
        timetable reaches, or from the terminal to the nearest station when the airport is."""
        if not ground_choice.road_allowed(False):
            return []   # the estimated stretch is by road: not a trip by train
        destination = current.TRIP.destination.local_name if current.TRIP else tr("destination")
        code = flight["arrival_airport"]
        start, walk = (stops or [(airport, 0)])[0]
        options: list[list[dict[str, Any]]] = []   # the connections through each station
        for hub in await self.hubs(city):
            at = (hub["lat"], hub["lon"])
            found = await unless_refused(self.onward(start, at, service, ready, zones=zones, terminal_known=known, modes=GROUND_MODES,
                                                     stop_walk=walk))
            if found:
                options.append(with_stretch(found, estimated_stretch(hub["name"], destination, hub, at, city), False, hub["name"], destination))
        if options:
            return fastest(options, False)
        for hub in await self.hubs(airport):
            at = (hub["lat"], hub["lon"])
            stretch = estimated_stretch(code, hub["name"], hub, airport, at)
            found = await unless_refused(self.onward(at, city, service, ready + stretch["minutes"], zones=zones, terminal_known=True,
                                                     modes=GROUND_MODES))
            if found:
                # The traveller leaves the terminal when the estimated stretch has to start, to be at the station in time
                options.append(with_stretch(found, stretch, True, code, hub["name"]))
        return fastest(options, False)

    async def to_airport(self, start: tuple, airport: tuple, flight: dict[str, Any], zones: tuple, known: bool,
                         stops: list[tuple] = ()) -> Answer:
        """Connections from the origin city that reach the terminal in time for a flight.
        Asked for up to the airport's stops (see airport_stops), and up to the point on the map when they give nothing."""
        service = reference_day(flight["date"])
        at_terminal = to_minutes(flight["departure_time"]) - current.SETTINGS.min_airport_minutes
        # Free trains: look for trains first, a paid coach is only worth it when no train fits
        modes = NO_COACH_MODES if current.SETTINGS.free_trains_at_origin else ""
        earliest, latest = to_minutes(current.SETTINGS.earliest_leave), to_minutes(current.SETTINGS.latest_leave)
        fits = lambda c: earliest <= c["leave"] <= latest and c["arrive"] <= at_terminal

        async def reaching(target: tuple | str, walk: int) -> list[Connection]:
            """The connections that reach one point or one stop of the airport."""
            # The connections that arrive last before the limit. If none of them leaves within the times the traveller
            # accepts, also the ones that leave first after the earliest of those times and the ones that leave in the
            # last LAST_RIDES_MINUTES before the latest, to be sure nothing suitable exists.
            ask = lambda minutes, arrive_by, modes: self.plan(start, target, service, minutes, arrive_by=arrive_by, zones=zones, to_airport=True,
                                                              terminal_known=known, modes=modes, stop_walk=walk)
            found = list(await ask(at_terminal, True, modes))
            if modes and not any(map(fits, found)):
                found += await ask(at_terminal, True, "")
            if not any(map(fits, found)):
                key = (start, target, service)   # the same question for every flight of that airport and day: asked once
                found += await self.shared(self.first_rides, key, lambda: ask(earliest, False, ""))
                if latest < at_terminal:
                    # A flight late in the day: the connections that arrive last all leave after the traveller's latest
                    # time, and the first ones of the day would leave the traveller waiting for hours at the airport
                    found += await self.shared(self.last_rides, key, lambda: ask(max(earliest, latest - LAST_RIDES_MINUTES), False, ""))
            return found

        found = await first_found([*stops, (airport, 0)], reaching)
        if not found and not await self.covers(start, airport, stops, service, zones, True, known):
            found = await self.mixed_to_airport(start, airport, flight, zones, known, stops, service, at_terminal)
            if not found:
                return dict(NOT_COVERED)
        if not found:
            # The timetables cover the route, but not the way the traveller chose to make it
            return {**self.answer([], service, flight["date"]), "choice_excluded": ground_choice.restricted(True)}
        # Kept up to the shortest time at the airport ever offered: the ones that arrive after the time asked for
        # only serve the closest alternatives, when nothing respects every requirement
        last_call = to_minutes(flight["departure_time"]) - current.SETTINGS.airport_floor_minutes
        return self.answer([c for c in found if c["arrive"] <= max(at_terminal, last_call)], service, flight["date"])

    async def onward(self, airport: tuple | str, end: tuple, service: str, ready: int, *, zones: tuple, terminal_known: bool,
                     modes: str = "", stop_walk: int = 0) -> list[Connection]:
        """The connections leaving an airport from a time on. One request serves every flight that lands while
        its answer still applies: several flights land at the same airport within a couple of hours."""
        key = (airport, end, service, modes)
        async with self.locks.setdefault(key, asyncio.Lock()):
            for asked_from, good_until, found in self.windows.setdefault(key, []):
                if asked_from <= ready <= good_until:
                    return [c for c in found if c["leave"] >= ready]
            found = await self.plan(airport, end, service, ready, arrive_by=False, zones=zones, to_airport=False,
                                    terminal_known=terminal_known, modes=modes, stop_walk=stop_walk)
            leaves = sorted(c["leave"] for c in found)
            # A full answer stops somewhere, and later connections exist beyond it: it is only reused up to its
            # second-to-last connection. A short answer is everything left that day, so it serves any later time.
            good_until = leaves[-2] if len(found) >= PAGE_SIZE else 24 * 60
            self.windows[key].append((ready, good_until, found))
            return [c for c in found if c["leave"] >= ready]

    async def leaving(self, start: tuple | str, walk: int, end: tuple, service: str, ready: int, zones: tuple,
                      known: bool) -> list[Connection]:
        """The connections that leave one point or one stop of an airport from a time on."""
        ask = lambda modes: self.onward(start, end, service, ready, zones=zones, terminal_known=known, modes=modes, stop_walk=walk)
        # Free trains: look for trains first, a paid coach is only worth it when no train is left that day
        if current.SETTINGS.free_trains_at_destination:
            return await ask(NO_COACH_MODES) or await ask("")
        # An answer is the first connections to leave, and trains leave so often that they fill it: the coaches,
        # a few a day, are asked for on their own. Chains of local buses are left out, a coach is what is looked for.
        # Not by train only: there is no coach to look for. Not by bus only: then the first answer has nothing but buses.
        if ground_choice.side(False)[0] != "any":
            return await ask("")
        found, by_road = await asyncio.gather(ask(""), ask(ROAD_MODES))
        return found + [c for c in by_road if any(step["coach"] for step in c["steps"])]

    async def from_airport(self, airport: tuple, end: tuple, flight: dict[str, Any], zones: tuple, known: bool,
                           stops: list[tuple] = ()) -> Answer:
        """Connections from the arrival airport to the destination that the traveller can catch after landing.
        Asked for from the airport's stops (see airport_stops), and from the point on the map when they give nothing."""
        service = reference_day(landing_day(flight))
        ready = to_minutes(flight["arrival_time"]) + current.SETTINGS.min_landing_minutes   # earliest time walking out of the terminal
        found = await first_found([*stops, (airport, 0)], lambda start, walk: self.leaving(start, walk, end, service, ready, zones, known))
        if not found and not await self.covers(end, airport, stops, service, zones, False, known):
            found = await self.mixed_from_airport(airport, end, flight, zones, known, stops, service, ready)
            if not found:
                return dict(NOT_COVERED)
        if not found:
            # The timetables cover the route, but not the way the traveller chose to make it
            return {**self.answer([], service, landing_day(flight)), "choice_excluded": ground_choice.restricted(False)}
        return self.answer(found, service, landing_day(flight))

    async def close(self) -> None:
        await self.http.aclose()
