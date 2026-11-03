"""Real prices of ground transport. Published timetables carry no prices, so the vehicles of a connection found
in them are priced by asking the booking system of the operator that runs them, where one can be read by code
(the operators are in sources/operators/, one module each).

A price is only kept when the operator sells that very departure: same stations, same minute of departure and
of arrival. Otherwise there is no price from here, and the leg keeps its estimate from the distance.

FARE_SOURCES lists the operators that price the vehicles of a connection. RIDE_SOURCES lists the operators that can
also say everything they run between a city and an airport on one day: rides the timetables may not have, each with
its price and confirmed for that day (grade A)."""
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from trip_planner.utils import logged
from trip_planner.sources.operators.alsa import ALSA_STOP, alsa_fare
from trip_planner.sources.operators import movelia, obilet, rede_expressos
from trip_planner.sources.operators.base import agency, FareSource, http, in_eur, NotOnSale, NotRunning, RATES, TARIFF_DAYS, TIMEOUT, Trip  # noqa: F401 (used by other modules)
from trip_planner.sources.operators.elron import elron_fare
from trip_planner.sources.operators.flixbus import flixbus_fare, flixbus_rides
from trip_planner.sources.operators.mav import mav_fare
from trip_planner.sources.operators.movelia import movelia_fare, movelia_rides
from trip_planner.sources.operators.obilet import obilet_rides
from trip_planner.sources.operators.oebb import oebb_fare
from trip_planner.sources.operators.rede_expressos import rede_expressos_rides
from trip_planner.sources.operators.regiojet import regiojet_fare
from trip_planner.sources.operators.tariffs import LUXEMBOURG_STOPS, luxembourg_fare, TARIFFS
from trip_planner.sources.operators.trenitalia import trenitalia_fare

MOVELIA_STOPS = ("es-RENFE-Larga-Distancia-y-AVE_", "es-Ouigo")   # the published timetables of the trains it sells

# Operators that can list everything they run between a city and an airport on one day.
# To support another operator, write a function like flixbus_rides (operators/) and add it here.
RIDE_SOURCES = {"FlixBus": flixbus_rides, "Rede Expressos": rede_expressos_rides, "Movelia": movelia_rides, "Obilet": obilet_rides}
# How many days ahead each one sells (see base.py). FlixBus sells over a year ahead: it is never said to be not on sale
SALES_DAYS = {"Rede Expressos": rede_expressos.SALES_DAYS, "Movelia": movelia.SALES_DAYS, "Obilet": obilet.SALES_DAYS}

FARE_SOURCES = [
    FareSource("Luxembourg (free public transport)", "LU",
               lambda step: all((step.get(end) or "").startswith(LUXEMBOURG_STOPS) for end in ("from_id", "to_id")), luxembourg_fare, through=True),
    FareSource("FlixBus", "EU", lambda step: all((step.get(end) or "").startswith("eu-flixbus_") for end in ("from_id", "to_id")), flixbus_fare, confirms=True),
    FareSource("RegioJet", "EU", lambda step: agency(step).startswith(("regiojet", "student agency")), regiojet_fare, confirms=True),   # Student Agency runs its coaches
    FareSource("ÖBB", "AT", lambda step: step["rail"] and agency(step).startswith(("oebb", "obb")), oebb_fare, through=True, confirms=True),
    FareSource("Trenitalia", "IT", lambda step: step["rail"] and agency(step) in ("trenitalia", "trenord"), trenitalia_fare, through=True, confirms=True),
    FareSource("MÁV", "HU", lambda step: step["rail"] and (step.get("from_id") or "").startswith("hu-mav_"), mav_fare, through=True, confirms=True),
    FareSource("Elron", "EE", lambda step: step["rail"] and "elron" in agency(step), elron_fare, confirms=True),
    FareSource("ALSA", "ES", lambda step: all((step.get(end) or "").startswith(ALSA_STOP) for end in ("from_id", "to_id")), alsa_fare, confirms=True),
    # Movelia sells Spain's long-distance trains and coaches: Renfe's (not its suburban Cercanías), Ouigo's, and the
    # coaches of the other companies (ALSA's have their own source, above)
    FareSource("Movelia", "ES", lambda step: (step.get("rail") and (step.get("from_id") or "").startswith(MOVELIA_STOPS))
               or (step.get("coach") and (step.get("from_id") or "").startswith("es-")), movelia_fare, confirms=True),
    *TARIFFS,
]

def source_of(step: dict[str, Any]) -> FareSource | None:
    return next((source for source in FARE_SOURCES if not source.expired and source.sells(step)), None)

