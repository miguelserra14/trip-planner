"""The result of a trip as a page, read from its options as data (trip_planner/report/options.py), in the way of a
flight search site: what was asked at the top, then the options as cards that open, sorted by what the rules
recommend, by price or by time; the trips without a plane; what each travel date would cost; the model's
recommendation, apart and said to be a model's; and what to check before booking.

Nothing is ranked here. The rules' order is the report's, and the other two tabs only sort by the totals and the
durations the planner worked out. Every grade is shown on the card as it is closed, and every text that is not the
app's own (a place, an operator, a link) is escaped: only http and https links become links (report_page.inline)."""
import html
from datetime import date, datetime
from itertools import groupby
from typing import Any

from app.icons import icon, mode_of
from app.report_page import inline, lights, timeline, to_html
from trip_planner.report.formatting import grade_text, leg_source, price_grade_text, price_source, with_day
from trip_planner.report.summary import booking_checks
from trip_planner.texts import tr
from trip_planner.trip.trip_settings import LIGHTS
from trip_planner.utils import as_duration

SORTS = ("best", "cheapest", "fastest")   # the tabs, by the address's ?sort=; the first is the rules' order
esc = lambda text: html.escape(str(text))

def town(written: str) -> str:
    """'Coimbra, Portugal' is Coimbra on a card."""
    return written.split(",")[0].strip()

def euros(amount: float) -> str:
    return f"{amount:.0f} EUR"

def light(grade: str) -> str:
    """A grade's dot in its colour, with the grade's symbol for who does not see colours."""
    return lights(LIGHTS[grade])

def planned(written: str) -> str:
    """When a trip was planned, as a traveller reads it: '03/11/2026 11:45'."""
    return f"{datetime.fromisoformat(written):%d/%m/%Y %H:%M}"

def weekday(day: str) -> str:
    when = date.fromisoformat(day)
    return f"{tr('date_weekdays').split(',')[when.weekday()]} {when:%d/%m}"

def ordered(data: dict[str, Any], sort: str) -> list[dict[str, Any]]:
    """The options in the order of a tab: the recommended one first and then the report's order, or by total, or by
    duration (the other number breaking a tie)."""
    options = data["options"]
    if sort == "cheapest":
        return sorted(options, key=lambda option: (option["total_eur"], option["minutes"]))
    if sort == "fastest":
        return sorted(options, key=lambda option: (option["minutes"], option["total_eur"]))
    return sorted(options, key=lambda option: option["number"] != data["recommended"])

def modes(vehicles_by_part: list[list[dict[str, Any]]]) -> str:
    """The vehicles of a trip in order, as icons: one per kind of vehicle in a row, a short line between them."""
    names = []
    for vehicles in vehicles_by_part:
        names += [name for name in (mode_of(vehicle) for vehicle in vehicles) if name != "walk"] or ["route"]
    kinds = [name for name, _ in groupby(names)]
    return '<span class="modes">' + "<i></i>".join(icon(name) for name in kinds) + "</span>"

def days_later(first: str, last: str) -> str:
    """'+1' beside a time that is on a later day than the trip started."""
    later = (date.fromisoformat(last) - date.fromisoformat(first)).days
    return f"<sup>+{later}</sup>" if later > 0 else ""

# ---------------------------------------------------------------- the options with a flight

def option_line(option: dict[str, Any], origin: str, destination: str) -> str:
    """The option drawn to scale, as the report draws it (summary.option_timeline): each part in the colour of how
    sure its times are, the waits grey."""
    flight, to_leg, from_leg = option["flight"], option["to"], option["from"]
    usual = flight["date"]
    return timeline(
        [(with_day(option["leave"]["time"], option["leave"]["date"], usual), town(origin)), (option["at_airport"], flight["departure_airport"]),
         (flight["departure_time"], "✈"), (with_day(flight["arrival_time"], flight.get("arrival_date") or usual, usual), flight["arrival_airport"]),
         (from_leg["leave_time"], ""), (with_day(option["arrive"]["time"], option["arrive"]["date"], usual), town(destination))],
        [(to_leg["grade"], to_leg["minutes"]), (None, option["airport_wait"]), ("A", flight["duration_minutes"]),
         (None, option["wait_after_landing"]), (from_leg["grade"], from_leg["minutes"])])

