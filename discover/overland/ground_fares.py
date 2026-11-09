"""The prices of the trains and buses of the map (ground.py) on the days of the trip, read by code from the
operators that sell to programs, with the planner's own code. Grade A: sold for that very day.
- FlixBus: one search per day between the two stations (its stops in the timetables carry FlixBus's own ids),
  which gives every ride of the day with its price; the cheapest direct one is kept.
- The operators the planner prices (fares.py: ÖBB, RegioJet, Trenitalia, MÁV, Elron, ALSA, Renfe and Ouigo): the
  direct departures of that day in the timetables, MAX_DEPARTURES of them asked of the operator.
The others (Deutsche Bahn, SNCF, Eurostar, BlaBlaCar Bus...) keep their timetable and no price. No model is asked.

FlixBus also sells trips with a change of coach, which the timetables do not give: the map takes them up to
TripSettings.max_coach_changes changes (0, straight only, unless the traveller chooses), each change said with its
station and its wait. The trains are priced straight only.

FlixBus's stations in the timetables are not always the ones it sells from (Antwerp to Heidelberg gives nothing
station to station): when they give no ride at all, FlixBus is asked city to city (TOWN_KM), the two cities found by
name and checked by position, and its own stations are shown. Still a direct ride only.

A price is for the ticket from station to station: getting to the station in the traveller's city, and from the
one reached, is not counted (the flights count the ways to and from their airports).

Asking is slow, so only the cities that can still change the map's cheapest are priced, as confirm.py looks up the
flights, in rounds of ROUND cities:
- a city not priced yet costs at least, for each operator that sells it, its distance times the cheapest price per
  km known for that operator over a trip at least as long, out and back for a return trip: the ones seen in this
  map, and the ones of the maps of the last RATES_DAYS (RATES); nothing when none is known (those come first, the
  longest first, so that every shorter one gets its least);
- it is priced when that least is below the dearest of the TOP cheapest of the map, flights and trains and buses
  together; a city a flight goes to, also only when it is below that flight's total.
The pricing stops when no city is left that can, or after MAX_PRICED cities. The one assumption, stated because the
result is only as sure as it: an operator's price per km does not rise with the distance. A price per km kept from
another map only chooses what is asked: it is never shown as a price.

FlixBus is asked for several cities at once (AT_ONCE), as its searches do not go through the timetables; the
operators asked departure by departure need the timetables, one request at a time, and go one after the other in
the meantime. While the flights are looked up in the timetables (confirm.py), the FlixBus searches the rounds will
likely want are asked already (prefetch): they are kept apart, and the rounds take them from there."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx

from discover.overland import ground, sold
from discover.confirm import TOP
from discover.finder import Found
from discover.flights.sources import Days
from trip_planner.sources import fares
from trip_planner.sources.operators.flixbus import flixbus_city, flixbus_price, flixbus_search
from trip_planner.sources.timetables.client import step_of
from trip_planner.texts import tr
from trip_planner.trip.places import distance_km, load_airports, Place
from trip_planner.utils import say as print_line, write_whole

MAX_PRICED = 30       # cities priced per map: each takes a few requests a day priced
ROUND = 6             # cities priced at once: the least of the others is worked out again after each round
MAX_DAYS = 4          # days priced each way: the first ones the traveller may leave, or come back, on
MAX_DEPARTURES = 3    # departures of a day asked of an operator that prices one departure at a time, spread over the day
AT_ONCE = 4           # FlixBus searches at the same time
# TripSettings.max_coach_changes of this map, set once as it starts (session.py, app/map_job.price_saved): read here,
# beside the timetables' lookups, which change current.SETTINGS meanwhile
MOST_CHANGES = 0
TOWN_KM = 25          # how far from a station the city FlixBus finds by its name may be (as flixbus.flixbus_rides)
FAILURES = (httpx.HTTPError, ValueError, KeyError, TypeError)   # an answer that fails or cannot be read: no price, as with no sale
RATES = Path(__file__).parents[1] / "cache" / "ground_rates.json"   # the prices per km seen, by operator: [km, EUR per km, day seen]
RATES_DAYS = 30       # a price per km seen longer ago than this no longer chooses what is asked
MAX_RATES = 300       # prices per km kept per operator, the newest

# FlixBus's answers of this map, by (from, to, day): what prefetch asked while the flights were looked up
FLIXBUS_ANSWERS: dict[tuple, dict[str, Any] | None] = {}
ANSWERS_LOCK = threading.Lock()

def days_of(days: Days, back: bool, today: date, after: str = "") -> list[str]:
    """The days priced for one way: the days clicked, or every day from the first, MAX_DAYS at most, none past and,
    for the way back, none before the day the way out leaves (after)."""
    chosen = list(days.back_days if back else days.out_days)
    if not chosen:
        first, last = (days.back_first, days.back_last) if back else (days.first, days.last)
        start, end = date.fromisoformat(first), date.fromisoformat(last)
        chosen = [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]
    return [day for day in chosen if day >= today.isoformat() and day >= after][:MAX_DAYS]

def flixbus_search_day(start_id: str, end_id: str, day: str) -> dict[str, Any] | None:
    """FlixBus's cheapest direct ride between two of its stations on a day, from one search; None when it sells none."""
    rides, stations = flixbus_search(start_id.split("_", 1)[1], end_id.split("_", 1)[1], day, "station")
    found = cheapest_ride(rides, day, stations)
    if found:
        del found["station_ids"]   # the timetable's own: the map names them already
    return found

