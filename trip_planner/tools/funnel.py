"""What the reports kept say, added up: python main.py --funnel

A flight search site stops at the airport. Each report keeps, with its options as data, the numbers of its search
(report/metrics.py). This adds them up over every report kept, recent and saved, and prints:
- the funnel: how many of the flights looked up a real timetable or an operator proved impossible to connect
  (legs.PROVEN), apart from the other reasons; in how many trips the cheapest ticket was one of those; what the
  recommended trip cost, door to door, against the cheapest ticket found;
- how much of the recommended trip's price was confirmed for the travel date (grade A);
- what code did to what the models wrote: colours put right, claims pointed out, times no step gave, links left out;
- the models: calls, failures, calls answered by plan B, which model wrote the recommendation;
- Discover against the planner: of the searches a map opened, how often the planner proved the map's flight
  impossible, and how far the map's prices were, for the dots looked up in the timetables and the estimated ones;
- what python main.py --verify found so far: of the vehicles shown, how many ran, by the light they were given.

Reports made before the numbers were kept have the funnel only in words, in the Markdown report: it is read from
there with the texts the report was written with (any language there is a file for), and said as such. Nothing is
asked of any service and nothing is written."""
import json
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trip_planner.itineraries.legs import PROVEN
from trip_planner.report.full import options_of, report_files
from trip_planner.texts import TEXTS
from trip_planner.tools.prices import PRICE_VALID_HOURS
from trip_planner.tools.verify import answers, OUTCOMES
from trip_planner.trip.trip_settings import LIGHTS

# The texts of a rejection a real timetable or an operator proves, to read it in an older report, with its kind
PROVEN_KEYS = {"reject_not_running": "not_running", "reject_no_real_connection": "timetable",
               "reject_no_connection_after_landing": "timetable"}
# The kinds of the ranking an older report can tell by their text; any other reason is "other"
OTHER_KEYS = {"reject_below_minimum": "below_minimum", "reject_not_worth_it": "not_worth_it", "reject_program_error": "program"}

def pattern(key: str) -> re.Pattern:
    """A text of every language as a regular expression, each {value} matching anything."""
    shapes = [re.sub(r"\\\{\w+\\\}", ".*?", re.escape(texts[key])) for texts in TEXTS.values() if key in texts]
    return re.compile("|".join(f"(?:{shape})" for shape in shapes), re.S)

def kind_of(reason: str) -> str:
    """The kind of a rejection, from its words in an older report. One from a model's data is never proven: no
    timetable showed it."""
    if any(texts.get("unverified_basis", "\0") in reason for texts in TEXTS.values()):
        return "estimate"
    keys = {**PROVEN_KEYS, **OTHER_KEYS}
    return next((kind for key, kind in keys.items() if pattern(key).search(reason)), "other")

def section(text: str, number: int) -> str:
    """The text of a numbered step of a report's reasoning ("### 2. ..."), up to the next heading."""
    found = re.search(rf"^### {number}\. [^\n]*\n(.*?)(?=^#)", text, re.M | re.S)
    return found[1] if found else ""

def from_markdown(report: Path) -> dict[str, Any] | None:
    """The funnel of a report made before it was kept, read from its words. None when it has no flights to read."""
    text = report.read_text(encoding="utf-8")
    flights, rejected = section(text, 2), section(text, 4)
    shapes = [re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>\\d+)", re.escape(texts["flights_found"])) for texts in TEXTS.values() if "flights_found" in texts]
    counts = next((match for match in (re.search(shape, flights) for shape in shapes) if match), None)
    if counts is None:
        return None
    tickets = [float(price) for price in re.findall(r"\|\s*(\d+(?:\.\d+)?) EUR\s*\|", flights)]
    left_out = [{"flight": flight, "ticket_eur": float(price) if price else None, "why": kind_of(reason)}
                for flight, price, reason in re.findall(r"^- (\S+?)(?: \((\d+) EUR\))?: (.*)$", rejected, re.M)]
    return {"found": int(counts["count"]), "checked": int(counts["checked"]), "cheapest_flight": None,
            "cheapest_ticket_eur": min(tickets, default=None), "left_out": left_out, "from_words": True}

