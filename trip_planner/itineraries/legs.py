"""The ground legs of an itinerary, to the departure airport and from the arrival airport: a real connection when
one is published, otherwise a model's estimate, checked against the distance."""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from trip_planner import current
from trip_planner.trip import ground_choice
from trip_planner.itineraries.estimates import estimated_minutes, estimated_price, MIN_TICKET_EUR, ROAD_FACTOR, step_price
from trip_planner.shapes import Answer, Connection
from trip_planner.sources import fares, timetables
from trip_planner.texts import tr
from trip_planner.utils import to_minutes, to_time

# The estimates themselves are in estimates.py. These catch numbers from a model that the distance makes impossible.
MAX_PLAUSIBLE_SPEED_KMH = 200     # no public transport is faster than this in a straight line, door to door
MAX_PLAUSIBLE_EUR_PER_KM = 1.0    # ... or more expensive than this
# How far a ground leg may be estimated, and how estimated prices count, are settings: max_estimate_km,
# small_estimate_eur and unverified_price_factor (trip/trip_settings.py)

class Rejected(ValueError):
    """Why a flight cannot make an itinerary, written for the traveller. Any other error raised while an itinerary
    is worked out is a mistake of the program, and is not shown as a rejection (steps.itinerary_or_reason).
    kind is the reason as data, for the funnel saved with the report (report/metrics.py). The first two are proven
    by a real timetable or an operator: what a flight search site cannot see (PROVEN).
      timetable      a real timetable shows no connection fits, whatever a model said
      not_running    the operator says the connection does not run that day
      flight         the flight itself breaks a limit (stopovers, an airport not asked for)
      ground_choice  no connection by the way of travelling the traveller chose
      price_unknown  no price that can be believed, and estimates are not allowed
      estimate       no real timetable, and the model's estimate cannot be used or does not fit"""

    def __init__(self, reason: str, kind: str = "other"):
        super().__init__(reason)
        self.kind = kind

PROVEN = ("timetable", "not_running")   # the kinds of Rejected a real timetable or an operator proves

def country_of(airport: str) -> str:
    """The country of an airport of the trip, which is where its ground transport runs. Empty when not known."""
    found = current.TRIP.airport(airport) if current.TRIP else None
    return (found or {}).get("country", "")

def model_price(price_eur: float, known: bool, km: float | None, country: str = "") -> tuple:
    """The price a model reported, if it can be believed, otherwise an estimate from the distance.
    Returns (price, "model" or "estimate"). Raises ValueError when estimates are not allowed."""
    if known and price_eur > 0 and (km is None or price_eur <= 3 + km * ROAD_FACTOR * MAX_PLAUSIBLE_EUR_PER_KM):
        return float(price_eur), "model"
    if km is None:
        raise Rejected(tr("reject_price_unknown"), "price_unknown")
    price = estimated_price(km, country)
    if not current.SETTINGS.allow_estimated_prices and price > current.SETTINGS.small_estimate_eur:
        raise Rejected(tr("reject_price_unknown"), "price_unknown")
    return price, "estimate"

def real_options_text(options: list[Connection]) -> str:
    """A few real connections, for the reason an itinerary is rejected."""
    shown = sorted({(option["leave"], option["arrive"]) for option in options})[:5]
    return ", ".join(f"{to_time(leave)} -> {to_time(arrive)}" for leave, arrive in shown) or tr("none")

# The choice between the connections of a leg is made on real prices. An estimate from the distance cannot tell
# a cheap coach from a dear train, so the tickets of the connections most likely to win are read first.
MAX_PRICED = 3       # connections of a leg whose tickets are read in one round
PRICING_ROUNDS = 2   # a second round when the real prices put a connection that was not read ahead
KNOWN_PRICES = ("operator", "free", "tariff", "operator_typical")   # kinds of price that are not a guess

def best_on_real_prices(candidates: list[Connection], key: Callable, day: str, free_trains: bool) -> Connection:
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
        raise Rejected(not_running_reason([name for candidate in candidates for name in candidate["not_running"]], day), "not_running")
    return min(running(), key=key)

def not_running_reason(operators: list[str], day: str) -> str:
    """Why a connection is dropped when its operators say it does not run on day (yyyy-mm-dd), for the traveller."""
    return tr("reject_not_running", operators=", ".join(dict.fromkeys(operators)), day=f"{day[8:10]}/{day[5:7]}/{day[:4]}")

