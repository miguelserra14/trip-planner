"""One search, in a process of its own: the app starts it (search.py) and reads what it says. The planner keeps the
trip in globals (trip_planner/current.py), so two searches, or a search and a map, cannot share a process.

It reads the trip on stdin as JSON, {"origin", "destination", "dates", "return_dates"}, each one text as the
traveller typed it, and writes one JSON object per line on stdout:
    {"kind": "say", "text": ...}      a progress line (and anything else the planner prints)
    {"kind": "show", "text": ...}     Markdown of the result, shown as soon as the code has it
    {"kind": "report", "text": ...}   the path of a report saved: one, or two for a round trip
    {"kind": "failed", "text": ...}   why the search could not run or stopped, in the traveller's language
Every other setting is the one in settings.py, except the airports added or left out there: they were chosen for
the trip in settings.py, not for this one. The airports are all kept, as on a flight search site."""
import asyncio
import dataclasses
import json
import sys
import threading
import traceback
from typing import Any, TextIO

from trip_planner.trip.trip_settings import TripSettings

FIELDS = ("origin", "destination", "dates", "return_dates")   # what the form sends; TripSettings reads and checks each

def settings_for(asked: dict[str, Any], defaults: TripSettings) -> TripSettings:
    """The trip asked for, on top of the defaults. Raises SettingError, in the traveller's language, if a value is
    not valid: the app calls this too, to answer the form at once."""
    values = {name: str(asked.get(name) or "") for name in FIELDS}
    return dataclasses.replace(defaults, **values, include_airports=[], exclude_airports=[], confirm_airports=False)

class Events:
    """The lines to the app, on the stdout the process started with: one JSON object each, whole, whatever thread
    writes them."""

    def __init__(self, stream: TextIO):
        self.stream, self.lock = stream, threading.Lock()

    def send(self, kind: str, text: str) -> None:
        with self.lock:
            self.stream.write(json.dumps({"kind": kind, "text": text}, ensure_ascii=False) + "\n")
            self.stream.flush()

class Printed:
    """Stands in for stdout: whatever the planner prints becomes a progress line, so that it cannot break the JSON."""

    def __init__(self, events: Events):
        self.events, self.pending = events, ""

    def write(self, text: str) -> int:
        *lines, self.pending = (self.pending + text).split("\n")
        for line in lines:
            if line.strip():
                self.events.send("say", line)
        return len(text)

    def flush(self) -> None:
        pass

async def plan(settings: TripSettings, events: Events) -> None:
    from trip_planner import planner
    from trip_planner.sources import flight_search
    await flight_search.connect()
    await planner.plan_trip(settings)
    for trip in planner.PLANNED:
        if trip.get("report") is not None:
            events.send("report", str(trip["report"]))

def main(request: str, events: Events) -> int:
    from settings import SETTINGS
    from trip_planner import current, utils
    from trip_planner.texts import tr
    current.SETTINGS = SETTINGS   # the reason a setting is refused is in the language of settings.py
    try:
        asked = json.loads(request)
        settings = settings_for(asked if isinstance(asked, dict) else {}, SETTINGS)
    except json.JSONDecodeError:
        events.send("failed", tr("app_not_a_trip"))
        return 1
    except ValueError as error:
        events.send("failed", str(error))
        return 1
    current.SETTINGS = settings
    utils.LISTENERS["say"] = lambda text: events.send("say", text)
    utils.LISTENERS["show"] = lambda markdown: events.send("show", markdown)
    try:
        asyncio.run(plan(settings, events))
    except Exception:
        events.send("failed", traceback.format_exc())
        return 1
    return 0

if __name__ == "__main__":
    out = sys.stdout
    out.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8")
    events = Events(out)
    sys.stdout = Printed(events)
    sys.exit(main(sys.stdin.read(), events))
