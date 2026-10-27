"""One map, from the city typed to the page served: what the command (__main__.py) and the window (window.py) both do.

    session = Session("Coimbra", Days(...))
    session.make()        the flights, the ways to and from the airports, the page saved to discover/maps
    session.serve()       the page on this computer, address in session.address; a click on a trip opens the planner
    session.look_up()     the cheapest destinations in the real timetables, then the trains and buses priced
                          (ground_fares.py), the page redrawing itself (blocks)
    session.stop()        the server stopped

Each step blocks, and runs in this process: the planner's own code reads the trip of the moment from globals
(current.SETTINGS, current.TRIP) that the lookups change, so a map is never made inside the planner's window."""
import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from discover import confirm, finder, ground, ground_fares
from discover.breakdown import as_text, breakdown
from discover.finder import discover, Found
from discover.page import map_page, map_state
from discover.plan import trips
from discover.serve import address_of, new_token, open_window, Planner, start, stop
from discover.sources import Days
from settings import SETTINGS
from trip_planner import current, utils
from trip_planner.texts import tr
from trip_planner.trip.places import geocode, load_airports
from trip_planner.utils import plain

MAPS = Path(__file__).parent / "maps"   # the maps made, in this folder: nothing of the planner's is written to

def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """What the page is served, apart from the map being worked on: the server writes it out in its own thread while
    the lookups go on changing the destinations."""
    return json.loads(json.dumps(state, ensure_ascii=False))

def times() -> dict[str, int]:
    """The planner's times at the airports (settings.py), for the time in the city of each trip (page.stay)."""
    return {"airport_minutes": SETTINGS.min_airport_minutes, "landing_minutes": SETTINGS.min_landing_minutes}

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
        self.sources = tr("discover_sources_ryanair") + (" " + tr("discover_sources_kiwi") if self.found.kiwi else "")
        days = self.days
        # The days clicked, when not all in a row, by their day of the month: two maps of the same weeks stay apart
        clicked = lambda chosen: "_" + "-".join(day[8:] for day in chosen) if chosen else ""
        name = (f"{plain(place.english_name).replace(' ', '-')}_{days.first}_{days.last}{clicked(days.out_days)}"
                + (f"_back_{days.back_first}_{days.back_last}{clicked(days.back_days)}" if days.round_trip else ""))
        MAPS.mkdir(exist_ok=True)
        self.path = MAPS / f"{name}.html"
        self.save()
        return self.path

    def save(self) -> None:
        """The map as a file, and every request, what it answered or why it failed, and how long it took."""
        found = self.found
        self.sections = breakdown(found)
        self.path.write_text(map_page(found, self.sources, self.sections, max_lookups=confirm.MAX_LOOKUPS, **times()), encoding="utf-8")
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
        self.live["state"] = snapshot(map_state(self.found, self.sections, running=looking_up, **times()))
        page = map_page(self.found, self.sources, self.sections, token=token, running=looking_up,
                        max_lookups=confirm.MAX_LOOKUPS, **times())
        self.server = start(page, self.planner, lambda: self.live["state"])
        self.address = address_of(self.server)
        return self.address

    def look_up(self, timetables: bool = True) -> None:
        """What is searched once the map is open, the served page following it (found.phase), the file saved again
        at the end: the trains and buses priced, in seconds when they are FlixBus's, against the flights' totals as
        estimated; with timetables, the cheapest destinations in the real timetables, a city at a time, and the trains
        and buses priced again for the cities that can now be among the cheapest; then the way back in the
        timetables of the trains and buses with no price for it."""
        airports = load_airports()

        def changed(found: Found) -> None:
            with self.lock:
                if self.planner:
                    self.planner.trips = trips(found, airports)
                self.live["state"] = snapshot(map_state(found, breakdown(found), running=True, version=self.live["state"].get("version", 0) + 1, **times()))

        def phase(name: str) -> None:
            # The file is saved as each phase ends: a window closed halfway keeps what was found up to then
            if self.found.phase:
                self.save()
            self.found.phase = name
            changed(self.found)
        try:
            phase("prices")
            ground_fares.price(self.found, changed)
            if timetables:
                # FlixBus is asked already for the cities the second pricing will likely want, while the flights'
                # timetables are looked up: its searches do not go through the timetables, so they take nothing from them
                stop = threading.Event()
                prefetching = threading.Thread(target=ground_fares.prefetch, args=(self.found, stop), daemon=True)
                prefetching.start()
                try:
                    phase("timetables")
                    asyncio.run(confirm.confirm(self.found, changed))
                finally:
                    stop.set()
                    prefetching.join()
                phase("prices")
                ground_fares.price(self.found, changed)
            phase("ways back")
            ground.ways_back([options for _, options, _, _ in ground_fares.cities_of(self.found)], self.days)
        finally:
            self.found.phase = ""
            self.save()
            with self.lock:
                self.live["state"] = snapshot(map_state(self.found, self.sections, running=False, version=self.live["state"].get("version", 0) + 1, **times()))

    def stop(self) -> None:
        if self.server:
            stop(self.server)
            self.server = None
