"""What the app answers at each path. Reading a page is a GET; starting or stopping a search is a POST, which must
carry the token of this run of the app: it is in the app's own forms, and another page cannot read it, so it
cannot start a search that spends the models' quota (see server.py)."""
import html
import secrets
from typing import Callable, NamedTuple
from urllib.parse import parse_qs

from app import reports, search
from app.job import FIELDS, settings_for
from app.report_page import page
from trip_planner import current
from trip_planner.texts import tr
from trip_planner.trip.trip_settings import TripSettings

class Reply(NamedTuple):
    status: int
    html: str = ""
    location: str = ""   # where a 303 sends the browser

class Site:
    def __init__(self, defaults: TripSettings, start: Callable[[dict[str, str]], search.Job] = search.Job):
        self.defaults, self.start = defaults, start   # settings.py, and how a search is started
        self.token = secrets.token_urlsafe(24)
        self.job: search.Job | None = None

    def busy(self) -> bool:
        return self.job is not None and self.job.running

    def home(self, asked: dict[str, str] | None = None, refusal: str = "") -> str:
        body = search.form(self.token, asked or search.asked_of(self.defaults), refusal, self.busy()) + reports.list_section()
        return page(tr("app_plan_title"), body, current.SETTINGS.language)

    def not_found(self) -> Reply:
        text = html.escape(tr("app_not_found"))
        return Reply(404, page(tr("app_not_found"), f'<p>{text}</p><p><a href="/">{html.escape(tr("app_all_reports"))}</a></p>',
                               current.SETTINGS.language))

    def get(self, path: str) -> Reply:
        if path in ("/", "/index.html"):
            return Reply(200, self.home())
        if path == "/search":
            if self.job is None:
                return Reply(303, location="/")
            return Reply(200, search.progress_page(self.job, self.token, reports.link_to))
        report = reports.find(path)
        return Reply(200, reports.report_page(report)) if report else self.not_found()

    def post(self, path: str, body: bytes) -> Reply:
        try:
            sent = {name: values[0] for name, values in parse_qs(body.decode("utf-8"), max_num_fields=10).items()}
        except (UnicodeDecodeError, ValueError):
            sent = {}
        if not secrets.compare_digest(sent.get("token", ""), self.token):
            return Reply(403, page(tr("app_not_this_app"), f"<p>{html.escape(tr('app_not_this_app'))}</p>", current.SETTINGS.language))
        if path == "/stop":
            if self.busy():
                self.job.stop()
            return Reply(303, location="/search")
        if path != "/search":
            return self.not_found()
        if self.busy():   # one search at a time: the one running is shown
            return Reply(303, location="/search")
        asked = {name: sent.get(name, "").strip() for name in FIELDS}
        try:
            settings_for(asked, self.defaults)   # checked here too, so that a mistake is answered on the form at once
        except ValueError as error:
            return Reply(400, self.home(asked, str(error)))
        self.job = self.start(asked)
        return Reply(303, location="/search")

    def close(self) -> None:
        if self.busy():
            self.job.stop()
