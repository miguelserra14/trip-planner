"""The coaches of the operators that publish no timetable but sell to programs, asked city to city: Rede
Expressos, Portugal's national network, and Movelia, which sells Spain's coach companies (Damas, Comes, Monbus,
ALSA...: its trains are left out, the timetables have them, ground.py).

With no timetable, where they go is found by asking: from the traveller's city, when it is in their country, to
the cities of that country with SOLD_POPULATION people, farther than ground.MIN_KM and at most MAX_KM away, the
nearest MAX_CITIES, on one day DAYS_AHEAD ahead (ground.py's week starts there too). A city they sell a direct
coach to that day is reached, in the shape ground.reached gives, so that ground.py groups and shows it as any
other: its times are the ones of that day and its weekday that day's only (the coaches seen on sale, not a
timetable of the week), grade B. Kept ground.GROUND_DAYS, like ground.py's.

Its price on the days of the trip is asked again (day_price, from ground_fares.py), grade A, direct coaches only:
these operators give a change of coach without its station or its wait.
Not asked: Obilet (Turkey, the Balkans, Italy, Poland), whose search page answered 403 to code on 2026-11-09."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx

from discover.overland import ground
from trip_planner.sources.operators.base import NotOnSale
from trip_planner.sources.operators.movelia import movelia_places, movelia_search
from trip_planner.sources.operators.rede_expressos import (REDE_EXPRESSOS_CITY_KM, REDE_EXPRESSOS_STOPS, rede_expressos_schedules,
                                                           rede_expressos_stops, town_of)
from trip_planner.texts import tr
from trip_planner.trip.places import distance_km, Place
from trip_planner.utils import plain, say as print_line, write_whole

SOLD = Path(__file__).parents[1] / "cache" / "sold.json"   # the cities each operator was seen selling to, by traveller's city
SOLD_VERSION = 1           # what is kept changes: read again
SOLD_POPULATION = 40_000   # a national network serves its district capitals: Faro, Évora and Viseu have fewer than ground.MIN_POPULATION
MAX_KM = 800               # farther, within one country, a coach is not the way
MAX_CITIES = 30            # cities asked per operator: one search each, once every ground.GROUND_DAYS
AT_ONCE = 4                # searches at the same time, as FlixBus's (ground_fares.AT_ONCE)
FAILURES = (httpx.HTTPError, ValueError, KeyError, TypeError, NotOnSale)

def clock(text: str) -> datetime:
    return datetime.fromisoformat(text)

def ride(leaves: datetime, arrives: datetime, price: float, start: str, end: str, operator: str, direct: bool) -> dict[str, Any]:
    """One coach on sale, as both operators are read: its local times, price, stops and company."""
    return {"day": leaves.date().isoformat(), "leave": leaves.strftime("%H:%M"), "arrive": arrives.strftime("%H:%M"),
            "arrive_day": arrives.date().isoformat(), "minutes": round((arrives - leaves).total_seconds() / 60),
            "price_eur": round(price, 2), "from": start, "to": end, "operator": operator, "direct": direct}

class RedeExpressos:
    key, name, country, site = "rede-expressos", "Rede Expressos", "PT", "https://rede-expressos.pt/pt"

    def end(self, city: dict[str, Any]) -> dict[str, Any] | None:
        """Its stop nearest to a city's centre, not an airport's: {"id", "name", "lat", "lon"}."""
        near = rede_expressos_stops(city["lat"], city["lon"], REDE_EXPRESSOS_CITY_KM, False)
        return {"id": near[0]["id"], "name": near[0]["name"], "lat": near[0]["lat"], "lon": near[0]["lon"]} if near else None

    def rides(self, start: str, end: str, day: str) -> list[dict[str, Any]]:
        """Everything it sells on the day between the towns of the two stops, every company it sells for under its
        own name: it is the one sold from. A search also answers with the towns around (Santarém with Alcanena),
        which are left out, as rede_expressos_rides does."""
        towns = {stop["id"]: town_of(stop["name"]) for stop in REDE_EXPRESSOS_STOPS if stop["id"] in (start, end)}
        found = []
        for each in rede_expressos_schedules(start, end, day):
            leaves, reaches = (each.get("from") or {}).get("name", ""), (each.get("to") or {}).get("name", "")
            if (each.get("minPrice") and each.get("availableSeats", 1)
                    and town_of(leaves or "?") == towns.get(start) and town_of(reaches or "?") == towns.get(end)):
                found.append(ride(clock(each["departureDate"]), clock(each["arrivalDate"]), float(each["minPrice"]),
                                  leaves, reaches, self.name, not each.get("transfersCount")))
        return found

class Movelia:
    key, name, country, site = "movelia", "Movelia", "ES", "https://www.movelia.es/es/app"
    COACH = "1"   # its kind of vehicle for a coach
    NAMES = {"nex continental holdings": "ALSA"}   # companies it gives by their legal name: the one travellers know

    def end(self, city: dict[str, Any]) -> dict[str, Any] | None:
        """Its place of a city, by the city's name: {"id" (the name it searches by), "name", "lat", "lon"}."""
        found = movelia_places().get(plain(city["name"]))
        return {"id": found, "name": city["name"], "lat": city["lat"], "lon": city["lon"]} if found else None

    def rides(self, start: str, end: str, day: str) -> list[dict[str, Any]]:
        """Its coaches of the day, each under its company's name. ALSA's are also in the timetables (ground.py): the
        map keeps the quickest of the two, as for any operator found twice."""
        try:
            combinations = movelia_search(start, end, day)
        except NotOnSale:
            return []
        found = []
        for each in combinations:
            company = ground.COMPANY.sub("", (each.get("companyName") or "").strip())
            company = next((shown for start_of, shown in self.NAMES.items() if plain(company).startswith(start_of)), company.title())
            if each.get("busOrBoat") != self.COACH or each.get("salable") != "1" or not each.get("basicPrice"):
                continue
            moment = lambda written, time: datetime.strptime(f"{written} {time}", "%d/%m/%Y %H:%M")
            found.append(ride(moment(each["departureDate"], each["departureTime"]), moment(each["arrivalDate"], each["arrivalTime"]),
                              float(each["basicPrice"].replace(",", ".")), (each.get("originName") or start).title(),
                              (each.get("destinationName") or end).title(), company, each.get("direct") == "1"))
        return found

