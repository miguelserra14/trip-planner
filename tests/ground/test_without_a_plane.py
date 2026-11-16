"""The whole trip by train or coach, compared with flying (sources/timetables/all_the_way.py and
itineraries/ground_only.py): read from the timetables and FlixBus, held to the traveller's limits, priced by the
operators and never by a guess for the main vehicles, and compared with the recommended option. No network."""
import asyncio
from datetime import date, datetime, timedelta

import pytest

from trip_planner.agents import steps
from trip_planner.itineraries import ground_only
from trip_planner.report.formatting import read_timeline
from trip_planner.report.summary import ground_lines
from trip_planner.sources.timetables import all_the_way

DAY = (date.today() + timedelta(days=20)).isoformat()

def step(time: str, arrive: str, line: str, start: str, end: str, km: int, rail: bool = True, coach: bool = False) -> dict:
    return {"time": time, "arrive": arrive, "line": line, "agency": "", "from": start, "to": end, "rail": rail, "coach": coach, "km": km}

def trip(leave: int, arrive: int, steps: list, source: str = "timetable", service: str = DAY, **more) -> dict:
    return {"day": DAY, "service_day": service, "source": source, "leave": leave, "arrive": arrive, "minutes": arrive - leave,
            "steps": steps, "urls": [], **more}

TRAINS = [step("05:10", "07:00", "Metro", "Coimbra", "Coimbra-B", 3, rail=False),
          step("07:13", "12:00", "IC 512", "Coimbra-B", "Porto", 110), step("12:30", "18:29", "AVE", "Porto", "Madrid", 500)]
COACH = [step("23:55", "07:10", "FlixBus", "Coimbra", "Madrid Sur", 0, rail=False, coach=True)]

def shown(found: dict | None, recommended: dict | None) -> list:
    """What the report shows: the trips priced in step 2, ranked against the recommended option in step 3."""
    return ground_only.ground_options({"priced": ground_only.price_all(found)} if found else None, recommended)

def flight_option(cost: float, minutes: int) -> dict:
    return {"total_cost": cost, "total_minutes": minutes}

@pytest.fixture
def tickets(monkeypatch):
    """The operators' answers: positions of the steps each one prices, and at what."""
    answers = {}
    monkeypatch.setattr(ground_only.fares, "tickets", lambda steps, day, service: [
        {"positions": positions, "operator": "Renfe", "price_eur": price, "travel_day": True, "tariff": False, "confirms": True}
        for positions, price in answers.get(steps[-1]["line"], [])])
    return answers

# ---------------------------------------------------------------- the traveller's limits
def test_trip_within_limits(settings):
    settings.earliest_leave, settings.latest_leave = "06:00", "20:00"
    assert ground_only.fits(trip(7 * 60, 12 * 60, TRAINS, grade="B"))
    assert not ground_only.fits(trip(5 * 60, 12 * 60, TRAINS, grade="B"))   # too early

def test_next_day_arrival_rules(settings):
    overnight = trip(23 * 60 + 55, 24 * 60 + 7 * 60 + 10, COACH, source="FlixBus", grade="A")
    assert ground_only.fits(overnight)
    settings.latest_arrival = "06:00"
    assert not ground_only.fits(overnight)   # 07:10 on the day it arrives
    settings.latest_arrival, settings.next_day_arrivals = "", False
    assert not ground_only.fits(overnight)

def test_unreliable_trip_excluded(settings):
    settings.min_reliability = "A"
    assert not ground_only.fits(trip(7 * 60, 12 * 60, TRAINS, grade="B"))

# ---------------------------------------------------------------- prices
def test_unpriced_train_no_guess(tickets, settings):
    tickets["AVE"] = [([2], 45.0)]   # Renfe prices the AVE; nobody the IC of CP
    priced = ground_only.priced(trip(5 * 60 + 10, 18 * 60 + 29, TRAINS))
    assert priced["price_eur"] is None and priced["price_kind"] is None

def test_only_metro_estimated(tickets, settings):
    tickets["AVE"] = [([1], 20.0), ([2], 45.0)]
    priced = ground_only.priced(trip(5 * 60 + 10, 18 * 60 + 29, TRAINS))
    assert 65.0 < priced["price_eur"] < 70.0 and priced["price_kind"] == "partly" and priced["confirmed_eur"] == 65.0
    assert priced["grade"] == "A"   # the timetable read is the travel date's

