"""The trip to plan. This is the only file you need to edit: every setting and its default is described in
TripSettings (trip_planner/trip/trip_settings.py). Then run main.py."""
from trip_planner.trip.trip_settings import TripSettings

# EDIT SETTINGS to plan another trip. Only origin, destination and dates are required.
SETTINGS = TripSettings(
    origin="Antuérpia",
    destination="Coimbra",
    dates=["17/12/2026", "18/12/2026"],
    language="pt-PT",           # language of the airport list and the final report: "en" or "pt-PT"
    # Airports are found by distance. Uncomment to adjust the choice with codes, airport names or cities.
    # include_airports=["Paris"],
    # exclude_airports=["Londres", "LUX"],
    earliest_leave="",          # empty = from midnight
    latest_leave="",            # empty = until the end of the day
    earliest_arrival="",        # empty = any time
    latest_arrival="",          # empty = up to the last connection of the day
    max_stopovers=0,
    min_reliability="B",        # A = confirmed for the travel date, B = typical timetable, C = also unverified estimates
    tiebreak_eur_per_hour=5.0,          # first extra hour of travel
    fallback_saving_eur_per_hour=10.0,  # first extra hour caused by a distant airport
    extra_hour_growth=1.5,              # each further hour costs 1.5 times the previous one
    options_shown=5,
    free_trains_at_origin=False,
    free_trains_at_destination=False,

    # ---- Rules of the program. The values below are the defaults: change one only if you disagree with the rule.
    # Ground legs (trip_planner/itineraries/legs.py)
    max_estimate_km=50,         # when no published timetable covers the way between an airport and its city, a model's
                                # estimate (checked against the distance) stands in for it only up to this many km.
                                # Beyond it the option is left out: a guess over 150 km is not a trip you can take
    small_estimate_eur=5.0,     # an estimated price up to this (a metro ticket) is kept even with allow_estimated_prices=False
    unverified_price_factor=2.0,  # an estimated price counts as this many times itself next to a real one, so a guess only
                                  # beats a real price when it is clearly cheaper
    # Timetables (trip_planner/sources/timetables/)
    city_walk_minutes=40,       # walking accepted between the city centre and the first or last stop (not counted in the trip)
    wide_city_walk_minutes=90,  # ... tried when nothing is found with the walk above, where a city's own transport is not published
    airport_walk_minutes=20,    # walking accepted between the terminal and a stop (counted in the trip)
    airport_floor_minutes=45,   # the closest alternatives may cut min_airport_minutes, but never below this
    night_minutes=240,          # how far a ground connection may cross midnight: after a late landing it may arrive up to
                                # 04:00, and before an early flight one of the evening before (from 20:00) is shown
                                # among the closest alternatives
    # Places (trip_planner/trip/places.py)
    min_trip_km=80,             # an origin and a destination closer than this are not a trip by plane
    city_airport_radius_km=45,  # a city written in the airport settings ("Paris") means every airport this close to it
    ambiguous_importance=0.2,   # when places of the same name differ in importance (0 to 1, from OpenStreetMap) by less
                                # than this, you are asked which one you mean ("Nazaré": Bahia or Leiria)
    # Reports (trip_planner/report/full.py)
    recent_reports=5,           # reports kept in reports/. Older ones are deleted, except those you keep in reports/saved/
)

# More trips to plan in the same run, each with its own settings. Leave the list empty to plan only the trip above.
EXTRA_TRIPS = [
    # TripSettings(
    #     origin="Lisboa",
    #     destination="Gante",
    #     dates=["20/01/2027", "21/01/2027"],
    #     language="pt-PT",
    #     latest_arrival="22:00",
    #     max_stopovers=1,
    # ),
]
