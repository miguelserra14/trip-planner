"""The report saved to reports/: everything behind the recommendation. The settings, what each step found and
where it came from, what was discarded and why, how long each step took, and the final result."""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from trip_planner import current
from trip_planner.trip import ground_choice
from trip_planner.trip.trip_settings import LIGHTS, ROOT
from trip_planner.trip.places import airport_groups, too_far_notice
from trip_planner.report import options
from trip_planner.report.formatting import at_destination, grade_text, table
from trip_planner.sources import timetables
from trip_planner.texts import tr
from trip_planner.utils import failed, plain, REQUESTS, TIMINGS, to_time

def step_name(step: str) -> str:
    """A step of the timings by its number ("2a"), in the traveller's language; the number itself for one with no text."""
    try:
        return tr(f"timing_{step}")
    except KeyError:
        return step

def services_table() -> list[str]:
    """What was asked of each service in this run: how many requests, how many failed, the time they took."""
    services: dict[str, list[dict]] = {}
    for entry in REQUESTS:
        services.setdefault(entry["service"], []).append(entry)
    return table([tr("col_service"), tr("col_requests"), tr("col_failed"), tr("col_seconds"), tr("col_slowest")],
                 [[service, len(entries), sum(map(failed, entries)), f"{sum(entry['seconds'] for entry in entries):.0f}",
                   f"{max(entry['seconds'] for entry in entries):.1f}"] for service, entries in sorted(services.items())])

def render_log() -> str:
    """The log of the run, one request per line, in the order they ended."""
    lines = ["Every request of this run: when it was made, the seconds it took, the seconds it waited for its turn, the service, how it ended, what was asked.", ""]
    for entry in REQUESTS:
        waited = f"{entry['waited']:5.1f}" if "waited" in entry else "    -"
        lines.append(f"{entry['at']:8}  {entry['seconds']:6.2f}  {waited}  {entry['service']:<16}  {entry['result']:<18}  {entry['what']}")
    return "\n".join(lines) + "\n"

def log_of(report: Path) -> Path:
    """The file of the log that goes with a report."""
    return report.with_suffix(".log")

def vehicles_of(report: Path) -> Path:
    """The file that goes with a report with the vehicles of the options it shows, for python main.py --verify."""
    return report.with_suffix(".json")

def options_of(report: Path) -> Path:
    """The file that goes with a report with its options as data, for a page (report/options.py). Not named trip_*.json:
    --verify and --prices read every one of those as theirs."""
    return report.with_name(f"options_{report.stem}.json")

