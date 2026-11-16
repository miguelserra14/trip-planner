"""Asks the real services what a map asks them, once per case, and saves their answers as checkpoints: the
tests replay them through the map's own code, with no network. `pytest --live discover -k Coimbra` records
one case again. Only the fields the code reads are kept, so a checkpoint stays small enough for the repository.

Five kinds of case:
- ground: the trains and buses straight from a city (ground.from_city), every request to Transitous it made;
- sold: the coaches Rede Expressos or Movelia sell straight from a city (sold.from_city), every search it made and
  the GeoNames cities it asked about;
- ryanair: the cheapest fare to each destination from an airport, one way (sources.ryanair_destinations);
- flown: the routes flown from an airport (flown.routes), its Wikipedia table as Tavily read it and Wikidata's answers;
- wikidata: the cities Wikidata says some airports serve (access.ask_wikidata)."""
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from discover.airports import access
from discover.flights import flown, sources
from discover.overland import ground, sold
from trip_planner.trip.places import distance_km
from trip_planner.utils import plain

CHECKPOINTS = Path(__file__).parent / "checkpoints"
RYANAIR_DAYS_AHEAD = 30   # the fares of a month ahead: on sale, and not yet the last seats
RYANAIR_DAYS = 3          # days in a row asked, as a map asks for the days the traveller ticked

REAL_GET = ground.Transitous.get   # read on import, before the tests' fixture replaces them with empty answers
REAL_CITIES = ground.cities

def checkpoint_file(kind: str, name: str) -> Path:
    return CHECKPOINTS / f"{kind}_{name}.json"

def load(kind: str, name: str) -> dict[str, Any] | None:
    path = checkpoint_file(kind, name)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