def estimate_allowed(airport: str, to_airport: bool) -> None:
    """Raises Rejected when a model's estimate cannot be used for a leg no timetable covers: when the traveller chose
    how to travel, since an estimate cannot be checked against that choice, or when the airport is too far from its
    city (max_estimate_km in the settings)."""
    if ground_choice.restricted(to_airport):
        raise Rejected(tr("reject_choice_unverifiable", airport=airport, choice=ground_choice.describe(to_airport)), "ground_choice")
    km = current.TRIP.km(airport)
    if km is None or km > current.SETTINGS.max_estimate_km:
        raise Rejected(tr("reject_estimate_too_far", airport=airport, km="?" if km is None else km, limit=current.SETTINGS.max_estimate_km),
                       "estimate")

def accepted(options: list[Connection], airport: str, to_airport: bool) -> list[Connection]:
    """Only the connections the traveller accepts (ground_choice.py), checked again here whatever their source.
    Raises Rejected when there were some and none is accepted."""
    chosen = [o for o in options if ground_choice.accepts(o, to_airport)]
    if options and not chosen:
        raise Rejected(tr("reject_ground_choice", airport=airport, choice=ground_choice.describe(to_airport), real=real_options_text(options)),
                       "ground_choice")
    return chosen

def leave_window(flight: dict[str, Any]) -> tuple[int, int, int]:
    """The earliest and latest the traveller leaves for a flight, and when they must be at the terminal, in minutes."""
    settings = current.SETTINGS
    return (to_minutes(settings.earliest_leave), to_minutes(settings.latest_leave),
            to_minutes(flight["departure_time"]) - settings.min_airport_minutes)

def to_airport_leg(flight: dict[str, Any], ground: dict[str, Any], transfer: dict[str, Any] | None,
                   closest: bool = False, priced: bool = False) -> Connection:
    """How the traveller gets to the departure airport: a real connection when one is published, otherwise the
    model's estimate. Raises Rejected with the reason when the flight cannot be caught.
    closest=True also accepts a real connection outside the times the traveller leaves at, or one that leaves less
    than the time asked for at the airport: the one that breaks the least, for the closest alternatives.
    priced=True reads the operators' tickets before choosing between connections (best_on_real_prices)."""
    airport, day = flight["departure_airport"], flight["date"]
    rides = ((ground.get("operators") or {}).get(airport) or {}).get(day)        # None: no operator could be checked
    timetable = (ground.get(flight["id"]) or {}).get("to_airport") or timetables.NOT_COVERED
    # The model's price is for the way it found, which may not be the way the traveller chose
    transfer = transfer if not ground_choice.restricted(True) else None
    real = to_airport_options(flight, rides, timetable, transfer)
    best = best_to_airport(real, flight, closest, priced)
    if best is not None:
        return best
    if timetable.get("choice_excluded") and not real:
        raise Rejected(tr("reject_ground_choice_none", airport=airport, choice=ground_choice.describe(True)), "ground_choice")
    if rides or timetable["covered"]:
        # Real timetables cover this airport and nothing fits: the flight cannot be caught, whatever a model says
        raise Rejected(missed_flight_reason(real, flight), "timetable")
    return model_to_airport(flight, transfer)

def to_airport_options(flight: dict[str, Any], rides: list[Connection] | None, timetable: Answer,
                       transfer: dict[str, Any] | None) -> list[Connection]:
    """The real connections to the departure airport, each with its price: the operators' rides, and the timetable's
    connections priced vehicle by vehicle (free trains) or with the model's price, else an estimate."""
    airport, settings = flight["departure_airport"], current.SETTINGS
    real = [{**ride, "kind": "operator", "price_kind": "operator"} for ride in rides or []]
    if timetable["options"] and settings.free_trains_at_origin:
        # Trains cost this traveller nothing: each connection is priced vehicle by vehicle, as at the destination
        for option in timetable["options"]:
            price = sum(step_price(step, True, country_of(airport)) for step in option["steps"])
            if settings.allow_estimated_prices or price <= settings.small_estimate_eur:
                real.append({**option, "kind": "timetable", "country": country_of(airport), "price_eur": price, "price_kind": "estimate" if price else "free"})
    elif timetable["options"]:
        # A timetable has no prices: the model's price if it found a believable one, otherwise an estimate
        found = transfer is not None and transfer["reachable"]
        price, price_kind = model_price(transfer["price_eur"] if found else 0, found and transfer["price_known"], current.TRIP.km(airport),
                                        country_of(airport))
        real += [{**option, "kind": "timetable", "country": country_of(airport), "price_eur": price, "price_kind": price_kind} for option in timetable["options"]]
    return accepted(real, airport, True)

