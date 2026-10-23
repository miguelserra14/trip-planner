"""Elron, the Estonian trains."""
from trip_planner.utils import plain
from trip_planner.sources.operators.base import http, Trip

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
