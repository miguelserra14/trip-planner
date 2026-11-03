"""The reports of the trips planned, in the browser: the list of the recent and the kept ones, and each one as a
page (report_page.py). A report is found by its key among the files that exist, never by a path from the request."""
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote

from app import results
from app.report_page import to_html
from trip_planner import current
from trip_planner.report.full import read_options, REPORTS, SAVED_REPORTS, trip_folders
from trip_planner.texts import tr
from trip_planner.utils import as_duration

REPORT_PATH = "/report/"   # a report's page is this and its key

def reports() -> dict[str, dict[str, Path]]:
    """The reports of every trip, newest first, by group ("recent", "saved") and key. A round trip's folder has two."""
    found = {}
    for group, where in (("recent", REPORTS), ("saved", SAVED_REPORTS)):
        folders = sorted(trip_folders(where), key=lambda folder: folder.stat().st_mtime, reverse=True)
        found[group] = {f"{group}/{path.stem}": path for folder in folders for path in sorted(folder.glob("trip_*.md"))}
    return found

def title_of(path: Path) -> str:
    """The first heading of a report, which names the trip, and when it was planned, from its file's name."""
    with path.open(encoding="utf-8") as file:
        heading = file.readline().removeprefix("# ").strip() or path.stem
    when = re.search(r"(\d{4}-\d{2}-\d{2}_\d{4})$", path.stem)
    return f"{heading} ({datetime.strptime(when[1], '%Y-%m-%d_%H%M'):%d/%m/%Y %H:%M})" if when else heading


def link(key: str, path: Path) -> str:
    return f'<a href="{REPORT_PATH}{quote(key)}">{html.escape(title_of(path))}</a>'

def trip_card(key: str, path: Path) -> str:
    """A report as a card of the list: the trip, its days and the option the rules recommend, with its price and how
    sure its times are. A report without its options as data (made before they were kept) shows its title only."""
    data = read_options(path)
    address = f"{REPORT_PATH}{quote(key)}"
    if not data:
        return f'<li><a class="trip" href="{address}"><span class="route">{html.escape(title_of(path))}</span></a></li>'
    days = ", ".join(f"{datetime.fromisoformat(day):%d/%m}" for day in data["dates"])
    first = next((option for option in data["options"] if option["number"] == data["recommended"]), None)
    price = (f'<span class="price"><b>{first["total_eur"]:.0f} EUR</b><small>{results.light(first["schedule_grade"])} '
             f'{html.escape(as_duration(first["minutes"]))} · {html.escape(first["flight"]["departure_airport"])} → '
             f'{html.escape(first["flight"]["arrival_airport"])}</small></span>' if first
             else f'<span class="price"><small>{html.escape(tr("app_no_options"))}</small></span>')
    return (f'<li><a class="trip" href="{address}"><span class="route">{html.escape(results.town(data["origin"]["written"]))} → '
            f'{html.escape(results.town(data["destination"]["written"]))}</span><small>{html.escape(days)} · '
            f'{html.escape(tr("app_planned", when=results.planned(data["written"])))}</small>{price}</a></li>')

def link_to(path: Path) -> str:
    """The link to a report from its file, or its name if it is not among the reports (already deleted)."""
    for group in reports().values():
        for key, found in group.items():
            if found.resolve() == path.resolve():
                return link(key, found)
    return html.escape(path.name)

def list_section() -> str:
    """The reports, the recent ones and the kept ones, newest first."""
    found = reports()
    body = [f"<h1>{html.escape(tr('app_reports'))}</h1>"]
    for group in ("recent", "saved"):
        body.append(f'<h2>{html.escape(tr("app_" + group))}</h2>')
        items = [trip_card(key, path) for key, path in found[group].items()]
        body.append(f'<ul class="trips">{"".join(items)}</ul>' if items else f'<p class="muted">{html.escape(tr("app_no_reports"))}</p>')
    return "\n".join(body)

def key_of(path: Path) -> str:
    return next(key for group in reports().values() for key, found in group.items() if found == path)

def report_page(path: Path, token: str, whole: Callable[[str, str], str], view: str = "", sort: str = "") -> str:
    """A report: its options as cards (results.py) when it has them as data, otherwise, or with view "full", the whole
    Markdown with its list of contents. Both with the button that keeps it (or puts it back among the recent ones)."""
    key = key_of(path)
    kept = key.startswith("saved/")
    address = f"{REPORT_PATH}{quote(key)}"
    keep = (f'<form method="post" action="/keep" class="keep"><input type="hidden" name="token" value="{html.escape(token)}">'
            f'<input type="hidden" name="report" value="{html.escape(key)}"><input type="hidden" name="keep" value="{"0" if kept else "1"}">'
            f'<button type="submit" class="quiet">{html.escape(tr("app_unkeep" if kept else "ui_keep_report"))}</button>'
            f'<small>{html.escape(tr("app_kept" if kept else "app_not_kept", limit=current.SETTINGS.recent_reports))}</small></form>')
    data = read_options(path)
    if data and view != "full":
        return whole(title_of(path), results.results_page(data, sort, keep, address))
    content, sections = to_html(path.read_text(encoding="utf-8"))
    contents = "".join(f'<li><a href="#{anchor}">{html.escape(title)}</a></li>' for anchor, title in sections)
    back = (f'<a href="{address}">{html.escape(tr("app_back_to_results"))}</a>' if data
            else f'<a href="/">{html.escape(tr("app_all_reports"))}</a>')
    body = (f'<p class="back">{back}</p><div class="notice">{keep}</div>'
            f'<nav aria-label="{html.escape(tr("app_contents"))}"><ul>{contents}</ul></nav><article>{content}</article>')
    return whole(title_of(path), body)

def trip_of(path: Path) -> dict[str, str] | None:
    """What the planner's form needs to search a report's trip again, from its options as data: the places as they
    were written and the days, one way. None for a report without them."""
    data = read_options(path)
    if not data:
        return None
    return {"origin": data["origin"]["written"], "destination": data["destination"]["written"], "return_dates": "",
            "dates": ", ".join(f"{datetime.fromisoformat(day):%d/%m/%Y}" for day in data["dates"])}

def find(path: str) -> Path | None:
    """The report a path of the app asks for, /report/<key>, if there is one by that key."""
    if not path.startswith(REPORT_PATH):
        return None
    key = unquote(path[len(REPORT_PATH):])
    return next((group[key] for group in reports().values() if key in group), None)
