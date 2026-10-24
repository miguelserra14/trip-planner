"""The trains and coaches that go straight from the traveller's city to a destination of the map, with no flight,
run by the operators whose prices programs cannot read: BlaBlaCar Bus, Deutsche Bahn, Eurostar, SNCF, NS
International, SNCB, European Sleeper and CP. Their timetables are published (Transitous); their sites refuse
programs, forbid them, or ask for a key. The operators the planner prices (fares.py) are left to the planner.

The way out is direkt.bahn.guru's: the long-distance departures of the stations near the city over a week, then
the stations each of those trains and coaches calls at after it. A station near the centre of a city of the map
makes that city reachable; a city no flight goes to, farther than MIN_KM, is a destination of its own, by train or
coach only. The way back, for a return trip, is one request per destination and operator: the
direct connections of that operator, on the first day to come back on, from the station reached to the one left.

Only the timetable is known: grade B, and no price. The map shows each next to the flight for the traveller to
check its price on the operator's site; the code never takes it for the cheapest way. No web search, no model.
The way out is kept GROUND_DAYS per city; the way back depends on the day, and is asked for again each map."""
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from discover.sources import Days
from trip_planner.sources.timetables.client import TRANSITOUS_API
from trip_planner.trip.places import distance_km, Place
from trip_planner.trip.trip_settings import USER_AGENT
from trip_planner.utils import say as print_line

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
}
LONG_MODES = ("HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL", "COACH")   # the departures read: no local ones
COACH_FEED = "eu-blablacar-bus_"   # the coach stops read: the other coach stops are of operators the planner prices
GROUND = Path(__file__).parent / "cache" / "ground.json"   # the way out found for each city
GROUND_DAYS = 30           # a city's trains and coaches are looked for again after this long
STOP_KM = 12               # the stations this close to the traveller's city are its stations
MATCH_KM = 20              # a station this close to the centre of a city of the map reaches that city
# A city no flight of the map goes to is shown when a train or coach reaches it straight, farther than this: the
# stations on the way out of the traveller's region are no trip. The ones shown are the quickest MAX_ONLY.
MIN_KM = 100
MAX_ONLY = 40
# The country of a city reached is the one of the nearest airport this close: a guess, for the map's filter only,
# never shown. It is wrong near a border (Aachen by Maastricht's, Basel by its own in France), missing with no airport.
COUNTRY_KM = 60
AIRPORT_STATION_KM = 3    # a station this close to an airport is the way to a flight (Schiphol, CDG 2), no city
# The words a station's name ends with that are not the city's: "Köln Hbf" is Köln
STATION_WORDS = {"hbf", "hl.n.", "centraal", "centrum", "central", "centrale", "bf", "gare"}
DAYS_AHEAD = 7             # the week of departures read starts this far ahead: today's may be gone already
WEEK_SECONDS = 7 * 24 * 3600
MAX_DEPARTURES = 300       # departures read per station over that week
MAX_TRIPS = 30             # trains and coaches read per city, one of each pattern (operator, where it goes)
MAX_PER_CITY = 3           # operators shown per destination, the quickest first
MAX_BACK_REQUESTS = 20     # ways back asked for per map
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
    GROUND.parent.mkdir(exist_ok=True)
    GROUND.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")

def place_key(place: Place) -> str:
    return f"{place.latitude:.2f},{place.longitude:.2f}"

def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))

def local(text: str, zone: str) -> datetime:
    return moment(text).astimezone(ZoneInfo(zone or "UTC"))

def operator_of(agency: str) -> tuple[str, str] | None:
    """The operator shown and its site, for an agency of Transitous; None for one the map leaves to the planner."""
    name = (agency or "").lower()
    return next((shown for start, shown in OPERATORS.items() if name.startswith(start)), None)

