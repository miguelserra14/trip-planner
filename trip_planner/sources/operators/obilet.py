"""Obilet, the booking site of the coach companies of Turkey, the Balkans, Greece, Italy, Poland and their neighbours."""
import re
import threading
import time
from typing import Any

import httpx

from trip_planner import current
from trip_planner.places import distance_km, Place
from trip_planner.texts import tr
from trip_planner.utils import plain, to_minutes, to_time
from trip_planner.sources.operators.base import in_eur

# Obilet, the booking site of the coach companies of Turkey, which also sells those of the Balkans, Greece (the KTEL)
# and Romania: the countries where published timetables are fewest (none in Transitous for Turkey, Montenegro, North
# Macedonia, Kosovo, Albania or the Greek coaches on 2026-10-21). It is read with the requests its own site makes: the page of a search opens a
# session, and the journeys come as JSON. Its places are named in Turkish ("Ohri", "Burgaz"), each with its country.
# The airports of Turkey and of Bucharest are places of their own; elsewhere a coach leaves from the city, and the
# way between the airport and the city's coach station, in no timetable, is estimated as in a mixed leg
# (timetables.py): up to max_estimate_km, and the option is then not verified (C).
OBILET = "https://www.obilet.com"
OBILET_BROWSER = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}   # it refuses a program
OBILET_COUNTRIES = {"TR": "", "ME": "karadag", "MK": "makedonya", "XK": "kosova", "AL": "arnavutluk", "BA": "bosna-hersek",
                    "RS": "sirbistan", "BG": "bulgaristan", "RO": "romanya", "GR": "yunanistan", "HR": "hirvatistan",
                    "SI": "slovenya", "IT": "italya", "PL": "polonya", "MD": "moldova"}   # where it is asked, and the country as it writes it
# Italy's national coaches (Itabus, MarinoBus), Poland's private ones (Szwagropol, Majer) and Moldova's are in no
# published timetable either: Obilet sells them
OBILET_AIRPORT = re.compile(r"havaliman|airport|aeroport")
OBILET_STATION = re.compile(r"(?:main|central|centralna|glavna) bus station|otogar|avtobusna postaja|autobusni kolodvor")   # a town's own station, named without the town
OBILET_TIMEOUT = 45   # its searches in Bulgaria and Greece can take this long
OBILET_SPACING_SECONDS = 1.0   # it closes the connection to a client that asks faster: one request at a time, this far apart
SALES_DAYS = 120   # measured on 2026-10-23, Istanbul airport -> Bursa; Itabus sells about 178 days ahead. It varies by company
OBILET_SESSION: dict[str, Any] = {}
OBILET_LOCK = threading.Lock()

def obilet_ask(method: str, url: str, **options) -> httpx.Response:
    """One request to it, in its session, after the one before has had its time."""
    with OBILET_LOCK:
        if "client" not in OBILET_SESSION:
            OBILET_SESSION["client"] = httpx.Client(headers=OBILET_BROWSER, timeout=OBILET_TIMEOUT, follow_redirects=True)
        time.sleep(max(0.0, OBILET_SESSION.get("last", 0.0) + OBILET_SPACING_SECONDS - time.time()))
        try:
            return OBILET_SESSION["client"].request(method, url, **options).raise_for_status()
        finally:
            OBILET_SESSION["last"] = time.time()

def obilet_places(name: str) -> list[dict[str, Any]]:
    key = plain(name)
    if key not in OBILET_SESSION.setdefault("places", {}):
        found = obilet_ask("POST", f"{OBILET}/json/duraklar", json={"query": name}).json()
        OBILET_SESSION["places"][key] = found.get("items") or []
    return OBILET_SESSION["places"][key]

def obilet_words(text: str) -> set:
    """The words of a name that can tell one place from another."""
    return set(re.split(r"[^a-z0-9]+", plain(text.replace("ı", "i")))) - {"", "airport", "international", "havalimani", "de", "del", "la", "di"}

