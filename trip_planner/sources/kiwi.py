"""Flights, from the Kiwi MCP server. Every departure airport is searched against every arrival airport on every
date, and flight data is always copied from Kiwi by code, never retyped by a model."""
import asyncio
import json
import logging
import random
import re
from typing import Any, Dict, List

from langchain.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent

from trip_planner import current
from trip_planner.texts import tr
from trip_planner.utils import to_minutes

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
FLIGHTS: Dict[str, Dict[str, Any]] = {}
SEARCHED: set = set()            # (departure airport, arrival airport, yyyy-mm-dd) Kiwi answered for
FLIGHT_GAPS: List[str] = []      # what may be missing from the flight list, for the report
KIWI_RESULT_CAP = 15             # Kiwi returns at most this many flights per search

def register_flight(itinerary: Dict[str, Any]) -> Dict[str, Any] | None:
    """Copy one Kiwi result into FLIGHTS. Returns the flight, or None if it breaks one of the traveller's limits."""
    leg = itinerary["outbound"]
    departure_date, departure_time = leg["departureTime"][:10], leg["departureTime"][11:16]
    arrival_date, arrival_time = leg["arrivalTime"][:10], leg["arrivalTime"][11:16]
    if leg["stops"] > current.SETTINGS.max_stopovers or departure_date not in current.SETTINGS.iso_dates:
        return None
    if arrival_date != departure_date:
        return None   # lands the next day, too late to reach the destination the same day
    if to_minutes(departure_time) < current.SETTINGS.earliest_flight_minutes:
        return None   # cannot be caught: earliest time leaving plus the minimum time at the airport
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

async def kiwi_flights(fly_from: str, fly_to: str, first_day: str, last_day: str) -> List[Dict[str, Any]] | None:
    """One Kiwi search. fly_to may list several airports, the days are yyyy-mm-dd. Returns None if Kiwi did not answer."""
    as_kiwi = lambda day: f"{day[8:10]}/{day[5:7]}/{day[0:4]}"
    query = {
        "flyFrom": fly_from,
        "flyTo": fly_to,
        "departureDate": as_kiwi(first_day),
        "sort": "price",
        "dtime_from": current.SETTINGS.earliest_flight_minutes // 60,
        "max_sector_stopovers": current.SETTINGS.max_stopovers,
    }
    if last_day != first_day:
        query["departureDateTo"] = as_kiwi(last_day)
    if current.SETTINGS.latest_arrival:
        query["atime_to"] = current.SETTINGS.latest_arrival_minutes // 60   # landing later than this can never be on time
    try:
        result = await kiwi_search.ainvoke(query)
        text = result if isinstance(result, str) else "".join(block.get("text", "") for block in result if isinstance(block, dict))
        return json.loads(text)["itineraries"]
    except Exception:
        return None

async def search_airport_flights(departure: str, arrivals: List[str], days: List[str]) -> None:
    """Every flight from one departure airport. Starts with one search for all arrival airports and all dates.
    Kiwi returns at most KIWI_RESULT_CAP flights per search, so a full answer is split into smaller searches
    until nothing can have been cut off."""
    found = await kiwi_flights(departure, ",".join(arrivals), min(days), max(days))
    searches = [(arrivals, days)] if found is not None and len(found) < KIWI_RESULT_CAP else []
    if not searches:
        # Failed or possibly cut off: one search per arrival airport and per date
        for arrival in arrivals:
            for day in days:
                part = await kiwi_flights(departure, arrival, day, day)
                if part is None:
                    FLIGHT_GAPS.append(tr("gap_no_answer", route=f"{departure} -> {arrival}", day=day))
                    continue
                if len(part) >= KIWI_RESULT_CAP:
                    FLIGHT_GAPS.append(tr("gap_capped", route=f"{departure} -> {arrival}", day=day, cap=KIWI_RESULT_CAP))
                found = (found or []) + part
                searches.append(([arrival], [day]))
    for itinerary in found or []:
        register_flight(itinerary)
    for searched_arrivals, searched_days in searches:
        SEARCHED.update((departure, arrival, day) for arrival in searched_arrivals for day in searched_days)

async def search_all_flights() -> None:
    """Search every departure airport against every arrival airport on every date, in code."""
    arrivals = [airport["code"] for airport in current.TRIP.arrival]
    await asyncio.gather(*(search_airport_flights(airport["code"], arrivals, current.SETTINGS.iso_dates) for airport in current.TRIP.departure))

def describe_flight(flight: Dict[str, Any]) -> str:
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
