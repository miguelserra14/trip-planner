"""The ground legs of an itinerary, to the departure airport and from the arrival airport: a real connection when
one is published, otherwise a model's estimate, checked against the distance."""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from trip_planner import current, ground_choice
from trip_planner.sources import fares, timetables
from trip_planner.texts import tr
from trip_planner.utils import to_minutes, to_time

# Used when no price was found, and to catch numbers from a model that the distance makes impossible.
ROAD_FACTOR = 1.3                 # a real trip is about this much longer than the straight line
ESTIMATE_SPEED_KMH = 70           # door-to-door speed of public transport, changes included
ESTIMATE_FIXED_MINUTES = 20       # getting to the station and waiting
ESTIMATE_EUR_PER_KM = 0.12        # price of bus and train where nothing better is known about the country
MIN_TICKET_EUR = 2.0              # no ticket is cheaper than this
MAX_PLAUSIBLE_SPEED_KMH = 200     # no public transport is faster than this in a straight line, door to door
MAX_PLAUSIBLE_EUR_PER_KM = 1.0    # ... or more expensive than this
# How far a ground leg may be estimated, and how estimated prices count, are settings: max_estimate_km,
# small_estimate_eur and unverified_price_factor (config.py)

class Rejected(ValueError):
    """Why a flight cannot make an itinerary, written for the traveller. Any other error raised while an itinerary
    is worked out is a mistake of the program, and is not shown as a rejection (steps.itinerary_or_reason)."""

def estimated_minutes(km: float) -> int:
    return round(km * ROAD_FACTOR / ESTIMATE_SPEED_KMH * 60 + ESTIMATE_FIXED_MINUTES)

# What a km of train costs differs several times over between countries, so the estimate has a rate per country.
# Measured where the railway's own prices can be read (fares.py): the median, over a handful of routes, of the
# price divided by the distance by road. Read on 13 October 2026.
MEASURED_EUR_PER_KM = {"AT": 0.20, "IT": 0.09, "HU": 0.05, "EE": 0.065, "CZ": 0.047}
# Everywhere else, deduced from how expensive transport is in the country: Eurostat's price level of transport
# services (prc_ppp_ind, A010703, 2024, European Union = 100). The curve that fits the five measured countries is
# FITTED_EUR_PER_KM x (level / 100) squared: where transport costs twice as much, trains cost four times as much.
TRANSPORT_PRICE_LEVEL = {
    "AL": 55.7, "AT": 118.2, "BA": 118.4, "BE": 118.1, "BG": 57.2, "CH": 151.7, "CY": 97.3, "CZ": 73.6, "DE": 118.7, "DK": 159.9,
    "EE": 97.9, "GR": 89.5, "ES": 81.5, "FI": 141.2, "FR": 116.3, "HR": 87.0, "HU": 61.6, "IE": 115.1, "IS": 169.6, "IT": 84.8,
    "LT": 77.4, "LU": 98.4, "LV": 71.5, "ME": 72.0, "MK": 55.2, "MT": 89.8, "NL": 118.4, "NO": 161.6, "PL": 68.7, "PT": 80.6,
    "RO": 73.4, "RS": 77.4, "SE": 144.1, "SI": 98.9, "SK": 91.0, "TR": 63.5,
}
FITTED_EUR_PER_KM = 0.106
# Long-distance coaches cost about the same per km everywhere: the median of FlixBus's direct rides in 21 countries
COACH_EUR_PER_KM = 0.05

def eur_per_km(country: str, coach: bool = False) -> float:
    """What a km by road costs on public transport in a country (two-letter code), for the estimates."""
    if coach:
        return COACH_EUR_PER_KM
    if country in MEASURED_EUR_PER_KM:
        return MEASURED_EUR_PER_KM[country]
    if country in TRANSPORT_PRICE_LEVEL:
        return round(FITTED_EUR_PER_KM * (TRANSPORT_PRICE_LEVEL[country] / 100) ** 2, 3)
    return ESTIMATE_EUR_PER_KM

def country_of(airport: str) -> str:
    """The country of an airport of the trip, which is where its ground transport runs. Empty when not known."""
    found = current.TRIP.airport(airport) if current.TRIP else None
    return (found or {}).get("country", "")

def estimated_price(km: float, country: str = "", coach: bool = False) -> float:
    return max(MIN_TICKET_EUR, float(round(km * ROAD_FACTOR * eur_per_km(country, coach))))