OPERATORS = {operator.key: operator for operator in (RedeExpressos(), Movelia())}

def read() -> dict[str, Any]:
    try:
        found = json.loads(SOLD.read_text(encoding="utf-8"))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}

def home_of(place: Place) -> dict[str, Any]:
    """The traveller's city as GeoNames has it, at the place's own position: its country tells who is asked."""
    city = ground.city_of({"lat": place.latitude, "lon": place.longitude}, ground.city_grid(ground.cities())) or {}
    return {**city, "name": city.get("name") or place.local_name, "lat": place.latitude, "lon": place.longitude}

def candidates(home: dict[str, Any], country: str) -> list[dict[str, Any]]:
    """The cities of the country asked: SOLD_POPULATION people, between ground.MIN_KM and MAX_KM, the nearest MAX_CITIES."""
    away = lambda city: distance_km(home["lat"], home["lon"], city["lat"], city["lon"])
    found = [city for city in ground.cities() if city["country"] == country and city["population"] >= SOLD_POPULATION
             and ground.MIN_KM < away(city) <= MAX_KM]
    return sorted(found, key=away)[:MAX_CITIES]

def reached(operator: Any, start: dict[str, Any], end: dict[str, Any], rides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The city reached by each company's direct coaches of the day, as ground.reached gives a station: the
    quickest one's minutes and stop, every one's time. "source" and "ends" say whom to ask its price, and between what."""
    by_company: dict[str, list[dict[str, Any]]] = {}
    for found in rides:
        if found["direct"]:
            by_company.setdefault(found["operator"], []).append(found)
    stops = []
    for company, found in by_company.items():
        quickest = min(found, key=lambda each: each["minutes"])
        stops.append({"stop": quickest["to"] or end["name"], "stop_id": f"{operator.key}:{end['id']}", "lat": end["lat"], "lon": end["lon"],
                      "minutes": quickest["minutes"], "from_stop": quickest["from"] or start["name"], "from_stop_id": f"{operator.key}:{start['id']}",
                      "from_lat": start["lat"], "from_lon": start["lon"], "operator": company, "site": operator.site,
                      "sold": operator.country, "modes": "COACH", "mode": "bus", "times": sorted({each["leave"] for each in found}),
                      "weekdays": [ground.WEEKDAYS[date.fromisoformat(quickest["day"]).weekday()]], "last": True,
                      "source": operator.key, "ends": [start["id"], end["id"]]})
    return stops

def asked(operator: Any, home: dict[str, Any], day: str, say: Callable) -> list[dict[str, Any]]:
    """Where one operator's direct coaches go from the traveller's city on the day."""
    start = operator.end(home)
    if not start:
        return []
    ends = [end for end in map(operator.end, candidates(home, operator.country)) if end and end["id"] != start["id"]]
    ends = list({end["id"]: end for end in ends}.values())   # two towns of one stop (a suburb) are asked once
    say(tr("discover_say_sold_reading", operator=operator.name, place=home["name"], count=len(ends), days=ground.GROUND_DAYS))

    def one(end: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return reached(operator, start, end, operator.rides(start["id"], end["id"], day))
        except FAILURES:
            return []   # that city only: the others are still asked
    with ThreadPoolExecutor(AT_ONCE) as pool:
        return [stop for found in pool.map(one, ends) for stop in found]

def from_city(place: Place, today: date, say: Callable = print_line) -> list[dict[str, Any]]:
    """Every city these operators sell a direct coach to from the traveller's city, kept ground.GROUND_DAYS. Empty
    outside their countries, with nothing asked."""
    home = home_of(place)
    selling = [operator for operator in OPERATORS.values() if operator.country == home.get("country")]
    if not selling:
        return []
    known, key = read(), ground.place_key(place)
    kept = known.get(key)
    if (kept and kept.get("version") == SOLD_VERSION
            and date.fromisoformat(kept["checked"]) >= today - timedelta(days=ground.GROUND_DAYS)):
        return kept["reached"]
    day = (today + timedelta(days=ground.DAYS_AHEAD)).isoformat()
    stops = []
    for operator in selling:
        try:
            stops += asked(operator, home, day, say)
        except FAILURES as error:   # its places could not be had: no coach of it, and not kept, to ask again next time
            say(tr("discover_say_sold_failed", operator=operator.name, error=type(error).__name__))
            return stops
    known[key] = {"checked": today.isoformat(), "place": place.label, "version": SOLD_VERSION, "reached": stops}
    write_whole(SOLD, json.dumps(known, ensure_ascii=False, indent=1))
    return stops

def stations_reached(place: Place, today: date, say: Callable = print_line) -> list[dict[str, Any]] | None:
    """ground.stations_reached and these operators' coaches, asked at the same time: they ask different services.
    None only when Transitous failed and these have nothing either."""
    with ThreadPoolExecutor(2) as pool:
        timetables = pool.submit(ground.stations_reached, place, today, say)
        coaches = pool.submit(from_city, place, today, say)
        stops, more = timetables.result(), coaches.result()
    return None if stops is None and not more else (stops or []) + more

def day_price(option: dict[str, Any], back: bool, day: str) -> dict[str, Any] | None:
    """The cheapest direct coach of the option's company on a day, as ground_fares.py gives a price; None when
    none is sold that day."""
    operator = OPERATORS[option["source"]]
    start, end = option["ends"][::-1] if back else option["ends"]
    found = [each for each in operator.rides(start, end, day)
             if each["direct"] and each["operator"] == option["operator"] and each["day"] == day]
    if not found:
        return None
    best = min(found, key=lambda each: each["price_eur"])
    return {"price_eur": best["price_eur"], "day": day, "leave": best["leave"], "arrive": best["arrive"], "arrive_day": best["arrive_day"],
            "seller": operator.name, "grade": "A", "url": operator.site, "from_stop": best["from"], "to_stop": best["to"], "changes": []}

def is_sold(option: dict[str, Any]) -> bool:
    return option.get("source") in OPERATORS
