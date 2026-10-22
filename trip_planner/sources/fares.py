"""Real prices of ground transport. Published timetables carry no prices, so the vehicles of a connection found
in them are priced by asking the booking system of the operator that runs them, where one can be read by code.

A price is only kept when the operator sells that very departure: same stations, same minute of departure and
of arrival. Otherwise there is no price from here, and the leg keeps its estimate from the distance.

FARE_SOURCES lists the operators. None of these is an official service for programs: they are the ones the
operators' own sites use, and they can change without notice. To support another operator, write a function
like oebb_fare and add it there.

RIDE_SOURCES lists the operators that can also say everything they run between a city and an airport on one day:
rides the timetables may not have, each with its price and confirmed for that day (grade A)."""
import base64
import json
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx
from primp import Client

from trip_planner import current
from trip_planner.config import ROOT, USER_AGENT
from trip_planner.places import distance_km, Place
from trip_planner.texts import tr
from trip_planner.utils import logged, plain, to_minutes, to_time

TIMEOUT = 20
SAME_MINUTES = 2          # an operator's departure is the timetable's when it leaves within this of it...
SAME_ARRIVAL_MINUTES = 6  # ... and arrives within this: the two do not always agree on the minute a train gets in
SAME_STATION_KM = 1.5     # an operator's station is the timetable's stop when it is this close
OPERATOR_DATA = ROOT / "resources" / "operators"   # what operators' sites do not give with each answer: their stops, their place names
http = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=TIMEOUT, follow_redirects=True)

@dataclass(frozen=True)
class Trip:
    """What is asked of an operator: one ticket from the first stop of a run of its vehicles to the last."""
    start: str            # name of the stop, as the timetable writes it
    end: str
    start_id: str         # the timetable's id of the stop: "eu-flixbus_<FlixBus's own id>"
    end_id: str
    start_at: tuple       # (latitude, longitude), or () when the timetable did not give it
    end_at: tuple
    day: str              # yyyy-mm-dd
    leave: str            # hh:mm on the local clock
    arrive: str
    line: str = ""        # the line of the first vehicle, for the tariffs that price one line apart

    @property
    def minutes(self) -> int:
        return to_minutes(self.arrive) - to_minutes(self.leave)

    def matches(self, leave: str, arrive: str) -> bool:
        """Whether a departure of the operator ("...T10:28...", or "10:28") is this trip."""
        clock = lambda moment: to_minutes(moment[11:16] if "T" in moment else moment[:5])
        return (abs(clock(leave) - to_minutes(self.leave)) <= SAME_MINUTES
                and abs(clock(arrive) - to_minutes(self.arrive)) <= SAME_ARRIVAL_MINUTES)

class NotRunning(Exception):
    """An operator's answer for a day it already runs rides on between the two stops: the departure asked for is
    not among them. Raised only by operators that recognise the stops by the timetable's own ids, so that a stop
    named differently on their site cannot pass for a departure that does not run."""

# ---------------------------------------------------------------- currencies
RATES: dict[str, float] = {}

def in_eur(amount: float, currency: str) -> float | None:
    """An amount in EUR at the European Central Bank's rate of the day. None if the rate is not known."""
    if currency == "EUR":
        return amount
    if not RATES:
        page = http.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml").raise_for_status().text
        RATES.update({cube.get("currency"): float(cube.get("rate")) for cube in ElementTree.fromstring(page).iter() if cube.get("currency")})
    return round(amount / RATES[currency], 2) if currency in RATES else None

# ---------------------------------------------------------------- the operators
FLIXBUS = "https://global.api.flixbus.com/search"
FLIXBUS_PLACES: dict[str, str | None] = {}

def flixbus_search(start: str, end: str, day: str, by: str, sold_out: bool = False) -> tuple:
    """What FlixBus sells between two of its stations, or two of its cities (by), on one day (yyyy-mm-dd).
    Returns (the rides that can be bought, the names of the stations by their id). sold_out also returns the rides
    that run but can no longer be bought."""
    found = http.get(f"{FLIXBUS}/service/v4/search", params={
        f"from_{by}_id": start, f"to_{by}_id": end, "search_by": "stations" if by == "station" else "cities",
        "departure_date": f"{day[8:10]}.{day[5:7]}.{day[0:4]}", "products": '{"adult":1}', "currency": "EUR", "locale": "en",
        "include_after_midnight_rides": "0",
    }).raise_for_status().json()
    rides = [ride for trip in found.get("trips") or [] for ride in (trip.get("results") or {}).values() if sold_out or ride.get("status") == "available"]
    return rides, {identity: station.get("name", "") for identity, station in (found.get("stations") or {}).items()}

def flixbus_price(ride: dict[str, Any]) -> float:
    return float(ride["price"].get("total_with_platform_fee") or ride["price"]["total"])

def flixbus_fare(trip: Trip) -> float | None:
    """FlixBus, anywhere: the timetable's stop ids are FlixBus's own. Raises NotRunning when FlixBus runs rides
    between the two stops that day, sold out or not, and none at those times."""
    rides, stations = flixbus_search(trip.start_id.split("_", 1)[1], trip.end_id.split("_", 1)[1], trip.day, "station", sold_out=True)
    that_day = [ride for ride in rides if ride["departure"]["date"][:10] == trip.day]
    same = [ride for ride in that_day if trip.matches(ride["departure"]["date"], ride["arrival"]["date"])]
    if that_day and not same:
        raise NotRunning
    return next((flixbus_price(ride) for ride in same if ride.get("status") == "available"), None)

def flixbus_city(query: str, latitude: float, longitude: float, max_km: float) -> str | None:
    """The id FlixBus gives to a city or an airport, found by name and checked by position."""
    key = f"{plain(query)}|{max_km}"
    if key not in FLIXBUS_PLACES:
        found = http.get(f"{FLIXBUS}/autocomplete/cities", params={"q": query, "lang": "en", "flixbus_cities_only": "false", "stations": "true"}).raise_for_status().json()
        near = [(distance_km(latitude, longitude, city["location"]["lat"], city["location"]["lon"]), city["id"]) for city in found if city.get("location")]
        near = [candidate for candidate in near if candidate[0] <= max_km]
        FLIXBUS_PLACES[key] = min(near)[1] if near else None
    return FLIXBUS_PLACES[key]