def recommended(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """The option the rules recommended, as data, or None."""
    if not data or not data.get("recommended"):
        return None
    return next((option for option in data["options"] if option["number"] == data["recommended"]), None)

def read_all() -> list[dict[str, Any]]:
    """Every report kept: its name, its numbers (report/metrics.py, or only the funnel read from its words) and the
    option the rules recommended."""
    found = []
    for report in report_files("trip_*.md"):
        try:
            data = json.loads(options_of(report).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        numbers = (data or {}).get("metrics") or {}
        funnel = numbers.get("funnel") or from_markdown(report)
        if funnel:
            found.append({"report": report.stem, "funnel": funnel, "checks": numbers.get("checks"), "models": numbers.get("models"),
                          "option": recommended(data), "from_map": numbers.get("from_map"), "written": (data or {}).get("written")})
    return found

def spread(differences: list[float], unit: str, near: tuple[float, ...]) -> str:
    """The median of differences and the share of them within each of near, in words."""
    within = ", ".join(f"{sum(abs(difference) <= limit for difference in differences) / len(differences):.0%} within {limit:g}{unit}"
                       for limit in near)
    return f"median {statistics.median(differences):+.0f}{unit}, {within}"

def map_part(reports: list[dict[str, Any]]) -> list[str]:
    """Discover against the planner: for each search a map opened (report/metrics.py, from_map), whether the planner
    could make the map's very flight, and how far the map's prices were from the planner's (the planner's less the
    map's: positive when the map promised too little). Only searches run within PRICE_VALID_HOURS of the click count:
    later, prices change for reasons that are not the map's. A map there and back gives the flight's price for the
    round trip: only its ways to and from the airports are compared; a one-way map's whole trip is compared too."""
    late = lambda report: (datetime.fromisoformat(report["written"]) - datetime.fromisoformat(report["from_map"]["clicked"])
                           > timedelta(hours=PRICE_VALID_HOURS))
    opened = [report for report in reports if report.get("from_map") and report.get("written")]
    pairs = [report["from_map"] for report in opened if not late(report)]
    if not pairs:
        return ["Discover against the planner: no search opened from a map yet" + (f" within {PRICE_VALID_HOURS} hours of the click." if opened else ".")]
    lines = [f"Discover against the planner: {len(pairs)} searches opened from a map within {PRICE_VALID_HOURS} hours"
             + (f" ({len(opened) - len(pairs)} later ones left out)" if len(opened) > len(pairs) else "") + "."]
    for name, group in (("every dot", pairs), ("dark ring: looked up in the timetables", [pair for pair in pairs if pair["looked_up"]]),
                        ("estimated ways", [pair for pair in pairs if not pair["looked_up"]])):
        if not group:
            continue
        made = [pair for pair in group if "total_eur" in pair["planner"]]
        proven = sum(pair["planner"].get("left_out") in PROVEN for pair in group)
        line = (f"  {name} ({len(group)}): flight not found by the planner {sum(not pair['planner']['found'] for pair in group)}, "
                f"proven impossible {proven} ({proven / len(group):.0%}), itinerary made {len(made)}")
        ground = [pair["planner"]["total_eur"] - pair["planner"]["flight_eur"] - pair["ground_eur"] for pair in made]
        whole = [(pair["planner"]["total_eur"] - pair["total_eur"]) / pair["planner"]["total_eur"] * 100
                 for pair in made if not pair["round_trip"] and pair.get("total_eur") and pair["planner"]["total_eur"]]
        if ground:
            line += f". To and from the airports: {spread(ground, ' EUR', (5, 10))}"
        if whole:
            line += f". Door to door, one-way maps: {spread(whole, '%', (10, 20))}"
        lines.append(line + ".")
    return lines

def cheapest_was_impossible(funnel: dict[str, Any]) -> bool:
    """Whether the cheapest ticket found could not be connected. An older report does not name its cheapest flight:
    a flight left out as proven impossible at the cheapest price is taken for it."""
    proven = [entry for entry in funnel["left_out"] if entry["why"] in PROVEN]
    if funnel.get("cheapest_flight"):
        return any(entry["flight"] == funnel["cheapest_flight"] for entry in proven)
    cheapest = funnel.get("cheapest_ticket_eur")
    return cheapest is not None and any(entry["ticket_eur"] is not None and round(entry["ticket_eur"]) <= round(cheapest)
                                        for entry in proven)

def funnel_part(reports: list[dict[str, Any]]) -> list[str]:
    rows = [f"{'report':<52} {'found':>5} {'looked':>6} {'proven':>6} {'other':>5} {'ticket':>6} {'trip':>6}"]
    for report in reports:
        funnel, option = report["funnel"], report["option"]
        proven = sum(entry["why"] in PROVEN for entry in funnel["left_out"])
        ticket = funnel.get("cheapest_ticket_eur")
        rows.append(f"{report['report'][:51] + ('*' if funnel.get('from_words') else ''):<52} {funnel['found']:>5} {funnel['checked']:>6} "
                    f"{proven:>6} {len(funnel['left_out']) - proven:>5} {f'{ticket:.0f}' if ticket is not None else '-':>6} "
                    f"{f'{option['total_eur']:.0f}' if option else '-':>6}")
    looked = sum(report["funnel"]["checked"] for report in reports)
    kinds = Counter(entry["why"] for report in reports for entry in report["funnel"]["left_out"])
    proven = sum(kinds[kind] for kind in PROVEN)
    paired = [(report["funnel"]["cheapest_ticket_eur"], report["option"]["total_eur"]) for report in reports
              if report["funnel"].get("cheapest_ticket_eur") and report["option"]]
    lines = [*rows, "", "* read from the words of a report made before the numbers were kept", "",
             f"Trips: {len(reports)}. Flights found: {sum(report['funnel']['found'] for report in reports)}, ground transport looked up for {looked}.",
             f"Left out because a real timetable or operator proves it cannot be connected: {proven} of {looked}"
             + (f" ({proven / looked:.0%})." if looked else "."),
             "Left out for other reasons: " + (", ".join(f"{kind} {number}" for kind, number in sorted(kinds.items()) if kind not in PROVEN) or "none") + ".",
             f"Trips whose cheapest ticket could not be connected: {sum(cheapest_was_impossible(report['funnel']) for report in reports)} of {len(reports)}."]
    if paired:
        lines.append(f"Recommended trip door to door against the cheapest ticket ({len(paired)} trips): "
                     f"median {statistics.median(total / ticket for ticket, total in paired):.1f}x, "
                     f"median +{statistics.median(total - ticket for ticket, total in paired):.0f} EUR.")
    return lines

def confirmed_part(reports: list[dict[str, Any]]) -> list[str]:
    """How much of the recommended trip's price was confirmed for the travel date."""
    options = [report["option"] for report in reports if report["option"] and report["option"]["total_eur"]]
    if not options:
        return []
    shares = [option["confirmed_eur"] / option["total_eur"] for option in options]
    return [f"Price of the recommended trip confirmed for the travel date ({len(options)} trips): median {statistics.median(shares):.0%}, "
            f"lowest {min(shares):.0%}, highest {max(shares):.0%}."]

def checks_part(reports: list[dict[str, Any]]) -> list[str]:
    """What code did to what the models wrote, and how the models answered, in the reports that keep it."""
    kept = [report for report in reports if report["checks"] is not None]
    if not kept:
        return ["What code did to the models' text: no report keeps it yet (only those planned from now on)."]
    checks = sum((Counter(report["checks"]) for report in kept), Counter())
    with_models = [report["models"] for report in kept if report["models"]]
    calls = sum(models["calls"] for models in with_models)
    by = Counter(models["recommendation_by"] or "no model (fixed text)" for models in with_models)
    return [f"What code did to the models' text ({len(kept)} trips): {checks['claims_corrected']} colours put right, "
            f"{checks['claims_contradicted']} claims pointed out, {checks['numbers_unknown']} times or durations no step gave, "
            f"{checks['links_dropped']} links left out.",
            f"Models ({len(with_models)} trips): {calls} calls, {sum(models['failed'] for models in with_models)} failed, "
            f"{sum(models['plan_b'] for models in with_models)} answered by plan B. Recommendation written by: "
            + ", ".join(f"{name} {number}" for name, number in by.most_common()) + "."]

def verify_part() -> list[str]:
    """What python main.py --verify has found so far, as kept in the reports' files: nothing is asked again here."""
    by_light: dict[str, Counter] = {}
    for path in report_files("trip_*.json"):
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
            found = answers(kept)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for grade, _, outcome, _ in found:
            by_light.setdefault(grade, Counter())[outcome] += 1
    if not by_light:
        return ["Connections verified on their day: no report keeps them yet."]
    return ["Vehicles of the options shown, checked on their day (python main.py --verify to ask again):"] + [
        f"  {LIGHTS[grade]} {grade}: " + ", ".join(f"{OUTCOMES[outcome]} {counts[outcome]}" for outcome in OUTCOMES if counts[outcome])
        for grade, counts in sorted(by_light.items())]

def summary(reports: list[dict[str, Any]]) -> str:
    return "\n".join([*funnel_part(reports), *confirmed_part(reports), "", *checks_part(reports), "", *map_part(reports), "", *verify_part()])

def main() -> int:
    reports = read_all()
    if not reports:
        print("No report keeps its flights yet: plan a trip first.")
        return 1
    print(summary(reports))
    return 0
