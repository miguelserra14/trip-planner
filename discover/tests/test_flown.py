"""The routes flown today from the traveller's airports (flown.py), and what the map does with them. No network.

- The table of a Wikipedia article is read by code, the seasonal routes told apart.
- A link becomes an airport by Wikidata, or by the airport of that name.
- A destination is missed only when no answer gave it or its city, and Ryanair's own answer misses nothing.
- How many of the destinations flown the map found is counted.
- The destinations missed are asked of Kiwi once more, by name."""
import asyncio
from datetime import date

from discover import finder
from discover.flights import flown
from discover.flights.sources import Days
from trip_planner.trip.places import Place

ARTICLE = """## Airlines and destinations

The following airlines operate regular scheduled and charter flights at Brussels South Charleroi Airport:[[15]](#cite_note-18)

| Airlines | Destinations |
| --- | --- |
| [Air Corsica](https://en.wikipedia.org/wiki/Air_Corsica "Air Corsica") | **Seasonal:** [Ajaccio](https://en.wikipedia.org/wiki/Ajaccio_Napoleon_Bonaparte_Airport "Ajaccio Napoleon Bonaparte Airport"),[[17]](#cite_note-20) [Bastia](https://en.wikipedia.org/wiki/Bastia_%E2%80%93_Poretta_Airport "Bastia – Poretta Airport") |
| [Wizz Air](https://en.wikipedia.org/wiki/Wizz_Air "Wizz Air") | [Bucharest–Otopeni](https://en.wikipedia.org/wiki/Henri_Coand%C4%83_International_Airport "Henri Coandă International Airport"), [Skopje](https://en.wikipedia.org/wiki/Skopje_International_Airport "Skopje International Airport") **Seasonal:** [Burgas](https://en.wikipedia.org/wiki/Burgas_Airport "Burgas Airport") |

## Statistics
"""

def test_seasonal_routes_told_apart():
    rows = flown.table_rows(ARTICLE)
    assert rows[0] == ("Air Corsica", [("Ajaccio Napoleon Bonaparte Airport", True, "Ajaccio"), ("Bastia – Poretta Airport", True, "Bastia")])
    assert rows[1] == ("Wizz Air", [("Henri Coandă International Airport", False, "Bucharest–Otopeni"),
                                    ("Skopje International Airport", False, "Skopje"), ("Burgas Airport", True, "Burgas")])
    assert flown.table_rows("An article with no table.") == []

def test_link_becomes_airport(monkeypatch):
    monkeypatch.setattr(flown.access, "sparql", lambda query: [{"title": "Henri Coandă International Airport", "iata": "OTP"}])
    monkeypatch.setattr(flown, "load_airports", lambda: {"SKP": {"name": "Skopje International Airport"}, "BOJ": {"name": "Burgas Airport"}})
    found = flown.codes_of(["Henri Coandă International Airport", "Skopje International Airport", "Burgas Airport", "Nowhere Airport"])
    assert found == {"Henri Coandă International Airport": "OTP", "Skopje International Airport": "SKP", "Burgas Airport": "BOJ"}

ROUTES = {"CRL": {"routes": [{"to": "OTP", "airline": "Wizz Air", "seasonal": False}, {"to": "BOJ", "airline": "Wizz Air", "seasonal": True},
                             {"to": "BGY", "airline": "Ryanair", "seasonal": False}, {"to": "LGW", "airline": "Wizz Air", "seasonal": False},
                             {"to": "EIN", "airline": "TUI", "seasonal": False}], "checked": "", "article": ""},
          "EIN": {"routes": [{"to": "OTP", "airline": "Wizz Air", "seasonal": False}], "checked": "", "article": ""}}
AIRPORTS = {code: {"code": code, "country": country, "city": city, "latitude": latitude, "longitude": longitude}
            for code, country, city, latitude, longitude in
            (("OTP", "RO", "Bucharest", 44.57, 26.08), ("BOJ", "BG", "Burgas", 42.57, 27.51), ("BGY", "IT", "Bergamo", 45.67, 9.7),
             ("LGW", "GB", "London", 51.15, -0.19), ("STN", "GB", "London, Essex", 51.88, 0.24), ("EIN", "NL", "Eindhoven", 51.45, 5.37),
             ("CRL", "BE", "Charleroi", 50.46, 4.45))}

def test_missed_only_if_absent():
    seen = flown.seen_codes(ROUTES, {"STN"}, AIRPORTS)   # London by Stansted
    assert seen == {"LGW"}
    gaps = flown.missing(ROUTES, seen, ryanair={"CRL"})
    assert sorted(gaps) == ["BOJ", "OTP"]   # Bergamo is Ryanair's, which answered; Eindhoven is the traveller's own airport
    assert [route["from"] for route in gaps["OTP"]] == ["CRL", "EIN"]

def test_flown_destinations_counted():
    found = flown.coverage(ROUTES, flown.seen_codes(ROUTES, {"STN", "OTP"}, AIRPORTS), ryanair={"CRL"})
    assert (found["flown"], found["found"], found["ryanair_no_fare"], found["missing"], found["missing_seasonal"]) == (4, 2, 1, ["BOJ"], ["BOJ"])