def test_operators_confirm_typical_timetable(tickets, settings):
    week = (date.today() + timedelta(days=6)).isoformat()
    tickets["AVE"] = [([0, 1, 2], 70.0)]
    assert ground_only.priced(trip(5 * 60 + 10, 18 * 60 + 29, TRAINS, service=week))["grade"] == "A"
    tickets["AVE"] = [([1, 2], 65.0)]   # the metro is not sold by anyone: the times stay typical
    assert ground_only.priced(trip(5 * 60 + 10, 18 * 60 + 29, TRAINS, service=week))["grade"] == "B"

def test_flixbus_sale_confirmed(settings):
    priced = ground_only.priced(trip(23 * 60 + 55, 31 * 60 + 10, COACH, source="FlixBus", grade="A", price_eur=28.98))
    assert priced["price_eur"] == 28.98 and priced["confirmed_eur"] == 28.98 and priced["grade"] == "A"

# ---------------------------------------------------------------- choosing and comparing
def test_compared_with_recommended(tickets, settings):
    tickets["AVE"] = [([1], 20.0), ([2], 45.0)]
    found = {"connections": [trip(23 * 60 + 55, 31 * 60 + 10, COACH, source="FlixBus", grade="A", price_eur=29.0),
                             trip(5 * 60 + 10, 18 * 60 + 29, TRAINS)]}
    options = shown(found, flight_option(150.0, 7 * 60))
    assert [option["source"] for option in options] == ["FlixBus", "timetable"]   # 29 EUR and 7h15 first
    coach = options[0]
    assert coach["beats"] and coach["versus_eur"] < 0   # 121 EUR cheaper, 15 minutes slower
    slow = shown(found, flight_option(40.0, 3 * 60))
    assert not any(option["beats"] for option in slow)

def test_same_trains_one_trip(tickets, settings):
    tickets["AVE"] = [([1], 20.0), ([2], 45.0)]
    longer = [step("05:10", "07:05", "Bus 7", "Coimbra", "Coimbra-B", 4, rail=False), *TRAINS[1:]]
    found = {"connections": [trip(5 * 60 + 10, 18 * 60 + 29, TRAINS), trip(5 * 60 + 10, 18 * 60 + 40, longer)]}
    assert len(shown(found, None)) == 1

def test_same_coach_one_trip(tickets, settings):
    """Coimbra to Madrid, 2026-10-27: FlixBus's own answer, and its coach in the timetables with a train at the end."""
    in_timetables = [step("23:55", "07:10", "FlixBus N795", "Coimbra", "Madrid (South Bus Station)", 0, rail=False, coach=True),
                     step("07:13", "07:19", "C5", "Méndez Álvaro", "Embajadores", 3)]
    found = {"connections": [trip(23 * 60 + 55, 31 * 60 + 19, in_timetables),
                             trip(23 * 60 + 55, 31 * 60 + 10, COACH, source="FlixBus", grade="A", price_eur=29.0)]}
    options = shown(found, None)
    assert len(options) == 1 and options[0]["source"] == "FlixBus"

def test_long_bus_not_guessed(tickets, settings):
    """Vigo to Ourense, 90 km by a regional bus of Galicia: not the bus to the station."""
    galicia = [step("12:50", "18:25", "FlixBus 1082", "Coimbra", "Vigo", 220, rail=False, coach=True),
               step("18:45", "20:25", "XG623002", "Vigo", "Ourense", 90, rail=False), step("20:43", "23:07", "AVE", "Ourense", "Madrid", 360)]
    tickets["AVE"] = [([0], 25.0), ([2], 25.0)]
    assert ground_only.priced(trip(12 * 60 + 50, 23 * 60 + 7, galicia))["price_eur"] is None

def test_huge_gap_in_hours(tickets, settings):
    """Antwerp to Coimbra, 2026-10-27: 30 hours by coach against 11 by plane were worth "+21489 EUR"."""
    found = {"connections": [trip(60 + 10, 30 * 60 + 25, COACH, source="FlixBus", grade="A", price_eur=97.0)]}
    text = "\n".join(ground_lines(shown(found, flight_option(151.0, 11 * 60 + 20)), 1))
    assert "EUR" not in text.split("| No plane 1 |")[1].split("\n")[0].split("|")[-2]   # the last column says hours
    assert "17h55 longer" in text and "21489" not in text

def test_faster_unpriced_shown_last(tickets, settings):
    found = {"connections": [trip(23 * 60 + 55, 31 * 60 + 10, COACH, source="FlixBus", grade="A", price_eur=29.0),
                             trip(5 * 60 + 10, 11 * 60, TRAINS)]}   # no ticket for the trains
    options = shown(found, None)
    assert [option["price_eur"] for option in options] == [29.0, None]