def best_to_airport(real: list[Connection], flight: dict[str, Any], closest: bool, priced: bool) -> Connection | None:
    """The real connection to take for a flight: the best of those that leave within the traveller's times and reach
    the terminal in time, or with closest the one that breaks the least. None when none will do."""
    settings = current.SETTINGS
    earliest, latest, at_terminal = leave_window(flight)
    # A connection with a known price (an operator's, or a free train) comes before one whose price is a guess:
    # an unverified price must not beat a real one. Then the cheapest, then the one that leaves last.
    preference = lambda o: (o["price_kind"] not in KNOWN_PRICES, o["price_eur"], -o["leave"])
    suitable = [o for o in real if earliest <= o["leave"] <= latest and o["leave"] < o["arrive"] <= at_terminal]
    if suitable:
        # Leaving hours before it is needed is not free: every hour spent waiting at the airport, compared with the
        # connection that leaves last, costs what an extra hour of travel costs this traveller. A price that is a
        # guess counts unverified_price_factor times, so that it only beats a real one when it clearly would.
        last = max(o["leave"] for o in suitable)
        waiting = lambda o: settings.extra_hours_cost(settings.tiebreak_eur_per_hour, (last - o["leave"]) / 60)
        weighed = lambda o: o["price_eur"] * (1 if o["price_kind"] in KNOWN_PRICES else settings.unverified_price_factor)
        value = lambda o: (weighed(o) + waiting(o), *preference(o))
        return best_on_real_prices(suitable, value, flight["date"], settings.free_trains_at_origin) if priced else min(suitable, key=value)
    if closest:
        # Whatever real connection still reaches the airport: first the one that cuts the least of the time at the
        # airport, then the one that leaves closest to the window (see REQUIREMENTS in ranking.py)
        last_call = to_minutes(flight["departure_time"]) - settings.airport_floor_minutes
        reachable = [o for o in real if o["leave"] < o["arrive"] <= last_call]
        if reachable:
            return min(reachable, key=lambda o: (max(0, o["arrive"] - at_terminal),
                                                 max(earliest - o["leave"], o["leave"] - latest, 0), *preference(o)))
    return None

def missed_flight_reason(real: list[Connection], flight: dict[str, Any]) -> str:
    """Why no real connection reaches the airport in time, with the ones that come closest: the last ones that reach
    the airport in time but leave outside the window, and the first ones that leave inside it but arrive too late."""
    earliest, latest, at_terminal = leave_window(flight)
    in_time = sorted((o for o in real if o["arrive"] <= at_terminal), key=lambda o: o["leave"])[-3:]
    in_window = sorted((o for o in real if earliest <= o["leave"] <= latest and o["arrive"] > at_terminal),
                       key=lambda o: o["leave"])[:2]
    return tr("reject_no_real_connection", airport=flight["departure_airport"], earliest=current.SETTINGS.earliest_leave,
              latest=current.SETTINGS.latest_leave, limit=to_time(at_terminal), real=real_options_text(in_time + in_window or real))