def test_missed_reasked_by_name(monkeypatch, tmp_path):
    monkeypatch.setattr(finder, "KIWI_COUNT", tmp_path / "kiwi.json")
    monkeypatch.setattr(finder, "KIWI_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(finder, "airports_near", lambda place, km, airlines_only: [{"code": "CRL", "km": 50, "country": "BE"},
                                                                                   {"code": "EIN", "km": 80, "country": "NL"}])
    monkeypatch.setattr(finder, "route_counts", lambda: {})
    monkeypatch.setattr(finder.flown, "routes", lambda codes, today, say=print, extract=None: {code: ROUTES[code] for code in codes})
    monkeypatch.setattr(finder, "load_airports", lambda: AIRPORTS)
    monkeypatch.setattr(finder, "countries_near", lambda place, departures: ["IT"])
    monkeypatch.setattr(finder, "ryanair_destinations", lambda code, days: [])
    monkeypatch.setattr(finder.access, "leg", lambda *arguments, **more: {"airport": arguments[0], "to_airport": arguments[1], "place": "",
                                                                         "price_eur": 5.0, "minutes": 30, "grade": "C", "price_grade": "C",
                                                                         "how": "estimated", "looked_up": False})
    monkeypatch.setattr(finder.access, "centres", lambda codes, airports, today, say: {})
    monkeypatch.setattr(finder.transfers, "look_up", lambda *arguments: {})
    monkeypatch.setattr(finder.operator_rides, "look_up", lambda *arguments: {})

    async def connect() -> None:
        pass
    sent = []

    async def kiwi(codes: str, where: str, days: Days, stops: int, excluded: str) -> list:
        sent.append((codes, where))
        return [{"from": "EIN", "to": "OTP", "price_eur": 30.0, "date": "2026-11-12", "time": "07:00", "airline": "Wizz Air", "carrier": "W6",
                 "stops": 0, "source": "Kiwi", "back": None}] if where != "IT" else []
    monkeypatch.setattr(finder, "kiwi_destinations", kiwi)
    monkeypatch.setattr(finder.kiwi, "connect_kiwi", connect)
    monkeypatch.setattr(finder.kiwi, "kiwi_search", object())
    found = asyncio.run(finder.discover(Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.22, 4.40), Days("2026-11-12", "2026-11-19")))
    assert sent[0] == ("CRL,EIN", "IT") and sent[1][0] == "CRL,EIN" and set(sent[1][1].split(",")) == {"OTP", "BOJ", "LGW"}
    assert found.coverage["found"] == 1 and "OTP" not in found.coverage["missing"]   # found by asking it by name

def test_missing_listed_with_airlines():
    from discover.view import breakdown
    seen = flown.seen_codes(ROUTES, {"STN", "OTP"}, AIRPORTS)
    found = finder.Found(Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.22, 4.40), Days("2026-11-12", "2026-11-19"), [], [], [], [],
                         flown=ROUTES, coverage=flown.coverage(ROUTES, seen, {"CRL"}, asked={"BOJ"}))
    part = breakdown.coverage_section(found, AIRPORTS)
    assert part["rows"] == [["BOJ", "Burgas", "BG", "Wizz Air", "CRL", "seasonal", "yes"]]
    assert part["note"].startswith("2 of the 4 destinations flown from CRL, EIN")

def test_link_matched_by_name():
    airports = {"BCN": {"name": "Josep Tarradellas Barcelona-El Prat Airport", "city": "Barcelona", "large": True, "latitude": 41.3, "longitude": 2.08},
                "BLA": {"name": "General José Antonio Anzoategui International Airport", "city": "Barcelona", "large": True, "latitude": 10.1, "longitude": -64.7},
                "IST": {"name": "İstanbul Airport", "city": "Istanbul", "large": True, "latitude": 41.26, "longitude": 28.74},
                "SAW": {"name": "Sabiha Gökçen International Airport", "city": "Pendik, Istanbul", "large": True, "latitude": 40.9, "longitude": 29.31}}
    cities = {}
    for code, airport in airports.items():
        cities.setdefault(flown.plain(flown.access.city_name(airport)), []).append(code)
    eindhoven = (51.45, 5.37)
    assert flown.by_text("Barcelona", "Barcelona–El Prat Airport", airports, cities, eindhoven) == "BCN"   # not Venezuela's
    assert flown.by_text("Istanbul–Sabiha Gökçen", "Istanbul–Sabiha Gökçen", airports, cities, eindhoven) == "SAW"
    assert flown.by_text("Nowhere", "Nowhere Field", airports, cities, eindhoven) is None

def test_routes_within_reach():
    airports = {"BCN": {"latitude": 41.3, "longitude": 2.08}, "BKK": {"latitude": 13.69, "longitude": 100.75}}
    routes = {"AMS": {"routes": [{"to": "BCN", "airline": "KLM", "seasonal": False}, {"to": "BKK", "airline": "KLM", "seasonal": False}]}}
    near = flown.within(routes, Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.22, 4.40), 3500, airports)
    assert [route["to"] for route in near["AMS"]["routes"]] == ["BCN"]

def test_unasked_destination_maybe():
    found = flown.coverage(ROUTES, flown.seen_codes(ROUTES, set(), AIRPORTS), ryanair={"CRL"}, asked={"OTP"})
    assert found["missing"] == ["BOJ", "LGW", "OTP"] and found["not_asked"] == ["BOJ", "LGW"]

def test_airport_article_first(monkeypatch):
    monkeypatch.setattr(flown.access, "sparql", lambda query: [
        {"iata": "DUS", "article": "https://en.wikipedia.org/wiki/D%C3%BCsseldorf_Airport"},
        {"iata": "DUS", "article": "https://en.wikipedia.org/wiki/D%C3%BCsseldorf_Airport_station"}])
    assert flown.articles(["DUS"]) == {"DUS": "https://en.wikipedia.org/wiki/D%C3%BCsseldorf_Airport"}
