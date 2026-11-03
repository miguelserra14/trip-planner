"""The trains and buses that go straight from the traveller's city to a destination of the map, with no flight.
Their timetables are published (Transitous). Two kinds of operators, "sold" telling them apart:
- the ones code can read the price of for a day (ground_fares.py): FlixBus, and the ones the planner prices
  (fares.py: ÖBB, RegioJet, Trenitalia, MÁV, Elron, ALSA, Renfe and Ouigo);
- the ones whose sites refuse programs, forbid them, or ask for a key: BlaBlaCar Bus, Deutsche Bahn, Eurostar,
  SNCF, NS International, SNCB, European Sleeper and CP. Their timetable only.

The way out is direkt.bahn.guru's: the long-distance departures of the stations near the city over a week, then
the stations each of those trains and buses calls at after it. A station near the centre of a city of the map
makes that city reachable; a city no flight goes to is a destination of its own, by train or bus only, when it is
farther than MIN_KM and is the end of the line or a city of MIN_POPULATION on the way (GeoNames), or when it is a
city of BIG_CITY at any distance (Brussels, 41 km from Antwerp). The way back, for a return trip, is one request per
destination and operator: the direct connections of that operator, on the first day to come back on, from the
station reached to the one left.

Here only the timetable is known: grade B, and no price; ground_fares.py adds the prices it can read. One with
no price is shown for the traveller to check on the operator's site, and never taken for the cheapest way. No web
search, no model.
The way out is kept GROUND_DAYS per city; the way back depends on the day, and is asked for again each map."""
import io
import json
import re
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from discover.sources import Days
from trip_planner.sources import fares
from trip_planner.sources.operators.alsa import ALSA_STOP
from trip_planner.sources.timetables.client import TRANSITOUS_API
from trip_planner.sources.web import secure_url
from trip_planner.texts import tr
from trip_planner.trip.ground_choice import is_rail
from trip_planner.trip.places import distance_km, Place
from trip_planner.trip.trip_settings import USER_AGENT
from trip_planner.utils import say as print_line, write_whole

# The operators shown, by the start of their name in Transitous (lower case): the name shown and where to buy
OPERATORS = {
    "blablacar bus": ("BlaBlaCar Bus", "https://www.blablacar.com"),
    "db fernverkehr": ("Deutsche Bahn", "https://int.bahn.de"),
    "eurostar": ("Eurostar", "https://www.eurostar.com"),
    "sncf": ("SNCF", "https://www.sncf-connect.com"),
    "ns international": ("NS International", "https://www.nsinternational.com"),
    "nmbs": ("SNCB", "https://www.belgiantrain.be"),
    "european sleeper": ("European Sleeper", "https://www.europeansleeper.eu"),
    "cp - comboios": ("CP", "https://www.cp.pt"),
    "flixbus": ("FlixBus", "https://global.flixbus.com"),
}
LONG_MODES = ("HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL", "COACH")   # the departures read: no local ones
# The bus stops read. FlixBus's are also in a "gb-flixbus_" feed, under the same ids and with the same buses;
# a "Waypoint" of FlixBus is where its buses pass, not where travellers get on.
COACH_FEEDS = ("eu-blablacar-bus_", "eu-flixbus_", ALSA_STOP)
GROUND = Path(__file__).parent / "cache" / "ground.json"   # the way out found for each city
GROUND_DAYS = 30           # a city's trains and buses are looked for again after this long...
GROUND_VERSION = 4         # ... or when what is read changes: 4, FlixBus and the operators the planner prices, with where (2026-10-24)
STOP_KM = 12               # the stations this close to the traveller's city are its stations
MATCH_KM = 20              # a station this close to the centre of a city of the map reaches that city
# A city no flight of the map goes to is shown when a train or bus reaches it straight, farther than this (Antwerp
# to Rotterdam is 78 km): nearer is a day out, not a trip... unless it has BIG_CITY people: Brussels is 41 km from
# Antwerp. A city within MATCH_KM is the traveller's own. The ones shown are the quickest MAX_ONLY: the map's
# filters thin them out.
MIN_KM = 50
BIG_CITY = 1_000_000
MAX_ONLY = 80
# Which stops are worth a trip: a train or bus calls at towns and halts on its way, and the ones shown are the
# cities of at least MIN_POPULATION, or the end of the line, whatever its size (the operator runs it there). The
# cities are GeoNames' of more than 15000 people (CC BY 4.0), downloaded once: their name, country and population.
CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
CITIES = Path(__file__).parent / "cache" / "cities15000.tsv"
MIN_POPULATION = 100_000
# A numbered district of a city, which GeoNames lists as a city of its own: "Paris 15 Vaugirard", "Lyon 03"
DISTRICT = re.compile(r"^\S.* \d{1,2}( |$)")
CENTRE_KM = 5              # a station this close to a city's centre is that city's, the largest one if several...
CITY_KM = 10               # ... and else the nearest city this close; with none, the station names its place
AIRPORT_STATION_KM = 3     # a station this close to an airport is the way to a flight (Schiphol, CDG 2), no city
# The words a station's name ends with that are not the city's: "Köln Hbf" is Köln
STATION_WORDS = {"hbf", "hl.n.", "centraal", "centrum", "central", "centrale", "bf", "bahnhof", "gare"}
DAYS_AHEAD = 7             # the week of departures read starts this far ahead: today's may be gone already
WEEK_SECONDS = 7 * 24 * 3600
# Departures read per station over that week: FlixBus's Antwerp Rooseveltplaats had 300 in less than a week (2026-10-24)
MAX_DEPARTURES = 1000
MAX_TRIPS = 80             # trains and buses read per city, one of each pattern (operator, where it goes): 2 minutes once a month
MAX_PER_CITY = 4           # operators shown per destination, the quickest first
MAX_BACK_REQUESTS = 60     # ways back asked for per map, once it is open: a minute and a half of timetables
BACK_WINDOW_DAYS = 3       # the way back is looked for over the first days to come back on, at most this many
SPACING_SECONDS = 1.5      # between two requests: Transitous is a free community service
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]   # by datetime.weekday(), whatever the language of the computer

