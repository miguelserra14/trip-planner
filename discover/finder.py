"""From a city to the cheapest flight to each destination: the departure airports around it, every source asked for
each of them, and the cheapest way to each destination, the way to and from the airports included (access.py).
Every request is kept, with what it found or why it failed, for the breakdown of what was searched (breakdown.py).
The cheapest destinations are then checked in the real timetables (confirm.py)."""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from discover import access, flown, transfers
from discover import rides as operator_rides
from discover.routes import known_routes, route_counts
from discover.sources import Days, kiwi_destinations, ryanair_destinations
from trip_planner.places import airports_near, distance_km, load_airports, Place
from trip_planner.sources import kiwi
from trip_planner.utils import say

RADIUS_KM = 200          # departure airports this close to the city...
# ... the ones with the most routes: the closest ones left Charleroi, Ryanair's base in Belgium, out of a map of
# Antwerp for Antwerp's own airport, which has a handful of flights (2026-10-22). Asking Ryanair costs one request
# per airport and Kiwi is asked for all of them at once, so more airports cost little; the way to reach each is in
# the total, so a farther one only wins when its flight saves more than the way there costs.
MAX_AIRPORTS = 6
MAX_CANDIDATES = 10      # airports whose routes of today are read to choose them: an article read for each
# The destinations flown from the traveller's airports (flown.py) that no answer gave are asked of Kiwi once more,
# by name: this many at a time (Kiwi answers 15, one per city), in at most GAP_REQUESTS requests, counted in the
# day's limit as the others
GAP_BATCH = 15
GAP_REQUESTS = 8
COUNTRIES_KM = 3500      # Kiwi is asked for the countries with a large airport this close to the city: Europe and around it
# Kiwi gives at most 15 flights per request. Asked with every airline, Ryanair filled most of them (77% of the ones to
# Germany, on 2026-10-20) and the others were left out: London by easyJet, Paris, Frankfurt, Istanbul. Ryanair's own
# answer has all its direct flights, so Kiwi is asked for the other airlines only, from the airports where Ryanair
# answered. And itineraries with two stops or more filled the answers for the distant countries (none of the 19 to
# Turkey was direct): direct flights only, by default, as Ryanair's; --max-stops allows connections.
KIWI_EXCLUDED = "FR"
MAX_STOPS = 0
# Kiwi refused this program for weeks once, for asking too much, and the planner needs it. So a map asks it at most
# this many times, a couple at a time with a pause: the countries with the most large airports first. Each request
# is for one country from all the traveller's airports at once: Kiwi takes several airports to fly from and answers
# with the cheapest flight to each city from any of them (tried on 2026-10-22, from BRU, CRL, EIN and ANR). Asked
# airport by airport, the same requests covered a third of the countries.
KIWI_REQUESTS = 60
KIWI_LIGHT_REQUESTS = 20   # --light: the countries with most known routes only
KIWI_AT_ONCE = 2
KIWI_PAUSE_SECONDS = 1.0
# ... and every map of one day together at most this many times, counted in this file. The planner's own requests
# are not counted here: the limit leaves room for them.
KIWI_DAILY = 150
KIWI_COUNT = Path(__file__).parent / "cache" / "kiwi_requests.json"

