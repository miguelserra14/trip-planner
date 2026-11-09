"""A Discover map made in the search's process (job.py, task "map"), and told to the app: the app serves the page,
the destinations as they change, and a click on a trip opens the planner's form in the app, filled in (maps.py).
The map is made by discover's own Session, as the command (python -m discover) makes it; nothing of it changes.

The process says, besides its progress lines:
    map      the page, once the flights and the ways to the airports are found
    trips    {key: [origin, destination, day]} the trips the planner's form can be opened on, once
    state    what the page shows that changes while the timetables are looked up, each time it changes
    done     the map is complete, and where its file is
    ready    then it waits: the trains and buses of a city can still be priced, until it is told to stop

Told {"price": key}, it prices the trains and buses of that city of the map (Session.price_asked), at once, while the
lookups go on or after them, and the page is sent the state again. Told {"stop": true}, or its stdin closed, it ends
at the next city or phase, its files saved: the app asks a map to stop this way, never killing it halfway through
writing a cache unless it does not end in time (maps.STOP_SECONDS)."""
import dataclasses
import json
import queue
import threading
from typing import Any, Iterator

WATCH_SECONDS = 1   # how often the map's state is looked at, to tell the app when it changed

def make_map(asked: dict[str, Any], events, answers: Iterator[str] | None = None) -> None:
    from trip_planner import current
    from trip_planner.texts import tr
    from trip_planner.trip.trip_settings import settings_file
    settings = settings_file()
    # discover's Session reads the language of its texts from settings.py: the one of the app's pages instead
    settings.SETTINGS = dataclasses.replace(settings.SETTINGS, language=current.SETTINGS.language,
                                            max_coach_changes=asked.get("coach_changes") or 0)   # checked by the form (maps.coach_settings)
    from discover import confirm, finder
    from discover.view.page import map_page, map_state, trips
    from discover.session import Session, snapshot, times
    from discover.flights.sources import read_days
    from trip_planner.trip.places import load_airports

    try:
        days = read_days(str(asked.get("dates") or ""), str(asked.get("return_dates") or ""))
    except ValueError as error:
        events.send("failed", tr("discover_win_days_wrong", error=error))
        return
    source = asked.get("source")
    session = Session(str(asked.get("city") or "").strip(), days, fast=source == "fast", light=source == "light",
                      max_stops=finder.MAX_STOPS, ground_only=source == "ground")

    asked_prices: queue.Queue = queue.Queue()   # the cities whose price the page asked for, None once told to stop

    def listen() -> None:
        for line in answers:   # the app's lines, on stdin
            try:
                message = json.loads(line)
                if message.get("stop"):
                    break
                if isinstance(message.get("price"), str):
                    asked_prices.put(message["price"])
            except (ValueError, AttributeError):
                continue
        session.stopping.set()   # look_up ends at its next step; while the flights are found, it is not even started
        asked_prices.put(None)
    listener = threading.Thread(target=listen, daemon=True)
    if answers is not None:
        listener.start()
    session.make()
    found = session.found
    if found.kiwi_why:
        events.send("say", tr("discover_win_only_ryanair", why=found.kiwi_why))
    events.send("trips", trips(found, load_airports()))   # the flights do not change while the timetables are looked up
    session.live["state"] = snapshot(map_state(found, session.sections, running=True, **times()))
    events.send("map", map_page(found, session.sources, session.sections, running=True, max_lookups=confirm.MAX_LOOKUPS,
                                planner_link=str(asked.get("planner_link") or ""), token=str(asked.get("token") or ""), **times()))
    events.send("state", session.live["state"])

    ended = threading.Event()

    def watch() -> None:
        sent = session.live["state"].get("version")
        while not ended.wait(WATCH_SECONDS):
            with session.lock:
                state = session.live["state"]
            if state.get("version") != sent:
                sent = state.get("version")
                events.send("state", state)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    pricing: list[threading.Thread] = []

    def price_asked() -> None:
        # A thread for each city: one that waits for the timetables does not hold back FlixBus's, asked in seconds
        while (key := asked_prices.get()) is not None:
            pricing.append(threading.Thread(target=session.price_asked, args=(key,), daemon=True))
            pricing[-1].start()
    pricer = threading.Thread(target=price_asked, daemon=True)
    pricer.start()
    try:
        session.look_up(timetables=bool(asked.get("look_up")))
        with session.lock:
            events.send("state", session.live["state"])
        events.send("done", tr("app_map_done", file=session.path))
        if answers is not None:
            events.send("ready", "")
            listener.join()   # until the app says stop, or closes
    finally:
        asked_prices.put(None)
        pricer.join()
        for thread in pricing:   # a price being written into the map, and its file, is let end
            thread.join()
        ended.set()
        watcher.join()
        with session.lock:
            events.send("state", session.live["state"])

def price_saved(asked: dict[str, Any], events) -> None:
    """The trains and buses of some cities of a map made before priced, as the page of a map being made asks its own
    process (Session.price_asked): asked {"name": the map, "keys": its cities}. The map is read back from the data in
    its page, the prices written into that data and the page saved again, with the time it was made kept, so that it
    stays where it was in the list."""
    import dataclasses
    import os
    from app import maps
    from discover.finder import Found
    from discover.overland import ground_fares
    from trip_planner import current
    from trip_planner.texts import tr
    # The changes of coach of the form as it is now (app/site.py), checked as every setting is
    current.SETTINGS = dataclasses.replace(current.SETTINGS, max_coach_changes=asked.get("coach_changes") or 0)
    ground_fares.MOST_CHANGES = current.SETTINGS.max_coach_changes
    from trip_planner.trip.places import Place
    from trip_planner.utils import write_whole
    path = maps.saved_path(str(asked.get("name") or ""))
    page = path.read_text(encoding="utf-8") if path else ""
    data = maps.page_data(page) if page else None
    days = maps.days_of_saved(path) if path else None
    if not data or days is None:
        events.send("failed", tr("app_not_found"))
        return
    state, origin = data["state"], data["origin"]
    place = Place(origin.get("short") or origin["name"], origin["name"], origin.get("short") or origin["name"], "", origin["lat"], origin["lon"])
    # Its destinations and its cities by train or bus as the page has them: the prices are written into those very ones
    found = Found(place, days, [], state.get("points") or [], [], [], by_ground=state.get("by_ground") or [],
                  fare_lookups=state.setdefault("fare_lookups", []))
    answers = state.setdefault("asked_prices", {})
    for key in [key for key in asked.get("keys") or [] if isinstance(key, str)]:
        try:
            answer = ground_fares.price_asked(found, key)
        except Exception:   # whatever failed, the page must not wait for this city for ever
            answer = None
        answers[key] = "priced" if answer and ground_fares.write_asked(found, *answer) else "none"
    state["version"] = state.get("version", 0) + 1
    made = path.stat().st_mtime
    write_whole(path, maps.with_data(page, data))
    os.utime(path, (made, made))
    events.send("done", tr("app_task_done"))

