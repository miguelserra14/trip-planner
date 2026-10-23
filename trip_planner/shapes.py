"""The dicts that pass between the modules, key by key: a vehicle of a connection, a connection, and what the
timetables found for one side of a flight. At run time they are plain dicts; these types only say what each holds.
A key that only some of them have says so in its comment."""
from typing import Any, TypedDict

# One vehicle of a connection, or a stretch in no timetable that is estimated from the distance ("estimated").
# Written by the timetables (sources/timetables/client.py) and by the operators (sources/operators/).
Step = TypedDict("Step", {
    "time": str,                # when it leaves, HH:MM on the clock of where it leaves
    "arrive": str,              # when it arrives, HH:MM
    "line": str,                # as the operator names it: "IC 523", "FlixBus", or the words of an estimated stretch
    "agency": str,              # the operator, empty for an estimated stretch
    "from": str, "to": str,     # the names of the two stops
    "rail": bool, "coach": bool,
    "km": int,                  # in a straight line
    "mode": str,                # the kind of vehicle as the timetables name it ("REGIONAL_RAIL"): timetables only
    "from_id": str, "to_id": str,             # the timetables' ids of the two stops, for the operators' prices
    "from_at": list[float], "to_at": list[float],   # where the two stops are, latitude and longitude
    "estimated": bool,          # a stretch estimated from the distance
    "road_km": int,             # ... how long it is by road
    "minutes": int,             # ... and how long it takes
}, total=False)

class Connection(TypedDict, total=False):
    """A way between a city and an airport: found in a timetable, sold by an operator, or estimated by a model. The
    times are minutes since the midnight its day starts with: below 0 on the evening before, above 24 hours in the
    night after. The ground legs (itineraries/legs.py) add how it is priced and chosen."""
    leave: int
    arrive: int
    steps: list[Step]           # empty for a model's estimate
    urls: list[str]             # the operators' own sites
    grade: str                  # A confirmed for the travel date, B typical timetable, C not verified
    source: str                 # "timetable", or the operator that sold it
    service_day: str | None     # the day read, yyyy-mm-dd: another week's when the travel date is not published yet
    price_eur: float | None     # None until the ground legs price it
    confirmed_eur: float        # how much of the price an operator confirmed for the travel date
    estimated_km: int           # a stretch of it is estimated from the distance (a mixed leg): how long, by road
    notes: list[str]            # for the traveller, in their language
    # Added by the ground legs
    kind: str                   # "timetable", "operator" or "model"
    price_kind: str             # how the price is known: "operator", "free", "tariff", "estimate", "model"...
    country: str                # where it runs, for the estimated prices
    tickets: list[dict[str, Any]]   # what the operators sell its vehicles for (fares.py)
    confirmed_by: list[str]     # the operators whose sale confirms it for the travel date (grade A)
    not_running: list[str]      # the operators that say it does not run that day
    text: str                   # a model's estimate, in its words
    later: list[str]            # from the airport: the next ones that leave, HH:MM
    other: "Connection | None"  # from the airport: the best by the other kind of vehicle (coach or train)
    other_by_coach: bool

class Answer(TypedDict, total=False):
    """What the timetables found for one side of a flight: to its departure airport, or from its arrival airport."""
    covered: bool               # whether any timetable covers the way at all
    service_day: str | None     # the day read, as in Connection
    grade: str                  # the worst grade of the options
    options: list[Connection]
    choice_excluded: bool       # covered, but by nothing the traveller chose (ground_choice.py)
