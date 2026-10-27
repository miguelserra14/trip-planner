"""The two sources that answer "everywhere from this airport": Ryanair's fare finder and Kiwi asked by country.

The days are a Days: when the traveller may leave, and when they may come back (None for a one-way trip).
Each flight is a leg, plus {"price_eur", "carrier", "source", "url", "back"}. price_eur is the price of the whole
trip, url is where to book it (empty when not known), and back is the return leg, or None for a one-way trip.
A leg is {"from", "to", "date", "time", "arrival_date", "arrival_time", "minutes", "airline", "stops", "segments"}:
the times on the local clock of each airport, minutes from take-off to landing (0 when not known), and segments its flights one by
one, {"from", "to", "from_name", "to_name", "date", "time", "arrival_date", "arrival_time", "flight", "airline"}.
A return can leave from another airport than the one flown to, and land at another of the traveller's airports:
Kiwi combines them when it is cheaper (Birmingham out, Manchester back)."""
import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from trip_planner import current
from trip_planner.sources import kiwi
from trip_planner.texts import REFERENCE, text_in
from trip_planner.utils import logged

RYANAIR = "https://www.ryanair.com/api/farfnd/v4"

MAX_RUNS = 2   # runs of days in a row asked apart, each side: the closest ones are joined beyond it (Days.runs)

def runs_of(days: list[str], most: int = MAX_RUNS) -> list[tuple[str, str]]:
    """Days in a row as (first, last), the two runs with the smallest gap joined until there are at most most."""
    found: list[list[date]] = []
    for day in sorted(date.fromisoformat(text) for text in days):
        if found and (day - found[-1][1]).days <= 1:
            found[-1][1] = day
        else:
            found.append([day, day])
    while len(found) > most:
        gap = min(range(len(found) - 1), key=lambda at: (found[at + 1][0] - found[at][1]).days)
        found[gap:gap + 2] = [[found[gap][0], found[gap + 1][1]]]
    return [(first.isoformat(), last.isoformat()) for first, last in found]

