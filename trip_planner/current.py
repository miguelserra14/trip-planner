"""The trip being planned. SETTINGS and TRIP change with every trip, so the other modules always read them from
here (`current.SETTINGS`, `current.TRIP`) instead of importing the values."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trip_planner.trip.trip_settings import TripSettings
    from trip_planner.trip.places import TripPlaces

# The settings of the trip. Set by main.py, then by plan_trip for each trip; read by the tools, the checks and the texts.
SETTINGS: "TripSettings | None" = None
# The places of the trip: the two cities and its airports. Set by plan_trip, read by the tools, the checks and the report.
TRIP: "TripPlaces | None" = None