def read() -> dict[str, Any]:
    try:
        found = json.loads(GROUND.read_text(encoding="utf-8"))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}

def write(content: dict[str, Any]) -> None:
    write_whole(GROUND, json.dumps(content, ensure_ascii=False, indent=1))

def place_key(place: Place) -> str:
    return f"{place.latitude:.2f},{place.longitude:.2f}"

def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))

def local(text: str, zone: str) -> datetime:
    return moment(text).astimezone(ZoneInfo(zone or "UTC"))

def operator_of(agency: str, mode: str = "", stop_id: str = "") -> tuple[str, str, str] | None:
    """The operator shown, its site and where code can read its price for a day (ground_fares.py), for a departure
    of Transitous: one of OPERATORS (FlixBus is read everywhere, "EU"), or one the planner's fares.py prices (ÖBB,
    RegioJet, Trenitalia, MÁV, Elron, ALSA, and Renfe and Ouigo through Movelia, shown under their own name), with
    the country it sells in as fares.py says. "" where it is not read. None for the others, and for a published
    tariff or Luxembourg's free transport: city networks, not trips."""
    name = (agency or "").lower()
    known = next((shown for start, shown in OPERATORS.items() if name.startswith(start)), None)
    if known:
        return known[0], known[1], "EU" if known[0] == "FlixBus" else ""
    source = fares.source_of({"agency": agency or "", "rail": is_rail(mode), "coach": mode == "COACH", "mode": mode,
                              "from_id": stop_id, "to_id": stop_id})
    if source is None or source.tariff or source.name.startswith("Luxembourg"):
        return None
    # Movelia sells for others: their own name is shown, "RENFE OPERADORA" as Renfe Operadora
    shown = " ".join(word.title() if word.isupper() and len(word) > 3 else word for word in agency.split())
    return (shown if source.name == "Movelia" else source.name), "", source.country

def mode_of(modes: str) -> str:
    return "bus" if modes == "COACH" else "train"

