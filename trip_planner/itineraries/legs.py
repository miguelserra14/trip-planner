"""The ground legs of an itinerary, to the departure airport and from the arrival airport: a real connection when
one is published, otherwise a model's estimate, checked against the distance."""
from typing import Any

from trip_planner import current
from trip_planner.sources import timetables
from trip_planner.texts import tr
from trip_planner.utils import to_minutes, to_time

# Used when no price was found, and to catch numbers from a model that the distance makes impossible.
ROAD_FACTOR = 1.3                 # a real trip is about this much longer than the straight line
ESTIMATE_SPEED_KMH = 70           # door-to-door speed of public transport, changes included
ESTIMATE_FIXED_MINUTES = 20       # getting to the station and waiting
ESTIMATE_EUR_PER_KM = 0.12        # typical price of bus and train in Europe
MIN_TICKET_EUR = 2.0              # no ticket is cheaper than this
MAX_PLAUSIBLE_SPEED_KMH = 200     # no public transport is faster than this in a straight line, door to door
MAX_PLAUSIBLE_EUR_PER_KM = 1.0    # ... or more expensive than this
UNVERIFIED_PRICE_FACTOR = 2       # a price that is a guess is compared as this many times itself with a real price
SMALL_ESTIMATE_EUR = 5.0          # estimates up to this (a metro ticket) are accepted even when estimates are not allowed

def estimated_minutes(km: float) -> int:
    return round(km * ROAD_FACTOR / ESTIMATE_SPEED_KMH * 60 + ESTIMATE_FIXED_MINUTES)

def estimated_price(km: float) -> float:
    return max(MIN_TICKET_EUR, float(round(km * ROAD_FACTOR * ESTIMATE_EUR_PER_KM)))

def model_price(price_eur: float, known: bool, km: float | None) -> tuple:
    """The price a model reported, if it can be believed, otherwise an estimate from the distance.
    Returns (price, "model" or "estimate"). Raises ValueError when estimates are not allowed."""
    if known and price_eur > 0 and (km is None or price_eur <= 3 + km * ROAD_FACTOR * MAX_PLAUSIBLE_EUR_PER_KM):
        return float(price_eur), "model"
    if km is None:
        raise ValueError(tr("reject_price_unknown"))
    price = estimated_price(km)
    if not current.SETTINGS.allow_estimated_prices and price > SMALL_ESTIMATE_EUR:
        raise ValueError(tr("reject_price_unknown"))
    return price, "estimate"

def real_options_text(options: list[dict[str, Any]]) -> str:
    """A few real connections, for the reason an itinerary is rejected."""
    shown = sorted({(option["leave"], option["arrive"]) for option in options})[:5]
    return ", ".join(f"{to_time(leave)} -> {to_time(arrive)}" for leave, arrive in shown) or tr("none")

