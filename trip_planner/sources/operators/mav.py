"""MÁV, the Hungarian railways."""
from trip_planner.utils import plain, to_minutes
from trip_planner.sources.operators.base import http, in_eur, Trip

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