def flixbus_town_day(towns: tuple, day: str) -> dict[str, Any] | None:
    """FlixBus's cheapest direct ride between two cities on a day, towns ((name, latitude, longitude) of each end),
    with the names of the stations it leaves from and reaches; None when it knows either city by none of its
    stops near there, or sells none."""
    ids = [flixbus_city(name, latitude, longitude, TOWN_KM) for name, latitude, longitude in towns]
    if not all(ids) or ids[0] == ids[1]:
        return None
    rides, stations = flixbus_search(ids[0], ids[1], day, "city")
    found = cheapest_ride(rides, day, stations)
    if found:
        leaves, arrives = found.pop("station_ids")
        found["from_stop"], found["to_stop"] = stations.get(leaves, ""), stations.get(arrives, "")
    return found

def changes_of(ride: dict[str, Any]) -> int:
    """The changes of coach of a FlixBus ride: one fewer than its legs (a direct one has one, or none listed)."""
    return max(len(ride.get("legs") or []) - 1, 0 if ride.get("transfer_type") == "Direct" else 1)

def cheapest_ride(rides: list[dict[str, Any]], day: str, stations: dict[str, str] | None = None) -> dict[str, Any] | None:
    """The cheapest ride of a FlixBus search that leaves on the day, with no more changes than the traveller's
    setting, as the map shows a price: each change with its station (stations, by id) and its wait in minutes."""
    direct = [ride for ride in rides if changes_of(ride) <= MOST_CHANGES and ride["departure"]["date"][:10] == day]
    if not direct:
        return None
    ride = min(direct, key=flixbus_price)
    leaves, arrives = ride["departure"], ride["arrival"]
    # The search page of FlixBus's shop on the same cities and day, as the planner links to it (flixbus_rides)
    url = (f"https://shop.flixbus.pt/search?departureCity={leaves['city_id']}&arrivalCity={arrives['city_id']}"
           f"&rideDate={day[8:10]}.{day[5:7]}.{day[0:4]}&adult=1")
    return {"price_eur": round(flixbus_price(ride), 2), "day": day, "leave": leaves["date"][11:16], "arrive": arrives["date"][11:16],
            "arrive_day": arrives["date"][:10], "seller": "FlixBus", "grade": "A", "url": url,
            "station_ids": [leaves.get("station_id", ""), arrives.get("station_id", "")],
            "changes": [{"at": (stations or {}).get(first["arrival"].get("station_id", ""), ""),
                         "wait": round((datetime.fromisoformat(second["departure"]["date"]) - datetime.fromisoformat(first["arrival"]["date"])).total_seconds() / 60)}
                        for first, second in zip(ride.get("legs") or [], (ride.get("legs") or [])[1:])]}

def flixbus_day(start_id: str, end_id: str, day: str, towns: tuple | None = None) -> dict[str, Any] | None:
    """flixbus_search_day, or its answer when prefetch asked it already in this map; city to city (flixbus_town_day)
    when the stations give nothing and the towns are known."""
    with ANSWERS_LOCK:
        known = (start_id, end_id, day) in FLIXBUS_ANSWERS
        found = FLIXBUS_ANSWERS.get((start_id, end_id, day))
    if not known:
        found = flixbus_search_day(start_id, end_id, day)
    return found or (flixbus_town_day(towns, day) if towns else None)