def model_price(price_eur: float, known: bool, km: float | None, country: str = "") -> tuple:
    """The price a model reported, if it can be believed, otherwise an estimate from the distance.
    Returns (price, "model" or "estimate"). Raises ValueError when estimates are not allowed."""
    if known and price_eur > 0 and (km is None or price_eur <= 3 + km * ROAD_FACTOR * MAX_PLAUSIBLE_EUR_PER_KM):
        return float(price_eur), "model"
    if km is None:
        raise Rejected(tr("reject_price_unknown"))
    price = estimated_price(km, country)
    if not current.SETTINGS.allow_estimated_prices and price > current.SETTINGS.small_estimate_eur:
        raise Rejected(tr("reject_price_unknown"))
    return price, "estimate"

def real_options_text(options: list[dict[str, Any]]) -> str:
    """A few real connections, for the reason an itinerary is rejected."""
    shown = sorted({(option["leave"], option["arrive"]) for option in options})[:5]
    return ", ".join(f"{to_time(leave)} -> {to_time(arrive)}" for leave, arrive in shown) or tr("none")

# The choice between the connections of a leg is made on real prices. An estimate from the distance cannot tell
# a cheap coach from a dear train, so the tickets of the connections most likely to win are read first.
MAX_PRICED = 3       # connections of a leg whose tickets are read in one round
PRICING_ROUNDS = 2   # a second round when the real prices put a connection that was not read ahead
KNOWN_PRICES = ("operator", "free", "tariff", "operator_typical")   # kinds of price that are not a guess

def best_on_real_prices(candidates: list[dict[str, Any]], key: Callable, day: str, free_trains: bool) -> dict[str, Any]:
    """The best of the connections of a leg by key, once the tickets of the ones in front were read for day.
    A connection its operator says does not run that day is dropped (price_with_tickets). Raises Rejected when
    every connection was dropped."""
    read: list[int] = []
    running = lambda: [candidate for candidate in candidates if not candidate.get("not_running")]
    for _ in range(PRICING_ROUNDS):
        front = [candidate for candidate in sorted(running(), key=key)[:MAX_PRICED] if id(candidate) not in read]
        if not front:
            break
        with ThreadPoolExecutor(len(front)) as pool:
            list(pool.map(lambda candidate: price_with_tickets(candidate, day, free_trains), front))
        read += [id(candidate) for candidate in front]
    if not running():
        raise Rejected(not_running_reason([name for candidate in candidates for name in candidate["not_running"]], day))
    return min(running(), key=key)

def not_running_reason(operators: list[str], day: str) -> str:
    """Why a connection is dropped when its operators say it does not run on day (yyyy-mm-dd), for the traveller."""
    return tr("reject_not_running", operators=", ".join(dict.fromkeys(operators)), day=f"{day[8:10]}/{day[5:7]}/{day[:4]}")

def estimate_allowed(airport: str) -> None:
    """Raises Rejected when an airport is too far from its city for a ground leg to be estimated (max_estimate_km in the settings)."""
    km = current.TRIP.km(airport)
    if km is None or km > current.SETTINGS.max_estimate_km:
        raise Rejected(tr("reject_estimate_too_far", airport=airport, km="?" if km is None else km, limit=current.SETTINGS.max_estimate_km))

