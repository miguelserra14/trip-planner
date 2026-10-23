"""Movelia, the booking site of the Spanish coach companies and of the Spanish trains."""
import json
import re
import threading
import time
from typing import Any

from trip_planner.places import Place
from trip_planner.utils import plain, to_minutes
from trip_planner.sources.operators.base import http, NotOnSale, OPERATOR_DATA, Trip

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
SALES_DAYS = 35   # measured on 2026-10-23: Damas (Sevilla -> Huelva) sells about 35 days ahead, Renfe and Monbus months
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

MOVELIA_SEARCHES: dict[tuple, list[dict[str, Any]]] = {}   # its answer for (from, to, day), asked once in a session

def movelia_search(start: str, end: str, day: str) -> list[dict[str, Any]]:
    """Everything it answers between two of its places on one day (yyyy-mm-dd): its "combinations", each a coach, a
    train, a boat or a flight. Raises NotOnSale when it says the day is not on sale yet."""
    key = (start, end, day)
    if key in MOVELIA_SEARCHES:
        return MOVELIA_SEARCHES[key]
    body = {"origin": start, "destination": end, "departureDate": f"{day[8:10]}/{day[5:7]}/{day[0:4]}", "returnDate": "", "routeID": "",
            "isOpen": 0, "busplusCardNumber": "", "busplusDocumentIdentifier": "", "promotionalCode": "", "clientIP": "", "changeTicketData": "",
            "voucherNumber": "", "voucherFamilyDiscountType": 0, "voucherFamilyDiscountNumber": "",
            "chosenPassengers": [{"order": 1, "type": 1, "age": 0, "pmr": 0}]}
    answer = http.post(f"{MOVELIA}/v1/SchedulesFromText", json=body, headers=movelia_headers(), timeout=60)
    if answer.status_code == 401:
        MOVELIA_SESSION.pop("token", None)   # the token expired: a new one, once
        answer = http.post(f"{MOVELIA}/v1/SchedulesFromText", json=body, headers=movelia_headers(), timeout=60)
    if answer.status_code == 400:
        refused = answer.json()
        if refused.get("ShowMsgNotSchedulesYet"):
            raise NotOnSale   # its own word that the day is not on sale yet
        if refused.get("NoSchedules"):
            MOVELIA_SEARCHES[key] = []   # nothing runs between the two that day
            return []
    found = (answer.raise_for_status().json().get("schedules") or {}).get("responseData") or {}
    sections = ((found.get("travel") or {}).get("outbound") or {}).get("section") or []
    MOVELIA_SEARCHES[key] = [ride for section in sections for ride in section.get("combination") or []]
    return MOVELIA_SEARCHES[key]

def movelia_town(stop_name: str, places: dict[str, str]) -> tuple:
    """Its place for a stop of the published timetables, by the longest start of the stop's name it knows
    ("Madrid-Puerta de Atocha" is MADRID, "Barcelona El Clot" BARCELONA), and the stop's other words, which tell its
    station from the town's others. (None, set()) when it knows no start of it."""
    words = [word for word in re.split(r"[^a-z0-9]+", plain(stop_name)) if word]
    for length in range(len(words), 0, -1):
        if " ".join(words[:length]) in places:
            return places[" ".join(words[:length])], set(words[length:])
    return None, set()

def movelia_fare(trip: Trip) -> float | None:
    """A Spanish train (Renfe, Ouigo) or coach of the published timetables, priced by what it sells: the same
    departure and arrival, from a station whose name shares a word with the timetable's."""
    places = movelia_places()
    (start, start_words), (end, end_words) = movelia_town(trip.start, places), movelia_town(trip.end, places)
    if not start or not end or start == end:
        return None
    try:
        combinations = movelia_search(start, end, trip.day)
    except NotOnSale:
        return None
    same_station = lambda words, name: not words or bool(words & set(re.split(r"[^a-z0-9]+", plain(name))))
    for ride in combinations:
        if (ride.get("busOrBoat") in MOVELIA_GROUND and ride.get("salable") == "1" and ride.get("basicPrice")
                and trip.matches(ride.get("departureTime") or "", ride.get("arrivalTime") or "")
                and same_station(start_words, ride.get("originName") or "") and same_station(end_words, ride.get("destinationName") or "")):
            return float(ride["basicPrice"].replace(",", "."))
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
    day_written = f"{day[8:10]}/{day[5:7]}/{day[0:4]}"
    combinations = movelia_search(start, end, day)
    rides = []
    for ride in combinations:
        at_airport = ride.get("destinationName" if to_airport else "originName") or ""
        if (ride.get("salable") != "1" or ride.get("direct") != "1" or ride.get("busOrBoat") not in MOVELIA_GROUND
                or not MOVELIA_AIRPORT_STATION.search(plain(at_airport))
                or ride.get("departureDate") != day_written or not ride.get("basicPrice")):
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
