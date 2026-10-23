"""The days and the clocks of the timetables: which day is read for a travel date, and the local time of a place."""
import importlib.resources
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from trip_planner import current
from trip_planner.places import distance_km

def landing_day(flight: dict[str, Any]) -> str:
    """The day a flight lands, which is the day of everything at the destination. Later than its date for a night flight."""
    return flight.get("arrival_date") or flight["date"]

def reference_day(day: str) -> str:
    """The same weekday as a travel date, in the week that starts today: the days every operator has published.
    Some publish no further than six days ahead (Zadar, Thessaloniki), so a day a week from now finds them empty."""
    target, soon = date.fromisoformat(day), date.today()
    soon += timedelta(days=(target.weekday() - soon.weekday()) % 7)
    return min(soon, target).isoformat() if target >= date.today() else day

def local_to_utc(day: str, minutes: int, zone: str) -> str:
    local = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(zone)) + timedelta(minutes=minutes)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")

def utc_to_local(moment: str, zone: str) -> tuple:
    """A UTC time from the timetable as (yyyy-mm-dd, minutes since midnight) on the local clock."""
    local = datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
    return local.date().isoformat(), local.hour * 60 + local.minute

def minutes_since(day: str, moment: str, zone: str) -> int:
    """A UTC time from the timetable in minutes since the midnight day starts with, on the local clock: below 0 on
    the evening before, above 24 hours in the night after."""
    local_day, minutes = utc_to_local(moment, zone)
    return minutes + (date.fromisoformat(local_day) - date.fromisoformat(day)).days * 24 * 60

def day_bounds(to_airport: bool) -> tuple[int, int]:
    """The earliest a connection may leave and the latest it may arrive, in minutes since the midnight its day starts
    with. Up to night_minutes across a midnight: towards the airport from the evening before, and from it into the
    night after, if the traveller accepts arriving on a later day."""
    settings = current.SETTINGS
    first_leave = -settings.night_minutes if to_airport else 0
    last_arrive = 24 * 60 + (settings.night_minutes if not to_airport and settings.next_day_arrivals else 0)
    return first_leave, last_arrive

def zone_of_country(country: str, latitude: float, longitude: float) -> str:
    """The time zone of a point from its country alone: of the zones of that country, the one whose city is the
    nearest (Portugal has three). From the list that comes with the time zone database. Empty when not known."""
    try:
        table = importlib.resources.files("tzdata.zoneinfo").joinpath("zone.tab").read_text(encoding="utf-8")
    except Exception:
        return ""
    zones = []
    for line in table.splitlines():
        code, where, name = (line.split("\t") + ["", ""])[:3]
        place = re.fullmatch(r"([+-])(\d\d)(\d\d)(?:\d\d)?([+-])(\d\d\d)(\d\d)(?:\d\d)?", where)   # degrees and minutes
        if code == country and place:
            north = (int(place[2]) + int(place[3]) / 60) * (-1 if place[1] == "-" else 1)
            east = (int(place[5]) + int(place[6]) / 60) * (-1 if place[4] == "-" else 1)
            zones.append((distance_km(latitude, longitude, north, east), name))
    return min(zones)[1] if zones else ""