def to_airport_leg(flight: dict[str, Any], ground: dict[str, Any], transfer: dict[str, Any] | None,
                   closest: bool = False) -> dict[str, Any]:
    """How the traveller gets to the departure airport: a real connection when one is published, otherwise the
    model's estimate. Raises ValueError with the reason when the flight cannot be caught.
    closest=True also accepts a real connection outside the times the traveller leaves at, or one that leaves less
    than the time asked for at the airport: the one that breaks the least, for the closest alternatives."""
    airport, day = flight["departure_airport"], flight["date"]
    km = current.TRIP.km(airport)
    at_terminal = to_minutes(flight["departure_time"]) - current.SETTINGS.min_airport_minutes
    earliest, latest = to_minutes(current.SETTINGS.earliest_leave), to_minutes(current.SETTINGS.latest_leave)
    rides = ((ground.get("operators") or {}).get(airport) or {}).get(day)        # None: no operator could be checked
    timetable = (ground.get(flight["id"]) or {}).get("to_airport") or timetables.NOT_COVERED

    real = [{**ride, "kind": "operator", "price_kind": "operator"} for ride in rides or []]
    if timetable["options"] and current.SETTINGS.free_trains_at_origin:
        # Trains cost this traveller nothing: each connection is priced vehicle by vehicle, as at the destination
        for option in timetable["options"]:
            price = sum(step_price(step, True) for step in option["steps"])
            if current.SETTINGS.allow_estimated_prices or price <= SMALL_ESTIMATE_EUR:
                real.append({**option, "kind": "timetable", "price_eur": price, "price_kind": "estimate" if price else "free"})
    elif timetable["options"]:
        # A timetable has no prices: the model's price if it found a believable one, otherwise an estimate
        found = transfer is not None and transfer["reachable"]
        price, price_kind = model_price(transfer["price_eur"] if found else 0, found and transfer["price_known"], km)
        real += [{**option, "kind": "timetable", "price_eur": price, "price_kind": price_kind} for option in timetable["options"]]
    # A connection with a known price (an operator's, or a free train) comes before one whose price is a guess:
    # an unverified price must not beat a real one. Then the cheapest, then the one that leaves last.
    preference = lambda o: (o["price_kind"] not in ("operator", "free"), o["price_eur"], -o["leave"])
    suitable = [o for o in real if earliest <= o["leave"] <= latest and o["leave"] < o["arrive"] <= at_terminal]
    if suitable:
        # Leaving hours before it is needed is not free: every hour spent waiting at the airport, compared with the
        # connection that leaves last, costs what an extra hour of travel costs this traveller. A price that is a
        # guess counts UNVERIFIED_PRICE_FACTOR times, so that it only beats a real one when it clearly would.
        last = max(o["leave"] for o in suitable)
        waiting = lambda o: current.SETTINGS.extra_hours_cost(current.SETTINGS.tiebreak_eur_per_hour, (last - o["leave"]) / 60)
        weighed = lambda o: o["price_eur"] * (1 if o["price_kind"] in ("operator", "free") else UNVERIFIED_PRICE_FACTOR)
        return min(suitable, key=lambda o: (weighed(o) + waiting(o), *preference(o)))
    if closest:
        # Whatever real connection still reaches the airport: first the one that cuts the least of the time at the
        # airport, then the one that leaves closest to the window (see REQUIREMENTS in ranking.py)
        last_call = to_minutes(flight["departure_time"]) - timetables.MIN_AIRPORT_FLOOR
        reachable = [o for o in real if o["leave"] < o["arrive"] <= last_call]
        if reachable:
            return min(reachable, key=lambda o: (max(0, o["arrive"] - at_terminal),
                                                 max(earliest - o["leave"], o["leave"] - latest, 0), *preference(o)))
    if rides or timetable["covered"]:
        # Real timetables cover this airport and nothing fits: the flight cannot be caught, whatever a model says
        # Show the connections that come closest: the last ones that reach the airport in time but leave outside
        # the window, and the first ones that leave inside it but arrive too late
        in_time = sorted((o for o in real if o["arrive"] <= at_terminal), key=lambda o: o["leave"])[-3:]
        in_window = sorted((o for o in real if earliest <= o["leave"] <= latest and o["arrive"] > at_terminal),
                           key=lambda o: o["leave"])[:2]
        raise ValueError(tr("reject_no_real_connection", airport=airport, earliest=current.SETTINGS.earliest_leave,
                            latest=current.SETTINGS.latest_leave, limit=to_time(at_terminal),
                            real=real_options_text(in_time + in_window or real)))

    # No timetable covers this airport: only the model's estimate is left (grade C)
    if transfer is None:
        raise ValueError(tr("reject_no_transfer", airport=airport))
    if not transfer["reachable"]:
        raise ValueError(tr("reject_unreachable", airport=airport, notes=transfer["notes"]))
    notes = []
    minutes = transfer["duration_minutes"]
    if km is not None and minutes < max(10, km / MAX_PLAUSIBLE_SPEED_KMH * 60):
        notes.append(tr("note_impossible_duration", airport=airport, minutes=minutes, km=km, used=estimated_minutes(km)))
        minutes = estimated_minutes(km)
    price, price_kind = model_price(transfer["price_eur"], transfer["price_known"], km)
    # Aim for the ideal time at the airport, but never leave outside the allowed window
    leave = max(earliest, min(latest, at_terminal + current.SETTINGS.min_airport_minutes - current.SETTINGS.ideal_airport_minutes - minutes))
    if leave + minutes > at_terminal:
        raise ValueError(tr("unverified_basis") + " " + tr("reject_too_far", earliest=current.SETTINGS.earliest_leave, airport=airport,
                                                         arrive=to_time(leave + minutes), flight=flight["departure_time"],
                                                         minimum=current.SETTINGS.min_airport_minutes))
    return {
        "kind": "model", "grade": "C", "service_day": None, "leave": leave, "arrive": leave + minutes,
        "price_eur": price, "price_kind": price_kind, "steps": [], "text": transfer["description"],
        "urls": [transfer["operator_url"]] if transfer.get("operator_url") else [], "notes": notes,
    }