def obilet_place(names: list[str], country: str, airport: bool) -> dict[str, Any] | None:
    """Its place for a city (or for an airport) of a country, by name: the first it answers that is in that country.
    For an airport, the one whose name shares most words with the airport's (Istanbul has two)."""
    written = OBILET_COUNTRIES[country]
    country_of = lambda item: plain(item["text"].split(" - ")[-1].replace("ı", "i"))   # Turkish ı is not an accented i: plain() drops it
    in_country = lambda item: item.get("isDomestic") if not written else written in country_of(item)
    kept = lambda items: [item for item in items if in_country(item) and bool(OBILET_AIRPORT.search(plain(item["text"]))) == airport]
    for name in (name for name in names if plain(name)):   # a name in another alphabet (Ρέθυμνο) it answers at random
        found = kept(obilet_places(name))
        if found and airport:
            # The one whose name shares most words with the airport's, and at least one: Rome has two airports
            words = obilet_words(" ".join(names))
            score = lambda item: len(words & obilet_words(item["text"]))
            best = max(found, key=lambda item: (score(item), -len(item["text"])))
            if score(best):
                return best
            continue
        if found:
            # A district answers for its city ("Agia Triada Rethymno - Resmo, Yunanistan"): the city itself is meant
            parent = found[0]["text"].split(" - ")[-1].split(",")[0] if "," in found[0]["text"].split(" - ")[-1] else ""
            city = [item for item in kept(obilet_places(parent)) if plain(item["display"]) == plain(parent)] if parent else []
            return city[0] if city else found[0]
    return None

def obilet_rides(city: Place, airport: dict[str, Any], day: str, to_airport: bool = True) -> list[dict[str, Any]] | None:
    """Every coach it sells between a city and an airport of the countries above on one day (yyyy-mm-dd), with its
    price in EUR. From the airport's own place first; when it has none, or nothing leaves from it, from the coach
    station of the airport's city, with the way between the two estimated. Returns None when it does not know the
    city, or neither the airport nor the airport's city."""
    from trip_planner.itineraries.legs import ROAD_FACTOR   # legs.py reads this module: imported when used
    from trip_planner.places import geocode
    if airport["country"] not in OBILET_COUNTRIES:
        return None
    town = obilet_place([city.local_name, city.english_name], airport["country"], False)
    if not town:
        return None
    # The airport's city: the first word of its name (Skopje's airport is in Petrovec, Athens's in Spata), then the
    # database's. It answers a name it does not know with the nearest it does (Spata is "Sparta", 150 km away): the
    # database's small town is only taken when its answer starts like it. The first word is a city it knows under
    # its own name (Athens is "Atina").
    first, municipality = airport["name"].split()[0], airport["city"]
    tries = []
    station = obilet_place([airport["name"], first, municipality], airport["country"], True)
    if station:
        tries.append((station, 0.0))
    for name, checked in ((first, False), (municipality, True)):
        place = obilet_place([name], airport["country"], False)
        if not place or (checked and plain(place["display"])[:4] != plain(name)[:4]):
            continue
        centre = geocode(name)
        km = distance_km(airport["latitude"], airport["longitude"], centre.latitude, centre.longitude) * ROAD_FACTOR
        if km <= current.SETTINGS.max_estimate_km and place["id"] not in (other["id"] for other, _ in tries):
            tries.append((place, km))
    tries = [(station, km) for station, km in tries if station["id"] != town["id"]]
    for station, km in tries:
        rides = obilet_search(town, station, km, airport, day, to_airport, (city.local_name, city.english_name))
        if rides:
            return rides
    return [] if tries else None