def test_nothing_found_shows_nothing(settings):
    assert shown(None, flight_option(100.0, 300)) == [] and ground_lines([], 1) == []

# ---------------------------------------------------------------- the report
def test_report_shows_each_trip(tickets, settings):
    found = {"connections": [trip(23 * 60 + 55, 31 * 60 + 10, COACH, source="FlixBus", grade="A", price_eur=29.0)]}
    options = shown(found, flight_option(150.0, 7 * 60))
    lines = ground_lines(options, 2)
    text = "\n".join(lines)
    assert "No plane 1" in text and "better than the recommended option 2" in text and "07:10 (" in text
    points, parts = read_timeline(next(line for line in lines if read_timeline(line)))
    assert points[0][0] == "23:55" and points[-1][0] == "07:10" and [grade for grade, _ in parts] == ["A"]

def test_unpriced_report_says_check(tickets, settings):
    lines = "\n".join(ground_lines(shown({"connections": [trip(5 * 60 + 10, 18 * 60 + 29, TRAINS)]}, None), 0))
    assert "no price found" in lines and "look their price up" in lines

# ---------------------------------------------------------------- reading the sources
def test_connection_keeps_vehicles():
    leg = lambda mode, start, end, at, to: {"mode": mode, "startTime": start, "endTime": end, "displayName": mode, "agencyName": "",
                                           "from": {"name": at, "lat": 40.2, "lon": -8.4}, "to": {"name": to, "lat": 40.4, "lon": -3.7}}
    itinerary = {"legs": [leg("WALK", "2026-11-16T05:00:00Z", "2026-11-16T05:10:00Z", "a", "b"),
                          leg("COACH", "2026-11-16T23:55:00Z", "2026-11-17T06:10:00Z", "Coimbra", "Madrid")]}
    found = all_the_way.connection(DAY, DAY, itinerary, ("Europe/Lisbon", "Europe/Madrid"))
    assert found["minutes"] == 6 * 60 + 15 and len(found["steps"]) == 1
    assert found["arrive"] - found["leave"] == 7 * 60 + 15   # the clocks of Lisbon and Madrid differ by an hour

class Answering:
    """The timetables: each answer has PAGE_SIZE connections from the time asked, spread over a number of hours."""
    def __init__(self, hours: float):
        self.hours, self.asked = hours, []

    async def get(self, path, params, later=False):
        assert later   # the flights' connections go first
        self.asked.append(params["time"])
        start = datetime.fromisoformat(params["time"].replace("Z", "+00:00"))
        moments = [start + timedelta(hours=self.hours * number / (all_the_way.PAGE_SIZE - 1)) for number in range(all_the_way.PAGE_SIZE)]
        text = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        legs = lambda moment: [{"mode": "RAIL", "startTime": text(moment), "endTime": text(moment + timedelta(hours=5)), "displayName": "IC",
                                "agencyName": "", "from": {"name": "a", "lat": 40.2, "lon": -8.4}, "to": {"name": "b", "lat": 40.4, "lon": -3.7}}]
        return {"itineraries": [{"startTime": text(moment), "legs": legs(moment)} for moment in moments]}

def test_windows_follow_last_connection(settings):
    def asked(hours: float) -> int:
        answering = Answering(hours)
        assert asyncio.run(all_the_way.published(answering, (40.2, -8.4), (40.4, -3.7), DAY, ("UTC", "UTC")))
        return len(answering.asked)
    windows = len(range(0, 24 * 60, all_the_way.WINDOW_HOURS * 60))
    assert asked(10) == 3          # each answer reaches ten hours further: three requests cover the day
    assert asked(1) == windows     # an answer of one hour: still one request every WINDOW_HOURS, as many as before

def test_timetable_plane_ignored():
    leg = {"mode": "AIRPLANE", "startTime": "2026-11-16T05:00:00Z", "endTime": "2026-11-16T06:00:00Z", "from": {}, "to": {}}
    assert all_the_way.connection(DAY, DAY, {"legs": [leg]}, ("UTC", "UTC")) is None

