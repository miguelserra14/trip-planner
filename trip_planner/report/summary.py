"""The result shown to the traveller: the comparison, each option step by step, and what to check before booking."""
from datetime import date
from typing import Any

from trip_planner import current
from trip_planner.config import LIGHTS
from trip_planner.itineraries.ranking import price_range, REQUIREMENTS, tied_on_price
from trip_planner.report.formatting import arriving, at_destination, day_text, leaving, grade_text, leg_source, price_source, price_text, steps_text, table
from trip_planner.texts import tr
from trip_planner.utils import as_duration, to_time

def total_text(itinerary: dict[str, Any]) -> str:
    """The total cost of an option, with what it may really cost when part of it is a guess: '78 EUR (59-116)'."""
    if itinerary.get("guessed_eur", 0) < 1:
        return f"{itinerary['total_cost']:.0f} EUR"
    lowest, highest = price_range(itinerary)
    return f"{itinerary['total_cost']:.0f} EUR ({lowest:.0f}-{highest:.0f})"

def option_lines(number: int, itinerary: dict[str, Any]) -> list[str]:
    """One option, step by step: its cost, how reliable each part is, the times and where to book."""
    origin, destination = current.SETTINGS.origin, current.SETTINGS.destination
    lines: list[str] = []
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
                [tr("part_flight_times"), f"{flight['departure_time']} → {at_destination(flight['arrival_time'], flight)}", tr("source_kiwi", source=flight["source"]), grade_text("A")],
                [tr("part_flight_price"), f"{itinerary['flight_price']:.0f} EUR",
                 tr("source_airline", airline=flight["airline"]) if booking["verified"] else tr("source_kiwi_price", source=flight["source"]),
                 grade_text("A") if booking["verified"] else f"{LIGHTS['B']} " + tr("grade_reseller")],
                [tr("part_to_times"), f"{leaving(itinerary)} → {itinerary['at_airport_time']}", leg_source(to_leg),
                 grade_text(to_leg["grade"], to_leg.get("service_day"))],
                [tr("part_to_price"), f"{to_leg['price_eur']:.0f} EUR", price_text(to_leg), grade_text(to_price[1])],
                [tr("part_from_times"), f"{to_time(from_leg['leave'])} → {arriving(itinerary)}", leg_source(from_leg),
                 grade_text(from_leg["grade"], from_leg.get("service_day"))],
                [tr("part_from_price"), f"{from_leg['price_eur']:.0f} EUR", price_text(from_leg), grade_text(from_price[1])],
            ],
        ),
        "",
        f"- **{leaving(itinerary)}** " + tr("step_leave", origin=origin)
        + ("" if to_leg["kind"] != "model" else " " + tr("computed_time")) + f" {steps_text(to_leg)}",
        f"- **{itinerary['at_airport_time']}** " + tr("step_at_airport", airport=flight["departure_airport"], wait=itinerary["airport_wait"]),
        f"- **{flight['departure_time']}** " + tr("step_flight", flights=flight["flights"], stops=stops, duration=as_duration(flight["duration_minutes"])),
        f"- **{at_destination(flight['arrival_time'], flight)}** " + tr("step_landing", airport=flight["arrival_airport"]),
        f"- **{to_time(from_leg['leave'])}** " + tr("step_onward", wait=itinerary["wait_after_landing"]) + f" {steps_text(from_leg)}",
        f"- **{arriving(itinerary)}** " + tr("step_arrival", destination=destination)
        + (" " + tr("later_connections", times=", ".join(from_leg["later"])) if from_leg.get("later") else ""),
    ]
    other = from_leg.get("other")
    if other:
        lines.append("- " + tr("other_by_coach" if from_leg["other_by_coach"] else "other_without_coach",
                               leave=to_time(other["leave"]), arrive=to_time(other["arrive"]),
                               price=f"{other['price_eur']:.0f}") + f" {steps_text(other)}")
    if itinerary["notes"]:
        lines += [""] + [f"- {note}" for note in itinerary["notes"]]
    lines += [
        "",
        f"**{tr('where_to_book')}**",
        "",
        f"- {tr('book_flight', airline=flight['airline'])}: {booking['url']} ("
        + (tr("book_confirmed", source=flight["source"], kiwi=f"{flight['price_eur']:.0f}") if booking["verified"] else tr("book_lookup"))
        + ("" if flight["cabin_bag_included"] else "; " + tr("no_cabin_bag")) + ")",
        f"- {tr('book_to', airport=flight['departure_airport'])}: " + (", ".join(to_leg["urls"]) or tr("no_site")),
        f"- {tr('book_from', airport=flight['arrival_airport'], destination=destination)}: " + (", ".join(from_leg["urls"]) or tr("no_site")),
    ]
    return lines

def booking_checks(itineraries: list[dict[str, Any]]) -> list[str]:
    """What the traveller must confirm before paying, for the options shown."""
    lines: list[str] = []
    lines += ["", f"## {tr('check_before_booking')}", ""]
    checks = {}
    for number, itinerary in enumerate(itineraries, start=1):
        for side, leg in ((tr("side_to"), itinerary["to"]), (tr("side_from"), itinerary["from"])):
            if leg["grade"] == "B":
                checks[tr("check_typical", number=number, side=side, day=day_text(leg["service_day"]))] = None
            elif leg["grade"] == "C":
                checks[tr("check_unverified", number=number, side=side)] = None
        if not itinerary["booking"]["verified"]:
            checks[tr("check_flight_price", flights=itinerary["flight"]["flights"], source=itinerary["flight"]["source"])] = None
    lines += [f"- {check}" for check in checks]
    lines += [f"- {tr('check_general_prices')}", f"- {tr('check_general_bags')}", f"- {tr('check_general_links')}"]
    return lines

