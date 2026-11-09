"""The published ways from an airport into its city (transfers.py), the city each airport serves (access.py) and the
operators' rides on the traveller's side (rides.py). No network: the sources and the model answer what each test says.

- A price is read by the code, in its currency, from a sentence that is word for word in the source.
- A price the model changed, put in another sentence, or that is not plausible for the distance is dropped.
- The way chosen is public transport, a single ticket, a usual price before a "from" one.
- Of a city's Wikivoyage article, only the airport's own part is read.
- The map shows a published price, with its source, before an estimate.
- An airport's city is the one travellers go to.
- The ride chosen on the traveller's side fits the flight."""
from datetime import date, timedelta

import pytest

from discover.airports import access, rides, transfers
from trip_planner import current
from trip_planner.trip.trip_settings import TripSettings
from trip_planner.trip.places import Place

TODAY = date.today()
PARIS = Place("Paris", "Paris", "Paris", "FR", 48.8566, 2.3522)
BEAUVAIS = {"code": "BVA", "name": "Paris Beauvais-Tillé Airport", "city": "Beauvais", "country": "FR", "latitude": 49.4544, "longitude": 2.1128}
PAGE = ("PRICE: Online : adult one way €17,90 / return €29,90€, child (4-11 years) one way €9,90. "
        "The shuttle takes about 75 minutes to Porte Maillot.")
SOURCES = [{"name": "aeroportparisbeauvais.com", "url": "https://www.aeroportparisbeauvais.com/en/shuttle", "text": PAGE}]

@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    for module, name in ((access, "CENTRES"), (access, "ACCESS"), (transfers, "TRANSFERS"), (transfers, "PAGES")):
        monkeypatch.setattr(module, name, tmp_path / f"{name.lower()}.json")
    settings = TripSettings(origin="Coimbra", destination="Paris", dates=[f"{TODAY + timedelta(days=30):%d/%m/%Y}"],
                            earliest_leave="", latest_leave="")
    monkeypatch.setattr(current, "SETTINGS", settings)

def option(**changes) -> dict:
    return {"source": 0, "mode": "shuttle", "operator": "Navette", "to": "Porte Maillot",
            "quote": "PRICE: Online : adult one way €17,90 / return €29,90€, child (4-11 years) one way €9,90.",
            "price_text": "€17,90", "one_way": True, "minutes_text": "about 75 minutes"} | changes

def check(**changes) -> dict | None:
    return transfers.checked_option(option(**changes), SOURCES, BEAUVAIS, 80, lambda amount, currency: amount if currency == "EUR" else amount * 1.2)

# ---------------------------------------------------------------- reading a price
def test_a_price_is_read_by_the_code_in_its_currency():
    assert transfers.read_price("€19,90", "FR") == (19.9, "EUR")
    assert transfers.read_price("£ 19", "GB") == (19.0, "GBP")
    assert transfers.read_price("49 kr", "SE") == (49.0, "SEK") and transfers.read_price("49 kr", "NO") == (49.0, "NOK")
    assert transfers.read_price("30 dh", "MA") == (30.0, "MAD") and transfers.read_price("7.45 EUR", "ES") == (7.45, "EUR")
    assert transfers.read_price("platform 4", "ES") is None
    assert transfers.read_minutes("1 hour 15 minutes") == 75 and transfers.read_minutes("1h15") == 75 and transfers.read_minutes("500 m") is None

def test_a_price_quoted_word_for_word_is_kept_with_its_source():
    kept = check()
    assert (kept["price_eur"], kept["minutes"], kept["url"], kept["from_price"]) == (17.9, 75, SOURCES[0]["url"], False)

def test_a_price_the_model_changed_or_moved_is_dropped():
    assert check(price_text="€15,90") is None                        # not in the quote
    assert check(quote="Adult one way €15,90.", price_text="€15,90") is None   # not in the source
    assert check(source=3) is None and check(mode="helicopter") is None
    assert check(quote="child (4-11 years) one way €9,90.", price_text="€9,90", minutes_text="2 hours") is not None   # a real sentence...
    assert check(quote="child (4-11 years) one way €9,90.", price_text="€9,90", minutes_text="2 hours")["minutes"] is None   # ...but not its time

def test_a_price_not_plausible_for_the_distance_is_dropped():
    page = [{"name": "x", "url": "https://x.example", "text": "A private transfer costs €900 per car."}]
    found = transfers.checked_option(option(quote="A private transfer costs €900 per car.", price_text="€900"), page, BEAUVAIS, 80,
                                     lambda amount, currency: amount)
    assert found is None

