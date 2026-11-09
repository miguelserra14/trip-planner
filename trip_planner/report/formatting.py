"""Small pieces shared by the reports: tables, grades and sources in the traveller's language."""
import re
from datetime import date
from typing import Any

from trip_planner.trip.trip_settings import GRADES, LIGHTS
from trip_planner.texts import listed, tr   # listed lives in texts/, where a text can be said again in another language

TIMELINE_SQUARES = 24   # how many squares the line of an option takes in the Markdown; the app draws it to scale
# The colour of each part of that line: the grade of its schedule, or None for a wait
SQUARES = {**dict(zip(GRADES, "🟦🟩🟧")), None: "⬜"}

def table(header: list[str], rows: list[list[Any]]) -> list[str]:
    """A Markdown table. Pipes inside a cell would break it, so they are replaced."""
    clean = lambda cell: str(cell).replace("|", "/").replace("\n", " ")
    return [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(":--" for _ in header) + " |",
        *("| " + " | ".join(clean(cell) for cell in row) + " |" for row in rows),
    ]

def at_destination(time: str, flight: dict[str, Any]) -> str:
    """A time at the destination. With its date when the flight lands on a later day than it leaves."""
    if not flight.get("arrival_day_offset"):
        return time
    return f"{time} ({date.fromisoformat(flight['arrival_date']).strftime('%d/%m')})"

def with_day(time: str, day: str, usual: str) -> str:
    """A time, with its date when that is not the day the reader takes it to be."""
    return time if day == usual else f"{time} ({date.fromisoformat(day).strftime('%d/%m')})"

def leaving(itinerary: dict[str, Any]) -> str:
    """When the traveller leaves home. With the date when that is the evening before the flight."""
    return with_day(itinerary["leave_time"], itinerary["leave_date"], itinerary["flight"]["date"])

def arriving(itinerary: dict[str, Any]) -> str:
    """When the traveller arrives. With the date when that is a later day than the flight leaves on: a flight that
    lands the next day, or a connection that reaches the destination after midnight."""
    return with_day(itinerary["arrival_time"], itinerary["arrival_date"], itinerary["flight"]["date"])

def day_text(day: str) -> str:
    return date.fromisoformat(day).strftime("%d/%m/%Y")

def grade_text(grade: str, service_day: str | None = None) -> str:
    """A reliability grade as the traveller sees it: its light, and what it means in the traveller's language."""
    if grade == "A":
        return f"{LIGHTS['A']} " + tr("grade_a")
    if grade == "B":
        return f"{LIGHTS['B']} " + (tr("grade_b", day=day_text(service_day)) if service_day else tr("grade_b_any_day"))
    return f"{LIGHTS['C']} " + tr("grade_c")

def price_grade_text(leg: dict[str, Any]) -> str:
    """The grade of the price of a ground leg as the traveller sees it. A price has no timetable: its middle grade is
    a published tariff, or the operator's price for a nearer week (price_source)."""
    kind = leg["price_kind"]
    grade = price_source(kind)[1]
    if grade == "B":
        return f"{LIGHTS['B']} " + tr("grade_b_tariff" if kind == "tariff" else "grade_b_price")
    return grade_text(grade)

def leg_source(leg: dict[str, Any]) -> str:
    """Where the times of a ground leg come from."""
    if leg["kind"] == "operator":
        return tr("source_operator", operator=leg["source"])
    if leg.get("confirmed_by"):   # read in a timetable, then found on sale for the day at the same times
        return tr("source_operator", operator=", ".join(leg["confirmed_by"]))
    if leg["kind"] == "timetable":
        return tr("source_timetable")
    return tr("source_model")

def price_source(kind: str) -> tuple:
    """Where a price comes from and its grade."""
    return {
        "operator": (tr("price_operator"), "A"),
        "operator_typical": (tr("price_operator_typical"), "B"),
        "tariff": (tr("price_tariff"), "B"),
        "partly": (tr("price_partly"), "C"),
        "free": (tr("price_free"), "A"),
        "model": (tr("price_model"), "C"),
        "estimate": (tr("price_estimate"), "C"),
    }[kind]

OLD_TARIFF_SUFFIX = " (published tariff)"   # in the operator's own name, in English, in the reports saved before 2026-11-09

def ticket_operator(ticket: dict[str, Any]) -> str:
    """Who priced a ticket, for the traveller: a published tariff (tariffs.py) said to be one, in their language."""
    name = ticket["operator"].removesuffix(OLD_TARIFF_SUFFIX)
    return f"{name} ({tr('price_published_tariff')})" if ticket.get("tariff") else name

def ticket_operators(leg: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(ticket_operator(ticket) for ticket in leg.get("tickets") or []))

def price_text(leg: dict[str, Any]) -> str:
    """Where the price of a ground leg comes from, with the operators whose tickets were read."""
    operators = ticket_operators(leg)
    return price_source(leg["price_kind"])[0] + (f": {', '.join(operators)}" if operators else "")

def steps_text(leg: dict[str, Any]) -> str:
    """The vehicles of a ground leg, in order."""
    if not leg["steps"]:
        return leg.get("text", "")
    return "; ".join(
        f"{step['time']} {step['line']} {step['from']} → {step['to']} ({step['arrive']})"
        + (f", {step['agency']}" if step.get("agency") and step["agency"] != step["line"] else "")
        for step in leg["steps"]
    )

def timeline(points: list[tuple[str, str]], parts: list[tuple[str | None, int]]) -> str:
    """An option as one line from start to end: at each point where something changes, its time and place, and
    between two points squares in proportion to the minutes, coloured by the grade of that part. A part gets one
    square however short it is, so that every point stays apart. points has one more item than parts."""
    total = sum(max(0, minutes) for _, minutes in parts) or 1
    counts = [max(1, round(TIMELINE_SQUARES * max(0, minutes) / total)) for _, minutes in parts]
    pieces = []
    for number, (time, place) in enumerate(points):
        pieces.append(f"**{time}** {place}".rstrip())
        if number < len(parts):
            pieces.append(SQUARES[parts[number][0]] * counts[number])
    return " ".join(pieces)

def read_timeline(line: str) -> tuple[list[tuple[str, str]], list[tuple[str | None, int]]] | None:
    """The points and parts of a line written by timeline(), each part with its grade and its squares; None for any
    other line. The window draws it from these."""
    if not line.startswith("**") or not any(square in line for square in SQUARES.values()):
        return None
    pieces = re.split("((?:" + "|".join(SQUARES.values()) + ")+)", line)
    points = [re.fullmatch(r"\s*\*\*(.+?)\*\*\s*(.*?)\s*", piece) for piece in pieces[0::2]]
    grades = {square: grade for grade, square in SQUARES.items()}
    runs = pieces[1::2]
    if not all(points) or any(len(set(run)) > 1 for run in runs):   # two colours with no point between them: not ours
        return None
    parts = [(grades[run[0]], len(run)) for run in runs]
    return [(point[1], point[2]) for point in points], parts
