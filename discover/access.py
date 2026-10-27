"""The way between an airport and its city, on both sides of a trip: from the traveller's city to the departure
airport, from the arrival airport to the destination's centre, and the same on the way back.

Each way is the best that is known about it, with its grade, best first:
- looked up for this very flight: an operator's ride sold for that day (rides.py, A), or the real timetables
  (confirm.py), with the leg's own grades;
- looked up before, for another flight, and remembered here: B at best, since it was read for another day and hour;
- the price the operators and the guides publish for the way into the city (transfers.py): B;
- otherwise an estimate from the distance: C, and said so.

The city of each airport is the one Wikidata says it serves (Marseille for Marignane's airport), with its
position, found once for all the airports of a map; OpenStreetMap's Nominatim only for the ones Wikidata lacks.
What is remembered does not depend on where the traveller comes from: the way from Stansted to London is the
same for everyone. So every map makes the next ones more exact."""
import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from discover import transfers
from trip_planner.trip.trip_settings import USER_AGENT
from trip_planner.itineraries.estimates import estimated_minutes, estimated_price
from trip_planner.texts import tr
from trip_planner.trip.places import airport_from, ask_nominatim, distance_km, load_airports, move_to_centre, place_of, Place, settlement
from trip_planner.utils import plain

CACHE = Path(__file__).parent / "cache"
CENTRES = CACHE / "city_centres.json"     # the city each destination airport serves, found once
ACCESS = CACHE / "airport_access.json"    # the ways between an airport and its city looked up in the timetables
ACCESS_DAYS = 45            # timetables change with the seasons: a way remembered longer than this is not used
MISSING_DAYS = 30           # an airport whose city was not found is asked about again after this long
CENTRE_MAX_KM = 100         # a city found farther than this from its airport is another place of the same name
SAME_CITY_KM = 15           # a traveller's city this close to an airport's city is that city: its published ways apply
WIKIDATA = "https://query.wikidata.org/sparql"
WIKIDATA_BATCH = 80         # airports asked about in one query
# Wikimedia asks every program for a contact in its name, and refuses some without one. Put one in env/.env under
# this key (an email or a web page) and it is added to the requests to Wikidata; nothing else is ever sent there.
CONTACT_KEY = "WIKIMEDIA_CONTACT"
# Nominatim asks for one request a second at most, and answered 429 (too many) after 22 cities asked at that pace,
# on 2026-10-21: twice as slow, and at the first 429 the other cities wait for the next map
NOMINATIM_SPACING_SECONDS = 2.0
# ... and the planner locates its cities there too: a map locates at most this many cities there, the cheapest first
MAX_NEW_CITIES = 15
# A city not located yet: the way into it is estimated as from an airport this far from its city, about what the
# airports of the maps made so far are. A guess, grade C, and said so; counting nothing would make that city cheap.
TYPICAL_AIRPORT_KM = 20

# The grade of a price, from the kind of price the planner gives it (legs.py): A when an operator sold that very
# ride, B when the price is real but not read for that departure, C when it is a guess
PRICE_GRADES = {"operator": "A", "free": "A", "tariff": "B", "operator_typical": "B"}

def read(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}

def write(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=0), encoding="utf-8")

def city_name(airport: dict[str, Any]) -> str:
    """The city an airport is in, as the airport database writes it, without where the airport is: the database
    writes "Paris (Orly, Val-de-Marne)" and "London, Essex", which would make them other cities. Empty when none."""
    return re.split(r"[(,]", airport.get("city") or "")[0].strip()

def city_key(airport: dict[str, Any]) -> str:
    """The city of an airport by the database, when its centre is not known: its country and its name."""
    return f"{airport.get('country', '')}|{city_name(airport) or airport.get('code', '')}"

def place_key(place: Place) -> str:
    """A place as a key: its position, to about a kilometre. Two airports serving one city share it."""
    return f"{place.latitude:.2f},{place.longitude:.2f}"

