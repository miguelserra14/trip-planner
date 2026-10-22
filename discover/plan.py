"""From a trip on the map to the planner's window, with its form filled: the origin, the destination and the day.
The map only knows the flight; the planner looks up the ground transport in real timetables and leaves out a
flight that cannot be reached, which is what makes a dot on the map a trip that can be taken. The window plans
nothing, and spends nothing of the models' quota, until the traveller asks it to.

    python -m discover.plan Coimbra London 12/11/2026

The other settings are the ones in settings.py, except the airports added or left out there: they were chosen for
the trip in settings.py, not for this one."""
import dataclasses
import sys
from datetime import date
from typing import Any

from discover.access import city_name
from discover.finder import Found

def day(text: str) -> str:
    """yyyy-mm-dd as the planner reads it, dd/mm/yyyy."""
    return date.fromisoformat(text).strftime("%d/%m/%Y")

def city(code: str, airports: dict[str, Any], centres: dict | None = None) -> str:
    """The city an airport serves, by the name the planner will look it up by: Marseille, not Marignane."""
    if centres and code in centres:
        return centres[code].english_name
    airport = airports.get(code) or {}
    return city_name(airport) or airport.get("name") or code

def trips(found: Found, airports: dict[str, Any]) -> dict[str, list[str]]:
    """Every trip of the map the planner can be opened on, by key: "<code>-out" for the way there and "<code>-back"
    for the way back, each [origin, destination, day]. The page sends only the key: what the planner is opened on
    comes from here, never from the page."""
    home = found.place.written
    found_trips = {}
    for flight in found.destinations:
        there = city(flight["to"], airports, found.centres)
        found_trips[f"{flight['to']}-out"] = [home, there, day(flight["date"])]
        if flight.get("back"):
            found_trips[f"{flight['to']}-back"] = [city(flight["back"]["from"], airports, found.centres), home, day(flight["back"]["date"])]
    return found_trips

def command(trip: list[str]) -> list[str]:
    """The command that opens the planner on a trip, to run from the repository's folder."""
    return [sys.executable, "-m", "discover.plan", *trip]

def main(arguments: list[str]) -> int:
    if len(arguments) != 3:
        print(__doc__)
        return 1
    origin, destination, travel_day = arguments
    try:
        from settings import SETTINGS
        from trip_planner import gui
        settings = dataclasses.replace(SETTINGS, origin=origin, destination=destination, dates=[travel_day],
                                       include_airports=[], exclude_airports=[])
    except Exception as error:
        # Opened from the map there may be no terminal to read the error in: a day already past, a key missing
        import tkinter
        from tkinter import messagebox
        tkinter.Tk().withdraw()
        messagebox.showerror("Trip Planner", f"The trip planner could not open this trip.\n\n{type(error).__name__}: {error}")
        return 1
    return gui.run(settings)

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
