"""Trenitalia, the Italian railways (Le Frecce, the site its own pages use)."""
from trip_planner.utils import plain, to_minutes
from trip_planner.sources.operators.base import http, Trip

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
