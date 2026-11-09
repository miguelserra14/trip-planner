"""A search started from the browser: the process that plans the trip (job.py), what it said, and the part of the
page that shows how it goes. One search at a time: each one spends the models' quota, and the planner's own
requests are already spread over its services. The program's own jobs (checking the services, asking the ground
prices again, adding a language) run the same way, one at a time with the searches.

The process asks the traveller things on the way: which place a name means, which airports
to leave out; once the trip is planned it takes questions about it. Each of those is a form in the page, sent
back here and passed on to the process (site.py). While the process works the page asks for its part again every
few seconds (page.js), or, without the script, the whole page."""
import html
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.waiting import waiting_part
from trip_planner.report.full import past_durations
from trip_planner.texts import tr

ROOT = Path(__file__).parents[1]   # the planner is run from the repository's folder
COMMAND = [sys.executable, "-m", "app.job"]
LINES_SHOWN = 40      # the last progress lines on the page; the whole log is saved next to the report

# A process started before it is needed, with the planner already loaded (about 12 seconds, see job.py), for the
# next search or job to take. The next one is started once the job that took it has searched (it said "ready" or
# "done", or ended), not before: a process reads the caches of resources/ as it loads, and would miss what that job
# writes to them. Only the app keeps one (keep_spares): the jobs of a test do not.
SPARE: dict[str, Any] = {"keep": False, "process": None}
SPARE_LOCK = threading.Lock()

def started(command: list[str]) -> subprocess.Popen:
    return subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")

def keep_spares() -> None:
    """Keep a spare process from now on, the first one at once."""
    SPARE["keep"] = True
    start_spare()

def start_spare() -> None:
    with SPARE_LOCK:
        if SPARE["keep"] and SPARE["process"] is None:
            SPARE["process"] = started(COMMAND)

def take_spare() -> subprocess.Popen | None:
    """The spare process, if there is one still running: it is no longer the spare."""
    with SPARE_LOCK:
        process, SPARE["process"] = SPARE["process"], None
    return process if process is not None and process.poll() is None else None

def close_spare() -> None:
    """The program closing: no more spare process, and the one waiting ends."""
    with SPARE_LOCK:
        SPARE["keep"] = False
        process, SPARE["process"] = SPARE["process"], None
    if process is not None and process.poll() is None:
        process.kill()