def obilet_search(town: dict[str, Any], station: dict[str, Any], stretch_km: float, airport: dict[str, Any], day: str,
                  to_airport: bool, names: tuple = ()) -> list[dict[str, Any]]:
    """Its journeys between the traveller's town and a place at the airport's end, as rides. stretch_km is the way,
    in no timetable, between that place and the airport: 0 when the place is the airport. names are the town's own
    names, to know its stops."""
    from trip_planner.itineraries.legs import ROAD_FACTOR, estimated_minutes, estimated_price
    start, end = (town, station) if to_airport else (station, town)
    page = f"{OBILET}/seferler/{start['id']}-{end['id']}/{day}"
    if not OBILET_SESSION.get("opened"):
        obilet_ask("GET", page)   # the page of a search opens the session its journeys are given in
        OBILET_SESSION["opened"] = True
    found = obilet_ask("POST", f"{OBILET}/json/journeys/{start['id']}-{end['id']}/{day}", json={}, headers={"Referer": page}).json()
    town_names = [plain(name) for name in (town["display"], *names) if plain(name)]
    airport_words = obilet_words(f"{airport.get('name', '')} {airport.get('city', '')} {airport['code']}")
    stretch_minutes = estimated_minutes(stretch_km / ROAD_FACTOR) + 20 if stretch_km else 0   # 20: to find the bus or taxi
    stretch_price = estimated_price(stretch_km / ROAD_FACTOR, airport["country"]) if stretch_km else 0.0
    rides = []
    for item in found.get("journeys") or []:
        journey = item.get("journey") or {}
        departure, arrival = journey.get("departure") or "", journey.get("arrival") or ""
        # Its own price in EUR (sorting-price) where the European Central Bank has no rate: dinars, denars, leks
        price = in_eur(float(journey.get("internet-price") or 0), journey.get("currency") or "EUR") or journey.get("sorting-price")
        if departure[:10] != day or not price or not item.get("is-active", True) or not item.get("available-seats", 1):
            continue
        at_airport = journey.get("destination" if to_airport else "origin") or ""
        # A stop of this very airport needs no estimated way to it, whatever place was asked
        at_this_airport = bool(OBILET_AIRPORT.search(plain(at_airport))) and bool(obilet_words(at_airport) & airport_words)
        if not stretch_km and not OBILET_AIRPORT.search(plain(at_airport)):
            continue   # a place of an airport also answers with stops of its district (Sabiha Gökçen with Ataşehir)
        at_town = plain(journey.get("origin" if to_airport else "destination") or "")
        if not (any(name[:5] in at_town for name in town_names) or OBILET_STATION.search(at_town)):
            continue   # a town also answers with stops of the towns around it (Nafplion with Nemea, 45 km away)
        leave, arrive = to_minutes(departure[11:16]), to_minutes(arrival[11:16]) + (24 * 60 if arrival[:10] > day else 0)
        company = item.get("partner-name") or "Obilet"
        steps = [{"time": departure[11:16], "arrive": arrival[11:16], "line": company, "agency": company, "from": journey.get("origin") or start["display"],
                  "to": journey.get("destination") or end["display"], "rail": journey.get("kind") == "Train", "coach": journey.get("kind") != "Train",
                  "km": airport["km"]}]
        ride = {"grade": "A", "source": "Obilet", "service_day": day, "leave": leave, "arrive": arrive, "price_eur": price, "urls": [page]}
        if stretch_km and not at_this_airport:
            # The estimated way at the airport's end: after the coach towards the airport, before it from the airport
            code, coach_end = airport["code"], at_airport or station["display"]
            at = arrive if to_airport else leave - stretch_minutes
            estimated = {"time": to_time(at), "arrive": to_time(at + stretch_minutes), "line": tr("estimated_stretch"), "agency": "",
                         "from": coach_end if to_airport else code, "to": code if to_airport else coach_end, "rail": False, "coach": False,
                         "km": round(stretch_km), "estimated": True, "minutes": stretch_minutes}
            steps = [*steps, estimated] if to_airport else [estimated, *steps]
            ride.update(grade="C", leave=leave if to_airport else at, arrive=at + stretch_minutes if to_airport else arrive,
                        price_eur=round(price + stretch_price, 2), confirmed_eur=price, estimated_km=round(stretch_km),
                        notes=[tr("note_estimated_stretch", km=round(stretch_km), start=estimated["from"], end=estimated["to"])])
        rides.append({**ride, "steps": steps})
    unique = {(ride["leave"], ride["arrive"], ride["steps"][0]["agency"]): ride for ride in rides}
    return sorted(unique.values(), key=lambda ride: ride["leave"])
