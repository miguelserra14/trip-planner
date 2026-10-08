"""Places and airports. Code only, no model. The cities are located with OpenStreetMap, so they can be written in
any language, and the airports come from the OurAirports database, chosen by distance."""
import csv
import math
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from trip_planner.config import ROOT, TripSettings, USER_AGENT
from trip_planner.texts import tr
from trip_planner.utils import plain, say

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"   # OurAirports, public domain
AIRPORTS_FILE = ROOT / "resources" / "airports.csv"     # downloaded once, then reused
AIRPORT_TYPES = {"large_airport", "medium_airport"}     # with scheduled service only, see load_airports
CITY_AIRPORT_RADIUS_KM = 45                             # "Londres" means every airport this close to London

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

def geocode(name: str) -> Place:
    """Locate a city written in any language, with OpenStreetMap's Nominatim."""
    key = plain(name)
    if key not in PLACES:
        response = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": name, "format": "jsonv2", "limit": 1, "namedetails": 1, "addressdetails": 1, "accept-language": "en"},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            raise ValueError(tr("place_not_found", name=name))
        found = results[0]
        names = found.get("namedetails") or {}
        PLACES[key] = Place(
            written=name,
            local_name=names.get("name") or found["name"],
            english_name=names.get("name:en") or found["name"],
            country=(found.get("address") or {}).get("country", ""),
            latitude=float(found["lat"]),
            longitude=float(found["lon"]),
        )
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
                        "name": row["name"],
                        "city": row["municipality"],
                        "country": row["iso_country"],
                        "large": row["type"] == "large_airport",
                        "latitude": float(row["latitude_deg"]),
                        "longitude": float(row["longitude_deg"]),
                        "search_text": plain(f"{row['name']} {row['municipality']} {row['keywords']}"),
                    }
    return AIRPORTS

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

def airports_near(place: Place, radius_km: float) -> list[dict[str, Any]]:
    """The airports within a radius of a city, closest first."""
    near = [airport_from(place, code) for code in load_airports()]
    return sorted((airport for airport in near if airport["km"] <= radius_km), key=lambda airport: airport["km"])

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

    primary = resolve(settings.primary_airports, origin) if settings.primary_airports else airports_near(origin, settings.primary_radius_km)
    fallback = resolve(settings.fallback_airports, origin) if settings.fallback_airports else airports_near(origin, settings.fallback_radius_km)
    arrival = resolve(settings.arrival_airports, destination) if settings.arrival_airports else airports_near(destination, settings.arrival_radius_km)

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

    primary = tidy(primary, set(), settings.max_departure_airports)
    fallback = tidy(fallback, {a["code"] for a in primary}, settings.max_departure_airports - len(primary))
    arrival = tidy(arrival, {a["code"] for a in primary + fallback}, settings.max_arrival_airports)
    if not primary and fallback:
        primary, fallback = fallback[:1], fallback[1:]   # the closest airport is the reference for the others
    if not primary:
        raise ValueError(tr("no_departure_airports", km=settings.fallback_radius_km, city=settings.origin))
    if not arrival:
        raise ValueError(tr("no_arrival_airports", km=settings.arrival_radius_km, city=settings.destination))
    return TripPlaces(origin, destination, primary, fallback, arrival, dropped=sorted(excluded), unknown=unknown)

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
