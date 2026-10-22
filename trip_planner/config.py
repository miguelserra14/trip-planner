"""The settings that define a trip (TripSettings), and where the project's files are."""
import re
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from trip_planner.dates import read_dates, UnreadableDate
from trip_planner.ground_choice import CHOICES, NO_CHANGE_LIMIT
from trip_planner.texts import LANGUAGES, REFERENCE, text_in
from trip_planner.utils import plain, to_minutes, to_time

ROOT = Path(__file__).resolve().parent.parent   # the project folder: resources/, reports/ and env/ are in it
load_dotenv(ROOT / "env" / ".env")       # the API keys, before any client is created

SCALES = ("near", "wide")   # how far around a city its airports are looked for, see TripSettings
GRADES = "ABC"   # reliability grades, best first. See "Data integrity" in trip_planner/__init__.py.
LIGHTS = dict(zip(GRADES, "🔵🟢🟠"))   # how the traveller sees each grade: a colour (blue, green, orange), never the letter

# The values each number may take: (lowest, highest). Outside them the setting is a mistake, not a preference.
RANGES = {
    "primary_radius_km": (1, 1000), "fallback_radius_km": (1, 1500), "arrival_radius_km": (1, 1000),
    "max_departure_airports": (1, 30), "max_arrival_airports": (1, 15), "max_road_hours": (1, 48),
    "min_airport_minutes": (30, 360), "ideal_airport_minutes": (30, 480), "min_landing_minutes": (0, 240),
    "max_stopovers": (0, 3), "max_changes_to_airport": (0, NO_CHANGE_LIMIT), "max_changes_from_airport": (0, NO_CHANGE_LIMIT),
    "tiebreak_eur_per_hour": (0, 1000), "fallback_saving_eur_per_hour": (0, 1000), "extra_hour_growth": (1, 5),
    "options_shown": (1, 10),
    "max_estimate_km": (0, 300), "small_estimate_eur": (0, 50), "unverified_price_factor": (1, 10),
    "city_walk_minutes": (5, 120), "wide_city_walk_minutes": (5, 180), "airport_walk_minutes": (5, 60),
    "airport_floor_minutes": (15, 360), "night_minutes": (0, 360),
    "min_trip_km": (0, 500), "city_airport_radius_km": (5, 150), "ambiguous_importance": (0, 1),
    "recent_reports": (1, 100),
}
MAX_DATES = 7                # travel dates in one search: each one multiplies the routes to search
MAX_DAYS_AHEAD = 360         # airlines do not sell further ahead than about this
FAR_DAYS_AHEAD = 300         # ... and beyond this few flights are on sale yet
CONFIRMED_DAYS_AHEAD = 8     # timetables for the travel date itself (grade A) are only read this close to it
TIGHT_DAY_MINUTES = 90       # time left for all the travel, below which the leave and arrival limits hardly fit
NARROW_LEAVE_MINUTES = 60    # a window to leave in shorter than this rarely contains a connection

class SettingError(ValueError):
    """A setting that cannot be used. Its reason is one of the texts of texts.py, in the traveller's language."""

    def __init__(self, language: str, key: str, **values):
        super().__init__(key)
        self.language, self.key, self.values = language, key, values

    def __str__(self) -> str:
        return text_in(self.language, self.key, **self.values)

