"""The routes flown today from the traveller's airports, from the "Airlines and destinations" table of each airport's
Wikipedia article: kept up to date by its readers, unlike OpenFlights (routes.py, 2014). They are what tells
whether the map is missing a destination, how many, and which ones.

Read by code, without a model: the table's rows are an airline and its destinations, each a link to the
destination airport's own article, "Seasonal:" before the ones flown part of the year. The article of each
airport is found on Wikidata, read through Tavily (Wikimedia refuses this program's own requests without a
contact, access.CONTACT_KEY), and a link becomes an airport by Wikidata (the article's IATA code) or, for a link
Wikidata does not know (a redirect), by the airport of that name. Kept ROUTES_DAYS.

A route flown and not found on the map is not always missed: a route flown twice a week may not fly those days.
So the destinations not found are asked of Kiwi once more, by name (finder.py), and what is still not found then
is listed, with its airlines, for the traveller to look at."""
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from discover import access
from trip_planner.trip.places import distance_km, load_airports
from trip_planner.texts import tr
from trip_planner.utils import plain

CACHE = Path(__file__).parent / "cache"
ROUTES = CACHE / "airport_routes.json"   # the routes read for each airport
ROUTES_DAYS = 30            # airlines open and close routes each season: the table is read again after this long
TITLES_BATCH = 150          # article titles asked of Wikidata in one query
SEASONAL = re.compile(r"\*\*\s*(?:seasonal|seasonal charter|charter)\s*:?\s*\*\*\s*:?", re.IGNORECASE)
LINK = re.compile(r"\[([^\]]+)\]\((https://en\.wikipedia\.org/wiki/[^)\s]+)(?:\s+\"[^\"]*\")?\)")

def title_of(url: str) -> str:
    """The title of a Wikipedia article from its link: "Bari_Karol_Wojty%C5%82a_Airport" is "Bari Karol Wojtyła Airport"."""
    return unquote(url.rsplit("/wiki/", 1)[1].split("#")[0]).replace("_", " ")

def table_rows(text: str) -> list[tuple]:
    """The rows of the "Airlines and destinations" table of an article, as (airline, [(title, seasonal)])."""
    heading = [match.end() for match in re.finditer(r"Airlines and destinations", text)]
    starts = [text.find("| Airlines", position) for position in heading]
    start = min((position for position in starts if position >= 0), default=-1)
    if start < 0:
        return []
    rows = []
    for line in text[start:].splitlines()[2:]:   # the header and its rule
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        if len(cells) < 2:
            continue
        airline = next((match.group(1) for match in LINK.finditer(cells[0])), re.sub(r"[\[\]*]", "", cells[0]).strip())
        destinations, seasonal = [], False
        # The cell, cut where "Seasonal:" or "Charter:" begins: every link after it is flown part of the year only
        for part in SEASONAL.split(cells[1]):
            destinations += [(title_of(match.group(2)), seasonal, match.group(1)) for match in LINK.finditer(part)]
            seasonal = True
        rows.append((airline, destinations))
    return rows

def articles(codes: list) -> dict[str, str]:
    """The English Wikipedia article of each airport, by IATA code, from Wikidata."""
    query = """SELECT ?iata ?article WHERE {
      VALUES ?iata { %s }
      ?airport wdt:P238 ?iata .
      ?article schema:about ?airport ; schema:isPartOf <https://en.wikipedia.org/> .
    }""" % " ".join(f'"{code}"' for code in codes if re.fullmatch(r"[A-Z0-9]{3}", code))
    # A code can be on more than the airport: DUS is also Düsseldorf Airport's railway station. The airport's article first
    airport_first = lambda url: ("airport" not in title_of(url).lower(), "station" in title_of(url).lower())
    found: dict[str, str] = {}
    for row in sorted(access.sparql(query), key=lambda row: airport_first(row["article"]), reverse=True):
        found[row["iata"]] = row["article"]
    return found