def test_the_way_in_is_public_transport_a_single_ticket_and_a_usual_price_first():
    options = [{"mode": "taxi", "one_way": True, "from_price": False, "price_eur": 5.0},
               {"mode": "bus", "one_way": True, "from_price": True, "price_eur": 9.9},
               {"mode": "shuttle", "one_way": False, "from_price": False, "price_eur": 12.0},
               {"mode": "shuttle", "one_way": True, "from_price": False, "price_eur": 17.9}]
    assert transfers.best_option(options)["price_eur"] == 17.9
    page = [{"name": "x", "url": "https://x.example", "text": "Book online starting from €9.90 and skip the lines."}]
    starting = transfers.checked_option(option(quote="Book online starting from €9.90 and skip the lines.", price_text="€9.90"),
                                        page, BEAUVAIS, 80, lambda amount, currency: amount)
    assert starting["from_price"]

# ---------------------------------------------------------------- the texts
ARTICLE = """## Get in

### By plane

#### [Barcelona El Prat Airport](https://www.aena.es) (BCN IATA)

The Aerobus costs €7.45 for a single journey.

#### [Girona–Costa Brava Airport](https://www.aena.es)

A one-way ticket costs €16.

### By train

Trains arrive at Sants."""

def test_only_the_airports_own_part_of_the_article_is_read():
    el_prat = {"code": "BCN", "name": "Josep Tarradellas Barcelona-El Prat Airport"}
    part = transfers.by_plane(ARTICLE, el_prat)
    assert "Aerobus" in part and "Girona" not in part and "Sants" not in part
    alone = transfers.by_plane("### By plane\n\nThe bus costs €3.\n\n### By bus\n\nNo.", {"code": "XXX", "name": "Somewhere Airport"})
    assert "€3" in alone and "No." not in alone

def test_an_airports_published_ways_are_looked_for_once_and_kept(monkeypatch):
    asked = []
    found = transfers.look_for(BEAUVAIS, PARIS, "", search=lambda query: asked.append(query) or list(SOURCES),
                               model=lambda airport, city, sources: [option()], convert=lambda amount, currency: amount)
    assert found["best"]["price_eur"] == 17.9 and len(found["options"]) == 1 and "Beauvais" in asked[0]
    monkeypatch.setattr(transfers, "look_for", lambda *arguments: found)
    centres = {"BVA": PARIS}
    assert transfers.look_up(["BVA"], {"BVA": BEAUVAIS}, centres, {}, TODAY, say=lambda text: None)["BVA"]["best"]["price_eur"] == 17.9
    monkeypatch.setattr(transfers, "look_for", lambda *arguments: pytest.fail("looked for again"))
    assert transfers.look_up(["BVA"], {"BVA": BEAUVAIS}, centres, {}, TODAY, say=lambda text: None)["BVA"]["best"]["price_eur"] == 17.9

# ---------------------------------------------------------------- on the map
def test_the_map_shows_a_published_price_with_its_source_before_an_estimate(monkeypatch):
    monkeypatch.setattr(access, "ask_wikidata", lambda codes: [])
    access.write(access.CENTRES, {"BVA": {"name": "Paris", "latitude": PARIS.latitude, "longitude": PARIS.longitude, "voyage": "", "country": "FR"}})
    found = transfers.look_for(BEAUVAIS, PARIS, "", search=lambda query: list(SOURCES), model=lambda airport, city, sources: [option()],
                               convert=lambda amount, currency: amount)
    transfers.write(transfers.TRANSFERS, {"BVA": {**found, "checked": TODAY.isoformat()}})
    way = access.leg("BVA", False, PARIS, TODAY)
    assert (way["price_eur"], way["price_grade"], way["url"]) == (17.9, "B", SOURCES[0]["url"]) and "aeroportparisbeauvais.com" in way["how"]
    far = Place("Lyon", "Lyon", "Lyon", "FR", 45.76, 4.84)
    assert access.leg("BVA", False, far, TODAY)["price_grade"] == "C"   # the price into Paris says nothing about the way to Lyon

