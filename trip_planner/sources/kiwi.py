"""Flights, from the Kiwi MCP server. Every departure airport is searched against every arrival airport on every
date, and flight data is always copied from Kiwi by code, never retyped by a model."""
import asyncio
import json
import logging
import random
import re
from typing import Any

from langchain.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent

from trip_planner import current
from trip_planner.texts import tr
from trip_planner.utils import to_minutes, to_time

RETRYABLE_MCP_CODES = {-32603}

# The MCP client logs a full traceback for every failed request, even when the retry below recovers.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)

class RetryMCPInterceptor:
    """Intercept MCP tool calls: retry transient failures, surface all errors gracefully.

    - Retryable McpError codes (e.g. -32603): retry with exponential backoff.
    - Non-retryable McpError codes (e.g. -32602): return error message immediately.
    - Any other exception (fetch failed, network errors, etc.): retry then return error message.
    - The Kiwi server answers 503 when it receives a burst of parallel calls, so at most
      max_concurrency calls are sent at the same time.
    """

    def __init__(self, max_retries: int = 5, max_concurrency: int = 4, verbose: bool = False):
        self.max_retries = max_retries
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.verbose = verbose   # True prints every failed attempt
        self.retries = 0         # failed attempts that were tried again
        self.failures = 0        # calls that failed for good

    async def __call__(self, request, handler):
        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with self.semaphore:
                    return await handler(request)
            except McpError as exc:
                last_error = exc
                if self.verbose:
                    print(f"[MCP interceptor] {type(exc).__name__} on {request.name} "
                          f"(code {exc.error.code}, attempt {attempt+1}/{self.max_retries}): {exc}")
                if exc.error.code not in RETRYABLE_MCP_CODES:
                    self.failures += 1
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Tool call failed (non-retryable): {exc}")],
                        isError=False,
                    )
            except Exception as exc:
                last_error = exc
                if self.verbose:
                    print(f"[MCP interceptor] {type(exc).__name__} on {request.name} "
                          f"(attempt {attempt+1}/{self.max_retries}): {exc}")

            if attempt < self.max_retries - 1:
                self.retries += 1
                await asyncio.sleep(2 ** attempt + random.random())

        self.failures += 1
        return CallToolResult(
            content=[TextContent(type="text", text=f"Tool call failed after {self.max_retries} attempts: {last_error}")],
            isError=False,
        )

# The Kiwi server refuses calls when it is busy. Each refused call is tried again, so this is only a
# problem when a call fails for good, and then the route is listed in the report as not searched.
kiwi_retries = RetryMCPInterceptor()

client = MultiServerMCPClient(
    {
        "travel_server": {
                "transport": "streamable_http",
                "url": "https://mcp.kiwi.com"
            }
    },
    tool_interceptors=[kiwi_retries],
)

kiwi_search = None   # Kiwi's flight search tool, loaded by connect_kiwi before the first trip

async def connect_kiwi() -> None:
    """Load the flight search tool from the Kiwi MCP server."""
    global kiwi_search
    # The Kiwi MCP server sometimes answers 503, so retry before giving up.
    tools = []
    for attempt in range(5):
        try:
            tools = await client.get_tools()
            break
        except Exception as exc:
            print(f"[MCP] could not load tools (attempt {attempt+1}/5): {type(exc).__name__}")
            await asyncio.sleep(2 ** attempt)

    kiwi_search = next((t for t in tools if t.name == "search-flight"), None)
    if kiwi_search is None:
        raise RuntimeError("Kiwi MCP server unavailable. Wait a minute and run the program again.")

# Every flight returned by Kiwi, by flight id. Flight data is always copied from Kiwi by code and never retyped by a model.
FLIGHTS: dict[str, dict[str, Any]] = {}
SEARCHED: set = set()            # (departure airport, arrival airport, yyyy-mm-dd) Kiwi answered for
FLIGHT_GAPS: list[str] = []      # what may be missing from the flight list, for the report
KIWI_RESULT_CAP = 15             # Kiwi returns at most this many flights per search
MAX_SPLIT_SEARCHES = 12          # most searches one departure airport is split into, by arrival airport and date

PROBE_STOPOVERS = 2   # stopovers allowed when asking Kiwi what exists without the traveller's limits

def flight_problems(leg: dict[str, Any]) -> list[str]:
    """Which of the traveller's limits a Kiwi flight breaks. Empty if the flight can be used."""
    settings, problems = current.SETTINGS, []
    departure_date, departure_time = leg["departureTime"][:10], leg["departureTime"][11:16]
    arrival_date, arrival_time = leg["arrivalTime"][:10], leg["arrivalTime"][11:16]
    if leg["stops"] > settings.max_stopovers:
        problems.append("stopovers")
    if to_minutes(departure_time) < settings.earliest_flight_minutes:
        problems.append("too_early")    # cannot be caught: earliest time leaving plus the minimum time at the airport
    if arrival_date != departure_date:
        problems.append("next_day")     # too late to reach the destination the same day
    elif settings.latest_arrival and to_minutes(arrival_time) > settings.latest_arrival_minutes:
        problems.append("too_late")     # lands after the time the traveller must already be at the destination
    return problems

