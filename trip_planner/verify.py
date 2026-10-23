"""Whether the ground connections of the options shown really ran on the day of the trip: python main.py --verify

A light says how far a connection can be trusted when the trip is planned. Whether it deserved it can only be known
later, when the day of the trip is on sale or published. So each report keeps, next to it (.json), the vehicles of
the options it showed, each with the day it leaves on and the light its connection had. --verify goes through every
report kept and asks again, for each vehicle whose day can now be known:
- the operator that sells it, where one can be read by code (fares.py): selling it that day means it runs, running
  other rides that day but not this one means it does not (NotRunning);
- otherwise the timetable published for that very day (Transitous), once the day is within the week every operator
  has published: the same departure between the same two stops, or not.
An answer that settles the question is kept in the file and not asked again. The table printed at the end counts,
by light and by source, the vehicles that ran, the ones that did not, and the ones that cannot be known yet: how
far each light deserves to be trusted."""
import asyncio
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trip_planner import current
from trip_planner.config import LIGHTS
from trip_planner.report.full import REPORTS, SAVED_REPORTS
from trip_planner.sources import fares, timetables
from trip_planner.sources.timetables import landing_day, local_to_utc, utc_to_local
from trip_planner.utils import to_minutes

KEPT = ("time", "arrive", "line", "agency", "from", "to", "from_id", "to_id", "from_at", "to_at", "rail", "coach", "mode", "km")
SETTLED = ("ran", "did_not_run", "not_in_timetable")   # answers that are not asked again
OUTCOMES = {"ran": "ran", "did_not_run": "did not run", "not_in_timetable": "not in that day's timetable", None: "not known yet"}
SAME_MINUTES = 2   # a departure of the day's timetable is the one shown when it leaves within this of it

# ---------------------------------------------------------------- what a report keeps