def codes_of(titles: list) -> dict[str, str]:
    """The IATA code of the airport of each article title: from Wikidata, otherwise from an airport of that name."""
    found: dict[str, str] = {}
    unique = list(dict.fromkeys(titles))
    for start in range(0, len(unique), TITLES_BATCH):
        batch = unique[start:start + TITLES_BATCH]
        names = " ".join('"' + title.replace("\\", "").replace('"', "") + '"@en' for title in batch)
        query = """SELECT ?title ?iata WHERE {
          VALUES ?title { %s }
          ?article schema:name ?title ; schema:isPartOf <https://en.wikipedia.org/> ; schema:about ?airport .
          ?airport wdt:P238 ?iata .
        }""" % names
        try:
            found.update({row["title"]: row["iata"] for row in access.sparql(query)})
        except Exception:
            pass   # the names below still find most of them
    by_name = {plain(airport["name"]): code for code, airport in load_airports().items()}
    for title in unique:
        if title not in found and plain(title) in by_name:
            found[title] = by_name[plain(title)]
    return found

GENERIC_WORDS = {"airport", "international", "aeroporto", "aeropuerto", "aeroport", "aéroport", "flughafen", "lotnisko", "regional",
                 "the", "de", "di", "del", "da", "la", "le", "of"}

def words(text: str) -> set:
    """The words of an airport's name that tell it apart: not "International Airport"."""
    return {word for word in re.findall(r"[a-z0-9]+", plain(text)) if word not in GENERIC_WORDS and len(word) > 1}

def by_text(text: str, title: str, airports: dict[str, Any], cities: dict[str, list], near: tuple = ()) -> str | None:
    """The airport of a link Wikidata does not know (a redirect: "Barcelona–El Prat Airport"), from the link's text,
    a city and maybe which of its airports ("Rome–Fiumicino", "Istanbul–Sabiha Gökçen"), or else from the words of
    the title. Two cities of one name: the one nearer the airport flown from (near), Barcelona in Spain from
    Eindhoven, not in Venezuela. None when it is not one airport for sure."""
    city, which = (re.split(r"\s*[–—]\s*", text, maxsplit=1) + [""])[:2]
    found = cities.get(plain(city), [])
    if which:
        found = [code for code in found if words(which) <= words(f"{airports[code]['name']} {airports[code].get('city', '')}")]
        if not found:   # the city of the database is another: the airport's own name says it
            found = [code for code, airport in airports.items() if words(which) and words(which) <= words(airport["name"])]
    elif len(found) > 1:
        found = [code for code in found if airports[code].get("large")] or found
    if len(found) > 1 and near:
        found = [min(found, key=lambda code: distance_km(*near, airports[code]["latitude"], airports[code]["longitude"]))]
    if len(found) == 1:
        return found[0]
    named = [code for code, airport in airports.items() if words(title) and words(title) <= words(airport["name"])]
    return named[0] if len(named) == 1 else None

def read_routes(article: str, extract: Callable[[str], str], near: tuple = ()) -> list[dict[str, Any]]:
    """The routes of one airport's article, near being where that airport is: [{"to", "airline", "seasonal"}]."""
    rows = table_rows(extract(article))
    codes = codes_of([title for _, destinations in rows for title, _, _ in destinations])
    airports = load_airports()
    cities: dict[str, list] = {}
    for code, airport in airports.items():
        cities.setdefault(plain(access.city_name(airport)), []).append(code)
    for _, destinations in rows:
        for title, _, text in destinations:
            if title not in codes and (code := by_text(text, title, airports, cities, near)):
                codes[title] = code
    return [{"to": codes[title], "airline": airline, "seasonal": seasonal}
            for airline, destinations in rows for title, seasonal, _ in destinations if title in codes]

