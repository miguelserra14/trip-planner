"""ÖBB, the Austrian railways."""
from typing import Any

from trip_planner.places import distance_km
from trip_planner.utils import to_minutes
from trip_planner.sources.operators.base import http, SAME_STATION_KM, Trip

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
