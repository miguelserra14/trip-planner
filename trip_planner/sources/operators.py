"""Operators whose own booking system can be read by code: real rides and real prices for the travel date (grade A)."""
from typing import Any

import httpx

from trip_planner.places import distance_km, Place
from trip_planner.utils import plain, to_minutes

FLIXBUS_API = "https://global.api.flixbus.com/search"
FLIXBUS_PLACES: dict[str, str | None] = {}

def flixbus_city(query: str, latitude: float, longitude: float, max_km: float) -> str | None:
    """The id FlixBus gives to a city or an airport, found by name and checked by position."""
    key = f"{plain(query)}|{max_km}"
    if key not in FLIXBUS_PLACES:
        response = httpx.get(
            f"{FLIXBUS_API}/autocomplete/cities",
            params={"q": query, "lang": "en", "flixbus_cities_only": "false", "stations": "true"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        near = [
            (distance_km(latitude, longitude, city["location"]["lat"], city["location"]["lon"]), city["id"])
            for city in response.json() if city.get("location")
        ]
        near = [candidate for candidate in near if candidate[0] <= max_km]
        FLIXBUS_PLACES[key] = min(near)[1] if near else None
    return FLIXBUS_PLACES[key]

def flixbus_rides(origin: Place, airport: dict[str, Any], day: str) -> list[dict[str, Any]] | None:
    """Every FlixBus ride from the origin city to an airport on one day (yyyy-mm-dd), with its real price.
    Returns None when FlixBus has no stop at that airport, so there is nothing to check."""
    from_id = flixbus_city(origin.english_name, origin.latitude, origin.longitude, 25)
    to_id = (
        flixbus_city(f"{airport['city']} Airport", airport["latitude"], airport["longitude"], 6)
        or flixbus_city(airport["name"], airport["latitude"], airport["longitude"], 6)
    )
    if not from_id or not to_id:
        return None
    ride_date = f"{day[8:10]}.{day[5:7]}.{day[0:4]}"
    response = httpx.get(
        f"{FLIXBUS_API}/service/v4/search",
        params={
            "from_city_id": from_id, "to_city_id": to_id, "departure_date": ride_date, "products": '{"adult":1}',
            "currency": "EUR", "locale": "en", "search_by": "cities", "include_after_midnight_rides": "0",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    stations = data.get("stations") or {}
    station = lambda station_id: (stations.get(station_id) or {}).get("name", "")
    rides = []
    for trip in data.get("trips") or []:
        for ride in (trip.get("results") or {}).values():
            departure, arrival = ride["departure"]["date"], ride["arrival"]["date"]
            if ride.get("status") != "available" or departure[:10] != day or arrival[:10] != day:
                continue
            rides.append({
                "grade": "A",
                "source": "FlixBus",
                "service_day": day,
                "leave": to_minutes(departure[11:16]),
                "arrive": to_minutes(arrival[11:16]),
                "price_eur": float(ride["price"].get("total_with_platform_fee") or ride["price"]["total"]),
                "steps": [{
                    "time": departure[11:16], "arrive": arrival[11:16], "line": "FlixBus", "agency": "FlixBus",
                    "from": station(ride["departure"]["station_id"]), "to": station(ride["arrival"]["station_id"]),
                    "rail": False, "km": airport["km"],
                }],
                "urls": [f"https://shop.flixbus.pt/search?departureCity={from_id}&arrivalCity={to_id}&rideDate={ride_date}&adult=1"],
            })
    return sorted(rides, key=lambda ride: ride["leave"])

# Operators whose own booking system can be read by code: real rides and real prices for the travel date.
# To support another operator, write a function like flixbus_rides and add it here.
OPERATOR_CHECKERS = {
    "FlixBus": flixbus_rides,
}
