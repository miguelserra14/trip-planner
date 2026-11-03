"""The options of a report as data, saved beside it (full.options_of), for a page that shows them as cards to sort
and compare instead of reading the Markdown. Made from the same values the Markdown report is made from
(summary.py), never from its text.

Nothing here is in words but what was already words when the trip was planned (the warnings, the notes, the
recommendation): the times, prices, codes, grades and the kinds of each source, which the page says in the traveller's
language with the texts the report uses (formatting.py). The recommendation was written by a model and is kept apart
from everything else, to be shown as such."""
from datetime import datetime
from typing import Any

from trip_planner import current
from trip_planner.itineraries.ranking import price_range
from trip_planner.report.formatting import price_source
from trip_planner.report.summary import part_grades
from trip_planner.utils import to_time

VERSION = 1   # a page reads only the version it knows: with another, the report is shown as Markdown
# What is kept of each flight, and of each vehicle of a ground leg, as the searches and the timetables give them
FLIGHT_KEYS = ("id", "date", "route", "airline", "carrier", "flights", "departure_airport", "arrival_airport", "departure_time",
               "arrival_time", "arrival_date", "arrival_day_offset", "duration_minutes", "stops", "source", "price_eur",
               "cabin_bag_included")
STEP_KEYS = ("time", "arrive", "line", "agency", "from", "to", "mode", "rail", "coach")
# What is kept of each trip without a plane (itineraries/ground_only.py): leave and arrive are minutes from the
# midnight of its day, past 24 hours when it arrives on a later day
GROUND_KEYS = ("day", "service_day", "leave", "arrive", "minutes", "grade", "price_eur", "price_kind", "confirmed_eur",
               "urls", "versus_eur", "versus_minutes", "beats")

def vehicles(holder: dict[str, Any]) -> list[dict[str, Any]]:
    return [{key: step.get(key) for key in STEP_KEYS} for step in holder.get("steps") or []]

def leg_data(leg: dict[str, Any]) -> dict[str, Any]:
    """A ground leg: its times and where they come from, how sure they are, its price and where that comes from,
    its vehicles and where to buy them, and the later connections if this one is missed."""
    return {"kind": leg["kind"], "source": leg.get("source") or "", "confirmed_by": leg.get("confirmed_by") or [],
            "grade": leg["grade"], "service_day": leg.get("service_day"),
            "leave_time": to_time(leg["leave"]), "arrive_time": to_time(leg["arrive"]), "minutes": leg["arrive"] - leg["leave"],
            "price_eur": leg["price_eur"], "price_kind": leg["price_kind"], "price_grade": price_source(leg["price_kind"])[1],
            "operators": list(dict.fromkeys(ticket["operator"] for ticket in leg.get("tickets") or [])),
            "vehicles": vehicles(leg), "text": leg.get("text") or "", "urls": leg.get("urls") or [], "later": leg.get("later") or []}

def option_data(number: int, itinerary: dict[str, Any]) -> dict[str, Any]:
    """One option of the report, as its section says it (summary.option_lines): the flight, the two ground legs, the
    total with what it may really cost when part of it is a guess, and the grade of each part."""
    flight, from_leg = itinerary["flight"], itinerary["from"]
    other = from_leg.get("other")
    return {
        "number": number,
        "flight": {key: flight.get(key) for key in FLIGHT_KEYS},
        "flight_price": itinerary["flight_price"],
        "booking": {"url": itinerary["booking"]["url"], "verified": itinerary["booking"]["verified"]},
        "leave": {"date": itinerary["leave_date"], "time": itinerary["leave_time"]},
        "at_airport": itinerary["at_airport_time"], "airport_wait": itinerary["airport_wait"],
        "wait_after_landing": itinerary["wait_after_landing"],
        "arrive": {"date": itinerary["arrival_date"], "time": itinerary["arrival_time"]},
        "to": leg_data(itinerary["to"]), "from": leg_data(from_leg),
        # Another way from the airport, by coach or without one, when it is worth knowing (legs.from_airport_leg)
        "other": {"leave_time": to_time(other["leave"]), "arrive_time": to_time(other["arrive"]), "price_eur": other["price_eur"],
                  "vehicles": vehicles(other), "by_coach": bool(from_leg.get("other_by_coach"))} if other else None,
        "total_eur": itinerary["total_cost"],
        "range_eur": list(price_range(itinerary)) if itinerary.get("guessed_eur", 0) >= 1 else None,
        "confirmed_eur": itinerary["confirmed_eur"],
        "minutes": itinerary["total_minutes"],
        "extra_minutes": itinerary.get("extra_minutes"), "time_cost": itinerary.get("time_cost"),   # not for the closest options
        "schedule_grade": itinerary["schedule_grade"],
        "grades": part_grades(itinerary),
        "notes": list(itinerary.get("notes") or []),
        "breaches": [dict(breach) for breach in itinerary.get("breaches") or []],   # what a closest option breaks
    }

def compare(data: dict[str, Any]) -> dict[str, Any]:
    """Which option is the cheapest and which the fastest, and how far each one is from both: what the page puts on
    its tabs and its cards. Done again when the ground prices were asked again since (full.read_options)."""
    options = data["options"]
    cheapest = min(options, key=lambda option: (option["total_eur"], option["minutes"]), default=None)
    fastest = min(options, key=lambda option: (option["minutes"], option["total_eur"]), default=None)
    data["cheapest"] = cheapest["number"] if cheapest else 0
    data["fastest"] = fastest["number"] if fastest else 0
    for option in options:
        option["versus"] = {"cheapest_eur": round(option["total_eur"] - cheapest["total_eur"], 2),
                            "fastest_minutes": option["minutes"] - fastest["minutes"]}
    return data

def trip_data(response: dict[str, Any]) -> dict[str, Any]:
    """Everything a page needs to show the result of one way of a trip: the trip, the options in the report's order
    with the one the rules recommend, the trips without a plane, what each travel date would cost and the warnings."""
    shown = response.get("shown") or []
    numbers = {id(itinerary): number for number, itinerary in enumerate(shown, start=1)}
    trip, settings = current.TRIP, current.SETTINGS
    place = lambda written, found: {"written": written, "label": found.label if found else written}
    return compare({
        "version": VERSION,
        "written": datetime.now().isoformat(timespec="minutes"),
        "origin": place(settings.origin, trip.origin if trip else None),
        "destination": place(settings.destination, trip.destination if trip else None),
        "dates": settings.iso_dates,
        "language": settings.language,
        "recommended": response.get("recommended") or 0,   # its number among the options, 0 when there is none
        "closest": any(itinerary.get("breaches") for itinerary in shown),   # none respects every requirement: the closest ones
        "options": [option_data(number, itinerary) for number, itinerary in enumerate(shown, start=1)],
        "ground_only": [{**{key: option.get(key) for key in GROUND_KEYS}, "vehicles": vehicles(option)}
                        for option in response.get("ground_only") or []],
        # The cheapest of each date, and its number when it is among the options shown
        "by_date": [{"date": day, "option": numbers.get(id(itinerary)), "route": itinerary["flight"].get("route"),
                     "total_eur": itinerary["total_cost"], "minutes": itinerary["total_minutes"]} if itinerary else
                    {"date": day, "option": None, "route": None, "total_eur": None, "minutes": None}
                    for day, itinerary in response.get("by_date") or []],
        "warnings": list(response.get("warnings") or []),
        "recommendation": response.get("recommendation") or "",   # written by a model: never shown as confirmed
    })
