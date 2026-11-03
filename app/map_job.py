"""A Discover map made in the search's process (job.py, task "map"), and told to the app instead of served by a
server of its own (discover/serve.py): the app serves the page, the destinations as they change, and a click on a
trip opens the planner's form in the app, filled in (maps.py). The map is made by discover's own Session, as the
window and the command make it; nothing of it changes.

The process says, besides its progress lines:
    map      the page, once the flights and the ways to the airports are found
    trips    {key: [origin, destination, day]} the trips the planner's form can be opened on, once
    state    what the page shows that changes while the timetables are looked up, each time it changes
    done     the map is complete, and where its file is

Told {"stop": true}, or its stdin closed, it ends at the next city or phase, its files saved: the app asks a map to
stop this way, never killing it halfway through writing a cache unless it does not end in time (maps.STOP_SECONDS)."""
import dataclasses
import json
import threading
from typing import Any, Iterator

WATCH_SECONDS = 1   # how often the map's state is looked at, to tell the app when it changed

def make_map(asked: dict[str, Any], events, answers: Iterator[str] | None = None) -> None:
    import settings
    from trip_planner import current
    from trip_planner.texts import tr
    # discover's Session reads the language of its texts from settings.py: the one of the app's pages instead
    settings.SETTINGS = dataclasses.replace(settings.SETTINGS, language=current.SETTINGS.language)
    from discover import confirm, finder
    from discover.page import map_page, map_state, trips
    from discover.session import Session, snapshot, times
    from discover.sources import read_days
    from trip_planner.trip.places import load_airports

    try:
        days = read_days(str(asked.get("dates") or ""), str(asked.get("return_dates") or ""))
    except ValueError as error:
        events.send("failed", tr("discover_win_days_wrong", error=error))
        return
    source = asked.get("source")
    session = Session(str(asked.get("city") or "").strip(), days, fast=source == "fast", light=source == "light",
                      max_stops=finder.MAX_STOPS)

    def listen() -> None:
        for line in answers:   # the app's lines, on stdin
            try:
                if json.loads(line).get("stop"):
                    break
            except (ValueError, AttributeError):
                continue
        session.stopping.set()   # look_up ends at its next step; while the flights are found, it is not even started
    if answers is not None:
        threading.Thread(target=listen, daemon=True).start()
    session.make()
    found = session.found
    if found.kiwi_why:
        events.send("say", tr("discover_win_only_ryanair", why=found.kiwi_why))
    events.send("trips", trips(found, load_airports()))   # the flights do not change while the timetables are looked up
    session.live["state"] = snapshot(map_state(found, session.sections, running=True, **times()))
    events.send("map", map_page(found, session.sources, session.sections, running=True, max_lookups=confirm.MAX_LOOKUPS,
                                planner_link=str(asked.get("planner_link") or ""), **times()))
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
    try:
        session.look_up(timetables=bool(asked.get("look_up")))
    finally:
        ended.set()
        watcher.join()
        with session.lock:
            events.send("state", session.live["state"])
    events.send("done", tr("app_map_done", file=session.path))