def register_flight(itinerary: dict[str, Any], closest: bool = False) -> dict[str, Any] | None:
    """Copy one Kiwi result into FLIGHTS. Returns the flight, or None if it breaks one of the traveller's limits.
    closest=True also keeps a flight that breaks them, for the closest alternatives, unless it lands the next day."""
    leg = itinerary["outbound"]
    departure_date, departure_time = leg["departureTime"][:10], leg["departureTime"][11:16]
    arrival_time = leg["arrivalTime"][11:16]
    problems = flight_problems(leg)
    if departure_date not in current.SETTINGS.iso_dates or "next_day" in problems or (problems and not closest):
        return None
    if not current.TRIP.airport(leg["from"]) or not current.TRIP.airport(leg["to"]):
        return None   # Kiwi reads a code as its city: asked for BRU it also answers with CRL, which is not in the trip
    duration_minutes = leg["durationSeconds"] // 60
    flight_id = f"{leg['from']}-{leg['to']}-{departure_date[8:10]}{departure_date[5:7]}-{departure_time.replace(':', '')}"
    flight = {
        "id": flight_id,
        "date": departure_date,
        "departure_airport": leg["from"],
        "departure_time": departure_time,
        "arrival_airport": leg["to"],
        "arrival_time": arrival_time,
        "stops": leg["stops"],
        "route": " - ".join(leg["route"]),
        "flights": " + ".join(f"{s['carrierName']} {s['flightNumber']}" for s in leg["segments"]),
        "airline": leg["segments"][0]["carrierName"],
        "carrier": leg["segments"][0]["carrier"],
        "flight_number": leg["segments"][0]["flightNumber"],
        "duration_minutes": duration_minutes,
        # How far the departure clock is ahead of the arrival clock, deduced from the local times
        "timezone_shift_minutes": duration_minutes - (to_minutes(arrival_time) - to_minutes(departure_time)),
        "price_eur": float(itinerary["price"]),
        "cabin_bag_included": bool((itinerary.get("baggage") or {}).get("cabinBag")),
        "booking_url": itinerary["bookingUrl"],
    }
    if flight_id not in FLIGHTS or FLIGHTS[flight_id]["price_eur"] > flight["price_eur"]:
        FLIGHTS[flight_id] = flight
    return FLIGHTS[flight_id]

async def kiwi_flights(fly_from: str, fly_to: str, first_day: str, last_day: str,
                       stopovers: int | None = None) -> list[dict[str, Any]] | None:
    """One Kiwi search. fly_to may list several airports, the days are yyyy-mm-dd. Returns None if Kiwi did not answer.
    Giving stopovers asks for what exists at any time of the day with at most that many stopovers, instead of
    what fits the traveller's limits."""
    limits = stopovers is None
    as_kiwi = lambda day: f"{day[8:10]}/{day[5:7]}/{day[0:4]}"
    query = {
        "flyFrom": fly_from,
        "flyTo": fly_to,
        "departureDate": as_kiwi(first_day),
        "sort": "price",
        "max_sector_stopovers": current.SETTINGS.max_stopovers if limits else stopovers,
    }
    if last_day != first_day:
        query["departureDateTo"] = as_kiwi(last_day)
    if limits:
        query["dtime_from"] = current.SETTINGS.earliest_flight_minutes // 60
    if limits and current.SETTINGS.latest_arrival:
        query["atime_to"] = current.SETTINGS.latest_arrival_minutes // 60   # landing later than this can never be on time
    try:
        result = await kiwi_search.ainvoke(query)
        text = result if isinstance(result, str) else "".join(block.get("text", "") for block in result if isinstance(block, dict))
        return json.loads(text)["itineraries"]
    except Exception:
        return None

async def search_airport_flights(departure: str, arrivals: list[str], days: list[str]) -> None:
    """Every flight from one departure airport. Starts with one search for all arrival airports and all dates.
    Kiwi returns at most KIWI_RESULT_CAP flights per search, so a full answer is split into smaller searches
    until nothing can have been cut off.
    With many arrival airports that is dozens of searches per departure airport, and Kiwi refuses a client that
    asks that much. Then the split is by date only. A full answer for a date holds the cheapest flights of that
    date, and only the cheapest few per airport and date are looked at further (see timetables.flights_to_check)."""
    found = await kiwi_flights(departure, ",".join(arrivals), min(days), max(days))
    searches = [(arrivals, days)] if found is not None and len(found) < KIWI_RESULT_CAP else []
    groups = [[arrival] for arrival in arrivals] if len(arrivals) * len(days) <= MAX_SPLIT_SEARCHES else [arrivals]
    if not searches and found is not None and len(groups) == 1 and len(days) == 1:
        # Nothing smaller to ask for: the same search again would give the same answer
        FLIGHT_GAPS.append(tr("gap_capped", route=f"{departure} -> {','.join(arrivals)}", day=days[0], cap=KIWI_RESULT_CAP))
        searches.append((arrivals, days))
    elif not searches:
        # Failed or possibly cut off: one search per arrival airport (or for all of them) and per date
        for group in groups:
            for day in days:
                route = f"{departure} -> {','.join(group)}"
                part = await kiwi_flights(departure, ",".join(group), day, day)
                if part is None:
                    FLIGHT_GAPS.append(tr("gap_no_answer", route=route, day=day))
                    continue
                if len(part) >= KIWI_RESULT_CAP:
                    FLIGHT_GAPS.append(tr("gap_capped", route=route, day=day, cap=KIWI_RESULT_CAP))
                found = (found or []) + part
                searches.append((group, [day]))
    for itinerary in found or []:
        register_flight(itinerary)
    for searched_arrivals, searched_days in searches:
        SEARCHED.update((departure, arrival, day) for arrival in searched_arrivals for day in searched_days)

