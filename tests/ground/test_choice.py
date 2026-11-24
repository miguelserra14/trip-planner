"""How the traveller chose to travel on the ground: by train or by bus, with at most so many changes, on each side
of the flight (ground_choice.py). No network.

- The timetables are only asked for what the traveller accepts, with the limit of changes.
- Every connection is checked again, whatever its source: a bus in a trip by train, or one change too many, is
  dropped, and the flight is rejected saying why.
- A leg that cannot be checked (a model's estimate) is not used when the traveller chose.
- Without a choice nothing changes."""
import asyncio
import dataclasses
from datetime import date, timedelta

import pytest

from trip_planner import current
from trip_planner.trip import ground_choice
from trip_planner.trip.trip_settings import SettingError, TripSettings
from trip_planner.itineraries.legs import from_airport_leg, Rejected, to_airport_leg
from trip_planner.trip.places import Place, TripPlaces
from trip_planner.sources import timetables

DAY = (date.today() + timedelta(days=70)).isoformat()

def choose(**chosen) -> None:
    current.SETTINGS = dataclasses.replace(current.SETTINGS, **chosen)

def step(mode: str, time: str = "10:00", arrive: str = "11:00", km: int = 30) -> dict:
    return {"time": time, "arrive": arrive, "line": mode, "agency": "", "from": "A", "to": "B", "rail": ground_choice.is_rail(mode),
            "coach": mode == "COACH", "mode": mode, "km": km, "from_id": "", "to_id": "", "from_at": [0, 0], "to_at": [0, 0]}

def connection(leave: int, arrive: int, *modes: str, km: int = 30) -> dict:
    return {"leave": leave, "arrive": arrive, "steps": [step(mode, km=km) for mode in modes], "urls": [], "grade": "B",
            "source": "timetable", "service_day": DAY, "price_eur": None}

# ---------------------------------------------------------------- the settings
def test_choices_default_to_any():
    settings = TripSettings(origin="Antwerp", destination="Conegliano", dates=[f"{date.fromisoformat(DAY):%d/%m/%Y}"])
    assert (settings.to_airport_by, settings.from_airport_by) == ("any", "any")
    assert not ground_choice.restricted(True) and not ground_choice.restricted(False)
    assert TripSettings(origin="Antwerp", destination="Conegliano", dates=settings.dates, to_airport_by="Train").to_airport_by == "train"
    with pytest.raises(SettingError):
        TripSettings(origin="Antwerp", destination="Conegliano", dates=settings.dates, from_airport_by="plane")
    with pytest.raises(SettingError):
        TripSettings(origin="Antwerp", destination="Conegliano", dates=settings.dates, max_changes_to_airport=10)

# ---------------------------------------------------------------- what is accepted
def test_city_transport_always_counts():
    choose(to_airport_by="train", from_airport_by="bus")
    assert ground_choice.accepts(connection(600, 700, "SUBWAY", "REGIONAL_RAIL"), True)
    assert not ground_choice.accepts(connection(600, 700, "BUS", "REGIONAL_RAIL"), True)
    assert ground_choice.accepts(connection(600, 700, "TRAM", "COACH"), False)
    assert not ground_choice.accepts(connection(600, 700, "COACH", "HIGHSPEED_RAIL"), False)

def test_changes_limited_per_side():
    choose(max_changes_to_airport=1)
    assert ground_choice.accepts(connection(600, 700, "RAIL", "BUS"), True)
    assert not ground_choice.accepts(connection(600, 700, "RAIL", "RAIL", "BUS", km=5), True)
    assert ground_choice.accepts(connection(600, 700, "RAIL", "RAIL", "BUS", km=5), False)   # the other side has no limit

def test_third_long_ride_refused():
    assert ground_choice.accepts(connection(600, 700, "RAIL", "COACH"), True)
    assert not ground_choice.accepts(connection(600, 700, "BUS", "BUS", "BUS"), False)
    choose(max_long_rides=ground_choice.NO_CHANGE_LIMIT)
    assert ground_choice.accepts(connection(600, 700, "BUS", "BUS", "BUS"), False)

def test_city_rides_not_counted():
    """The metro to the station and the bus from the airport into its town are not long rides; a "metro" of 40 km,
    as Transitous calls the S32 from Antwerp to Roosendaal, is one."""
    trip = connection(600, 700, "RAIL", "RAIL")
    trip["steps"] = [step("BUS", km=8), *trip["steps"], step("SUBWAY", km=40)]
    assert ground_choice.long_rides(trip) == 3 and not ground_choice.accepts(trip, True)
    trip["steps"][-1]["km"] = 6
    assert ground_choice.long_rides(trip) == 2 and ground_choice.accepts(trip, True)
    unknown = connection(600, 700, "TRAM", "RAIL")
    for ride in unknown["steps"]:
        ride.pop("km")
    assert ground_choice.long_rides(unknown) == 1   # without a distance, by its kind

def test_old_steps_kind_inferred():
    choose(to_airport_by="train")
    old = connection(600, 700, "RAIL")
    old["steps"][0].pop("mode")
    assert ground_choice.accepts(old, True)
    old["steps"][0].update(rail=False, coach=True)
    assert not ground_choice.accepts(old, True)

