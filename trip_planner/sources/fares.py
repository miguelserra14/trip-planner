"""Real prices of ground transport. Published timetables carry no prices, so the vehicles of a connection found
in them are priced by asking the booking system of the operator that runs them, where one can be read by code.

A price is only kept when the operator sells that very departure: same stations, same minute of departure and
of arrival. Otherwise there is no price from here, and the leg keeps its estimate from the distance.

FARE_SOURCES lists the operators. None of these is an official service for programs: they are the ones the
operators' own sites use, and they can change without notice. To support another operator, write a function
like oebb_fare and add it there."""
import threading
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from trip_planner.config import USER_AGENT
from trip_planner.places import distance_km
from trip_planner.utils import plain, to_minutes

TIMEOUT = 20
SAME_MINUTES = 2          # an operator's departure is the timetable's when it leaves within this of it...
SAME_ARRIVAL_MINUTES = 6  # ... and arrives within this: the two do not always agree on the minute a train gets in
SAME_STATION_KM = 1.5     # an operator's station is the timetable's stop when it is this close
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

    def matches(self, leave: str, arrive: str) -> bool:
        """Whether a departure of the operator ("...T10:28...", or "10:28") is this trip."""
        clock = lambda moment: to_minutes(moment[11:16] if "T" in moment else moment[:5])
        return (abs(clock(leave) - to_minutes(self.leave)) <= SAME_MINUTES
                and abs(clock(arrive) - to_minutes(self.arrive)) <= SAME_ARRIVAL_MINUTES)

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
def flixbus_fare(trip: Trip) -> float | None:
    """FlixBus, anywhere: the timetable's stop ids are FlixBus's own."""
    ride_date = f"{trip.day[8:10]}.{trip.day[5:7]}.{trip.day[0:4]}"
    found = http.get("https://global.api.flixbus.com/search/service/v4/search", params={
        "from_station_id": trip.start_id.split("_", 1)[1], "to_station_id": trip.end_id.split("_", 1)[1], "search_by": "stations",
        "departure_date": ride_date, "products": '{"adult":1}', "currency": "EUR", "locale": "en", "include_after_midnight_rides": "0",
    }).raise_for_status().json()
    for ride in (ride for found_trip in found.get("trips") or [] for ride in (found_trip.get("results") or {}).values()):
        if ride.get("status") == "available" and trip.matches(ride["departure"]["date"], ride["arrival"]["date"]):
            return float(ride["price"].get("total_with_platform_fee") or ride["price"]["total"])
    return None

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

agency = lambda step: plain(step.get("agency") or "")
FARE_SOURCES = [
    FareSource("Luxembourg (free public transport)", "LU",
               lambda step: all((step.get(end) or "").startswith(LUXEMBOURG_STOPS) for end in ("from_id", "to_id")), luxembourg_fare, through=True),
    FareSource("FlixBus", "EU", lambda step: all((step.get(end) or "").startswith("eu-flixbus_") for end in ("from_id", "to_id")), flixbus_fare),
    FareSource("RegioJet", "EU", lambda step: agency(step).startswith(("regiojet", "student agency")), regiojet_fare),   # Student Agency runs its coaches
    FareSource("ÖBB", "AT", lambda step: step["rail"] and agency(step).startswith(("oebb", "obb")), oebb_fare, through=True),
    FareSource("Trenitalia", "IT", lambda step: step["rail"] and agency(step) in ("trenitalia", "trenord"), trenitalia_fare, through=True),
    FareSource("MÁV", "HU", lambda step: step["rail"] and (step.get("from_id") or "").startswith("hu-mav_"), mav_fare, through=True),
    FareSource("Elron", "EE", lambda step: step["rail"] and "elron" in agency(step), elron_fare),
]

def source_of(step: dict[str, Any]) -> FareSource | None:
    return next((source for source in FARE_SOURCES if source.sells(step)), None)

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
LOCK = threading.Lock()

def fare(source: FareSource, trip: Trip) -> float | None:
    """The price of one ticket, asked once. None when the operator does not answer or does not sell that departure."""
    key = (source.name, trip)
    with LOCK:
        if key in CACHE:
            return CACHE[key]
    try:
        price = source.fare(trip)
    except Exception:
        price = None   # an operator that fails only leaves the estimate in place
    with LOCK:
        CACHE[key] = price
    return price

def tickets(steps: list[dict[str, Any]], travel_day: str, service_day: str | None, skip: Callable[[dict[str, Any]], bool] = lambda step: False) -> list[dict[str, Any]]:
    """The tickets found for the vehicles of a connection: {"positions", "operator", "price_eur", "travel_day"}.
    Each is asked for the travel date. When the operator does not sell that date yet, for the day the timetable
    was read on, and travel_day is then False. skip leaves out vehicles that cost this traveller nothing."""
    found = []
    for source, positions in runs(steps):
        first, last = steps[positions[0]], steps[positions[-1]]
        if all(skip(steps[position]) for position in positions) or "time" not in first:
            continue
        for day in dict.fromkeys(day for day in (travel_day, service_day) if day):
            if datetime.fromisoformat(day).date() < datetime.now().date():
                continue
            price = fare(source, Trip(first["from"], last["to"], first.get("from_id") or "", last.get("to_id") or "",
                                      tuple(first.get("from_at") or ()), tuple(last.get("to_at") or ()), day, first["time"], last["arrive"]))
            if price is not None:
                found.append({"positions": positions, "operator": source.name, "price_eur": price, "travel_day": day == travel_day})
                break
    return found
