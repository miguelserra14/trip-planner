"""Flights from the Kiwi MCP server: the connection to it and one search. The first source of flight_search.py."""
import asyncio
import json
import logging
import random
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent

from trip_planner import current

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

NAME = "Kiwi"
RESULT_CAP = 15      # Kiwi returns at most this many flights per search
kiwi_search = None   # Kiwi's flight search tool, loaded by connect_kiwi before the first trip. None: Kiwi is not available

def refused(error: BaseException) -> bool:
    """Whether Kiwi answered 403: it refuses this client, and asking again changes nothing."""
    if isinstance(error, BaseExceptionGroup):
        return any(refused(inner) for inner in error.exceptions)
    return "403" in str(error)

async def connect_kiwi() -> None:
    """Load the flight search tool from the Kiwi MCP server. It stays None when Kiwi is not available."""
    global kiwi_search
    # The Kiwi MCP server sometimes answers 503, so retry before giving up.
    tools = []
    for attempt in range(5):
        try:
            tools = await client.get_tools()
            break
        except Exception as exc:
            print(f"[MCP] could not load tools (attempt {attempt+1}/5): {type(exc).__name__}")
            if refused(exc):
                break
            await asyncio.sleep(2 ** attempt)

    kiwi_search = next((t for t in tools if t.name == "search-flight"), None)

async def ask_kiwi(fly_from: str, fly_to: str, first_day: str, last_day: str,
                   stopovers: int | None = None) -> list[dict[str, Any]] | None:
    """One Kiwi search. fly_to may list several airports, the days are yyyy-mm-dd. Giving stopovers asks for what
    exists at any time of the day with at most that many stopovers, instead of what fits the traveller's limits.
    Returns the flights as Kiwi describes them, or None if Kiwi did not answer."""
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
