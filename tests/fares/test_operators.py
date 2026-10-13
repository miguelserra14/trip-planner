"""The operators' prices on real connections: one route per operator that can be read (fares.FARE_SOURCES).

With --live the route is looked up in the timetables, its best connection is priced by the operators, and both
are saved as a checkpoint. Without it the saved checkpoint is checked: the operator expected sold the tickets,
at a believable price, and today's code makes the same total from them."""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from trip_planner import current
from trip_planner.itineraries import legs
from trip_planner.places import airport_from, as_place, search_places
from trip_planner.sources import fares, timetables

CHECKPOINTS = Path(__file__).parent / "checkpoints"
DAYS_AHEAD = 16   # close enough for every operator to sell the travel date
# (airport, place, the operator that must price it, whether every vehicle of the connection is its own)
ROUTES = [
    ("VIE", "Linz", "ÖBB"),
    ("FCO", "Roma", "Trenitalia"),
    ("BGY", "Pavia", "Trenitalia"),            # Trenord's train, sold by Trenitalia, after a FlixBus and two metros
    ("BUD", "Szeged", "MÁV"),
    ("LUX", "Vianden, Luxembourg", "Luxembourg (free public transport)"),
    ("PRG", "Brno", "RegioJet"),
    ("OPO", "Braganca, Portugal", "FlixBus"),
    ("VCE", "Cortina d'Ampezzo, Italy", "FlixBus"),
]

async def record_route(code: str, city: str, transit: timetables.Timetables) -> dict:
    day = (date.today() + timedelta(days=DAYS_AHEAD)).isoformat()
    flight = {"id": code, "date": day, "arrival_airport": code, "arrival_time": "11:00"}
    place = as_place(city, await asyncio.to_thread(search_places, city))
    await asyncio.sleep(1.1)   # Nominatim asks for one request a second at most
    airport = airport_from(place, code)
    terminal = (await asyncio.to_thread(timetables.find_terminals, [airport])).get(code)
    spot = tuple(terminal) if terminal else (airport["latitude"], airport["longitude"])
    end = (place.latitude, place.longitude)
    zones = (await transit.zone(*spot), await transit.zone(*end))
    answer = await transit.from_airport(spot, end, flight, zones, terminal is not None, await transit.airport_stops(airport, terminal))
    leg = legs.from_airport_leg(flight, {code: {"from_airport": answer}}, None)
    estimate = leg["price_eur"]
    await asyncio.to_thread(legs.price_with_tickets, leg, flight["date"], False)
    return {"recorded": date.today().isoformat(), "flight": flight, "km": airport["km"], "estimate_eur": estimate,
            "leg": {key: leg.get(key) for key in ("kind", "service_day", "steps", "tickets", "price_eur", "price_kind", "confirmed_eur")}}

@pytest.fixture(scope="module", params=ROUTES, ids=lambda route: f"{route[0]}-{route[1].split(',')[0].replace(' ', '_')}-{route[2].split(' ')[0]}")
def priced(request, live, transit):
    code, city, operator = request.param
    path = CHECKPOINTS / f"{code}-{city.split(',')[0].replace(' ', '_')}.json"
    if live:
        loop, client = transit
        found = loop.run_until_complete(record_route(code, city, client))
        CHECKPOINTS.mkdir(exist_ok=True)
        path.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    elif not path.exists():
        pytest.skip("no checkpoint saved: run pytest --live")
    else:
        found = json.loads(path.read_text(encoding="utf-8"))
    return operator, found

def test_the_operator_sold_a_ticket(priced):
    operator, found = priced
    tickets = found["leg"].get("tickets") or []
    assert operator in [ticket["operator"] for ticket in tickets], f"no ticket from {operator}: {tickets}"
    assert all(ticket["travel_day"] for ticket in tickets), "sold for another day, this close to the travel date"

def test_the_price_is_believable(priced):
    """Not below zero, and not above what the distance could ever cost: a price in another currency would be."""
    operator, found = priced
    for ticket in found["leg"]["tickets"]:
        km = sum(found["leg"]["steps"][position]["km"] for position in ticket["positions"])
        assert 0 <= ticket["price_eur"] <= 3 + km * legs.ROAD_FACTOR * legs.MAX_PLAUSIBLE_EUR_PER_KM, ticket
        if not operator.startswith("Luxembourg"):
            assert ticket["price_eur"] > 0 or ticket["operator"].startswith("Luxembourg")

def test_the_same_total_from_the_same_tickets(priced, monkeypatch):
    """Today's code, given the tickets that were read, makes the total and the kind of price that were saved."""
    operator, found = priced
    saved = found["leg"]
    monkeypatch.setattr(fares, "tickets", lambda steps, travel_day, service_day, skip=None: saved["tickets"])
    leg = {"kind": "timetable", "steps": saved["steps"], "service_day": saved["service_day"], "price_kind": "estimate", "price_eur": found["estimate_eur"]}
    legs.price_with_tickets(leg, found["flight"]["date"], False)
    assert leg["price_eur"] == pytest.approx(saved["price_eur"]) and leg["price_kind"] == saved["price_kind"]
    assert leg["confirmed_eur"] <= leg["price_eur"]
