"""What the app answers at each path. Reading a page is a GET; whatever changes something (a search started, an
answer to it, a question, a report kept, the language, one of the program's own jobs) is a POST, which must carry the token of this run of the
app: it is in the app's own forms, and another page cannot read it, so it cannot start a search that spends the
models' quota (see server.py)."""
import dataclasses
import html
import secrets
from pathlib import Path
from typing import Any, Callable, NamedTuple
from urllib.parse import parse_qs, quote

from app import fields, reports, search
from app.job import settings_for
from app.report_page import page, to_html
from trip_planner import current
from trip_planner.report.full import keep_report
from trip_planner.texts import LANGUAGES, REFERENCE, TEXTS, tr, translate
from trip_planner.tools.prices import PRICE_VALID_HOURS, stale_reports
from trip_planner.trip.trip_settings import TripSettings

SCRIPT = Path(__file__).with_name("page.js")   # the page's only script: the calendar, and the part of a search asked again

class Reply(NamedTuple):
    status: int
    html: str = ""
    location: str = ""   # where a 303 sends the browser
    kind: str = "text/html; charset=utf-8"

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

    def begin(self, first: dict[str, Any], title: str) -> Reply:
        """Start a search or one of the program's own jobs, and show how it goes."""
        if self.job is not None and self.job.running:   # the last search, waiting for questions: it ends with it
            self.job.stop()
        self.job, self.title = self.start(first), title
        return Reply(303, location="/search")

    def languages_read(self) -> None:
        """Once a language job ended, its file is there: read the languages again, as the window does."""
        if self.languages_added and not (self.job is not None and self.job.running):
            self.languages_added = False
            translate.refresh()

    def busy(self) -> bool:
        return self.job is not None and self.job.working

    def page(self, title: str, body: str, working: bool = False) -> str:
        languages = "".join(f'<option value="{code}"{" selected" if code == current.SETTINGS.language else ""}>'
                            f'{html.escape(TEXTS[code].get("language_name", code))}</option>' for code in LANGUAGES)
        header = (f'<header><a href="/" class="name">{html.escape(tr("ui_title"))}</a>'
                  f'<form method="post" action="/language">{search.hidden(self.token)}<label class="inline">{html.escape(tr("ui_language"))} '
                  f'<select name="language" data-submit>{languages}</select></label><noscript><button type="submit">OK</button></noscript></form></header>')
        return page(title, header + body, current.SETTINGS.language, refresh=3 if working else 0)

    def home(self, refusal: str = "") -> str:
        body = fields.trip_form(self.token, self.values, self.defaults, refusal, self.busy())
        if self.job is not None:
            body += f'<p><a href="/search">{html.escape(tr("app_last_search"))}</a></p>'
        return self.page(tr("app_plan_title"), body + reports.list_section() + self.tools())

    def tools(self) -> str:
        """The program's own jobs, as the window has them: the ground prices asked again (when some are too old),
        the check of the services, and the languages."""
        def button(path: str, text: str, note: str = "", disabled: bool = False) -> str:
            off = " disabled" if disabled or self.busy() else ""
            small = f"<small>{html.escape(note)}</small>" if note else ""
            return (f'<form method="post" action="{path}" class="tool">{search.hidden(self.token)}'
                    f'<button type="submit" class="quiet"{off}>{html.escape(text)}</button>{small}</form>')
        stale = len(self.stale())
        prices = button("/prices", tr("ui_prices_count", count=stale) if stale else tr("ui_prices"),
                        "" if stale else tr("prices_none", hours=PRICE_VALID_HOURS), disabled=not stale)
        return (f'<h2>{html.escape(tr("app_tools"))}</h2><div class="tools">{prices}'
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
        body = (f'<p><a href="/">{html.escape(tr("app_all_reports"))}</a></p><h1>{html.escape(tr("app_languages"))}</h1>{said}'
                f'<div class="table"><table><tbody>{"".join(rows)}</tbody></table></div>{add}')
        return self.page(tr("app_languages"), body)

    def search_page(self) -> str:
        return self.page(self.title, f"<h1>{html.escape(self.title)}</h1>{self.live()}", working=self.job.working)

    def live(self) -> str:
        return search.live_part(self.job, self.token, reports.link_to, lambda markdown: to_html(markdown)[0])

    def not_found(self) -> Reply:
        text = html.escape(tr("app_not_found"))
        return Reply(404, self.page(tr("app_not_found"), f'<p>{text}</p><p><a href="/">{html.escape(tr("app_all_reports"))}</a></p>'))

    def get(self, path: str) -> Reply:
        self.languages_read()
        if path == "/languages":
            return Reply(200, self.languages_page())
        if path in ("/", "/index.html"):
            return Reply(200, self.home())
        if path == "/page.js":
            return Reply(200, SCRIPT.read_text(encoding="utf-8"), kind="text/javascript; charset=utf-8")
        if path in ("/search", "/search/part"):
            if self.job is None:
                return Reply(303, location="/")
            return Reply(200, self.live() if path == "/search/part" else self.search_page())
        report = reports.find(path)
        if not report:
            return self.not_found()
        return Reply(200, reports.report_page(report, self.token, lambda title, body: self.page(title, body)))

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
                   "/language/remove": self.post_remove_language}
        return answers[path](one, sent) if path in answers else self.not_found()

    def post_search(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if self.busy():   # one search at a time: the one running is shown
            return Reply(303, location="/search")
        values = fields.read(one, self.defaults)
        try:
            settings = settings_for(values, self.defaults)   # checked here too, so that a mistake is answered on the form at once
        except ValueError as error:
            self.values = self.form_values(one)
            return Reply(400, self.home(str(error)))
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

    def post_language(self, one: dict[str, str], sent: dict[str, list[str]]) -> Reply:
        if one.get("language") in LANGUAGES:
            current.SETTINGS = dataclasses.replace(current.SETTINGS, language=one["language"])
            self.values["language"] = one["language"]
        return Reply(303, location="/")

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
        # A page or a trip in a language that is gone carries on in English, as in the window
        if current.SETTINGS.language not in LANGUAGES:
            current.SETTINGS = dataclasses.replace(current.SETTINGS, language=REFERENCE)
        if self.values.get("language") not in LANGUAGES:
            self.values["language"] = REFERENCE
        note = " ".join([tr("ui_language_removed", name=name)] + [tr("ui_language_in_settings", code=code)] * translate.in_settings(code))
        return Reply(200, self.languages_page(note=note))

    def close(self) -> None:
        if self.job is not None and self.job.running:
            self.job.stop()