def flixbus_rides(city: Place, airport: dict[str, Any], day: str, to_airport: bool = True) -> list[dict[str, Any]] | None:
    """Every FlixBus ride between a city and an airport on one day (yyyy-mm-dd), with its real price: towards the
    airport, or from it. Returns None when FlixBus has no stop in one of the two, so there is nothing to check."""
    city_id = flixbus_city(city.english_name, city.latitude, city.longitude, 25)
    airport_id = (flixbus_city(f"{airport['city']} Airport", airport["latitude"], airport["longitude"], 6)
                  or flixbus_city(airport["name"], airport["latitude"], airport["longitude"], 6))
    if not city_id or not airport_id or city_id == airport_id:   # the same: FlixBus knows the airport only as a stop of the city
        return None
    start, end = (city_id, airport_id) if to_airport else (airport_id, city_id)
    found, stations = flixbus_search(start, end, day, "city")
    rides = []
    for ride in found:
        departure, arrival = ride["departure"]["date"], ride["arrival"]["date"]
        if departure[:10] != day or arrival[:10] != day:
            continue
        rides.append({
            "grade": "A", "source": "FlixBus", "service_day": day,
            "leave": to_minutes(departure[11:16]), "arrive": to_minutes(arrival[11:16]), "price_eur": flixbus_price(ride),
            "steps": [{"time": departure[11:16], "arrive": arrival[11:16], "line": "FlixBus", "agency": "FlixBus",
                       "from": stations.get(ride["departure"]["station_id"], ""), "to": stations.get(ride["arrival"]["station_id"], ""),
                       "rail": False, "coach": True, "km": airport["km"]}],
            "urls": [f"https://shop.flixbus.pt/search?departureCity={start}&arrivalCity={end}&rideDate={day[8:10]}.{day[5:7]}.{day[0:4]}&adult=1"],
        })
    return sorted(rides, key=lambda ride: ride["leave"])

# Rede Expressos, the national coach network of Portugal (with RENEX and the other companies it sells for). It
# publishes no timetable (none in Transitous, Transitland or the Mobility Database on 2026-10-21): its rides are
# only in its booking system, read here with the requests its own site makes. The site lists its stops without
# their position: REDE_EXPRESSOS_STOPS_FILE places them, worked out once from the site's points of sale (most are
# at the coach terminal), the airport database and Nominatim.
REDE_EXPRESSOS = "https://webapi.rede-expressos.pt/api/App"
REDE_EXPRESSOS_STOPS_FILE = OPERATOR_DATA / "rede_expressos_stops.json"
REDE_EXPRESSOS_STOPS: list[dict[str, Any]] = (json.loads(REDE_EXPRESSOS_STOPS_FILE.read_text(encoding="utf-8"))
                                              if REDE_EXPRESSOS_STOPS_FILE.exists() else [])
REDE_EXPRESSOS_CITY_KM = 10     # the stop nearest to the traveller's city serves it when it is this close...
REDE_EXPRESSOS_AIRPORT_KM = 3   # ... and the one nearest to an airport, the airport, when it is this close
REDE_EXPRESSOS_CITY_STOPS = 2   # stations of that town asked, nearest first: Lisbon and Porto have several

def rede_expressos_headers() -> dict[str, str]:
    """The headers its site sends: a client id and a token made of a random id and the time, both in base64."""
    token, now = str(uuid.uuid4()), str(round(time.time()))
    encode = lambda text: base64.b64encode(text.encode()).decode()
    return {"X-Client-Type": "SITE", "X-Client-ID": encode(str(uuid.uuid4())), "X-Client-Lang": "pt",
            "X-Legacy": encode(token[:3] + now[5:10] + token[8:24] + now[:5] + token[29:36])}

def rede_expressos_stops(latitude: float, longitude: float, max_km: float, airport: bool) -> list[dict[str, Any]]:
    """Its stops this close to a place, nearest first: at an airport only those of the airport, in a city none of them."""
    near = [(distance_km(latitude, longitude, stop["lat"], stop["lon"]), stop) for stop in REDE_EXPRESSOS_STOPS
            if ("aeroporto" in plain(stop["name"])) == airport]
    return [stop for km, stop in sorted(near, key=lambda pair: pair[0]) if km <= max_km]

def town_of(stop_name: str) -> str:
    """The town of a stop: "Lisboa (Sete Rios)" and "Lisboa - Oriente" are both in Lisboa."""
    return plain(re.split(r"\s+\(|\s+-\s+", stop_name)[0])

def rede_expressos_rides(city: Place, airport: dict[str, Any], day: str, to_airport: bool = True) -> list[dict[str, Any]] | None:
    """Every Rede Expressos ride between a city and an airport of Portugal on one day (yyyy-mm-dd), with the
    cheapest price on sale. A ride may arrive the next day: its arrival is then counted past 24:00. Returns None
    when it has no stop at one of the two, so there is nothing to check."""
    airport_stops = rede_expressos_stops(airport["latitude"], airport["longitude"], REDE_EXPRESSOS_AIRPORT_KM, True)
    near = rede_expressos_stops(city.latitude, city.longitude, REDE_EXPRESSOS_CITY_KM, False)
    if airport["country"] != "PT" or not airport_stops or not near:
        return None
    # The stops of the town of the nearest one, and only those: a village a few km away is another place, and a
    # search for a town also answers with rides to the towns around it (Lagos with Alvor, Nazaré with Pataias)
    town = town_of(near[0]["name"])
    city_stops = [stop for stop in near if town_of(stop["name"]) == town][:REDE_EXPRESSOS_CITY_STOPS]
    serves_city = lambda name: town_of(name) == town
    rides = []
    for city_stop in city_stops:
        start, end = (city_stop, airport_stops[0]) if to_airport else (airport_stops[0], city_stop)
        found = http.post(f"{REDE_EXPRESSOS}/OneWayForSelectedDate", headers=rede_expressos_headers(), json={
            "from": start["id"], "to": end["id"], "date": day, "campaign": "", "isCampaignFirstSearch": False,
            "returnNextMinPrice": False, "passengers": [{"fareTypeId": "1", "quantity": 1}],
        }).raise_for_status().json()
        for ride in found.get("outgoingSchedules") or []:
            departure, arrival = ride["departureDate"], ride["arrivalDate"]
            if departure[:10] != day or not ride.get("minPrice") or not ride.get("availableSeats", 1):
                continue
            if not serves_city((ride.get("from" if to_airport else "to") or {}).get("name") or city_stop["name"]):
                continue
            arrive = to_minutes(arrival[11:16]) + (24 * 60 if arrival[:10] > day else 0)
            line = (ride.get("departureCarrier") or {}).get("name") or "Rede Expressos"
            changes = ride.get("transfersCount") or 0
            rides.append({
                "grade": "A", "source": "Rede Expressos", "service_day": day,
                "leave": to_minutes(departure[11:16]), "arrive": arrive, "price_eur": float(ride["minPrice"]),
                "steps": [{"time": departure[11:16], "arrive": arrival[11:16], "agency": "Rede Expressos",
                           "line": tr("ride_changes", line=line, count=changes) if changes else line,
                           "from": (ride.get("from") or {}).get("name") or start["name"], "to": (ride.get("to") or {}).get("name") or end["name"],
                           "rail": False, "coach": True, "km": ride.get("distance") or airport["km"]}],
                "urls": ["https://rede-expressos.pt/pt"],
            })
    unique = {(ride["leave"], ride["arrive"], ride["steps"][0]["from"]): ride for ride in rides}
    return sorted(unique.values(), key=lambda ride: ride["leave"])

