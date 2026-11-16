"""Ground connections that cross midnight. No network.

After a late landing the last trains reach the destination in the first hours of the next day: that is an arrival
like any other, later than every time of the day before. Before an early flight, the last connection of the evening
before is not what the traveller asked for (it is another day), so it only shows among the closest alternatives."""
import asyncio
from datetime import timedelta

import pytest

from tests.flights.test_next_day import accept, connection, found_by_search, LEAVES, trip   # noqa: F401 (trip is the fixture)
from trip_planner import current
from trip_planner.itineraries.ranking import check_itinerary
from trip_planner.report.formatting import arriving, leaving
from trip_planner.sources import flight_search
from trip_planner.sources.timetables import Timetables

DAY, EVE, NEXT = LEAVES.isoformat(), (LEAVES - timedelta(days=1)).isoformat(), (LEAVES + timedelta(days=1)).isoformat()
MIDNIGHT = 24 * 60

def itinerary_of(departure: str, arrival: str, to_airport: tuple, from_airport: tuple, closest: bool = False) -> dict:
    flight = flight_search.register_flight(found_by_search(departure, arrival, 610), closest=closest)
    ground = {flight["id"]: {"to_airport": connection(*to_airport, DAY), "from_airport": connection(*from_airport, DAY)}}
    return check_itinerary(flight, ground, None, None, closest)

def test_after_midnight_next_day():
    itinerary = itinerary_of("14:00", "21:10", (11 * 60, 12 * 60), (22 * 60 + 30, MIDNIGHT + 20))
    assert itinerary["breaches"] == [] and itinerary["arrival_time"] == "00:20"
    assert itinerary["arrival_date"] == NEXT and arriving(itinerary) == f"00:20 ({LEAVES + timedelta(days=1):%d/%m})"
    assert itinerary["leave_date"] == DAY and leaving(itinerary) == "11:00"
    assert itinerary["total_minutes"] == MIDNIGHT + 20 + 180 - 11 * 60   # Rio's clock is three hours behind Lisbon's

def test_after_midnight_is_later():
    accept(latest_arrival="23:30")
    with pytest.raises(ValueError):
        itinerary_of("14:00", "21:10", (11 * 60, 12 * 60), (22 * 60 + 30, MIDNIGHT + 20))
    itinerary = itinerary_of("14:00", "21:10", (11 * 60, 12 * 60), (22 * 60 + 30, MIDNIGHT + 20), closest=True)
    assert [(breach["kind"], breach["amount"]) for breach in itinerary["breaches"]] == [("arrival", 50)]

def test_midnight_needs_next_day():
    accept(next_day_arrivals=False)
    with pytest.raises(ValueError):
        itinerary_of("14:00", "21:10", (11 * 60, 12 * 60), (22 * 60 + 30, MIDNIGHT + 20))

def test_evening_before_only_closest():
    evening = (-30, 45)   # leaves at 23:30 the day before, at the terminal at 00:45
    with pytest.raises(ValueError):
        itinerary_of("07:00", "14:10", evening, (15 * 60, 16 * 60))
    itinerary = itinerary_of("07:00", "14:10", evening, (15 * 60, 16 * 60), closest=True)
    assert [(breach["kind"], breach["side"], breach["amount"]) for breach in itinerary["breaches"]] == [("leave", "before", 30)]
    assert itinerary["leave_time"] == "23:30" and itinerary["leave_date"] == EVE
    assert leaving(itinerary) == f"23:30 ({LEAVES - timedelta(days=1):%d/%m})"
    assert itinerary["airport_wait"] == 7 * 60 - 45
    assert itinerary["total_minutes"] == 16 * 60 + 180 + 30

def planned(start: str, end: str) -> dict:
    """What the planner answers: one bus between two UTC moments."""
    place = {"name": "a", "lat": 0.0, "lon": 0.0, "tz": "UTC"}
    leg = {"mode": "BUS", "startTime": start, "endTime": end, "from": place, "to": place, "routeShortName": "1"}
    return {"itineraries": [{"startTime": start, "endTime": end, "legs": [leg]}]}

def read(answer: dict, to_airport: bool) -> list[tuple]:
    """The connections the program reads from an answer of the planner, as (leave, arrive)."""
    async def work() -> list[tuple]:
        client = Timetables()

        async def get(path, params):
            return answer
        client.get = get
        found = await client.plan((0.0, 0.0), (1.0, 1.0), DAY, 12 * 60, arrive_by=False, zones=("UTC", "UTC"), to_airport=to_airport,
                                  terminal_known=True, modes="BUS")
        await client.close()
        return [(connection["leave"], connection["arrive"]) for connection in found]
    return asyncio.run(work())

def test_arrival_may_end_overnight():
    assert read(planned(f"{DAY}T22:40:00Z", f"{NEXT}T00:20:00Z"), to_airport=False) == [(22 * 60 + 40, MIDNIGHT + 20)]
    late = f"{NEXT}T{current.SETTINGS.night_minutes // 60 + 1:02}:00:00Z"
    assert read(planned(f"{DAY}T23:00:00Z", late), to_airport=False) == []       # a night in a station is not a connection
    assert read(planned(f"{NEXT}T00:10:00Z", f"{NEXT}T01:00:00Z"), to_airport=False) == []   # it leaves the next day
    accept(next_day_arrivals=False)
    assert read(planned(f"{DAY}T22:40:00Z", f"{NEXT}T00:20:00Z"), to_airport=False) == []

def test_departure_may_leave_evening():
    assert read(planned(f"{EVE}T23:30:00Z", f"{DAY}T00:45:00Z"), to_airport=True) == [(-30, 45)]
    assert read(planned(f"{EVE}T12:00:00Z", f"{EVE}T13:00:00Z"), to_airport=True) == []      # the day before, not its evening
    assert read(planned(f"{DAY}T22:40:00Z", f"{NEXT}T00:20:00Z"), to_airport=True) == []     # after the day of the flight