def to_airport_leg(flight: dict[str, Any], ground: dict[str, Any], transfer: dict[str, Any] | None,
                   closest: bool = False, priced: bool = False) -> dict[str, Any]:
    """How the traveller gets to the departure airport: a real connection when one is published, otherwise the
    model's estimate. Raises Rejected with the reason when the flight cannot be caught.
    closest=True also accepts a real connection outside the times the traveller leaves at, or one that leaves less
    than the time asked for at the airport: the one that breaks the least, for the closest alternatives.
    priced=True reads the operators' tickets before choosing between connections (best_on_real_prices)."""
    airport, day = flight["departure_airport"], flight["date"]
    km = current.TRIP.km(airport)
    at_terminal = to_minutes(flight["departure_time"]) - current.SETTINGS.min_airport_minutes
    earliest, latest = to_minutes(current.SETTINGS.earliest_leave), to_minutes(current.SETTINGS.latest_leave)
    rides = ((ground.get("operators") or {}).get(airport) or {}).get(day)        # None: no operator could be checked
    timetable = (ground.get(flight["id"]) or {}).get("to_airport") or timetables.NOT_COVERED

    real = [{**ride, "kind": "operator", "price_kind": "operator"} for ride in rides or []]
    # The model's price is for the way it found, which may not be the way the traveller chose
    transfer = transfer if not ground_choice.restricted(True) else None
    if timetable["options"] and current.SETTINGS.free_trains_at_origin:
        # Trains cost this traveller nothing: each connection is priced vehicle by vehicle, as at the destination
        for option in timetable["options"]:
            price = sum(step_price(step, True, country_of(airport)) for step in option["steps"])
            if current.SETTINGS.allow_estimated_prices or price <= current.SETTINGS.small_estimate_eur:
                real.append({**option, "kind": "timetable", "country": country_of(airport), "price_eur": price, "price_kind": "estimate" if price else "free"})
    elif timetable["options"]:
        # A timetable has no prices: the model's price if it found a believable one, otherwise an estimate
        found = transfer is not None and transfer["reachable"]
        price, price_kind = model_price(transfer["price_eur"] if found else 0, found and transfer["price_known"], km, country_of(airport))
        real += [{**option, "kind": "timetable", "country": country_of(airport), "price_eur": price, "price_kind": price_kind} for option in timetable["options"]]
    # Only the connections the traveller accepts (ground_choice.py), checked again here whatever their source
    chosen = [o for o in real if ground_choice.accepts(o, True)]
    if real and not chosen:
        raise Rejected(tr("reject_ground_choice", airport=airport, choice=ground_choice.describe(True), real=real_options_text(real)))
    real = chosen
    # A connection with a known price (an operator's, or a free train) comes before one whose price is a guess:
    # an unverified price must not beat a real one. Then the cheapest, then the one that leaves last.
    preference = lambda o: (o["price_kind"] not in KNOWN_PRICES, o["price_eur"], -o["leave"])
    suitable = [o for o in real if earliest <= o["leave"] <= latest and o["leave"] < o["arrive"] <= at_terminal]
    if suitable:
        # Leaving hours before it is needed is not free: every hour spent waiting at the airport, compared with the
        # connection that leaves last, costs what an extra hour of travel costs this traveller. A price that is a
        # guess counts unverified_price_factor times, so that it only beats a real one when it clearly would.
        last = max(o["leave"] for o in suitable)
        waiting = lambda o: current.SETTINGS.extra_hours_cost(current.SETTINGS.tiebreak_eur_per_hour, (last - o["leave"]) / 60)
        weighed = lambda o: o["price_eur"] * (1 if o["price_kind"] in KNOWN_PRICES else current.SETTINGS.unverified_price_factor)
        value = lambda o: (weighed(o) + waiting(o), *preference(o))
        return best_on_real_prices(suitable, value, day, current.SETTINGS.free_trains_at_origin) if priced else min(suitable, key=value)
    if closest:
        # Whatever real connection still reaches the airport: first the one that cuts the least of the time at the
        # airport, then the one that leaves closest to the window (see REQUIREMENTS in ranking.py)
        last_call = to_minutes(flight["departure_time"]) - current.SETTINGS.airport_floor_minutes
        reachable = [o for o in real if o["leave"] < o["arrive"] <= last_call]
        if reachable:
            return min(reachable, key=lambda o: (max(0, o["arrive"] - at_terminal),
                                                 max(earliest - o["leave"], o["leave"] - latest, 0), *preference(o)))
    if timetable.get("choice_excluded") and not real:
        raise Rejected(tr("reject_ground_choice_none", airport=airport, choice=ground_choice.describe(True)))
    if rides or timetable["covered"]:
        # Real timetables cover this airport and nothing fits: the flight cannot be caught, whatever a model says
        # Show the connections that come closest: the last ones that reach the airport in time but leave outside
        # the window, and the first ones that leave inside it but arrive too late
        in_time = sorted((o for o in real if o["arrive"] <= at_terminal), key=lambda o: o["leave"])[-3:]
        in_window = sorted((o for o in real if earliest <= o["leave"] <= latest and o["arrive"] > at_terminal),
                           key=lambda o: o["leave"])[:2]
        raise Rejected(tr("reject_no_real_connection", airport=airport, earliest=current.SETTINGS.earliest_leave,
                          latest=current.SETTINGS.latest_leave, limit=to_time(at_terminal),
                          real=real_options_text(in_time + in_window or real)))

    # No timetable covers this airport: only the model's estimate is left (grade C), if the airport is near enough
    # and the traveller did not choose how to travel: an estimate cannot be checked against that choice
    if ground_choice.restricted(True):
        raise Rejected(tr("reject_choice_unverifiable", airport=airport, choice=ground_choice.describe(True)))
    estimate_allowed(airport)
    if transfer is None:
        raise Rejected(tr("reject_no_transfer", airport=airport))
    if not transfer["reachable"]:
        raise Rejected(tr("reject_unreachable", airport=airport, notes=transfer["notes"]))
    notes = []
    minutes = transfer["duration_minutes"]
    if km is not None and minutes < max(10, km / MAX_PLAUSIBLE_SPEED_KMH * 60):
        notes.append(tr("note_impossible_duration", airport=airport, minutes=minutes, km=km, used=estimated_minutes(km)))
        minutes = estimated_minutes(km)
    price, price_kind = model_price(transfer["price_eur"], transfer["price_known"], km, country_of(airport))
    # Aim for the ideal time at the airport, but never leave outside the allowed window
    leave = max(earliest, min(latest, at_terminal + current.SETTINGS.min_airport_minutes - current.SETTINGS.ideal_airport_minutes - minutes))
    if leave + minutes > at_terminal:
        raise Rejected(tr("unverified_basis") + " " + tr("reject_too_far", earliest=current.SETTINGS.earliest_leave, airport=airport,
                                                       arrive=to_time(leave + minutes), flight=flight["departure_time"],
                                                       minimum=current.SETTINGS.min_airport_minutes))
    return {
        "kind": "model", "grade": "C", "service_day": None, "leave": leave, "arrive": leave + minutes,
        "price_eur": price, "price_kind": price_kind, "steps": [], "text": transfer["description"],
        "urls": [transfer["operator_url"]] if transfer.get("operator_url") else [], "notes": notes,
    }

