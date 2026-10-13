"""Places and airports. Code only, no model. The cities are located with OpenStreetMap, so they can be written in
any language, and the airports come from the OurAirports database, chosen by distance: the straight line first,
then the hours by road, which leave out the airports across a sea."""
import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from trip_planner import current
from trip_planner.config import ROOT, TripSettings, USER_AGENT
from trip_planner.texts import tr
from trip_planner.utils import as_duration, plain, say

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"   # OurAirports, public domain
AIRPORTS_FILE = ROOT / "resources" / "airports.csv"     # downloaded once, then reused
AIRPORT_TYPES = {"large_airport", "medium_airport"}     # with scheduled service only, see load_airports
MIN_TRIP_KM = 80                                        # cities closer than this are not a trip by plane
CITY_AIRPORT_RADIUS_KM = 45                             # "Londres" means every airport this close to London
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"
ROUTES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"   # OpenFlights: airline routes up to 2014
AIRFIELDS_FILE = ROOT / "resources" / "airfields.json"  # worked out once from the two files above, then reused
AIRLINER_RUNWAY_FT = 5900                               # about 1800 m: below this, no ordinary airliner
ROADS_URL = "https://router.project-osrm.org/table/v1/driving/"   # OSRM's public server: road routes on OpenStreetMap, ferries included

@dataclass
class Place:
    """A city as the traveller wrote it and as the rest of the world writes it."""
    written: str          # what the traveller typed, kept for the report
    local_name: str       # name in the local language, the one operators and timetables use
    english_name: str
    country: str
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        """The name given to the agents, for example 'Antwerpen / Antwerp, Belgium'."""
        names = list(dict.fromkeys(name for name in (self.local_name, self.english_name) if name))
        return f"{' / '.join(names)}, {self.country}"

PLACES: dict[str, Place] = {}
SETTLEMENTS = {"city", "town", "village", "municipality", "hamlet", "suburb", "borough"}
SAME_NAME_IMPORTANCE = 0.15   # a city this close in importance to the region or country of its name is what is meant
MUNICIPALITY_KM = 30          # the town a municipality is named after is this close to the middle of its territory

def settlement(results: list[dict[str, Any]]) -> dict[str, Any]:
    """The place a traveller means by a name. The first result may be the province or the country of that name
    ("Pavia", "Luxembourg"), whose middle is kilometres from any station: then the city of the same name."""
    first = results[0]
    close = lambda result: result.get("importance", 0) >= first.get("importance", 0) - SAME_NAME_IMPORTANCE
    if first.get("addresstype") == "municipality":
        # A municipality is located in the middle of its territory, which in the mountains is kilometres from the
        # town it is named after ("Bovec": 5 km, on a slope with no stop). The town itself, when it is also found.
        inside = lambda result: distance_km(float(first["lat"]), float(first["lon"]), float(result["lat"]), float(result["lon"])) <= MUNICIPALITY_KM
        return next((result for result in results[1:] if result.get("addresstype") in SETTLEMENTS - {"municipality"}
                     and close(result) and inside(result)), first)
    if first.get("addresstype") in SETTLEMENTS:
        return first
    return next((result for result in results[1:] if result.get("addresstype") in SETTLEMENTS and close(result)), first)

def search_places(name: str) -> list[dict[str, Any]]:
    """What OpenStreetMap's Nominatim finds for a name, most important first."""
    response = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": name, "format": "jsonv2", "limit": 5, "namedetails": 1, "addressdetails": 1, "accept-language": "en"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()

def as_place(name: str, results: list[dict[str, Any]]) -> Place:
    """The place meant by a name, among what Nominatim found for it. Raises ValueError if it found nothing."""
    if not results:
        raise ValueError(tr("place_not_found", name=name))
    found = settlement(results)
    names = found.get("namedetails") or {}
    return Place(
        written=name,
        local_name=names.get("name") or found["name"],
        english_name=names.get("name:en") or found["name"],
        country=(found.get("address") or {}).get("country", ""),
        latitude=float(found["lat"]),
        longitude=float(found["lon"]),
    )

