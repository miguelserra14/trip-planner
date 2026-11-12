"""Discover measured against the planner, in a batch: a one-way map from a city on one day, then the planner on a few
of its trips, each with what the map said of it (maps.estimate_of), as a click on "plan" in the app sends it.

    python -m app.sample Coimbra 03/12/2026        six trips of the map
    python -m app.sample Antwerp 04/12/2026 4      four

Half the trips chosen were looked up in the timetables (the dark ring), half are estimated; each half is spread
over the map's cheapest trips, from the cheapest to the dearest of them. The reports are kept in reports/sample, apart
from the traveller's own: the app neither lists nor deletes them (full.trip_folders reads reports/ and
reports/saved only), and none of them is deleted here. python main.py --funnel reports/sample adds them up.

Kept light on purpose, for the free services it asks (Transitous, the operators) and the models' quota: the searches
run one at a time with a pause between them, at most MAX_TRIPS a batch, and the coordinator's recommendation in
words is left out (the fixed line of the rules instead): nobody reads it, and it is the scarcest quota. The app
imports Discover and the planner both; neither imports the app, so this lives here."""
import asyncio
import dataclasses
import sys
import time
from datetime import datetime
from typing import Any

from trip_planner.report import full

SAMPLE = full.REPORTS / "sample"   # the batches' reports, apart from the traveller's
MAX_TRIPS = 6         # planner searches in one batch: each one asks Transitous about a hundred times
PAUSE_SECONDS = 120   # between two searches of a batch
CHEAPEST = 30         # the trips chosen come from the map's cheapest this many
KEPT = 100            # recent_reports while a batch runs: no report of the sample is deleted by a later one

def spread(trips: list[Any], count: int) -> list[Any]:
    """count of trips, sorted by price, from the cheapest to the dearest, evenly apart."""
    if count <= 0:
        return []
    if len(trips) <= count:
        return list(trips)
    if count == 1:
        return trips[:1]
    return [trips[round(number * (len(trips) - 1) / (count - 1))] for number in range(count)]

def chosen(points: list[dict[str, Any]], planned: dict[str, list[str]], count: int) -> list[dict[str, Any]]:
    """The map's trips to plan: of its cheapest CHEAPEST one-way trips the planner can be opened on, half looked up
    in the timetables and half estimated, spread over their prices. When one half has too few, the other gives more."""
    cheapest = sorted((point for point in points if f"{point['to']}-out" in planned and point.get("total_eur") and point.get("time")),
                      key=lambda point: point["total_eur"])[:CHEAPEST]
    ring = [point for point in cheapest if point["confirmed"]]
    estimated = [point for point in cheapest if not point["confirmed"]]
    picked = spread(ring, min(count // 2, len(ring)))
    return picked + spread(estimated, count - len(picked))

def make_map(city: str, day: str, count: int) -> list[dict[str, Any]]:
    """The map, with its lookups in the timetables, and what it says of each trip chosen (maps.estimate_of)."""
    from app.maps import estimate_of
    from discover.flights.sources import Days
    from discover.session import Session
    from discover.view.page import map_state, trips
    from trip_planner.trip.places import load_airports

    session = Session(city, Days(day, day))
    session.make()
    session.look_up(timetables=True)
    state = map_state(session.found, session.sections)
    planned = trips(session.found, load_airports())
    estimates = [estimate_of(state, f"{point['to']}-out", planned[f"{point['to']}-out"], session.path.name)
                 for point in chosen(state["points"], planned, count)]
    print(f"Map: {session.path}", flush=True)
    for estimate in filter(None, estimates):
        print(f"  {estimate['trip'][1]:<24} {estimate['total_eur']:>7} EUR  {'ring' if estimate['looked_up'] else 'estimated'}", flush=True)
    return [estimate for estimate in estimates if estimate]

async def plan(estimates: list[dict[str, Any]]) -> None:
    """The planner on each trip, one at a time, with the map's estimate kept in its report (metrics.from_map)."""
    from app.maps import same_trip
    from trip_planner import planner
    from trip_planner.sources import flight_search
    from trip_planner.texts import tr
    from trip_planner.trip.trip_settings import settings_file

    async def rules_only(state: dict[str, Any], messages: list[Any]) -> str:
        recommended = state.get("recommended") or 0
        return tr("recommendation_by_rules", heading=tr("recommendation"), option=recommended) if recommended else ""
    planner.recommend = rules_only
    base = dataclasses.replace(settings_file().SETTINGS, recent_reports=KEPT)
    await flight_search.connect()
    for number, estimate in enumerate(estimates):
        if number:
            time.sleep(PAUSE_SECONDS)
        origin, destination, day = estimate["trip"]
        settings = dataclasses.replace(base, origin=origin, destination=destination, dates=[day], return_dates=[],
                                       ground_only=False, confirm_airports=False)
        assert same_trip(estimate, settings)   # the search the app would send with this estimate
        print(f"\n#### {origin} -> {destination}, {day}", flush=True)
        try:
            await planner.plan_trip(settings, from_map=estimate)
        except Exception as error:   # one search failing does not end the batch
            print(f"#### failed: {type(error).__name__}: {error}", flush=True)

def main(arguments: list[str]) -> int:
    if len(arguments) not in (2, 3) or len(arguments) == 3 and not arguments[2].isdigit():
        print(__doc__)
        return 1
    city, written = arguments[0], arguments[1]
    count = int(arguments[2]) if len(arguments) == 3 else MAX_TRIPS
    try:
        day = datetime.strptime(written, "%d/%m/%Y").date().isoformat()
    except ValueError:
        print(f"'{written}' is not a day written dd/mm/yyyy, such as 03/12/2026")
        return 1
    if not 1 <= count <= MAX_TRIPS:
        print(f"Between 1 and {MAX_TRIPS} trips a batch.")
        return 1
    full.REPORTS, full.SAVED_REPORTS = SAMPLE, SAMPLE / "saved"   # before any search: its report goes there
    full.SAVED_REPORTS.mkdir(parents=True, exist_ok=True)
    asyncio.run(plan(make_map(city, day, count)))
    return 0

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
