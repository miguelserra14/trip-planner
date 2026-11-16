"""A town found as its boundary is placed where the town is, not in the middle of its territory. No network.

Siracusa was found 7 km from the city, among fields: the trip to the airport then started with one of the two
buses a day that stop there, and took 217 minutes instead of under two hours."""
import pytest

from trip_planner.trip import places

def result(category: str, kind: str, latitude: float, longitude: float, name: str = "Siracusa", local: str = "Siracusa") -> dict:
    return {"category": category, "addresstype": kind, "lat": str(latitude), "lon": str(longitude), "name": name, "importance": 0.6,
            "namedetails": {"name": local}, "address": {"country": "Italy", "country_code": "it"}}

BOUNDARY = (37.0315752, 15.2124277)
CITY = (37.0646139, 15.2907196)

@pytest.fixture
def nominatim(monkeypatch):
    """What Nominatim answers to the search by name, then to the search for a city of that name."""
    def answers(by_name: list, as_city: list | None) -> list:
        asked = []
        def ask(question: dict) -> list:
            asked.append(question)
            if "q" in question:
                return by_name
            if as_city is None:
                raise RuntimeError("no answer")
            return as_city
        monkeypatch.setattr(places, "ask_nominatim", ask)
        monkeypatch.setattr(places.time, "sleep", lambda seconds: None)
        return asked
    return answers

def test_boundary_moved_to_town(nominatim):
    asked = nominatim([result("boundary", "city", *BOUNDARY)],
                      [result("boundary", "city", *BOUNDARY), result("place", "city", *CITY, name="Syracuse")])   # named in English
    place = places.as_place("Siracusa", places.search_places("Siracusa"))
    assert (place.latitude, place.longitude) == CITY
    assert asked[1] == {"city": "Siracusa", "countrycodes": "it"}

def test_town_point_not_reasked(nominatim):
    asked = nominatim([result("place", "city", *CITY)], [])
    place = places.as_place("Siracusa", places.search_places("Siracusa"))
    assert (place.latitude, place.longitude) == CITY and len(asked) == 1

def test_boundary_kept_without_better(nominatim):
    for as_city in ([result("boundary", "city", *BOUNDARY)],                       # the town has no point of its own
                    [result("place", "city", *CITY, local="Siracusa Scalo")],     # another place
                    [result("place", "city", 45.0, 9.0)],                         # a town of that name somewhere else
                    None):                                                        # no answer
        nominatim([result("boundary", "city", *BOUNDARY)], as_city)
        place = places.as_place("Siracusa", places.search_places("Siracusa"))
        assert (place.latitude, place.longitude) == BOUNDARY
