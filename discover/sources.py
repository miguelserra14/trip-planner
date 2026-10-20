"""The two sources that answer "everywhere from this airport": Ryanair's fare finder and Kiwi asked by country.
Each flight is {"from", "to", "price_eur", "date", "time", "airline", "stops", "source"}."""
import json
from typing import Any

import httpx

from trip_planner.sources import kiwi
from trip_planner.utils import logged

RYANAIR_FARES = "https://www.ryanair.com/api/farfnd/v4/oneWayFares"

def ryanair_flights(answer: dict[str, Any], airport: str) -> list[dict[str, Any]]:
    """The flights of an answer of Ryanair's fare finder."""
    return [{"from": airport, "to": fare["outbound"]["arrivalAirport"]["iataCode"], "price_eur": float(fare["outbound"]["price"]["value"]),
             "date": fare["outbound"]["departureDate"][:10], "time": fare["outbound"]["departureDate"][11:16],
             "airline": "Ryanair", "stops": 0, "source": "Ryanair"} for fare in answer.get("fares") or []]

def ryanair_destinations(airport: str, first_day: str, last_day: str) -> list[dict[str, Any]]:
    """The cheapest Ryanair fare to each of its destinations from an airport, between two days (yyyy-mm-dd)."""
    with logged("Ryanair", f"every destination from {airport}, {first_day} to {last_day}") as entry:
        response = httpx.get(RYANAIR_FARES, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, params={
            "departureAirportIataCode": airport, "outboundDepartureDateFrom": first_day, "outboundDepartureDateTo": last_day,
            "currency": "EUR", "adultPaxCount": 1})
        response.raise_for_status()
        found = ryanair_flights(response.json(), airport)
        entry["result"] = f"ok: {len(found)} destinations"
    return found

def kiwi_flights(text: str) -> list[dict[str, Any]] | None:
    """The flights of an answer of Kiwi's search, or None when it is not one (Kiwi failed, or refused)."""
    try:
        itineraries = json.loads(text)["itineraries"]
    except (ValueError, KeyError, TypeError):
        return None
    return [{"from": leg["from"], "to": leg["to"], "price_eur": float(itinerary["price"]), "date": leg["departureTime"][:10],
             "time": leg["departureTime"][11:16], "airline": leg["segments"][0]["carrierName"], "stops": leg["stops"], "source": "Kiwi"}
            for itinerary in itineraries for leg in [itinerary["outbound"]]]

async def kiwi_destinations(airport: str, country: str, first_day: str, last_day: str) -> list[dict[str, Any]] | None:
    """Kiwi's cheapest flights, of every airline, from an airport to a country (its two-letter code) between two days.
    None when Kiwi did not answer. kiwi.connect_kiwi must have been called."""
    if kiwi.kiwi_search is None:
        return None
    as_kiwi = lambda day: f"{day[8:10]}/{day[5:7]}/{day[0:4]}"
    with logged("Kiwi", f"{airport} -> {country}, {first_day} to {last_day}") as entry:
        result = await kiwi.kiwi_search.ainvoke({"flyFrom": airport, "flyTo": country, "departureDate": as_kiwi(first_day),
                                                 "departureDateTo": as_kiwi(last_day), "sort": "price"})
        text = result if isinstance(result, str) else "".join(block.get("text", "") for block in result if isinstance(block, dict))
        found = kiwi_flights(text)
        entry["result"] = text[:200] if found is None else f"ok: {len(found)} flights"
    return found
