"""The reports of the trips planned, in the browser: the list of the recent and the kept ones, and each one as a
page (report_page.py). A report is found by its key among the files that exist, never by a path from the request."""
import html
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

from app.report_page import page, to_html
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

def list_page(found: dict[str, dict[str, Path]]) -> str:
    body = [f"<h1>{html.escape(tr('app_reports'))}</h1>"]
    for group in ("recent", "saved"):
        body.append(f"<h2>{html.escape(tr('app_' + group))}</h2>")
        items = [f'<li><a href="{REPORT_PATH}{quote(key)}">{html.escape(title_of(path))}</a></li>' for key, path in found[group].items()]
        body.append(f"<ul>{''.join(items)}</ul>" if items else f'<p class="muted">{html.escape(tr("app_no_reports"))}</p>')
    return page(tr("app_reports"), "\n".join(body), current.SETTINGS.language)

def report_page(path: Path) -> str:
    content, sections = to_html(path.read_text(encoding="utf-8"))
    contents = "".join(f'<li><a href="#{anchor}">{html.escape(title)}</a></li>' for anchor, title in sections)
    body = (f'<p><a href="/">{html.escape(tr("app_all_reports"))}</a></p>'
            f'<nav aria-label="{html.escape(tr("app_contents"))}"><ul>{contents}</ul></nav><article>{content}</article>')
    return page(title_of(path), body, current.SETTINGS.language)

def answer(path: str) -> tuple[int, str]:
    """The page at a path: the list at /, a report at /report/<key>, nothing else."""
    found = reports()
    if path in ("/", "/index.html"):
        return 200, list_page(found)
    if path.startswith(REPORT_PATH):
        key = unquote(path[len(REPORT_PATH):])
        report = next((group[key] for group in found.values() if key in group), None)
        if report:
            return 200, report_page(report)
    return 404, page(tr("app_not_found"), f'<p>{html.escape(tr("app_not_found"))}</p><p><a href="/">{html.escape(tr("app_all_reports"))}</a></p>',
                     current.SETTINGS.language)