def spread(items: list, count: int) -> list:
    """count items spread over the list, the first and the last among them: a morning and an evening departure."""
    if len(items) <= count:
        return items
    return [items[round(index * (len(items) - 1) / (count - 1))] for index in range(count)] if count > 1 else items[:1]

def timetable_day(option: dict[str, Any], start_id: str, end_id: str, day: str, transitous: ground.Transitous) -> dict[str, Any] | None:
    """The cheapest of MAX_DEPARTURES direct departures of the option's operator on a day, each asked of the operator
    the planner asks (fares.tickets); None when the timetables have none that day, or the operator sells none."""
    steps = []
    for itinerary in transitous.direct(start_id, end_id, day, option["modes"], 1):
        rides = [leg for leg in itinerary["legs"] if leg["mode"] != "WALK"]
        if len(rides) != 1:
            continue
        leg = rides[0]
        zone = leg["from"].get("tz") or "UTC"
        operator = ground.operator_of(leg.get("agencyName", ""), leg.get("mode", ""), leg["from"].get("stopId", ""))
        if (operator or ("",))[0] == option["operator"] and ground.local(leg["startTime"], zone).date().isoformat() == day:
            steps.append((step_of(leg, (zone, leg["to"].get("tz") or zone)), ground.local(leg["endTime"], leg["to"].get("tz") or zone)))
    best = None
    for step, arrives in spread(steps, MAX_DEPARTURES):
        for ticket in fares.tickets([step], day, None):
            if ticket["travel_day"] and not ticket["tariff"] and (best is None or ticket["price_eur"] < best["price_eur"]):
                best = {"price_eur": round(ticket["price_eur"], 2), "day": day, "leave": step["time"], "arrive": step["arrive"],
                        "arrive_day": arrives.date().isoformat(), "seller": ticket["operator"], "grade": "A", "url": option["site"]}
    return best

def by_timetable(option: dict[str, Any]) -> bool:
    """Whether an option is priced through the timetables (timetable_day), which are asked one request at a time:
    not FlixBus's, nor sold.py's coaches, asked of their operator straight."""
    return option["operator"] != "FlixBus" and not sold.is_sold(option)

def ends(option: dict[str, Any], back: bool) -> tuple[str, str]:
    return (option["stop_id"], option["from_stop_id"]) if back else (option["from_stop_id"], option["stop_id"])

def cheapest_day(option: dict[str, Any], back: bool, chosen: list[str], transitous: ground.Transitous, after: str = "",
                 towns: tuple | None = None) -> dict[str, Any] | None:
    """The cheapest of the days for one way of an option, or None when none of them sells one. after ("yyyy-mm-dd
    hh:mm"): the way back leaves after the way out arrives, a ride before it is no way back. towns: the cities of the
    way out (towns_of), for FlixBus city to city."""
    start, end = ends(option, back)
    found = []
    for day in chosen:
        try:
            price = (flixbus_day(start, end, day, towns=(towns[::-1] if back else towns) if towns else None) if option["operator"] == "FlixBus"
                     else sold.day_price(option, back, day) if sold.is_sold(option)
                     else timetable_day(option, start, end, day, transitous))
        except FAILURES:
            price = None
        if price and f"{price['day']} {price['leave']}" > after:
            found.append(price)
    return min(found, key=lambda price: price["price_eur"], default=None)

def stay(out: dict[str, Any], back: dict[str, Any]) -> dict[str, int]:
    """The time in the city, as the map gives it for a flight (page.stay): from arriving at the station to leaving it,
    with the nights in between. The times are local, and the same city's on both ends."""
    arrives = datetime.fromisoformat(f"{out['arrive_day']}T{out['arrive']}")
    leaves = datetime.fromisoformat(f"{back['day']}T{back['leave']}")
    return {"minutes": max(0, round((leaves - arrives).total_seconds() / 60)), "nights": max(0, (leaves.date() - arrives.date()).days)}