def model_to_airport(flight: dict[str, Any], transfer: dict[str, Any] | None) -> Connection:
    """The model's estimate of the way to the airport (grade C), the only one left when no timetable covers it."""
    airport, settings = flight["departure_airport"], current.SETTINGS
    earliest, latest, at_terminal = leave_window(flight)
    estimate_allowed(airport, True)
    if transfer is None:
        raise Rejected(tr("reject_no_transfer", airport=airport), "estimate")
    if not transfer["reachable"]:
        raise Rejected(tr("reject_unreachable", airport=airport, notes=transfer["notes"]), "estimate")
    km = current.TRIP.km(airport)
    notes = []
    minutes = transfer["duration_minutes"]
    if km is not None and minutes < max(10, km / MAX_PLAUSIBLE_SPEED_KMH * 60):
        notes.append(tr("note_impossible_duration", airport=airport, minutes=minutes, km=km, used=estimated_minutes(km)))
        minutes = estimated_minutes(km)
    price, price_kind = model_price(transfer["price_eur"], transfer["price_known"], km, country_of(airport))
    # Aim for the ideal time at the airport, but never leave outside the allowed window
    leave = max(earliest, min(latest, at_terminal + settings.min_airport_minutes - settings.ideal_airport_minutes - minutes))
    if leave + minutes > at_terminal:
        raise Rejected(tr("unverified_basis") + " " + tr("reject_too_far", earliest=settings.earliest_leave, airport=airport,
                                                       arrive=to_time(leave + minutes), flight=flight["departure_time"],
                                                       minimum=settings.min_airport_minutes), "estimate")
    return {
        "kind": "model", "grade": "C", "service_day": None, "leave": leave, "arrive": leave + minutes,
        "price_eur": price, "price_kind": price_kind, "steps": [], "text": transfer["description"],
        "urls": [transfer["operator_url"]] if transfer.get("operator_url") else [], "notes": notes,
    }

def price_with_tickets(leg: Connection, day: str, free_trains: bool) -> None:
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
                     closest: bool = False, priced: bool = False) -> Connection:
    """How the traveller gets from the arrival airport to the destination. Same rules as to_airport_leg.
    closest=True also accepts a real connection that arrives outside the window asked for: the nearest to it.
    priced=True reads the operators' tickets before choosing between connections (best_on_real_prices)."""
    airport = flight["arrival_airport"]
    timetable = (ground.get(flight["id"]) or {}).get("from_airport") or timetables.NOT_COVERED
    # The rides an operator sells that day, as towards the airport: the ones the traveller can still catch
    ready = to_minutes(flight["arrival_time"]) + current.SETTINGS.min_landing_minutes + timetables.MIN_STOP_WALK_MINUTES
    rides = [{**ride, "kind": "operator", "price_kind": "operator", "country": country_of(airport)}
             for ride in ((ground.get("operators_from") or {}).get(airport) or {}).get(timetables.landing_day(flight)) or []
             if ready <= ride["leave"] < ride["arrive"]]
    if timetable["covered"] or rides:
        return best_from_airport(from_airport_options(flight, rides, timetable), flight, timetable, closest, priced)
    return model_from_airport(flight, route)

def arrives_in_window(minutes: int) -> bool:
    return current.SETTINGS.earliest_arrival_minutes <= minutes <= current.SETTINGS.latest_arrival_minutes

def from_airport_options(flight: dict[str, Any], rides: list[Connection], timetable: Answer) -> list[Connection]:
    """The real connections from the arrival airport, each with its price: the operators' rides, and the timetable's
    connections priced vehicle by vehicle."""
    airport, settings = flight["arrival_airport"], current.SETTINGS
    options = rides + [{**o, "kind": "timetable", "country": country_of(airport),
                        "price_eur": sum(step_price(step, settings.free_trains_at_destination, country_of(airport)) for step in o["steps"])}
                       for o in timetable["options"]]
    options = accepted(options, airport, False)
    if not options and timetable.get("choice_excluded"):
        raise Rejected(tr("reject_ground_choice_none", airport=airport, choice=ground_choice.describe(False)), "ground_choice")
    for option in options:
        option.setdefault("price_kind", "estimate" if option["price_eur"] else "free")
    return options

