"""RegioJet, trains and coaches in Czechia, Slovakia and their neighbours."""
from trip_planner.places import distance_km
from trip_planner.sources.operators.base import http, SAME_STATION_KM, Trip

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
