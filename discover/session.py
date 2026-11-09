"""One map, from the city typed to the page saved: what the command (__main__.py) and the app (app/map_job.py) both do.

    session = Session("Coimbra", Days(...))
    session.make()        the flights, the ways to and from the airports, the page saved to discover/maps
    session.look_up()     the cheapest destinations in the real timetables, then the trains and buses priced
                          (ground_fares.py), session.live["state"] following them (blocks)
    session.price_asked(key) the trains and buses of one city priced, as the traveller asks on the map, during
                          look_up or after it (from another thread)
    session.stopping.set()   look_up ends at the next city or phase, keeping what it found (another map is wanted)

Each step blocks, and runs in this process: the planner's own code reads the trip of the moment from globals
(current.SETTINGS, current.TRIP) that the lookups change, so a map has a process of its own, apart from a search."""
import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

from discover import confirm, finder
from discover.overland import ground, ground_fares
from discover.view.breakdown import as_text, breakdown
from discover.finder import discover, Found
from discover.view.page import map_page, map_state
from discover.flights.sources import Days
from trip_planner import current, utils
from trip_planner.report.full import remember_duration
from trip_planner.texts import tr
from trip_planner.trip.places import geocode
from trip_planner.trip.trip_settings import settings_file
from trip_planner.utils import plain, progress

SETTINGS = settings_file().SETTINGS   # its language and its rules: the map has no dates of the planner's to refuse

MAPS = Path(__file__).parent / "maps"   # the maps made, in this folder: nothing of the planner's is written to

def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """What the page is told, apart from the map being worked on: the app is sent it from another thread while the
    lookups go on changing the destinations."""
    return json.loads(json.dumps(state, ensure_ascii=False))

class Stopped(Exception):
    """Raised by look_up's own callbacks when the session is asked to stop, to leave the lookups between two cities."""

def times() -> dict[str, int]:
    """The planner's times at the airports (settings.py), for the time in the city of each trip (page.stay)."""
    return {"airport_minutes": SETTINGS.min_airport_minutes, "landing_minutes": SETTINGS.min_landing_minutes}