def runs(steps: list[dict[str, Any]]) -> list[tuple]:
    """The vehicles of a connection grouped into tickets: (operator, positions of the vehicles the ticket covers).
    Vehicles in a row of an operator that sells through tickets are one ticket."""
    found: list[tuple] = []
    for position, step in enumerate(steps):
        source = source_of(step)
        if source is None:
            continue
        if found and source.through and found[-1][0] is source and found[-1][1][-1] == position - 1:
            found[-1][1].append(position)
        else:
            found.append((source, [position]))
    return found

CACHE: dict[tuple, float | None] = {}
NOT_RUNNING: set = set()   # (operator, Trip) whose operator answered, for that day, that the departure does not run
ASKING: dict[tuple, threading.Event] = {}   # the tickets being asked for now, set once their answer is in CACHE
LOCK = threading.Lock()

def fare(source: FareSource, trip: Trip) -> float | None:
    """The price of one ticket, asked once. None when the operator does not answer or does not sell that departure.
    When its answer says the departure does not run that day (NotRunning), the ticket is noted in NOT_RUNNING.
    The itineraries are priced in several threads, and often share a ticket: one asks, the others wait for its answer."""
    key = (source.name, trip)
    with LOCK:
        if key in CACHE:
            return CACHE[key]
        asking = ASKING.get(key)
        if asking is None:
            ASKING[key] = threading.Event()
    if asking is not None:
        asking.wait()
        with LOCK:
            return CACHE.get(key)
    price = None
    try:
        if source.tariff:
            price = source.fare(trip)   # a published tariff is worked out here: nothing is asked of anyone
        else:
            with logged(source.name, f"fare {trip.start} -> {trip.end}, {trip.day} {trip.leave}") as entry:
                try:
                    price = source.fare(trip)
                except NotRunning:
                    price = None
                    with LOCK:
                        NOT_RUNNING.add(key)
                entry["result"] = ("ok: does not run that day" if key in NOT_RUNNING else "ok: no price" if price is None
                                   else f"ok: {price:.2f} EUR")
    except Exception:
        price = None   # an operator that fails only leaves the estimate in place
    finally:
        with LOCK:
            CACHE[key] = price
            ASKING.pop(key).set()
    return price

def forget_prices() -> None:
    """Forget every price and every answer of the operators so far, and the exchange rates: whatever is asked next is
    asked again. Prices change by the hour, and these are only kept for one search (see tools/prices.py)."""
    with LOCK:
        CACHE.clear()
        NOT_RUNNING.clear()
    movelia.MOVELIA_SEARCHES.clear()
    RATES.clear()

def trip_of(steps: list[dict[str, Any]], positions: list[int], day: str) -> Trip:
    """The ticket asked of an operator for a run of its vehicles (runs), on one day."""
    first, last = steps[positions[0]], steps[positions[-1]]
    return Trip(first["from"], last["to"], first.get("from_id") or "", last.get("to_id") or "",
                tuple(first.get("from_at") or ()), tuple(last.get("to_at") or ()), day, first["time"], last["arrive"],
                str(first.get("line") or ""))

def not_running(steps: list[dict[str, Any]], day: str) -> list[str]:
    """The operators whose answer for day says that one of these vehicles does not run then. Nothing is asked here:
    it reads what tickets() was answered for the same day."""
    return list(dict.fromkeys(source.name for source, positions in runs(steps)
                              if "time" in steps[positions[0]] and (source.name, trip_of(steps, positions, day)) in NOT_RUNNING))

def tickets(steps: list[dict[str, Any]], travel_day: str, service_day: str | None, skip: Callable[[dict[str, Any]], bool] = lambda step: False) -> list[dict[str, Any]]:
    """The tickets found for the vehicles of a connection: {"positions", "operator", "price_eur", "travel_day", "tariff", "confirms"}.
    Each is asked for the travel date. When the operator does not sell that date yet, for the day the timetable
    was read on, and travel_day is then False. tariff says the price is a published one and not a booking system's
    answer for that departure. confirms says the operator only answers for a departure it sells, at those very
    times. skip leaves out vehicles that cost this traveller nothing."""
    found = []
    for source, positions in runs(steps):
        first, last = steps[positions[0]], steps[positions[-1]]
        if all(skip(steps[position]) for position in positions) or "time" not in first:
            continue
        for day in dict.fromkeys(day for day in (travel_day, service_day) if day):
            if datetime.fromisoformat(day).date() < datetime.now().date():
                continue
            price = fare(source, trip_of(steps, positions, day))
            if price is not None:
                found.append({"positions": positions, "operator": source.name, "price_eur": price, "travel_day": day == travel_day,
                              "tariff": source.tariff, "confirms": source.confirms})
                break
    return found