def within(flown: dict[str, dict[str, Any]], place, km: float, airports: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The routes to destinations within km of a place: the ones a map looks for. Kiwi is asked for the countries
    that near, so a route to Bangkok counted as missed would only say the map is not what it is not meant to be."""
    near = lambda code: code in airports and distance_km(place.latitude, place.longitude, airports[code]["latitude"], airports[code]["longitude"]) <= km
    return {code: {**entry, "routes": [route for route in entry["routes"] if near(route["to"])]} for code, entry in flown.items()}

def routes(codes: list, today: date, say: Callable = print, extract: Callable | None = None) -> dict[str, dict[str, Any]]:
    """The routes flown from each airport, by code: {"routes", "article", "checked"}; kept ROUTES_DAYS. An airport
    whose article or table could not be read has none, and is read again by the next map."""
    if extract is None:
        from discover.transfers import tavily_extract as extract
    known = access.read(ROUTES)
    fresh = lambda code: code in known and date.fromisoformat(known[code]["checked"]) > today - timedelta(days=ROUTES_DAYS)
    new = [code for code in codes if not fresh(code)]
    if new:
        say(tr("discover_say_routes", airports=", ".join(new), days=ROUTES_DAYS))
        try:
            pages = articles(new)
        except Exception as error:
            say(tr("discover_say_wikidata_failed", error=type(error).__name__))
            pages = {}
        for code in new:
            if code not in pages:
                continue
            try:
                airport = load_airports().get(code) or {}
                found = read_routes(pages[code], extract, (airport["latitude"], airport["longitude"]) if airport else ())
            except Exception as error:
                say(tr("discover_say_routes_failed", airport=code, error=type(error).__name__))
                continue
            if found:
                known[code] = {"routes": found, "article": pages[code], "checked": today.isoformat()}
                access.write(ROUTES, known)
    return {code: known[code] for code in codes if fresh(code)}

def counts(flown: dict[str, dict[str, Any]]) -> dict[str, int]:
    """How many destinations each airport flies to, from its routes."""
    return {code: len({route["to"] for route in entry["routes"]}) for code, entry in flown.items()}

def missing(flown: dict[str, dict[str, Any]], seen: set, ryanair: set) -> dict[str, list[dict[str, Any]]]:
    """The destinations flown from the traveller's airports that no answer gave: {destination: its routes}. The
    traveller's own airports are no destinations, and a route of Ryanair from an airport where Ryanair answered is
    not missed: its own answer has every fare it sells those days."""
    mine = set(flown)
    gaps: dict[str, list[dict[str, Any]]] = {}
    for code, entry in flown.items():
        for route in entry["routes"]:
            if route["to"] in mine or route["to"] in seen or (plain(route["airline"]) == "ryanair" and code in ryanair):
                continue
            gaps.setdefault(route["to"], []).append({**route, "from": code})
    return gaps

def seen_codes(flown: dict[str, dict[str, Any]], answered: set, airports: dict[str, Any]) -> set:
    """The destinations of the routes that an answer gave, the airport itself or another of its city: Kiwi gives
    one airport per city, so London by Stansted is London by Gatwick too."""
    cities = {access.city_key(airports[code]) for code in answered if code in airports}
    destinations = {route["to"] for entry in flown.values() for route in entry["routes"]}
    return {code for code in destinations if code in answered or (code in airports and access.city_key(airports[code]) in cities)}

def coverage(flown: dict[str, dict[str, Any]], seen: set, ryanair: set, asked: set = frozenset()) -> dict[str, Any]:
    """How much of what is flown from the traveller's airports the map found, by destination:
    {"flown", "found", "ryanair_no_fare" (Ryanair's own answer had no fare to it those days: nothing missed),
     "missing" (not found), "missing_seasonal", "not_asked" (the missing ones not asked of Kiwi by name: beyond the
     requests a map may make, so they may have flights), "airports"}. asked is the destinations asked by name."""
    mine = set(flown)
    all_year: dict[str, bool] = {}   # destination -> flown all year from at least one of the airports
    for entry in flown.values():
        for route in entry["routes"]:
            if route["to"] not in mine:
                all_year[route["to"]] = all_year.get(route["to"], False) or not route["seasonal"]
    gaps = missing(flown, seen, ryanair)
    lost = sorted(code for code in all_year if code not in seen and code in gaps)
    return {"flown": len(all_year), "found": sum(code in seen for code in all_year),
            "ryanair_no_fare": sum(code not in seen and code not in gaps for code in all_year),
            "missing": lost, "missing_seasonal": [code for code in lost if not all_year[code]],
            "not_asked": [code for code in lost if code not in asked], "airports": sorted(flown)}
