"""The result shown to the traveller: the comparison, each option step by step, and what to check before booking."""
from datetime import date
from typing import Any

from trip_planner import current
from trip_planner.report.formatting import day_text, grade_text, leg_source, price_source, steps_text, table
from trip_planner.texts import tr
from trip_planner.utils import as_duration, to_time

def render_report(itineraries: list[dict[str, Any]], recommended: dict[str, Any], warnings: list[str]) -> str:
    """The Markdown report shown to the traveller, in the traveller's language."""
    origin, destination = current.SETTINGS.origin, current.SETTINGS.destination
    lines = []
    if warnings:
        lines += [f"## {tr('warnings')}", ""] + [f"- {warning}" for warning in warnings] + [""]
    lines += [
        f"## {tr('comparison')}",
        "",
        *table(
            [tr("col_option"), tr("col_date"), tr("col_route"), tr("col_total"), tr("col_leave"), tr("col_arrive"),
             tr("col_duration"), tr("col_extra_time"), tr("col_time_value"), tr("col_schedules"), tr("col_confirmed")],
            [[(f"**{number}** ({tr('recommended')})" if itinerary is recommended else str(number)),
              date.fromisoformat(itinerary["flight"]["date"]).strftime("%d/%m"), itinerary["flight"]["route"],
              f"{itinerary['total_cost']:.0f} EUR", itinerary["leave_time"], itinerary["arrival_time"],
              as_duration(itinerary["total_minutes"]),
              as_duration(itinerary["extra_minutes"]) if itinerary["extra_minutes"] else tr("fastest"),
              f"{itinerary['time_cost']:.0f} EUR",
              f"{itinerary['schedule_grade']}: " + tr("short_" + itinerary["schedule_grade"].lower()),
              f"{itinerary['confirmed_eur']:.0f} / {itinerary['total_cost']:.0f} EUR"]
             for number, itinerary in enumerate(itineraries, start=1)],
        ),
        "",
        tr("legend_time", table=current.SETTINGS.extra_hours_table(current.SETTINGS.tiebreak_eur_per_hour)),
        "",
        tr("legend_grades"),
    ]
    cheapest = itineraries[0]
    if recommended is not cheapest:
        lines += ["", tr("not_cheapest", extra=f"{recommended['total_cost'] - cheapest['total_cost']:.0f}",
                         saved=as_duration(cheapest["total_minutes"] - recommended["total_minutes"]),
                         worth=f"{cheapest['time_cost'] - recommended['time_cost']:.0f}")]

    for number, itinerary in enumerate(itineraries, start=1):
        flight, booking, to_leg, from_leg = itinerary["flight"], itinerary["booking"], itinerary["to"], itinerary["from"]
        stops = tr("direct") if flight["stops"] == 0 else tr("stopovers", count=flight["stops"])
        to_price, from_price = price_source(to_leg["price_kind"]), price_source(from_leg["price_kind"])
        lines += [
            "",
            f"### {tr('option')} {number}: {flight['route']}, {date.fromisoformat(flight['date']).strftime('%d/%m')}, {itinerary['total_cost']:.0f} EUR",
            "",
            tr("cost_line", flight=f"{itinerary['flight_price']:.0f}", to=f"{to_leg['price_eur']:.0f}", back=f"{from_leg['price_eur']:.0f}",
               total=f"{itinerary['total_cost']:.0f}", duration=as_duration(itinerary["total_minutes"])),
            "",
            f"**{tr('reliability')}**",
            "",
            *table(
                [tr("col_part"), tr("col_value"), tr("col_source"), tr("col_grade")],
                [
                    [tr("part_flight_times"), f"{flight['departure_time']} → {flight['arrival_time']}", tr("source_kiwi"), grade_text("A")],
                    [tr("part_flight_price"), f"{itinerary['flight_price']:.0f} EUR",
                     tr("source_airline", airline=flight["airline"]) if booking["verified"] else tr("source_kiwi_price"),
                     grade_text("A") if booking["verified"] else "B: " + tr("grade_reseller")],
                    [tr("part_to_times"), f"{itinerary['leave_time']} → {itinerary['at_airport_time']}", leg_source(to_leg),
                     grade_text(to_leg["grade"], to_leg.get("service_day"))],
                    [tr("part_to_price"), f"{to_leg['price_eur']:.0f} EUR", to_price[0], grade_text(to_price[1])],
                    [tr("part_from_times"), f"{to_time(from_leg['leave'])} → {itinerary['arrival_time']}", leg_source(from_leg),
                     grade_text(from_leg["grade"], from_leg.get("service_day"))],
                    [tr("part_from_price"), f"{from_leg['price_eur']:.0f} EUR", from_price[0], grade_text(from_price[1])],
                ],
            ),
            "",
            f"- **{itinerary['leave_time']}** " + tr("step_leave", origin=origin)
            + ("" if to_leg["kind"] != "model" else " " + tr("computed_time")) + f" {steps_text(to_leg)}",
            f"- **{itinerary['at_airport_time']}** " + tr("step_at_airport", airport=flight["departure_airport"], wait=itinerary["airport_wait"]),
            f"- **{flight['departure_time']}** " + tr("step_flight", flights=flight["flights"], stops=stops, duration=as_duration(flight["duration_minutes"])),
            f"- **{flight['arrival_time']}** " + tr("step_landing", airport=flight["arrival_airport"]),
            f"- **{to_time(from_leg['leave'])}** " + tr("step_onward", wait=itinerary["wait_after_landing"]) + f" {steps_text(from_leg)}",
            f"- **{itinerary['arrival_time']}** " + tr("step_arrival", destination=destination)
            + (" " + tr("later_connections", times=", ".join(from_leg["later"])) if from_leg.get("later") else ""),
        ]
        if itinerary["notes"]:
            lines += [""] + [f"- {note}" for note in itinerary["notes"]]
        lines += [
            "",
            f"**{tr('where_to_book')}**",
            "",
            f"- {tr('book_flight', airline=flight['airline'])}: {booking['url']} ("
            + (tr("book_confirmed", kiwi=f"{flight['price_eur']:.0f}") if booking["verified"] else tr("book_lookup"))
            + ("" if flight["cabin_bag_included"] else "; " + tr("no_cabin_bag")) + ")",
            f"- {tr('book_to', airport=flight['departure_airport'])}: " + (", ".join(to_leg["urls"]) or tr("no_site")),
            f"- {tr('book_from', airport=flight['arrival_airport'], destination=destination)}: " + (", ".join(from_leg["urls"]) or tr("no_site")),
        ]

    lines += ["", f"## {tr('check_before_booking')}", ""]
    checks = {}
    for number, itinerary in enumerate(itineraries, start=1):
        for side, leg in ((tr("side_to"), itinerary["to"]), (tr("side_from"), itinerary["from"])):
            if leg["grade"] == "B":
                checks[tr("check_typical", number=number, side=side, day=day_text(leg["service_day"]))] = None
            elif leg["grade"] == "C":
                checks[tr("check_unverified", number=number, side=side)] = None
        if not itinerary["booking"]["verified"]:
            checks[tr("check_flight_price", flights=itinerary["flight"]["flights"])] = None
    lines += [f"- {check}" for check in checks]
    lines += [f"- {tr('check_general_prices')}", f"- {tr('check_general_bags')}", f"- {tr('check_general_links')}"]
    return "\n".join(lines)