# ---------------------------------------------------------------- the city of each airport
def wikimedia_agent() -> str:
    contact = os.environ.get(CONTACT_KEY, "").strip()
    return f"{USER_AGENT[:-1]}; {contact})" if contact and USER_AGENT.endswith(")") else USER_AGENT

def ask_wikidata(codes: list) -> list[dict[str, str]]:
    """The cities Wikidata says each airport serves, by IATA code, with their position and Wikivoyage article."""
    query = """SELECT ?iata ?cityLabel ?population ?coord ?voyage WHERE {
      VALUES ?iata { %s }
      ?airport wdt:P238 ?iata ; wdt:P931 ?city .
      ?city wdt:P625 ?coord .
      # A state or a region is no city: Frankfurt-Hahn "serves" Rhineland-Palatinate
      FILTER NOT EXISTS { ?city wdt:P31/wdt:P279* wd:Q10864048 }
      OPTIONAL { ?city wdt:P1082 ?population }
      OPTIONAL { ?voyage schema:about ?city ; schema:isPartOf <https://en.wikivoyage.org/> }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }""" % " ".join(f'"{code}"' for code in codes if re.fullmatch(r"[A-Z0-9]{3}", code))
    return sparql(query)

def sparql(query: str) -> list[dict[str, str]]:
    """The rows of a Wikidata query, each {variable: value}."""
    response = httpx.post(WIKIDATA, data={"query": query, "format": "json"}, timeout=90,
                          headers={"User-Agent": wikimedia_agent(), "Accept": "application/sparql-results+json"})
    response.raise_for_status()
    return [{key: value["value"] for key, value in row.items()} for row in response.json()["results"]["bindings"]]

def served(rows: list[dict[str, str]], airport: dict[str, Any]) -> dict[str, Any] | None:
    """Of the cities Wikidata gives for an airport, the one travellers go to: the one the airport database names,
    then one named in the airport's name, then one with a guide, then the most populous (Rabat before Salé)."""
    def point(row: dict[str, str]) -> tuple:
        longitude, latitude = re.findall(r"-?\d+(?:\.\d+)?", row["coord"])[:2]
        return float(latitude), float(longitude)
    near = [row for row in rows if distance_km(*point(row), airport["latitude"], airport["longitude"]) <= CENTRE_MAX_KM]
    if not near:
        return None
    named = plain(city_name(airport))
    row = min(near, key=lambda row: (plain(row["cityLabel"]) != named, plain(row["cityLabel"]) not in plain(airport["name"]),
                                     "voyage" not in row, -float(row.get("population") or 0)))
    latitude, longitude = point(row)
    return {"name": row["cityLabel"], "latitude": latitude, "longitude": longitude, "voyage": row.get("voyage", ""), "source": "Wikidata"}

last_request = [0.0]

def find_centre(name: str, country: str) -> Place | None:
    """A city by its name and country, with OpenStreetMap's Nominatim, put where its town is (move_to_centre)."""
    wait = last_request[0] + NOMINATIM_SPACING_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        results = ask_nominatim({"city": name, "countrycodes": country.lower()})
    finally:
        last_request[0] = time.monotonic()
    if not results:
        return None
    chosen = settlement(results, name)
    move_to_centre(chosen)
    return place_of(name, chosen)

def as_place(entry: dict[str, Any]) -> Place:
    return Place(entry["name"], entry["name"], entry["name"], entry.get("country", ""), entry["latitude"], entry["longitude"])

