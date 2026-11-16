"""The ground transport of every flight, from the published timetables and from the operators' own booking systems."""
import asyncio
from datetime import date
from typing import Any

from trip_planner import current
from trip_planner.trip import ground_choice
from trip_planner.sources import fares
from trip_planner.sources.timetables.airport_stops import find_terminals
from trip_planner.sources.timetables.client import NOT_COVERED, Timetables
from trip_planner.sources.timetables.days import landing_day
from trip_planner.sources.timetables.joined import join_published_ends
from trip_planner.texts import tr
from trip_planner.utils import logged, say, to_minutes

MAX_FLIGHTS_CHECKED = 16         # flights whose ground transport is looked up: the cheapest ones
MAX_FLIGHTS_PER_AIRPORT_DAY = 3  # ... with at most this many per departure airport and date

def timetable_sources() -> str:
    """Where the real timetables come from, in the traveller's language."""
    return f"Transitous ({tr('source_transitous')}) + " + ", ".join(fares.RIDE_SOURCES)

def flights_to_check(flights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The flights whose ground transport is looked up: the cheapest, a few per departure airport and date.
    When the flights are the ones that break the traveller's limits (see search_flights), the ones that break the
    most important limits by the least come first: landing late, then stopovers, then the price."""
    settings = current.SETTINGS
    breaks = lambda flight: (max(0, to_minutes(flight["arrival_time"]) - settings.latest_arrival_minutes),
                             max(0, flight["stops"] - settings.max_stopovers))
    chosen, seen = [], {}
    for flight in sorted(flights, key=lambda flight: (*breaks(flight), flight["price_eur"])):
        key = (flight["departure_airport"], flight["date"])
        if len(chosen) < MAX_FLIGHTS_CHECKED and seen.get(key, 0) < MAX_FLIGHTS_PER_AIRPORT_DAY:
            seen[key] = seen.get(key, 0) + 1
            chosen.append(flight)
    return chosen

async def ground_transport_for(flights: list[dict[str, Any]], timetables: Timetables | None = None,
                               known: dict[str, Any] | None = None) -> dict[str, Any]:
    """Real ground transport for every flight: to its departure airport and from its arrival airport.
    Returns {flight id: {"to_airport": ..., "from_airport": ...}}, the operator rides under "operators"
    and the number of timetable requests under "requests".
    timetables is the client of the whole search, when there is one (it is then not closed here, and "requests"
    counts the requests of the whole search). known is what earlier rounds found: its operators' rides are not asked
    for again, and the rides found now are added to them."""
    own = timetables is None
    timetables = timetables or Timetables()
    origin, destination = (current.TRIP.origin.latitude, current.TRIP.origin.longitude), (current.TRIP.destination.latitude, current.TRIP.destination.longitude)
    try:
        used = {flight["departure_airport"] for flight in flights} | {flight["arrival_airport"] for flight in flights}
        airports = {code: current.TRIP.airport(code) for code in used}
        terminals = await asyncio.to_thread(find_terminals, list(airports.values()))
        # Not named known: that is what earlier rounds found, read below for the operators' rides
        has_terminal = {code: terminals.get(code) is not None for code in airports}
        spots = {code: tuple(terminals[code]) if has_terminal[code] else (airport["latitude"], airport["longitude"])
                 for code, airport in airports.items()}
        zone_list = await asyncio.gather(
            timetables.zone(*origin), timetables.zone(*destination), *(timetables.zone(*spots[code], airport["country"]) for code, airport in airports.items()),
            return_exceptions=True,
        )
        zones = dict(zip(["origin", "destination", *airports], [zone if isinstance(zone, str) else "" for zone in zone_list]))
        found_stops = await asyncio.gather(*(timetables.airport_stops(airports[code], terminals.get(code)) for code in airports), return_exceptions=True)
        stops = {code: found if isinstance(found, list) else [] for code, found in zip(airports, found_stops)}

        async def leg(lookup, missing_zone: bool) -> dict[str, Any]:
            if missing_zone:
                return dict(NOT_COVERED)
            try:
                return await lookup()
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", "")
                say(f"[timetables] {type(e).__name__} {status}: one leg is treated as not covered")
                return dict(NOT_COVERED)

        async def one(flight: dict[str, Any]) -> tuple:
            departure, arrival = flight["departure_airport"], flight["arrival_airport"]
            to_airport, from_airport = await asyncio.gather(
                leg(lambda: timetables.to_airport(origin, spots[departure], flight, (zones["origin"], zones[departure]), has_terminal[departure], stops[departure]),
                    not zones["origin"] or not zones[departure]),
                leg(lambda: timetables.from_airport(spots[arrival], destination, flight, (zones[arrival], zones["destination"]), has_terminal[arrival], stops[arrival]),
                    not zones["destination"] or not zones[arrival]),
            )
            return flight["id"], {"to_airport": to_airport, "from_airport": from_airport}

        in_landing_order = sorted(flights, key=lambda flight: (flight["arrival_airport"], landing_day(flight), flight["arrival_time"]))

        # Operators with their own booking system: real rides and prices for the day, on both sides of the flight.
        # Asked at the same time as the timetables: they do not need them.
        not_on_sale: list[dict[str, Any]] = []

        async def rides(name: str, source, code: str, day: str, to_airport: bool) -> tuple:
            city = current.TRIP.origin if to_airport else current.TRIP.destination
            try:
                with logged(name, f"rides {'to' if to_airport else 'from'} {code} on {day}") as entry:
                    try:
                        found_rides = await asyncio.to_thread(source, city, airports[code], day, to_airport)
                        sells = fares.SALES_DAYS.get(name)
                        # Nothing found for a day further away than it sells: not on sale yet, rather than not running
                        late = found_rides == [] and sells is not None and (date.fromisoformat(day) - date.today()).days > sells
                    except fares.NotOnSale:
                        found_rides, late = [], True
                    if late:
                        not_on_sale.append({"source": name, "airport": code, "day": day, "to_airport": to_airport,
                                            "days": fares.SALES_DAYS.get(name)})
                    entry["result"] = ("ok: not on sale yet" if late else "ok: no answer" if found_rides is None
                                       else f"ok: {len(found_rides)} rides")
                return to_airport, code, day, found_rides
            except Exception as e:
                say(f"[timetables] {name} {code} {day}: {type(e).__name__}")
                return to_airport, code, day, None
        # An operator's rides are by coach: not asked for on a side the traveller wants to make by train
        operators, operators_from = (known or {}).get("operators", {}), (known or {}).get("operators_from", {})
        seen = lambda code, day, to_airport: day in (operators if to_airport else operators_from).get(code, {})
        wanted = sorted({(flight["departure_airport"], flight["date"], True) for flight in flights if ground_choice.road_allowed(True)}
                        | {(flight["arrival_airport"], landing_day(flight), False) for flight in flights if ground_choice.road_allowed(False)})
        legs, found = await asyncio.gather(
            asyncio.gather(*(one(flight) for flight in in_landing_order)),
            asyncio.gather(*(rides(name, source, *asked) for name, source in fares.RIDE_SOURCES.items() for asked in wanted if not seen(*asked))))
        ground: dict[str, Any] = dict(legs)
        ground["operators"], ground["operators_from"] = operators, operators_from
        for to_airport, code, day, rides_found in found:
            if rides_found is not None:
                ground["operators" if to_airport else "operators_from"].setdefault(code, {}).setdefault(day, []).extend(rides_found)
        ground["not_on_sale"] = not_on_sale
        await join_published_ends(timetables, ground, flights, spots, zones, has_terminal, airports)
        ground["requests"] = timetables.requests
        return ground
    finally:
        if own:
            await timetables.close()