# Movelia, the booking site of the Spanish coach companies (Monbus, Damas, Bacoma, Subus, ALSA...) and of Renfe's
# trains: one search answers with all of them, with real prices for the day, and many of them publish no timetable
# (not in Transitous on 2026-10-21). It is read with the requests its own site makes: a token for the anonymous
# visitor its pages log in as, then a search by the names of two places. Some companies sell only a few weeks
# ahead (Damas): on a far date the search answers with the others.
MOVELIA = "https://api.movelia.es/api_iu"
MOVELIA_VISITOR = {"user": "usuNoReg", "password": "u5uN0R3g"}   # written in the site's own code, the same for every visitor
MOVELIA_BROWSER = {"User-Agent": "Mozilla/5.0"}   # it refuses the token to a client that says it is a program
MOVELIA_PLACES_FILE = OPERATOR_DATA / "movelia_places.json"   # the names it searches by, kept here and read again MOVELIA_PLACES_DAYS later
MOVELIA_PLACES_DAYS = 30
MOVELIA_AIRPORT_STATION = re.compile(r"aerop|airport|aeroport|barajas|\bt[1-4]\b")   # a station of the airport, by its name
MOVELIA_TRAIN = "3"     # its kind of vehicle for a train
MOVELIA_GROUND = {"1", "3"}   # coaches and trains: it also sells boats (2) and flights (4)
MOVELIA_SESSION: dict[str, Any] = {}
MOVELIA_LOCK = threading.Lock()

def movelia_places() -> dict[str, str]:
    """The places it searches by, by their plain name: "ALICANTE / ALACANT" is found as "alicante" and as "alacant"."""
    with MOVELIA_LOCK:
        if "places" not in MOVELIA_SESSION:
            fresh = MOVELIA_PLACES_FILE.exists() and time.time() - MOVELIA_PLACES_FILE.stat().st_mtime < MOVELIA_PLACES_DAYS * 86400
            if not fresh:
                listed = http.get(f"{MOVELIA}/v1/GetCitiesWithBooking", headers=movelia_headers()).raise_for_status().json()
                OPERATOR_DATA.mkdir(parents=True, exist_ok=True)
                MOVELIA_PLACES_FILE.write_text(json.dumps(sorted(place["name"] for place in listed if place.get("countrY_CODE") == "34"),
                                                          ensure_ascii=False, indent=0), encoding="utf-8", newline="\n")
            names = json.loads(MOVELIA_PLACES_FILE.read_text(encoding="utf-8"))
            MOVELIA_SESSION["places"] = {plain(part): name for name in names for part in [name, *name.split("/")] if plain(part)}
        return MOVELIA_SESSION["places"]

def movelia_headers() -> dict[str, str]:
    if "token" not in MOVELIA_SESSION:
        answer = http.post(f"{MOVELIA}/GetToken", json={**MOVELIA_VISITOR, "language": "es"}, headers=MOVELIA_BROWSER).raise_for_status().json()
        MOVELIA_SESSION["token"] = answer["value"]
    return {**MOVELIA_BROWSER, "Authorization": f"Bearer {MOVELIA_SESSION['token']}"}

def movelia_airport(airport: dict[str, Any], places: dict[str, str]) -> str | None:
    """The name it searches an airport by: its own name for the airport (AEROPUERTO ASTURIAS), else the airport's
    city or one of the airport database's names for it (Seville is SEVILLA). Its rides are kept only from or to a
    station of the airport (movelia_rides)."""
    words = set(plain(f"{airport['name']} {airport['city']}").replace("-", " ").split()) - {"airport", "international", "de", "del", "la"}
    for key, name in places.items():
        if key.startswith("aeropuerto") and words & set(key.replace("-", " ").split()):
            return name
    for key in [airport["city"], *airport.get("search_text", "").split(",")]:
        key = plain(key).removesuffix(" airport").strip()
        if key in places:
            return places[key]
    return None

def movelia_rides(city: Place, airport: dict[str, Any], day: str, to_airport: bool = True) -> list[dict[str, Any]] | None:
    """Every coach and train it sells between a city and an airport of Spain on one day (yyyy-mm-dd), direct ones
    only, with their price. Returns None when it knows one of the two by no name, so there is nothing to check."""
    if airport["country"] != "ES":
        return None
    places = movelia_places()
    town = next((places[plain(name)] for name in (city.local_name, city.english_name) if plain(name) in places), None)
    station = movelia_airport(airport, places)
    if not town or not station or town == station:
        return None
    start, end = (town, station) if to_airport else (station, town)
    body = {"origin": start, "destination": end, "departureDate": f"{day[8:10]}/{day[5:7]}/{day[0:4]}", "returnDate": "", "routeID": "",
            "isOpen": 0, "busplusCardNumber": "", "busplusDocumentIdentifier": "", "promotionalCode": "", "clientIP": "", "changeTicketData": "",
            "voucherNumber": "", "voucherFamilyDiscountType": 0, "voucherFamilyDiscountNumber": "",
            "chosenPassengers": [{"order": 1, "type": 1, "age": 0, "pmr": 0}]}
    answer = http.post(f"{MOVELIA}/v1/SchedulesFromText", json=body, headers=movelia_headers(), timeout=60)
    if answer.status_code == 401:
        MOVELIA_SESSION.pop("token", None)   # the token expired: a new one, once
        answer = http.post(f"{MOVELIA}/v1/SchedulesFromText", json=body, headers=movelia_headers(), timeout=60)
    found = (answer.raise_for_status().json().get("schedules") or {}).get("responseData") or {}
    sections = ((found.get("travel") or {}).get("outbound") or {}).get("section") or []
    rides = []
    for ride in (ride for section in sections for ride in section.get("combination") or []):
        at_airport = ride.get("destinationName" if to_airport else "originName") or ""
        if (ride.get("salable") != "1" or ride.get("direct") != "1" or ride.get("busOrBoat") not in MOVELIA_GROUND
                or not MOVELIA_AIRPORT_STATION.search(plain(at_airport))
                or ride.get("departureDate") != body["departureDate"] or not ride.get("basicPrice")):
            continue
        arrive = to_minutes(ride["arrivalTime"]) + (24 * 60 if ride.get("arrivalDate") != ride["departureDate"] else 0)
        train = ride.get("busOrBoat") == MOVELIA_TRAIN
        company = (ride.get("companyName") or "").title()
        rides.append({
            "grade": "A", "source": "Movelia", "service_day": day,
            "leave": to_minutes(ride["departureTime"]), "arrive": arrive, "price_eur": float(ride["basicPrice"].replace(",", ".")),
            "steps": [{"time": ride["departureTime"], "arrive": ride["arrivalTime"], "line": company, "agency": company,
                       "from": ride.get("originName") or start, "to": ride.get("destinationName") or end,
                       "rail": train, "coach": not train, "km": airport["km"]}],
            "urls": ["https://www.movelia.es/es/app"],
        })
    unique = {(ride["leave"], ride["arrive"], ride["steps"][0]["from"], ride["steps"][0]["agency"]): ride for ride in rides}
    return sorted(unique.values(), key=lambda ride: ride["leave"])

