"""The ground prices of the options a report shows, asked again before booking: Refresh prices in the window, or
python main.py --prices. Operators change their prices by the hour (dynamic pricing), so a price read more than
PRICE_VALID_HOURS ago is no longer believed, and every report whose prices are that old can be refreshed.

Only what an operator's booking system answered is asked again, of the same system, for the same departure on the
same day: the rides an operator sold between the city and the airport (fares.RIDE_SOURCES), and the tickets of the
vehicles of a timetable connection (fares.FARE_SOURCES). A published tariff, a price estimated from the distance and
a price a model found keep their price, and say so. Nothing is asked of a model, and the flights are not asked again.
The new prices are kept in the report's .json, which starts PRICE_VALID_HOURS again, and added at the end of the report."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from trip_planner.report.formatting import table
from trip_planner.report.full import report_files
from trip_planner.sources import fares
from trip_planner.texts import tr
from trip_planner.trip.places import Place

PRICE_VALID_HOURS = 24   # a price an operator gave is no longer believed this long after it was read

# ---------------------------------------------------------------- which reports

def report_of(kept_file: Path) -> Path:
    """The report a .json of vehicles and prices goes with."""
    return kept_file.with_suffix(".md")

def read_on(kept_file: Path, kept: dict[str, Any]) -> datetime:
    """When the prices of a report were read. A report made before the prices were kept was read when it was written."""
    if kept.get("prices_read"):
        return datetime.fromisoformat(kept["prices_read"])
    written = report_of(kept_file) if report_of(kept_file).exists() else kept_file
    return datetime.fromtimestamp(written.stat().st_mtime)

def ahead(kept: dict[str, Any]) -> bool:
    """Whether some leg of the report is still to be made: a trip that has passed has nothing left to book."""
    return any(date.fromisoformat(leg["day"]) >= date.today() for option in kept["options"] for leg in option["legs"])

def stale_reports(now: datetime | None = None) -> list[Path]:
    """The .json of every report, recent or kept, whose prices were read more than PRICE_VALID_HOURS ago, for a trip
    that has not passed."""
    now = now or datetime.now()
    found = []
    for path in report_files("trip_*.json"):
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
            if now - read_on(path, kept) > timedelta(hours=PRICE_VALID_HOURS) and ahead(kept):
                found.append(path)
        except (OSError, ValueError, KeyError):
            continue   # a file this version does not read: nothing to refresh in it
    return found

# ---------------------------------------------------------------- asking again
# What happened to the price of a leg: "changed", "same", "new" (no price was kept before), "not_booking" (not a booking
# system's price: not asked again), "no_answer", "gone" (the operator no longer sells or runs that departure), "past"

def refresh_ride(leg: dict[str, Any]) -> tuple[float | None, str]:
    """The price the operator now asks for a ride it sold between the city and the airport, and what happened."""
    try:
        rides = fares.RIDE_SOURCES[leg["source"]](Place(**leg["city"]), leg["airport"], leg["day"], leg["side"] == "to")
    except Exception:   # not on sale yet, or no answer: the price kept stays
        return None, "no_answer"
    if not rides:
        return None, "no_answer"
    same = [ride for ride in rides if ride["leave"] == leg["leave"] and ride["arrive"] == leg["arrive"]]
    if not same:
        return None, "gone"
    return min(ride.get("confirmed_eur", ride["price_eur"]) for ride in same), ""

def booked(leg: dict[str, Any]) -> list[dict[str, Any]]:
    """The tickets of a timetable connection that a booking system priced: a published tariff is not asked again."""
    return [ticket for ticket in leg.get("tickets") or [] if not ticket.get("tariff")]

def refresh_tickets(leg: dict[str, Any]) -> tuple[float | None, str]:
    """How much the booked tickets of a timetable connection now cost all together, each asked of the operator that
    sold it, and what happened. A ticket its operator does not answer for keeps the price read before."""
    sources = {source.name: source for source in fares.FARE_SOURCES}
    new, status = [], ""
    for ticket in booked(leg):
        source, positions = sources.get(ticket["operator"]), ticket["positions"]
        trip = fares.trip_of(leg["vehicles"], positions, leg["vehicles"][positions[0]]["day"])
        price = fares.fare(source, trip) if source else None
        if price is None and source and (source.name, trip) in fares.NOT_RUNNING:
            return None, "gone"
        if price is None:
            status = "no_answer"
        new.append(price)
    for ticket, price in zip(booked(leg), new):
        if price is not None:
            ticket["price_eur"], ticket["travel_day"] = price, True
    return sum(ticket["price_eur"] for ticket in booked(leg)), status

def refresh_leg(leg: dict[str, Any]) -> dict[str, Any]:
    """Ask again the price of one ground leg. Returns what it was, what it is, who answered and what happened, and
    keeps the new price in the leg."""
    before = leg.get("price_eur")
    if date.fromisoformat(leg["day"]) < date.today():
        return {"before": before, "after": before, "status": "past"}
    if "source" in leg:
        how, old_part = leg["source"], leg.get("confirmed_eur")
        part, status = refresh_ride(leg)
    elif booked(leg):
        how = ", ".join(dict.fromkeys(ticket["operator"] for ticket in booked(leg)))
        old_part = sum(ticket["price_eur"] for ticket in booked(leg))
        part, status = refresh_tickets(leg)
    else:
        return {"before": before, "after": before, "status": "not_booking"}
    if part is None:
        return {"before": before, "after": before, "status": status, "how": how}
    if before is None or old_part is None:
        # A report from before the prices were kept: only the operator's part is known, and nothing to compare it with
        leg["confirmed_eur"] = part
        return {"before": None, "after": part, "status": "new", "how": how}
    after = round(before + part - old_part, 2)
    leg["price_eur"], leg["confirmed_eur"] = after, round(leg.get("confirmed_eur", 0.0) + part - old_part, 2)
    return {"before": before, "after": after, "how": how,
            "status": status or ("same" if abs(after - before) < 0.01 else "changed")}

def leg_text(result: dict[str, Any]) -> str:
    before, after, how = result["before"], result["after"], result.get("how", "")
    money = lambda price: "?" if price is None else f"{price:.2f}"
    return {"changed": lambda: tr("prices_changed", before=money(before), after=money(after), how=how),
            "same": lambda: tr("prices_same", price=money(after), how=how),
            "new": lambda: tr("prices_new", price=money(after), how=how),
            "not_booking": lambda: tr("prices_not_booking", price=money(before)),
            "no_answer": lambda: tr("prices_no_answer", price=money(after), how=how),
            "gone": lambda: tr("prices_gone", how=how),
            "past": lambda: tr("prices_past")}[result["status"]]()

def refresh_report(kept_file: Path, now: datetime) -> str:
    """Ask again the ground prices of the options of one report, keep them in its .json and at the end of the report,
    and return what changed, in Markdown."""
    kept = json.loads(kept_file.read_text(encoding="utf-8"))
    read = read_on(kept_file, kept)
    rows, gone = [], []
    for option in kept["options"]:
        results = [refresh_leg(leg) for leg in option["legs"]]
        total = option.get("total")
        if total is not None and all(result["before"] is not None for result in results):
            option["total"] = round(total + sum(result["after"] - result["before"] for result in results), 2)
            total_text = f"{option['total']:.2f} EUR" if abs(option["total"] - total) < 0.01 else f"{total:.2f} → {option['total']:.2f} EUR"
        else:
            total_text = "?"   # a report from before the prices were kept
        when = f"{date.fromisoformat(option['date']):%d/%m}" if option.get("date") else ""
        rows.append([option["number"], f"{option.get('route') or option['flight']} {when}".strip(), *map(leg_text, results), total_text])
        gone += [tr("prices_gone_option", number=option["number"], how=result.get("how", "")) for result in results if result["status"] == "gone"]
    kept["prices_read"] = now.isoformat(timespec="minutes")
    kept_file.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    body = "\n".join([tr("prices_intro", read=f"{read:%d/%m/%Y %H:%M}", hours=PRICE_VALID_HOURS), "",
                      *table([tr("col_option"), tr("col_route"), tr("col_to_airport"), tr("col_from_airport"), tr("col_total")], rows),
                      *([""] + [f"- {line}" for line in gone] if gone else [])])
    if report_of(kept_file).exists():
        with report_of(kept_file).open("a", encoding="utf-8") as report:
            report.write(f"\n\n## {tr('prices_title', when=f'{now:%d/%m/%Y %H:%M}')}\n\n{body}\n")
    return f"### {kept['trip']} ({report_of(kept_file).name})\n\n{body}"

def refresh(paths: list[Path] | None = None) -> str:
    """Ask again the ground prices of the reports whose prices are too old (stale_reports), or of the ones given. Every
    price is asked of its operator again: nothing kept from earlier in this session is used."""
    now = datetime.now()
    paths = stale_reports(now) if paths is None else paths
    if not paths:
        return tr("prices_none", hours=PRICE_VALID_HOURS)
    fares.forget_prices()
    return f"## {tr('prices_title', when=f'{now:%d/%m/%Y %H:%M}')}\n\n" + "\n\n".join(refresh_report(path, now) for path in paths)

def main() -> int:
    print(refresh())
    return 0