def save(kind: str, name: str, record: dict[str, Any]) -> None:
    """One line per key, and one per answer: a change of a service shows as the lines of the requests it touched."""
    lines = []
    for key, value in record.items():
        if key == "answers":
            inner = ",\n".join(f"  {json.dumps(answer, ensure_ascii=False)}" for answer in value)
            lines.append(f"{json.dumps(key)}: [\n{inner}\n]")
        else:
            lines.append(f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    CHECKPOINTS.mkdir(exist_ok=True)
    checkpoint_file(kind, name).write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")

def pick(found: dict[str, Any] | None, keys: tuple) -> dict[str, Any]:
    return {key: found[key] for key in keys if found and key in found}

# What ground.py reads of each answer of Transitous (patterns, reached, Transitous.stops)
STOP_KEYS = ("name", "stopId", "lat", "lon", "modes")
CALL_KEYS = ("name", "stopId", "lat", "lon", "tz", "departure", "scheduledDeparture", "arrival", "scheduledArrival",
             "pickupType", "dropoffType")
STATION_KEYS = ("name", "stopId", "lat", "lon", "tz")
ROUTE_KEYS = ("agencyName", "mode", "headsign", "routeShortName")
LOCAL_MODES = {"BUS", "TRAM", "SUBWAY", "METRO", "FERRY", "CABLE_CAR", "FUNICULAR", "AERIAL_LIFT", "ODM", "OTHER"}

def expanded(path: str, answer: Any) -> Any:
    """An answer as Transitous gave it, from the way it is saved (trimmed)."""
    if path != "stoptimes":
        return answer
    departures = []
    for route, trip_id, leaves, *rest in answer["rows"]:
        place = {**answer["stop"], **(rest[0] if rest else {})}
        if leaves:
            place["departure"] = leaves
        departures.append({**dict(zip(ROUTE_KEYS, answer["routes"][route])), "tripId": trip_id, "place": place})
    return {"stopTimes": departures}

def trimmed(path: str, answer: Any) -> Any:
    if path == "map/stops":
        # The city's bus, tram and metro stops are hundreds, and Transitous.stops never takes them: left out. Every
        # train station is kept, regional ones too, so a change of the modes read can be replayed
        local = lambda stop: set(stop.get("modes") or []) <= LOCAL_MODES and not stop.get("stopId", "").startswith(ground.COACH_FEEDS)
        return [pick(stop, STOP_KEYS) for stop in answer if not local(stop)]
    if path == "stoptimes":
        # A station has a thousand departures in a week, of a few dozen trains and buses under the same stop: the
        # stop is kept once, each route (operator, mode, headsign) once, and each departure as [route, trip id, when,
        # what else differs]. patterns() reads a trip id only from the first departure of a route a traveller can
        # board, the others are "".
        departures = answer.get("stopTimes") or []
        stop = pick(departures[0].get("place"), STATION_KEYS) if departures else {}
        routes: list[list] = []
        boarded: list[list] = []
        rows = []
        for each in departures:
            route = [each.get(key, "") for key in ROUTE_KEYS]
            if route not in routes:
                routes.append(route)
            place = each.get("place") or {}
            first = route not in boarded and place.get("departure") and place.get("pickupType") != "NOT_ALLOWED"
            if first:
                boarded.append(route)
            other = {key: place[key] for key in (*STATION_KEYS, "pickupType") if key in place and stop.get(key) != place[key]
                     and not (key == "pickupType" and place[key] == "NORMAL")}
            rows.append([routes.index(route), each.get("tripId", "") if first else "", place.get("departure"), *([other] if other else [])])
        return {"stop": stop, "routes": routes, "rows": rows}
    if path == "trip":
        return {"legs": [{**pick(leg, ("agencyName", "agencyUrl", "mode")), "from": pick(leg.get("from"), CALL_KEYS),
                          "to": pick(leg.get("to"), CALL_KEYS),
                          "intermediateStops": [pick(call, CALL_KEYS) for call in leg.get("intermediateStops") or []]}
                         for leg in answer.get("legs") or []]}
    raise ValueError(f"no trimming known for {path}")

class Recording(ground.Transitous):
    """The real Transitous, keeping each request and what it answered, trimmed."""

    def __init__(self):
        super().__init__()
        self.answers: list[list] = []

    def get(self, path: str, **params) -> Any:
        answer = trimmed(path, REAL_GET(self, path, **params))
        self.answers.append([path, params, answer])
        return expanded(path, answer)   # the code reads what the tests will replay, not more

class Replayed(ground.Transitous):
    """Transitous as it answered when the checkpoint was recorded. A request it was not asked then fails the test:
    the code asks something else now, and the case must be recorded again."""

    def __init__(self, answers: list[list]):
        self.answers = {(path, json.dumps(params, sort_keys=True)): answer for path, params, answer in answers}

    def get(self, path: str, **params) -> Any:
        key = (path, json.dumps(params, sort_keys=True))
        if key not in self.answers:
            raise AssertionError(f"not asked when recorded: {path} {params}. Record again with pytest --live")
        return expanded(path, self.answers[key])

def record_ground(case) -> dict[str, Any]:
    """The way out of one city, through the real Transitous: every answer, and what from_city made of them."""
    today = date.today()
    transitous = Recording()
    reached = ground.from_city(case.place, today, say=lambda *_: None, transitous=transitous)
    record = {"case": case.name, "recorded": today.isoformat(), "reached": reached, "answers": transitous.answers}
    save("ground", case.name, record)
    return record

def ryanair_fare(fare: dict[str, Any]) -> dict[str, Any]:
    flight = lambda leg: {**pick(leg, ("departureDate", "arrivalDate", "flightNumber")), "price": pick(leg.get("price"), ("value",)),
                          "arrivalAirport": pick(leg.get("arrivalAirport"), ("iataCode", "name")),
                          "departureAirport": pick(leg.get("departureAirport"), ("iataCode", "name"))}
    return {"outbound": flight(fare["outbound"]), **({"inbound": flight(fare["inbound"])} if fare.get("inbound") else {})}

def record_ryanair(case, monkeypatch) -> dict[str, Any]:
    """Ryanair's fare finder from one airport, through the real site: its answer, trimmed, and the days asked."""
    first = date.today() + timedelta(days=RYANAIR_DAYS_AHEAD)
    days = sources.Days(first.isoformat(), (first + timedelta(days=RYANAIR_DAYS - 1)).isoformat())
    kept: dict[str, Any] = {}

    def keeping(*args, **kwargs):
        response = httpx.get(*args, **kwargs).raise_for_status()
        kept["fares"] = [ryanair_fare(fare) for fare in response.json().get("fares") or []]
        return Answer({"fares": kept["fares"]})
    monkeypatch.setattr(sources, "httpx", SimpleNamespace(get=keeping))   # this module only: httpx itself is left alone
    sources.ryanair_destinations(case.airport, days)
    record = {"case": case.name, "recorded": date.today().isoformat(), "days": [days.first, days.last], "answers": kept["fares"]}
    save("ryanair", case.name, record)
    return record

# What sold.py reads of each coach of the two operators (RedeExpressos.rides, Movelia.rides)
SOLD_KEYS = {"rede-expressos": ("minPrice", "availableSeats", "departureDate", "arrivalDate", "transfersCount"),
             "movelia": ("companyName", "busOrBoat", "salable", "basicPrice", "departureDate", "departureTime", "arrivalDate",
                         "arrivalTime", "originName", "destinationName", "direct")}
NOT_ON_SALE = "not on sale"   # saved in place of an answer when the operator said the day is not on sale yet
FAILED = "failed: "           # ... and this with the error's name when the search failed

def sold_trimmed(operator: str, rides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    named = lambda stop: {"name": (stop or {}).get("name", "")}
    return [{**pick(each, SOLD_KEYS[operator]), **({"from": named(each.get("from")), "to": named(each.get("to"))}
                                                    if operator == "rede-expressos" else {})} for each in rides]

def sold_cities(place, country: str) -> list[dict[str, Any]]:
    """The GeoNames cities sold.py can ask for from a place: GeoNames' file is a cache, not in the repository."""
    away = lambda city: distance_km(place.latitude, place.longitude, city["lat"], city["lon"])
    return [city for city in REAL_CITIES() if city["country"] == country
            and (city["population"] >= sold.SOLD_POPULATION and away(city) <= sold.MAX_KM or away(city) <= ground.CITY_KM)]

def record_sold(case, monkeypatch) -> dict[str, Any]:
    """The cities an operator sells a direct coach to from one city (sold.from_city), through its real site: every
    search and its answer, trimmed, and the cities it was asked about."""
    cities = sold_cities(case.place, case.country)
    monkeypatch.setattr(ground, "cities", lambda: cities)
    answers: list[list] = []

    def keeping(operator: str, search):
        def kept(start: str, end: str, day: str):
            try:
                found = sold_trimmed(operator, search(start, end, day))
            except sold.NotOnSale:
                answers.append([operator, [start, end, day], NOT_ON_SALE])
                raise
            except Exception as error:   # sold.py leaves that city out, and so must the replay
                answers.append([operator, [start, end, day], f"{FAILED}{type(error).__name__}"])
                raise
            answers.append([operator, [start, end, day], found])
            return found
        return kept
    monkeypatch.setattr(sold, "rede_expressos_schedules", keeping("rede-expressos", sold.rede_expressos_schedules))
    monkeypatch.setattr(sold, "movelia_search", keeping("movelia", sold.movelia_search))
    names = {plain(city["name"]) for city in cities}
    places = {key: value for key, value in sold.movelia_places().items() if key in names} if case.country == "ES" else {}
    today = date.today()
    reached = sold.from_city(case.place, today, say=lambda *_: None)
    record = {"case": case.name, "recorded": today.isoformat(), "cities": cities, "movelia_places": places, "reached": reached,
              "answers": sorted(answers, key=lambda answer: (answer[0], answer[1]))}   # searches run four at a time: in order
    save("sold", case.name, record)
    return record

def replay_sold(record: dict[str, Any], monkeypatch) -> None:
    """sold.py as it was answered when the checkpoint was recorded. A search it did not make then fails the test."""
    saved = {(operator, *asked): answer for operator, asked, answer in record["answers"]}

    def answering(operator: str):
        def answer(start: str, end: str, day: str):
            if (operator, start, end, day) not in saved:
                raise AssertionError(f"not asked when recorded: {operator} {start} -> {end} on {day}. Record again with pytest --live")
            if saved[operator, start, end, day] == NOT_ON_SALE:
                raise sold.NotOnSale
            if str(saved[operator, start, end, day]).startswith(FAILED):
                raise ValueError(saved[operator, start, end, day])   # one of sold.FAILURES, as the real one was
            return saved[operator, start, end, day]
        return answer
    monkeypatch.setattr(ground, "cities", lambda: record["cities"])
    monkeypatch.setattr(sold, "rede_expressos_schedules", answering("rede-expressos"))
    monkeypatch.setattr(sold, "movelia_search", answering("movelia"))
    monkeypatch.setattr(sold, "movelia_places", lambda: record["movelia_places"])

def article_table(text: str) -> str:
    """The part of a Wikipedia article flown.table_rows reads: from its "Airlines and destinations" heading to
    the end of the table. The whole article when there is no such table, so a test says what was read instead."""
    heading = text.find("Airlines and destinations")
    start = text.find("| Airlines", heading) if heading >= 0 else -1
    if start < 0:
        return text
    lines = text[start:].splitlines()
    end = next((at for at, line in enumerate(lines) if at >= 2 and not line.startswith("|")), len(lines))
    return text[heading:start] + "\n".join(lines[:end]) + "\n"

def keeping_sparql(monkeypatch) -> list[list]:
    """Wikidata's real answers, each [query, rows], kept as access.sparql makes them."""
    answers: list[list] = []
    real = access.sparql

    def kept(query: str) -> list[dict[str, str]]:
        rows = real(query)
        answers.append([" ".join(query.split()), rows])
        return rows
    monkeypatch.setattr(access, "sparql", kept)
    return answers

def replay_sparql(answers: list[list], monkeypatch) -> None:
    saved = {query: rows for query, rows in answers}

    def answer(query: str) -> list[dict[str, str]]:
        if " ".join(query.split()) not in saved:
            raise AssertionError(f"not asked when recorded: {' '.join(query.split())[:200]}. Record again with pytest --live")
        return saved[" ".join(query.split())]
    monkeypatch.setattr(access, "sparql", answer)

def record_flown(case, monkeypatch) -> dict[str, Any]:
    """The routes flown from one airport (flown.routes): its Wikipedia article as Tavily read it, cut to the table,
    and Wikidata's answers. One Tavily credit."""
    from discover.airports.transfers import tavily_extract
    answers = keeping_sparql(monkeypatch)
    articles: dict[str, str] = {}

    def extract(url: str) -> str:
        articles[url] = article_table(tavily_extract(url))
        return articles[url]
    today = date.today()
    found = flown.routes([case.airport], today, say=lambda *_: None, extract=extract)
    record = {"case": case.name, "recorded": today.isoformat(), "routes": found, "articles": articles, "answers": answers}
    save("flown", case.name, record)
    return record

def record_wikidata(case, monkeypatch) -> dict[str, Any]:
    """The cities Wikidata says some airports serve (access.ask_wikidata): one query."""
    answers = keeping_sparql(monkeypatch)
    access.ask_wikidata(sorted(case.serves))
    record = {"case": case.name, "recorded": date.today().isoformat(), "answers": answers}
    save("wikidata", case.name, record)
    return record

class Answer:
    """A response of httpx with a saved body, for code that calls raise_for_status and json."""

    def __init__(self, body: Any):
        self.body = body

    def raise_for_status(self) -> "Answer":
        return self

    def json(self) -> Any:
        return self.body