# Obilet, the booking site of the coach companies of Turkey, which also sells those of the Balkans, Greece (the KTEL)
# and Romania: the countries where published timetables are fewest (none in Transitous for Turkey, Montenegro, North
# Macedonia, Kosovo, Albania or the Greek coaches on 2026-10-21). It is read with the requests its own site makes: the page of a search opens a
# session, and the journeys come as JSON. Its places are named in Turkish ("Ohri", "Burgaz"), each with its country.
# The airports of Turkey and of Bucharest are places of their own; elsewhere a coach leaves from the city, and the
# way between the airport and the city's coach station, in no timetable, is estimated as in a mixed leg
# (timetables.py): up to max_estimate_km, and the option is then not verified (C).
OBILET = "https://www.obilet.com"
OBILET_BROWSER = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}   # it refuses a program
OBILET_COUNTRIES = {"TR": "", "ME": "karadag", "MK": "makedonya", "XK": "kosova", "AL": "arnavutluk", "BA": "bosna-hersek",
                    "RS": "sirbistan", "BG": "bulgaristan", "RO": "romanya", "GR": "yunanistan", "HR": "hirvatistan",
                    "SI": "slovenya", "IT": "italya", "PL": "polonya", "MD": "moldova"}   # where it is asked, and the country as it writes it
# Italy's national coaches (Itabus, MarinoBus), Poland's private ones (Szwagropol, Majer) and Moldova's are in no
# published timetable either: Obilet sells them
OBILET_AIRPORT = re.compile(r"havaliman|airport|aeroport")
OBILET_STATION = re.compile(r"(?:main|central|centralna|glavna) bus station|otogar|avtobusna postaja|autobusni kolodvor")   # a town's own station, named without the town
OBILET_TIMEOUT = 45   # its searches in Bulgaria and Greece can take this long
OBILET_SPACING_SECONDS = 1.0   # it closes the connection to a client that asks faster: one request at a time, this far apart
OBILET_SESSION: dict[str, Any] = {}
OBILET_LOCK = threading.Lock()

def obilet_ask(method: str, url: str, **options) -> httpx.Response:
    """One request to it, in its session, after the one before has had its time."""
    with OBILET_LOCK:
        if "client" not in OBILET_SESSION:
            OBILET_SESSION["client"] = httpx.Client(headers=OBILET_BROWSER, timeout=OBILET_TIMEOUT, follow_redirects=True)
        time.sleep(max(0.0, OBILET_SESSION.get("last", 0.0) + OBILET_SPACING_SECONDS - time.time()))
        try:
            return OBILET_SESSION["client"].request(method, url, **options).raise_for_status()
        finally:
            OBILET_SESSION["last"] = time.time()

def obilet_places(name: str) -> list[dict[str, Any]]:
    key = plain(name)
    if key not in OBILET_SESSION.setdefault("places", {}):
        found = obilet_ask("POST", f"{OBILET}/json/duraklar", json={"query": name}).json()
        OBILET_SESSION["places"][key] = found.get("items") or []
    return OBILET_SESSION["places"][key]

def obilet_words(text: str) -> set:
    """The words of a name that can tell one place from another."""
    return set(re.split(r"[^a-z0-9]+", plain(text.replace("ı", "i")))) - {"", "airport", "international", "havalimani", "de", "del", "la", "di"}

def obilet_place(names: list[str], country: str, airport: bool) -> dict[str, Any] | None:
    """Its place for a city (or for an airport) of a country, by name: the first it answers that is in that country.
    For an airport, the one whose name shares most words with the airport's (Istanbul has two)."""
    written = OBILET_COUNTRIES[country]
    country_of = lambda item: plain(item["text"].split(" - ")[-1].replace("ı", "i"))   # Turkish ı is not an accented i: plain() drops it
    in_country = lambda item: item.get("isDomestic") if not written else written in country_of(item)
    kept = lambda items: [item for item in items if in_country(item) and bool(OBILET_AIRPORT.search(plain(item["text"]))) == airport]
    for name in (name for name in names if plain(name)):   # a name in another alphabet (Ρέθυμνο) it answers at random
        found = kept(obilet_places(name))
        if found and airport:
            # The one whose name shares most words with the airport's, and at least one: Rome has two airports
            words = obilet_words(" ".join(names))
            score = lambda item: len(words & obilet_words(item["text"]))
            best = max(found, key=lambda item: (score(item), -len(item["text"])))
            if score(best):
                return best
            continue
        if found:
            # A district answers for its city ("Agia Triada Rethymno - Resmo, Yunanistan"): the city itself is meant
            parent = found[0]["text"].split(" - ")[-1].split(",")[0] if "," in found[0]["text"].split(" - ")[-1] else ""
            city = [item for item in kept(obilet_places(parent)) if plain(item["display"]) == plain(parent)] if parent else []
            return city[0] if city else found[0]
    return None

def obilet_rides(city: Place, airport: dict[str, Any], day: str, to_airport: bool = True) -> list[dict[str, Any]] | None:
    """Every coach it sells between a city and an airport of the countries above on one day (yyyy-mm-dd), with its
    price in EUR. From the airport's own place first; when it has none, or nothing leaves from it, from the coach
    station of the airport's city, with the way between the two estimated. Returns None when it does not know the
    city, or neither the airport nor the airport's city."""
    from trip_planner.itineraries.legs import ROAD_FACTOR   # legs.py reads this module: imported when used
    from trip_planner.places import geocode
    if airport["country"] not in OBILET_COUNTRIES:
        return None
    town = obilet_place([city.local_name, city.english_name], airport["country"], False)
    if not town:
        return None
    # The airport's city: the first word of its name (Skopje's airport is in Petrovec, Athens's in Spata), then the
    # database's. It answers a name it does not know with the nearest it does (Spata is "Sparta", 150 km away): the
    # database's small town is only taken when its answer starts like it. The first word is a city it knows under
    # its own name (Athens is "Atina").
    first, municipality = airport["name"].split()[0], airport["city"]
    tries = []
    station = obilet_place([airport["name"], first, municipality], airport["country"], True)
    if station:
        tries.append((station, 0.0))
    for name, checked in ((first, False), (municipality, True)):
        place = obilet_place([name], airport["country"], False)
        if not place or (checked and plain(place["display"])[:4] != plain(name)[:4]):
            continue
        centre = geocode(name)
        km = distance_km(airport["latitude"], airport["longitude"], centre.latitude, centre.longitude) * ROAD_FACTOR
        if km <= current.SETTINGS.max_estimate_km and place["id"] not in (other["id"] for other, _ in tries):
            tries.append((place, km))
    tries = [(station, km) for station, km in tries if station["id"] != town["id"]]
    for station, km in tries:
        rides = obilet_search(town, station, km, airport, day, to_airport, (city.local_name, city.english_name))
        if rides:
            return rides
    return [] if tries else None