def read_options(report: Path) -> dict[str, Any] | None:
    """The options of a report as data, with the ground prices asked again since (tools/prices.py) in place of the ones
    it was written with, so that a page shows one total per option. None for a report without them (made before they
    were kept) or of another version: the report is then shown as Markdown."""
    try:
        data = json.loads(options_of(report).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("version") != options.VERSION:
        return None
    try:
        kept = json.loads(vehicles_of(report).read_text(encoding="utf-8"))
        asked_again = {option["number"]: option for option in kept["options"]}
    except (OSError, ValueError, KeyError):
        return data
    for option in data["options"]:
        again = asked_again.get(option["number"])
        if not again or again.get("total") is None or abs(again["total"] - option["total_eur"]) < 0.01:
            continue
        option["total_eur"] = again["total"]
        for leg in again["legs"]:
            if leg.get("price_eur") is not None:
                option[leg["side"]]["price_eur"] = leg["price_eur"]
        option["prices_read"] = kept.get("prices_read")   # said beside the total: not the price the trip was planned with
    return options.compare(data)

def render_full_report(response: dict[str, Any], recommendation: str) -> str:
    """Everything behind the recommendation: the settings, what each step found and where it came from,
    what was discarded and why, how long each step took, and the final result."""
    yes_no = lambda value: tr("yes") if value else tr("no")
    ground = response.get("ground") or {}
    flights = sorted(response.get("flights") or [], key=lambda flight: flight["price_eur"])
    checked = set(response.get("checked") or [])
    leg_cell = lambda leg: (tr("not_covered") if not leg["covered"]
                            else f"{grade_text(leg['grade'], leg['service_day'])}, {len(leg['options'])} {tr('connections')}")
    lines = [
        f"# {tr('trip_plan')}: {current.SETTINGS.origin} → {current.SETTINGS.destination}",
        "",
        tr("generated", when=datetime.now().strftime("%d/%m/%Y %H:%M")),
        "",
        f"## {tr('settings')}",
        "",
        *table([tr("col_setting"), tr("col_value")],
               [[name, value if value not in ("", []) else "-"] for name, value in vars(current.SETTINGS).items()]),
        "",
        f"## {tr('how_to_read')}",
        "",
        tr("grades_explained"),
        "",
        f"- {LIGHTS['A']} {tr('grade_a_long')}",
        f"- {LIGHTS['B']} {tr('grade_b_long')}",
        f"- {LIGHTS['C']} {tr('grade_c_long')}",
        "",
        tr("min_reliability_explained", minimum=LIGHTS[current.SETTINGS.min_reliability]),
        "",
        f"## {tr('reasoning')}",
        "",
        f"### 1. {tr('step_places')}",
        "",
        f"- {tr('origin')}: \"{current.TRIP.origin.written}\" → {current.TRIP.origin.label}",
        f"- {tr('destination')}: \"{current.TRIP.destination.written}\" → {current.TRIP.destination.label}",
        f"- {tr('airports_dropped')}: {', '.join(current.TRIP.dropped) or tr('none')}",
        *([f"- {too_far_notice(current.TRIP)}"] if current.TRIP.too_far else []),
        "",
        *table([tr("col_group"), tr("col_code"), tr("col_airport"), tr("col_city"), tr("col_country"), tr("col_distance")],
               [[title, a["code"], a["name"], a["city"], a["country"], f"{a['km']} km"]
                for title, airports in airport_groups(current.TRIP) for a in airports]),
        "",
        f"### 2. {tr('step_flights', source=' + '.join(sorted({flight['source'] for flight in flights})) or 'Kiwi')}",
        "",
        tr("flights_found", count=len(flights), checked=len(checked)),
        "",
        *([tr("flight_gaps_title"), ""] + [f"- {gap}" for gap in response.get("flight_gaps") or []] + [""]
          if response.get("flight_gaps") else []),
        *table([tr("col_flight"), tr("col_date"), tr("col_departure"), tr("col_arrival"), tr("col_airline"), tr("col_stops"),
                tr("col_kiwi_price"), tr("col_checked")],
               [[f["id"], f["date"], f"{f['departure_airport']} {f['departure_time']}", f"{f['arrival_airport']} {at_destination(f['arrival_time'], f)}",
                 f["flights"], f["stops"], f"{f['price_eur']:.0f} EUR", yes_no(f["id"] in checked)] for f in flights]),
        "",
        f"### 3. {tr('step_ground')}",
        "",
        tr("ground_explained", sources=timetables.timetable_sources(), requests=ground.get("requests", 0)),
        "",
        tr("ground_choice_report", to_airport=ground_choice.describe(True), from_airport=ground_choice.describe(False)),
        "",
        *table([tr("col_flight"), tr("col_to_airport"), tr("col_from_airport")],
               [[f["id"], leg_cell(ground[f["id"]]["to_airport"]), leg_cell(ground[f["id"]]["from_airport"])]
                for f in flights if f["id"] in ground]),
        "",
        tr("operator_rides_title"),
        "",
        *table([tr("col_airport"), tr("col_date"), tr("col_rides")],
               [[code, day, "; ".join(f"{ride['source']} {to_time(ride['leave'])} → {to_time(ride['arrive'])}, {ride['price_eur']:.2f} EUR"
                                      for ride in rides) or tr("none")]
                for side in ("operators", "operators_from") for code, days in (ground.get(side) or {}).items() for day, rides in days.items()]),
        *([""] + [f"- {tr('not_on_sale_line', source=entry['source'], side=tr('sale_side_to' if entry['to_airport'] else 'sale_side_from', airport=entry['airport']), day=entry['day'], days=entry['days'])}"
                  for entry in ground.get("not_on_sale") or []] if ground.get("not_on_sale") else []),
        "",
        tr("model_transfers_title"),
        "",
        *table([tr("col_airport"), tr("col_reachable"), tr("col_duration"), tr("col_price"), tr("col_how"), tr("col_model_says_source"), tr("col_notes")],
               [[t["airport"], yes_no(t["reachable"]), f"{t['duration_minutes']} min",
                 f"{t['price_eur']:.0f} EUR" if t["price_known"] and t["price_eur"] > 0 else tr("not_found"),
                 t["description"], yes_no(t["found_in_source"]), t["notes"]] for t in response.get("transfers") or []]),
        "",
        tr("model_trains_title"),
        "",
        *table([tr("col_airport"), tr("col_station"), tr("col_link"), tr("col_trains"), tr("col_model_says_source"), tr("col_notes")],
               [[r["airport"], r["station"], f"{r['link_description']} ({r['link_minutes']} min)",
                 ", ".join(f"{d['departure_time']}→{d['arrival_time']}" for d in r["departures"]) or "-",
                 yes_no(r["found_in_source"]), r["notes"]] for r in response.get("trains") or []]),
        "",
        f"### 4. {tr('step_rejected')}",
        "",
        *([f"- {reason}" for reason in response.get("rejected") or []] or [f"- {tr('nothing_rejected')}"]),
        "",
        f"### 5. {tr('step_timings')}",
        "",
        *table([tr("col_step"), tr("col_seconds")], [[step_name(step), f"{seconds:.0f}"] for step, seconds in sorted(TIMINGS.items())]),
        "",
        tr("services_explained"),
        "",
        *services_table(),
        "",
        f"# {tr('final_result')}",
        "",
        response.get("report") or f"## {tr('no_itinerary')}",
        "",
        recommendation,
        "",
    ]
    return "\n".join(lines)

REPORTS = ROOT / "reports"           # every trip saves its reports here, and only the newest recent_reports (a setting) stay
SAVED_REPORTS = REPORTS / "saved"    # the trips the traveller chose to keep. Nothing here is ever deleted
# Each trip is a folder of its own in one of the two above, named after its first report: the report (.md), the log
# of the run (.log), the vehicles of its options (.json) and its options as data (options_*.json). A round trip is
# one trip: both ways are in one folder.

DURATIONS_KEPT = 20   # searches whose length is kept (durations.json), for the page that waits to say how long is left

def durations_file() -> Path:
    return REPORTS / "durations.json"

def remember_duration(seconds: float, kind: str) -> None:
    """How long a search took, kept beside the reports and not with them: the newest DURATIONS_KEPT, whatever happens to
    the reports. Each kind apart (app/waiting.kind_of): "plane" and "ground" for one way of a trip, "discover_light"...
    for a Discover map until it opens. A trip without a plane takes seconds, not minutes."""
    kept = past_durations()
    kept.append({"at": datetime.now().isoformat(timespec="seconds"), "seconds": round(seconds), "kind": kind})
    durations_file().parent.mkdir(parents=True, exist_ok=True)
    durations_file().write_text(json.dumps(kept[-DURATIONS_KEPT:], indent=1), encoding="utf-8")

def past_durations() -> list[dict[str, Any]]:
    """The searches kept by remember_duration, oldest first. A file that cannot be read is no history at all."""
    try:
        kept = json.loads(durations_file().read_text(encoding="utf-8"))
        return [entry for entry in kept if isinstance(entry, dict) and isinstance(entry.get("seconds"), (int, float))]
    except (OSError, ValueError, AttributeError, TypeError):
        return []

def trip_folders(where: Path) -> list[Path]:
    return [folder for folder in where.glob("trip_*") if folder.is_dir()]

def report_files(pattern: str) -> list[Path]:
    """The files of every trip, recent ones first, then the kept ones, that match a pattern ("trip_*.json")."""
    return [path for where in (REPORTS, SAVED_REPORTS) for folder in sorted(trip_folders(where)) for path in sorted(folder.glob(pattern))]

def save_report(response: dict[str, Any], recommendation: str, beside: Path | None = None) -> Path:
    """Save the full reasoning and the final result in a new folder for the trip, and make room: the oldest trips go.
    The log of the run is saved next to it, and the next run starts a new one. beside is a report that goes with
    this one (the way there of a round trip): this one is saved in its folder, which is not made room with."""
    slug = lambda text: re.sub(r"[^a-z0-9]+", "-", plain(text)).strip("-")
    name = f"trip_{slug(current.SETTINGS.origin)}_{slug(current.SETTINGS.destination)}_{datetime.now():%Y-%m-%d_%H%M}"
    path = (beside.parent if beside else REPORTS / name) / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_full_report(response, recommendation), encoding="utf-8")
    log_of(path).write_text(render_log(), encoding="utf-8")
    if response.get("verify"):
        vehicles_of(path).write_text(json.dumps(response["verify"], ensure_ascii=False, indent=1), encoding="utf-8")
    options_of(path).write_text(json.dumps(options.trip_data(response), ensure_ascii=False, indent=1), encoding="utf-8")
    REQUESTS.clear()
    remove_old_reports(keep={path.parent})
    return path