def badges(option: dict[str, Any], data: dict[str, Any]) -> str:
    found = []
    if option["number"] == data["recommended"]:
        found.append(f'<span class="badge {"near" if data["closest"] else "best"}">{esc(tr("app_sort_closest" if data["closest"] else "app_sort_best"))}</span>')
    if option["number"] == data["cheapest"]:
        found.append(f'<span class="badge cheap">{esc(tr("app_sort_cheapest"))}</span>')
    if option["number"] == data["fastest"]:
        found.append(f'<span class="badge quick">{esc(tr("app_sort_fastest"))}</span>')
    return f'<div class="badges">{"".join(found)}</div>' if found else ""

def price_part(total: float, confirmed: float, grade: str, may_cost: list[float] | None, versus: list[str]) -> str:
    """The price side of a card: the total, what it may really cost when part of it is a guess, how much of it an
    operator confirmed and how sure the times are, and how far it is from the cheapest and the fastest."""
    range_text = f'<small>{esc(tr("app_may_cost", lowest=f"{may_cost[0]:.0f}", highest=f"{may_cost[1]:.0f}"))}</small>' if may_cost else ""
    return (f'<div class="price"><b>{euros(total)}</b>{range_text}'
            f'<small>{light(grade)} {esc(tr("app_confirmed", confirmed=f"{confirmed:.0f}", total=f"{total:.0f}"))}</small>'
            + (f'<span class="versus">{esc(" · ".join(versus))}</span>' if versus else "") + "</div>")

def vehicles_list(vehicles: list[dict[str, Any]]) -> str:
    rows = "".join(f'<li>{icon(mode_of(vehicle))}<span><b>{esc(vehicle.get("time") or "")}</b> {esc(vehicle.get("line") or "")} '
                   f'{esc(vehicle.get("from") or "")} → {esc(vehicle.get("to") or "")} ({esc(vehicle.get("arrive") or "")})'
                   + (f', {esc(vehicle["agency"])}' if vehicle.get("agency") and vehicle["agency"] != vehicle.get("line") else "")
                   + "</span></li>" for vehicle in vehicles)
    return f'<ul class="vehicles">{rows}</ul>' if rows else ""

def leg_facts(leg: dict[str, Any]) -> str:
    """How sure the times and the price of a ground leg are, and where each comes from."""
    operators = f": {', '.join(leg['operators'])}" if leg["operators"] else ""
    return (f'<div class="facts"><span>{esc(tr("app_times"))}: {lights(esc(grade_text(leg["grade"], leg.get("service_day"))))} · '
            f'{esc(leg_source(leg))}</span><span>{esc(tr("app_price"))} {euros(leg["price_eur"])}: '
            f'{lights(esc(price_grade_text(leg)))} · {esc(price_source(leg["price_kind"])[0] + operators)}</span></div>')

def step(at: str, what: str, mark: str = "<b></b>", grade: str | None = None) -> str:
    """One step of the vertical line: a place (a dot) or a ride (its icon, the line in the colour of its grade)."""
    ride = f' class="ride" style="--ride: var(--light-{grade})"' if grade else ""
    return f'<li{ride}><span class="at">{at}</span><span class="rail">{mark}</span><div class="what">{what}</div></li>'

