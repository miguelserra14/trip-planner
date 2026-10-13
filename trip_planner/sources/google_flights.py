"""Flights from Google Flights, read with the fast-flights library. Plan B: only asked when Kiwi does not answer
(see flight_search.search). Google has no public service for this: the library reads the page a browser would get,
so it stops working whenever Google changes that page, and Google may refuse it.

One request per departure airport, arrival airport and date. Each flight is returned in the shape Kiwi uses, so
the rest of the program treats both alike."""
import asyncio
import json
from typing import Any

from fast_flights import create_query, FlightQuery
from fast_flights.fetcher import URL
from primp import Client
from selectolax.lexbor import LexborHTMLParser

NAME = "Google Flights"
CONCURRENCY = 3     # requests sent at the same time
# In Europe Google answers with its cookie consent page first. This cookie is the answer "reject all".
CONSENT = {"SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}
requests = 0        # requests sent, for the progress line
semaphore = asyncio.Semaphore(CONCURRENCY)

class FullAnswer(list):
    """The flights of a search answered here: route by route, so nothing was cut at a maximum.
    missing lists the (arrival airport, date) that got no answer."""
    missing: list[tuple]

def moment(day: list[int], time: list[int | None] | None) -> str:
    """A date and a time of the page as yyyy-mm-ddThh:mm. The page leaves zeros out: [16] is 16:00, [None, 35] is 00:35."""
    hour, minute = [*(time or []), None, None][:2]
    return f"{day[0]:04d}-{day[1]:02d}-{day[2]:02d}T{hour or 0:02d}:{minute or 0:02d}"

def read_route(departure: str, arrival: str, day: str, stopovers: int) -> list[dict[str, Any]]:
    """The flights of one route on one day (yyyy-mm-dd), economy, one adult, in EUR. Raises if the page is not the expected one."""
    query = create_query(flights=[FlightQuery(date=day, from_airport=departure, to_airport=arrival, max_stops=stopovers)],
                         trip="one-way", language="en-GB", currency="EUR")
    client = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)
    page = client.get(URL, params=query.params(), cookies=CONSENT).text
    data = LexborHTMLParser(page).css_first(r"script.ds\:1").text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise RuntimeError("Google Flights answered with an error")
    payload = json.loads(data)
    found = []
    for section in (2, 3):   # the flights Google calls best, then the others
        for row in (payload[section] or [None])[0] or []:
            flight, price = row[0], row[1][0][1]
            legs = flight[2]
            if price is None or not legs:
                continue
            found.append({
                "source": NAME,
                "price": price,
                "bookingUrl": query.url(),
                "outbound": {
                    "from": legs[0][3], "to": legs[-1][6],
                    "departureTime": moment(legs[0][20], legs[0][8]), "arrivalTime": moment(legs[-1][21], legs[-1][10]),
                    "stops": len(legs) - 1,
                    "route": [legs[0][3], *(leg[6] for leg in legs)],
                    "durationSeconds": flight[9] * 60,
                    "segments": [{"carrier": leg[22][0], "flightNumber": f"{leg[22][0]}{leg[22][1]}", "carrierName": leg[22][3]} for leg in legs],
                },
            })
    return found

async def flights(departure: str, arrivals: list[str], days: list[str], stopovers: int) -> FullAnswer | None:
    """Every flight from one airport to each arrival airport on each day. None if no route got an answer."""
    global requests

    async def one(arrival: str, day: str) -> list[dict[str, Any]] | None:
        global requests
        async with semaphore:
            requests += 1
            try:
                return await asyncio.to_thread(read_route, departure, arrival, day, stopovers)
            except Exception:
                return None

    wanted = [(arrival, day) for arrival in arrivals for day in days]
    answers = await asyncio.gather(*(one(arrival, day) for arrival, day in wanted))
    if all(answer is None for answer in answers):
        return None
    found = FullAnswer(itinerary for answer in answers for itinerary in answer or [])
    found.missing = [pair for pair, answer in zip(wanted, answers) if answer is None]
    return found