def geocode(name: str) -> Place:
    """Locate a city written in any language, with OpenStreetMap's Nominatim."""
    key = plain(name)
    if key not in PLACES:
        PLACES[key] = as_place(name, search_places(name))
    return PLACES[key]

AIRPORTS: dict[str, dict[str, Any]] = {}

def load_airports() -> dict[str, dict[str, Any]]:
    """Every airport with scheduled passenger flights, by IATA code."""
    if not AIRPORTS:
        if not AIRPORTS_FILE.exists():
            AIRPORTS_FILE.parent.mkdir(exist_ok=True)
            response = httpx.get(AIRPORTS_URL, headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
            response.raise_for_status()
            AIRPORTS_FILE.write_bytes(response.content)
        with AIRPORTS_FILE.open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                if row["iata_code"] and row["type"] in AIRPORT_TYPES and row["scheduled_service"] == "yes":
                    AIRPORTS[row["iata_code"]] = {
                        "code": row["iata_code"],
                        "ident": row["ident"],
                        "name": row["name"],
                        "city": row["municipality"],
                        "country": row["iso_country"],
                        "large": row["type"] == "large_airport",
                        "latitude": float(row["latitude_deg"]),
                        "longitude": float(row["longitude_deg"]),
                        "search_text": plain(f"{row['name']} {row['municipality']} {row['keywords']}"),
                    }
    return AIRPORTS

AIRFIELDS: set | None = None

def airfields() -> set:
    """Airports the database lists with scheduled flights but that no airline sells seats from, such as Viseu,
    Bragança or Cascais: airfields with an air taxi. An airport is one of them when it is not a large airport, no
    airline route ever used it and its runway is too short for an airliner. One sign is not enough: London City has
    a short runway, and an airport opened after the route data was collected has no routes."""
    global AIRFIELDS
    if AIRFIELDS is None and AIRFIELDS_FILE.exists():
        AIRFIELDS = set(json.loads(AIRFIELDS_FILE.read_text(encoding="utf-8")))
    if AIRFIELDS is None:
        try:
            fetch = lambda url: httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True).raise_for_status().text
            longest: dict[str, float] = {}
            for row in csv.DictReader(io.StringIO(fetch(RUNWAYS_URL))):
                if row["length_ft"]:
                    longest[row["airport_ident"]] = max(longest.get(row["airport_ident"], 0), float(row["length_ft"]))
            flown = {code for row in csv.reader(io.StringIO(fetch(ROUTES_URL))) if len(row) > 4 for code in (row[2], row[4])}
            AIRFIELDS = {code for code, airport in load_airports().items()
                         if not airport["large"] and code not in flown and airport["ident"] in longest
                         and longest[airport["ident"]] < AIRLINER_RUNWAY_FT}
            AIRFIELDS_FILE.write_text(json.dumps(sorted(AIRFIELDS)), encoding="utf-8")
        except Exception as e:
            say(f"[airports] {type(e).__name__}: could not tell the airfields from the airports, all of them are listed")
            return set()   # not kept: asked again on the next trip
    return AIRFIELDS

def distance_km(latitude_1: float, longitude_1: float, latitude_2: float, longitude_2: float) -> float:
    """Straight-line distance between two points."""
    a, b = math.radians(latitude_1), math.radians(latitude_2)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(longitude_2 - longitude_1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))

def airport_from(place: Place, code: str) -> dict[str, Any]:
    """An airport with its distance from a city."""
    airport = load_airports()[code]
    km = distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"])
    kept = ("code", "name", "city", "country", "large", "latitude", "longitude")
    return {**{key: airport[key] for key in kept}, "km": round(km)}

def airports_near(place: Place, radius_km: float, airlines_only: bool = False) -> list[dict[str, Any]]:
    """The airports within a radius of a city, closest first. airlines_only leaves out the airfields."""
    skipped = airfields() if airlines_only else set()
    near = [airport_from(place, code) for code in load_airports() if code not in skipped]
    return sorted((airport for airport in near if airport["km"] <= radius_km), key=lambda airport: airport["km"])

