"""The result shown to the traveller: the comparison, each option step by step, and what to check before booking."""
from datetime import date, datetime, timedelta
from typing import Any

from trip_planner import current
from trip_planner.trip.trip_settings import LIGHTS
from trip_planner.itineraries.ground_only import main_step
from trip_planner.itineraries.ranking import price_range, REQUIREMENTS, tied_on_price
from trip_planner.report.formatting import (arriving, at_destination, day_text, leaving, grade_text, leg_source, price_grade_text, price_source,
                                             price_text, steps_text, table, timeline)
from trip_planner.texts import tr
from trip_planner.utils import as_duration, to_minutes, to_time

def total_text(itinerary: dict[str, Any]) -> str:
    """The total cost of an option, with what it may really cost when part of it is a guess: '78 EUR (59-116)'."""
    if itinerary.get("guessed_eur", 0) < 1:
        return f"{itinerary['total_cost']:.0f} EUR"
    lowest, highest = price_range(itinerary)
    return f"{itinerary['total_cost']:.0f} EUR ({lowest:.0f}-{highest:.0f})"

def ride_parts(leg: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str | None, int]]]:
    """A ground leg on the line, one part per vehicle, as the app's cards draw it (app/results.py, ride_parts): the
    points inside it, where the traveller gets off and where the next vehicle leaves, and its parts, each vehicle in
    the colour of the leg's grade and the waits between them without. The walks to the first stop and from the last
    one go with the first and the last vehicle. A leg of one vehicle, or whose vehicles' times do not fit in its own,
    stays one part."""
    whole = [], [(leg["grade"], leg["arrive"] - leg["leave"])]
    steps = leg.get("steps") or []
    if len(steps) < 2 or not all(step.get("time") and step.get("arrive") for step in steps):
        return whole
    later = lambda at, time: at + (to_minutes(time) - at) % (24 * 60)   # the first time on that clock from at on
    points, parts, at = [], [], leg["leave"]
    try:
        for number, step in enumerate(steps):
            starts = later(at, step["time"]) if number else at
            if number:
                points.append((steps[number - 1]["arrive"], steps[number - 1].get("to") or ""))
                if starts > at:   # the wait for the next vehicle
                    parts.append((None, starts - at))
                    points.append((step["time"], ""))
            at = later(starts, step["arrive"])
            parts.append((leg["grade"], at - starts))
    except ValueError:   # a time a timetable wrote some other way
        return whole
    if at > leg["arrive"]:
        return whole
    parts[-1] = (leg["grade"], parts[-1][1] + leg["arrive"] - at)   # the walk from the last stop
    return points, parts

def option_timeline(itinerary: dict[str, Any]) -> str:
    """The option as a line from home to the destination: the times where something changes, each vehicle of a
    ground leg apart, and each part in the colour of how sure its times are. The flight's times come from Kiwi for the
    day (A); waiting has no colour."""
    flight, to_leg, from_leg = itinerary["flight"], itinerary["to"], itinerary["from"]
    town = lambda written: written.split(",")[0].strip()   # "Coimbra, Portugal" is Coimbra on the line
    to_points, to_parts = ride_parts(to_leg)
    from_points, from_parts = ride_parts(from_leg)
    return timeline(
        [(leaving(itinerary), town(current.SETTINGS.origin)), *to_points, (itinerary["at_airport_time"], flight["departure_airport"]),
         (flight["departure_time"], "✈"), (at_destination(flight["arrival_time"], flight), flight["arrival_airport"]),
         (to_time(from_leg["leave"]), ""), *from_points, (arriving(itinerary), town(current.SETTINGS.destination))],
        [*to_parts, (None, itinerary["airport_wait"]), ("A", flight["duration_minutes"]),
         (None, itinerary["wait_after_landing"]), *from_parts])

def part_grades(itinerary: dict[str, Any]) -> dict[str, str]:
    """The grade of each part of an option, by the text that names the part in its table: what the table shows,
    and what a model's text about the option is checked against (agents/claims.py)."""
    return {
        "part_flight_times": "A",   # Kiwi, for the travel date
        "part_flight_price": "A" if itinerary["booking"]["verified"] else "B",
        "part_to_times": itinerary["to"]["grade"],
        "part_to_price": price_source(itinerary["to"]["price_kind"])[1],
        "part_from_times": itinerary["from"]["grade"],
        "part_from_price": price_source(itinerary["from"]["price_kind"])[1],
    }

