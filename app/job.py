"""One search, in a process of its own: the app starts it (search.py) and talks to it, one JSON object per line each
way. The planner keeps the trip in globals (trip_planner/current.py), so two searches, or a search and a map,
cannot share a process; and the process stays while the traveller asks about the trip, because the conversation
with the model is kept in it (planner.PLANNED).

It also runs the program's own jobs, which the app has as buttons, when the first line names one instead of a
trip: {"task": "check" | "prices" | "language" | "map", "name": the language to add, "language": the one to write in};
a Discover map takes the fields of its form too (map_job.py).
Each says what it does as a search does, and ends with {"kind": "done", "text": how it went}.

On stdin, first the trip, then the traveller's answers, each when it is asked for:
    {"settings": {...}}        every setting of the form, as fields.read gives them
    {"index": n}               which of the places of the same name (after "unclear")
    {"dropped": [codes]}       the airports left out (after "airports")
    {"question": text}         a question about the trip planned (after "ready"), as many as wanted
    {"stop": true}             a map ends at its next city, keeping what it found (map_job.py)
On stdout, {"kind": ..., "text": ...}:
    say, show                  a progress line (and anything else printed), and Markdown of the result
    progress                   where the search is, as data (utils.progress), for the page that waits (waiting.py)
    unclear                    {"setting", "name", "options": [labels]}: a name of several places; it waits for "index"
    airports                   {"groups": [{"title", "airports": [...]}], "notes": [...]}: it waits for "dropped"
    refused                    why those airports cannot be left out; it waits for "dropped" again
    report                     the path of a report saved: one, or two for a round trip
    ready                      planned: it waits for a question, or for stdin to close
    unanswered                 the model did not answer the last question; it waits for another
    failed                     why the search could not run or stopped, in the traveller's language
The process ends when stdin closes."""
import asyncio
import dataclasses
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Iterator, TextIO

from trip_planner.trip.form import named
from trip_planner.trip.trip_settings import TripSettings

def settings_for(values: dict[str, Any], defaults: TripSettings) -> TripSettings:
    """The trip asked for, on top of the defaults: only the settings the form holds. Raises SettingError, in the
    traveller's language, if a value is not valid: the app calls this too, to answer the form at once."""
    from app.fields import ADVANCED, TRIP
    from trip_planner.trip.form import MAIN_SETTINGS
    allowed = {*TRIP, *ADVANCED, *MAIN_SETTINGS}
    return dataclasses.replace(defaults, **{name: value for name, value in values.items() if name in allowed})

class Events:
    """The lines to the app, on the stdout the process started with: one JSON object each, whole, whatever thread
    writes them."""

    def __init__(self, stream: TextIO):
        self.stream, self.lock = stream, threading.Lock()
        self.shown = 0   # how many results and answers were shown: a question that adds none was not answered

    def send(self, kind: str, text: Any = "") -> None:
        with self.lock:
            self.shown += kind == "show"
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

def airports_shown(places) -> dict[str, Any]:
    """The airports found, by group, and what the traveller should know about them, for the list to untick."""
    from trip_planner.texts import tr
    from trip_planner.trip.places import airport_groups, too_far_notice
    notes = [tr("unknown_airports", entries=", ".join(places.unknown))] if places.unknown else []
    notes += [too_far_notice(places)] if places.too_far else []
    return {"groups": [{"title": title, "airports": [{key: airport[key] for key in ("code", "km", "name", "city", "country")}
                                                      for airport in airports]} for title, airports in airport_groups(places)],
            "notes": notes}

async def converse(settings: TripSettings, answers: Iterator[str], events: Events, from_map: dict[str, Any] | None = None) -> None:
    """A search, then the questions about it. from_map is what a Discover map said of this trip (maps.estimate_of)."""
    from trip_planner import planner
    from trip_planner.sources import flight_search
    from trip_planner.texts import tr
    from trip_planner.trip import places as where

    async def wait(kind: str, text: Any) -> dict[str, Any] | None:
        """Tell the app what is asked, and wait for the traveller's answer. None once the app is gone."""
        events.send(kind, text)
        line = await asyncio.to_thread(next, answers, None)
        if line is None:
            return None
        answer = json.loads(line)
        return answer if isinstance(answer, dict) else {}

    # A name that several places have is chosen first; then the airports are looked for
    while unclear := await asyncio.to_thread(where.unclear_places, settings):
        setting, name, options = unclear[0]
        answer = await wait("unclear", {"setting": setting, "name": name, "options": [label for label, _ in options]})
        if answer is None:
            return
        index = answer.get("index")
        where.choose_place(name, options[index if isinstance(index, int) and 0 <= index < len(options) else 0][1])
    try:   # by train or coach all the way, the two cities are all there is to find
        found = await asyncio.to_thread(where.cities_only if settings.ground_only else where.choose_airports, settings)
    except ValueError as error:   # the two places are too close, or one has no airport: the reason is the traveller's
        events.send("failed", str(error))
        return
    if settings.confirm_airports and not settings.ground_only:
        while True:
            answer = await wait("airports", airports_shown(found))
            if answer is None:
                return
            dropped = answer.get("dropped")
            try:
                where.remove_airports(found, [code for code in dropped if isinstance(code, str)] if isinstance(dropped, list) else [])
                break
            except ValueError as error:
                events.send("refused", str(error))
    elif not settings.ground_only:
        where.show_airports(found)
    if not settings.ground_only:
        await flight_search.connect()
    await planner.plan_trip(settings, found, from_map=from_map)
    for trip in planner.PLANNED:
        if trip.get("report") is not None:
            events.send("report", str(trip["report"]))
    while (answer := await wait("ready", "")) is not None:
        question = answer.get("question")
        if isinstance(question, str) and question.strip():
            before = events.shown
            await planner.ask(question.strip())
            if events.shown == before:   # the model did not answer: planner.ask said why in a progress line
                events.send("unanswered", tr("app_no_answer"))