@dataclass
class Found:
    """What a map was made from."""
    place: Place
    days: Days
    departures: list[dict[str, Any]]                 # the traveller's airports, with "ground_eur": the estimate to reach each
    destinations: list[dict[str, Any]]               # the cheapest way to each city, cheapest first (one_per_city)
    flights: list[dict[str, Any]]                    # every flight any source answered
    searches: list[dict[str, Any]]                   # every request: {"source", "from", "to", "answered", "error", "flights"}
    countries_left_out: list[str] = field(default_factory=list)   # countries Kiwi was not asked about: beyond kiwi_allowed
    kiwi_options: str = ""                           # how Kiwi was asked, in words
    kiwi: bool = True                                # whether Kiwi was asked at all
    kiwi_allowed: int = KIWI_REQUESTS                # the requests to Kiwi this map could make: its limit, or what was left of the day's
    kiwi_refused: str = ""                           # what Kiwi said when it refused a request: the ones after it were not sent
    centres: dict[str, Place] = field(default_factory=dict)        # the centre of the city of each destination airport
    rides: dict[tuple, list] = field(default_factory=dict)         # the operators' rides on the traveller's side (rides.py)
    flown: dict[str, dict] = field(default_factory=dict)          # the routes flown today from the traveller's airports (flown.py)
    coverage: dict[str, Any] = field(default_factory=dict)        # how many of the destinations flown the answers gave (flown.coverage)
    discarded: list[dict[str, Any]] = field(default_factory=list)  # ways the timetables showed cannot be made, with "reason"
    lookups: list[dict[str, Any]] = field(default_factory=list)    # every city looked up in the timetables (confirm.py)
    confirm_stop: str = ""                           # why the lookups stopped, in words; empty while they run or before

def kiwi_used(today: str) -> int:
    """The requests to Kiwi the maps made today."""
    try:
        saved = json.loads(KIWI_COUNT.read_text(encoding="utf-8"))
        return int(saved["requests"]) if saved.get("day") == today else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0

def count_kiwi(today: str) -> None:
    """One more request to Kiwi today. Counted before it is sent, so a map stopped halfway still counts it."""
    used = kiwi_used(today) + 1
    KIWI_COUNT.parent.mkdir(exist_ok=True)
    KIWI_COUNT.write_text(json.dumps({"day": today, "requests": used}), encoding="utf-8")

def countries_near(place: Place, departures: set, km: float = COUNTRIES_KM) -> list[str]:
    """The two-letter codes of the countries with a large airport within km of a place. First the ones with most
    routes known from the departure airports (routes.py), then the ones with most large airports."""
    airports = load_airports()
    large: dict[str, int] = {}
    for airport in airports.values():
        if airport["large"] and distance_km(place.latitude, place.longitude, airport["latitude"], airport["longitude"]) <= km:
            large[airport["country"]] = large.get(airport["country"], 0) + 1
    routes: dict[str, int] = {}
    for code in known_routes(departures, airports) or {}:
        routes[airports[code]["country"]] = routes.get(airports[code]["country"], 0) + 1
    return sorted(large, key=lambda country: (-routes.get(country, 0), -large[country], country))