def obilet_search(town: dict[str, Any], station: dict[str, Any], stretch_km: float, airport: dict[str, Any], day: str,
                  to_airport: bool, names: tuple = ()) -> list[dict[str, Any]]:
    """Its journeys between the traveller's town and a place at the airport's end, as rides. stretch_km is the way,
    in no timetable, between that place and the airport: 0 when the place is the airport. names are the town's own
    names, to know its stops."""
    from trip_planner.itineraries.legs import ROAD_FACTOR, estimated_minutes, estimated_price
    start, end = (town, station) if to_airport else (station, town)
    page = f"{OBILET}/seferler/{start['id']}-{end['id']}/{day}"
    if not OBILET_SESSION.get("opened"):
        obilet_ask("GET", page)   # the page of a search opens the session its journeys are given in
        OBILET_SESSION["opened"] = True
    found = obilet_ask("POST", f"{OBILET}/json/journeys/{start['id']}-{end['id']}/{day}", json={}, headers={"Referer": page}).json()
    town_names = [plain(name) for name in (town["display"], *names) if plain(name)]
    airport_words = obilet_words(f"{airport.get('name', '')} {airport.get('city', '')} {airport['code']}")
    stretch_minutes = estimated_minutes(stretch_km / ROAD_FACTOR) + 20 if stretch_km else 0   # 20: to find the bus or taxi
    stretch_price = estimated_price(stretch_km / ROAD_FACTOR, airport["country"]) if stretch_km else 0.0
    rides = []
    for item in found.get("journeys") or []:
        journey = item.get("journey") or {}
        departure, arrival = journey.get("departure") or "", journey.get("arrival") or ""
        # Its own price in EUR (sorting-price) where the European Central Bank has no rate: dinars, denars, leks
        price = in_eur(float(journey.get("internet-price") or 0), journey.get("currency") or "EUR") or journey.get("sorting-price")
        if departure[:10] != day or not price or not item.get("is-active", True) or not item.get("available-seats", 1):
            continue
        at_airport = journey.get("destination" if to_airport else "origin") or ""
        # A stop of this very airport needs no estimated way to it, whatever place was asked
        at_this_airport = bool(OBILET_AIRPORT.search(plain(at_airport))) and bool(obilet_words(at_airport) & airport_words)
        if not stretch_km and not OBILET_AIRPORT.search(plain(at_airport)):
            continue   # a place of an airport also answers with stops of its district (Sabiha Gökçen with Ataşehir)
        at_town = plain(journey.get("origin" if to_airport else "destination") or "")
        if not (any(name[:5] in at_town for name in town_names) or OBILET_STATION.search(at_town)):
            continue   # a town also answers with stops of the towns around it (Nafplion with Nemea, 45 km away)
        leave, arrive = to_minutes(departure[11:16]), to_minutes(arrival[11:16]) + (24 * 60 if arrival[:10] > day else 0)
        company = item.get("partner-name") or "Obilet"
        steps = [{"time": departure[11:16], "arrive": arrival[11:16], "line": company, "agency": company, "from": journey.get("origin") or start["display"],
                  "to": journey.get("destination") or end["display"], "rail": journey.get("kind") == "Train", "coach": journey.get("kind") != "Train",
                  "km": airport["km"]}]
        ride = {"grade": "A", "source": "Obilet", "service_day": day, "leave": leave, "arrive": arrive, "price_eur": price, "urls": [page]}
        if stretch_km and not at_this_airport:
            # The estimated way at the airport's end: after the coach towards the airport, before it from the airport
            code, coach_end = airport["code"], at_airport or station["display"]
            at = arrive if to_airport else leave - stretch_minutes
            estimated = {"time": to_time(at), "arrive": to_time(at + stretch_minutes), "line": tr("estimated_stretch"), "agency": "",
                         "from": coach_end if to_airport else code, "to": code if to_airport else coach_end, "rail": False, "coach": False,
                         "km": round(stretch_km), "estimated": True, "minutes": stretch_minutes}
            steps = [*steps, estimated] if to_airport else [estimated, *steps]
            ride.update(grade="C", leave=leave if to_airport else at, arrive=at + stretch_minutes if to_airport else arrive,
                        price_eur=round(price + stretch_price, 2), confirmed_eur=price, estimated_km=round(stretch_km),
                        notes=[tr("note_estimated_stretch", km=round(stretch_km), start=estimated["from"], end=estimated["to"])])
        rides.append({**ride, "steps": steps})
    unique = {(ride["leave"], ride["arrive"], ride["steps"][0]["agency"]): ride for ride in rides}
    return sorted(unique.values(), key=lambda ride: ride["leave"])

# Operators that can list everything they run between a city and an airport on one day.
# To support another operator, write a function like flixbus_rides and add it here.
RIDE_SOURCES = {"FlixBus": flixbus_rides, "Rede Expressos": rede_expressos_rides, "Movelia": movelia_rides, "Obilet": obilet_rides}

OEBB = "https://shop.oebbtickets.at/api"
OEBB_PASSENGER = {"type": "ADULT", "id": 1, "me": False, "remembered": False, "relations": [], "cards": [], "isSelected": False,
                  "challengedFlags": {"hasHandicappedPass": False, "hasAssistanceDog": False, "hasWheelchair": False, "hasAttendant": False},
                  "birthdateChangeable": True, "birthdateDeletable": True, "nameChangeable": True, "passengerDeletable": True}
OEBB_FILTER = {"regionaltrains": False, "direct": False, "changeTime": False, "wheelchair": False, "bikes": False, "trains": False,
               "motorail": False, "droppedConnections": False}