def centres(codes: list, airports: dict[str, Any], today: date, say=print) -> dict[str, Place]:
    """The centre of the city each airport serves, by airport code, the codes the most wanted first. Found once
    per airport and kept in CENTRES: Wikidata for all of them at once, Nominatim for at most MAX_NEW_CITIES it
    lacks. An airport whose city is not found, or is found too far from it, has none."""
    known = read(CENTRES)
    wanted = [code for code in dict.fromkeys(codes) if code in airports]

    def usable(entry: dict[str, Any] | None) -> bool:
        return bool(entry) and ("latitude" in entry or date.fromisoformat(entry.get("checked", "2000-01-01")) > today - timedelta(days=MISSING_DAYS))
    new = [code for code in wanted if not usable(known.get(code))]
    if new:
        say(tr("discover_say_centres", count=len(new)))
    left = []
    for start in range(0, len(new), WIKIDATA_BATCH):
        batch = new[start:start + WIKIDATA_BATCH]
        try:
            rows = ask_wikidata(batch)
        except Exception as error:
            say(tr("discover_say_wikidata_failed", error=type(error).__name__))
            left += batch
            continue
        for code in batch:
            city = served([row for row in rows if row["iata"] == code], airports[code])
            if city:
                known[code] = {**city, "country": airports[code]["country"]}
            else:
                left.append(code)
    write(CENTRES, known)
    if len(left) > MAX_NEW_CITIES:
        say(tr("discover_say_centres_later", count=len(left) - MAX_NEW_CITIES, most=MAX_NEW_CITIES))
    for code in left[:MAX_NEW_CITIES]:
        airport = airports[code]
        try:
            place = find_centre(city_name(airport) or airport["name"], airport["country"])
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                say(tr("discover_say_osm_wait"))
                break
            continue   # not kept: asked again next time
        except Exception:
            continue
        if place and airport_from(place, code)["km"] <= CENTRE_MAX_KM:
            known[code] = {"name": place.english_name, "latitude": place.latitude, "longitude": place.longitude, "voyage": "",
                           "source": "OpenStreetMap", "country": airport["country"]}
        else:
            known[code] = {"checked": today.isoformat()}
        write(CENTRES, known)
    return {code: as_place(known[code]) for code in wanted if "latitude" in (known.get(code) or {})}

def voyages(codes: list) -> dict[str, str]:
    """The Wikivoyage article of the city each airport serves, when Wikidata knows one."""
    known = read(CENTRES)
    return {code: known[code]["voyage"] for code in codes if (known.get(code) or {}).get("voyage", "").startswith("https://")}

def centre_of(code: str) -> Place | None:
    entry = read(CENTRES).get(code) or {}
    return as_place(entry) if "latitude" in entry else None

# ---------------------------------------------------------------- the ways remembered
def access_key(code: str, to_airport: bool, place: Place) -> str:
    return f"{code}|{'to' if to_airport else 'from'}|{place_key(place)}"

def remembered(code: str, to_airport: bool, place: Place, today: date) -> dict[str, Any] | None:
    """A way looked up before, when it is recent enough to be used."""
    entry = read(ACCESS).get(access_key(code, to_airport, place))
    if not entry or date.fromisoformat(entry["checked"]) <= today - timedelta(days=ACCESS_DAYS):
        return None
    return entry

def remember(code: str, to_airport: bool, place: Place, leg: dict[str, Any], today: date) -> None:
    """Keep a way looked up for a flight, for the next maps: what it cost and took, and its grades."""
    known = read(ACCESS)
    known[access_key(code, to_airport, place)] = {key: leg[key] for key in ("price_eur", "minutes", "grade", "price_grade", "for")} | {
        "checked": today.isoformat()}
    write(ACCESS, known)

def worse(grade: str, floor: str) -> str:
    return max(grade, floor)   # "A" < "B" < "C"

def published_for(code: str, place: Place, today: date) -> dict[str, Any] | None:
    """What is published for the way between an airport and its city, when the place is that city."""
    centre = centre_of(code)
    if centre is None or distance_km(place.latitude, place.longitude, centre.latitude, centre.longitude) > SAME_CITY_KM:
        return None
    return transfers.published(code, today)

