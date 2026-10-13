"""The report saved to reports/: everything behind the recommendation. The settings, what each step found and
where it came from, what was discarded and why, how long each step took, and the final result."""
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from trip_planner import current
from trip_planner.config import LIGHTS, ROOT
from trip_planner.places import airport_groups, too_far_notice
from trip_planner.report.formatting import at_destination, grade_text, table
from trip_planner.sources import timetables
from trip_planner.texts import tr
from trip_planner.utils import plain, TIMINGS, to_time

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
        tr("ground_explained", sources=timetables.TIMETABLE_SOURCES, requests=ground.get("requests", 0)),
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
        *table([tr("col_step"), tr("col_seconds")], [[step, f"{seconds:.0f}"] for step, seconds in sorted(TIMINGS.items())]),
        "",
        f"# {tr('final_result')}",
        "",
        response.get("report") or f"## {tr('no_itinerary')}",
        "",
        recommendation,
        "",
    ]
    return "\n".join(lines)

REPORTS = ROOT / "reports"           # every run saves its report here, and only the newest RECENT_REPORTS stay
SAVED_REPORTS = REPORTS / "saved"    # the reports the traveller chose to keep. Nothing here is ever deleted
RECENT_REPORTS = 5

def save_report(response: dict[str, Any], recommendation: str) -> Path:
    """Save the full reasoning and the final result, one file per run, and make room: the oldest reports go."""
    slug = lambda text: re.sub(r"[^a-z0-9]+", "-", plain(text)).strip("-")
    path = REPORTS / f"trip_{slug(current.SETTINGS.origin)}_{slug(current.SETTINGS.destination)}_{datetime.now():%Y-%m-%d_%H%M}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(render_full_report(response, recommendation), encoding="utf-8")
    remove_old_reports()
    return path

def remove_old_reports() -> None:
    """Delete the reports beyond the newest RECENT_REPORTS. The ones in saved/ are not counted and not touched."""
    reports = sorted(REPORTS.glob("trip_*.md"), key=lambda report: report.stat().st_mtime)
    for old in reports[:-RECENT_REPORTS]:
        old.unlink(missing_ok=True)

def keep_report(path: Path, keep: bool) -> Path:
    """Move a report to saved/ to keep it, or back among the recent ones. Returns where the report is now."""
    target = (SAVED_REPORTS if keep else REPORTS) / path.name
    if target != path and path.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
    return target