def cheapest_by_destination(flights: list[dict[str, Any]], getting_there: dict[str, float],
                            getting_home: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """The cheapest way to each destination: the flights plus the price of reaching the departure airport, and of
    getting home from the airport the return lands at (getting_home, the same as getting_there when not given).
    The traveller's own airports are not destinations, and a return that lands at none of them is left out."""
    getting_home = getting_there if getting_home is None else getting_home
    best: dict[str, dict[str, Any]] = {}
    for flight in flights:
        back = flight.get("back")
        if flight["to"] in getting_there or flight["from"] not in getting_there or (back and back["to"] not in getting_home):
            continue
        ground = getting_there[flight["from"]] + (getting_home[back["to"]] if back else 0.0)
        total = flight["price_eur"] + ground
        if flight["to"] not in best or total < best[flight["to"]]["total_eur"]:
            best[flight["to"]] = {**flight, "ground_eur": ground, "total_eur": round(total, 2)}
    return sorted(best.values(), key=lambda flight: flight["total_eur"])

def with_access(flight: dict[str, Any], home: Place, centres: dict[str, Place], today: date,
                rides: dict[tuple, list] | None = None) -> dict[str, Any]:
    """A flight with the ways to and from its airports (access.py) under "access", their sum under "ground_eur" and
    the whole trip under "total_eur". Each way is the best known about it: what was looked up for this flight is
    kept (and so is a way found to be covered by no timetable), the others are taken again from access.py, so a way
    remembered since replaces an estimate. rides are the operators' rides on the traveller's side (rides.py): the
    one that fits the flight is a real price for that day."""
    back = flight.get("back")
    rides = rides or {}
    fitting = lambda code, day, to_airport, leg: operator_rides.best_ride(rides.get((code, day, to_airport)) or [], leg, to_airport)
    ends = [(flight["from"], True, home, home.written, fitting(flight["from"], flight["date"], True, flight)),
            (flight["to"], False, centres.get(flight["to"]), "", None)]
    if back:
        ends += [(back["from"], True, centres.get(back["from"]), "", None),
                 (back["to"], False, home, home.written, fitting(back["to"], back.get("arrival_date") or back["date"], False, back))]
    before = {(leg["airport"], leg["to_airport"]): leg for leg in flight.get("access") or [] if leg["looked_up"] or leg.get("checked")}
    legs = [before.get((code, to_airport)) or access.leg(code, to_airport, place, today, label, ride)
            for code, to_airport, place, label, ride in ends]
    ground = sum(leg["price_eur"] for leg in legs)
    return {**flight, "access": legs, "ground_eur": round(ground, 2), "total_eur": round(flight["price_eur"] + ground, 2)}

def options_of(destinations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every airport of every city on the map, each a destination of its own, as one_per_city was given them."""
    return [{key: value for key, value in flight.items() if key != "also"} for city in destinations for flight in [city, *city.get("also", [])]]

def city_of(code: str, airports: dict[str, Any], centres: dict[str, Place] | None = None) -> str:
    """The city an airport serves, as a key: its centre when it is known (Orly and Charles de Gaulle serve Paris),
    otherwise the city the airport database names, otherwise the airport itself."""
    if centres and code in centres:
        return access.place_key(centres[code])
    airport = airports.get(code) or {}
    return access.city_key(airport) if access.city_name(airport) else code

def one_per_city(destinations: list[dict[str, Any]], airports: dict[str, Any], centres: dict[str, Place] | None = None) -> list[dict[str, Any]]:
    """The cheapest airport of each city, with the other airports of that city, whole, in "also": Ryanair answers
    by airport and Kiwi by city, so London would otherwise be several dots. The cheapest counts the ways to and
    from the airports: an airport far out may have the cheaper flight and the dearer way into the city."""
    best: dict[str, dict[str, Any]] = {}
    for flight in sorted(destinations, key=lambda flight: flight["total_eur"]):
        city = city_of(flight["to"], airports, centres)
        if city in best:
            best[city]["also"].append(flight)
        else:
            best[city] = {**flight, "also": []}
    return list(best.values())

def departure_airports(place: Place, today: date | None = None, say=print, current: bool = True) -> list[dict[str, Any]]:
    """The traveller's airports: of the ones within RADIUS_KM with scheduled flights, the MAX_AIRPORTS with the most
    destinations, closest first. The destinations are the ones flown today (flown.py) for the MAX_CANDIDATES that
    had the most in 2014 (routes.py), the 2014 ones for an airport whose article could not be read. Without either,
    the closest airports."""
    near = airports_near(place, RADIUS_KM, airlines_only=True)
    old = route_counts() or {}
    candidates = sorted(near, key=lambda airport: (-old.get(airport["code"], 0), airport["km"]))[:MAX_CANDIDATES]
    read = flown.routes([airport["code"] for airport in candidates], today or date.today(), say) if current else {}
    now = flown.counts(flown.within(read, place, COUNTRIES_KM, load_airports()))   # the destinations a map looks for
    count = lambda airport: now.get(airport["code"], old.get(airport["code"], 0))
    chosen = sorted(candidates, key=lambda airport: (-count(airport), airport["km"]))[:MAX_AIRPORTS]
    return sorted(chosen, key=lambda airport: airport["km"])

def kiwi_groups(departures: list[dict[str, Any]], with_ryanair: set) -> list[tuple]:
    """The airports Kiwi is asked for together, with the airlines left out: Ryanair from the airports where its own
    answer came, every airline from the others. Each group is a request per country. [(codes, left out)]"""
    answered = [airport["code"] for airport in departures if airport["code"] in with_ryanair]
    others = [airport["code"] for airport in departures if airport["code"] not in with_ryanair]
    return [(codes, excluded) for codes, excluded in ((answered, KIWI_EXCLUDED), (others, "")) if codes]

def error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}".strip(": ")[:300]

async def discover(place: Place, days: Days, fast: bool = False, max_stops: int = MAX_STOPS,
                   kiwi_requests: int = KIWI_REQUESTS) -> Found:
    """The cheapest destinations from a place. fast asks Ryanair only. max_stops is the most stops each way Kiwi
    may give, kiwi_requests the most requests to Kiwi this map may make."""
    today = date.today()
    departures = await asyncio.to_thread(departure_airports, place, today, say)
    routes_flown = flown.within(await asyncio.to_thread(flown.routes, [airport["code"] for airport in departures], today, say),
                                place, COUNTRIES_KM, load_airports())
    if not departures:
        raise ValueError(f"No airport within {RADIUS_KM} km of {place.label}.")
    for airport in departures:
        airport["ground_eur"] = access.leg(airport["code"], True, place, today)["price_eur"]
        airport["home_eur"] = access.leg(airport["code"], False, place, today)["price_eur"]
    say(f"{place.label}: " + ", ".join(f"{airport['code']} ({airport['km']} km)" for airport in departures) + f"; {days}")
    searches: list[dict[str, Any]] = []

    async def asking(source: str, start: str, target: str, ask) -> list[dict[str, Any]]:
        """One request, kept with its error: a failure, a refusal and an answer the code cannot read are told apart."""
        try:
            found, error = await ask(), ""
        except Exception as problem:
            found, error = None, error_text(problem)
        searches.append({"source": source, "from": start, "to": target, "answered": found is not None, "error": error,
                         "flights": found or []})
        return found or []

    # Ryanair first, while Kiwi connects: Kiwi leaves Ryanair out only from the airports where Ryanair answered, or
    # Ryanair's destinations would be missing from the map when its own request failed.
    ryanair = asyncio.gather(*[asking("Ryanair", airport["code"], "all its destinations",
                                      lambda code=airport["code"]: asyncio.to_thread(ryanair_destinations, code, days))
                               for airport in departures])
    answers = await ryanair if fast else (await asyncio.gather(ryanair, kiwi.connect_kiwi()))[0]
    flights = [flight for answer in answers for flight in answer]
    with_ryanair = {search["from"] for search in searches if search["source"] == "Ryanair" and search["answered"]}

    countries, left_out, allowed, refusal, asked_by_name = [], [], 0, [], set()
    if not fast:
        day = today.isoformat()
        allowed = max(0, min(kiwi_requests, KIWI_DAILY - kiwi_used(day)))
        if kiwi.kiwi_search is None:
            say("Kiwi is not available: only Ryanair's destinations are on the map.")
        else:
            near = countries_near(place, {airport["code"] for airport in departures})
            groups = kiwi_groups(departures, with_ryanair)
            each = allowed // len(groups)
            countries, left_out = near[:each], near[each:]
            if countries:
                say(f"Kiwi: {len(countries)} countries from {' and '.join('+'.join(codes) for codes, _ in groups)}, "
                    f"{len(countries) * len(groups)} requests")
            else:
                say(f"Kiwi was asked {KIWI_DAILY} times today already: only Ryanair's destinations are on the map.")
            turn = asyncio.Semaphore(KIWI_AT_ONCE)

            async def politely(codes: str, country: str, excluded: str) -> list[dict[str, Any]]:
                async with turn:
                    # Once Kiwi refused, asking again only makes it refuse for longer, the planner included
                    if refusal:
                        raise RuntimeError(f"not asked: Kiwi refused an earlier request ({refusal[0]})")
                    count_kiwi(day)
                    try:
                        return await kiwi_destinations(codes, country, days, max_stops, excluded)
                    except Exception as error:
                        if kiwi.refused(error):
                            refusal.append(error_text(error)[:120])
                        raise
                    finally:
                        await asyncio.sleep(KIWI_PAUSE_SECONDS)
            asked = [asking("Kiwi", ",".join(codes), country,
                            lambda codes=",".join(codes), country=country, excluded=excluded: politely(codes, country, excluded))
                     for codes, excluded in groups for country in countries]
            flights += [flight for answer in await asyncio.gather(*asked) for flight in answer]
            # The destinations flown from these airports that no answer gave: asked once more, by name, while the
            # day's requests allow it
            seen = flown.seen_codes(routes_flown, {flight["to"] for flight in flights}, load_airports())
            gaps = flown.missing(routes_flown, seen, with_ryanair)
            wanted = sorted(gaps, key=lambda code: -len(gaps[code]))
            batches = [wanted[start:start + GAP_BATCH] for start in range(0, len(wanted), GAP_BATCH)]
            room = max(0, min(GAP_REQUESTS, KIWI_DAILY - kiwi_used(day)))
            gap_asks = [(codes, excluded, batch) for batch in batches for codes, excluded in groups][:room]
            asked_by_name = {code for _, _, batch in gap_asks for code in batch}
            if gap_asks:
                say(f"Kiwi: {len(wanted)} destinations flown from these airports were in no answer, asked again by name "
                    f"({len(gap_asks)} requests)")
                asked = [asking("Kiwi", ",".join(codes), "flown: " + ",".join(batch),
                                lambda codes=",".join(codes), batch=",".join(batch), excluded=excluded: politely(codes, batch, excluded))
                         for codes, excluded, batch in gap_asks]
                flights += [flight for answer in await asyncio.gather(*asked) for flight in answer]
    if refusal:
        say(f"Kiwi refused a request ({refusal[0]}): the ones after it were not sent.")
    unanswered = sum(1 for search in searches if not search["answered"])
    say(f"{len(flights)} flights found" + (f", {unanswered} of {len(searches)} requests got no answer" if unanswered else ""))
    getting_there = {airport["code"]: airport["ground_eur"] for airport in departures}
    getting_home = {airport["code"]: airport["home_eur"] for airport in departures}
    stops = "direct flights only" if not max_stops else f"at most {max_stops} stop(s) each way"
    without = [airport["code"] for airport in departures if airport["code"] not in with_ryanair]
    options = (f"every airline but {KIWI_EXCLUDED}"
               + (f" ({KIWI_EXCLUDED} too from {', '.join(without)}, where Ryanair did not answer)" if without else "")
               + f", {stops}, one flight per city, the cheapest {kiwi.RESULT_CAP}")
    cheapest = cheapest_by_destination(flights, getting_there, getting_home)
    airports = load_airports()
    by_price = sorted(cheapest, key=lambda flight: flight["total_eur"])   # the cheapest are the ones worth locating first
    codes = list(dict.fromkeys(code for flight in by_price for code in [flight["to"], *([flight["back"]["from"]] if flight.get("back") else [])]))
    centres = await asyncio.to_thread(access.centres, codes, airports, today, say)
    # The ways into the cities as published, the cheapest destinations first, and the operators' rides on the
    # traveller's side for the day of each flight: so the map opens with prices that have a source
    await asyncio.to_thread(transfers.look_up, codes, airports, centres, access.voyages(codes), today, say)
    found_rides = await asyncio.to_thread(operator_rides.look_up, place, departures, operator_rides.wanted_for(cheapest, departures), say)
    destinations = one_per_city([with_access(flight, place, centres, today, found_rides) for flight in cheapest], airports, centres)
    return Found(place, days, departures, destinations, flights, searches, left_out, kiwi=bool(countries),
                 kiwi_options=options if countries else "", kiwi_allowed=allowed, kiwi_refused=refusal[0] if refusal else "",
                 centres=centres, rides=found_rides,
                 coverage=flown.coverage(routes_flown, flown.seen_codes(routes_flown, {flight["to"] for flight in flights}, airports), with_ryanair,
                                         asked_by_name),
                 flown=routes_flown)