def oebb_fare(trip: Trip) -> float | None:
    """ÖBB, in Austria. Its shop also prices other countries, at the international tariff, which nobody pays there."""
    session = {"AccessToken": http.get(f"{OEBB}/domain/v4/init").raise_for_status().json()["accessToken"], "Channel": "inet"}

    def station(name: str, at: tuple) -> dict[str, Any] | None:
        found = http.get(f"{OEBB}/hafas/v1/stations", params={"name": name, "count": 5}, headers=session).raise_for_status().json()
        near = [s for s in found if not at or distance_km(at[0], at[1], s["latitude"] / 1e6, s["longitude"] / 1e6) <= SAME_STATION_KM]
        return near[0] if near else None

    start, end = station(trip.start, trip.start_at), station(trip.end, trip.end_at)
    if not start or not end:
        return None
    asked_from = to_minutes(trip.leave) - 5
    found = http.post(f"{OEBB}/hafas/v4/timetable", headers=session, json={
        "reverse": False, "datetimeDeparture": f"{trip.day}T{max(asked_from, 0) // 60:02d}:{max(asked_from, 0) % 60:02d}:00.000",
        "filter": OEBB_FILTER, "passengers": [OEBB_PASSENGER], "count": 5, "from": start, "to": end, "timeout": {},
    }).raise_for_status().json()
    same = [c["id"] for c in found.get("connections") or [] if trip.matches(c["from"]["departure"], c["to"]["arrival"])]
    if not same:
        return None
    offers = http.get(f"{OEBB}/offer/v1/prices", headers=session,
                      params=[("connectionIds[]", same[0]), ("sortType", "DEPARTURE"), ("bestPriceId", "undefined")]).raise_for_status().json()
    price = next((offer.get("price") for offer in offers.get("offers") or [] if offer.get("connectionId") == same[0]), None)
    return float(price) if price else None

TRENITALIA = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website"

def trenitalia_fare(trip: Trip) -> float | None:
    """Trenitalia and the regional railways it sells (Trenord), in Italy."""
    def station(name: str) -> int | None:
        found = http.get(f"{TRENITALIA}/locations/search", params={"name": name, "limit": 5}).raise_for_status().json()
        exact = [s for s in found if plain(s["name"]) == plain(name)]
        return (exact or found or [{}])[0].get("id")

    start, end = station(trip.start), station(trip.end)
    if not start or not end:
        return None
    asked_from = max(to_minutes(trip.leave) - 5, 0)
    found = http.post(f"{TRENITALIA}/ticket/solutions", json={
        "departureLocationId": start, "arrivalLocationId": end, "adults": 1, "children": 0,
        "departureTime": f"{trip.day}T{asked_from // 60:02d}:{asked_from % 60:02d}:00.000",
        "criteria": {"frecceOnly": False, "regionalOnly": False, "noChanges": False, "order": "DEPARTURE_DATE", "limit": 6, "offset": 0},
        "advancedSearchRequest": {"bestFare": False},
    }).raise_for_status().json()
    for solution in (found_solution["solution"] for found_solution in found.get("solutions") or []):
        if trip.matches(solution["departureTime"], solution["arrivalTime"]):
            price = (solution.get("price") or {}).get("amount")
            return float(price) if price else None
    return None

MAV = "https://jegy-a.mav.hu/IK_API_PROD/api/OfferRequestApi"
MAV_SESSION = {"UserSessionId": "''", "Language": "en"}
MAV_STATIONS: dict[str, str] = {}

def mav_fare(trip: Trip) -> float | None:
    """MÁV, in Hungary. Prices are in forints."""
    if not MAV_STATIONS:
        found = http.post(f"{MAV}/GetStationList", json={}, headers=MAV_SESSION).raise_for_status().json()
        MAV_STATIONS.update({plain(station["name"]): station["code"] for station in found["stations"] if station.get("canUseForOfferRequest")})
    start, end = MAV_STATIONS.get(plain(trip.start)), MAV_STATIONS.get(plain(trip.end))
    if not start or not end:
        return None
    asked_from = max(to_minutes(trip.leave) - 5, 0)
    moment = f"{trip.day}T{asked_from // 60:02d}:{asked_from % 60:02d}:00"
    found = http.post(f"{MAV}/GetOfferRequest", headers=MAV_SESSION, json={
        "offerkind": "1", "startStationCode": start, "endStationCode": end, "innerStationsCodes": [],
        "passangers": [{"passengerCount": 1, "passengerId": 0, "customerTypeKey": "HU_44_025-065", "customerDiscountsKeys": []}],
        "isOneWayTicket": True, "isTravelEndTime": False, "isSupplementaryTicketsOnly": False,
        "travelStartDate": moment, "travelReturnDate": moment, "selectedServices": [52],
        "selectedSearchServices": [], "eszkozSzamok": [], "isOfDetailedSearch": False,
    }).raise_for_status().json()
    prices = [(route.get("travelClasses") or [{}])[0].get("price") for route in found.get("route") or []
              if trip.matches(route["departure"]["time"], route["arrival"]["time"])]
    prices = [price for price in prices if price and price.get("amount")]
    if not prices:
        return None
    cheapest = min(prices, key=lambda price: price["amount"])   # the same train is offered with and without a seat reservation
    return in_eur(float(cheapest["amount"]), cheapest["currency"]["name"])

ELRON = "https://api.ridango.com/v2/64/intercity"
ELRON_STOPS: dict[str, str] = {}

def elron_fare(trip: Trip) -> float | None:
    """Elron, the trains of Estonia. Direct trains only: that is what its search answers."""
    if not ELRON_STOPS:
        ELRON_STOPS.update({plain(stop["stop_name"]): stop["stop_area_id"] for stop in http.get(f"{ELRON}/originstops").raise_for_status().json()})
    start, end = ELRON_STOPS.get(plain(trip.start)), ELRON_STOPS.get(plain(trip.end))
    if not start or not end:
        return None
    found = http.put(f"{ELRON}/stopareas/trips/direct", json={
        "date": trip.day, "origin_stop_area_id": start, "destination_stop_area_id": end, "channel": "web"}).raise_for_status().json()
    for ride in (ride for journey in found.get("journeys") or [] for ride in journey.get("trips") or []):
        if trip.matches(ride["departure_time"], ride["arrival_time"]):
            price = (ride.get("product") or {}).get("price")
            return float(price) if price else None
    return None

REGIOJET = "https://brn-ybus-pubapi.sa.cz/restapi"
REGIOJET_STATIONS: list[tuple] = []

def regiojet_fare(trip: Trip) -> float | None:
    """RegioJet, trains and coaches in Czechia, Slovakia and their neighbours."""
    if not REGIOJET_STATIONS:
        countries = http.get(f"{REGIOJET}/consts/locations").raise_for_status().json()
        REGIOJET_STATIONS.extend((station["latitude"], station["longitude"], station["id"]) for country in countries
                                 for city in country["cities"] for station in city["stations"] if station.get("latitude"))

    def station(at: tuple) -> int | None:
        if not at:
            return None
        km, identity = min((distance_km(at[0], at[1], latitude, longitude), identity) for latitude, longitude, identity in REGIOJET_STATIONS)
        return identity if km <= SAME_STATION_KM else None

    start, end = station(trip.start_at), station(trip.end_at)
    if not start or not end:
        return None
    found = http.get(f"{REGIOJET}/routes/search/simple", headers={"X-Currency": "EUR"}, params={
        "fromLocationId": start, "toLocationId": end, "fromLocationType": "STATION", "toLocationType": "STATION",
        "departureDate": trip.day, "tariffs": "REGULAR"}).raise_for_status().json()
    for route in found.get("routes") or []:
        if route.get("bookable", True) and trip.matches(route["departureTime"], route["arrivalTime"]):
            return float(route["priceFrom"]) if route.get("priceFrom") else None
    return None