def option_steps(option: dict[str, Any], origin: str, destination: str) -> str:
    """The option from top to bottom, as the report says it (summary.option_lines), each ride with its vehicles and
    how sure its times and its price are."""
    flight, to_leg, from_leg = option["flight"], option["to"], option["from"]
    usual = flight["date"]
    stops = tr("direct") if flight["stops"] == 0 else tr("stopovers", count=flight["stops"])
    leave = with_day(option["leave"]["time"], option["leave"]["date"], usual)
    arrive = with_day(option["arrive"]["time"], option["arrive"]["date"], usual)
    landing = with_day(flight["arrival_time"], flight.get("arrival_date") or usual, usual)
    estimated = f' <span class="refusal">{esc(tr("computed_time"))}</span>' if to_leg["kind"] == "model" else ""
    ride = lambda leg: (vehicles_list(leg["vehicles"]) or f'<p class="muted">{esc(leg["text"])}</p>') + leg_facts(leg)
    flight_price = (f'{esc(tr("source_airline", airline=flight["airline"]))}: {lights(esc(grade_text("A")))}' if option["booking"]["verified"]
                    else f'{esc(tr("source_kiwi_price", source=flight["source"]))}: {light("B")} {esc(tr("grade_reseller"))}')
    later = f'<p class="muted">{esc(tr("later_connections", times=", ".join(from_leg["later"])))}</p>' if from_leg["later"] else ""
    other = option.get("other")
    other_text = (f'<p class="muted">{esc(tr("other_by_coach" if other["by_coach"] else "other_without_coach", leave=other["leave_time"], arrive=other["arrive_time"], price=f"{other_eur:.0f}"))}</p>'
                  if other and (other_eur := other["price_eur"]) is not None else "")
    return "<ol class=\"steps\">" + "".join([
        step(esc(leave), f'<p>{esc(tr("step_leave", origin=town(origin)))}{estimated}</p>'),
        step("", ride(to_leg), icon(mode_of(to_leg["vehicles"][0]) if to_leg["vehicles"] else "route"), to_leg["grade"]),
        step(esc(option["at_airport"]), f'<p>{esc(tr("step_at_airport", airport=flight["departure_airport"], wait=option["airport_wait"]))}</p>'),
        step(esc(flight["departure_time"]), f'<p>{esc(tr("step_flight", flights=flight["flights"], stops=stops, duration=as_duration(flight["duration_minutes"])))}</p>'
             f'<div class="facts"><span>{esc(tr("app_times"))}: {lights(esc(grade_text("A")))} · {esc(tr("source_kiwi", source=flight["source"]))}</span>'
             f'<span>{esc(tr("app_price"))} {euros(option["flight_price"])}: {flight_price}</span></div>', icon("plane"), "A"),
        step(esc(landing), f'<p>{esc(tr("step_landing", airport=flight["arrival_airport"]))}</p>'),
        step(esc(from_leg["leave_time"]), f'<p>{esc(tr("step_onward", wait=option["wait_after_landing"]))}</p>' + ride(from_leg) + later + other_text,
             icon(mode_of(from_leg["vehicles"][0]) if from_leg["vehicles"] else "route"), from_leg["grade"]),
        step(esc(arrive), f'<p>{esc(tr("step_arrival", destination=town(destination)))}</p>'),
    ]) + "</ol>"

def where_to_book(option: dict[str, Any], destination: str) -> str:
    flight, booking = option["flight"], option["booking"]
    said = (tr("book_confirmed", source=flight["source"], kiwi=f"{flight['price_eur']:.0f}") if booking["verified"] else tr("book_lookup"))
    said += "" if flight.get("cabin_bag_included") else "; " + tr("no_cabin_bag")
    rows = [f"{tr('book_flight', airline=flight['airline'])}: {booking['url']} ({said})",
            f"{tr('book_to', airport=flight['departure_airport'])}: " + (", ".join(option["to"]["urls"]) or tr("no_site")),
            f"{tr('book_from', airport=flight['arrival_airport'], destination=town(destination))}: " + (", ".join(option["from"]["urls"]) or tr("no_site"))]
    return (f'<div class="book"><h3>{esc(tr("where_to_book"))}</h3><ul>' + "".join(f"<li>{inline(row)}</li>" for row in rows) + "</ul></div>")