class Job:
    """The process of one search, what it said so far, and what it waits for. Its lines are read in a thread."""

    def __init__(self, first: dict[str, Any], command: list[str] = COMMAND):
        """first is what the process is told first: {"settings": ...} for a search, {"task": ...} for a job."""
        self.nothing_said(first)
        self.spare_after = command == COMMAND   # a job of the planner: the spare it took is replaced once it has searched
        self.process = (take_spare() if self.spare_after else None) or started(command)
        self.tell(first)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def nothing_said(self, first: dict[str, Any]) -> None:
        """What is known of the job before its process says anything."""
        self.first = first
        self.values = first.get("settings") or {}   # the settings of the form, for a search
        self.lines: list[str] = []      # the progress lines
        self.shown: list[str] = []      # the Markdown of the result, as it came
        # The questions about the trip planned, in order: {"question", "answer" (Markdown), "unanswered" (why not)}.
        # The one still waiting has neither
        self.conversation: list[dict[str, str]] = []
        # How many progress lines there were when the result was first shown: what the planner says after it (the
        # recommendation the model did not write, where the report was saved) is shown with the result, not folded
        self.result_at: int | None = None
        self.reports: list[Path] = []
        self.failure = ""
        self.refusal = ""               # why the airports left out cannot be: the list is asked for again
        self.stopped = False
        self.done = ""                  # how one of the program's own jobs went, once it ended (job.run_task)
        self.map_page = ""              # for a Discover map: its page, its trips by key, and its state as it changes
        self.map_link = ""              # ... and its address in the app (maps.address_of), given by the app
        self.trips: dict[str, list[str]] = {}
        self.map_state: dict[str, Any] = {}
        self.asked: tuple[str, Any] | None = None   # what the process waits for: ("unclear" | "airports" | "ready", what)
        # Where the search is, one entry per way of the trip (utils.progress): {"trip", "step", "counts"}, and when
        # the first way started, for how long is left (waiting.py): the time the traveller took to tick airports is not the search's
        self.ways: list[dict[str, Any]] = []
        self.started: float | None = None
        self.lock = threading.Lock()

    def tell(self, message: dict[str, Any]) -> None:
        with self.lock:
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):   # the process has ended, its pipe closed: what it said is already read
                pass

    def read(self) -> None:
        for line in self.process.stdout:
            try:
                event = json.loads(line)
                kind, text = event["kind"], event["text"]
            except (ValueError, KeyError, TypeError):
                kind, text = "say", line.rstrip("\n")   # a warning of a library, on stderr: a line like any other
            self.read_event(kind, text)
            if kind in ("ready", "done") and self.spare_after:
                start_spare()
        code = self.process.wait()
        if self.spare_after:
            start_spare()
        with self.lock:   # nothing more is told to it, nor read: an app open for days does not keep them
            for pipe in (self.process.stdin, self.process.stdout):
                try:
                    pipe.close()
                except OSError:
                    pass
        self.asked = None
        if code and not self.failure and not self.stopped:
            self.failure = tr("app_search_ended", code=code)

    def read_event(self, kind: str, text: Any) -> None:
        """What one line of the process says. Shown while a question waits, it is that question's answer."""
        if kind == "show" and self.pending():
            self.pending()["answer"] = text
        elif kind == "show":
            self.shown.append(text)
            if self.result_at is None:
                self.result_at = len(self.lines)
        elif kind == "unanswered" and self.pending():
            self.pending()["unanswered"] = text
        elif kind == "report":
            self.reports.append(Path(text))
        elif kind == "failed":
            self.failure = text
        elif kind == "done":
            self.done = text
        elif kind == "map":       # a Discover map (map_job.py): its page, its trips, what changes in it
            self.map_page = text
        elif kind == "trips":
            self.trips = text
        elif kind == "state":
            self.map_state = text
        elif kind == "refused":
            self.refusal = text
        elif kind == "progress" and isinstance(text, dict):
            self.read_progress(text)
        elif kind in ("unclear", "airports", "ready"):
            self.asked = (kind, text)
        elif isinstance(text, str) and text.strip():
            self.lines.append(text)

    def read_progress(self, what: dict[str, Any]) -> None:
        """A way of the trip that starts, a step that starts, or what a step found."""
        if "trip" in what:
            self.ways.append({"trip": what["trip"], "step": "", "counts": {}})
            self.started = self.started or time.time()
        elif self.ways and "step" in what:
            self.ways[-1]["step"] = what["step"]
        elif self.ways and "found" in what:
            self.ways[-1]["counts"][what["found"]] = what.get("count", 0)
            if "departure" in what:   # a Discover map's airports, found as one of its steps
                self.ways[-1]["trip"]["departure"] = what["departure"]

    @property
    def running(self) -> bool:
        return self.process.poll() is None or self.reader.is_alive()

    @property
    def working(self) -> bool:
        """Running and not waiting for the traveller: a new search would have to wait for this one."""
        return self.running and self.asked is None

    def waiting(self, kind: str) -> Any:
        """What the process asked, if it waits for that kind of answer now."""
        asked = self.asked
        return asked[1] if asked and asked[0] == kind else None

    def pending(self) -> dict[str, str] | None:
        """The question the model is answering now, if there is one."""
        last = self.conversation[-1] if self.conversation else None
        return last if last and not last["answer"] and not last["unanswered"] else None

    def answer(self, message: dict[str, Any]) -> None:
        self.asked, self.refusal = None, ""
        if "question" in message:
            self.conversation.append({"question": message["question"], "answer": "", "unanswered": ""})
        self.tell(message)

    def ask_to_stop(self) -> None:
        """Ask the process to end at its next step, keeping what it found (a Discover map: map_job.py). It may take a
        while: stop() is for one that has not ended in time."""
        if self.running:
            self.stopped = True
            self.tell({"stop": True})

    def stop(self) -> None:
        if self.process.poll() is None:
            self.stopped = True
            self.process.kill()
        self.reader.join(timeout=5)

def hidden(token: str) -> str:
    return f'<input type="hidden" name="token" value="{html.escape(token)}">'

def place_form(asked: dict[str, Any], token: str) -> str:
    options = "".join(f'<label class="tick"><input type="radio" name="index" value="{number}"{" checked" if number == 0 else ""}> '
                      f"{html.escape(label)}</label>" for number, label in enumerate(asked["options"]))
    return (f'<form method="post" action="/place" class="ask"><h2>{html.escape(tr("ui_place_title"))}</h2>'
            f'<p>{html.escape(tr("place_several", name=asked["name"]))}</p>{hidden(token)}{options}'
            f'<button type="submit">{html.escape(tr("ui_place_use"))}</button></form>')

def airports_form(asked: dict[str, Any], token: str, refusal: str) -> str:
    """Every airport found, ticked, by group: the ones unticked are left out."""
    groups = []
    for group in asked["groups"]:
        boxes = "".join(f'<label class="airport"><input type="checkbox" name="keep" value="{html.escape(airport["code"])}" checked>'
                        f'<b>{html.escape(airport["code"])}</b><span>{html.escape(airport["name"])}'
                        f'<small>{html.escape(airport["city"])}, {html.escape(airport["country"])}</small></span>'
                        f'<span class="muted">{airport["km"]} km</span></label>'
                        for airport in group["airports"])
        groups.append(f'<fieldset><legend>{html.escape(group["title"])}</legend>{boxes or html.escape(tr("none"))}</fieldset>')
    notes = "".join(f'<p class="muted">{html.escape(note)}</p>' for note in asked.get("notes") or [])
    error = f'<p class="refusal" role="alert">{html.escape(refusal)}</p>' if refusal else ""
    return (f'<form method="post" action="/airports" class="ask"><p>{html.escape(tr("ui_airports_instruction"))}</p>{error}'
            f'{hidden(token)}<div class="groups">{"".join(groups)}</div>{notes}<button type="submit">{html.escape(tr("ui_search"))}</button></form>')

