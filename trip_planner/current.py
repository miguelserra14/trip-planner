"""The trip being planned. SETTINGS and TRIP change with every trip, so the other modules always read them from
here (`current.SETTINGS`, `current.TRIP`) instead of importing the values."""
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trip_planner.trip.trip_settings import TripSettings
    from trip_planner.trip.places import TripPlaces

# The settings of the trip. Set by main.py, then by plan_trip for each trip; read by the tools, the checks and the texts.
SETTINGS: "TripSettings | None" = None
# The places of the trip: the two cities and its airports. Set by plan_trip, read by the tools, the checks and the report.
TRIP: "TripPlaces | None" = None

@contextmanager
def trip(settings: "TripSettings", places: "TripPlaces | None" = None):
    """For a while, another trip is the one being planned: Discover looks its destinations up with the planner's
    code as if each were a trip. The trip before is put back afterwards, also when what ran in between failed.
    These are still one pair of globals for the whole process: code running at the same time in another thread
    sees this trip too, so what runs beside a lookup must not read them (discover/session.py)."""
    global SETTINGS, TRIP
    before = SETTINGS, TRIP
    SETTINGS, TRIP = settings, places
    try:
        yield
    finally:
        SETTINGS, TRIP = before
