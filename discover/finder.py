"""From a city to the cheapest flight to each destination: the departure airports around it, every source asked for
each of them, and the cheapest way to each destination. Every request is kept, with what it found, for the
breakdown of what was searched (breakdown.py)."""
import asyncio
from dataclasses import dataclass, field
from typing import Any

from discover.routes import known_routes
from discover.sources import Days, kiwi_destinations, ryanair_destinations
from trip_planner.itineraries.legs import estimated_price
from trip_planner.places import airports_near, distance_km, load_airports, Place
from trip_planner.sources import kiwi
from trip_planner.utils import say

RADIUS_KM = 200          # departure airports this close to the city...
MAX_AIRPORTS = 3         # ... the closest ones: each one is a request per country to Kiwi
COUNTRIES_KM = 3500      # Kiwi is asked for the countries with a large airport this close to the city: Europe and around it
# Kiwi gives at most 15 flights per request. Asked with every airline, Ryanair filled most of them (77% of the ones to
# Germany, on 2026-10-20) and the others were left out: London by easyJet, Paris, Frankfurt, Istanbul. Ryanair's own
# answer has all its direct flights, so Kiwi is asked for the other airlines only. And itineraries with two stops or
# more filled the answers for the distant countries (none of the 19 to Turkey was direct): direct flights only, by
# default, as Ryanair's; --max-stops allows connections.
KIWI_EXCLUDED = "FR"
MAX_STOPS = 0
# Kiwi refused this program for weeks once, for asking too much, and the planner needs it. So a map asks it at most
# this many times, a couple at a time with a pause: the countries with the most large airports first.
KIWI_REQUESTS = 60
KIWI_AT_ONCE = 2
KIWI_PAUSE_SECONDS = 1.0

@dataclass
class Found:
    """What a map was made from."""
    place: Place
    days: Days
    departures: list[dict[str, Any]]                 # the traveller's airports, with "ground_eur": the estimate to reach each
    destinations: list[dict[str, Any]]               # the cheapest way to each destination, cheapest first
    flights: list[dict[str, Any]]                    # every flight any source answered
    searches: list[dict[str, Any]]                   # every request: {"source", "from", "to", "answered", "flights"}
    countries_left_out: list[str] = field(default_factory=list)   # countries Kiwi was not asked about: beyond KIWI_REQUESTS
    kiwi_options: str = ""                           # how Kiwi was asked, in words
    kiwi: bool = True                                # whether Kiwi was asked at all

def countries_near(place: Place, departures: set, km: float = COUNTRIES_KM) -> list[str]:
    """The two-letter codes of the countries with a large airport within km of a place. First the ones with most
    routes known from the departure airports (routes.py), then the ones with most large airports."""
    airports = load_airports()
    large: dict[str, int] = {}
    for airport in airports.values():
        if airport["large"] and distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"]) <= km:
            large[airport["country"]] = large.get(airport["country"], 0) + 1
    routes: dict[str, int] = {}
    for code in known_routes(departures, airports) or {}:
        routes[airports[code]["country"]] = routes.get(airports[code]["country"], 0) + 1
    return sorted(large, key=lambda country: (-routes.get(country, 0), -large[country], country))

def cheapest_by_destination(flights: list[dict[str, Any]], getting_there: dict[str, float]) -> list[dict[str, Any]]:
    """The cheapest way to each destination: the flights plus the estimated price of reaching the departure airport,
    and of getting home from the airport the return lands at. The traveller's own airports are not destinations,
    and a return that lands at none of them is left out."""
    best: dict[str, dict[str, Any]] = {}
    for flight in flights:
        back = flight.get("back")
        if flight["to"] in getting_there or flight["from"] not in getting_there or (back and back["to"] not in getting_there):
            continue
        ground = getting_there[flight["from"]] + (getting_there[back["to"]] if back else 0.0)
        total = flight["price_eur"] + ground
        if flight["to"] not in best or total < best[flight["to"]]["total_eur"]:
            best[flight["to"]] = {**flight, "ground_eur": ground, "total_eur": round(total, 2)}
    return sorted(best.values(), key=lambda flight: flight["total_eur"])

async def discover(place: Place, days: Days, fast: bool = False, max_stops: int = MAX_STOPS) -> Found:
    """The cheapest destinations from a place. fast asks Ryanair only. max_stops is the most stops each way Kiwi
    may give."""
    departures = airports_near(place, RADIUS_KM, airlines_only=True)[:MAX_AIRPORTS]
    if not departures:
        raise ValueError(f"No airport within {RADIUS_KM} km of {place.label}.")
    for airport in departures:
        airport["ground_eur"] = float(estimated_price(airport["km"], airport["country"]))
    say(f"{place.label}: " + ", ".join(f"{airport['code']} ({airport['km']} km)" for airport in departures) + f"; {days}")
    searches: list[dict[str, Any]] = []

    async def asking(source: str, start: str, target: str, ask) -> list[dict[str, Any]]:
        try:
            found = await ask()
        except Exception:
            found = None
        searches.append({"source": source, "from": start, "to": target, "answered": found is not None, "flights": found or []})
        return found or []

    asked = [asking("Ryanair", airport["code"], "all its destinations",
                    lambda code=airport["code"]: asyncio.to_thread(ryanair_destinations, code, days)) for airport in departures]
    countries, left_out = [], []
    if not fast:
        await kiwi.connect_kiwi()
        if kiwi.kiwi_search is None:
            say("Kiwi is not available: only Ryanair's destinations are on the map.")
        else:
            near = countries_near(place, {airport['code'] for airport in departures})
            countries, left_out = near[:KIWI_REQUESTS // len(departures)], near[KIWI_REQUESTS // len(departures):]
            say(f"Kiwi: {len(countries)} countries for each of the {len(departures)} airports, {len(countries) * len(departures)} requests")
            turn = asyncio.Semaphore(KIWI_AT_ONCE)

            async def politely(code: str, country: str) -> list[dict[str, Any]] | None:
                async with turn:
                    found = await kiwi_destinations(code, country, days, max_stops, KIWI_EXCLUDED)
                    await asyncio.sleep(KIWI_PAUSE_SECONDS)
                    return found
            asked += [asking("Kiwi", airport["code"], country, lambda code=airport["code"], country=country: politely(code, country))
                      for airport in departures for country in countries]
    flights = [flight for answer in await asyncio.gather(*asked) for flight in answer]
    unanswered = sum(1 for search in searches if not search["answered"])
    say(f"{len(flights)} flights found" + (f", {unanswered} of {len(searches)} requests got no answer" if unanswered else ""))
    getting_there = {airport["code"]: airport["ground_eur"] for airport in departures}
    stops = "direct flights only" if not max_stops else f"at most {max_stops} stop(s) each way"
    options = f"every airline but {KIWI_EXCLUDED}, {stops}, one flight per city, the cheapest {kiwi.RESULT_CAP}"
    return Found(place, days, departures, cheapest_by_destination(flights, getting_there), flights, searches, left_out,
                 kiwi=bool(countries), kiwi_options=options if countries else "")
