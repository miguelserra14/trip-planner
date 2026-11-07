"""What the app answers at each path. Reading a page is a GET; whatever changes something (a search started, an
answer to it, a question, a report kept, the language, one of the program's own jobs) is a POST, which must carry the token of this run of the
app: it is in the app's own forms, and another page cannot read it, so it cannot start a search that spends the
models' quota (see server.py)."""
import dataclasses
import html
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple
from urllib.parse import parse_qs, quote, unquote

from app import fields, maps, reports, search
from app.icons import icon
from app.job import settings_for
from app.report_page import page, STYLE, to_html
from trip_planner import current
from trip_planner.report.full import keep_report
from trip_planner.texts import LANGUAGES, REFERENCE, TEXTS, tr, translate
from trip_planner.tools.prices import PRICE_VALID_HOURS, stale_reports
from trip_planner.trip.form import named
from trip_planner.trip.trip_settings import TripSettings

KEY = "GOOGLE_API_KEY"   # the models' key, the only one a search cannot do without (tools/check.py)
SCRIPT = Path(__file__).with_name("page.js")   # the page's only script: the calendar, and the part of a search asked again

class Reply(NamedTuple):
    status: int
    html: str = ""
    location: str = ""   # where a 303 sends the browser
    kind: str = "text/html; charset=utf-8"
    policy: str = ""     # what the page may do, when not what the app's own pages may (server.POLICY)
    referrer: str = "no-referrer"   # what the browser tells other sites of the page their links and images come from