def test_an_airports_city_is_the_one_travellers_go_to():
    rabat = {"code": "RBA", "name": "Rabat–Salé Airport", "city": "Rabat", "country": "MA", "latitude": 34.05, "longitude": -6.75}
    rows = [{"iata": "RBA", "cityLabel": "Salé", "coord": "Point(-6.81 34.05)", "population": "945101"},
            {"iata": "RBA", "cityLabel": "Rabat", "coord": "Point(-6.84 34.02)", "population": "515619", "voyage": "https://en.wikivoyage.org/wiki/Rabat"}]
    assert access.served(rows, rabat)["name"] == "Rabat"
    marseille = {"code": "MRS", "name": "Marseille Provence Airport", "city": "Marignane", "country": "FR", "latitude": 43.44, "longitude": 5.22}
    assert access.served([{"iata": "MRS", "cityLabel": "Marseille", "coord": "Point(5.37 43.30)"}], marseille)["name"] == "Marseille"
    assert access.served([{"iata": "MRS", "cityLabel": "Marseille", "coord": "Point(-70.0 10.0)"}], marseille) is None   # too far

def test_wikidata_gives_the_cities_and_nominatim_only_the_ones_it_lacks(monkeypatch):
    monkeypatch.setattr(access, "ask_wikidata", lambda codes: [{"iata": "MRS", "cityLabel": "Marseille", "coord": "Point(5.37 43.30)"}])
    monkeypatch.setattr(access, "find_centre", lambda name, country: None)
    airports = {"MRS": {"code": "MRS", "name": "Marseille Provence Airport", "city": "Marignane", "country": "FR", "latitude": 43.44, "longitude": 5.22},
                "XXX": {"code": "XXX", "name": "Nowhere Airport", "city": "Nowhere", "country": "FR", "latitude": 43.0, "longitude": 5.0}}
    found = access.centres(["MRS", "XXX"], airports, TODAY, say=lambda text: None)
    assert found["MRS"].english_name == "Marseille" and "XXX" not in found

# ---------------------------------------------------------------- the traveller's side
def test_the_ride_to_the_airport_arrives_in_time_and_the_ride_home_leaves_after_landing():
    found = [{"leave": 6 * 60, "arrive": 8 * 60, "price_eur": 5.0, "source": "FlixBus", "urls": ["https://shop.flixbus.pt/x"]},
             {"leave": 9 * 60, "arrive": 11 * 60, "price_eur": 3.0, "source": "FlixBus", "urls": []},
             {"leave": 17 * 60, "arrive": 19 * 60, "price_eur": 4.0, "source": "Rede Expressos", "urls": []}]
    flight = {"time": "11:30", "arrival_time": "15:40"}
    out = rides.best_ride(found, flight, True)    # at the airport two hours before 11:30: the 06:00, not the cheaper 09:00
    assert (out["price_eur"], out["leave_time"], out["url"]) == (5.0, "06:00", "https://shop.flixbus.pt/x")
    home = rides.best_ride(found, flight, False)   # after landing at 15:40: the 17:00
    assert (home["source"], home["leave_time"]) == ("Rede Expressos", "17:00")
    assert rides.best_ride(found, {"time": "06:30"}, True) is None
    way = access.leg("OPO", True, Place("Coimbra", "Coimbra", "Coimbra", "PT", 40.21, -8.43), TODAY, ride=out)
    assert way["looked_up"] and way["grade"] == way["price_grade"] == "A" and way["price_eur"] == 5.0

def test_the_rides_are_asked_once_per_airport_day_and_direction():
    flights = [{"from": "OPO", "date": "2026-11-12", "back": {"to": "LIS", "date": "2026-11-15", "arrival_date": "2026-11-15"}},
               {"from": "OPO", "date": "2026-11-12", "back": None}, {"from": "XXX", "date": "2026-11-13", "back": None}]
    assert rides.wanted_for(flights, [{"code": "OPO"}, {"code": "LIS"}]) == {("OPO", "2026-11-12", True), ("LIS", "2026-11-15", False)}