async def run_task(asked: dict[str, Any], events: Events, answers: Iterator[str] = iter(())) -> None:
    """One of the program's own jobs: check every service, ask the ground prices of the
    reports again, or translate the program into one more language. Each ends with "done", or with "failed"."""
    from trip_planner import utils
    from trip_planner.texts import TEXTS, tr
    if asked["task"] == "check":
        from trip_planner.tools.check import check_setup
        failed = await check_setup()
        events.send("done", tr("app_check_failed" if failed else "app_check_passed"))
    elif asked["task"] == "prices":
        from trip_planner.tools import prices
        chosen = [Path(path) for path in asked["reports"]] if asked.get("reports") else None   # the ones ticked, or every one too old
        utils.show(await asyncio.to_thread(prices.refresh, chosen))
        events.send("done", tr("app_task_done"))
    elif asked["task"] == "language":
        from trip_planner.texts import translate
        name = str(asked.get("name") or "").strip()
        try:
            left = await translate.add(name)
        except translate.UnknownLanguage:
            events.send("failed", tr("ui_language_unknown", name=name))
            return
        except ValueError as error:
            events.send("failed", str(error))
            return
        translate.refresh()
        events.send("done", " ".join(tr("ui_language_added", name=TEXTS[code].get("language_name", code))
                                     + (f" ({tr('ui_language_incomplete', count=count)})" if count else "") for code, count in left.items()))
    elif asked["task"] == "map":
        from app.map_job import make_map
        await asyncio.to_thread(make_map, asked, events, answers)   # discover's Session runs its own event loops
    elif asked["task"] == "price_saved":   # a city of a map made before, priced as its page asks (map_job.py)
        from app.map_job import price_saved
        await asyncio.to_thread(price_saved, asked, events)
    else:
        events.send("failed", tr("app_not_a_trip"))

def failed(error: Exception, events: Events) -> None:
    """Something no one foresaw: the traveller is told its last line, and the whole trace goes
    with the progress lines below it, so that what went wrong can be told apart from that line."""
    from trip_planner.texts import tr
    for line in traceback.format_exc().splitlines():
        events.send("say", line)
    events.send("failed", tr("ui_failed", error=f"{type(error).__name__}: {error}"))

def main(answers: Iterator[str], events: Events) -> int:
    # The first line is waited for before settings.py is read: a spare process (search.py) may have waited a while
    # for its job, and settings.py may have changed meanwhile
    first = next(answers, "")
    from trip_planner import current, utils
    from trip_planner.texts import tr
    from trip_planner.trip.trip_settings import settings_file
    SETTINGS = settings_file().SETTINGS   # what the form does not hold; its dates, moved if passed, are the form's anyway
    current.SETTINGS = SETTINGS   # the reason a setting is refused is in the language of settings.py
    utils.LISTENERS["say"] = lambda text: events.send("say", text)
    utils.LISTENERS["show"] = lambda markdown: events.send("show", markdown)
    utils.LISTENERS["progress"] = lambda what: events.send("progress", what)
    try:
        asked = json.loads(first)
    except json.JSONDecodeError:
        events.send("failed", tr("app_not_a_trip"))
        return 1
    if isinstance(asked, dict) and "task" in asked:
        try:
            current.SETTINGS = dataclasses.replace(SETTINGS, language=asked.get("language") or SETTINGS.language)
            asyncio.run(run_task(asked, events, answers))
        except Exception as error:
            failed(error, events)
            return 1
        return 0
    try:
        settings = settings_for(asked.get("settings") or {} if isinstance(asked, dict) else {}, SETTINGS)
    except ValueError as error:
        events.send("failed", named(str(error)))
        return 1
    current.SETTINGS = settings
    try:
        from_map = asked.get("from_map") if isinstance(asked, dict) else None
        asyncio.run(converse(settings, answers, events, from_map if isinstance(from_map, dict) else None))
    except Exception as error:
        failed(error, events)
        return 1
    return 0

if __name__ == "__main__":
    out = sys.stdout
    out.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8")
    events = Events(out)
    sys.stdout = Printed(events)
    # The planner is loaded before the first line comes, about 12 seconds: a spare process (search.py) is then ready
    # when its search starts
    import trip_planner.planner  # noqa: F401,E402
    sys.exit(main(iter(sys.stdin.readline, ""), events))