class Site:
    def __init__(self, defaults: TripSettings, start: Callable[[dict[str, Any]], search.Job] = search.Job,
                 stale: Callable[[], list] = stale_reports):
        self.defaults, self.start = defaults, start   # settings.py, and how a search or a job is started
        self.token = secrets.token_urlsafe(24)
        self.job: search.Job | None = None
        self.title = ""                               # what the job is: the trip, or the job's name
        self.values = fields.values_of(defaults)      # what the form shows: settings.py, then the last search
        self.stale = stale                            # the reports whose ground prices are too old to believe
        self.languages_added = False                  # a language job ran: the languages are read again once it ends
        self.map_job: search.Job | None = None        # the last Discover map, in a process of its own (map_job.py)
        self.maps: dict[str, search.Job] = {}         # the last maps (maps.MAPS_KEPT), by their address
        self.map_values = maps.values_of(defaults.origin)   # what Discover's form shows
        self.maps_made = 0                            # how many maps were asked for: each one's address is its number
        self.map_next: dict[str, Any] | None = None   # a map asked for while the last one stops: it starts once that one ended
        self.stop_asked = 0.0                         # when the last map was asked to stop (time.monotonic), or 0
        self.maps_lock = threading.Lock()             # the pages are answered in threads of their own: one map starts at a time
        self.quitting = threading.Event()             # set by the Quit button: the server stops (server.serve)
        self.theme = "dark"                           # "light" or "dark", turned by the button of the header: dark until then

    def begin(self, first: dict[str, Any], title: str) -> Reply:
        """Start a search or one of the program's own jobs, and show how it goes."""
        if self.job is not None and self.job.running:   # the last search, waiting for questions: it ends with it
            self.job.stop()
        self.job, self.title = self.start(first), title
        return Reply(303, location="/search")

    def languages_read(self) -> None:
        """Once a language job ended, its file is there: read the languages again."""
        if self.languages_added and not (self.job is not None and self.job.running):
            self.languages_added = False
            translate.refresh()

    def busy(self) -> bool:
        return self.job is not None and self.job.working

    def page(self, title: str, body: str, working: bool = False, here: str = "") -> str:
        """A page with the header: the parts of the app (here is the one shown), the language of the pages, light or
        dark, and the button that closes the program."""
        escape = lambda text: html.escape(str(text))
        languages = "".join(f'<option value="{code}"{" selected" if code == current.SETTINGS.language else ""}>'
                            f'{escape(TEXTS[code].get("language_name", code))}</option>' for code in LANGUAGES)
        parts = "".join(f'<a href="{path}"{" aria-current=\"page\"" if path == here else ""}>{escape(tr(key))}</a>'
                        for path, key in (("/", "app_plan_title"), ("/discover", "discover_win_title"), ("/program", "app_tools")))
        theme = (f'<form method="post" action="/theme" data-theme-form>{search.hidden(self.token)}<input type="hidden" name="shown">'
                 f'<input type="hidden" name="back"><button type="submit" class="quiet theme" '
                 f'title="{escape(tr("app_theme"))}" aria-label="{escape(tr("app_theme"))}">{icon("moon")}{icon("sun")}</button></form>')
        header = (f'<header><a href="/" class="name">{escape(tr("ui_title"))}</a><nav class="inline">{parts}</nav>'
                  f'<form method="post" action="/language">{search.hidden(self.token)}<label class="inline"><span class="visually-hidden">{escape(tr("ui_language"))}</span>'
                  f'<select name="language" data-submit>{languages}</select></label><noscript><button type="submit">OK</button></noscript></form>'
                  f'{theme}<form method="post" action="/quit">{search.hidden(self.token)}<button type="submit" class="quiet">{escape(tr("app_quit"))}</button></form></header>')
        return page(title, header + body, current.SETTINGS.language, refresh=3 if working else 0, theme=self.theme)

    def home(self, refusal: str = "", note: str = "") -> str:
        # The key is read from env/.env when the settings are (trip_settings.py): without it every search fails, so it
        # is said before one is tried
        missing = "" if os.environ.get(KEY) else f'<p class="refusal" role="alert">{html.escape(tr("app_no_key"))}</p>'
        body = missing + fields.trip_form(self.token, self.values, self.defaults, refusal, self.busy(), note)
        if self.job is not None:
            body += f'<p class="notice"><a href="/search">{html.escape(tr("app_last_search"))}</a></p>'
        stale = len(self.stale())
        if stale:   # the prices are asked again on the program's page: said here, where the reports are
            body += f'<p class="notice">{html.escape(tr("app_prices_old", count=stale))} <a href="/program">{html.escape(tr("ui_prices"))}</a></p>'
        return self.page(tr("app_plan_title"), body + reports.list_section(), here="/")

    def program_page(self) -> str:
        return self.page(tr("app_tools"), f'<h1>{html.escape(tr("app_tools"))}</h1>{self.tools()}', here="/program")

    def tools(self) -> str:
        """The program's own jobs: the ground prices asked again (when some are too old),
        the check of the services, and the languages."""
        def button(path: str, text: str, note: str = "", disabled: bool = False) -> str:
            off = " disabled" if disabled or self.busy() else ""
            small = f"<small>{html.escape(note)}</small>" if note else ""
            return (f'<form method="post" action="{path}" class="tool">{search.hidden(self.token)}'
                    f'<button type="submit" class="quiet"{off}>{html.escape(text)}</button>{small}</form>')
        stale = len(self.stale())
        prices = button("/prices", tr("ui_prices_count", count=stale) if stale else tr("ui_prices"),
                        "" if stale else tr("prices_none", hours=PRICE_VALID_HOURS), disabled=not stale)
        return (f'<div class="tools card">{prices}'
                f'{button("/check", tr("ui_check"), tr("app_check_note"))}'
                f'<p><a href="/languages">{html.escape(tr("ui_languages"))}</a></p></div>')

    def languages_page(self, asking: str = "", refusal: str = "", note: str = "") -> str:
        """The languages of the program, each with how complete it is, to add one or remove one. asking is the
        code of a language whose removal is to be confirmed: the page asks, and only the second request removes it."""
        rows = []
        for code in TEXTS:
            name = TEXTS[code].get("language_name", code)
            gaps = len(translate.missing(code, TEXTS))
            state = (tr("ui_language_reference") if code == REFERENCE else
                     tr("ui_language_incomplete", count=gaps) if gaps else tr("ui_language_complete"))
            remove = ""
            if code != REFERENCE:
                confirm = (f'<input type="hidden" name="confirm" value="1"><span class="refusal">'
                           f'{html.escape(tr("ui_language_confirm", name=name))}</span> ') if code == asking else ""
                off = " disabled" if self.busy() else ""
                remove = (f'<form method="post" action="/language/remove" class="tool">{search.hidden(self.token)}'
                          f'<input type="hidden" name="code" value="{html.escape(code)}">{confirm}'
                          f'<button type="submit" class="quiet"{off}>{html.escape(tr("ui_language_remove"))}</button></form>')
            rows.append(f"<tr><td><code>{html.escape(code)}</code></td><td>{html.escape(name)}</td>"
                        f"<td>{html.escape(state)}</td><td>{remove}</td></tr>")
        said = (f'<p class="refusal" role="alert">{html.escape(refusal)}</p>' if refusal else
                f'<p role="status">{html.escape(note)}</p>' if note else "")
        off = " disabled" if self.busy() else ""
        add = (f'<form method="post" action="/language/add" class="ask question">{search.hidden(self.token)}'
               f'<label>{html.escape(tr("app_language_new"))}<input name="name" required maxlength="40"></label>'
               f'<button type="submit"{off}>{html.escape(tr("ui_language_add"))}</button>'
               f'<small>{html.escape(tr("ui_languages_hint"))}</small></form>')
        body = (f'<p class="back"><a href="/program">← {html.escape(tr("app_tools"))}</a></p><h1>{html.escape(tr("app_languages"))}</h1>{said}'
                f'<div class="table card"><table><tbody>{"".join(rows)}</tbody></table></div>{add}')
        return self.page(tr("app_languages"), body, here="/program")

    def search_page(self) -> str:
        return self.page(self.title, f"<h1>{html.escape(self.title)}</h1>{self.live()}", working=self.job.working)

    def live(self) -> str:
        return search.live_part(self.job, self.token, reports.link_to, lambda markdown: to_html(markdown)[0])

    def not_found(self) -> Reply:
        text = html.escape(tr("app_not_found"))
        return Reply(404, self.page(tr("app_not_found"), f'<p>{text}</p><p><a href="/">{html.escape(tr("app_all_reports"))}</a></p>'))

    def get(self, path: str) -> Reply:
        self.languages_read()
        path, _, query = path.partition("?")
        asked = {name: values[0] for name, values in parse_qs(query).items()}
        if path == "/languages":
            return Reply(200, self.languages_page())
        if path == "/program":
            return Reply(200, self.program_page())
        if path.startswith("/discover"):
            return self.get_discover(path)
        if path in ("/", "/index.html"):
            return Reply(200, self.home())
        if path == "/page.js":
            return Reply(200, SCRIPT.read_text(encoding="utf-8"), kind="text/javascript; charset=utf-8")
        if path == "/page.css":
            return Reply(200, STYLE.read_text(encoding="utf-8"), kind="text/css; charset=utf-8")
        if path in ("/search", "/search/part"):
            if self.job is None:
                return Reply(303, location="/")
            return Reply(200, self.live() if path == "/search/part" else self.search_page())
        report = reports.find(path)
        if not report:
            return self.not_found()
        trip = reports.trip_of(report) if "edit" in asked else None
        if trip:   # the planner's form filled in with the report's trip, as a trip of the map fills it: nothing is searched yet
            self.values = {**self.values, **trip}
            return Reply(303, location="/")
        return Reply(200, reports.report_page(report, self.token, lambda title, body: self.page(title, body),
                                              view=asked.get("view", ""), sort=asked.get("sort", "")))

    def post(self, path: str, body: bytes) -> Reply:
        try:
            sent = parse_qs(body.decode("utf-8"), max_num_fields=200)
        except (UnicodeDecodeError, ValueError):
            sent = {}
        one = {name: values[0] for name, values in sent.items()}
        if not secrets.compare_digest(one.get("token", ""), self.token):
            return Reply(403, page(tr("app_not_this_app"), f"<p>{html.escape(tr('app_not_this_app'))}</p>", current.SETTINGS.language))
        answers = {"/search": self.post_search, "/stop": self.post_stop, "/place": self.post_place, "/airports": self.post_airports,
                   "/ask": self.post_question, "/keep": self.post_keep, "/language": self.post_language,
                   "/check": self.post_check, "/prices": self.post_prices, "/language/add": self.post_add_language,
                   "/language/remove": self.post_remove_language, "/discover": self.post_discover,
                   "/discover/stop": self.post_discover_stop, "/restore": self.post_restore, "/quit": self.post_quit,
                   "/theme": self.post_theme}
        return answers[path](one, sent) if path in answers else self.not_found()

    def post_search(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if self.busy():   # one search at a time: the one running is shown
            return Reply(303, location="/search")
        values = fields.read(one, self.defaults)
        try:
            settings = settings_for(values, self.defaults)   # checked here too, so that a mistake is answered on the form at once
        except ValueError as error:
            self.values = self.form_values(one)
            return Reply(400, self.home(named(str(error))))
        self.values = fields.values_of(settings)   # as the settings read them: "sexta" becomes its day
        current.SETTINGS = dataclasses.replace(current.SETTINGS, language=settings.language)   # the pages follow the trip's language
        return self.begin({"settings": values}, f"{settings.origin} → {settings.destination}")

    def form_values(self, one: dict[str, str]) -> dict[str, Any]:
        """What the form showed when it was sent, to show it again with the reason it was refused."""
        values = {**fields.values_of(self.defaults), **{name: value for name, value in one.items() if name != "token"}}
        for name in fields.ADVANCED:
            if fields.kind_of(name) is bool:
                values[name] = name in one
        values["departure_scale"] = "wide" if "departure_scale" in one else "near"
        values["arrival_scale"] = "wide" if "arrival_scale" in one else "near"
        values["max_stopovers"] = fields.stopovers(one)
        return values

    def post_restore(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        """Every setting back to what settings.py says, the main card's ticks included: the
        places, the days and the language stay as the form has them."""
        kept = {name: one.get(name, "") for name in fields.TRIP if name in one}
        self.values = {**fields.values_of(self.defaults), **kept}
        return Reply(200, self.home(note=tr("ui_restored")))

    def post_quit(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        """The program closed from its page: the search stops and the map is asked to, then the server stops and
        gives the map its while to end (server.serve, close)."""
        self.map_next = None
        if self.job is not None and self.job.running:
            self.job.stop()
        if self.map_job is not None and self.map_job.running:
            self.ask_map_to_stop()
        self.quitting.set()
        return Reply(200, page(tr("app_quit"), f'<p role="status">{html.escape(tr("app_quit_done"))}</p>', current.SETTINGS.language))

    def post_stop(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if self.job is not None and self.job.running:
            self.job.stop()
        return Reply(303, location="/search")

    def post_place(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if self.job is not None and self.job.waiting("unclear"):
            index = one.get("index", "0")
            self.job.answer({"index": int(index) if index.isdigit() else 0})
        return Reply(303, location="/search")

    def post_airports(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        asked = self.job.waiting("airports") if self.job is not None else None
        if asked:
            kept = set(sent.get("keep", []))
            listed = [airport["code"] for group in asked["groups"] for airport in group["airports"]]
            self.job.answer({"dropped": [code for code in listed if code not in kept]})
        return Reply(303, location="/search")

    def post_question(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        question = one.get("question", "").strip()[:500]
        if self.job is not None and self.job.waiting("ready") is not None and question:
            self.job.answer({"question": question})
        return Reply(303, location="/search#conversation")   # where the answer will be

    def post_keep(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        """Keep a report in saved/, or put it back among the recent ones: its whole folder, both ways of a round trip."""
        report = reports.find(reports.REPORT_PATH + one.get("report", ""))
        if not report:
            return self.not_found()
        keep = one.get("keep") == "1"
        moved = keep_report(report, keep)
        return Reply(303, location=reports.REPORT_PATH + quote(f"{'saved' if keep else 'recent'}/{moved.stem}"))

    def post_theme(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        """Dark if the pages were light, light if they were dark. They start dark (the user's choice of 2026-11-07)."""
        shown = self.theme or one.get("shown", "light")
        self.theme = "light" if shown == "dark" else "dark"
        return Reply(303, location=one.get("back", "/") if one.get("back", "").startswith("/") else "/")

    def post_language(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if one.get("language") in LANGUAGES:
            current.SETTINGS = dataclasses.replace(current.SETTINGS, language=one["language"])
            self.values["language"] = one["language"]
        return Reply(303, location="/")

    # ---------------------------------------------------------------- Discover (maps.py)
    def discover_page(self, refusal: str = "") -> str:
        body = maps.form(self.token, self.map_values, refusal)
        if self.map_next is not None:
            body += f'<p role="status">{html.escape(tr("discover_win_stopping"))}</p>'
        if self.map_job is not None:
            body += self.map_live()   # with its button to stop the map, while it works
        return self.page(tr("discover_win_title"), body, working=self.map_next is not None or
                         (self.map_job is not None and self.map_job.working), here="/discover")

    def map_live(self) -> str:
        return search.live_part(self.map_job, self.token, reports.link_to, lambda markdown: to_html(markdown)[0], part="/discover/part",
                                stop="/discover/stop")

    def get_discover(self, path: str) -> Reply:
        """The form and how the map goes; the map's page and its destinations as they change; a trip of the map
        opening the planner's form, by its key among the map's own trips."""
        self.map_turn()
        if path == "/discover":
            return Reply(200, self.discover_page())
        if self.map_job is None:
            return Reply(303, location="/discover")
        if path == "/discover/part":
            return Reply(200, self.map_live())
        address, what = maps.part_of(path)
        job = self.maps.get(address)
        if job is None:   # not a map, or one too old to be kept
            return self.not_found()
        if what == "":
            if not job.map_page:
                return Reply(303, location="/discover")
            return Reply(200, job.map_page, policy=maps.MAP_POLICY, referrer=maps.MAP_REFERRER)
        if what == "state":
            return Reply(200, json.dumps(job.map_state, ensure_ascii=False), kind="application/json; charset=utf-8")
        if what in maps.FILES:   # the page's script and style
            file, kind = maps.FILES[what]
            return Reply(200, file.read_text(encoding="utf-8"), kind=kind)
        if what.startswith("plan/"):
            trip = job.trips.get(unquote(what[len("plan/"):]))
            if trip:
                self.values = maps.planner_values(trip, self.values)
                return Reply(303, location="/")
        return self.not_found()

    def post_discover(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        asked = maps.read(one)
        self.map_values = {**asked, "look_up": "on" if asked["look_up"] else ""}
        refusal = maps.refusal_of(asked)
        if refusal:
            return Reply(400, self.discover_page(refusal))
        with self.maps_lock:
            self.map_next = {"task": "map", "language": current.SETTINGS.language, **asked}   # the form as it is now
        if self.map_job is not None and self.map_job.running:   # two maps would write the same caches (discover/cache)
            self.ask_map_to_stop()
        self.map_turn()
        return Reply(303, location="/discover")

    def post_discover_stop(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        self.map_next = None
        if self.map_job is not None and self.map_job.running:
            self.ask_map_to_stop()
        return Reply(303, location="/discover")

    def ask_map_to_stop(self) -> None:
        if not self.stop_asked:   # once: the time it was asked is the one it is given
            self.stop_asked = time.monotonic()
            self.map_job.ask_to_stop()

    def map_turn(self) -> None:
        """A map asked to stop is killed if it has not ended in time; once it ended, the map asked for next starts.
        Asked at each look at Discover's pages, which ask again every few seconds while a map works or waits."""
        with self.maps_lock:
            last = self.map_job
            if last is not None and last.running and self.stop_asked and time.monotonic() - self.stop_asked > maps.STOP_SECONDS:
                last.stop()
            if last is not None and last.running or self.map_next is None:
                return
            asked, self.map_next, self.stop_asked = self.map_next, None, 0.0
            self.maps_made += 1
            address = maps.address_of(self.maps_made)
            self.map_job = self.start({**asked, "planner_link": address + "plan/"})
            self.map_job.map_link = address
            self.maps[address] = self.map_job
            while len(self.maps) > maps.MAPS_KEPT:   # the oldest first: a dict keeps its order
                del self.maps[next(iter(self.maps))]

    def post_check(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if self.busy():
            return Reply(303, location="/search")
        return self.begin({"task": "check", "language": current.SETTINGS.language}, tr("ui_check"))

    def post_prices(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if self.busy():
            return Reply(303, location="/search")
        return self.begin({"task": "prices", "language": current.SETTINGS.language}, tr("ui_prices"))

    def post_add_language(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        name = one.get("name", "").strip()[:40]
        if self.busy():
            return Reply(303, location="/search")
        if not name:
            return Reply(303, location="/languages")
        self.languages_added = True
        return self.begin({"task": "language", "name": name, "language": current.SETTINGS.language},
                          tr("ui_language_adding", name=name))

    def post_remove_language(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        """Remove a language once it is confirmed: the first request only asks, in the page, whether to."""
        code = one.get("code", "")
        if code not in TEXTS or code == REFERENCE or self.busy():
            return Reply(303, location="/languages")
        if one.get("confirm") != "1":
            return Reply(200, self.languages_page(asking=code))
        name = TEXTS[code].get("language_name", code)
        try:
            translate.remove(code)
        except ValueError as error:
            return Reply(400, self.languages_page(refusal=str(error)))
        translate.refresh()
        # A page or a trip in a language that is gone carries on in English
        if current.SETTINGS.language not in LANGUAGES:
            current.SETTINGS = dataclasses.replace(current.SETTINGS, language=REFERENCE)
        if self.values.get("language") not in LANGUAGES:
            self.values["language"] = REFERENCE
        note = " ".join([tr("ui_language_removed", name=name)] + [tr("ui_language_in_settings", code=code)] * translate.in_settings(code))
        return Reply(200, self.languages_page(note=note))

    def close(self) -> None:
        """The program closing: the search stops, the map is asked to, and given a while to save what it found."""
        search.close_spare()   # first: a job that ends would start another one
        self.map_next = None
        if self.map_job is not None and self.map_job.running:
            self.ask_map_to_stop()
        if self.job is not None and self.job.running:
            self.job.stop()
        ends = time.monotonic() + maps.STOP_SECONDS
        while self.map_job is not None and self.map_job.running and time.monotonic() < ends:
            time.sleep(0.2)
        if self.map_job is not None and self.map_job.running:
            self.map_job.stop()
