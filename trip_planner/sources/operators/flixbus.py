"""FlixBus, everywhere in Europe: its timetable is published (Transitous, with FlixBus's own stop ids), its prices
and the rides it sells only in its booking system."""
from typing import Any

from trip_planner.places import distance_km, Place
from trip_planner.utils import plain, to_minutes
from trip_planner.sources.operators.base import http, Trip, NotRunning

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
