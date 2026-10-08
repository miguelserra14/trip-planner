"""The settings that define a trip (TripSettings), and where the project's files are."""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from trip_planner.utils import to_minutes

ROOT = Path(__file__).resolve().parent.parent   # the project folder: resources/, reports/ and env/ are in it
load_dotenv(ROOT / "env" / ".env")       # the API keys, before any client is created

# Languages the traveller can read the airport list and the final report in
LANGUAGES = {"en": "English", "pt-PT": "European Portuguese"}
GRADES = "ABC"   # reliability grades, best first. See "Data integrity" in trip_planner/__init__.py.

@dataclass
class TripSettings:
    """Everything that defines a trip. The agents, the checks and the report all read it."""
    origin: str                           # city where the trip starts, in any language
    destination: str                      # city where the trip ends, in any language
    dates: List[str]                      # acceptable travel dates, dd/mm/yyyy
    language: str = "en"                  # language of the airport list and the final report: "en" or "pt-PT"

    # Airports are found automatically by distance from the two cities.
    primary_radius_km: int = 100          # departure airports this close to the origin are "primary"
    fallback_radius_km: int = 300         # departure airports up to this far are "fallback": only worth it when cheaper
    arrival_radius_km: int = 200          # arrival airports this close to the destination
    max_departure_airports: int = 12      # primary airports first, then the large ones, then the closest
    max_arrival_airports: int = 3         # large airports first, then the closest
    # Adjust the automatic choice. Each entry is an airport code ("BRU"), an airport name ("Charleroi")
    # or a city ("Paris" means every airport of Paris).
    include_airports: List[str] = field(default_factory=list)    # airports to add
    exclude_airports: List[str] = field(default_factory=list)    # airports never to use
    # Or replace the automatic choice of a whole group, with the same kind of entries.
    primary_airports: List[str] = field(default_factory=list)
    fallback_airports: List[str] = field(default_factory=list)
    arrival_airports: List[str] = field(default_factory=list)
    confirm_airports: bool = True         # list the airports with checkboxes to drop some, before searching

    # Hard constraints, enforced in code by build_itineraries
    earliest_leave: str = "06:30"         # earliest time leaving the origin city
    latest_leave: str = "17:00"           # latest time leaving the origin city
    min_airport_minutes: int = 90         # minimum time at the airport before the flight departs
    ideal_airport_minutes: int = 120      # time at the airport aimed for when no real timetable is available
    min_landing_minutes: int = 45         # minimum time between landing and leaving the arrival airport
    earliest_arrival: str = ""            # earliest time arriving at the destination. Empty = any time
    latest_arrival: str = ""              # latest time arriving at the destination. Empty = whatever the last connection of the day allows
    max_stopovers: int = 0                # 0 = direct flights only

    # Data integrity
    min_reliability: str = "B"            # worst schedule grade accepted: "A", "B" or "C". See "Data integrity" above
    allow_estimated_prices: bool = True   # keep itineraries whose ground transport price was not found, with an estimate from the distance

    # Preferences: what the traveller's time is worth. Extra hours cost more and more: the first extra hour costs
    # the value below, and each further hour costs extra_hour_growth times the previous one. With 5 EUR and 1.5,
    # one extra hour is worth 5 EUR, two 12.50, three 23.75 and four 40.63.
    tiebreak_eur_per_hour: float = 5.0            # first extra hour, when comparing an itinerary with the fastest one
    fallback_saving_eur_per_hour: float = 10.0    # first extra hour, when a fallback airport makes the trip longer
    extra_hour_growth: float = 1.5                # 1 = every extra hour costs the same
    options_shown: int = 5                        # itineraries in the final comparison
    free_trains_at_destination: bool = False      # True if trains at the destination cost this traveller nothing

    # Speed
    use_flight_agent: bool = False        # True adds a model that also searches flights. Slower, and the code searches every route anyway
    extra_wishes: str = ""                # anything else the coordinator should know, in plain text

    def __post_init__(self):
        if not self.origin.strip() or not self.destination.strip():
            raise ValueError("origin and destination are required")
        if not self.dates:
            raise ValueError("give at least one travel date")
        for day in self.dates:
            if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", day):
                raise ValueError(f"'{day}' is not a dd/mm/yyyy date")
        if self.language not in LANGUAGES:
            raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
        if self.min_reliability not in GRADES:
            raise ValueError("min_reliability must be A, B or C")
        for name in ("include_airports", "exclude_airports", "primary_airports", "fallback_airports", "arrival_airports"):
            setattr(self, name, [entry.strip() for entry in getattr(self, name) if entry.strip()])
        if to_minutes(self.earliest_leave) > to_minutes(self.latest_leave):
            raise ValueError("earliest_leave is after latest_leave")
        if self.ideal_airport_minutes < self.min_airport_minutes:
            raise ValueError("ideal_airport_minutes is below min_airport_minutes")
        for name in ("earliest_arrival", "latest_arrival"):
            if getattr(self, name):
                to_minutes(getattr(self, name))   # raises if it is not a time
        if self.earliest_arrival and self.latest_arrival and to_minutes(self.earliest_arrival) > to_minutes(self.latest_arrival):
            raise ValueError("earliest_arrival is after latest_arrival")
        if self.max_stopovers < 0:
            raise ValueError("max_stopovers cannot be negative")
        if self.primary_radius_km > self.fallback_radius_km:
            raise ValueError("primary_radius_km is above fallback_radius_km")
        if self.tiebreak_eur_per_hour < 0 or self.fallback_saving_eur_per_hour < 0:
            raise ValueError("the EUR per hour preferences cannot be negative")
        if self.extra_hour_growth < 1:
            raise ValueError("extra_hour_growth must be 1 or more")
        if self.options_shown < 1:
            raise ValueError("options_shown must be at least 1")

    # Limits deduced from the settings, used by the flight search, the checks and the prompts
    @property
    def earliest_flight_minutes(self) -> int:
        """No flight departing before this can be caught: earliest time leaving plus the minimum time at the airport."""
        return to_minutes(self.earliest_leave) + self.min_airport_minutes

    @property
    def earliest_arrival_minutes(self) -> int:
        return to_minutes(self.earliest_arrival) if self.earliest_arrival else 0

    @property
    def latest_arrival_minutes(self) -> int:
        return to_minutes(self.latest_arrival) if self.latest_arrival else 24 * 60 - 1

    @property
    def iso_dates(self) -> List[str]:
        """The travel dates as yyyy-mm-dd."""
        return [f"{day[6:10]}-{day[3:5]}-{day[0:2]}" for day in self.dates]

    @property
    def arrival_window(self) -> str:
        """The arrival window in words, for the prompts."""
        if self.earliest_arrival and self.latest_arrival:
            return f"between {self.earliest_arrival} and {self.latest_arrival}"
        if self.earliest_arrival:
            return f"at {self.earliest_arrival} or later, and before midnight"
        if self.latest_arrival:
            return f"at {self.latest_arrival} at the latest"
        return "on the same day the flight lands, so the last connection of the day is the limit"

    def extra_hours_cost(self, first_hour_eur: float, hours: float) -> float:
        """What a number of extra hours of travel is worth in EUR. The first hour costs first_hour_eur and each
        further hour costs extra_hour_growth times the previous one. Part of an hour counts in proportion."""
        if hours <= 0:
            return 0.0
        whole = int(hours)
        return first_hour_eur * (
            sum(self.extra_hour_growth ** hour for hour in range(whole))
            + self.extra_hour_growth ** whole * (hours - whole)
        )

    def extra_hours_table(self, first_hour_eur: float) -> str:
        """The cost of 1 to 4 extra hours, in words, for the prompts and the report."""
        return ", ".join(f"{hours}h = {self.extra_hours_cost(first_hour_eur, hours):.0f} EUR" for hours in (1, 2, 3, 4))



USER_AGENT = "trip-planner/1.0 (educational project)"
