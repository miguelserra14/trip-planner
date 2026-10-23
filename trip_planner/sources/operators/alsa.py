"""ALSA, the largest coach company of Spain."""
import json
import re
import threading
from typing import Any

from primp import Client

from trip_planner.sources.operators.base import NotRunning, TIMEOUT, Trip

# ALSA, the largest coach company of Spain. Its timetable is published (in Transitous), its prices only on its site,
# which is behind a protection that refuses plain programs: it is read with a client that looks like a browser, as
# google_flights.py does. A search is kept by the site in the session (the purchase page, then its list of
# journeys), so the searches are made one at a time. The stop ids of the published timetable hold the site's own:
# "es-ALSA-autobuses_0000555500000001" is its stop 5555 (Madrid airport T4).
ALSA = "https://www.alsa.es"
ALSA_STOP = "es-ALSA-autobuses_"
ALSA_SESSION: dict[str, Any] = {}
ALSA_LOCK = threading.Lock()

def alsa_stop(stop_id: str) -> str:
    return str(int(stop_id[len(ALSA_STOP):][:8]))

def alsa_fare(trip: Trip) -> float | None:
    """ALSA: the cheapest fare on sale for the very departure, in Spain. Raises NotRunning when ALSA lists journeys
    between the two stops that day and none at those times."""
    with ALSA_LOCK:
        if not ALSA_SESSION:
            client = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True, follow_redirects=True, timeout=TIMEOUT)
            home = client.get(f"{ALSA}/").text
            ALSA_SESSION.update(client=client, search=re.search(r'purchasePortletSearchJourneysURL"\] = \'([^\']+)\'', home).group(1))
        client = ALSA_SESSION["client"]
        purchase = client.get(ALSA_SESSION["search"], params={
            "originStationId": alsa_stop(trip.start_id), "destinationStationId": alsa_stop(trip.end_id),
            "departureDate": f"{trip.day[8:10]}/{trip.day[5:7]}/{trip.day[0:4]}", "returnDate": "", "seats": "1", "travelType": "OUTWARD",
            "promoCode": "", "youngPromoCode": "", "employee": "false", "alsaspam": "", "passengerType-ADULT": "1"})
        listing = re.search(r'getJourneyListResourceURL"\] = \'([^\']+)\'', purchase.text)
        if not listing:
            ALSA_SESSION.clear()   # the session expired: a new one next time
            return None
        found = json.loads(client.get(listing.group(1), headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}).text)
    journeys = found.get("journeys") or []
    for journey in journeys:
        prices = [fare["price"] for fare in journey.get("fares") or [] if fare.get("price")]
        if journey.get("canBeSold") == "V" and prices and trip.matches(journey["departureTime"], journey["arrivalTime"]):
            return float(min(prices))
    if journeys and not any(trip.matches(journey["departureTime"], journey["arrivalTime"]) for journey in journeys):
        raise NotRunning
    return None
