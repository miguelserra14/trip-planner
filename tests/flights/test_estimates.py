"""When no timetable covers a ground leg, a model's estimate stands in for it only if the airport is near its city
(max_estimate_km in the settings). Beyond that the leg is left out, however believable the estimate looks. No network."""
import pytest

from tests.flights.test_next_day import found_by_search, trip   # noqa: F401 (trip is the fixture)
from trip_planner import current
from trip_planner.itineraries import legs
from trip_planner.sources import flight_search

TRANSFER = {"reachable": True, "duration_minutes": 40, "price_eur": 6, "price_known": True, "description": "Bus to the airport.",
            "notes": "", "operator_url": ""}

def airport_at(km: int) -> None:
    current.TRIP.primary[0]["km"] = km

def test_near_airport_estimated():
    airport_at(current.SETTINGS.max_estimate_km)
    leg = legs.to_airport_leg(flight_search.register_flight(found_by_search("10:00", "17:10", 610)), {}, TRANSFER)
    assert leg["kind"] == "model" and leg["grade"] == "C"

def test_far_airport_never_estimated():
    airport_at(current.SETTINGS.max_estimate_km + 1)
    with pytest.raises(legs.Rejected, match=str(current.SETTINGS.max_estimate_km)):
        legs.to_airport_leg(flight_search.register_flight(found_by_search("10:00", "17:10", 610)), {}, TRANSFER)

def test_far_airport_not_closest():
    airport_at(150)
    with pytest.raises(legs.Rejected):
        legs.to_airport_leg(flight_search.register_flight(found_by_search("10:00", "17:10", 610)), {}, TRANSFER, closest=True)