def test_a_night_bus_a_range_and_a_forum_are_not_the_way_in():
    way = lambda **changes: {"mode": "bus", "operator": "Aerobus", "quote": "The Aerobus costs 7.45.", "price_text": "€7.45", "one_way": True,
                             "from_price": False, "price_eur": 7.45, "url": "https://www.aerobusbcn.com"} | changes
    night = way(operator="NitBus N17", quote="The NitBus N17 costs 2.90.", price_eur=2.9)
    forum = way(price_eur=1.0, url="https://www.reddit.com/r/barcelona")
    assert transfers.best_option([night, forum, way()])["operator"] == "Aerobus"
    page = [{"name": "x", "url": "https://x.example", "text": "Arriva buses cost £1–3 into the centre."}]
    ranged = transfers.checked_option(option(quote="Arriva buses cost £1–3 into the centre.", price_text="£1–3"), page, BEAUVAIS, 80,
                                      lambda amount, currency: amount)
    assert ranged["from_price"]
    page = [{"name": "reddit.com", "url": "https://www.reddit.com/r/x", "text": "I paid €2 for the bus."}]
    assert transfers.checked_option(option(quote="I paid €2 for the bus.", price_text="€2"), page, BEAUVAIS, 80, lambda amount, currency: amount) is None

def test_a_connection_found_in_the_timetables_takes_the_published_price_of_its_kind():
    from discover import confirm
    published = {"options": [
        {"mode": "bus", "operator": "Aerobus", "quote": "Aerobus costs €7.45.", "price_text": "€7.45", "one_way": True, "from_price": False,
         "price_eur": 7.45, "url": "https://www.aerobusbcn.com", "source": "aerobusbcn.com"},
        {"mode": "train", "operator": "Rodalies R2", "quote": "The R2 costs €5.15.", "price_text": "€5.15", "one_way": True, "from_price": False,
         "price_eur": 5.15, "url": "https://rodalies.gencat.cat", "source": "rodalies.gencat.cat"}]}
    by_train = {"steps": [{"mode": "REGIONAL_RAIL", "km": 14}, {"mode": "SUBWAY", "km": 2}]}
    assert confirm.published_fare(by_train, published)["price_eur"] == 5.15
    assert confirm.published_fare({"steps": [{"mode": "TRAM", "km": 9}]}, published) is None   # nothing published for the tram
    assert confirm.published_fare(by_train, None) is None

def test_a_much_earlier_ride_to_save_a_euro_is_not_the_one_taken():
    found = [{"leave": 3 * 60, "arrive": 4 * 60 + 35, "price_eur": 5.0, "source": "Rede Expressos", "urls": []},
             {"leave": 7 * 60, "arrive": 8 * 60 + 40, "price_eur": 6.0, "source": "Rede Expressos", "urls": []}]
    assert rides.best_ride(found, {"time": "11:00"}, True)["leave_time"] == "07:00"

def test_an_airport_where_nothing_usable_was_found_is_looked_for_again_a_week_later(monkeypatch):
    transfers.write(transfers.TRANSFERS, {"BVA": {"options": [], "best": None, "city": "Paris", "checked": (TODAY - timedelta(days=8)).isoformat()}})
    asked = []
    monkeypatch.setattr(transfers, "look_for", lambda *arguments: asked.append(arguments[0]["code"]) or {"options": [], "best": None, "city": "Paris"})
    transfers.look_up(["BVA"], {"BVA": BEAUVAIS}, {"BVA": PARIS}, {}, TODAY, say=lambda text: None)
    assert asked == ["BVA"]
    transfers.look_up(["BVA"], {"BVA": BEAUVAIS}, {"BVA": PARIS}, {}, TODAY, say=lambda text: None)
    assert asked == ["BVA"]   # not again the same week

def test_the_published_price_of_the_very_line_found_comes_before_a_cheaper_one():
    from discover import confirm
    published = {"options": [
        {"mode": "shuttle", "operator": "AMTS", "quote": "The AMTS bus costs €1.", "price_text": "€1", "one_way": True, "from_price": False,
         "price_eur": 1.0, "url": "https://www.aeroporto.catania.it", "source": "aeroporto.catania.it"},
        {"mode": "shuttle", "operator": "AMTS Alibus", "quote": "The Alibus costs €4.", "price_text": "€4", "one_way": True, "from_price": False,
         "price_eur": 4.0, "url": "https://en.wikivoyage.org/wiki/Catania", "source": "Wikivoyage"}]}
    assert confirm.published_fare({"steps": [{"mode": "BUS", "km": 7, "line": "ALIB"}]}, published)["price_eur"] == 4.0
    assert confirm.published_fare({"steps": [{"mode": "BUS", "km": 7, "line": "22"}]}, published)["price_eur"] == 1.0
    assert confirm.part_estimated({"steps": [{"estimated": True}, {"mode": "BUS"}]}) and not confirm.part_estimated({"steps": [{"mode": "BUS"}]})