# ALSA, the largest coach company of Spain. Its timetable is published (in Transitous), its prices only on its site,
# which is behind a protection that refuses plain programs: it is read with a client that looks like a browser, as
# google_flights.py does. A search is kept by the site in the session (the purchase page, then its list of
# journeys), so the searches are made one at a time. The stop ids of the published timetable hold the site's own:
# "es-ALSA-autobuses_0000555500000001" is its stop 5555 (Madrid airport T4).
ALSA = "https://www.alsa.es"
ALSA_STOP = "es-ALSA-autobuses_"
ALSA_SESSION: dict[str, Any] = {}
ALSA_LOCK = threading.Lock()

def alsa_stop(stop_id: str) -> str:
    return str(int(stop_id[len(ALSA_STOP):][:8]))

def alsa_fare(trip: Trip) -> float | None:
    """ALSA: the cheapest fare on sale for the very departure, in Spain. Raises NotRunning when ALSA lists journeys
    between the two stops that day and none at those times."""
    with ALSA_LOCK:
        if not ALSA_SESSION:
            client = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True, follow_redirects=True, timeout=TIMEOUT)
            home = client.get(f"{ALSA}/").text
            ALSA_SESSION.update(client=client, search=re.search(r'purchasePortletSearchJourneysURL"\] = \'([^\']+)\'', home).group(1))
        client = ALSA_SESSION["client"]
        purchase = client.get(ALSA_SESSION["search"], params={
            "originStationId": alsa_stop(trip.start_id), "destinationStationId": alsa_stop(trip.end_id),
            "departureDate": f"{trip.day[8:10]}/{trip.day[5:7]}/{trip.day[0:4]}", "returnDate": "", "seats": "1", "travelType": "OUTWARD",
            "promoCode": "", "youngPromoCode": "", "employee": "false", "alsaspam": "", "passengerType-ADULT": "1"})
        listing = re.search(r'getJourneyListResourceURL"\] = \'([^\']+)\'', purchase.text)
        if not listing:
            ALSA_SESSION.clear()   # the session expired: a new one next time
            return None
        found = json.loads(client.get(listing.group(1), headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}).text)
    journeys = found.get("journeys") or []
    for journey in journeys:
        prices = [fare["price"] for fare in journey.get("fares") or [] if fare.get("price")]
        if journey.get("canBeSold") == "V" and prices and trip.matches(journey["departureTime"], journey["arrivalTime"]):
            return float(min(prices))
    if journeys and not any(trip.matches(journey["departureTime"], journey["arrivalTime"]) for journey in journeys):
        raise NotRunning
    return None

def luxembourg_fare(trip: Trip) -> float | None:
    """Public transport inside Luxembourg is free for everyone, in second class."""
    return 0.0

# ---------------------------------------------------------------- which operator prices a vehicle
LUXEMBOURG_STOPS = ("lu-", "be-sncb_82")   # Luxembourg's own timetable, and its stations in the Belgian one (82 is its railway code)

@dataclass(frozen=True)
class FareSource:
    name: str                              # shown in the report
    country: str                           # where it prices: a country code, or "EU" for an operator of many
    sells: Callable[[dict[str, Any]], bool]    # whether a vehicle of a connection is one of its own
    fare: Callable[[Trip], float | None]
    through: bool = False                  # one ticket covers several of its vehicles in a row
    checked: str = ""                      # a published tariff, not a booking system: the day it was read (yyyy-mm-dd)
    confirms: bool = False                 # its price is only given for a departure it sells: finding one confirms the timetable

    @property
    def tariff(self) -> bool:
        return bool(self.checked)

    @property
    def expired(self) -> bool:
        """A tariff read too long ago is no longer believed: prices change every year, some more often."""
        return self.tariff and (date.today() - date.fromisoformat(self.checked)).days > TARIFF_DAYS

agency = lambda step: plain(step.get("agency") or "")

# ---------------------------------------------------------------- published tariffs
# City networks sell one ticket at one price, whatever the stops: no booking system to ask, the price is the one
# the network publishes. Each tariff below was read on the page given, on the day given, and stops being used
# TARIFF_DAYS later: the vehicles then go back to an estimate until someone reads the page again and updates it.
# A ticket valid for a time covers the vehicles of the network in a row, as long as they fit in that time.
TARIFF_DAYS = 365
PRAGUE = (50.0755, 14.4378)
PRAGUE_KM = 17   # the airport is 11 km from the centre: further than this is another zone of the region, and another price

def timed(prices: list[tuple], currency: str = "EUR") -> Callable[[Trip], float | None]:
    """A tariff of tickets valid for a time: [(minutes, price)], shortest first. None when the ride is longer than all."""
    def fare(trip: Trip) -> float | None:
        price = next((price for minutes, price in prices if 0 <= trip.minutes <= minutes), None)
        return in_eur(price, currency) if price is not None else None
    return fare

def prague_fare(trip: Trip) -> float | None:
    in_prague = all(at and distance_km(at[0], at[1], *PRAGUE) <= PRAGUE_KM for at in (trip.start_at, trip.end_at))
    return timed([(30, 39), (90, 50)], "CZK")(trip) if in_prague else None

def budapest_fare(trip: Trip) -> float | None:
    """One ticket per vehicle. The airport bus 100E has its own."""
    return in_eur(2500 if trip.line.strip().upper() == "100E" else 500, "HUF")