def test_only_accepted_modes_asked():
    choose(to_airport_by="train", from_airport_by="bus")
    assert ground_choice.allowed_modes(timetables.GROUND_MODES, True) == "RAIL,SUBURBAN,SUBWAY,TRAM,FERRY,FUNICULAR,AERIAL_LIFT,OTHER"
    assert ground_choice.allowed_modes(timetables.GROUND_MODES, False) == "SUBWAY,TRAM,BUS,COACH,FERRY,FUNICULAR,AERIAL_LIFT,OTHER"
    assert ground_choice.allowed_modes("COACH,BUS", True) == ""

# ---------------------------------------------------------------- the question to the timetables
class Asked(timetables.Timetables):
    """Answers with one itinerary by bus and one by train, and keeps what it was asked."""
    def __init__(self):
        self.asked = []

    async def get(self, path, params):
        self.asked.append(params)
        leg = lambda mode, start, end: {"mode": mode, "startTime": f"{DAY}T{start}:00Z", "endTime": f"{DAY}T{end}:00Z",
                                        "from": {"name": "A", "lat": 0.0, "lon": 0.0}, "to": {"name": "B", "lat": 0.1, "lon": 0.1}}
        return {"itineraries": [{"startTime": f"{DAY}T08:00:00Z", "endTime": f"{DAY}T09:00:00Z", "legs": [leg("BUS", "08:00", "09:00")]},
                                {"startTime": f"{DAY}T10:00:00Z", "endTime": f"{DAY}T11:00:00Z", "legs": [leg("REGIONAL_RAIL", "10:00", "11:00")]}]}

def ask(to_airport: bool) -> tuple:
    transit = Asked()
    found = asyncio.run(transit.plan((0.0, 0.0), (0.1, 0.1), DAY, 12 * 60, arrive_by=True, zones=("UTC", "UTC"), to_airport=to_airport,
                                     terminal_known=True))
    return transit.asked[0], found

def test_no_choice_same_question():
    asked, found = ask(True)
    assert asked["transitModes"] == timetables.GROUND_MODES and "maxTransfers" not in asked and len(found) == 2

def test_choice_sent_and_checked():
    choose(to_airport_by="train", max_changes_to_airport=0)
    asked, found = ask(True)
    assert "BUS" not in asked["transitModes"] and asked["maxTransfers"] == 1   # Transitous counts one more (ground_choice.py)
    assert [connection["steps"][0]["mode"] for connection in found] == ["REGIONAL_RAIL"]   # the bus the service gave anyway is dropped

# ---------------------------------------------------------------- the legs
@pytest.fixture
def trip(monkeypatch):
    place = Place("a", "a", "a", "", 0.0, 0.0)
    airport = {"code": "XXX", "name": "XXX", "city": "XXX", "country": "", "km": 30}
    monkeypatch.setattr(current, "TRIP", TripPlaces(place, place, [airport], [], [airport]))

FLIGHT = {"id": "F", "date": DAY, "departure_airport": "XXX", "departure_time": "14:00", "arrival_airport": "XXX", "arrival_time": "09:00"}
TRANSFER = {"airport": "XXX", "reachable": True, "duration_minutes": 40, "price_eur": 5.0, "price_known": True, "description": "a bus",
            "notes": "", "operator_url": "", "found_in_source": True}

def ground(side: str, *options: dict) -> dict:
    return {"F": {side: {"covered": True, "service_day": DAY, "grade": "B", "options": list(options)}}}

def test_bus_rejected_for_train(trip):
    choose(to_airport_by="train")
    with pytest.raises(Rejected, match="by train"):
        to_airport_leg(FLIGHT, ground("to_airport", connection(600, 700, "BUS")), TRANSFER)
    leg = to_airport_leg(FLIGHT, ground("to_airport", connection(600, 700, "BUS"), connection(610, 690, "REGIONAL_RAIL")), TRANSFER)
    assert leg["steps"][0]["mode"] == "REGIONAL_RAIL" and leg["price_kind"] == "estimate"   # the model's price was for its own way

def test_extra_change_rejected(trip):
    choose(max_changes_from_airport=1)
    with pytest.raises(Rejected, match="1"):
        from_airport_leg(FLIGHT, ground("from_airport", connection(600, 700, "BUS", "RAIL", "TRAM")), None)
    assert from_airport_leg(FLIGHT, ground("from_airport", connection(600, 700, "BUS", "RAIL")), None)["arrive"] == 700

def test_choice_excludes_model_estimate(trip):
    choose(to_airport_by="bus")
    with pytest.raises(Rejected, match="estimate"):
        to_airport_leg(FLIGHT, {}, TRANSFER)
    choose(to_airport_by="any")
    assert to_airport_leg(FLIGHT, {}, TRANSFER)["kind"] == "model"   # without a choice, as before

def test_covered_only_another_way(trip):
    """Charleroi airport is reached by train to Charleroi and then a bus: by train only it cannot be reached, and the
    reason says that, not that no timetable covers it."""
    choose(to_airport_by="train")
    excluded = {"F": {"to_airport": {"covered": True, "service_day": DAY, "grade": "B", "options": [], "choice_excluded": True}}}
    with pytest.raises(Rejected, match="but none by train"):
        to_airport_leg(FLIGHT, excluded, TRANSFER)

def test_coverage_asked_any_vehicle():
    choose(to_airport_by="train", max_changes_to_airport=0)
    transit = Asked()
    transit.probes = {}
    assert asyncio.run(transit.is_covered((0.0, 0.0), (0.1, 0.1), DAY, ("UTC", "UTC"), True, True))
    assert transit.asked[0]["transitModes"] == timetables.GROUND_MODES and "maxTransfers" not in transit.asked[0]