def step_price(step: dict[str, Any], free_trains: bool) -> float:
    """The price of one vehicle of a timetable connection. Timetables give no prices: a train is free for the
    travellers who said so, and everything else is estimated from the distance."""
    if step["rail"] and free_trains:
        return 0.0
    return estimated_price(step["km"])

def from_airport_leg(flight: dict[str, Any], ground: dict[str, Any], route: dict[str, Any] | None,
                     closest: bool = False) -> dict[str, Any]:
    """How the traveller gets from the arrival airport to the destination. Same rules as to_airport_leg.
    closest=True also accepts a real connection that arrives outside the window asked for: the nearest to it."""
    airport = flight["arrival_airport"]
    landing = to_minutes(flight["arrival_time"])
    in_window = lambda minutes: current.SETTINGS.earliest_arrival_minutes <= minutes <= current.SETTINGS.latest_arrival_minutes
    timetable = (ground.get(flight["id"]) or {}).get("from_airport") or timetables.NOT_COVERED

    if timetable["covered"]:
        options = [{**o, "kind": "timetable", "price_eur": sum(step_price(step, current.SETTINGS.free_trains_at_destination) for step in o["steps"])} for o in timetable["options"]]
        suitable = [o for o in options if in_window(o["arrive"])]
        if not suitable and closest and options:
            outside = lambda o: max(current.SETTINGS.earliest_arrival_minutes - o["arrive"], o["arrive"] - current.SETTINGS.latest_arrival_minutes)
            suitable = [o for o in options if outside(o) == min(map(outside, options))]
        if not suitable:
            raise ValueError(tr("reject_no_connection_after_landing", airport=airport, landing=flight["arrival_time"],
                                limits=arrival_limits(), day=timetable["service_day"], real=real_options_text(options)))
        # The price plus what arriving later is worth to the traveller: a coach that saves a few EUR and arrives
        # hours after the first train is not the better way
        first = min(o["arrive"] for o in suitable)
        value = lambda o: (o["price_eur"] + current.SETTINGS.extra_hours_cost(current.SETTINGS.tiebreak_eur_per_hour, (o["arrive"] - first) / 60),
                           o["arrive"])
        best = min(suitable, key=value)
        if not current.SETTINGS.allow_estimated_prices and best["price_eur"] > SMALL_ESTIMATE_EUR:
            raise ValueError(tr("reject_price_unknown"))
        later = sorted({o["leave"] for o in suitable if o["leave"] > best["leave"]})
        # The best of the other kind is shown next to it: by coach when the best goes by train, and the reverse
        by_coach = lambda o: any(step.get("coach") for step in o["steps"])
        others = [o for o in suitable if by_coach(o) != by_coach(best)]
        return {**best, "price_kind": "free" if best["price_eur"] == 0 else "estimate", "notes": [],
                "later": [to_time(minutes) for minutes in later[:3]],
                "other": min(others, key=value) if others else None,
                "other_by_coach": not by_coach(best)}

    # No timetable covers this route: only the model's timetable is left (grade C)
    unverified = tr("unverified_basis") + " "
    if route is None:
        raise ValueError(tr("reject_no_route", airport=airport))
    km = current.TRIP.km(airport)
    link_minutes = min(max(route["link_minutes"], 0), 180)
    at_station = landing + current.SETTINGS.min_landing_minutes + link_minutes
    link_price = route["link_price_eur"] if 0 < route["link_price_eur"] <= 30 else MIN_TICKET_EUR
    common = {"kind": "model", "grade": "C", "service_day": None, "steps": [], "notes": [],
              "urls": [route["operator_url"]] if route.get("operator_url") else []}
    if not route["train_needed"]:
        # The destination is the airport's own city: the airport link is the last leg
        if at_station >= 24 * 60 or not in_window(at_station):
            raise ValueError(unverified + tr("reject_arrival_outside", arrive=to_time(at_station), limits=arrival_limits()))
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
        raise ValueError(unverified + tr("reject_no_route", airport=airport))
    catchable = [t for t in trains if t[0] >= at_station and in_window(t[1])]
    if not catchable:
        raise ValueError(unverified + tr("reject_no_connection_after_landing", airport=airport, landing=flight["arrival_time"],
                                         limits=arrival_limits(), day=tr("model_timetable"),
                                         real=", ".join(f"{to_time(t[0])} -> {to_time(t[1])}" for t in sorted(trains, key=lambda t: t[0])[-4:])))
    price_of = lambda t: 0.0 if current.SETTINGS.free_trains_at_destination else model_price(t[2].get("price_eur") or 0, True, km)[0]
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