class Transitous:
    """The requests this module makes, one at a time, with SPACING_SECONDS between them."""

    def __init__(self):
        self.http = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=90)
        self.last = 0.0

    def get(self, path: str, **params) -> Any:
        time.sleep(max(0.0, self.last + SPACING_SECONDS - time.time()))
        try:
            return self.http.get(f"{TRANSITOUS_API}/{path}", params=params).raise_for_status().json()
        finally:
            self.last = time.time()

    def stops(self, place: Place) -> list[dict[str, Any]]:
        """The long-distance stations near a place: rail ones of any feed, bus ones of BlaBlaCar Bus."""
        degrees = STOP_KM / 111
        found = self.get("map/stops", min=f"{place.latitude + degrees},{place.longitude - degrees * 1.6}",
                         max=f"{place.latitude - degrees},{place.longitude + degrees * 1.6}")
        rail = lambda stop: set(stop.get("modes") or []) & (set(LONG_MODES) - {"COACH"})
        return [stop for stop in found if (rail(stop) or stop.get("stopId", "").startswith(COACH_FEEDS))
                and not stop.get("name", "").startswith("Waypoint")
                and distance_km(place.latitude, place.longitude, stop["lat"], stop["lon"]) <= STOP_KM]

    def departures(self, stop_id: str, start: datetime) -> list[dict[str, Any]]:
        return self.get("stoptimes", stopId=stop_id, time=start.strftime("%Y-%m-%dT%H:%M:%SZ"), n=MAX_DEPARTURES,
                        window=WEEK_SECONDS, mode=",".join(LONG_MODES)).get("stopTimes") or []

    def trip(self, trip_id: str) -> dict[str, Any]:
        return self.get("trip", tripId=trip_id)

    def direct(self, start: str, end: str, day: str, modes: str, days: int) -> list[dict[str, Any]]:
        """The connections between two stations over days from a day, by these modes. Transitous refuses to be
        asked for no change at all (it then answers nothing), so the direct ones are kept by the caller."""
        return self.get("plan", fromPlace=start, toPlace=end, time=f"{day}T00:00:00Z", transitModes=modes,
                        searchWindow=days * 24 * 3600, numItineraries=40).get("itineraries") or []

