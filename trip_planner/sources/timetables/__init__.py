"""Real timetables. A model searching the web can find a travel time and a price, but not whether something
actually leaves at the right time on the travel date. So the ground transport of every flight is looked up
by code in two kinds of sources, and what a model says is only used where neither covers the route.

1. RIDE_SOURCES (fares.py): an operator's own booking system. Real rides and real prices for the day of
   the trip (grade A), to the departure airport and from the arrival airport.
2. Transitous: a free journey planner built on the timetables that operators publish, worldwide. Trains,
   metros, buses, coaches and airport shuttles of every operator that publishes one. No prices. Timetables are
   usually published only a few weeks ahead, so a far travel date is looked up on the same weekday of a nearer
   week (grade B, "typical timetable").

The package, part by part:
- lookup.py: ground_transport_for, where a search starts: the ground transport of every flight, on both sides.
- client.py: Timetables, the requests to Transitous, and the mixed legs where no timetable reaches one of the ends.
- joined.py: an operator's coach joined to a published connection between the airport and its coach station.
- airport_stops.py: where the terminal of each airport is, and which stops travellers use there.
- days.py: which day is read for a travel date, and the local time of each place.
- modes.py: the kinds of vehicle asked for."""
from trip_planner.sources.timetables.airport_stops import (AIRFIELD_WALK_MINUTES, airport_stop_candidates, choose_airport_stops,
                                                         fill_terminals, find_terminals, MIN_STOP_WALK_MINUTES, OVERPASS_SERVERS)
from trip_planner.sources.timetables.client import NOT_COVERED, Timetables
from trip_planner.sources.timetables.days import landing_day, local_to_utc, reference_day, utc_to_local, zone_of_country
from trip_planner.sources.timetables.joined import join_published_ends
from trip_planner.sources.timetables.lookup import flights_to_check, ground_transport_for, TIMETABLE_SOURCES
from trip_planner.sources.timetables.modes import GROUND_MODES

# What the rest of the planner reads as timetables.<name>. A test that replaces one of the module's own names
# replaces it in the module that uses it (client, joined, airport_stops...), not here.
__all__ = ["AIRFIELD_WALK_MINUTES", "airport_stop_candidates", "choose_airport_stops", "fill_terminals", "find_terminals",
           "flights_to_check", "GROUND_MODES", "ground_transport_for", "join_published_ends", "landing_day", "local_to_utc",
           "MIN_STOP_WALK_MINUTES", "NOT_COVERED", "OVERPASS_SERVERS", "reference_day", "TIMETABLE_SOURCES", "Timetables",
           "utc_to_local", "zone_of_country"]
