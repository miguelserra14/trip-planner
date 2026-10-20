"""From a city to the cheapest flight to each destination: the departure airports around it, every source asked for
each of them, and the cheapest way to each destination."""
import asyncio
from typing import Any

from discover.sources import kiwi_destinations, ryanair_destinations
from trip_planner.itineraries.legs import estimated_price
from trip_planner.places import airports_near, distance_km, load_airports, Place
from trip_planner.sources import kiwi
from trip_planner.utils import say

RADIUS_KM = 200          # departure airports this close to the city...
MAX_AIRPORTS = 3         # ... the closest ones: each one is a request per country to Kiwi
COUNTRIES_KM = 3500      # Kiwi is asked for the countries with a large airport this close to the city: Europe and around it
# Kiwi refused this program for weeks once, for asking too much, and the planner needs it. So a map asks it at most
# this many times, a couple at a time with a pause: the countries with the most large airports first.
KIWI_REQUESTS = 60
KIWI_AT_ONCE = 2
KIWI_PAUSE_SECONDS = 1.0

def countries_near(place: Place, km: float = COUNTRIES_KM) -> list[str]:
    """The two-letter codes of the countries with a large airport within km of a place, the ones with most first."""
    large: dict[str, int] = {}
    for airport in load_airports().values():
        if airport["large"] and distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"]) <= km:
            large[airport["country"]] = large.get(airport["country"], 0) + 1
    return sorted(large, key=lambda country: (-large[country], country))

def cheapest_by_destination(flights: list[dict[str, Any]], getting_there: dict[str, float]) -> list[dict[str, Any]]:
    """The cheapest way to each destination: the flight plus the estimated price of reaching its departure airport.
    The departure airports themselves are left out: they are where the traveller already is."""
    best: dict[str, dict[str, Any]] = {}
    for flight in flights:
        if flight["to"] in getting_there or flight["from"] not in getting_there:
            continue
        total = flight["price_eur"] + getting_there[flight["from"]]
        if flight["to"] not in best or total < best[flight["to"]]["total_eur"]:
            best[flight["to"]] = {**flight, "ground_eur": getting_there[flight["from"]], "total_eur": total}
    return sorted(best.values(), key=lambda flight: flight["total_eur"])

async def discover(place: Place, first_day: str, last_day: str, fast: bool = False) -> list[dict[str, Any]]:
    """The cheapest destinations from a place, between two days (yyyy-mm-dd). fast asks Ryanair only."""
    departures = airports_near(place, RADIUS_KM, airlines_only=True)[:MAX_AIRPORTS]
    if not departures:
        raise ValueError(f"No airport within {RADIUS_KM} km of {place.label}.")
    say(f"{place.label}: " + ", ".join(f"{airport['code']} ({airport['km']} km)" for airport in departures))
    getting_there = {airport["code"]: float(estimated_price(airport["km"], airport["country"])) for airport in departures}
    asked = [asyncio.to_thread(ryanair_destinations, airport["code"], first_day, last_day) for airport in departures]
    if not fast:
        await kiwi.connect_kiwi()
        if kiwi.kiwi_search is None:
            say("Kiwi is not available: only Ryanair's destinations are on the map.")
        else:
            countries = countries_near(place)[:KIWI_REQUESTS // len(departures)]
            say(f"Kiwi: {len(countries)} countries for each of the {len(departures)} airports, {len(countries) * len(departures)} requests")
            turn = asyncio.Semaphore(KIWI_AT_ONCE)

            async def politely(airport: str, country: str) -> list[dict[str, Any]] | None:
                async with turn:
                    found = await kiwi_destinations(airport, country, first_day, last_day)
                    await asyncio.sleep(KIWI_PAUSE_SECONDS)
                    return found
            asked += [politely(airport["code"], country) for airport in departures for country in countries]
    answers = await asyncio.gather(*asked, return_exceptions=True)
    flights = [flight for answer in answers if isinstance(answer, list) for flight in answer]
    unanswered = sum(1 for answer in answers if not isinstance(answer, list))
    say(f"{len(flights)} flights found" + (f", {unanswered} of {len(answers)} requests got no answer" if unanswered else ""))
    return cheapest_by_destination(flights, getting_there)