def option_lines(number: int, itinerary: dict[str, Any]) -> list[str]:
    """One option, step by step: its cost, how reliable each part is, the times and where to book."""
    origin, destination = current.SETTINGS.origin, current.SETTINGS.destination
    lines: list[str] = []
    flight, booking, to_leg, from_leg = itinerary["flight"], itinerary["booking"], itinerary["to"], itinerary["from"]
    stops = tr("direct") if flight["stops"] == 0 else tr("stopovers", count=flight["stops"])
    grades = part_grades(itinerary)
    lines += [
        "",
        f"### {tr('option')} {number}: {flight['route']}, {date.fromisoformat(flight['date']).strftime('%d/%m')}, {itinerary['total_cost']:.0f} EUR",
        "",
        option_timeline(itinerary),
        "",
        tr("cost_line", flight=f"{itinerary['flight_price']:.0f}", to=f"{to_leg['price_eur']:.0f}", back=f"{from_leg['price_eur']:.0f}",
           total=f"{itinerary['total_cost']:.0f}", duration=as_duration(itinerary["total_minutes"])),
        "",
        f"**{tr('reliability')}**",
        "",
        *table(
            [tr("col_part"), tr("col_value"), tr("col_source"), tr("col_grade")],
            [
                [tr("part_flight_times"), f"{flight['departure_time']} → {at_destination(flight['arrival_time'], flight)}", tr("source_kiwi", source=flight["source"]),
                 grade_text(grades["part_flight_times"])],
                [tr("part_flight_price"), f"{itinerary['flight_price']:.0f} EUR",
                 tr("source_airline", airline=flight["airline"]) if booking["verified"] else tr("source_kiwi_price", source=flight["source"]),
                 grade_text("A") if grades["part_flight_price"] == "A" else f"{LIGHTS['B']} " + tr("grade_reseller")],
                [tr("part_to_times"), f"{leaving(itinerary)} → {itinerary['at_airport_time']}", leg_source(to_leg),
                 grade_text(grades["part_to_times"], to_leg.get("service_day"))],
                [tr("part_to_price"), f"{to_leg['price_eur']:.0f} EUR", price_text(to_leg), price_grade_text(to_leg)],
                [tr("part_from_times"), f"{to_time(from_leg['leave'])} → {arriving(itinerary)}", leg_source(from_leg),
                 grade_text(grades["part_from_times"], from_leg.get("service_day"))],
                [tr("part_from_price"), f"{from_leg['price_eur']:.0f} EUR", price_text(from_leg), price_grade_text(from_leg)],
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

GROUND_PLACE_CHARACTERS = 24   # a station's name on the line of a trip without a plane: the rest is in its steps
VERSUS_SHOWN_EUR = 1000        # beyond this the value of the extra hours (growing hour after hour) means nothing in euros:
                               # the difference is said in hours instead

def ground_timeline(option: dict[str, Any]) -> str:
    """A trip without a plane as a line from the origin to the destination: the trains and coaches in the colour of
    its times, and between them the waits. A short ride inside a city goes with the wait before or after it."""
    town = lambda written: written.split(",")[0].strip()
    station = lambda name: name if len(name) <= GROUND_PLACE_CHARACTERS else name[:GROUND_PLACE_CHARACTERS - 1] + "…"
    gap = lambda first, last: (to_minutes(last) - to_minutes(first)) % (24 * 60)
    main = [step for step in option["steps"] if main_step(step)] or option["steps"]
    points, parts = [(to_time(option["leave"]), town(current.SETTINGS.origin))], []
    if main[0]["time"] != to_time(option["leave"]):
        parts.append((None, gap(to_time(option["leave"]), main[0]["time"])))
        points.append((main[0]["time"], station(main[0]["from"])))
    for number, step in enumerate(main):
        parts.append((option["grade"], gap(step["time"], step["arrive"])))
        points.append((step["arrive"], station(step["to"])))
        if number + 1 < len(main):
            parts.append((None, gap(step["arrive"], main[number + 1]["time"])))
            points.append((main[number + 1]["time"], ""))
    if main[-1]["arrive"] != to_time(option["arrive"]):
        parts.append((None, gap(main[-1]["arrive"], to_time(option["arrive"]))))
        points.append((to_time(option["arrive"]), ""))
    points[-1] = (points[-1][0], town(current.SETTINGS.destination))
    return timeline(points, parts)

def ground_arrival(option: dict[str, Any]) -> str:
    """When a trip without a plane arrives, with the date when it is a later day than it leaves."""
    later = option["arrive"] // (24 * 60)
    arrival = to_time(option["arrive"])
    return arrival if not later else f"{arrival} ({date.fromisoformat(option['day']) + timedelta(days=later):%d/%m})"

def ground_lines(options: list[dict[str, Any]], recommended: int) -> list[str]:
    """The trips without a plane, after the comparison of the flights: a table, how the best one compares with the
    recommended option by the same rules, and each trip as a line in time with its vehicles and where to book."""
    if not options:
        return []
    label = lambda number: f"{tr('ground_option')} {number}"
    price = lambda option: f"{option['price_eur']:.0f} EUR" if option["price_eur"] is not None else tr("ground_no_price")
    slower = lambda option: as_duration(option["minutes"] - option["versus_minutes"])
    versus = lambda option: ("-" if "versus_eur" not in option else
                             tr("ground_far_slower", hours=slower(option)) if option["versus_eur"] > VERSUS_SHOWN_EUR else
                             f"+{option['versus_eur']:.0f} EUR" if option["versus_eur"] >= 0 else f"-{-option['versus_eur']:.0f} EUR")
    lines = ["", f"## {tr('ground_title')}", "", tr("ground_intro"), "",
             *table([tr("col_option"), tr("col_date"), tr("col_leave"), tr("col_arrive"), tr("col_duration"), tr("col_changes"),
                     tr("col_total"), tr("col_schedules"), tr("col_confirmed")] + ([tr("col_versus", option=recommended)] if recommended else []),
                    [[label(number), date.fromisoformat(option["day"]).strftime("%d/%m"), to_time(option["leave"]), ground_arrival(option),
                      as_duration(option["minutes"]), len(option["steps"]) - 1, price(option), LIGHTS[option["grade"]],
                      f"{option['confirmed_eur']:.0f} / {option['price_eur']:.0f} EUR" if option["price_eur"] is not None else "-"]
                     + ([versus(option)] if recommended else [])
                     for number, option in enumerate(options, start=1)]),
             ""]
    compared = [(number, option) for number, option in enumerate(options, start=1) if "versus_eur" in option]
    if compared:
        number, best = min(compared, key=lambda pair: pair[1]["versus_eur"])
        if best["versus_eur"] > VERSUS_SHOWN_EUR:
            lines.append(tr("ground_loses_hours", ground=label(number), option=recommended, hours=slower(best)))
        else:
            lines.append(tr("ground_beats" if best["beats"] else "ground_loses", ground=label(number), option=recommended,
                            eur=f"{abs(best['versus_eur']):.0f}"))
    elif all(option["price_eur"] is None for option in options):
        lines.append(tr("ground_none_priced"))
    for number, option in enumerate(options, start=1):
        lines += ["", f"### {label(number)}: {date.fromisoformat(option['day']).strftime('%d/%m')}, {price(option)}", "",
                  ground_timeline(option), "",
                  *(f"- **{step['time']}** {step['line']} {step['from']} → {step['to']} ({step['arrive']})"
                    + (f", {step['agency']}" if step.get("agency") and step["agency"] != step["line"] else "") for step in option["steps"]),
                  "", f"- {tr('ground_book')}: " + (", ".join(option["urls"]) or tr("no_site"))]
    return lines

def render_report(itineraries: list[dict[str, Any]], recommended: dict[str, Any], warnings: list[str], ground: list[dict[str, Any]] = ()) -> str:
    """The Markdown report shown to the traveller, in the traveller's language. ground is the trips without a
    plane (itineraries/ground_only.py), shown after every option with a flight: a trip searched with a plane reads
    its flights first (the user's choice of 2026-11-07)."""
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
        "",
        tr("legend_timeline"),
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
    lines += ground_lines(list(ground), next(number for number, itinerary in enumerate(itineraries, start=1) if itinerary is recommended))
    return "\n".join(lines + booking_checks(itineraries))

def span(minutes: int) -> str:
    """A length of time in words: '45 min' or '2h10'."""
    return f"{minutes} min" if minutes < 60 else as_duration(minutes)

def render_closest(itineraries: list[dict[str, Any]], warnings: list[str], ground: list[dict[str, Any]] = ()) -> str:
    """Shown when no itinerary respects every requirement: the closest ones, closest first, each with what it
    breaks, what speaks for and against it, and the settings that would make it valid. ground is the trips without
    a plane, compared with the closest option, after every option with a flight."""
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
        tr("legend_timeline"),
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
    return "\n".join(lines + ground_lines(list(ground), 1) + booking_checks(itineraries))

ROUND_TRIP_PAIRS = 3   # pairs of a way there and a way back shown in the summary of a round trip

def worth(itinerary: dict[str, Any]) -> float:
    """What the rules weigh an option by: its cost and what its extra hours are worth (ranking.rank_itineraries).
    The closest options have no value of time: only their cost."""
    return itinerary["total_cost"] + itinerary.get("time_cost", 0)

def round_trip_pairs(going: list[dict[str, Any]], coming: list[dict[str, Any]]) -> list[tuple]:
    """The options of each way that can go together, best first: (number there, number back, there, back). The way
    back must leave after the way there arrives, on the destination's clock for both. Each way was ranked on its own
    and no fare is cheaper for being booked with the other, so a pair is worth the sum of its two."""
    pairs = [(number, other, there, back) for number, there in enumerate(going, start=1) for other, back in enumerate(coming, start=1)
             if f"{back['leave_date']} {back['leave_time']}" > f"{there['arrival_date']} {there['arrival_time']}"]
    return sorted(pairs, key=lambda pair: (worth(pair[2]) + worth(pair[3]), pair[2]["total_cost"] + pair[3]["total_cost"]))

def ground_stay(there: dict[str, Any], back: dict[str, Any]) -> int:
    """The minutes at the destination between a trip without a plane there and one back. Each trip's times are
    minutes from the midnight of its own day, past 24 hours when it arrives on a later day."""
    moment = lambda option, minutes: datetime.fromisoformat(option["day"]) + timedelta(minutes=minutes)
    return round((moment(back, back["leave"]) - moment(there, there["arrive"])).total_seconds() / 60)

def ground_round_trip_pairs(going: list[dict[str, Any]], coming: list[dict[str, Any]], min_stay_minutes: float) -> list[tuple]:
    """The trips without a plane of each way that can go together, best first: (number there, number back, there,
    back). The way back must leave at least min_stay_minutes after the way there arrives, so that a day trip is a
    day in the city and not an hour. Priced pairs first, the cheapest first, then those with a way not priced, the
    shortest first."""
    pairs = [(number, other, there, back) for number, there in enumerate(going, start=1) for other, back in enumerate(coming, start=1)
             if ground_stay(there, back) >= min_stay_minutes]
    unpriced = lambda pair: pair[2]["price_eur"] is None or pair[3]["price_eur"] is None
    return sorted(pairs, key=lambda pair: (unpriced(pair), 0 if unpriced(pair) else pair[2]["price_eur"] + pair[3]["price_eur"],
                                           pair[2]["minutes"] + pair[3]["minutes"]))

def render_ground_round_trip(going: list[dict[str, Any]], coming: list[dict[str, Any]], origin: str, destination: str,
                             min_stay_hours: float) -> str:
    """The summary of a round trip without a plane: the best pairs of a trip there and a trip back that leave the
    traveller at least min_stay_hours at the destination, with the time there and the total."""
    lines = [f"# {tr('round_trip_title', origin=origin, destination=destination)}", ""]
    if not going or not coming:
        return "\n".join(lines + [tr("round_trip_one_side", side=tr("round_side_there") if not going else tr("round_side_back"))])
    hours = f"{min_stay_hours:g}"
    pairs = ground_round_trip_pairs(going, coming, min_stay_hours * 60)
    if not pairs:
        return "\n".join(lines + [tr("round_trip_ground_none", hours=hours)])
    price = lambda option: f"{option['price_eur']:.0f} EUR" if option["price_eur"] is not None else tr("ground_no_price")
    option = lambda number, trip: (f"{tr('ground_option')} {number}: {date.fromisoformat(trip['day']).strftime('%d/%m')}, "
                                   f"{to_time(trip['leave'])} → {ground_arrival(trip)}, {price(trip)}")
    total = lambda there, back: (f"{there['price_eur'] + back['price_eur']:.0f} EUR" if there["price_eur"] is not None and back["price_eur"] is not None
                                 else tr("ground_no_price"))
    best = pairs[0]
    lines += [
        tr("round_trip_ground_intro", hours=hours),
        "",
        *table([tr("col_round_pair"), tr("col_round_there"), tr("col_round_back"), tr("col_round_stay"), tr("col_total"), tr("col_schedules"),
                tr("col_confirmed")],
               [[f"**{rank}** ({tr('recommended_pair')})" if rank == 1 else str(rank), option(number, there), option(other, back),
                 as_duration(ground_stay(there, back)), total(there, back), LIGHTS[max(there["grade"], back["grade"])],
                 f"{there['confirmed_eur'] + back['confirmed_eur']:.0f} / {total(there, back)}"]
                for rank, (number, other, there, back) in enumerate(pairs[:ROUND_TRIP_PAIRS], start=1)]),
        "",
        tr("round_trip_ground_best", there=best[0], back=best[1], total=total(best[2], best[3]), stay=as_duration(ground_stay(best[2], best[3]))),
    ]
    return "\n".join(lines)

def render_round_trip(going: list[dict[str, Any]], coming: list[dict[str, Any]], origin: str, destination: str) -> str:
    """The summary of a round trip, after the reports of the two ways: the best pairs of an option there and an
    option back, with the days at the destination and the total."""
    lines = [f"# {tr('round_trip_title', origin=origin, destination=destination)}", ""]
    if not going or not coming:
        return "\n".join(lines + [tr("round_trip_one_side", side=tr("round_side_there") if not going else tr("round_side_back"))])
    pairs = round_trip_pairs(going, coming)
    if not pairs:
        return "\n".join(lines + [tr("round_trip_none")])
    option = lambda number, itinerary: (f"{number}: {date.fromisoformat(itinerary['flight']['date']).strftime('%d/%m')}, "
                                        f"{itinerary['flight']['route']}, {itinerary['total_cost']:.0f} EUR")
    nights = lambda there, back: (date.fromisoformat(back["leave_date"]) - date.fromisoformat(there["arrival_date"])).days
    best = pairs[0]
    lines += [
        tr("round_trip_intro"),
        "",
        *table([tr("col_round_pair"), tr("col_round_there"), tr("col_round_back"), tr("col_round_nights"), tr("col_total"),
                tr("col_schedules"), tr("col_confirmed")],
               [[f"**{rank}** ({tr('recommended_pair')})" if rank == 1 else str(rank), option(number, there), option(other, back),
                 nights(there, back), f"{there['total_cost'] + back['total_cost']:.0f} EUR",
                 LIGHTS[max(there["schedule_grade"], back["schedule_grade"])],
                 f"{there['confirmed_eur'] + back['confirmed_eur']:.0f} / {there['total_cost'] + back['total_cost']:.0f} EUR"]
                for rank, (number, other, there, back) in enumerate(pairs[:ROUND_TRIP_PAIRS], start=1)]),
        "",
        tr("round_trip_best", there=best[0], back=best[1], total=f"{best[2]['total_cost'] + best[3]['total_cost']:.0f}",
           nights=nights(best[2], best[3])),
    ]
    return "\n".join(lines)