def option_card(option: dict[str, Any], data: dict[str, Any]) -> str:
    """One option as a card: closed, the times, the places, the vehicles, the duration, the price and its grades;
    open, the option to scale, step by step, its cost, what it breaks (a closest option) and where to book it."""
    flight = option["flight"]
    origin, destination = data["origin"]["written"], data["destination"]["written"]
    stops = tr("direct") if flight["stops"] == 0 else tr("stopovers", count=flight["stops"])
    versus = ([tr("app_versus_cheapest", eur=f"{option['versus']['cheapest_eur']:.0f}")] if option["versus"]["cheapest_eur"] >= 1 else []) + \
             ([tr("app_versus_fastest", time=as_duration(option["versus"]["fastest_minutes"]))] if option["versus"]["fastest_minutes"] > 0 else [])
    refreshed = (f'<p class="muted">{esc(tr("app_prices_refreshed", when=planned(option["prices_read"])))}</p>'
                 if option.get("prices_read") else "")
    breaches = "".join(f"<li>{esc(tr('breach_' + breach['kind'], value=breach['value'], limit=breach['limit']))}</li>" for breach in option["breaches"])
    notes = "".join(f"<li>{inline(note)}</li>" for note in option["notes"])
    cost = tr("cost_line", flight=f"{option['flight_price']:.0f}", to=f"{option['to']['price_eur']:.0f}", back=f"{option['from']['price_eur']:.0f}",
              total=f"{option['total_eur']:.0f}", duration=as_duration(option["minutes"]))
    summary = (f'<summary><div class="main">'
               f'<div class="times">{esc(option["leave"]["time"])} <span class="muted">→</span> {esc(option["arrive"]["time"])}'
               f'{days_later(option["leave"]["date"], option["arrive"]["date"])}</div>'
               f'<div class="places">{esc(town(origin))} · {esc(flight["departure_airport"])} → {esc(flight["arrival_airport"])} · {esc(town(destination))}</div>'
               f'<div class="how">{modes([option["to"]["vehicles"], [{"mode": "AIRPLANE"}], option["from"]["vehicles"]])}'
               f'<span>{icon("clock")} {esc(as_duration(option["minutes"]))}</span><span class="muted">{esc(stops)}</span>'
               f'<span class="muted">{esc(weekday(flight["date"]))} · {esc(flight["airline"])}</span></div>'
               f'{badges(option, data)}</div>'
               f'{price_part(option["total_eur"], option["confirmed_eur"], option["schedule_grade"], option["range_eur"], versus)}</summary>')
    body = (f'<div class="body">{option_line(option, origin, destination)}{refreshed}'
            + (f'<div class="notes"><h3>{esc(tr("app_breaks"))}</h3><ul>{breaches}</ul></div>' if breaches else "")
            + option_steps(option, origin, destination)
            + f'<p class="money">{inline(cost)}</p>'
            + (f'<div class="notes"><ul>{notes}</ul></div>' if notes else "")
            + where_to_book(option, destination) + "</div>")
    return f'<li id="option-{option["number"]}"><details class="option">{summary}{body}</details></li>'

# ---------------------------------------------------------------- the trips without a plane

def ground_card(number: int, trip: dict[str, Any], data: dict[str, Any]) -> str:
    """A trip by train or coach all the way, as a card like the others: its times, vehicles, changes and price;
    open, its vehicles and where to buy them. Compared with the recommended option as the report compares it."""
    minutes_from = lambda minutes: f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"
    later = trip["arrive"] // (24 * 60)
    changes = max(0, len(trip["vehicles"]) - 1)
    price = (price_part(trip["price_eur"], trip["confirmed_eur"] or 0, trip["grade"], None, []) if trip["price_eur"] is not None
             else f'<div class="price"><b class="muted">{esc(tr("ground_no_price"))}</b><small>{light(trip["grade"])}</small></div>')
    versus = ""
    if trip.get("versus_eur") is not None and data["recommended"]:
        amount = (tr("ground_far_slower", hours=as_duration(trip["minutes"] - trip["versus_minutes"])) if trip["versus_eur"] > 1000
                  else f"{trip['versus_eur']:+.0f} EUR")
        versus = f'<span class="muted">{esc(tr("col_versus", option=data["recommended"]))}: {esc(amount)}</span>'
    summary = (f'<summary><div class="main"><div class="times">{minutes_from(trip["leave"])} <span class="muted">→</span> '
               f'{minutes_from(trip["arrive"])}{f"<sup>+{later}</sup>" if later else ""}</div>'
               f'<div class="places">{esc(town(data["origin"]["written"]))} → {esc(town(data["destination"]["written"]))}</div>'
               f'<div class="how">{modes([trip["vehicles"]])}<span>{icon("clock")} {esc(as_duration(trip["minutes"]))}</span>'
               f'<span class="muted">{esc(tr("app_changes", count=changes))}</span><span class="muted">{esc(weekday(trip["day"]))}</span>{versus}</div>'
               f'</div>{price}</summary>')
    book = f"{tr('ground_book')}: " + (", ".join(trip["urls"]) or tr("no_site"))
    body = (f'<div class="body"><p class="facts">{lights(esc(grade_text(trip["grade"], trip.get("service_day"))))}</p>'
            f'{vehicles_list(trip["vehicles"])}<p class="book">{inline(book)}</p></div>')
    return f'<li id="ground-{number}"><details class="option">{summary}{body}</details></li>'

# ---------------------------------------------------------------- the page

