"""A search started from the browser: the form, the process that plans the trip (job.py) and the page that shows
how it goes. One search at a time: each one spends the models' quota, and the planner's own requests are already
spread over its services.

The page of a running search asks for itself again every few seconds (a refresh, no script), and stops once the
search has ended: then it links its report."""
import html
import json
import subprocess
import sys
import threading
from pathlib import Path

from app.report_page import page, to_html
from trip_planner import current
from trip_planner.texts import tr
from trip_planner.trip.trip_settings import TripSettings

ROOT = Path(__file__).parents[1]   # the planner is run from the repository's folder
COMMAND = [sys.executable, "-m", "app.job"]
REFRESH_SECONDS = 3   # how often the page of a running search asks for itself again
LINES_SHOWN = 40      # the last progress lines on the page; the whole log is saved next to the report

class Job:
    """The process of one search and what it said so far. Its lines are read in a thread of their own."""

    def __init__(self, asked: dict[str, str], command: list[str] = COMMAND):
        self.asked = asked
        self.lines: list[str] = []      # the progress lines
        self.shown: list[str] = []      # the Markdown of the result, as it came
        self.reports: list[Path] = []
        self.failure = ""
        self.stopped = False
        self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
        self.process.stdin.write(json.dumps(asked, ensure_ascii=False))
        self.process.stdin.close()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self) -> None:
        for line in self.process.stdout:
            try:
                event = json.loads(line)
                kind, text = event["kind"], event["text"]
            except (ValueError, KeyError, TypeError):
                kind, text = "say", line.rstrip("\n")   # a warning of a library, on stderr: a line like any other
            if kind == "show":
                self.shown.append(text)
            elif kind == "report":
                self.reports.append(Path(text))
            elif kind == "failed":
                self.failure = text
            elif text.strip():
                self.lines.append(text)
        code = self.process.wait()
        if code and not self.failure and not self.stopped:
            self.failure = tr("app_search_ended", code=code)

    @property
    def running(self) -> bool:
        return self.process.poll() is None or self.reader.is_alive()

    def stop(self) -> None:
        if self.process.poll() is None:
            self.stopped = True
            self.process.kill()
        self.reader.join(timeout=5)

def form(token: str, asked: dict[str, str], refusal: str = "", busy: bool = False) -> str:
    """The short form: where from, where to, and the days. Every other setting is the one in settings.py."""
    field = lambda name, label, hint="": (
        f'<label>{html.escape(tr(label))}<input name="{name}" value="{html.escape(asked.get(name, ""))}"'
        f'{" required" if not hint else ""}>{f"<small>{html.escape(tr(hint))}</small>" if hint else ""}</label>')
    error = f'<p class="refusal" role="alert">{html.escape(refusal)}</p>' if refusal else ""
    button = (f'<a href="/search">{html.escape(tr("app_search_running"))}</a>' if busy
              else f'<button type="submit">{html.escape(tr("app_search"))}</button>')
    return (f'<form method="post" action="/search" class="search"><h1>{html.escape(tr("app_plan_title"))}</h1>{error}'
            f'<input type="hidden" name="token" value="{html.escape(token)}">'
            f'{field("origin", "origin")}{field("destination", "destination")}'
            f'{field("dates", "app_dates", "app_dates_hint")}{field("return_dates", "app_return_dates", "app_return_dates_hint")}'
            f'<p class="muted">{html.escape(tr("app_other_settings"))}</p>{button}</form>')

def asked_of(defaults: TripSettings) -> dict[str, str]:
    """What the form starts with: the trip in settings.py."""
    return {"origin": defaults.origin, "destination": defaults.destination,
            "dates": ", ".join(defaults.dates), "return_dates": ", ".join(defaults.return_dates)}

def progress_page(job: Job, token: str, report_link) -> str:
    """How the search goes: its last lines and the result as soon as it is shown; once it ended, its reports."""
    trip = html.escape(f"{job.asked.get('origin')} → {job.asked.get('destination')}")
    lines = "\n".join(html.escape(line) for line in job.lines[-LINES_SHOWN:])
    parts = [f'<p><a href="/">{html.escape(tr("app_all_reports"))}</a></p>', f"<h1>{trip}</h1>"]
    if job.running:
        parts.append(f'<p role="status">{html.escape(tr("app_search_running"))}</p>'
                     f'<form method="post" action="/stop"><input type="hidden" name="token" value="{html.escape(token)}">'
                     f'<button type="submit">{html.escape(tr("app_stop"))}</button></form>')
    elif job.failure:
        parts.append(f'<p class="refusal" role="alert">{html.escape(job.failure)}</p>')
    elif job.stopped:
        parts.append(f'<p role="status">{html.escape(tr("app_search_stopped"))}</p>')
    else:
        links = "".join(f"<li>{report_link(path)}</li>" for path in job.reports)
        parts.append(f'<p role="status">{html.escape(tr("app_search_done"))}</p><ul>{links}</ul>')
    if job.shown:
        parts.append(f'<article>{"".join(to_html(markdown)[0] for markdown in job.shown)}</article>')
    parts.append(f'<details{" open" if not job.shown else ""}><summary>{html.escape(tr("app_progress"))}</summary>'
                 f'<pre class="log">{lines}</pre></details>')
    title = f"{job.asked.get('origin')} → {job.asked.get('destination')}"
    return page(title, "\n".join(parts), current.SETTINGS.language, refresh=REFRESH_SECONDS if job.running else 0)
