"""The airlines' own sites: where to book, and the confirmed price where it can be read by code."""
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from trip_planner.config import USER_AGENT
from trip_planner.sources import web
from trip_planner.utils import plain

# Kiwi is a reseller. The traveller books on the airline's own site, so the report links there.
# The site of an airline is looked up the first time it is needed, for any airline in the world.
# Add an entry here (IATA airline code -> URL) only to correct a wrong lookup.
AIRLINE_SITES: dict[str, str] = {}
GENERIC_AIRLINE_WORDS = {"air", "airline", "airlines", "airways", "lines", "linhas", "aereas", "international"}
# Subdomains of an airline's site where no flight is sold: the main site is used instead
NOT_THE_BOOKING_SITE = {"press", "news", "newsroom", "media", "blog", "corporate", "careers", "jobs", "investors", "ir", "about"}

def wikidata_site(carrier: str, airline: str) -> str:
    """The official website recorded on Wikidata for an IATA airline code. Empty if not found or not available."""
    response = httpx.get(
        "https://query.wikidata.org/sparql",
        params={"query": (
            'SELECT ?label ?site ?closed WHERE { ?airline wdt:P229 "%s"; wdt:P856 ?site; rdfs:label ?label. '
            'FILTER(LANG(?label) = "en") OPTIONAL { ?airline wdt:P576 ?closed } }' % carrier
        )},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=20,
    )
    response.raise_for_status()
    # IATA codes are reused: drop airlines that closed, then prefer the one whose name matches Kiwi's
    rows = [row for row in response.json()["results"]["bindings"] if "closed" not in row]
    rows.sort(key=lambda row: plain(row["label"]["value"]) not in plain(airline) and plain(airline) not in plain(row["label"]["value"]))
    return rows[0]["site"]["value"] if rows else ""

def searched_site(airline: str) -> str:
    """The airline's own website found by web search: the result whose domain carries the airline's name."""
    words = re.findall(r"[a-z0-9]+", plain(airline))
    full_name = "".join(words)                                         # "brusselsairlines"
    names = [word for word in words if len(word) >= 3 and word not in GENERIC_AIRLINE_WORDS]
    names.append("".join(word[0] for word in words))                   # initials, such as "ana"
    best, best_score = "", 0
    for query in (f"{airline} official site", f"{airline} airline official website book flights"):
        for result in web.tavily_client.search(query, max_results=5)["results"]:
            domain = urlparse(result["url"]).netloc.lower()
            if domain.split(".")[0] in NOT_THE_BOOKING_SITE:
                domain = domain.split(".", 1)[1]   # press.brusselsairlines.com -> brusselsairlines.com
            compact = domain.replace("-", "")
            # The whole name in the domain is a sure match; one word of it may also be an airport or a reseller
            score = 2 if full_name in compact else 1 if any(len(name) >= 3 and name in compact for name in names) else 0
            if score > best_score:
                best, best_score = f"https://{domain}", score
        if best_score == 2:
            break
    return best

def airline_site(carrier: str, airline: str) -> str:
    """The airline's website. Tries Wikidata, then a web search, and falls back to a search link.
    It is a lookup, not a confirmation: the report says so next to the link."""
    if carrier not in AIRLINE_SITES:
        site = ""
        for lookup in (lambda: wikidata_site(carrier, airline), lambda: searched_site(airline)):
            try:
                site = web.secure_url(lookup())
            except Exception:
                site = ""   # Wikidata often refuses automatic requests, the next lookup takes over
            if site:
                break
        AIRLINE_SITES[carrier] = site or f"https://www.google.com/search?q={quote_plus(airline + ' official site')}"
    return AIRLINE_SITES[carrier]

def ryanair_fare(flight: dict[str, Any]) -> dict[str, Any] | None:
    """The price of a flight on Ryanair's own site, or None if the flight is not found there."""
    response = httpx.get(
        "https://www.ryanair.com/api/farfnd/v4/oneWayFares",
        params={
            "departureAirportIataCode": flight["departure_airport"],
            "arrivalAirportIataCode": flight["arrival_airport"],
            "outboundDepartureDateFrom": flight["date"],
            "outboundDepartureDateTo": flight["date"],
            "outboundDepartureTimeFrom": flight["departure_time"],
            "outboundDepartureTimeTo": flight["departure_time"],
            "currency": "EUR",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    for fare in response.json()["fares"]:
        outbound = fare["outbound"]
        if outbound["flightNumber"] == flight["flight_number"] and outbound["departureDate"][11:16] == flight["departure_time"]:
            return {
                "price_eur": float(outbound["price"]["value"]),
                "url": (
                    "https://www.ryanair.com/pt/pt/trip/flights/select?adults=1&teens=0&children=0&infants=0"
                    f"&dateOut={flight['date']}&isConnectedFlight=false&isReturn=false&discount=0"
                    f"&originIata={flight['departure_airport']}&destinationIata={flight['arrival_airport']}"
                ),
            }
    return None

# Airlines whose own price can be read by code, by IATA code. Most airlines hide their fares behind a booking
# engine, so for them the Kiwi price is used and graded as a reseller's price. To support another airline,
# write a function like ryanair_fare, returning {"price_eur", "url"} or None, and add it here.
PRICE_CHECKERS = {
    "FR": ryanair_fare,
}

def verify_with_airline(flight: dict[str, Any]) -> dict[str, Any]:
    """Check a flight on the airline's own site.
    Returns {"verified", "price_eur", "url"}. url is empty unless the airline confirmed the flight. Never raises."""
    booking = {"verified": False, "price_eur": flight["price_eur"], "url": ""}
    checker = PRICE_CHECKERS.get(flight.get("carrier", ""))
    if checker is None or flight["stops"] != 0:
        return booking
    try:
        fare = checker(flight)
    except Exception:
        fare = None
    if fare is None:
        return booking
    return {"verified": True, "price_eur": fare["price_eur"], "url": fare["url"]}
