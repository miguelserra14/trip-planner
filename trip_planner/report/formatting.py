"""Small pieces shared by the reports: tables, grades and sources in the traveller's language."""
from datetime import date
from typing import Any

from trip_planner.texts import tr

def table(header: list[str], rows: list[list[Any]]) -> list[str]:
    """A Markdown table. Pipes inside a cell would break it, so they are replaced."""
    clean = lambda cell: str(cell).replace("|", "/").replace("\n", " ")
    return [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(":--" for _ in header) + " |",
        *("| " + " | ".join(clean(cell) for cell in row) + " |" for row in rows),
    ]

def day_text(day: str) -> str:
    return date.fromisoformat(day).strftime("%d/%m/%Y")

def grade_text(grade: str, service_day: str | None = None) -> str:
    """A reliability grade in words, in the traveller's language."""
    if grade == "A":
        return "A: " + tr("grade_a")
    if grade == "B":
        return "B: " + tr("grade_b", day=day_text(service_day) if service_day else "?")
    return "C: " + tr("grade_c")

def leg_source(leg: dict[str, Any]) -> str:
    """Where the times of a ground leg come from."""
    if leg["kind"] == "operator":
        return tr("source_operator", operator=leg["source"])
    if leg["kind"] == "timetable":
        return tr("source_timetable")
    return tr("source_model")

def price_source(kind: str) -> tuple:
    """Where a price comes from and its grade."""
    return {
        "operator": (tr("price_operator"), "A"),
        "free": (tr("price_free"), "A"),
        "model": (tr("price_model"), "C"),
        "estimate": (tr("price_estimate"), "C"),
    }[kind]

def steps_text(leg: dict[str, Any]) -> str:
    """The vehicles of a ground leg, in order."""
    if not leg["steps"]:
        return leg.get("text", "")
    return "; ".join(
        f"{step['time']} {step['line']} {step['from']} → {step['to']} ({step['arrive']})"
        + (f", {step['agency']}" if step.get("agency") and step["agency"] != step["line"] else "")
        for step in leg["steps"]
    )
