"""An operator's coach that leaves from the city of the airport, joined to a published connection between the
airport and the coach station, on made-up answers: no network. Where nothing is published, the estimated way stays,
measured by road to the station itself (timetables.join_published_ends)."""
import asyncio
from datetime import date, timedelta

from trip_planner.sources import timetables

AIRPORT, STATION = (42.36, 19.25), (42.43, 19.26)   # Podgorica airport and its coach station
DAY = (date.today() + timedelta(days=3)).isoformat()

def coach_ride(leave: str, arrive: str) -> dict:
    """An Obilet ride from the airport: the estimated way to the station, then its coach to Kotor."""
    estimated = {"time": "x", "arrive": leave, "line": "estimated", "from": "TGD", "to": "Podgorica Bus Station", "rail": False,
                 "coach": False, "km": 14, "estimated": True, "minutes": 50}
    coach = {"time": leave, "arrive": arrive, "line": "Royal Travel", "agency": "Royal Travel", "from": "Podgorica Bus Station",
             "to": "Kotor", "rail": False, "coach": True, "km": 80}
    return {"grade": "C", "source": "Obilet", "service_day": DAY, "leave": timetables.to_minutes(leave) - 50, "arrive": timetables.to_minutes(arrive),
            "price_eur": 14.0, "confirmed_eur": 11.0, "estimated_km": 14, "notes": ["estimated"], "steps": [estimated, coach], "urls": []}

def join(published: list, monkeypatch) -> list:
    """The rides from the airport after joining, the planner answering with these (leave, arrive) from the airport."""
    async def station_point(self, name, near):
        return STATION

    async def plan(self, start, end, day, minutes, **options):
        return [{"leave": timetables.to_minutes(leave), "arrive": timetables.to_minutes(arrive),
                 "steps": [{"time": leave, "arrive": arrive, "line": "Ulcinj to Podgorica", "agency": "", "from": "Airport", "to": "Main Bus Station",
                            "rail": False, "coach": False, "km": 12}]} for leave, arrive in published if timetables.to_minutes(leave) >= minutes]
    monkeypatch.setattr(timetables.Timetables, "station_point", station_point)
    monkeypatch.setattr(timetables.Timetables, "plan", plan)
    monkeypatch.setattr(timetables, "by_road", lambda point, hubs: [{**hub, "road_km": 11.0, "road_minutes": 16.0} for hub in hubs])
    monkeypatch.setattr(timetables, "reference_day", lambda day: day)
    rides = [coach_ride("15:45", "17:55"), coach_ride("07:05", "09:10")]
    ground = {"operators": {}, "operators_from": {"TGD": {DAY: rides}}}
    flight = {"id": "F", "date": DAY, "departure_airport": "LIS", "arrival_airport": "TGD", "arrival_time": "12:30", "departure_time": "09:00"}

    async def work() -> None:
        client = timetables.Timetables()
        await timetables.join_published_ends(client, ground, [flight], {"TGD": AIRPORT}, {"TGD": "Europe/Podgorica"}, {"TGD": True},
                                             {"TGD": {"country": "ME"}})
        await client.close()
    asyncio.run(work())
    return ground["operators_from"]["TGD"][DAY]

def test_a_published_bus_to_the_station_and_the_coach_are_one_ride(monkeypatch):
    rides = join([("13:34", "14:05"), ("15:40", "16:10")], monkeypatch)
    joined = [ride for ride in rides if not ride.get("estimated_km")]
    assert len(joined) == 1   # the coach of 07:05 leaves before the flight lands: nothing to join it to
    ride = joined[0]
    assert ride["grade"] == "A" and ride["leave"] == timetables.to_minutes("13:34")   # the bus that leaves last and still makes it
    assert [step["line"] for step in ride["steps"]] == ["Ulcinj to Podgorica", "Royal Travel"]
    assert ride["price_eur"] > 11.0 and not ride["notes"]

def test_with_nothing_published_the_estimate_is_measured_by_road_to_the_station(monkeypatch):
    rides = join([], monkeypatch)
    assert all(ride.get("estimated_km") == 11 for ride in rides) and len(rides) == 2
    stretch = rides[0]["steps"][0]
    assert stretch["minutes"] == 16 + timetables.STATION_WAIT_MINUTES and rides[0]["leave"] == timetables.to_minutes("15:45") - stretch["minutes"]