def best_from_airport(options: list[Connection], flight: dict[str, Any], timetable: Answer, closest: bool,
                      priced: bool) -> Connection:
    """The real connection to take after landing: the best of those that arrive within the traveller's window, or
    with closest the nearest to it, with the next ones that leave and the best of the other kind of vehicle."""
    settings = current.SETTINGS
    suitable = [o for o in options if arrives_in_window(o["arrive"])]
    if not suitable and closest and options:
        outside = lambda o: max(settings.earliest_arrival_minutes - o["arrive"], o["arrive"] - settings.latest_arrival_minutes)
        suitable = [o for o in options if outside(o) == min(map(outside, options))]
    if not suitable:
        raise Rejected(tr("reject_no_connection_after_landing", airport=flight["arrival_airport"], landing=flight["arrival_time"],
                          limits=arrival_limits(), day=timetable["service_day"], real=real_options_text(options)), "timetable")
    # The price plus what arriving later is worth to the traveller: a coach that saves a few EUR and arrives
    # hours after the first train is not the better way
    first = min(o["arrive"] for o in suitable)
    # As towards the airport, a price that is a guess counts unverified_price_factor times next to a real one:
    # it only wins when it clearly would. Among guesses alone nothing changes.
    some_real = lambda: any(o["price_kind"] in KNOWN_PRICES for o in suitable)
    weighed = lambda o: o["price_eur"] * (settings.unverified_price_factor if o["price_kind"] not in KNOWN_PRICES and some_real() else 1)
    value = lambda o: (weighed(o) + settings.extra_hours_cost(settings.tiebreak_eur_per_hour, (o["arrive"] - first) / 60), o["arrive"])
    best = (best_on_real_prices(suitable, value, timetables.landing_day(flight), settings.free_trains_at_destination)
            if priced else min(suitable, key=value))
    if not settings.allow_estimated_prices and best["price_kind"] not in KNOWN_PRICES and best["price_eur"] > settings.small_estimate_eur:
        raise Rejected(tr("reject_price_unknown"), "price_unknown")
    later = sorted({o["leave"] for o in suitable if o["leave"] > best["leave"] and not o.get("not_running")})
    # The best of the other kind is shown next to it: by coach when the best goes by train, and the reverse
    by_coach = lambda o: any(step.get("coach") for step in o["steps"])
    others = [o for o in suitable if by_coach(o) != by_coach(best) and not o.get("not_running")]
    return {**best, "notes": best.get("notes", []),
            "later": [to_time(minutes) for minutes in later[:3]],
            "other": min(others, key=value) if others else None,
            "other_by_coach": not by_coach(best)}

def model_from_airport(flight: dict[str, Any], route: dict[str, Any] | None) -> Connection:
    """The model's estimate of the way from the airport (grade C), the only one left when no timetable covers it:
    the airport link, then a train when the destination is not the airport's own city."""
    airport, settings = flight["arrival_airport"], current.SETTINGS
    estimate_allowed(airport, False)
    unverified = tr("unverified_basis") + " "
    if route is None:
        raise Rejected(tr("reject_no_route", airport=airport), "estimate")
    km = current.TRIP.km(airport)
    landing = to_minutes(flight["arrival_time"])
    link_minutes = min(max(route["link_minutes"], 0), 180)
    at_station = landing + settings.min_landing_minutes + link_minutes
    link_price = route["link_price_eur"] if 0 < route["link_price_eur"] <= 30 else MIN_TICKET_EUR
    common = {"kind": "model", "grade": "C", "service_day": None, "steps": [], "notes": [],
              "urls": [route["operator_url"]] if route.get("operator_url") else []}
    if not route["train_needed"]:
        # The destination is the airport's own city: the airport link is the last leg
        if at_station >= 24 * 60 or not arrives_in_window(at_station):
            raise Rejected(unverified + tr("reject_arrival_outside", arrive=to_time(at_station), limits=arrival_limits()), "estimate")
        return {**common, "leave": landing + settings.min_landing_minutes, "arrive": at_station, "price_eur": link_price,
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
        raise Rejected(unverified + tr("reject_no_route", airport=airport), "estimate")
    catchable = [t for t in trains if t[0] >= at_station and arrives_in_window(t[1])]
    if not catchable:
        raise Rejected(unverified + tr("reject_no_connection_after_landing", airport=airport, landing=flight["arrival_time"],
                                       limits=arrival_limits(), day=tr("model_timetable"),
                                       real=", ".join(f"{to_time(t[0])} -> {to_time(t[1])}" for t in sorted(trains, key=lambda t: t[0])[-4:])),
                       "estimate")
    price_of = lambda t: 0.0 if settings.free_trains_at_destination else model_price(t[2].get("price_eur") or 0, True, km, country_of(airport))[0]
    leave, arrive, train = min(catchable, key=lambda t: (price_of(t), t[1]))
    train_price = price_of((leave, arrive, train))
    estimated = not settings.free_trains_at_destination and not (train.get("price_eur") or 0) > 0
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
