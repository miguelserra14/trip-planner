"""One map, from the city typed to the page served: what the command (__main__.py) and the window (window.py) both do.

    session = Session("Coimbra", Days(...))
    session.make()        the flights, the ways to and from the airports, the page saved to discover/maps
    session.serve()       the page on this computer, address in session.address; a click on a trip opens the planner
    session.look_up()     the cheapest destinations in the real timetables, the page redrawing itself (blocks)
    session.stop()        the server stopped

Each step blocks, and runs in this process: the planner's own code reads the trip of the moment from globals
(current.SETTINGS, current.TRIP) that the lookups change, so a map is never made inside the planner's window."""
import asyncio
import json
import threading
from pathlib import Path

from discover import confirm, finder
from discover.breakdown import as_text, breakdown
from discover.finder import discover, Found
from discover.page import map_page, map_state
from discover.plan import trips
from discover.serve import address_of, new_token, open_window, Planner, start, stop
from discover.sources import Days
from settings import SETTINGS
from trip_planner import current, utils
from trip_planner.places import geocode, load_airports
from trip_planner.utils import plain

MAPS = Path(__file__).parent / "maps"   # the maps made, in this folder: nothing of the planner's is written to

class Session:
    def __init__(self, city: str, days: Days, fast: bool = False, light: bool = False, max_stops: int = finder.MAX_STOPS):
        self.city, self.days, self.fast, self.max_stops = city, days, fast, max_stops
        self.kiwi_requests = finder.KIWI_LIGHT_REQUESTS if light else finder.KIWI_REQUESTS
        self.found: Found | None = None
        self.path: Path | None = None
        self.sources = ""
        self.sections: list = []
        self.server = None
        self.address = ""
        self.planner: Planner | None = None
        self.live = {"state": {}}
        self.lock = threading.Lock()

    def make(self) -> Path:
        """Find the flights and the ways to and from the airports, and save the map. Returns its file."""
        current.SETTINGS = SETTINGS   # the planner's code reads its language and its rules from here: settings.py's
        place = geocode(self.city)
        self.found = asyncio.run(discover(place, self.days, fast=self.fast, max_stops=self.max_stops, kiwi_requests=self.kiwi_requests))
        self.sources = "Ryanair (all its destinations)" + (" and Kiwi (the cheapest of every airline, by country)" if self.found.kiwi else "")
        days = self.days
        name = f"{plain(place.english_name).replace(' ', '-')}_{days.first}_{days.last}" + (f"_back_{days.back_first}_{days.back_last}" if days.round_trip else "")
        MAPS.mkdir(exist_ok=True)
        self.path = MAPS / f"{name}.html"
        self.save()
        return self.path

    def save(self) -> None:
        """The map as a file, and every request, what it answered or why it failed, and how long it took."""
        found = self.found
        self.sections = breakdown(found)
        self.path.write_text(map_page(found, self.sources, self.sections, SETTINGS.min_airport_minutes, max_lookups=confirm.MAX_LOOKUPS), encoding="utf-8")
        self.path.with_suffix(".json").write_text(json.dumps({"days": str(self.days), "departures": found.departures, "searches": found.searches,
                                                              "lookups": found.lookups, "discarded": found.discarded, "log": utils.REQUESTS},
                                                             ensure_ascii=False, indent=1), encoding="utf-8")

    def report(self) -> str:
        """The cheapest destinations and what was searched, as text."""
        lines = []
        for flight in self.found.destinations[:10]:
            back = f", back {flight['back']['date']}" if flight.get("back") else ""
            looked = "looked up" if all(leg["looked_up"] for leg in flight["access"]) else "with published or estimated prices"
            lines.append(f"{flight['total_eur']:6.0f} EUR  {flight['to']}  from {flight['from']} on {flight['date']}{back}, {flight['airline']} "
                         f"({flight['source']}), {flight['ground_eur']:.0f} EUR to and from the airports, {looked}")
        return "\n".join(lines) + "\n" + as_text(self.sections) + (
            f"\n\n{len(self.found.destinations)} destinations on the map: {self.path}\nEvery request and its flights: {self.path.with_suffix('.json')}")

    def serve(self, looking_up: bool = True) -> str:
        """Serve the map on this computer. looking_up says the timetables are about to be looked up: the page then
        asks for the destinations again until look_up ends. Returns the address."""
        token = new_token()
        self.planner = Planner(trips(self.found, load_airports()), token, open_window)
        self.live["state"] = map_state(self.found, self.sections, running=looking_up)
        page = map_page(self.found, self.sources, self.sections, SETTINGS.min_airport_minutes, token=token, running=looking_up,
                        max_lookups=confirm.MAX_LOOKUPS)
        self.server = start(page, self.planner, lambda: self.live["state"])
        self.address = address_of(self.server)
        return self.address

    def look_up(self) -> None:
        """The cheapest destinations in the real timetables, a city at a time; the served page follows, and the
        file is saved again at the end."""
        airports = load_airports()

        def changed(found: Found) -> None:
            with self.lock:
                if self.planner:
                    self.planner.trips = trips(found, airports)
                self.live["state"] = map_state(found, breakdown(found), running=True, version=self.live["state"].get("version", 0) + 1)
        try:
            asyncio.run(confirm.confirm(self.found, changed))
        finally:
            self.save()
            with self.lock:
                self.live["state"] = map_state(self.found, self.sections, running=False, version=self.live["state"].get("version", 0) + 1)

    def stop(self) -> None:
        if self.server:
            stop(self.server)
            self.server = None