@dataclass
class TripSettings:
    """Everything that defines a trip. The agents, the checks and the report all read it."""
    origin: str                           # city where the trip starts, in any language
    destination: str                      # city where the trip ends, in any language
    dates: list[str]                      # acceptable travel dates, from today on: "17/12/2026", "17-19/12", "sexta"... see dates.py
    language: str = "en"                  # language of the airport list and the final report: "en" or "pt-PT"

    # Airports are found automatically by distance from the two cities.
    primary_radius_km: int = 100          # departure airports this close to the origin are "primary"
    fallback_radius_km: int = 300         # departure airports up to this far are "fallback": only worth it when cheaper
    arrival_radius_km: int = 200          # arrival airports this close to the destination
    max_departure_airports: int = 12      # primary airports first, then the large ones, then the closest
    max_arrival_airports: int = 3         # large airports first, then the closest
    max_road_hours: float = 6.0           # an airport further than this by road is left out: close on the map, but across a sea
    # The two scales above can be swapped: "wide" is fallback_radius_km and max_departure_airports,
    # "near" is arrival_radius_km and max_arrival_airports, whichever side it is used on.
    departure_scale: str = "wide"         # around the origin: worth going far when the traveller lives there
    arrival_scale: str = "wide"           # around the destination
    # Adjust the automatic choice. Each entry is an airport code ("BRU"), an airport name ("Charleroi")
    # or a city ("Paris" means every airport of Paris).
    include_airports: list[str] = field(default_factory=list)    # airports to add
    exclude_airports: list[str] = field(default_factory=list)    # airports never to use
    # Or replace the automatic choice of a whole group, with the same kind of entries.
    primary_airports: list[str] = field(default_factory=list)
    fallback_airports: list[str] = field(default_factory=list)
    arrival_airports: list[str] = field(default_factory=list)
    confirm_airports: bool = True         # list the airports with checkboxes to drop some, before searching

    # Hard constraints, enforced in code by build_itineraries
    earliest_leave: str = ""              # earliest time leaving the origin city. Empty = from midnight
    latest_leave: str = ""                # latest time leaving the origin city. Empty or 00:00 = until the end of the day
    min_airport_minutes: int = 90         # minimum time at the airport before the flight departs
    ideal_airport_minutes: int = 120      # time at the airport aimed for when no real timetable is available
    min_landing_minutes: int = 45         # minimum time between landing and leaving the arrival airport
    earliest_arrival: str = ""            # earliest time arriving at the destination. Empty or 00:00 = any time
    latest_arrival: str = ""              # latest time arriving at the destination. Empty or 00:00 = whatever the last connection of the day allows,
                                          # which may reach the destination in the first hours after midnight
    max_stopovers: int = 0                # 0 = direct flights only
    next_day_arrivals: bool = True        # accept flights that land on a later day than they leave (night and long flights),
                                          # and connections from the airport that reach the destination after midnight.
                                          # The arrival times above are then read on the clock of the day of arrival
    # Ground transport on each side of the flight (ground_choice.py): "any", "train" or "bus", and the most changes of
    # vehicle. Metro, tram and ferries are the city's own transport and count with either choice. 9 = no limit on changes.
    # With a choice, a leg that cannot be checked (a model's estimate, a stretch estimated from the distance) is not used.
    to_airport_by: str = "any"            # from the origin to the departure airport
    from_airport_by: str = "any"          # from the arrival airport to the destination
    max_changes_to_airport: int = NO_CHANGE_LIMIT
    max_changes_from_airport: int = NO_CHANGE_LIMIT

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
    free_trains_at_origin: bool = False           # True if trains around the origin cost this traveller nothing
    free_trains_at_destination: bool = False      # True if trains at the destination cost this traveller nothing

    # Rules of the program. They decide what counts as a trip, and are the same for every trip unless changed.
    # Ground legs (itineraries/legs.py)
    max_estimate_km: int = 50             # a ground leg no timetable covers is estimated (by a model, checked against the distance)
                                          # only if the airport is this close to its city. Beyond it a guess is not a trip:
                                          # Treviso to Campogalliano, 150 km, was a regional train the model made up
    small_estimate_eur: float = 5.0       # an estimated price up to this (a metro ticket) is accepted even with allow_estimated_prices=False
    unverified_price_factor: float = 2.0  # a price that is a guess counts as this many times itself next to a real one,
                                          # so that it only beats a real price when it clearly would
    # Timetables (sources/timetables.py)
    city_walk_minutes: int = 40           # walking allowed between the city centre and the first or last stop. Not counted in the trip
    wide_city_walk_minutes: int = 90      # ... tried when nothing is found with the walk above: where the timetables lack a city's
                                          # own transport (Athens's metro), the station is further than that on foot
    airport_walk_minutes: int = 20        # walking allowed between the terminal and a stop. Counted in the trip
    airport_floor_minutes: int = 45       # the closest alternatives may cut min_airport_minutes, but never below this
    night_minutes: int = 240              # a ground connection may cross midnight by this much: after a late landing it may reach the
                                          # destination up to 04:00, and before an early flight the one of the evening before
                                          # (from 20:00) is kept for the closest alternatives
    # Places (places.py)
    min_trip_km: int = 80                 # an origin and a destination closer than this are not a trip by plane
    city_airport_radius_km: int = 45      # a city in the airport settings ("Paris") means every airport this close to it
    ambiguous_importance: float = 0.2     # places of the same name whose importance (0 to 1, from OpenStreetMap) differs by less
                                          # than this could each be the one meant: the traveller chooses ("Nazare": Bahia or Leiria)
    # Reports (report/full.py)
    recent_reports: int = 5               # reports kept in reports/. Older ones are deleted, except those kept in reports/saved/

    # Speed
    use_flight_agent: bool = False        # True adds a model that also searches flights. Slower, and the code searches every route anyway
    extra_wishes: str = ""                # anything else the coordinator should know, in plain text

    def __post_init__(self):
        """Check every setting and put it in one form. Whatever is wrong raises SettingError, saying which setting
        and why, in the traveller's language. Nothing is checked later: a setting that passes here can be used."""
        # The language first: the reasons below are written in it
        languages = {code.lower(): code for code in LANGUAGES} | {code.split("-")[0].lower(): code for code in LANGUAGES}
        if not isinstance(self.language, str) or self.language.strip().lower() not in languages:
            raise SettingError(REFERENCE, "bad_choice", setting="language", value=self.language, choices=", ".join(LANGUAGES))
        self.language = languages[self.language.strip().lower()]
        fail = lambda key, **values: SettingError(self.language, key, **values)

        # Each setting in the type it was declared with: a number typed as text is read, anything else is refused
        for setting in fields(self):
            name, kind, value = setting.name, setting.type, getattr(self, setting.name)
            if kind is bool:
                if not isinstance(value, bool):
                    raise fail("bad_yes_no", setting=name, value=value)
            elif kind in (int, float):
                try:
                    number = float(str(value).strip().replace(",", ".")) if not isinstance(value, bool) else None
                except ValueError:
                    number = None
                if number is None or number != number or (kind is int and number != int(number)):
                    raise fail("bad_number", setting=name, value=value)
                setattr(self, name, kind(number))
                lowest, highest = RANGES[name]
                if not lowest <= number <= highest:
                    raise fail("out_of_range", setting=name, value=f"{number:g}", lowest=lowest, highest=highest)
            elif kind is str:
                if not isinstance(value, str):
                    raise fail("bad_text", setting=name, value=value)
                setattr(self, name, value.strip())
            else:   # a list of texts. One text with commas is a list too: dates="17/12/2026, 18/12/2026"
                entries = re.split(r"[,;]", value) if isinstance(value, str) else value
                if not isinstance(entries, (list, tuple)) or not all(isinstance(entry, str) for entry in entries):
                    raise fail("bad_list", setting=name, value=value)
                setattr(self, name, [entry.strip() for entry in entries if entry.strip()])

        # The two cities
        if not self.origin or not self.destination:
            raise fail("place_required")
        if plain(self.origin) == plain(self.destination):
            raise fail("same_place", place=self.origin)

        # The dates: real days, from today on, no further than airlines sell, in order and without repeats
        if not self.dates:
            raise fail("dates_required")
        today, days = date.today(), set()
        try:
            written = read_dates(self.dates, today)   # "17/12", "17-19 dez", "sexta": see dates.py
        except UnreadableDate as error:
            raise fail(error.key, **error.values)
        for day in written:
            if day < today:
                raise fail("date_past", value=f"{day:%d/%m/%Y}", today=f"{today:%d/%m/%Y}")
            if (day - today).days > MAX_DAYS_AHEAD:
                raise fail("date_too_far", value=f"{day:%d/%m/%Y}", days=MAX_DAYS_AHEAD)
            days.add(day)
        if len(days) > MAX_DATES:
            raise fail("too_many_dates", count=len(days), maximum=MAX_DATES)
        self.dates = [f"{day:%d/%m/%Y}" for day in sorted(days)]

        # The choices
        self.min_reliability = self.min_reliability.upper()
        if self.min_reliability not in GRADES:
            raise fail("bad_choice", setting="min_reliability", value=self.min_reliability, choices=", ".join(GRADES))
        for names, choices in ((("departure_scale", "arrival_scale"), SCALES), (("to_airport_by", "from_airport_by"), CHOICES)):
            for name in names:
                setattr(self, name, getattr(self, name).lower())
                if getattr(self, name) not in choices:
                    raise fail("bad_choice", setting=name, value=getattr(self, name), choices=", ".join(choices))

        # The times, all as HH:MM. A day runs from 00:00 to 23:59, so a limit "by 00:00" can only mean the end of the
        # day: read as its start it would leave no time at all. An empty time means no limit.
        def clock(name: str, when_empty: str) -> str:
            written = getattr(self, name).lower().replace(".", ":").replace(" ", "")
            if re.fullmatch(r"[0-9]{1,2}h?", written):
                written = written.rstrip("h") + ":00"         # "7" and "7h"
            elif re.fullmatch(r"[0-9]{3,4}", written):
                written = written[:-2] + ":" + written[-2:]   # "0630"
            try:
                return to_time(to_minutes(written)) if written else when_empty
            except ValueError:
                raise fail("bad_time", setting=name, value=getattr(self, name))
        self.earliest_leave = clock("earliest_leave", "00:00")
        self.latest_leave = clock("latest_leave", "23:59")
        self.earliest_arrival = clock("earliest_arrival", "")
        self.latest_arrival = clock("latest_arrival", "")
        if self.latest_leave == "00:00":
            self.latest_leave = "23:59"
        if self.latest_arrival == "00:00":
            self.latest_arrival = ""
        if self.earliest_arrival == "00:00":
            self.earliest_arrival = ""
        for first, last in (("earliest_leave", "latest_leave"), ("earliest_arrival", "latest_arrival")):
            if getattr(self, first) and getattr(self, last):
                if to_minutes(getattr(self, first)) > to_minutes(getattr(self, last)):
                    raise fail("window_reversed", first=first, last=last, start=getattr(self, first), end=getattr(self, last))
                if getattr(self, first) == getattr(self, last):
                    raise fail("window_empty", first=first, last=last, time=getattr(self, first))
        if self.earliest_flight_minutes >= 24 * 60:
            raise fail("no_flight_catchable", earliest=self.earliest_leave, minutes=self.min_airport_minutes)

        # Settings that only make sense together
        self.ideal_airport_minutes = max(self.ideal_airport_minutes, self.min_airport_minutes)
        self.airport_floor_minutes = min(self.airport_floor_minutes, self.min_airport_minutes)
        self.wide_city_walk_minutes = max(self.wide_city_walk_minutes, self.city_walk_minutes)
        if self.primary_radius_km > self.fallback_radius_km:
            raise fail("radius_order", primary=self.primary_radius_km, fallback=self.fallback_radius_km)
        both = {plain(entry) for entry in self.include_airports} & {plain(entry) for entry in self.exclude_airports}
        if both:
            raise fail("include_and_exclude", entry=", ".join(sorted(both)))

    def advice(self) -> list[str]:
        """Settings that are allowed but will probably disappoint, each with the reason, in the traveller's language.
        They are said before the search starts, not refused: the traveller may know better."""
        found, today = [], date.today()
        furthest = max((date.fromisoformat(day) - today).days for day in self.iso_dates)
        if self.latest_arrival:
            # Before any travel at all: leave, wait at the airport, walk out of the other airport
            fixed = self.min_airport_minutes + self.min_landing_minutes
            left = to_minutes(self.latest_arrival) - to_minutes(self.earliest_leave) - fixed
            if left < TIGHT_DAY_MINUTES:
                found.append(text_in(self.language, "advice_tight_day", earliest=self.earliest_leave, latest=self.latest_arrival,
                                     fixed=fixed, left=max(left, 0)))
        if to_minutes(self.latest_leave) - to_minutes(self.earliest_leave) < NARROW_LEAVE_MINUTES:
            found.append(text_in(self.language, "advice_narrow_leave", earliest=self.earliest_leave, latest=self.latest_leave))
        if self.min_reliability == "A" and furthest > CONFIRMED_DAYS_AHEAD:
            found.append(text_in(self.language, "advice_grade_a", days=CONFIRMED_DAYS_AHEAD))
        if furthest > FAR_DAYS_AHEAD:
            found.append(text_in(self.language, "advice_far_date", days=furthest))
        return found

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
        """On the clock of the day the flight lands. Past 24 hours it is in the night after that day."""
        if self.latest_arrival:
            return to_minutes(self.latest_arrival)
        return 24 * 60 - 1 + (self.night_minutes if self.next_day_arrivals else 0)

    def scale(self, name: str) -> tuple:
        """What a scale means: (radius in km, most airports kept, the setting that holds the radius)."""
        if name == "wide":
            return self.fallback_radius_km, self.max_departure_airports, "fallback_radius_km"
        return self.arrival_radius_km, self.max_arrival_airports, "arrival_radius_km"

    @property
    def iso_dates(self) -> list[str]:
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
        if self.next_day_arrivals:
            return "by the last connection of the day the flight lands, which may reach the destination shortly after midnight"
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