@dataclass(frozen=True)
class Days:
    first: str                 # yyyy-mm-dd: the first and the last day the traveller may leave on
    last: str
    back_first: str | None = None   # the first and the last day the traveller may fly back on. None: one way
    back_last: str | None = None
    out_days: tuple[str, ...] = ()    # the days clicked to leave on, when not every day from first to last
    back_days: tuple[str, ...] = ()   # and to come back on

    @classmethod
    def clicked(cls, out: list[str], back: list[str] | None = None) -> "Days":
        """The days clicked in a calendar, yyyy-mm-dd, kept as they are when they are not all in a row."""
        out, back = sorted(out), sorted(back or [])
        whole = lambda days: len(days) == (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1
        return cls(out[0], out[-1], *((back[0], back[-1]) if back else ()),
                   out_days=() if whole(out) else tuple(out), back_days=() if not back or whole(back) else tuple(back))

    @property
    def round_trip(self) -> bool:
        return self.back_first is not None

    def allows(self, flight: dict[str, Any]) -> bool:
        """Whether a flight leaves, and comes back, on days the traveller chose."""
        back = flight.get("back")
        return ((not self.out_days or flight["date"] in self.out_days)
                and (not back or not self.back_days or back["date"] in self.back_days))

    def runs(self) -> list["Days"]:
        """The days as the sources are asked them: one Days per run of days in a row, each side, a return only
        with a way out that does not start after it. Ryanair and Kiwi give the cheapest flight of each destination
        over the days asked: asked over the gap between two runs, that flight could be on a day not chosen."""
        outs = runs_of(list(self.out_days)) if self.out_days else [(self.first, self.last)]
        if not self.round_trip:
            return [Days(*out) for out in outs]
        backs = runs_of(list(self.back_days)) if self.back_days else [(self.back_first, self.back_last)]
        return [Days(*out, *back) for out in outs for back in backs if back[1] >= out[0]]

    def __str__(self) -> str:
        say = lambda key, **values: text_in(current.SETTINGS.language if current.SETTINGS else REFERENCE, key, **values)
        shown = lambda days, first, last: ", ".join(days) if days else say("discover_days_range", first=first, last=last)
        out = shown(self.out_days, self.first, self.last)
        return out + (say("discover_days_back", days=shown(self.back_days, self.back_first, self.back_last)) if self.round_trip
                      else say("discover_days_one_way"))

def ryanair_leg(flight: dict[str, Any]) -> dict[str, Any]:
    start, end = flight["departureAirport"], flight["arrivalAirport"]
    segment = {"from": start["iataCode"], "to": end["iataCode"], "from_name": start.get("name", ""), "to_name": end.get("name", ""),
               "date": flight["departureDate"][:10], "time": flight["departureDate"][11:16],
               "arrival_date": (flight.get("arrivalDate") or "")[:10], "arrival_time": (flight.get("arrivalDate") or "")[11:16],
               "flight": flight.get("flightNumber", ""), "airline": "Ryanair"}
    return {key: segment[key] for key in ("from", "to", "date", "time", "arrival_date", "arrival_time")} | {
        "minutes": 0,   # not known: the two times are on the clocks of two airports, maybe in two time zones
        "airline": "Ryanair", "stops": 0, "segments": [segment]}

RYANAIR_BOOKING = "https://www.ryanair.com/pt/pt/trip/flights/select?"

def ryanair_url(out: dict[str, Any], back: dict[str, Any] | None) -> str:
    """Ryanair's booking page for the trip, on the days of its flights."""
    asked = {"adults": 1, "teens": 0, "children": 0, "infants": 0, "dateOut": out["date"], "dateIn": back["date"] if back else "",
             "isConnectedFlight": "false", "isReturn": "true" if back else "false", "discount": 0,
             "originIata": out["from"], "destinationIata": out["to"]}
    return RYANAIR_BOOKING + urlencode(asked)

def ryanair_flight(fare: dict[str, Any], airport: str) -> dict[str, Any]:
    """One fare of Ryanair's fare finder, one way or with its return."""
    out, back = fare["outbound"], fare.get("inbound")
    price = float(out["price"]["value"]) + (float(back["price"]["value"]) if back else 0.0)
    out_leg = ryanair_leg({"departureAirport": {"iataCode": airport}, **out})
    back_leg = ryanair_leg(back) if back else None
    return {**out_leg, "price_eur": round(price, 2), "carrier": "FR", "source": "Ryanair", "url": ryanair_url(out_leg, back_leg), "back": back_leg}

def ryanair_destinations(airport: str, days: Days) -> list[dict[str, Any]]:
    """The cheapest Ryanair fare to each of its destinations from an airport, one way or there and back."""
    asked = {"departureAirportIataCode": airport, "outboundDepartureDateFrom": days.first, "outboundDepartureDateTo": days.last,
             "currency": "EUR", "adultPaxCount": 1}
    if days.round_trip:
        asked |= {"inboundDepartureDateFrom": days.back_first, "inboundDepartureDateTo": days.back_last}
    with logged("Ryanair", f"every destination from {airport}, {days}") as entry:
        response = httpx.get(f"{RYANAIR}/{'roundTripFares' if days.round_trip else 'oneWayFares'}", params=asked,
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        found = [ryanair_flight(fare, airport) for fare in response.json().get("fares") or []]
        entry["result"] = f"ok: {len(found)} destinations"
    return found

def kiwi_leg(leg: dict[str, Any]) -> dict[str, Any]:
    segments = [{"from": s["from"], "to": s["to"], "from_name": s.get("fromName") or s.get("fromCity") or "", "to_name": s.get("toName") or s.get("toCity") or "",
                 "date": s["departureTime"][:10], "time": s["departureTime"][11:16], "arrival_date": s["arrivalTime"][:10],
                 "arrival_time": s["arrivalTime"][11:16], "flight": s.get("flightNumber", ""), "airline": s.get("carrierName", "")}
                for s in leg["segments"]]
    return {"from": leg["from"], "to": leg["to"], "date": leg["departureTime"][:10], "time": leg["departureTime"][11:16],
            "arrival_date": leg["arrivalTime"][:10], "arrival_time": leg["arrivalTime"][11:16], "minutes": round(leg.get("durationSeconds", 0) / 60),
            "airline": leg["segments"][0]["carrierName"], "stops": leg["stops"], "segments": segments}

def kiwi_url(url: str) -> str:
    """Kiwi's booking link, kept only when it is one of Kiwi's own pages."""
    parsed = urlparse(url or "")
    return url if parsed.scheme == "https" and (parsed.netloc == "kiwi.com" or parsed.netloc.endswith(".kiwi.com")) else ""

def kiwi_flights(text: str) -> list[dict[str, Any]] | None:
    """The flights of an answer of Kiwi's search, or None when it is not one (Kiwi failed, or refused)."""
    try:
        itineraries = json.loads(text)["itineraries"]
    except (ValueError, KeyError, TypeError):
        return None
    return [{**kiwi_leg(itinerary["outbound"]), "price_eur": float(itinerary["price"]), "carrier": itinerary["outbound"]["segments"][0]["carrier"],
             "source": "Kiwi", "url": kiwi_url(itinerary.get("bookingUrl", "")),
             "back": kiwi_leg(itinerary["inbound"]) if itinerary.get("inbound") else None}
            for itinerary in itineraries]

async def kiwi_destinations(airport: str, country: str, days: Days, max_stops: int = 0, excluded: str = "") -> list[dict[str, Any]]:
    """Kiwi's cheapest flights from an airport to a country (its two-letter code): one per city of that country, at
    most kiwi.RESULT_CAP, with at most max_stops stops each way, of every airline but the excluded ones (IATA codes,
    separated by commas). Raises when Kiwi did not answer, with what it said instead: a refusal (403) must be told
    apart from a failure. kiwi.connect_kiwi must have been called."""
    if kiwi.kiwi_search is None:
        raise RuntimeError("Kiwi is not connected")
    as_kiwi = lambda day: f"{day[8:10]}/{day[5:7]}/{day[0:4]}"
    asked = {"flyFrom": airport, "flyTo": country, "departureDate": as_kiwi(days.first), "departureDateTo": as_kiwi(days.last),
             "sort": "price", "one_for_city": True,   # one per city: the 15 answers are 15 places, not one place 15 times
             "max_sector_stopovers": max_stops}
    if excluded:
        asked["exclude_airlines"] = excluded
    if days.round_trip:
        asked |= {"returnDate": as_kiwi(days.back_first), "returnDateTo": as_kiwi(days.back_last)}
    with logged("Kiwi", f"{airport} -> {country}, {days}") as entry:
        result = await kiwi.kiwi_search.ainvoke(asked)
        text = result if isinstance(result, str) else "".join(block.get("text", "") for block in result if isinstance(block, dict))
        found = kiwi_flights(text)
        if found is None:
            raise ValueError(f"not an answer: {text[:200]}")
        entry["result"] = f"ok: {len(found)} flights"
    return found