def remove_old_reports(keep: set = frozenset()) -> None:
    """Delete the trips beyond the newest recent_reports of the settings, every file of each. The ones in saved/ are
    not counted and not touched, and neither are the folders in keep."""
    folders = sorted(trip_folders(REPORTS), key=lambda folder: folder.stat().st_mtime)
    for old in folders[:-current.SETTINGS.recent_reports]:
        if old.resolve() not in {folder.resolve() for folder in keep}:
            for vehicles in old.glob("trip_*.json"):
                (verify_later() / old.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(vehicles, verify_later() / old.name / vehicles.name)
            shutil.rmtree(old, ignore_errors=True)

def verify_later() -> Path:
    """Where the vehicles of the trips deleted to make room are kept (.json), for --verify alone: whether a connection
    ran can only be known near its day, long after newer searches pushed its report out. Neither the prices nor the
    app read them."""
    return REPORTS / "verify"

def verify_files() -> list[Path]:
    """The vehicles --verify asks about again: those of every report there is, then those of the trips deleted."""
    return report_files("trip_*.json") + sorted(verify_later().glob("trip_*/trip_*.json"))

def keep_report(path: Path, keep: bool) -> Path:
    """Move the folder of a report to saved/ to keep it, or back among the recent ones, with every file of the trip.
    Returns where the report is now. Asked for the other report of a round trip, whose folder has moved already, it
    only says where it is."""
    target = (SAVED_REPORTS if keep else REPORTS) / path.parent.name
    if target != path.parent and path.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        path.parent.replace(target)
    return target / path.name