def towns_of(found: Found, holder: dict[str, Any], option: dict[str, Any]) -> tuple:
    """The two cities of an option, (name, latitude, longitude) each: the traveller's, and the one of the map it
    reaches, by its name on the map (or its airport's city) and its centre (or the station's position). The centre,
    not the station: a station on the edge of a city is nearer other places FlixBus knows by a name of their own."""
    centre = found.centres.get(holder.get("to"))
    there = holder.get("name") or (centre.english_name if centre else (load_airports().get(holder.get("to")) or {}).get("city", ""))
    latitude, longitude = ((centre.latitude, centre.longitude) if centre else (holder["lat"], holder["lon"]) if "to" not in holder
                           else (option["lat"], option["lon"]))
    return (found.place.english_name, found.place.latitude, found.place.longitude), (there, latitude, longitude)

def price_of(option: dict[str, Any], days: Days, today: date, transitous: ground.Transitous, towns: tuple | None = None) -> dict[str, Any]:
    """An option's price: {"out", "back", "total_eur", "stay"}, each way the cheapest of its days, the way back
    leaving after the way out arrives; total_eur is None when a way is sold on none of them (a return trip needs
    both). Nothing of the option is changed here: the rounds ask several at once, and only price() writes into the map."""
    out = cheapest_day(option, False, days_of(days, False, today), transitous, towns=towns)
    back = (cheapest_day(option, True, days_of(days, True, today, out["day"]), transitous, f"{out['arrive_day']} {out['arrive']}", towns=towns)
            if days.round_trip and out else None)
    whole = out and (back or not days.round_trip)
    return {"out": out, "back": back, "total_eur": round(out["price_eur"] + (back["price_eur"] if back else 0), 2) if whole else None,
            "stay": stay(out, back) if out and back else None}

def sellable(option: dict[str, Any], countries: set) -> bool:
    """Whether code can read the option's price: FlixBus anywhere ("EU"), an operator of fares.py when the trip
    starts or ends in the country it sells in (MÁV prices Hungary, not Vienna to Zürich)."""
    return bool(option.get("sold")) and (option["sold"] == "EU" or option["sold"] in countries)

def km_of(option: dict[str, Any], home: Place) -> float:
    return distance_km(home.latitude, home.longitude, option["lat"], option["lon"])