def mode_of(modes: str) -> str:
    return "coach" if modes == "COACH" else "train"

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
        """The long-distance stations near a place: rail ones of any feed, coach ones of BlaBlaCar Bus."""
        degrees = STOP_KM / 111
        found = self.get("map/stops", min=f"{place.latitude + degrees},{place.longitude - degrees * 1.6}",
                         max=f"{place.latitude - degrees},{place.longitude + degrees * 1.6}")
        rail = lambda stop: set(stop.get("modes") or []) & (set(LONG_MODES) - {"COACH"})
        return [stop for stop in found if (rail(stop) or stop.get("stopId", "").startswith(COACH_FEED))
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
    """One train or coach of each pattern, (operator, where it goes): the same one runs at several times and on
    several days under other trip ids. Each with the local times and the weekdays it leaves at."""
    found: dict[tuple, dict[str, Any]] = {}
    for departure in departures:
        stop, operator = departure["place"], operator_of(departure.get("agencyName", ""))
        if not operator or not stop.get("departure") or stop.get("pickupType") == "NOT_ALLOWED":
            continue
        leaves = local(stop["departure"], stop.get("tz", ""))
        entry = found.setdefault((operator[0], departure.get("headsign", "")), {
            "trip_id": departure["tripId"], "stop_id": stop["stopId"], "stop": stop["name"], "operator": operator[0], "site": operator[1],
            "modes": departure.get("mode", ""), "times": set(), "weekdays": set()})
        entry["times"].add(leaves.strftime("%H:%M"))
        entry["weekdays"].add(WEEKDAYS[leaves.weekday()])
    return found

def reached(trip: dict[str, Any], pattern: dict[str, Any]) -> list[dict[str, Any]]:
    """The stations a train or coach calls at after the traveller's, each with the minutes from leaving it."""
    found, leaves = [], None
    for leg in trip.get("legs") or []:
        for call in [leg["from"], *(leg.get("intermediateStops") or []), leg["to"]]:
            if call.get("stopId") == pattern["stop_id"]:
                leaves = call.get("departure") or call.get("scheduledDeparture")
                continue
            arrives = call.get("arrival") or call.get("scheduledArrival")
            if leaves and arrives and call.get("dropoffType") != "NOT_ALLOWED":
                found.append({"stop": call["name"], "stop_id": call.get("stopId", ""), "lat": call["lat"], "lon": call["lon"],
                              "minutes": round((moment(arrives) - moment(leaves)).total_seconds() / 60),
                              "from_stop": pattern["stop"], "from_stop_id": pattern["stop_id"], "operator": pattern["operator"],
                              "site": pattern["site"], "modes": pattern["modes"], "mode": mode_of(pattern["modes"]),
                              "times": sorted(pattern["times"]), "weekdays": sorted(pattern["weekdays"], key=WEEKDAYS.index)})
    return found

def from_city(place: Place, today: date, say: Callable = print_line, transitous: Transitous | None = None) -> list[dict[str, Any]]:
    """Every station these operators reach straight from the traveller's city, kept GROUND_DAYS."""
    known = read()
    key = place_key(place)
    kept = known.get(key)
    if kept and date.fromisoformat(kept["checked"]) >= today - timedelta(days=GROUND_DAYS):
        return kept["reached"]
    transitous = transitous or Transitous()
    say(f"Reading the trains and coaches with no published prices that leave {place.label} (Transitous): they are kept {GROUND_DAYS} days")
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
    known[key] = {"checked": today.isoformat(), "place": place.label, "reached": stops}
    write(known)
    return stops

def to_city(stops: list[dict[str, Any]], latitude: float, longitude: float) -> list[dict[str, Any]]:
    """The quickest train or coach of each operator to a city, by a station within MATCH_KM of its centre."""
    best: dict[str, dict[str, Any]] = {}
    for stop in stops:
        km = distance_km(latitude, longitude, stop["lat"], stop["lon"])
        if km <= MATCH_KM and (stop["operator"] not in best or stop["minutes"] < best[stop["operator"]]["minutes"]):
            best[stop["operator"]] = {**stop, "km": round(km), "grade": "B"}
    return sorted(best.values(), key=lambda option: option["minutes"])[:MAX_PER_CITY]

def city_name(stop: dict[str, Any], airports: dict[str, Any]) -> tuple[str, str]:
    """The name and country code of the city of a station: its name up to " - ", "," or "(" ("Paris - Bercy-Seine
    Bus Station" is Paris), without the STATION_WORDS at its end, and the country of the nearest airport within
    COUNTRY_KM. The airport's own city is not used: the database names the town it is in (Marignane, not Marseille)."""
    km = lambda airport: distance_km(stop["lat"], stop["lon"], airport["latitude"], airport["longitude"])
    near = min(airports.values(), key=km, default=None)
    country = near.get("country", "") if near and km(near) <= COUNTRY_KM else ""
    words = re.split(r" - |,|\(", stop["stop"])[0].split()
    while len(words) > 1 and words[-1].lower() in STATION_WORDS:
        words.pop()
    return " ".join(words) or stop["stop"], country

def only_by_ground(stops: list[dict[str, Any]], place: Place, flown_to: list[tuple[float, float]],
                   airports: dict[str, Any]) -> list[dict[str, Any]]:
    """The cities these trains and coaches reach straight that no flight of the map goes to, farther than MIN_KM,
    the quickest MAX_ONLY: the stations within MATCH_KM of the quickest one are one city. Each with its options,
    as to_city gives them, and no price. A station at an airport is left out."""
    at_airport = lambda stop: any(distance_km(stop["lat"], stop["lon"], airport["latitude"], airport["longitude"]) <= AIRPORT_STATION_KM
                                  for airport in airports.values())
    far = sorted((stop for stop in stops if distance_km(place.latitude, place.longitude, stop["lat"], stop["lon"]) >= MIN_KM
                  and all(distance_km(latitude, longitude, stop["lat"], stop["lon"]) > MATCH_KM for latitude, longitude in flown_to)
                  and not at_airport(stop)),
                 key=lambda stop: stop["minutes"])
    cities: list[list[dict[str, Any]]] = []
    for stop in far:
        city = next((city for city in cities if distance_km(city[0]["lat"], city[0]["lon"], stop["lat"], stop["lon"]) <= MATCH_KM), None)
        if city is None:
            cities.append([stop])
        else:
            city.append(stop)
    found = []
    for city in cities[:MAX_ONLY]:
        name, country = city_name(city[0], airports)
        options = to_city(city, city[0]["lat"], city[0]["lon"])
        found.append({"key": f"{city[0]['lat']:.2f},{city[0]['lon']:.2f}", "name": name, "country": country,
                      "lat": city[0]["lat"], "lon": city[0]["lon"], "minutes": options[0]["minutes"],
                      "modes": sorted({option["mode"] for option in options}), "ground": options})
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
        if len(rides) == 1 and (operator_of(rides[0].get("agencyName", "")) or ("",))[0] == option["operator"]:
            leaves = local(rides[0]["startTime"], rides[0]["from"].get("tz", ""))
            times.append(leaves.strftime("%d/%m %H:%M"))
            minutes.append(round(itinerary["duration"] / 60))
    return {"day": first.isoformat(), "days": span, "times": sorted(set(times)), "minutes": min(minutes, default=0)}

def look_up(place: Place, days: Days, destinations: list[dict[str, Any]], centres: dict[str, Place], airports: dict[str, Any],
            today: date, say: Callable = print_line, transitous: Transitous | None = None) -> list[dict[str, Any]]:
    """The trains and coaches of each destination of the map, under "ground" (grade B, no price), with the way
    back for a return trip. Every airport of the city carries them: the cheapest one may change once the timetables
    are looked up (confirm.py). Returns the cities they reach that no flight goes to (only_by_ground), with their
    way back while MAX_BACK_REQUESTS allows, after the cities of the flights. A failure of Transitous leaves the map
    as it is: these are hints, not part of the prices."""
    transitous = transitous or Transitous()
    try:
        stops = from_city(place, today, say, transitous)
    except (httpx.HTTPError, ValueError) as error:
        say(f"Trains and coaches with no published prices: Transitous did not answer ({type(error).__name__}), the map shows none")
        return []
    asked = [0]

    def with_back(options: list[dict[str, Any]]) -> None:
        for option in options:
            if days.round_trip and asked[0] < MAX_BACK_REQUESTS:
                asked[0] += 1
                try:
                    option["back"] = way_back(option, days, transitous)
                except (httpx.HTTPError, ValueError):
                    option["back"] = None   # not known: said as such, not as "none"
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
        with_back(options)
        for each in [flight, *flight.get("also", [])]:
            each["ground"] = options
        count += 1
    only = only_by_ground(stops, place, flown_to, airports)
    for city in only:
        with_back(city["ground"])
    if count or only:
        say(f"Trains and coaches with no published prices: {count} destinations of the map are also reached straight from "
            f"{place.label}, and {len(only)} cities with no flight (timetables only, no prices)")
    return only
