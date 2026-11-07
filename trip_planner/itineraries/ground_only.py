"""Trips without a plane, by train or coach all the way (sources/timetables/all_the_way.py): which ones the traveller
can take, what they cost, how sure their times are, and how they compare with the option the rules recommend.

They are held to the traveller's limits like the flights' itineraries: when to leave, when to arrive, the reliability
asked for. Their price is the operators' own, vehicle by vehicle (fares.py). Only the short rides inside a city
(metro, tram, the bus to the station) may keep a price estimated from the distance, as in the flights' ground legs:
a trip whose train or coach no operator priced has no price at all, and is shown for its times only. A price
estimated for hundreds of kilometres would be compared with real fares and could win on a guess.
They are compared with the recommended option by the same rule as the itineraries (ranking.py): the total cost plus
what the hours beyond the faster of the two are worth to the traveller."""
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from trip_planner import current
from trip_planner.itineraries.estimates import step_price
from trip_planner.sources import fares
from trip_planner.utils import to_minutes

LOCAL_KM = 30          # a ride shorter than this is the city's own transport: its price may be estimated
PRICED_PER_DAY = 8     # connections of the timetables priced per travel date, the fastest first: each asks the operators
PRICING_THREADS = 4    # trips priced at the same time: each operator keeps its own pace (fares.py)
GROUND_SHOWN = 3       # trips without a plane shown, the best first

def main_step(step: dict[str, Any]) -> bool:
    """A vehicle that carries the traveller between the two cities, not inside one of them: a coach, or anything
    that goes further than a city's own transport (a regional bus of 90 km is not the bus to the station)."""
    return bool(step.get("coach")) or step.get("km", 0) >= LOCAL_KM

def fits(option: dict[str, Any]) -> bool:
    """Whether a trip respects when the traveller leaves and arrives. Arriving on a later day is accepted only
    when the traveller accepts it for flights (next_day_arrivals), and the arrival limits are then read on the
    clock of that day."""
    settings = current.SETTINGS
    if not to_minutes(settings.earliest_leave) <= option["leave"] <= to_minutes(settings.latest_leave):
        return False
    later, clock = divmod(option["arrive"], 24 * 60)
    if later and not settings.next_day_arrivals:
        return False
    if settings.earliest_arrival and clock < settings.earliest_arrival_minutes:
        return False
    if settings.latest_arrival and clock > to_minutes(settings.latest_arrival):
        return False
    return option["grade"] <= settings.min_reliability

def priced(option: dict[str, Any]) -> dict[str, Any]:
    """A trip with its price and its grade. FlixBus sold it for the day: confirmed. A connection of the timetables
    is asked of the operators of its vehicles; it is confirmed when every one of them sells it that day at those times."""
    if option["source"] == "FlixBus":
        return {**option, "price_kind": "operator", "confirmed_eur": option["price_eur"], "tickets": []}
    option = {**option, "grade": "A" if option["service_day"] == option["day"] else "B", "price_eur": None, "price_kind": None,
              "confirmed_eur": 0.0, "tickets": []}
    steps = option["steps"]
    found = fares.tickets(steps, option["day"], option["service_day"])
    covered = {position for ticket in found for position in ticket["positions"]}
    rest = [step for position, step in enumerate(steps) if position not in covered]
    option["tickets"] = found
    if any(main_step(step) for step in rest):
        return option   # a train or coach nobody priced: no price, its times only
    option["price_eur"] = sum(ticket["price_eur"] for ticket in found) + sum(step_price(step, False) for step in rest)
    option["confirmed_eur"] = sum(ticket["price_eur"] for ticket in found if ticket["travel_day"] and not ticket.get("tariff"))
    option["price_kind"] = ("partly" if rest else "operator_typical" if not all(ticket["travel_day"] for ticket in found)
                            else "tariff" if any(ticket.get("tariff") for ticket in found) else "operator")
    if not rest and all(ticket["travel_day"] and ticket.get("confirms") for ticket in found):
        option["grade"] = "A"
    return option

def worth(cost: float, minutes: int, fastest: int) -> float:
    """What the rules weigh a trip by: its cost and what its hours beyond the fastest are worth (ranking.py)."""
    return cost + current.SETTINGS.extra_hours_cost(current.SETTINGS.tiebreak_eur_per_hour, (minutes - fastest) / 60)

def compared(option: dict[str, Any], recommended: dict[str, Any] | None) -> dict[str, Any]:
    """A priced trip next to the recommended option: how much more (or less) it is worth to the traveller."""
    if recommended is None or option["price_eur"] is None:
        return option
    fastest = min(option["minutes"], recommended["total_minutes"])
    difference = worth(option["price_eur"], option["minutes"], fastest) - worth(recommended["total_cost"], recommended["total_minutes"], fastest)
    return {**option, "versus_eur": round(difference, 2), "beats": difference < 0, "versus_minutes": recommended["total_minutes"]}

def candidates(found: dict[str, Any]) -> list[dict[str, Any]]:
    """The trips worth pricing: those that respect the traveller's limits, one per set of trains and coaches (the
    same ones reached by another bus at either end are one trip: FlixBus's own answer first, then the faster way),
    every one FlixBus sold and the fastest few of the timetables of each day."""
    kept: dict[tuple, dict[str, Any]] = {}
    for option in found.get("connections") or []:
        option = {**option, "grade": option.get("grade") or ("A" if option["service_day"] == option["day"] else "B")}
        if not fits(option):
            continue
        key = (option["day"], tuple((step["time"], step["arrive"]) for step in option["steps"] if main_step(step)))
        better = lambda new, old: (new["source"] == "FlixBus", -new["minutes"]) > (old["source"] == "FlixBus", -old["minutes"])
        if key not in kept or better(option, kept[key]):
            kept[key] = option
    chosen = []
    for day in sorted({option["day"] for option in kept.values()}):
        of_day = [option for option in kept.values() if option["day"] == day]
        chosen += [option for option in of_day if option["source"] == "FlixBus"]
        chosen += sorted((option for option in of_day if option["source"] != "FlixBus"), key=lambda option: option["minutes"])[:PRICED_PER_DAY]
    return chosen

def price_all(found: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The candidates, each with its price and grade, asked of the operators a few at a time. Done while the
    flights' ground transport is looked up: it asks other services, and takes as long as the operators answer."""
    if not found:
        return []
    with ThreadPoolExecutor(PRICING_THREADS) as pool:
        return list(pool.map(priced, candidates(found)))

def ground_options(found: dict[str, Any] | None, recommended: dict[str, Any] | None, shown: int = GROUND_SHOWN) -> list[dict[str, Any]]:
    """The trips without a plane to show, from those priced by price_all: the priced ones best first by the rules,
    then the fastest one without a price if it is faster than all of them. Each priced one says how it compares
    with the recommended option. shown is how many priced ones: a few after the flights, more when they are the
    whole result."""
    options = (found or {}).get("priced") or []
    with_price = [option for option in options if option["price_eur"] is not None]
    fastest = min([option["minutes"] for option in options] + ([recommended["total_minutes"]] if recommended else []), default=0)
    best = sorted(with_price, key=lambda option: (worth(option["price_eur"], option["minutes"], fastest), option["price_eur"]))[:shown]
    without_price = sorted((option for option in options if option["price_eur"] is None), key=lambda option: option["minutes"])
    if without_price and all(without_price[0]["minutes"] < option["minutes"] for option in best):
        best.append(without_price[0])
    return [compared(option, recommended) for option in best]
