"""The regions of Europe (regions.py): which ones a map found too few cities in, and what each is asked again. No network."""
from discover.flights import regions
from trip_planner.trip.places import Place

ANTWERP = Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.22, 4.40)
AIRPORTS = {code: {"code": code, "country": country, "large": True, "latitude": latitude, "longitude": longitude, "city": city}
            for code, country, city, latitude, longitude in
            (("BCN", "ES", "Barcelona", 41.3, 2.08), ("MAD", "ES", "Madrid", 40.47, -3.56), ("LPA", "ES", "Gran Canaria", 27.93, -15.39),
             ("RIX", "LV", "Riga", 56.92, 23.97), ("VNO", "LT", "Vilnius", 54.63, 25.29), ("KUN", "LT", "Kaunas", 54.96, 24.08),
             ("JFK", "US", "New York", 40.64, -73.78))}
city = lambda code, airports: airports[code]["city"]

def test_an_airport_belongs_to_its_region_and_the_islands_to_their_own():
    assert regions.region_of(AIRPORTS["BCN"]) == "Iberia" and regions.region_of(AIRPORTS["LPA"]) == "Canary Islands"
    assert regions.region_of({"country": "PT", "latitude": 32.7, "longitude": -16.8}) == "Madeira and the Azores"
    assert regions.region_of(AIRPORTS["JFK"]) is None

def test_a_flight_to_an_airport_the_list_does_not_know_counts_in_no_region():
    assert regions.region_of({}) is None
    assert regions.cities_found({"BCN", "XXX"}, AIRPORTS, city, ANTWERP) == {"Iberia": {"Barcelona"}}

def test_a_thin_region_is_asked_its_countries_and_islands_their_airports_by_name():
    wanted = regions.requests_for(ANTWERP, AIRPORTS, 3500, {"BCN", "MAD"}, asked={"LV"}, city_of=city)
    assert [region["region"] for region in wanted] == ["Baltic states", "Canary Islands"]   # Iberia has its two cities; New York is too far
    baltic, canaries = wanted
    assert baltic["targets"] == ["LT", "LV"]   # Lithuania not asked yet and with more airports; by name would give the same cities
    assert canaries["targets"] == ["LPA"]   # by name only: a request for Spain gives the mainland

def test_a_region_asked_again_makes_at_most_a_few_requests():
    many = {f"X{number:02}": {"code": f"X{number:02}", "country": "PL", "large": True, "latitude": 52.0, "longitude": 20.0 + number / 10,
                              "city": f"Town {number}"} for number in range(100)}
    wanted = regions.requests_for(ANTWERP, many, 3500, set(), asked=set(), city_of=city)
    assert len(wanted[0]["targets"]) == regions.REGION_REQUESTS

def test_the_map_says_how_many_cities_of_each_region_it_found():
    from discover.view import breakdown
    from discover import finder
    from discover.flights.sources import Days
    found = finder.Found(ANTWERP, Days("2026-11-12", "2026-11-19"), [], [], [], [],
                         regions=[{"region": "Baltic states", "large": 5, "before": 0, "requests": 3, "after": 2},
                                  {"region": "Iberia", "large": 20, "before": 9, "requests": 0, "after": 9}])
    part = breakdown.regions_section(found)
    assert part["rows"] == [["Baltic states", "5", "0", "3", "2"], ["Iberia", "20", "9", "-", "9"]] and "1 regions were" in part["note"]

def test_a_region_is_thin_by_its_share_of_cities_found_and_the_next_towns_count_for_none():
    assert regions.thin(3, 8) and not regions.thin(4, 8) and regions.thin(1, 2) and not regions.thin(2, 2)
    near = {"BRU": {"code": "BRU", "country": "BE", "large": True, "latitude": 50.9, "longitude": 4.48, "city": "Brussels"},
            "RTM": {"code": "RTM", "country": "NL", "large": True, "latitude": 51.96, "longitude": 4.44, "city": "Rotterdam"}}
    assert regions.large_airports(ANTWERP, near, 3500) == {}   # Benelux, from Antwerp: nobody flies there
    tenerife = {"TFS": {"code": "TFS", "country": "ES", "large": True, "latitude": 28.04, "longitude": -16.57, "city": "Tenerife"}}
    wanted = regions.requests_for(ANTWERP, {**AIRPORTS, **near, **tenerife}, 3500, set(), set(), city, departures=frozenset({"LPA"}))
    assert next(region for region in wanted if region["region"] == "Canary Islands")["targets"] == ["TFS"]   # never one's own airport