TARIFFS = [
    # https://pid.cz/en/new-pid-tariff-from-1-january-2026-dont-pay-more-than-you-have-to/ : paper tickets, zones P, 0 and B
    FareSource("PID Prague (published tariff)", "CZ", lambda step: agency(step) == "prazska integrovana doprava", prague_fare, through=True, checked="2026-10-13"),
    # https://bkk.hu/en/tickets-and-passes/prices/ : single ticket 500 Ft, airport shuttle bus single ticket 2 500 Ft
    FareSource("BKK Budapest (published tariff)", "HU", lambda step: agency(step) == "bkk", budapest_fare, checked="2026-10-13"),
    # https://www.vrt.be/vrtnws/en/2026/01/27/brussels-public-transport-company-to-increase-fare-prices-from-1 : 2.40 from 1 February 2026.
    # Line 12 to the airport has a dearer ticket, which was not read: it is left to the estimate
    FareSource("STIB Brussels (published tariff)", "BE", lambda step: agency(step) == "stib" and str(step.get("line")) != "12",
               timed([(60, 2.40)]), through=True, checked="2026-10-13"),
    # https://www.vrt.be/vrtnws/en/2026/01/02/de-lijn-to-increase-its-fares-from-february : the single ticket stays at 3.00, 60 minutes
    FareSource("De Lijn (published tariff)", "BE", lambda step: agency(step) == "de lijn", timed([(60, 3.00)]), through=True, checked="2026-10-13"),
    # https://www.belgiantrain.be/en/tickets-and-railcards/bus-ticket-charleroi-airport : the bus between the airport and Charleroi station
    FareSource("TEC Charleroi Airport (published tariff)", "BE", lambda step: agency(step) == "letec" and str(step.get("line")) == "A1",
               lambda trip: 6.00, checked="2026-10-13"),
    # https://www.metrolisboa.pt/wp-content/uploads/2025/12/Tarifas_site_2026_PT.pdf : Carris/Metro ticket, one hour on both networks
    FareSource("Carris/Metro Lisbon (published tariff)", "PT", lambda step: agency(step) in ("metropolitano de lisboa, e.p.e.", "carris"),
               timed([(60, 1.90)]), through=True, checked="2026-10-13"),
]

FARE_SOURCES = [
    FareSource("Luxembourg (free public transport)", "LU",
               lambda step: all((step.get(end) or "").startswith(LUXEMBOURG_STOPS) for end in ("from_id", "to_id")), luxembourg_fare, through=True),
    FareSource("FlixBus", "EU", lambda step: all((step.get(end) or "").startswith("eu-flixbus_") for end in ("from_id", "to_id")), flixbus_fare, confirms=True),
    FareSource("RegioJet", "EU", lambda step: agency(step).startswith(("regiojet", "student agency")), regiojet_fare, confirms=True),   # Student Agency runs its coaches
    FareSource("ÖBB", "AT", lambda step: step["rail"] and agency(step).startswith(("oebb", "obb")), oebb_fare, through=True, confirms=True),
    FareSource("Trenitalia", "IT", lambda step: step["rail"] and agency(step) in ("trenitalia", "trenord"), trenitalia_fare, through=True, confirms=True),
    FareSource("MÁV", "HU", lambda step: step["rail"] and (step.get("from_id") or "").startswith("hu-mav_"), mav_fare, through=True, confirms=True),
    FareSource("Elron", "EE", lambda step: step["rail"] and "elron" in agency(step), elron_fare, confirms=True),
    FareSource("ALSA", "ES", lambda step: all((step.get(end) or "").startswith(ALSA_STOP) for end in ("from_id", "to_id")), alsa_fare, confirms=True),
    *TARIFFS,
]

def source_of(step: dict[str, Any]) -> FareSource | None:
    return next((source for source in FARE_SOURCES if not source.expired and source.sells(step)), None)

def runs(steps: list[dict[str, Any]]) -> list[tuple]:
    """The vehicles of a connection grouped into tickets: (operator, positions of the vehicles the ticket covers).
    Vehicles in a row of an operator that sells through tickets are one ticket."""
    found: list[tuple] = []
    for position, step in enumerate(steps):
        source = source_of(step)
        if source is None:
            continue
        if found and source.through and found[-1][0] is source and found[-1][1][-1] == position - 1:
            found[-1][1].append(position)
        else:
            found.append((source, [position]))
    return found

CACHE: dict[tuple, float | None] = {}
NOT_RUNNING: set = set()   # (operator, Trip) whose operator answered, for that day, that the departure does not run
LOCK = threading.Lock()

def fare(source: FareSource, trip: Trip) -> float | None:
    """The price of one ticket, asked once. None when the operator does not answer or does not sell that departure.
    When its answer says the departure does not run that day (NotRunning), the ticket is noted in NOT_RUNNING."""
    key = (source.name, trip)
    with LOCK:
        if key in CACHE:
            return CACHE[key]
    try:
        if source.tariff:
            price = source.fare(trip)   # a published tariff is worked out here: nothing is asked of anyone
        else:
            with logged(source.name, f"fare {trip.start} -> {trip.end}, {trip.day} {trip.leave}") as entry:
                try:
                    price = source.fare(trip)
                except NotRunning:
                    price = None
                    with LOCK:
                        NOT_RUNNING.add(key)
                entry["result"] = ("ok: does not run that day" if key in NOT_RUNNING else "ok: no price" if price is None
                                   else f"ok: {price:.2f} EUR")
    except Exception:
        price = None   # an operator that fails only leaves the estimate in place
    with LOCK:
        CACHE[key] = price
    return price

def trip_of(steps: list[dict[str, Any]], positions: list[int], day: str) -> Trip:
    """The ticket asked of an operator for a run of its vehicles (runs), on one day."""
    first, last = steps[positions[0]], steps[positions[-1]]
    return Trip(first["from"], last["to"], first.get("from_id") or "", last.get("to_id") or "",
                tuple(first.get("from_at") or ()), tuple(last.get("to_at") or ()), day, first["time"], last["arrive"],
                str(first.get("line") or ""))

def not_running(steps: list[dict[str, Any]], day: str) -> list[str]:
    """The operators whose answer for day says that one of these vehicles does not run then. Nothing is asked here:
    it reads what tickets() was answered for the same day."""
    return list(dict.fromkeys(source.name for source, positions in runs(steps)
                              if "time" in steps[positions[0]] and (source.name, trip_of(steps, positions, day)) in NOT_RUNNING))

def tickets(steps: list[dict[str, Any]], travel_day: str, service_day: str | None, skip: Callable[[dict[str, Any]], bool] = lambda step: False) -> list[dict[str, Any]]:
    """The tickets found for the vehicles of a connection: {"positions", "operator", "price_eur", "travel_day", "tariff", "confirms"}.
    Each is asked for the travel date. When the operator does not sell that date yet, for the day the timetable
    was read on, and travel_day is then False. tariff says the price is a published one and not a booking system's
    answer for that departure. confirms says the operator only answers for a departure it sells, at those very
    times. skip leaves out vehicles that cost this traveller nothing."""
    found = []
    for source, positions in runs(steps):
        first, last = steps[positions[0]], steps[positions[-1]]
        if all(skip(steps[position]) for position in positions) or "time" not in first:
            continue
        for day in dict.fromkeys(day for day in (travel_day, service_day) if day):
            if datetime.fromisoformat(day).date() < datetime.now().date():
                continue
            price = fare(source, trip_of(steps, positions, day))
            if price is not None:
                found.append({"positions": positions, "operator": source.name, "price_eur": price, "travel_day": day == travel_day,
                              "tariff": source.tariff, "confirms": source.confirms})
                break
    return found
