"""Rede Expressos, the national coach network of Portugal."""
import base64
import json
import re
import time
import uuid
from typing import Any

from trip_planner.trip.places import distance_km, Place
from trip_planner.texts import tr
from trip_planner.utils import plain, to_minutes
from trip_planner.sources.operators.base import http, OPERATOR_DATA

# Rede Expressos, the national coach network of Portugal (with RENEX and the other companies it sells for). It
# publishes no timetable (none in Transitous, Transitland or the Mobility Database on 2026-10-21): its rides are
# only in its booking system, read here with the requests its own site makes. The site lists its stops without
# their position: REDE_EXPRESSOS_STOPS_FILE places them, worked out once from the site's points of sale (most are
# at the coach terminal), the airport database and Nominatim.
REDE_EXPRESSOS = "https://webapi.rede-expressos.pt/api/App"
REDE_EXPRESSOS_STOPS_FILE = OPERATOR_DATA / "rede_expressos_stops.json"
REDE_EXPRESSOS_STOPS: list[dict[str, Any]] = (json.loads(REDE_EXPRESSOS_STOPS_FILE.read_text(encoding="utf-8"))
                                              if REDE_EXPRESSOS_STOPS_FILE.exists() else [])
SALES_DAYS = 71   # measured on 2026-10-23, Porto airport -> Coimbra: sold until 2027-01-02
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

def rede_expressos_schedules(start_id: str, end_id: str, day: str) -> list[dict[str, Any]]:
    """Everything it sells between two of its stops on one day (yyyy-mm-dd), as its site gets it."""
    found = http.post(f"{REDE_EXPRESSOS}/OneWayForSelectedDate", headers=rede_expressos_headers(), json={
        "from": start_id, "to": end_id, "date": day, "campaign": "", "isCampaignFirstSearch": False,
        "returnNextMinPrice": False, "passengers": [{"fareTypeId": "1", "quantity": 1}],
    }).raise_for_status().json()
    return found.get("outgoingSchedules") or []

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
        for ride in rede_expressos_schedules(start["id"], end["id"], day):
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
