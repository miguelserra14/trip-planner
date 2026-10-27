"""The regions of Europe, so that a map has some of each: once the flights are searched, the regions within reach
with fewest cities found get a few more requests to Kiwi each (finder.py).

Kiwi is asked by country, the countries with most known routes first, and answers 15 cities each time: a light map
asks a third of the countries and can leave whole regions thin, however cheap their flights. A region is thin when
the cities found are fewer than MIN_SHARE of its large airports within reach, or fewer than MIN_CITIES. The thinnest
MAX_REGIONS are asked again, at most REGION_REQUESTS times each: their countries not asked yet, then the ones asked,
then their large airports by name; and with up to REGION_STOPS stops, since a region with no direct flight from the
traveller's airports would answer nothing. The airports within HOME_KM count for no region: nobody flies there.

The regions are by country, as the airports' database gives it, and two by position: the Canaries and the
Portuguese islands of the Atlantic are far from their mainland, and a request for Spain or Portugal fills its 15
cities with the mainland."""
from typing import Any

from trip_planner.texts import tr
from trip_planner.trip.places import distance_km, Place

MIN_SHARE = 0.5          # a region with fewer cities found than this share of its large airports within reach is thin...
MIN_CITIES = 3           # ... and one with fewer than this (or than its large airports)
MAX_REGIONS = 6          # thin regions asked again per map, the thinnest first
HOME_KM = 400            # airports this close to the traveller count for no region, and are never asked by name
REGION_REQUESTS = 6      # requests to Kiwi per region asked again
REGION_TOTAL = 36        # and per map, every region together (each counted in the day's limit as the others)
REGION_STOPS = 1         # stops each way those requests allow
NAMES_BATCH = 15         # airports asked by name in one request: Kiwi answers 15 cities

REGIONS = {
    "Iberia": ("ES", "PT", "AD", "GI"),
    "Canary Islands": ("ES",),
    "Madeira and the Azores": ("PT",),
    "France": ("FR", "MC"),
    "British Isles": ("GB", "IE", "IM", "JE", "GG"),
    "Benelux": ("BE", "NL", "LU"),
    "Germany and the Alps": ("DE", "AT", "CH", "LI"),
    "Italy and Malta": ("IT", "MT", "SM", "VA"),
    "Nordic countries": ("DK", "NO", "SE", "FI", "IS", "FO"),
    "Baltic states": ("EE", "LV", "LT"),
    "Central Europe": ("PL", "CZ", "SK", "HU", "SI"),
    "Western Balkans": ("HR", "BA", "RS", "ME", "MK", "AL", "XK"),
    "Romania, Bulgaria and Moldova": ("RO", "BG", "MD"),
    "Greece and Cyprus": ("GR", "CY"),
    "Turkey": ("TR",),
    "Caucasus": ("GE", "AM", "AZ"),
}
# The two regions by position, inside a country of another region
ISLANDS = {"Canary Islands": lambda airport: airport["country"] == "ES" and airport["latitude"] < 30,
           "Madeira and the Azores": lambda airport: airport["country"] == "PT" and airport["longitude"] < -12}

def region_name(region: str) -> str:
    """A region's name in the traveller's language: its text is discover_region_ and the name, in lower case."""
    return tr("discover_region_" + "".join(letter if letter.isalnum() else "_" for letter in region.lower()))

def region_of(airport: dict[str, Any]) -> str | None:
    """The region of an airport, None for one outside Europe and around it."""
    for name, inside in ISLANDS.items():
        if inside(airport):
            return name
    return next((name for name, countries in REGIONS.items() if name not in ISLANDS and airport.get("country") in countries), None)

def large_airports(place: Place, airports: dict[str, Any], km: float) -> dict[str, list[str]]:
    """The large airports of each region within km of the place and beyond HOME_KM, the closest first: the regions a
    map can reach."""
    found: dict[str, list[tuple]] = {}
    for code, airport in airports.items():
        region = region_of(airport) if airport.get("large") else None
        if region:
            away = distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"])
            if HOME_KM < away <= km:
                found.setdefault(region, []).append((away, code))
    return {region: [code for _, code in sorted(codes)] for region, codes in found.items()}

def cities_found(codes: set, airports: dict[str, Any], city_of, place: Place) -> dict[str, set]:
    """The cities found in each region, by the airports of the flights found, those within HOME_KM left out."""
    found: dict[str, set] = {}
    for code in codes:
        airport = airports.get(code) or {}
        region = region_of(airport)
        if region and distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"]) > HOME_KM:
            found.setdefault(region, set()).add(city_of(code, airports))
    return found

def thin(cities: int, large: int) -> bool:
    return cities < min(MIN_CITIES, large) or cities < MIN_SHARE * large

def requests_for(place: Place, airports: dict[str, Any], km: float, found: set, asked: set, city_of,
                 departures: frozenset = frozenset()) -> list[dict[str, Any]]:
    """The thinnest regions within reach, at most MAX_REGIONS, the thinnest first, each with the requests to make:
    [{"region", "cities", "large", "targets"}], a target being a country code or several airports by name."""
    reach = large_airports(place, airports, km)
    have = cities_found(found, airports, city_of, place)
    wanted = []
    for region, codes in reach.items():
        cities = len(have.get(region, set()))
        if not thin(cities, len(codes)):
            continue
        countries = sorted({airports[code]["country"] for code in codes},
                           key=lambda country: (country in asked, -sum(1 for code in codes if airports[code]["country"] == country)))
        # By name only where a request for the country cannot give them all: Kiwi answers 15 cities a country, and a
        # request for Spain gives the mainland, not the Canaries (tried on 2026-10-26: the Caucasus by name gave
        # the same five cities as its three countries)
        crowded = {country for country in countries if sum(1 for code in codes if airports[code]["country"] == country) > NAMES_BATCH}
        by_name = [code for code in codes if code not in found and code not in departures
                   and (region in ISLANDS or airports[code]["country"] in crowded)]
        targets = ([] if region in ISLANDS else countries) + [",".join(by_name[start:start + NAMES_BATCH])
                                                              for start in range(0, len(by_name), NAMES_BATCH)]
        wanted.append({"region": region, "cities": cities, "large": len(codes), "targets": targets[:REGION_REQUESTS]})
    return sorted(wanted, key=lambda region: (region["cities"] / region["large"], region["region"]))[:MAX_REGIONS]