def render_report(itineraries: list[dict[str, Any]], recommended: dict[str, Any], warnings: list[str]) -> str:
    """The Markdown report shown to the traveller, in the traveller's language."""
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
              total_text(itinerary), leaving(itinerary), arriving(itinerary),
              as_duration(itinerary["total_minutes"]),
              as_duration(itinerary["extra_minutes"]) if itinerary["extra_minutes"] else tr("fastest"),
              f"{itinerary['time_cost']:.0f} EUR",
              LIGHTS[itinerary["schedule_grade"]],
              f"{itinerary['confirmed_eur']:.0f} / {itinerary['total_cost']:.0f} EUR"]
             for number, itinerary in enumerate(itineraries, start=1)],
        ),
        "",
        tr("legend_time", table=current.SETTINGS.extra_hours_table(current.SETTINGS.tiebreak_eur_per_hour)),
        "",
        tr("legend_grades"),
    ]
    tied = tied_on_price(itineraries)
    if tied:
        lines += ["", tr("price_tie", options=", ".join(map(str, tied)), factor=f"{current.SETTINGS.unverified_price_factor:g}")]
    cheapest = itineraries[0]
    if recommended is not cheapest:
        lines += ["", tr("not_cheapest", extra=f"{recommended['total_cost'] - cheapest['total_cost']:.0f}",
                         saved=as_duration(cheapest["total_minutes"] - recommended["total_minutes"]),
                         worth=f"{cheapest['time_cost'] - recommended['time_cost']:.0f}")]

    for number, itinerary in enumerate(itineraries, start=1):
        lines += option_lines(number, itinerary)
    return "\n".join(lines + booking_checks(itineraries))

def span(minutes: int) -> str:
    """A length of time in words: '45 min' or '2h10'."""
    return f"{minutes} min" if minutes < 60 else as_duration(minutes)

def render_closest(itineraries: list[dict[str, Any]], warnings: list[str]) -> str:
    """Shown when no itinerary respects every requirement: the closest ones, closest first, each with what it
    breaks, what speaks for and against it, and the settings that would make it valid."""
    lines = []
    if warnings:
        lines += [f"## {tr('warnings')}", ""] + [f"- {warning}" for warning in warnings] + [""]
    lines += [
        f"## {tr('near_title')}",
        "",
        tr("near_intro"),
        "",
        *(f"{number}. {tr('near_order_' + kind, floor=current.SETTINGS.airport_floor_minutes)}" for number, kind in enumerate(REQUIREMENTS, start=1)),
        "",
        tr("near_rule"),
        "",
        *table(
            [tr("col_option"), tr("col_date"), tr("col_route"), tr("col_total"), tr("col_leave"), tr("col_arrive"),
             tr("col_duration"), tr("col_breaks"), tr("col_schedules"), tr("col_confirmed")],
            [[f"**{number}** ({tr('closest')})" if number == 1 else str(number),
              date.fromisoformat(itinerary["flight"]["date"]).strftime("%d/%m"), itinerary["flight"]["route"],
              total_text(itinerary), leaving(itinerary), arriving(itinerary),
              as_duration(itinerary["total_minutes"]),
              "; ".join(tr("breach_" + breach["kind"], value=breach["value"], limit=breach["limit"]) for breach in itinerary["breaches"]),
              LIGHTS[itinerary["schedule_grade"]],
              f"{itinerary['confirmed_eur']:.0f} / {itinerary['total_cost']:.0f} EUR"]
             for number, itinerary in enumerate(itineraries, start=1)],
        ),
    ]
    cheapest = min(itinerary["total_cost"] for itinerary in itineraries)
    fastest = min(itinerary["total_minutes"] for itinerary in itineraries)
    for number, itinerary in enumerate(itineraries, start=1):
        broken = {breach["kind"] for breach in itinerary["breaches"]}
        pros = [tr("pro_closest")] if number == 1 else []
        kept = [tr("req_" + kind) for kind in REQUIREMENTS if kind not in broken]
        if kept:
            pros.append(tr("pro_respects", kept=", ".join(kept)))
        if itinerary["total_cost"] == cheapest:
            pros.append(tr("pro_cheapest", cost=f"{cheapest:.0f}"))
        if itinerary["total_minutes"] == fastest:
            pros.append(tr("pro_fastest", duration=as_duration(fastest)))
        if itinerary["confirmed_eur"] > 0:
            pros.append(tr("pro_confirmed", confirmed=f"{itinerary['confirmed_eur']:.0f}", total=f"{itinerary['total_cost']:.0f}"))
        cons = [tr("con_" + breach["kind"] + ("_" + breach["side"] if "side" in breach else ""), value=breach["value"],
                   limit=breach["limit"], amount=span(breach["amount"]) if breach["kind"] != "stopovers" else breach["amount"])
                for breach in itinerary["breaches"]]
        if itinerary["total_cost"] > cheapest:
            cons.append(tr("con_dearer", extra=f"{itinerary['total_cost'] - cheapest:.0f}"))
        if itinerary["total_minutes"] > fastest:
            cons.append(tr("con_slower", extra=span(itinerary["total_minutes"] - fastest)))
        if itinerary["schedule_grade"] == "C":
            cons.append(tr("con_unverified"))
        changes = ", ".join(f"`{breach['setting']}` = {breach['value']}" for breach in itinerary["breaches"])
        lines += option_lines(number, itinerary)
        lines += ["", f"**{tr('near_pros')}**", "", *(f"- {pro}" for pro in pros),
                  "", f"**{tr('near_cons')}**", "", *(f"- {con}" for con in cons),
                  "", tr("near_fix", changes=changes)]
    return "\n".join(lines + booking_checks(itineraries))