def test_flixbus_city_with_change(monkeypatch):
    from trip_planner.trip.places import Place
    monkeypatch.setattr(all_the_way.flixbus, "flixbus_city", lambda name, latitude, longitude, km: name)
    ride = {"status": "available", "price": {"total": 27.99, "total_with_platform_fee": 28.98},
            "departure": {"date": f"{DAY}T23:55:00+00:00", "station_id": "c"}, "arrival": {"date": f"{date.fromisoformat(DAY) + timedelta(days=1)}T07:10:00+01:00", "station_id": "m"},
            "legs": [{"departure": {"date": f"{DAY}T23:55:00+00:00", "station_id": "c"}, "arrival": {"date": f"{date.fromisoformat(DAY) + timedelta(days=1)}T03:00:00+01:00", "station_id": "s"}},
                     {"departure": {"date": f"{date.fromisoformat(DAY) + timedelta(days=1)}T03:30:00+01:00", "station_id": "s"}, "arrival": {"date": f"{date.fromisoformat(DAY) + timedelta(days=1)}T07:10:00+01:00", "station_id": "m"}}]}
    monkeypatch.setattr(all_the_way.flixbus, "flixbus_search", lambda start, end, day, by: ([ride], {"c": "Coimbra", "s": "Salamanca", "m": "Madrid Sur"}))
    place = lambda name: Place(name, name, name, "", 0.0, 0.0)
    found = all_the_way.sold_by_flixbus(place("Coimbra"), place("Madrid"), DAY)
    assert len(found) == 1 and found[0]["price_eur"] == 28.98 and found[0]["minutes"] == 6 * 60 + 15
    assert [one["to"] for one in found[0]["steps"]] == ["Salamanca", "Madrid Sur"] and found[0]["arrive"] == 24 * 60 + 7 * 60 + 10

# ---------------------------------------------------------------- a trip asked for without a plane
def test_ground_only_skips_flights(settings):
    settings.ground_only = True
    assert steps.steps_of(settings) == steps.GROUND_STEPS
    assert steps.search_flights not in steps.GROUND_STEPS and steps.search_ground_transport not in steps.GROUND_STEPS

def test_ground_only_ranked_list(tickets, settings):
    tickets["AVE"] = [([1, 2], 60.0)]
    found = steps.rank_by_ground_only({"all_the_way": {"priced": ground_only.price_all({"connections": [trip(5 * 60 + 10, 18 * 60 + 29, TRAINS)]})}})
    assert found["recommended"] == 0 and found["shown"] == [] and len(found["ground_only"]) == 1
    price = found["ground_only"][0]["price_eur"]
    assert price >= 60 and f"{price:.0f} EUR" in found["report"] and "Without a plane" in found["report"]

def test_ground_only_nothing_found(settings):
    found = steps.rank_by_ground_only({"all_the_way": {}})
    assert found["ground_only"] == [] and "No trip by train or coach" in found["report"]

def test_flights_shown_before_ground(tickets, settings):
    from tests.report.test_options import itinerary
    from trip_planner.report.summary import render_closest, render_report
    flying = [itinerary(120, 600), itinerary(150, 420)]
    ground = shown({"connections": [trip(23 * 60 + 55, 31 * 60 + 10, COACH, source="FlixBus", grade="A", price_eur=29.0)]},
                   flight_option(150.0, 7 * 60))
    for text in (render_report(flying, flying[1], [], ground), render_closest([{**option, "breaches": []} for option in flying], [], ground)):
        last_flight = max(text.index(f"### {word}") for word in ("Option 1", "Option 2") if f"### {word}" in text)
        assert text.index("Without a plane") > last_flight

def test_no_flights_ground_searched(tickets, settings, monkeypatch):
    tickets["AVE"] = [([1, 2], 60.0)]

    async def whole_way(days, transit):
        return {"connections": [trip(5 * 60 + 10, 18 * 60 + 29, TRAINS)]}
    monkeypatch.setattr(steps, "all_the_way", whole_way)
    from types import SimpleNamespace
    from trip_planner import current
    monkeypatch.setattr(current, "TRIP", SimpleNamespace(departure=[{"code": "OPO"}], arrival=[{"code": "BRU"}]))

    async def no_flights():
        return None

    async def why():
        return "## No flights fit", []
    monkeypatch.setattr(steps.flight_search, "search_all_flights", no_flights)
    monkeypatch.setattr(steps.flight_search, "why_no_flights", why)
    state = asyncio.run(steps.search_flights({}))
    assert state["flights"] == [] and state["step"] == 1   # the next step still runs
    state.update(asyncio.run(steps.search_ground_transport(state)))
    assert state["checked"] == [] and len(state["all_the_way"]["priced"]) == 1
    found = steps.build_itineraries(state)
    assert found["recommended"] == 0 and len(found["ground_only"]) == 1
    assert found["report"].startswith("## No flights fit") and "Without a plane" in found["report"]