def sort_tabs(data: dict[str, Any], sort: str) -> str:
    """The three tabs, each with the price and the time of the option it puts first."""
    tabs = []
    for name in SORTS:
        first = ordered(data, name)[0]
        words = tr("app_sort_closest") if name == "best" and data["closest"] else tr(f"app_sort_{name}")
        current = ' aria-current="true"' if name == sort else ""
        tabs.append(f'<a href="?sort={name}#options"{current}><span>{esc(words)}</span><b>{euros(first["total_eur"])}</b>'
                    f'<small>{esc(as_duration(first["minutes"]))}</small></a>')
    return f'<nav class="sort" id="options" aria-label="{esc(tr("app_sort_label"))}">{"".join(tabs)}</nav>'

def by_date(data: dict[str, Any]) -> str:
    """What each travel date would cost: the cheapest itinerary left on it, whether shown above or not."""
    if len(data["by_date"]) < 2:
        return ""
    rows = []
    for day in data["by_date"]:
        if day["total_eur"] is None:
            rows.append(f'<li><span>{esc(weekday(day["date"]))}</span><span class="muted">{esc(tr("app_no_option_that_day"))}</span><b>-</b></li>')
            continue
        which = (f'<a href="#option-{day["option"]}">{esc(tr("app_see_option", number=day["option"]))}</a>' if day["option"]
                 else esc(tr("app_not_shown")))
        rows.append(f'<li><span>{esc(weekday(day["date"]))}</span><span class="muted">{esc(day["route"])} · {esc(as_duration(day["minutes"]))} · {which}</span>'
                    f'<b>{euros(day["total_eur"])}</b></li>')
    return (f'<section class="card days"><h2>{esc(tr("app_by_date"))}</h2><p class="muted">{esc(tr("app_by_date_intro"))}</p>'
            f'<ol>{"".join(rows)}</ol></section>')

def results_page(data: dict[str, Any], sort: str, keep: str, address: str) -> str:
    """The whole page of a report with its options as data. keep is the form that keeps it; address is the report's
    own, for the link to it as Markdown and to change the search."""
    sort = sort if sort in SORTS else "best"
    origin, destination = data["origin"]["written"], data["destination"]["written"]
    days = ", ".join(f"{date.fromisoformat(day):%d/%m/%Y}" for day in data["dates"])
    summary = (f'<section class="card summary"><div><h1>{esc(town(origin))} → {esc(town(destination))}</h1>'
               f'<p class="muted">{esc(days)} · {esc(tr("app_planned", when=planned(data["written"])))}</p></div>'
               f'<div class="links"><a class="button quiet" href="{esc(address)}?edit=1">{esc(tr("app_change_search"))}</a>{keep}</div></section>')
    warnings = (f'<details class="warnings card" open><summary>{esc(tr("app_warnings", count=len(data["warnings"])))}</summary>'
                f'<ul>{"".join(f"<li>{inline(warning)}</li>" for warning in data["warnings"])}</ul></details>' if data["warnings"] else "")
    parts = [f'<p class="back"><a href="/">{esc(tr("app_all_reports"))}</a></p>', summary, warnings]
    if data["options"]:
        if data["closest"]:
            parts.append(f'<p class="notice">{esc(tr("near_title"))}</p>')
        parts.append(sort_tabs(data, sort))
        parts.append('<ol class="options">' + "".join(option_card(option, data) for option in ordered(data, sort)) + "</ol>")
    else:
        parts.append(f'<p class="notice">{esc(tr("app_no_options"))}</p>')
    if data["ground_only"]:
        parts.append(f'<h2>{esc(tr("ground_title"))}</h2><p class="muted">{esc(tr("ground_intro"))}</p>'
                     '<ol class="options">' + "".join(ground_card(number, trip, data) for number, trip in enumerate(data["ground_only"], start=1)) + "</ol>")
    parts.append(by_date(data))
    if data["recommendation"]:
        parts.append(f'<section class="card model"><h2>{esc(tr("recommendation"))}</h2><p class="muted">{esc(tr("app_model_note"))}</p>'
                     f'{to_html(data["recommendation"])[0]}</section>')
    if data["options"]:
        parts.append(f'<section class="card">{to_html(chr(10).join(booking_checks(data["options"])))[0]}</section>')
    parts.append(f'<p class="full"><a href="{esc(address)}?view=full">{esc(tr("app_full_report"))}</a></p>')
    if data["options"]:
        first = ordered(data, "best")[0]
        parts.append(f'<a class="sticky" href="#option-{first["number"]}"><span>{esc(tr("app_sort_closest" if data["closest"] else "app_sort_best"))}</span>'
                     f'<span>{euros(first["total_eur"])} · {esc(as_duration(first["minutes"]))}</span></a>')
    return "".join(parts)