async def search_all_flights() -> None:
    """Search every departure airport against every arrival airport on every date, in code."""
    arrivals = [airport["code"] for airport in current.TRIP.arrival]
    await asyncio.gather(*(search_airport_flights(airport["code"], arrivals, current.SETTINGS.iso_dates) for airport in current.TRIP.departure))

async def why_no_flights() -> tuple:
    """Called when no flight fits the traveller's limits. Asks Kiwi again without them and says, in Markdown and
    in the traveller's language, which limits the flights that exist break. A model could only guess this.
    Returns that text and the flights Kiwi answered with, as it returns them."""
    settings = current.SETTINGS
    stops = max(settings.max_stopovers, PROBE_STOPOVERS)
    arrivals = ",".join(airport["code"] for airport in current.TRIP.arrival)
    # Kiwi returns the cheapest flights of each search, and those are the ones with stopovers. So each airport is
    # asked twice: with the stopovers the traveller accepts, to see what the time limits cut, and with a few more.
    answers = await asyncio.gather(*(
        kiwi_flights(airport["code"], arrivals, min(settings.iso_dates), max(settings.iso_dates), stopovers=allowed)
        for airport in current.TRIP.departure for allowed in sorted({settings.max_stopovers, stops})))
    lines = [f"## {tr('no_flights_title')}", ""]
    if all(answer is None for answer in answers):
        return "\n".join(lines + [tr("no_flights_unknown")]), []
    legs = {(leg["from"], leg["to"], leg["departureTime"], leg["arrivalTime"], leg["stops"]): leg
            for answer in answers for leg in (itinerary["outbound"] for itinerary in answer or [])
            if leg["departureTime"][:10] in settings.iso_dates}   # the same flight is sold at several prices
    broken = [problems for problems in map(flight_problems, legs.values()) if problems]
    if not broken:
        return "\n".join(lines + [tr("no_flights_none", stops=stops)]), []
    lines += [tr("no_flights_intro", stops=stops, count=len(broken)), ""]
    details = {
        "stopovers": {"maximum": settings.max_stopovers},
        "too_early": {"time": to_time(settings.earliest_flight_minutes), "earliest": settings.earliest_leave,
                      "minutes": settings.min_airport_minutes},
        "too_late": {"latest": settings.latest_arrival},
        "next_day": {},
    }
    for problem, values in details.items():
        count = sum(problem in problems for problems in broken)
        if count:
            # A flight that breaks only this limit comes back when this limit alone is relaxed
            alone = sum(problems == [problem] for problems in broken)
            lines.append(f"- {tr('no_flights_' + problem, count=count, **values)}; {tr('no_flights_alone', alone=alone)}")
    return "\n".join(lines), [itinerary for answer in answers for itinerary in answer or []]

def describe_flight(flight: dict[str, Any]) -> str:
    """One line with everything a specialist needs to know about a flight."""
    return (
        f"{flight['id']} | {flight['date']} | {flight['departure_airport']} {flight['departure_time']} -> "
        f"{flight['arrival_airport']} {flight['arrival_time']} (local times) | route {flight['route']} | "
        f"{flight['stops']} stops | {flight['duration_minutes']} min | {flight['price_eur']:.0f} EUR"
    )

@tool
async def search_route(fly_from: str, fly_to: str, date: str) -> str:
    """Search one-way economy flights for one adult between two airports on one date.
    fly_from and fly_to are IATA codes, date is dd/mm/yyyy.
    Returns the cheapest flights that fit the traveller's time window and limit on stopovers, each with a flight id."""
    if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", date):
        return "date must be dd/mm/yyyy"
    day = f"{date[6:10]}-{date[3:5]}-{date[0:2]}"
    found = await kiwi_flights(fly_from.upper(), fly_to.upper(), day, day)
    if found is None:
        return f"Search failed for {fly_from} -> {fly_to} on {date}, try again."
    flights = [flight for flight in (register_flight(itinerary) for itinerary in found) if flight]
    if not flights:
        return f"No suitable flights for {fly_from} -> {fly_to} on {date}."
    return "\n".join(describe_flight(flight) for flight in flights[:5])