def read_rates(today: date) -> dict[str, list[list]]:
    """The prices per km of the last RATES_DAYS, by operator."""
    try:
        kept = json.loads(RATES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    oldest = (today - timedelta(days=RATES_DAYS)).isoformat()
    return {operator: [seen for seen in found if seen[2] >= oldest] for operator, found in kept.items()} if isinstance(kept, dict) else {}

def keep_rates(rates: dict[str, list[list]], options: list[dict[str, Any]], home: Place, today: date) -> None:
    """Add the prices per km of the options just priced, each way, and save them for the next maps."""
    for option in options:
        km = km_of(option, home)
        for way in (option["price"]["out"], option["price"]["back"]):
            if way and km > 0:
                rates.setdefault(option["operator"], []).append([round(km), round(way["price_eur"] / km, 4), today.isoformat()])
    try:
        write_whole(RATES, json.dumps({operator: found[-MAX_RATES:] for operator, found in rates.items()}))
    except OSError:
        pass   # the next maps only ask a little more

def least(options: list[dict[str, Any]], home: Place, round_trip: bool, rates: dict[str, list[list]], countries: set) -> float | None:
    """The least a city can cost by train or bus: its options priced, and for each one not priced yet that code can
    price, the bound of the module's docstring. rates holds every price per km known, this map's included. None when
    none of its options can be priced."""
    found = []
    for option in options:
        if option.get("price"):
            if option["price"]["total_eur"] is not None:
                found.append(option["price"]["total_eur"])
        elif sellable(option, countries):
            km = km_of(option, home)
            per_km = min((rate for seen_km, rate, _ in rates.get(option["operator"], []) if seen_km >= km > 0), default=0.0)
            found.append(per_km * km * (2 if round_trip else 1))
    return min(found, default=None)

def fare_of(options: list[dict[str, Any]]) -> float | None:
    """A city's price by train or bus: its cheapest option priced in full."""
    return min((option["price"]["total_eur"] for option in options if option.get("price") and option["price"]["total_eur"] is not None), default=None)

def cities_of(found: Found) -> list[tuple]:
    """Every city with trains or buses: (the city, its options, the flight's total it must beat or None, the
    countries its trip starts and ends in)."""
    airports, grid = load_airports(), ground.city_grid(ground.cities())
    home = ground.city_of({"lat": found.place.latitude, "lon": found.place.longitude}, grid)
    there = home["country"] if home else None
    return ([(city, city["ground"], None, {there, city["country"]}) for city in found.by_ground]
            + [(flight, flight["ground"], flight["total_eur"], {there, (airports.get(flight["to"]) or {}).get("country")})
               for flight in found.destinations if flight.get("ground")])

def waiting(cities: list[tuple], found: Found, rates: dict[str, list[list]], totals: list[float]) -> list[int]:
    """The cities that can still change the map's cheapest, the ones to price first first, by their index in cities."""
    limit = sorted(totals)[TOP - 1] if len(totals) >= TOP else float("inf")
    chosen = []
    for index, (holder, options, beat, countries) in enumerate(cities):
        if not any(sellable(option, countries) and not option.get("price") for option in options):
            continue
        cost = least(options, found.place, found.days.round_trip, rates, countries)
        if cost is not None and cost < limit and (beat is None or cost < beat):
            chosen.append((cost, -max(km_of(option, found.place) for option in options), index))
    return [index for _, _, index in sorted(chosen)]

def prefetch(found: Found, stop: threading.Event, today: date | None = None) -> int:
    """While the flights are looked up in the timetables: ask FlixBus already for the cities the rounds will likely
    price, as they would choose them now with the prices per km of the last maps. Its answers go to FLIXBUS_ANSWERS
    only: nothing of the map is changed, as the timetables' lookups change it at the same time. Stops when stop is
    set. Returns how many searches were made."""
    today = today or date.today()
    with ANSWERS_LOCK:
        FLIXBUS_ANSWERS.clear()
    try:
        cities = cities_of(found)
        order = waiting(cities, found, read_rates(today), [flight["total_eur"] for flight in list(found.destinations)])[:MAX_PRICED]
    except Exception:
        return 0   # a guess that fails only leaves the rounds to ask everything themselves
    asked = [(start, end, day) for index in order for option in cities[index][1] if option["operator"] == "FlixBus" and sellable(option, cities[index][3])
             for back in ((False, True) if found.days.round_trip else (False,))
             for start, end in [ends(option, back)] for day in days_of(found.days, back, today)]

    def one(key: tuple) -> None:
        if stop.is_set():
            return
        try:
            answer = flixbus_search_day(*key)
        except FAILURES:
            return   # asked again by the rounds
        with ANSWERS_LOCK:
            FLIXBUS_ANSWERS[key] = answer
    with ThreadPoolExecutor(AT_ONCE) as pool:
        list(pool.map(one, asked))
    return len(FLIXBUS_ANSWERS)

def price(found: Found, on_change: Callable[[Found], None] = lambda found: None, say: Callable = print_line,
          transitous: ground.Transitous | None = None, today: date | None = None) -> Found:
    """Price the trains and buses of the map in rounds (see the module's docstring), MAX_PRICED cities at most
    each time it is called: once as the map opens, against the flights' totals as estimated, and again once their
    timetables are looked up (session.py), for the cities that can now be among the cheapest. A city with no flight
    gets "fare_eur", its cheapest option priced in full; every option priced gets "price"."""
    today, transitous = today or date.today(), transitous or ground.Transitous()
    cities = cities_of(found)
    found.fare_stop = ""
    if not any(sellable(option, countries) for _, options, _, countries in cities for option in options):
        found.fare_stop = tr("discover_fares_none_sellable")
        return found
    rates = read_rates(today)
    before = len(found.fare_lookups)
    while True:
        room = MAX_PRICED - (len(found.fare_lookups) - before)
        if room <= 0:
            found.fare_stop = tr("discover_fares_most", count=MAX_PRICED)
            break
        totals = [flight["total_eur"] for flight in found.destinations] + [city["fare_eur"] for city in found.by_ground if city.get("fare_eur") is not None]
        chosen = waiting(cities, found, rates, totals)[:min(ROUND, room)]
        if not chosen:
            found.fare_stop = tr("discover_fares_beaten", top=TOP)
            break
        selling = {index: [option for option in cities[index][1] if sellable(option, cities[index][3]) and not option.get("price")] for index in chosen}
        names = [cities[index][0].get("name") or cities[index][0]["to"] for index in chosen]
        say(tr("discover_say_fares_cities", cities=", ".join(names), number=len(found.fare_lookups) - before + len(chosen), most=MAX_PRICED))
        buses = [option for index in chosen for option in selling[index] if not by_timetable(option)]
        trains = [option for index in chosen for option in selling[index] if by_timetable(option)]
        with ThreadPoolExecutor(AT_ONCE + 1) as pool:
            # The trains one after the other, in one worker, as they share the timetables; the coaches in the others
            holders = {id(option): cities[index][0] for index in chosen for option in selling[index]}
            by_train = pool.submit(lambda: [price_of(option, found.days, today, transitous) for option in trains])
            by_bus = list(pool.map(lambda option: price_of(option, found.days, today, transitous,
                                                           towns=towns_of(found, holders[id(option)], option)), buses))
            for option, priced in zip(trains + buses, by_train.result() + by_bus):
                option["price"] = priced
        keep_rates(rates, trains + buses, found.place, today)
        for index, name in zip(chosen, names):
            holder, options, beat, _ = cities[index]
            if beat is None:   # a city with no flight: its price is the map's for it
                holder["fare_eur"] = fare_of(options)
            found.fare_lookups.append({"city": name, "operators": [option["operator"] for option in selling[index]],
                                       "priced": sum(1 for option in selling[index] if option["price"]["total_eur"] is not None)})
        on_change(found)
    say(tr("discover_say_fares_done", count=len(found.fare_lookups) - before, stop=found.fare_stop))
    on_change(found)
    return found

# ---------------------------------------------------------------- one city, asked for on the map
# The rounds price only what can be among the cheapest: a city the traveller wants to see anyway (Paris by coach when
# the flights are cheaper) is priced when the page asks for it, with the same price_of, grade A as any other.

def asked_city(cities: list[tuple], key: str) -> int | None:
    """The city of cities_of with this key on the page, by its index: "~" and its own key for a city no flight goes
    to, its airport's code for one a flight goes to. None for any other key."""
    for index, (holder, _, _, _) in enumerate(cities):
        if ("to" in holder and holder["to"] == key) or ("to" not in holder and "~" + str(holder.get("key")) == key):
            return index
    return None

def price_asked(found: Found, key: str, before_trains: Callable[[], Any] = lambda: None, today: date | None = None,
                transitous: ground.Transitous | None = None) -> tuple[tuple, list[tuple[dict, dict]]] | None:
    """The price of every option of one city that code can price and has not priced yet, whatever the rounds chose:
    (the city as cities_of gives it, [(option, its price_of)]), for write_asked; None for a key that is no city of the
    map. The city itself, not its place in the map: the flights' lookups may reorder them meanwhile. FlixBus AT_ONCE at a time; the operators asked departure by departure after before_trains, as they share the
    timetables the flights may still be looked up in. Nothing of the map is changed here."""
    today, transitous = today or date.today(), transitous or ground.Transitous()
    cities = cities_of(found)
    index = asked_city(cities, key)
    if index is None:
        return None
    holder, options, _, countries = cities[index]
    # Asked again for one that was sold on none of the days: the traveller asks, and FlixBus's seats come and go
    selling = [option for option in options if sellable(option, countries) and not (option.get("price") or {}).get("out")]
    buses = [option for option in selling if not by_timetable(option)]
    trains = [option for option in selling if by_timetable(option)]
    with ThreadPoolExecutor(AT_ONCE) as pool:
        priced = list(zip(buses, pool.map(lambda option: price_of(option, found.days, today, transitous,
                                                                  towns=towns_of(found, holder, option)), buses)))
    if trains:
        before_trains()
        priced += [(option, price_of(option, found.days, today, transitous)) for option in trains]
    return cities[index], priced

def write_asked(found: Found, city: tuple, priced: list[tuple[dict, dict]], today: date | None = None) -> int:
    """The prices of price_asked written into the map, as price() writes a round's: the city's fare when no flight
    goes there, its lookup listed, the prices per km kept. Returns how many options have a price in full."""
    today = today or date.today()
    holder, options, beat, _ = city
    for option, price_found in priced:
        option["price"] = price_found
    if not priced:
        return 0
    keep_rates(read_rates(today), [option for option, _ in priced], found.place, today)
    if beat is None:
        holder["fare_eur"] = fare_of(options)
    count = sum(1 for _, price_found in priced if price_found["total_eur"] is not None)
    found.fare_lookups.append({"city": holder.get("name") or holder["to"], "operators": [option["operator"] for option, _ in priced],
                               "priced": count, "asked": True})
    return count
