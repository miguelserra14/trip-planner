"""The reports of the trips planned, in the browser: the list of the recent and the kept ones, and each one as a
page (report_page.py). A report is found by its key among the files that exist, never by a path from the request."""
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote

from app.report_page import to_html
from trip_planner import current
from trip_planner.report.full import REPORTS, SAVED_REPORTS, trip_folders
from trip_planner.texts import tr

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
        body.append(f'<section class="card"><h2>{html.escape(tr("app_" + group))}</h2>')
        items = [f"<li>{link(key, path)}</li>" for key, path in found[group].items()]
        body.append(f'<ul class="reports">{"".join(items)}</ul>' if items else f'<p class="muted">{html.escape(tr("app_no_reports"))}</p>')
        body.append("</section>")
    return "\n".join(body)

def key_of(path: Path) -> str:
    return next(key for group in reports().values() for key, found in group.items() if found == path)

def report_page(path: Path, token: str, whole: Callable[[str, str], str]) -> str:
    """A report, its list of contents, and the button that keeps it (or puts it back among the recent ones)."""
    content, sections = to_html(path.read_text(encoding="utf-8"))
    contents = "".join(f'<li><a href="#{anchor}">{html.escape(title)}</a></li>' for anchor, title in sections)
    key = key_of(path)
    kept = key.startswith("saved/")
    keep = (f'<form method="post" action="/keep" class="keep"><input type="hidden" name="token" value="{html.escape(token)}">'
            f'<input type="hidden" name="report" value="{html.escape(key)}"><input type="hidden" name="keep" value="{"0" if kept else "1"}">'
            f'<button type="submit" class="quiet">{html.escape(tr("app_unkeep" if kept else "ui_keep_report"))}</button>'
            f'<small>{html.escape(tr("app_kept" if kept else "app_not_kept", limit=current.SETTINGS.recent_reports))}</small></form>')
    body = (f'<p><a href="/">{html.escape(tr("app_all_reports"))}</a></p>{keep}'
            f'<nav aria-label="{html.escape(tr("app_contents"))}"><ul>{contents}</ul></nav><article>{content}</article>')
    return whole(title_of(path), body)

def find(path: str) -> Path | None:
    """The report a path of the app asks for, /report/<key>, if there is one by that key."""
    if not path.startswith(REPORT_PATH):
        return None
    key = unquote(path[len(REPORT_PATH):])
    return next((group[key] for group in reports().values() if key in group), None)