def described(option: dict[str, Any]) -> str:
    """A published way in a few words: who, by what, where to, for how much, and where that was read."""
    words = " ".join(part for part in (option["operator"], option["mode"]) if part)
    return tr("discover_published", what=words + (tr("discover_page_to_place", place=option["to"]) if option["to"] else ""),
              price=(tr("discover_from_price") if option["from_price"] else "") + option["price_text"], source=option["source"])

def for_text(value: Any) -> str:
    """The flight a way was looked up for: {"leaving", "time"}, or the words of a map made before they were kept so."""
    if isinstance(value, dict):
        return tr("discover_for_leaving" if value.get("leaving") else "discover_for_landing", time=value.get("time", ""))
    return str(value)

def leg(code: str, to_airport: bool, place: Place | None, today: date, label: str = "", ride: dict[str, Any] | None = None) -> dict[str, Any]:
    """The best known way between an airport and a place: {"airport", "to_airport", "place", "price_eur", "minutes",
    "grade" (of the timetable), "price_grade", "how", "looked_up", and "url", "quote", "options" when published}.
    "urls" are where it is sold, for a ride sold for that day; "url" is where its price was read.
    ride is an operator's ride sold for this very flight (rides.py). looked_up is True for a way read for this flight."""
    base = {"airport": code, "to_airport": to_airport, "place": label or (place.english_name if place else ""), "looked_up": False}
    if ride:
        return {**base, "price_eur": ride["price_eur"], "minutes": ride["arrive"] - ride["leave"], "grade": "A", "price_grade": "A",
                "how": tr("discover_ride_sold", source=ride["source"], leave=ride["leave_time"], arrive=ride["arrive_time"]), "looked_up": True,
                "leave": ride["leave_time"], "arrive": ride["arrive_time"], "url": ride.get("url", ""),
                "urls": [ride["url"]] if ride.get("url") else []}
    if place is None:
        country = (load_airports().get(code) or {}).get("country", "")
        return {**base, "price_eur": float(estimated_price(TYPICAL_AIRPORT_KM, country)), "minutes": estimated_minutes(TYPICAL_AIRPORT_KM),
                "grade": "C", "price_grade": "C",
                "how": tr("discover_estimated_typical", km=TYPICAL_AIRPORT_KM)}
    before = remembered(code, to_airport, place, today)
    found = published_for(code, place, today)
    best = (found or {}).get("best")
    shown = {"options": [{key: option[key] for key in ("mode", "operator", "to", "price_eur", "price_text", "from_price", "url", "source", "quote")}
                         for option in (found or {}).get("options", [])[:6]]} if found else {}
    if before and before["price_grade"] <= "B":
        return {**base, **shown, "price_eur": before["price_eur"], "minutes": before["minutes"], "grade": worse(before["grade"], "B"),
                "price_grade": worse(before["price_grade"], "B"), "how": tr("discover_looked_up_on", day=before["checked"], flight=for_text(before["for"]))}
    if best:
        airport = airport_from(place, code)
        minutes = (before or {}).get("minutes") or best.get("minutes") or estimated_minutes(airport["km"])
        return {**base, **shown, "price_eur": best["price_eur"], "minutes": minutes, "grade": worse((before or {}).get("grade", "C"), "B"),
                "price_grade": "B", "how": described(best) + ", " + tr("discover_read_on", day=found["checked"]), "url": best["url"], "quote": best["quote"]}
    if before:
        return {**base, **shown, "price_eur": before["price_eur"], "minutes": before["minutes"], "grade": worse(before["grade"], "B"),
                "price_grade": worse(before["price_grade"], "B"), "how": tr("discover_looked_up_on", day=before["checked"], flight=for_text(before["for"]))}
    airport = airport_from(place, code)
    return {**base, **shown, "price_eur": float(estimated_price(airport["km"], airport["country"])), "minutes": estimated_minutes(airport["km"]),
            "grade": "C", "price_grade": "C",
            "how": tr("discover_estimated_distance", km=airport["km"]) + ("; " + tr("discover_nothing_published") if found else "")}