def step_price(step: dict[str, Any], free_trains: bool, country: str = "") -> float:
    """The price of one vehicle of a timetable connection. Timetables give no prices: a train is free for the
    travellers who said so, and everything else is estimated from the distance."""
    if step["rail"] and free_trains:
        return 0.0
    return estimated_price(step["km"], country, bool(step.get("coach")))

def price_with_tickets(leg: dict[str, Any], day: str, free_trains: bool) -> None:
    """Replace the estimated price of a connection read from a timetable with what its operators sell it for
    (fares.py), vehicle by vehicle. Vehicles no operator priced keep their estimate from the distance.
    day is the day the traveller makes this leg. The leg then says under "tickets" what was found, and under
    "confirmed_eur" how much of its price is real."""
    if leg.get("kind") != "timetable" or not leg.get("steps"):
        return
    steps = leg["steps"]
    free = lambda step: step["rail"] and free_trains
    found = fares.tickets(steps, day, leg.get("service_day"), skip=free)
    # An operator that runs other rides that day but not this one says the connection does not run then, whatever
    # the timetable of another week shows. Only for a leg made within the day: every vehicle of it leaves on day
    if 0 <= leg.get("leave", 0) and leg.get("arrive", 0) < 24 * 60 and fares.not_running(steps, day):
        leg["not_running"] = fares.not_running(steps, day)
    if not found:
        return
    priced = {position for ticket in found for position in ticket["positions"]}
    rest = [step for position, step in enumerate(steps) if position not in priced and not free(step)]
    if rest and leg["price_kind"] == "model":
        return   # a model found a price for the whole way: part of it from operators does not make a better total
    leg["tickets"] = found
    leg["price_eur"] = sum(ticket["price_eur"] for ticket in found) + sum(step_price(step, False, leg.get("country", "")) for step in rest)
    # Confirmed is what a booking system answered for the travel date. A published tariff is real, but nobody checked
    # it against that departure: it makes the price typical, like a price read for another day
    leg["confirmed_eur"] = sum(ticket["price_eur"] for ticket in found if ticket["travel_day"] and not ticket.get("tariff"))
    leg["price_kind"] = ("partly" if rest else "operator_typical" if not all(ticket["travel_day"] for ticket in found)
                         else "tariff" if any(ticket.get("tariff") for ticket in found) else "operator")
    # A timetable read for another week is only typical. When the operator of every vehicle sells that very
    # departure on the day of the trip, the connection is confirmed for that day, on either side of the flight.
    if len(priced) == len(steps) and all(ticket["travel_day"] and ticket.get("confirms") for ticket in found):
        leg["grade"] = "A"
        leg["confirmed_by"] = list(dict.fromkeys(ticket["operator"] for ticket in found))