class Session:
    def __init__(self, city: str, days: Days, fast: bool = False, light: bool = False, max_stops: int = finder.MAX_STOPS,
                 ground_only: bool = False):
        self.city, self.days, self.fast, self.max_stops, self.ground_only = city, days, fast, max_stops, ground_only
        # How its flights are searched, as the app's form says it (app/maps.py SOURCES): how long the map takes to open
        self.source = "ground" if ground_only else "fast" if fast else "light" if light else "full"
        self.kiwi_requests = finder.KIWI_LIGHT_REQUESTS if light else finder.KIWI_REQUESTS
        self.found: Found | None = None
        self.path: Path | None = None
        self.sources = ""
        self.sections: list = []
        self.live = {"state": {}}
        self.lock = threading.Lock()
        # Set when another map is wanted: two maps cannot look up at once, they change the same globals (current.TRIP)
        self.stopping = threading.Event()
        self.looking = False   # look_up is running: the page goes on asking for the state
        # Clear while the flights are looked up in the timetables: a price asked for on the map that needs them waits
        self.timetables_free = threading.Event()
        self.timetables_free.set()

    def make(self) -> Path:
        """Find the flights and the ways to and from the airports, and save the map. Returns its file."""
        current.SETTINGS = SETTINGS   # the planner's code reads its language and its rules from here: settings.py's
        ground_fares.MOST_CHANGES = SETTINGS.max_coach_changes   # once: the lookups change current.SETTINGS later
        started = time.time()
        place = geocode(self.city)
        # For the page that waits until the map opens (app/waiting.py): the city, then each step of finder.discover
        progress(trip={"discover": True, "source": self.source, "ground_only": self.ground_only, "departure": [], "arrival": [],
                       "origin": {"name": place.written.split(",")[0].strip(), "lat": place.latitude, "lon": place.longitude}})
        self.found = asyncio.run(discover(place, self.days, fast=self.fast, max_stops=self.max_stops, kiwi_requests=self.kiwi_requests,
                                          ground_only=self.ground_only))
        self.sources = (tr("discover_sources_ground") if self.ground_only else
                        tr("discover_sources_ryanair") + (" " + tr("discover_sources_kiwi") if self.found.kiwi else ""))
        days = self.days
        # The days clicked, when not all in a row, by their day of the month: two maps of the same weeks stay apart
        clicked = lambda chosen: "_" + "-".join(day[8:] for day in chosen) if chosen else ""
        name = (f"{plain(place.english_name).replace(' ', '-')}_{days.first}_{days.last}{clicked(days.out_days)}"
                + (f"_back_{days.back_first}_{days.back_last}{clicked(days.back_days)}" if days.round_trip else ""))
        MAPS.mkdir(exist_ok=True)
        self.path = MAPS / f"{name}.html"
        self.save()
        remember_duration(time.time() - started, f"discover_{self.source}")
        progress(step="done")
        return self.path

    def save(self) -> None:
        """The map as a file, and every request, what it answered or why it failed, and how long it took. Under the
        lock: a price asked for on the map (price_asked) may be written into it from another thread."""
        found = self.found
        with self.lock:
            self.sections = breakdown(found)
            self.path.write_text(map_page(found, self.sources, self.sections, max_lookups=confirm.MAX_LOOKUPS, **times()), encoding="utf-8")
            self.path.with_suffix(".json").write_text(json.dumps({"days": str(self.days), "departures": found.departures, "searches": found.searches,
                                                                  "lookups": found.lookups, "discarded": found.discarded, "log": list(utils.REQUESTS)},
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

    def look_up(self, timetables: bool = True) -> None:
        """What is searched once the map is open, the served page following it (found.phase), the file saved again
        at the end: the trains and buses priced, in seconds when they are FlixBus's, against the flights' totals as
        estimated; with timetables, the cheapest destinations in the real timetables, a city at a time, and the trains
        and buses priced again for the cities that can now be among the cheapest; then the way back in the
        timetables of the trains and buses with no price for it. When stopping is set it ends at the next city or
        phase, the page and the file keeping what was found up to then."""
        def changed(found: Found) -> None:
            if self.stopping.is_set():
                raise Stopped
            with self.lock:
                self.live["state"] = snapshot(map_state(found, breakdown(found), running=True, version=self.live["state"].get("version", 0) + 1, **times()))

        def phase(name: str) -> None:
            # The file is saved as each phase ends: a map stopped halfway keeps what was found up to then
            if self.found.phase:
                self.save()
            self.found.phase = name
            changed(self.found)
        self.looking = True
        try:
            phase("prices")
            ground_fares.price(self.found, changed)
            if timetables:
                # FlixBus is asked already for the cities the second pricing will likely want, while the flights'
                # timetables are looked up: its searches do not go through the timetables, so they take nothing from them.
                # Meanwhile each destination is the planner's trip in turn (current.trip): prefetch must never read it
                # (test_ground_fares.py)
                stop = threading.Event()
                prefetching = threading.Thread(target=ground_fares.prefetch, args=(self.found, stop), daemon=True)
                prefetching.start()
                self.timetables_free.clear()
                try:
                    phase("timetables")
                    asyncio.run(confirm.confirm(self.found, changed))
                finally:
                    stop.set()
                    prefetching.join()
                    self.timetables_free.set()
                phase("prices")
                ground_fares.price(self.found, changed)
            phase("ways back")
            ground.ways_back([options for _, options, _, _ in ground_fares.cities_of(self.found)], self.days)
        except Stopped:
            pass
        finally:
            self.looking = False
            self.found.phase = ""
            self.save()
            with self.lock:
                self.live["state"] = snapshot(map_state(self.found, self.sections, running=False, version=self.live["state"].get("version", 0) + 1, **times()))

    def tell_page(self) -> None:
        """The state the page is sent, made again after a change look_up does not make itself."""
        with self.lock:
            self.live["state"] = snapshot(map_state(self.found, breakdown(self.found), running=self.looking,
                                                    version=self.live["state"].get("version", 0) + 1, **times()))

    def price_asked(self, key: str) -> None:
        """The trains and buses of one city priced because the traveller asked on the map (ground_fares.price_asked),
        whether the rounds would have or not. The page follows found.asked_prices[key]: "waiting", then "priced" when
        one option has a price in full, or "none". Asked again while waiting, nothing more is asked. The file is
        saved here once look_up has ended; before, look_up saves it."""
        with self.lock:
            if self.found.asked_prices.get(key) == "waiting":
                return
            self.found.asked_prices[key] = "waiting"
        self.tell_page()
        try:
            answer = ground_fares.price_asked(self.found, key, before_trains=self.timetables_free.wait)
        except Exception:   # whatever failed, the page must not wait for this city for ever
            answer = None
        with self.lock:
            count = ground_fares.write_asked(self.found, *answer) if answer else 0
            self.found.asked_prices[key] = "priced" if count else "none"
        self.tell_page()
        if not self.looking:
            self.save()