def patterns(departures: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """One train or bus of each pattern, (operator, where it goes): the same one runs at several times and on
    several days under other trip ids. Each with the local times and the weekdays it leaves at."""
    found: dict[tuple, dict[str, Any]] = {}
    for departure in departures:
        stop = departure["place"]
        operator = operator_of(departure.get("agencyName", ""), departure.get("mode", ""), stop.get("stopId", ""))
        if not operator or not stop.get("departure") or stop.get("pickupType") == "NOT_ALLOWED":
            continue
        leaves = local(stop["departure"], stop.get("tz", ""))
        entry = found.setdefault((operator[0], departure.get("headsign", "")), {
            "trip_id": departure["tripId"], "stop_id": stop["stopId"], "stop": stop["name"], "operator": operator[0], "site": operator[1],
            "sold": operator[2], "modes": departure.get("mode", ""), "times": set(), "weekdays": set()})
        entry["times"].add(leaves.strftime("%H:%M"))
        entry["weekdays"].add(WEEKDAYS[leaves.weekday()])
    return found

def reached(trip: dict[str, Any], pattern: dict[str, Any]) -> list[dict[str, Any]]:
    """The stations a train or bus calls at after the traveller's, each with the minutes from leaving it, the
    last one marked "last". Its "site" is where its timetable says it is sold (the agency's site, as the planner
    links to it), else the operator's of OPERATORS."""
    found, leaves, calls = [], None, 0
    for leg in trip.get("legs") or []:
        site = secure_url(leg.get("agencyUrl")) or pattern["site"]
        for call in [leg["from"], *(leg.get("intermediateStops") or []), leg["to"]]:
            calls += 1
            if call.get("stopId") == pattern["stop_id"]:
                leaves = call.get("departure") or call.get("scheduledDeparture")
                continue
            arrives = call.get("arrival") or call.get("scheduledArrival")
            if leaves and arrives and call.get("dropoffType") != "NOT_ALLOWED":
                found.append({"stop": call["name"], "stop_id": call.get("stopId", ""), "lat": call["lat"], "lon": call["lon"],
                              "minutes": round((moment(arrives) - moment(leaves)).total_seconds() / 60),
                              "from_stop": pattern["stop"], "from_stop_id": pattern["stop_id"], "operator": pattern["operator"],
                              "site": site, "sold": pattern.get("sold", ""), "modes": pattern["modes"], "mode": mode_of(pattern["modes"]),
                              "times": sorted(pattern["times"]), "weekdays": sorted(pattern["weekdays"], key=WEEKDAYS.index),
                              "call": calls})
    if found:
        found[-1]["last"] = found[-1]["call"] == calls
    for stop in found:
        del stop["call"]
    return found

def from_city(place: Place, today: date, say: Callable = print_line, transitous: Transitous | None = None) -> list[dict[str, Any]]:
    """Every station these operators reach straight from the traveller's city, kept GROUND_DAYS, or until what is
    read changes (GROUND_VERSION)."""
    known = read()
    key = place_key(place)
    kept = known.get(key)
    if (kept and date.fromisoformat(kept["checked"]) >= today - timedelta(days=GROUND_DAYS)
            and kept.get("version") == GROUND_VERSION):
        # Kept before the map said "bus" (2026-10-26): the mode as it is said now, from the timetable's own
        return [{**stop, "mode": mode_of(stop["modes"])} for stop in kept["reached"]]
    transitous = transitous or Transitous()
    say(tr("discover_say_ground_reading", place=place.label, days=GROUND_DAYS))
    start = datetime.combine(today + timedelta(days=DAYS_AHEAD), datetime.min.time())
    found: dict[tuple, dict[str, Any]] = {}
    for stop in transitous.stops(place):
        for key_of, pattern in patterns(transitous.departures(stop["stopId"], start)).items():
            if key_of in found:   # the same train seen at another station of the city: its times are added
                found[key_of]["times"] |= pattern["times"]
                found[key_of]["weekdays"] |= pattern["weekdays"]
            else:
                found[key_of] = pattern
    stops = [stop for pattern in list(found.values())[:MAX_TRIPS] for stop in reached(transitous.trip(pattern["trip_id"]), pattern)]
    known[key] = {"checked": today.isoformat(), "place": place.label, "version": GROUND_VERSION, "reached": stops}
    write(known)
    return stops

def to_city(stops: list[dict[str, Any]], latitude: float, longitude: float) -> list[dict[str, Any]]:
    """The quickest train or bus of each operator to a city, by a station within MATCH_KM of its centre."""
    best: dict[str, dict[str, Any]] = {}
    for stop in stops:
        km = distance_km(latitude, longitude, stop["lat"], stop["lon"])
        if km <= MATCH_KM and (stop["operator"] not in best or stop["minutes"] < best[stop["operator"]]["minutes"]):
            best[stop["operator"]] = {**stop, "km": round(km), "grade": "B"}
    return sorted(best.values(), key=lambda option: option["minutes"])[:MAX_PER_CITY]

CITY_LIST: list[dict[str, Any]] = []   # the cities read from CITIES, once per run

def cities() -> list[dict[str, Any]]:
    """GeoNames' cities of more than 15000 people: {"name", "country", "lat", "lon", "population"}. Downloaded
    once into CITIES, with only these columns; the parts of a city (Amsterdam-Zuidoost) are left out. Empty when
    the file cannot be had: the stops are then all shown, each named after its station."""
    if CITY_LIST:
        return CITY_LIST
    try:
        if not CITIES.exists():
            response = httpx.get(CITIES_URL, headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                rows = archive.read("cities15000.txt").decode("utf-8").splitlines()
            kept = [f"{c[1]}\t{c[8]}\t{c[4]}\t{c[5]}\t{c[14]}" for c in (row.split("\t") for row in rows) if len(c) > 14 and c[7] != "PPLX"]
            write_whole(CITIES, "\n".join(kept))
        for row in CITIES.read_text(encoding="utf-8").splitlines():
            name, country, latitude, longitude, population = row.split("\t")
            if DISTRICT.match(name):
                continue
            CITY_LIST.append({"name": name, "country": country, "lat": float(latitude), "lon": float(longitude), "population": int(population or 0)})
    except (OSError, ValueError, zipfile.BadZipFile, KeyError, httpx.HTTPError):
        CITY_LIST.clear()
    return CITY_LIST

GRID_DEGREES = 0.2   # the cells the cities are put in: wider than CITY_KM in latitude and, up to 60°N, in longitude

def cell(latitude: float, longitude: float) -> tuple[int, int]:
    return int(latitude // GRID_DEGREES), int(longitude // GRID_DEGREES)

def city_grid(known: list[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """The cities by cell: a station is compared with the cities of its cell and the ones around it only, not with
    the 30000 of the list."""
    grid: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for city in known:
        grid.setdefault(cell(city["lat"], city["lon"]), []).append(city)
    return grid

def city_of(stop: dict[str, Any], grid: dict[tuple[int, int], list[dict[str, Any]]]) -> dict[str, Any] | None:
    """The city of a station: the largest of MIN_POPULATION within CENTRE_KM of it (Amsterdam for Sloterdijk, Delft
    for Delft, not The Hague 9 km away), else the largest of MIN_POPULATION within CITY_KM (Amsterdam for a bus
    stop in Diemen, Milan for one in Novate Milanese), else the nearest town within CITY_KM. None when no city is
    that close."""
    row, column = cell(stop["lat"], stop["lon"])
    near = [(distance_km(stop["lat"], stop["lon"], city["lat"], city["lon"]), city)
            for up in (-1, 0, 1) for side in (-1, 0, 1) for city in grid.get((row + up, column + side), [])]
    for km_at_most in (CENTRE_KM, CITY_KM):
        large = [city for km, city in near if km <= km_at_most and city["population"] >= MIN_POPULATION]
        if large:
            return max(large, key=lambda city: city["population"])
    return min(((km, city) for km, city in near if km <= CITY_KM), key=lambda pair: pair[0], default=(0, None))[1]

def station_name(stop: dict[str, Any]) -> str:
    """A place named after its station, when no city is known: its name up to " - ", "," or "(" ("Paris -
    Bercy-Seine Bus Station" is Paris), without the STATION_WORDS at its end."""
    words = re.split(r" - |,|\(", stop["stop"])[0].split()
    while len(words) > 1 and words[-1].lower() in STATION_WORDS:
        words.pop()
    return " ".join(words) or stop["stop"]

def only_by_ground(stops: list[dict[str, Any]], place: Place, flown_to: list[tuple[float, float]],
                   airports: dict[str, Any]) -> list[dict[str, Any]]:
    """The cities these trains and buses reach straight that no flight of the map goes to, the quickest MAX_ONLY,
    at the end of the line or on the way (Rotterdam, between Antwerp and Amsterdam). A stop is shown when it is
    farther than MIN_KM, or its city has BIG_CITY people, and when its city has MIN_POPULATION or the line ends
    there; the stations of one city are one destination. Each with its options, as to_city gives them, and no
    price. A station at an airport is left out."""
    at_airport = lambda stop: any(distance_km(stop["lat"], stop["lon"], airport["latitude"], airport["longitude"]) <= AIRPORT_STATION_KM
                                  for airport in airports.values())
    away = lambda stop: distance_km(place.latitude, place.longitude, stop["lat"], stop["lon"])
    candidates = sorted((stop for stop in stops if away(stop) > MATCH_KM
                         and all(distance_km(latitude, longitude, stop["lat"], stop["lon"]) > MATCH_KM for latitude, longitude in flown_to)
                         and not at_airport(stop)),
                        key=lambda stop: stop["minutes"])
    known = cities()
    grid = city_grid(known)
    grouped: dict[str, dict[str, Any]] = {}
    for stop in candidates:
        city = city_of(stop, grid) if known else None
        if away(stop) < MIN_KM and not (city and city["population"] >= BIG_CITY):
            continue   # a day out, not a trip
        if known and not stop.get("last") and (city is None or city["population"] < MIN_POPULATION):
            continue   # a halt on the way, or a town: not a trip of its own
        if city is None:   # no city near, or no list of cities: the station names the place, its stations within MATCH_KM
            key = next((key for key, entry in grouped.items() if not entry["city"]
                        and distance_km(entry["lat"], entry["lon"], stop["lat"], stop["lon"]) <= MATCH_KM), f"{stop['lat']:.2f},{stop['lon']:.2f}")
            entry = grouped.setdefault(key, {"name": station_name(stop), "country": "", "lat": stop["lat"], "lon": stop["lon"],
                                             "city": False, "stops": []})
        else:
            key = f"{city['country']}|{city['name']}|{city['lat']:.2f}"
            entry = grouped.setdefault(key, {"name": city["name"], "country": city["country"], "lat": city["lat"], "lon": city["lon"],
                                             "city": True, "stops": []})
        entry["stops"].append(stop)
    found = []
    for key, entry in list(grouped.items())[:MAX_ONLY]:
        options = to_city(entry["stops"], entry["stops"][0]["lat"], entry["stops"][0]["lon"])
        found.append({"key": key, "name": entry["name"], "country": entry["country"], "lat": entry["lat"], "lon": entry["lon"],
                      "minutes": options[0]["minutes"], "modes": sorted({option["mode"] for option in options}), "ground": options})
    return found

def way_back(option: dict[str, Any], days: Days, transitous: Transitous) -> dict[str, Any]:
    """The direct connections of the same operator back, from the station reached to the one left, over the first
    days to come back on: {"day", "times", "minutes"}, times empty when there is none those days."""
    first = date.fromisoformat(days.back_days[0] if days.back_days else days.back_first)
    last = date.fromisoformat(days.back_days[-1] if days.back_days else days.back_last)
    span = min(BACK_WINDOW_DAYS, (last - first).days + 1)
    found = transitous.direct(option["stop_id"], option["from_stop_id"], first.isoformat(), option["modes"], span)
    times, minutes = [], []
    for itinerary in found:
        rides = [leg for leg in itinerary["legs"] if leg["mode"] != "WALK"]
        if len(rides) == 1 and (operator_of(rides[0].get("agencyName", ""), rides[0].get("mode", ""),
                                            rides[0]["from"].get("stopId", "")) or ("",))[0] == option["operator"]:
            leaves = local(rides[0]["startTime"], rides[0]["from"].get("tz", ""))
            times.append(leaves.strftime("%d/%m %H:%M"))
            minutes.append(round(itinerary["duration"] / 60))
    return {"day": first.isoformat(), "days": span, "times": sorted(set(times)), "minutes": min(minutes, default=0)}

def stations_reached(place: Place, today: date, say: Callable = print_line, transitous: Transitous | None = None) -> list[dict[str, Any]] | None:
    """from_city, or None when Transitous fails: the map then shows no train or bus. It needs only the place, so
    finder.py starts it with the flights' searches, and the minutes it takes the first time are hidden by theirs."""
    try:
        return from_city(place, today, say, transitous)
    except (httpx.HTTPError, ValueError) as error:
        say(tr("discover_say_ground_failed", error=type(error).__name__))
        return None

def look_up(place: Place, stops: list[dict[str, Any]] | None, destinations: list[dict[str, Any]], centres: dict[str, Place],
            airports: dict[str, Any], say: Callable = print_line) -> list[dict[str, Any]]:
    """The trains and buses of each destination of the map, under "ground" (grade B, no price), from the stations
    reached (stations_reached). Every airport of the city carries them: the cheapest one may change once the
    timetables are looked up (confirm.py). Returns the cities they reach that no flight goes to (only_by_ground).
    Nothing is asked here: the way back is asked for once the map is open (ways_back). With no stations (Transitous
    failed), the map is as it would be without them: these come apart from the flights' prices."""
    if not stops:
        return []
    count, flown_to = 0, []
    for flight in destinations:
        centre = centres.get(flight["to"])
        airport = airports.get(flight["to"]) or {}
        latitude, longitude = (centre.latitude, centre.longitude) if centre else (airport.get("latitude"), airport.get("longitude"))
        if latitude is None:
            continue
        flown_to.append((latitude, longitude))
        options = to_city(stops, latitude, longitude)
        if not options:
            continue
        for each in [flight, *flight.get("also", [])]:
            each["ground"] = options
        count += 1
    only = only_by_ground(stops, place, flown_to, airports)
    if count or only:
        say(tr("discover_say_ground_found", count=count, place=place.label, only=len(only)))
    return only

def ways_back(cities: list[list[dict[str, Any]]], days: Days, say: Callable = print_line, transitous: Transitous | None = None) -> int:
    """For a return trip, the timetable's way back (way_back) of the options of these cities, the quickest cities
    first, MAX_BACK_REQUESTS at most: once the map is open, after the prices (ground_fares.py). An option whose way
    back was priced has it already, sold for the day, and is not asked again. Returns how many were asked."""
    if not days.round_trip:
        return 0
    transitous = transitous or Transitous()
    waiting = [option for options in sorted(cities, key=lambda options: min(option["minutes"] for option in options))
               for option in options if "back" not in option and not (option.get("price") and option["price"]["back"])]
    for option in waiting[:MAX_BACK_REQUESTS]:
        try:
            option["back"] = way_back(option, days, transitous)
        except (httpx.HTTPError, ValueError):
            option["back"] = None   # not known: said as such, not as "none"
    if waiting:
        say(tr("discover_say_ground_back", count=min(len(waiting), MAX_BACK_REQUESTS)))
    return min(len(waiting), MAX_BACK_REQUESTS)