def from_airport_leg(flight: dict[str, Any], ground: dict[str, Any], route: dict[str, Any] | None,
                     closest: bool = False, priced: bool = False) -> dict[str, Any]:
    """How the traveller gets from the arrival airport to the destination. Same rules as to_airport_leg.
    closest=True also accepts a real connection that arrives outside the window asked for: the nearest to it.
    priced=True reads the operators' tickets before choosing between connections (best_on_real_prices)."""
    airport = flight["arrival_airport"]
    landing = to_minutes(flight["arrival_time"])
    in_window = lambda minutes: current.SETTINGS.earliest_arrival_minutes <= minutes <= current.SETTINGS.latest_arrival_minutes
    timetable = (ground.get(flight["id"]) or {}).get("from_airport") or timetables.NOT_COVERED
    # The rides an operator sells that day, as towards the airport: the ones the traveller can still catch
    ready = landing + current.SETTINGS.min_landing_minutes + timetables.MIN_STOP_WALK_MINUTES
    rides = [{**ride, "kind": "operator", "price_kind": "operator", "country": country_of(airport)}
             for ride in ((ground.get("operators_from") or {}).get(airport) or {}).get(timetables.landing_day(flight)) or []
             if ready <= ride["leave"] < ride["arrive"]]

    if timetable["covered"] or rides:
        options = rides + [{**o, "kind": "timetable", "country": country_of(airport),
                    "price_eur": sum(step_price(step, current.SETTINGS.free_trains_at_destination, country_of(airport)) for step in o["steps"])} for o in timetable["options"]]
        # Only the connections the traveller accepts (ground_choice.py), checked again here whatever their source
        chosen = [o for o in options if ground_choice.accepts(o, False)]
        if options and not chosen:
            raise Rejected(tr("reject_ground_choice", airport=airport, choice=ground_choice.describe(False), real=real_options_text(options)))
        options = chosen
        if not options and timetable.get("choice_excluded"):
            raise Rejected(tr("reject_ground_choice_none", airport=airport, choice=ground_choice.describe(False)))
        for option in options:
            option.setdefault("price_kind", "estimate" if option["price_eur"] else "free")
        suitable = [o for o in options if in_window(o["arrive"])]
        if not suitable and closest and options:
            outside = lambda o: max(current.SETTINGS.earliest_arrival_minutes - o["arrive"], o["arrive"] - current.SETTINGS.latest_arrival_minutes)
            suitable = [o for o in options if outside(o) == min(map(outside, options))]
        if not suitable:
            raise Rejected(tr("reject_no_connection_after_landing", airport=airport, landing=flight["arrival_time"],
                              limits=arrival_limits(), day=timetable["service_day"], real=real_options_text(options)))
        # The price plus what arriving later is worth to the traveller: a coach that saves a few EUR and arrives
        # hours after the first train is not the better way
        first = min(o["arrive"] for o in suitable)
        # As towards the airport, a price that is a guess counts unverified_price_factor times next to a real one:
        # it only wins when it clearly would. Among guesses alone nothing changes.
        some_real = lambda: any(o["price_kind"] in KNOWN_PRICES for o in suitable)
        weighed = lambda o: o["price_eur"] * (current.SETTINGS.unverified_price_factor if o["price_kind"] not in KNOWN_PRICES and some_real() else 1)
        value = lambda o: (weighed(o) + current.SETTINGS.extra_hours_cost(current.SETTINGS.tiebreak_eur_per_hour, (o["arrive"] - first) / 60),
                           o["arrive"])
        best = (best_on_real_prices(suitable, value, timetables.landing_day(flight), current.SETTINGS.free_trains_at_destination)
                if priced else min(suitable, key=value))
        if not current.SETTINGS.allow_estimated_prices and best["price_kind"] not in KNOWN_PRICES and best["price_eur"] > current.SETTINGS.small_estimate_eur:
            raise Rejected(tr("reject_price_unknown"))
        later = sorted({o["leave"] for o in suitable if o["leave"] > best["leave"] and not o.get("not_running")})
        # The best of the other kind is shown next to it: by coach when the best goes by train, and the reverse
        by_coach = lambda o: any(step.get("coach") for step in o["steps"])
        others = [o for o in suitable if by_coach(o) != by_coach(best) and not o.get("not_running")]
        return {**best, "notes": best.get("notes", []),
                "later": [to_time(minutes) for minutes in later[:3]],
                "other": min(others, key=value) if others else None,
                "other_by_coach": not by_coach(best)}

    # No timetable covers this route: only the model's timetable is left (grade C), if the airport is near enough
    # and the traveller did not choose how to travel: an estimate cannot be checked against that choice
    if ground_choice.restricted(False):
        raise Rejected(tr("reject_choice_unverifiable", airport=airport, choice=ground_choice.describe(False)))
    estimate_allowed(airport)
    unverified = tr("unverified_basis") + " "
    if route is None:
        raise Rejected(tr("reject_no_route", airport=airport))
    km = current.TRIP.km(airport)
    link_minutes = min(max(route["link_minutes"], 0), 180)
    at_station = landing + current.SETTINGS.min_landing_minutes + link_minutes
    link_price = route["link_price_eur"] if 0 < route["link_price_eur"] <= 30 else MIN_TICKET_EUR
    common = {"kind": "model", "grade": "C", "service_day": None, "steps": [], "notes": [],
              "urls": [route["operator_url"]] if route.get("operator_url") else []}
    if not route["train_needed"]:
        # The destination is the airport's own city: the airport link is the last leg
        if at_station >= 24 * 60 or not in_window(at_station):
            raise Rejected(unverified + tr("reject_arrival_outside", arrive=to_time(at_station), limits=arrival_limits()))
        return {**common, "leave": landing + current.SETTINGS.min_landing_minutes, "arrive": at_station, "price_eur": link_price,
                "price_kind": "model", "text": route["link_description"], "later": []}
    trains = []
    for candidate in route["departures"]:
        try:
            leave, arrive = to_minutes(candidate["departure_time"]), to_minutes(candidate["arrival_time"])
        except ValueError:
            continue   # a malformed time from the model, ignore that train
        # A train faster than any train, or slower than walking, is not a real train
        if arrive <= leave or (km and not 15 <= km / ((arrive - leave) / 60) <= 300):
            continue
        trains.append((leave, arrive, candidate))
    if not trains:
        raise Rejected(unverified + tr("reject_no_route", airport=airport))
    catchable = [t for t in trains if t[0] >= at_station and in_window(t[1])]
    if not catchable:
        raise Rejected(unverified + tr("reject_no_connection_after_landing", airport=airport, landing=flight["arrival_time"],
                                       limits=arrival_limits(), day=tr("model_timetable"),
                                       real=", ".join(f"{to_time(t[0])} -> {to_time(t[1])}" for t in sorted(trains, key=lambda t: t[0])[-4:])))
    price_of = lambda t: 0.0 if current.SETTINGS.free_trains_at_destination else model_price(t[2].get("price_eur") or 0, True, km, country_of(airport))[0]
    leave, arrive, train = min(catchable, key=lambda t: (price_of(t), t[1]))
    train_price = price_of((leave, arrive, train))
    estimated = not current.SETTINGS.free_trains_at_destination and not (train.get("price_eur") or 0) > 0
    return {**common, "leave": leave, "arrive": arrive, "price_eur": link_price + train_price,
            "price_kind": "estimate" if estimated else "model",
            "text": f"{route['link_description']} {route['station']}: {train['train_type']} {train['departure_time']} -> {train['arrival_time']}.",
            "later": [to_time(minutes) for minutes in sorted({t[0] for t in catchable if t[0] > leave})][:3]}

def arrival_limits() -> str:
    """The arrival window of the current trip, in the traveller's language."""
    if current.SETTINGS.earliest_arrival and current.SETTINGS.latest_arrival:
        return tr("arrival_between", earliest=current.SETTINGS.earliest_arrival, latest=current.SETTINGS.latest_arrival)
    if current.SETTINGS.earliest_arrival:
        return tr("arrival_from", earliest=current.SETTINGS.earliest_arrival)
    if current.SETTINGS.latest_arrival:
        return tr("arrival_until", latest=current.SETTINGS.latest_arrival)
    return tr("arrival_same_day")