def read_road_hours(airports: list[dict[str, Any]], table: dict[str, Any]) -> dict[str, float]:
    """The hours by road to each airport, from OSRM's answer. Infinite when no road or ferry reaches it.
    An airport whose road is shorter than the straight line is left out of the answer: the service placed it
    somewhere else, so nothing is known about it."""
    hours = {}
    for airport, seconds, metres in zip(airports, table["durations"][0][1:], table["distances"][0][1:]):
        if seconds is None:
            hours[airport["code"]] = math.inf
        elif metres >= airport["km"] * 900:   # 10% under the straight line: the km are rounded and the road starts near the point
            hours[airport["code"]] = seconds / 3600
    return hours

def road_hours(place: Place, airports: list[dict[str, Any]]) -> dict[str, float]:
    """Hours by road, ferries included, from a city to each of these airports: one request to OSRM for them all.
    Empty when the service does not answer: the airports are then chosen by the straight line alone."""
    if not airports:
        return {}
    points = [(place.latitude, place.longitude)] + [(airport["latitude"], airport["longitude"]) for airport in airports]
    try:
        response = httpx.get(ROADS_URL + ";".join(f"{longitude},{latitude}" for latitude, longitude in points),
                             params={"sources": 0, "annotations": "duration,distance"}, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        return read_road_hours(airports, response.json())
    except Exception as e:
        say(f"[airports] {type(e).__name__}: could not check the airports by road, they are chosen by the straight line alone")
        return {}

def find_airports(entry: str, place: Place) -> list[str]:
    """Turn what the traveller wrote into airport codes. entry is a code ("BRU"), an airport name ("Charleroi")
    or a city ("Paris"). Names are only looked for within 1500 km of place, so that "Paris" is not Paris, Texas."""
    airports = load_airports()
    if re.fullmatch(r"[A-Za-z]{3}", entry) and entry.upper() in airports:
        return [entry.upper()]
    wanted = plain(entry)
    nearby = airports_near(place, 1500)
    # The airports of that town first ("Porto" is Porto, not Porto Santo), then a whole word of the airport's name
    found = [a["code"] for a in nearby if plain(a["city"]) == wanted or plain(a["city"]).startswith(wanted + " ")]
    word = re.compile("(?<![a-z0-9])" + re.escape(wanted) + "(?![a-z0-9])")
    if not found:
        found = [a["code"] for a in nearby if word.search(plain(a["name"]))]
    if found:
        return found   # the entry names a town with an airport or an airport: nothing else is meant
    # Otherwise it is a city, possibly in another language ("Londres"): every airport around it
    found = [a["code"] for a in nearby if word.search(airports[a["code"]]["search_text"])]
    try:
        city = geocode(entry)
        found += [a["code"] for a in airports_near(city, CITY_AIRPORT_RADIUS_KM)]
    except Exception:
        pass
    return list(dict.fromkeys(found))

@dataclass
class TripPlaces:
    """Where the trip happens: the two cities and the airports that will be searched."""
    origin: Place
    destination: Place
    primary: list[dict[str, Any]]      # departure airports close to the origin
    fallback: list[dict[str, Any]]     # departure airports further away
    arrival: list[dict[str, Any]]      # arrival airports
    dropped: list[str] = field(default_factory=list)    # airports removed by the settings or by the traveller, for the report
    unknown: list[str] = field(default_factory=list)    # entries of the settings that matched no airport
    too_far: dict[str, float] = field(default_factory=dict)   # airports left out for being close on the map but far by road: their hours

    @property
    def departure(self) -> list[dict[str, Any]]:
        return self.primary + self.fallback

    def airport(self, code: str) -> dict[str, Any] | None:
        return next((airport for airport in self.departure + self.arrival if airport["code"] == code), None)

    def km(self, code: str) -> int | None:
        """Straight-line distance between an airport and its city (origin for departures, destination for arrivals)."""
        airport = self.airport(code)
        return airport["km"] if airport else None

def describe_airport(airport: dict[str, Any]) -> str:
    return f"{airport['code']} ({airport['name']}, {airport['city']}, {airport['country']}, {airport['km']} km)"

def codes(airports: list[dict[str, Any]]) -> str:
    return ",".join(airport["code"] for airport in airports)

def choose_airports(settings: TripSettings) -> TripPlaces:
    """Locate the two cities and choose the airports: by distance, then adjusted by the settings."""
    origin, destination = geocode(settings.origin), geocode(settings.destination)
    unknown: list[str] = []

    def resolve(entries: list[str], place: Place) -> list[dict[str, Any]]:
        found_codes: list[str] = []
        for entry in entries:
            found = find_airports(entry, place)
            if not found:
                unknown.append(entry)
            found_codes += found
        return sorted((airport_from(place, code) for code in dict.fromkeys(found_codes)), key=lambda airport: airport["km"])

    apart = round(distance_km(origin.latitude, origin.longitude, destination.latitude, destination.longitude))
    if apart < MIN_TRIP_KM:
        raise ValueError(tr("places_too_close", origin=origin.label, destination=destination.label, km=apart))

    # Each side on the scale chosen for it: how far to look and how many airports to keep
    departure_km, departure_limit, departure_setting = settings.scale(settings.departure_scale)
    arrival_km, arrival_limit, arrival_setting = settings.scale(settings.arrival_scale)
    # Close on the map is not close by land: Dubrovnik is 198 km from Bari and 18 hours away, across the Adriatic.
    too_far: dict[str, float] = {}

    def by_road(place: Place, airports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The airports that are no more than max_road_hours from the city. The others are noted in too_far."""
        hours = road_hours(place, airports)
        far = {a["code"]: hours[a["code"]] for a in airports if hours.get(a["code"], 0) > settings.max_road_hours}
        too_far.update(far)
        return [a for a in airports if a["code"] not in far]

    # The automatic choice leaves out the airfields and the airports too far by road. One the traveller names in
    # the settings is still used.
    automatic = not (settings.primary_airports and settings.fallback_airports)
    around_origin = by_road(origin, airports_near(origin, departure_km, True)) if automatic else []
    primary = resolve(settings.primary_airports, origin) if settings.primary_airports else [a for a in around_origin if a["km"] <= settings.primary_radius_km]
    fallback = resolve(settings.fallback_airports, origin) if settings.fallback_airports else around_origin
    arrival = resolve(settings.arrival_airports, destination) if settings.arrival_airports else by_road(destination, airports_near(destination, arrival_km, True))

    # Airports to add go to the side they are closer to, and are never cut by the limits
    wanted: set = set()
    for entry in settings.include_airports:
        found = find_airports(entry, origin) or find_airports(entry, destination)
        if not found:
            unknown.append(entry)
        wanted.update(found)
        for code in found:
            from_origin, from_destination = airport_from(origin, code), airport_from(destination, code)
            if from_origin["km"] <= from_destination["km"]:
                (primary if from_origin["km"] <= settings.primary_radius_km else fallback).append(from_origin)
            else:
                arrival.append(from_destination)

    # Airports never to use
    excluded = set()
    for entry in settings.exclude_airports:
        found = find_airports(entry, origin) + find_airports(entry, destination)
        if not found:
            unknown.append(entry)
        excluded.update(found)

    def tidy(airports: list[dict[str, Any]], taken: set, limit: int) -> list[dict[str, Any]]:
        """Drop duplicates and excluded airports, keep the most useful ones up to the limit, closest first."""
        unique = {a["code"]: a for a in airports if a["code"] not in taken and a["code"] not in excluded}
        # Airports the traveller asked for come first, then the large ones (small ones rarely have the flight), then the closest
        ranked = sorted(unique.values(), key=lambda a: (a["code"] not in wanted, not a["large"], a["km"]))
        return sorted(ranked[:max(limit, 0)], key=lambda a: a["km"])

    primary = tidy(primary, set(), departure_limit)
    fallback = tidy(fallback, {a["code"] for a in primary}, departure_limit - len(primary))
    arrival = tidy(arrival, {a["code"] for a in primary + fallback}, arrival_limit)
    if not primary and fallback:
        primary, fallback = fallback[:1], fallback[1:]   # the closest airport is the reference for the others
    used = {a["code"] for a in primary + fallback + arrival}   # an airport too far by road that the traveller added is used
    too_far = {code: hours for code, hours in too_far.items() if code not in used}
    reason = "no_airports_by_road" if too_far else "no_airports_near"
    if not primary:
        raise ValueError(tr(reason, km=departure_km, city=settings.origin, setting=departure_setting, hours=f"{settings.max_road_hours:g}"))
    if not arrival:
        raise ValueError(tr(reason, km=arrival_km, city=settings.destination, setting=arrival_setting, hours=f"{settings.max_road_hours:g}"))
    return TripPlaces(origin, destination, primary, fallback, arrival, dropped=sorted(excluded), unknown=unknown, too_far=too_far)

def remove_airports(places: TripPlaces, removed: list[str]) -> TripPlaces:
    """Drop airports from the trip. Raises ValueError if no departure or no arrival airport is left."""
    dropped = set(places.dropped) | set(removed)
    keep = lambda airports: [airport for airport in airports if airport["code"] not in dropped]
    primary, fallback, arrival = keep(places.primary), keep(places.fallback), keep(places.arrival)
    if not primary and fallback:
        primary, fallback = fallback[:1], fallback[1:]   # the closest airport left is the reference for the others
    if not primary or not arrival:
        raise ValueError(tr("all_airports_removed"))
    places.primary, places.fallback, places.arrival, places.dropped = primary, fallback, arrival, sorted(dropped)
    return places

def airport_groups(places: TripPlaces) -> list[tuple]:
    """The three groups of airports with their title in the traveller's language."""
    return [(tr("group_primary"), places.primary), (tr("group_fallback"), places.fallback), (tr("group_arrival"), places.arrival)]

def too_far_notice(places: TripPlaces) -> str:
    """The airports left out for being too far by road, in words. Empty when there are none."""
    if not places.too_far:
        return ""
    airports = ", ".join(code if hours == math.inf else f"{code} ({as_duration(round(hours * 60))})" for code, hours in places.too_far.items())
    return tr("too_far_airports", hours=f"{current.SETTINGS.max_road_hours:g}", airports=airports)

def show_airports(places: TripPlaces) -> None:
    say(f"{tr('origin')}: {places.origin.written} -> {places.origin.label}")
    say(f"{tr('destination')}: {places.destination.written} -> {places.destination.label}")
    for title, airports in airport_groups(places):
        say(f"\n{title}:")
        for airport in airports:
            say(f"  {airport['code']}  {airport['km']:>4} km  {airport['name']} ({airport['city']}, {airport['country']})")
        if not airports:
            say(f"  ({tr('none')})")
    if places.unknown:
        say("\n" + tr("unknown_airports", entries=", ".join(places.unknown)))
    if places.too_far:
        say("\n" + too_far_notice(places))

def ask_airports(places: TripPlaces) -> TripPlaces:
    """Text version of the airport choice, for when checkboxes are not available: type what to drop."""
    show_airports(places)
    try:
        answer = input("\n" + tr("ask_airports_prompt") + " ")
    except Exception:
        say("\n" + tr("ask_airports_unavailable"))
        return places
    chosen: set = set()
    listed = places.departure + places.arrival
    for entry in (part.strip() for part in answer.split(",")):
        if not entry:
            continue
        wanted = plain(entry)
        matches = {a["code"] for a in listed if wanted == a["code"].lower() or wanted in plain(f"{a['name']} {a['city']}")}
        if not matches:
            # A city in another language, such as "Londres": drop the listed airports of that city
            matches = set(find_airports(entry, places.origin)) & {a["code"] for a in listed}
        if not matches:
            say("  " + tr("ask_airports_no_match", entry=entry))
        chosen.update(matches)
    return remove_airports(places, sorted(chosen))
