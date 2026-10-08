"""The trip to plan. This is the only file you need to edit: every setting and its default is described in
TripSettings (trip_planner/config.py). Then run main.py."""
from trip_planner.config import TripSettings

# EDIT SETTINGS to plan another trip. Only origin, destination and dates are required.
SETTINGS = TripSettings(
    origin="Antuérpia",
    destination="Coimbra",
    dates=["17/12/2026", "18/12/2026"],
    language="pt-PT",           # language of the airport list and the final report: "en" or "pt-PT"
    # Airports are found by distance. Uncomment to adjust the choice with codes, airport names or cities.
    # include_airports=["Paris"],
    # exclude_airports=["Londres", "LUX"],
    earliest_leave="06:30",
    latest_leave="17:00",
    earliest_arrival="",        # empty = any time
    latest_arrival="",          # empty = up to the last connection of the day
    max_stopovers=0,
    min_reliability="B",        # A = confirmed for the travel date, B = typical timetable, C = also unverified estimates
    tiebreak_eur_per_hour=5.0,          # first extra hour of travel
    fallback_saving_eur_per_hour=10.0,  # first extra hour caused by a distant airport
    extra_hour_growth=1.5,              # each further hour costs 1.5 times the previous one
    options_shown=5,
    free_trains_at_destination=True,
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