def vehicles(leg: dict[str, Any], day: str) -> list[dict[str, Any]]:
    """The vehicles of a ground leg, each with the day it leaves on. day is the day of the leg: a leg to the airport
    may start the evening before, and a vehicle after midnight leaves on the next day."""
    on = date.fromisoformat(day) + timedelta(days=leg.get("leave", 0) // (24 * 60))
    previous, found = leg.get("leave", 0) % (24 * 60), []
    for step in leg.get("steps") or []:
        minutes = to_minutes(step["time"])
        if minutes < previous:
            on += timedelta(days=1)
        previous = minutes
        found.append({key: step.get(key) for key in KEPT} | {"day": on.isoformat()})
    return found

def record(itineraries: list[dict[str, Any]]) -> dict[str, Any]:
    """What --verify needs of the options a report shows: their ground vehicles, with the light of their connection.
    A connection an operator sold between the city and the airport (fares.RIDE_SOURCES) is asked of it again as a whole."""
    options = []
    for number, itinerary in enumerate(itineraries, start=1):
        flight, legs = itinerary["flight"], []
        for side, leg, day in (("to", itinerary["to"], flight["date"]), ("from", itinerary["from"], landing_day(flight))):
            kept = {"side": side, "grade": leg["grade"], "kind": leg["kind"], "day": day, "vehicles": vehicles(leg, day)}
            if leg["kind"] == "operator" and leg.get("source") in fares.RIDE_SOURCES:
                airport = flight["departure_airport"] if side == "to" else flight["arrival_airport"]
                city = current.TRIP.origin if side == "to" else current.TRIP.destination
                kept |= {"source": leg["source"], "leave": leg["leave"], "arrive": leg["arrive"], "city": asdict(city),
                         "airport": current.TRIP.airport(airport)}
            legs.append(kept)
        options.append({"number": number, "flight": flight["id"], "legs": legs})
    return {"trip": f"{current.SETTINGS.origin} -> {current.SETTINGS.destination}", "options": options}

# ---------------------------------------------------------------- asking again

def knowable(day: str) -> bool:
    """Whether a day can still be asked about: it has not passed."""
    return date.fromisoformat(day) >= date.today()

async def in_timetable(vehicle: dict[str, Any], transit: timetables.Timetables) -> str | None:
    """Whether the timetable published for the vehicle's very day has it: "ran", "not_in_timetable", or None when
    that day is not published yet or the stops are not known."""
    day = vehicle["day"]
    if timetables.reference_day(day) != day or not (vehicle.get("from_id") and vehicle.get("to_id") and vehicle.get("from_at")):
        return None
    zone = await transit.zone(*vehicle["from_at"])
    if not zone:
        return None
    leave = to_minutes(vehicle["time"])
    found = await transit.get("plan", {
        "fromPlace": vehicle["from_id"], "toPlace": vehicle["to_id"], "time": local_to_utc(day, max(leave - SAME_MINUTES, 0), zone),
        "arriveBy": "false", "numItineraries": 5, "transitModes": vehicle.get("mode") or timetables.GROUND_MODES})   # maxTransfers=0 finds nothing there
    rides = [[leg for leg in itinerary["legs"] if leg["mode"] != "WALK"] for itinerary in found.get("itineraries") or []]
    starts = [utc_to_local(legs[0]["startTime"], zone) for legs in rides if len(legs) == 1]
    if any(start_day == day and abs(minutes - leave) <= SAME_MINUTES for start_day, minutes in starts):
        return "ran"
    return "not_in_timetable" if starts else None

async def ask_operator(source: fares.FareSource, trip: fares.Trip) -> str | None:
    """What an operator answers for a ticket on its day: "ran", "did_not_run", or None when it does not say."""
    price = await asyncio.to_thread(fares.fare, source, trip)
    if price is not None:
        return "ran"
    return "did_not_run" if (source.name, trip) in fares.NOT_RUNNING else None

async def check_vehicles(found: list[dict[str, Any]], transit: timetables.Timetables) -> None:
    """Ask again about the vehicles of one leg whose day can be known, and note each answer in the vehicle."""
    asked: set = set()
    for source, positions in fares.runs(found):
        day = found[positions[0]]["day"]
        if not source.confirms or source.tariff or not knowable(day) or all(found[p].get("outcome") in SETTLED for p in positions):
            continue
        outcome = await ask_operator(source, fares.trip_of(found, positions, day))
        if outcome:
            for position in positions:
                found[position] |= {"outcome": outcome, "how": source.name, "checked": date.today().isoformat()}
                asked.add(position)
    for position, vehicle in enumerate(found):
        if position in asked or vehicle.get("outcome") in SETTLED or not knowable(vehicle["day"]):
            continue
        outcome = await in_timetable(vehicle, transit)
        if outcome:
            vehicle |= {"outcome": outcome, "how": "Transitous", "checked": date.today().isoformat()}

async def check_ride(leg: dict[str, Any]) -> None:
    """Ask the operator that sold a ride between the city and the airport whether it still runs it that day."""
    if leg.get("outcome") in SETTLED or not knowable(leg["day"]):
        return
    from trip_planner.places import Place
    rides = await asyncio.to_thread(fares.RIDE_SOURCES[leg["source"]], Place(**leg["city"]), leg["airport"], leg["day"], leg["side"] == "to")
    if rides:
        same = any(ride["leave"] == leg["leave"] and ride["arrive"] == leg["arrive"] for ride in rides)
        leg |= {"outcome": "ran" if same else "did_not_run", "how": leg["source"], "checked": date.today().isoformat()}

async def check_report(kept: dict[str, Any], transit: timetables.Timetables) -> None:
    for option in kept["options"]:
        for leg in option["legs"]:
            try:
                if "source" in leg:
                    await check_ride(leg)
                else:
                    await check_vehicles(leg["vehicles"], transit)
            except Exception as error:   # one service failing leaves the rest to check
                print(f"  {option['flight']}, {leg['side']}: {type(error).__name__}: {error}")

# ---------------------------------------------------------------- the count

def answers(kept: dict[str, Any]) -> list[tuple]:
    """(light, source, outcome, what) for each thing checked in a report: a vehicle, or a ride sold as a whole."""
    found = []
    for option in kept["options"]:
        for leg in option["legs"]:
            if "source" in leg:
                found.append((leg["grade"], leg["source"], leg.get("outcome"), f"option {option['number']}, {leg['source']} {leg['day']}"))
                continue
            for vehicle in leg["vehicles"]:
                source = vehicle.get("how") or getattr(fares.source_of(vehicle), "name", None) or "Transitous"
                found.append((leg["grade"], source, vehicle.get("outcome"),
                              f"option {option['number']}, {vehicle['day']} {vehicle['time']} {vehicle['line']} {vehicle['from']} → {vehicle['to']}"))
    return found

def count_table(title: str, rows: dict[str, dict]) -> list[str]:
    columns = list(OUTCOMES)
    lines = ["", f"{title:<34}" + "".join(f"{OUTCOMES[outcome]:>30}" for outcome in columns)]
    for name, counts in sorted(rows.items()):
        lines.append(f"{name:<34}" + "".join(f"{counts.get(outcome, 0):>30}" for outcome in columns))
    return lines

def summary(reports: dict[Path, dict[str, Any]]) -> str:
    by_light: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    wrong = []
    for path, kept in reports.items():
        for grade, source, outcome, what in answers(kept):
            for table, name in ((by_light, f"{LIGHTS[grade]} {grade}"), (by_source, source)):
                table.setdefault(name, {}).setdefault(outcome, 0)
                table[name][outcome] += 1
            if outcome in ("did_not_run", "not_in_timetable"):
                wrong.append(f"- {path.name}, {what}: {OUTCOMES[outcome]}")
    lines = [f"Ground vehicles of the options shown in {len(reports)} report(s), checked against their own day."]
    lines += count_table("By the light they were given", by_light) + count_table("By who answered", by_source)
    if wrong:
        lines += ["", "Did not run as shown:"] + wrong
    return "\n".join(lines)

async def verify_reports() -> int:
    files = sorted(REPORTS.glob("trip_*.json")) + sorted(SAVED_REPORTS.glob("trip_*.json"))
    if not files:
        print("No report keeps its connections yet: only the reports made from now on can be verified.")
        return 0
    reports = {}
    transit = timetables.Timetables()
    try:
        for path in files:
            kept = json.loads(path.read_text(encoding="utf-8"))
            print(f"{path.name}: {kept['trip']}")
            await check_report(kept, transit)
            path.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
            reports[path] = kept
    finally:
        await transit.close()
    print("\n" + summary(reports))
    return 0

def main() -> int:
    return asyncio.run(verify_reports())