def question_form(token: str) -> str:
    return (f'<form method="post" action="/ask" class="ask question">{hidden(token)}'
            f'<label>{html.escape(tr("ui_question"))}<input name="question" required maxlength="500"></label>'
            f'<button type="submit">{html.escape(tr("ui_ask"))}</button><small>{html.escape(tr("followup_note"))}</small></form>')

def conversation_part(job: Job, token: str, to_html: Callable[[str], str]) -> str:
    """The questions about the trip and their answers, under the result and apart from it, with the one being
    answered and the form for the next. The page is sent here (#conversation) after a question, so that the
    answer is seen where it arrives, not at the end of a long report."""
    items = []
    for item in job.conversation:
        if item["answer"]:   # the answer repeats the question, in bold, and says it was written by a model
            items.append(f'<div class="answer">{to_html(item["answer"])}</div>')
        else:
            said = (f'<p class="refusal" role="alert">{html.escape(item["unanswered"])}</p>' if item["unanswered"]
                    else f'<p role="status">{html.escape(tr("ui_asking"))}</p>')
            items.append(f'<div class="answer"><p><strong>{html.escape(item["question"])}</strong></p>{said}</div>')
    form = question_form(token) if job.waiting("ready") is not None else ""
    return (f'<section id="conversation"><h2>{html.escape(tr("app_questions"))}</h2>{"".join(items)}{form}</section>'
            if items or form else "")

def live_part(job: Job, token: str, report_link: Callable[[Path], str], to_html: Callable[[str], str],
              part: str = "/search/part", stop: str = "/stop") -> str:
    """The part of the page that changes while the search runs: what it waits for, the result as soon as it is
    shown, and the progress lines, with the one button that stops it (a map's own on Discover's page). Marked as
    working while the page should ask for it again."""
    parts = []
    if job.failure:
        parts.append(f'<p class="refusal" role="alert">{html.escape(job.failure)}</p>')
    elif job.stopped:
        parts.append(f'<p role="status">{html.escape(tr("app_search_stopped"))}</p>')
    elif job.waiting("unclear"):
        parts.append(place_form(job.waiting("unclear"), token))
    elif job.waiting("airports"):
        parts.append(airports_form(job.waiting("airports"), token, job.refusal))
    elif job.working and job.pending():   # the search is over: only the model is answering a question
        parts.append(f'<p role="status">{html.escape(tr("ui_asking"))}</p>')
    elif job.working and job.ways and job.ways[-1]["step"] != "done":   # a way of the trip being searched: its map and steps
        parts.append(waiting_part(job.ways, bool(job.values.get("return_dates")), time.time() - (job.started or time.time()), past_durations()))
        parts.append(f'<form method="post" action="{stop}">{hidden(token)}<button type="submit" class="quiet">'
                     f'{html.escape(tr("app_stop"))}</button></form>')
    elif job.working:
        last = job.lines[-1] if job.lines else tr("ui_finding")
        parts.append(f'<p role="status"><b>{html.escape(tr("app_search_running" if job.values else "app_working"))}</b> {html.escape(last)}</p>'
                     f'<form method="post" action="{stop}">{hidden(token)}<button type="submit" class="quiet">'
                     f'{html.escape(tr("app_stop"))}</button></form>')
    elif job.done:
        parts.append(f'<p role="status">{html.escape(job.done)}</p>')
    if job.map_page and job.map_link:   # a Discover map can be opened as soon as its flights are found, while the rest goes on
        parts.insert(1 if job.failure or job.done else 0,
                     f'<p><a href="{html.escape(job.map_link)}" target="_blank" class="open" data-open-map>{html.escape(tr("app_open_map"))}</a></p>')
    elif job.reports:
        links = "".join(f"<li>{report_link(path)}</li>" for path in job.reports)
        notes = "".join(f'<li class="muted">{html.escape(line)}</li>' for line in job.lines[job.result_at or len(job.lines):])
        parts.append(f'<p role="status">{html.escape(tr("app_search_done"))}</p><ul>{links}</ul>'
                     + (f'<ul class="notes">{notes}</ul>' if notes else ""))
    if job.shown:
        parts.append(f'<article>{"".join(to_html(markdown) for markdown in job.shown)}</article>')
    parts.append(conversation_part(job, token, to_html))
    lines = "\n".join(html.escape(line) for line in job.lines[-LINES_SHOWN:])
    parts.append(f'<details{" open" if not job.shown else ""}><summary>{html.escape(tr("app_progress"))}</summary>'
                 f'<pre class="log">{lines}</pre></details>')
    working = ' data-working="1"' if job.working else ""
    return f'<section id="live" data-part="{part}"{working}>{"".join(parts)}</section>'
