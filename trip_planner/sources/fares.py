"""Real prices of ground transport. Published timetables carry no prices, so the vehicles of a connection found
in them are priced by asking the booking system of the operator that runs them, where one can be read by code.

A price is only kept when the operator sells that very departure: same stations, same minute of departure and
of arrival. Otherwise there is no price from here, and the leg keeps its estimate from the distance.

FARE_SOURCES lists the operators. None of these is an official service for programs: they are the ones the
operators' own sites use, and they can change without notice. To support another operator, write a function
like oebb_fare and add it there.

RIDE_SOURCES lists the operators that can also say everything they run between a city and an airport on one day:
rides the timetables may not have, each with its price and confirmed for that day (grade A)."""
import threading
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from trip_planner.config import USER_AGENT
from trip_planner.places import distance_km, Place
from trip_planner.utils import logged, plain, to_minutes

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
    line: str = ""        # the line of the first vehicle, for the tariffs that price one line apart

    @property
    def minutes(self) -> int:
        return to_minutes(self.arrive) - to_minutes(self.leave)

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
FLIXBUS = "https://global.api.flixbus.com/search"
FLIXBUS_PLACES: dict[str, str | None] = {}

def flixbus_search(start: str, end: str, day: str, by: str) -> tuple:
    """What FlixBus sells between two of its stations, or two of its cities (by), on one day (yyyy-mm-dd).
    Returns (the rides that can be bought, the names of the stations by their id)."""
    found = http.get(f"{FLIXBUS}/service/v4/search", params={
        f"from_{by}_id": start, f"to_{by}_id": end, "search_by": "stations" if by == "station" else "cities",
        "departure_date": f"{day[8:10]}.{day[5:7]}.{day[0:4]}", "products": '{"adult":1}', "currency": "EUR", "locale": "en",
        "include_after_midnight_rides": "0",
    }).raise_for_status().json()
    rides = [ride for trip in found.get("trips") or [] for ride in (trip.get("results") or {}).values() if ride.get("status") == "available"]
    return rides, {identity: station.get("name", "") for identity, station in (found.get("stations") or {}).items()}

def flixbus_price(ride: dict[str, Any]) -> float:
    return float(ride["price"].get("total_with_platform_fee") or ride["price"]["total"])

def flixbus_fare(trip: Trip) -> float | None:
    """FlixBus, anywhere: the timetable's stop ids are FlixBus's own."""
    rides, stations = flixbus_search(trip.start_id.split("_", 1)[1], trip.end_id.split("_", 1)[1], trip.day, "station")
    return next((flixbus_price(ride) for ride in rides if trip.matches(ride["departure"]["date"], ride["arrival"]["date"])), None)

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
    if not city_id or not airport_id:
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

# Operators that can list everything they run between a city and an airport on one day.
# To support another operator, write a function like flixbus_rides and add it here.
RIDE_SOURCES = {"FlixBus": flixbus_rides}

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
LOCK = threading.Lock()

def fare(source: FareSource, trip: Trip) -> float | None:
    """The price of one ticket, asked once. None when the operator does not answer or does not sell that departure."""
    key = (source.name, trip)
    with LOCK:
        if key in CACHE:
            return CACHE[key]
    try:
        if source.tariff:
            price = source.fare(trip)   # a published tariff is worked out here: nothing is asked of anyone
        else:
            with logged(source.name, f"fare {trip.start} -> {trip.end}, {trip.day} {trip.leave}") as entry:
                price = source.fare(trip)
                entry["result"] = "ok: no price" if price is None else f"ok: {price:.2f} EUR"
    except Exception:
        price = None   # an operator that fails only leaves the estimate in place
    with LOCK:
        CACHE[key] = price
    return price

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
            price = fare(source, Trip(first["from"], last["to"], first.get("from_id") or "", last.get("to_id") or "",
                                      tuple(first.get("from_at") or ()), tuple(last.get("to_at") or ()), day, first["time"], last["arrive"],
                                      str(first.get("line") or "")))
            if price is not None:
                found.append({"positions": positions, "operator": source.name, "price_eur": price, "travel_day": day == travel_day,
                              "tariff": source.tariff, "confirms": source.confirms})
                break
    return found
