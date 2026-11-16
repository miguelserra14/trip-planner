"""The trains and buses of the map priced for the days of the trip (ground_fares.py): FlixBus's cheapest direct
ride of a day, the planner's operators asked departure by departure, and the cities priced in rounds, only the ones
that can still be among the map's cheapest. No network: FlixBus, the timetables and the operators are made up."""
import threading
from datetime import date

from discover.overland import ground_fares
from discover.finder import Found
from discover.flights.sources import Days
from trip_planner.trip.places import Place

ANTWERP = Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.22, 4.40)
TODAY = date(2026, 10, 24)
ONE_WAY = Days("2026-11-12", "2026-11-13")
RETURN = Days("2026-11-12", "2026-11-12", "2026-11-15", "2026-11-16")

def option(operator: str, stop: str, latitude: float, longitude: float, sold: str = "EU", mode: str = "bus") -> dict:
    return {"operator": operator, "mode": mode, "modes": "COACH" if mode == "bus" else "LONG_DISTANCE", "sold": sold,
            "from_stop": "Antwerp (Berchem)", "from_stop_id": "eu-flixbus_home", "stop": stop, "stop_id": f"eu-flixbus_{stop.lower()}",
            "lat": latitude, "lon": longitude, "site": "https://global.flixbus.com", "times": ["08:00"], "weekdays": ["Thu"],
            "minutes": 60, "grade": "B"}

def ride(leaves: str, arrives: str, price: float, transfer: str = "Direct") -> dict:
    return {"transfer_type": transfer, "status": "available", "price": {"total": price, "total_with_platform_fee": price + 1},
            "departure": {"date": leaves, "city_id": "antwerp-city", "station_id": "home"},
            "arrival": {"date": arrives, "city_id": "paris-city", "station_id": "paris"}}

def test_flixbus_cheapest_direct_ride(monkeypatch):
    rides = [ride("2026-11-12T10:30:00+01:00", "2026-11-12T17:15:00+01:00", 15.0, "Transfer"),   # cheaper, with a change: not straight
             ride("2026-11-12T16:50:00+01:00", "2026-11-12T22:25:00+01:00", 20.99),
             ride("2026-11-12T22:05:00+01:00", "2026-11-13T05:30:00+01:00", 24.99)]
    asked = []
    monkeypatch.setattr(ground_fares, "flixbus_search", lambda start, end, day, by: asked.append((start, end, day, by)) or (rides, {}))
    found = ground_fares.flixbus_search_day("eu-flixbus_home", "eu-flixbus_paris", "2026-11-12")
    assert asked == [("home", "paris", "2026-11-12", "station")]   # FlixBus's own ids, one search for the whole day
    assert (found["price_eur"], found["leave"], found["arrive"], found["grade"]) == (21.99, "16:50", "22:25", "A")   # with the platform fee
    assert found["url"].startswith("https://shop.flixbus.pt/search?departureCity=antwerp-city&arrivalCity=paris-city&rideDate=12.11.2026")

def leg(agency: str, leaves: str, arrives: str) -> dict:
    end = lambda name, stop: {"name": name, "stopId": stop, "lat": 48.2, "lon": 16.4, "tz": "Europe/Vienna"}
    return {"mode": "LONG_DISTANCE", "agencyName": agency, "startTime": leaves, "endTime": arrives, "displayName": "RJ",
            "from": end("Wien Hbf", "at-wien"), "to": end("Salzburg Hbf", "at-salzburg")}

class Timetables:
    def __init__(self, itineraries):
        self.itineraries, self.asked = itineraries, []

    def direct(self, start, end, day, modes, days):
        self.asked.append((start, end, day))
        return self.itineraries

def test_planner_operator_cheapest_kept(monkeypatch):
    times = [("2026-11-12T05:00:00Z", "2026-11-12T07:50:00Z"), ("2026-11-12T08:00:00Z", "2026-11-12T10:50:00Z"),
             ("2026-11-12T11:00:00Z", "2026-11-12T13:50:00Z"), ("2026-11-12T14:00:00Z", "2026-11-12T16:50:00Z"),
             ("2026-11-12T17:00:00Z", "2026-11-12T19:50:00Z")]
    itineraries = [{"legs": [{"mode": "WALK"}, leg("OEBB Personenverkehr AG", *pair)]} for pair in times]
    itineraries.append({"legs": [leg("OEBB Personenverkehr AG", *times[0]), leg("OEBB Personenverkehr AG", *times[1])]})   # a change
    itineraries.append({"legs": [leg("Westbahn", *times[2])]})   # another operator
    prices = {"06:00": 29.9, "12:00": 19.9, "18:00": 39.9}
    asked = []
    monkeypatch.setattr(ground_fares.fares, "tickets", lambda steps, day, service: asked.append(steps[0]["time"]) or
                        ([{"price_eur": prices[steps[0]["time"]], "travel_day": True, "tariff": False, "operator": "ÖBB"}] if steps[0]["time"] in prices else []))
    train = {**option("ÖBB", "Salzburg", 47.81, 13.05, sold="AT", mode="train"), "site": "https://www.oebb.at"}
    found = ground_fares.timetable_day(train, "at-wien", "at-salzburg", "2026-11-12", Timetables(itineraries))
    assert asked == ["06:00", "12:00", "18:00"]   # MAX_DEPARTURES, spread over the day: the first, the middle and the last, local time
    assert (found["price_eur"], found["leave"], found["seller"], found["url"]) == (19.9, "12:00", "ÖBB", "https://www.oebb.at")

def test_return_total_needs_both(monkeypatch):
    asked = []

    def flixbus_day(start, end, day, towns=None):
        asked.append((start, day))
        return {"price_eur": 10.0 if start == "eu-flixbus_home" else 12.0, "day": day, "leave": "08:00", "arrive": "09:00", "arrive_day": day}
    monkeypatch.setattr(ground_fares, "flixbus_day", flixbus_day)
    bus = option("FlixBus", "Paris", 48.85, 2.35)
    price = ground_fares.price_of(bus, RETURN, TODAY, Timetables([]))
    assert price["total_eur"] == 22.0 and price["back"]["day"] == "2026-11-15" and "price" not in bus   # only price() writes into the map
    assert asked == [("eu-flixbus_home", "2026-11-12"), ("eu-flixbus_paris", "2026-11-15"), ("eu-flixbus_paris", "2026-11-16")]
    monkeypatch.setattr(ground_fares, "flixbus_day", lambda start, end, day, towns=None: None if start == "eu-flixbus_paris" else flixbus_day(start, end, day))
    price = ground_fares.price_of(bus, RETURN, TODAY, Timetables([]))
    assert price["total_eur"] is None and price["out"]["price_eur"] == 10.0   # no way back sold: no total

def test_return_after_arrival(monkeypatch):
    days = Days("2026-11-12", "2026-11-12", "2026-11-12", "2026-11-14")   # coming back may be the same day

    def flixbus_day(start, end, day, towns=None):
        if start == "eu-flixbus_home":   # a night bus: it arrives the next morning
            return {"price_eur": 20.0, "day": day, "leave": "22:00", "arrive": "06:30", "arrive_day": "2026-11-13"}
        return {"price_eur": 5.0 if day < "2026-11-14" else 15.0, "day": day, "leave": "18:00", "arrive": "02:00", "arrive_day": day}
    monkeypatch.setattr(ground_fares, "flixbus_day", flixbus_day)
    price = ground_fares.price_of(option("FlixBus", "Paris", 48.85, 2.35), days, TODAY, Timetables([]))
    assert price["back"]["day"] == "2026-11-13" and price["total_eur"] == 25.0   # not the 12th: it leaves before the bus arrives
    assert price["stay"] == {"minutes": 690, "nights": 0}   # 06:30 to 18:00 on the 13th
    one_way = ground_fares.price_of(option("FlixBus", "Paris", 48.85, 2.35), ONE_WAY, TODAY, Timetables([]))
    assert one_way["stay"] is None and one_way["back"] is None

def test_national_operator_scoped():
    assert ground_fares.sellable(option("MÁV", "Budapest", 47.5, 19.0, sold="HU"), {"AT", "HU"})
    assert not ground_fares.sellable(option("MÁV", "Zürich", 47.4, 8.5, sold="HU"), {"AT", "CH"})   # MÁV's feed has Vienna to Zürich
    assert ground_fares.sellable(option("FlixBus", "Zürich", 47.4, 8.5), {"AT", "CH"})
    assert not ground_fares.sellable(option("Deutsche Bahn", "Köln", 50.9, 6.9, sold=""), {"BE", "DE"})   # not read by code

def city(name: str, *options) -> dict:
    return {"key": name, "name": name, "country": "", "lat": options[0]["lat"], "lon": options[0]["lon"], "minutes": 60, "modes": ["bus"],
            "ground": list(options)}

# Made-up FlixBus prices of about 0.10 EUR a km, a little more for the short trips: no city breaks the module's assumption
FARES = {"Warsaw": 110.0, "Paris": 35.0, "Brussels": 6.0, "Berlin": 70.0, "Lyon": 30.0, "Lille": 30.0}
TEN_FLIGHTS = [{"to": f"F{n}", "total_eur": 40.0 + n} for n in range(10)]   # the dearest of the ten cheapest flights: 49 EUR

def priced_by(monkeypatch) -> list:
    """price_of made up from FARES; the cities asked, in the order asked."""
    asked = []

    def price_of(option, days, today, transitous, towns=None):
        asked.append(option["stop"])
        return {"out": {"price_eur": FARES[option["stop"]]}, "back": None, "total_eur": FARES[option["stop"]]}
    monkeypatch.setattr(ground_fares, "price_of", price_of)
    return asked

def test_hopeless_cities_unpriced(monkeypatch):
    monkeypatch.setattr(ground_fares, "ROUND", 1)
    asked = priced_by(monkeypatch)
    cities = [city("Warsaw", option("FlixBus", "Warsaw", 52.23, 21.01)), city("Paris", option("FlixBus", "Paris", 48.85, 2.35)),
              city("Brussels", option("FlixBus", "Brussels", 50.85, 4.35)), city("Berlin", option("FlixBus", "Berlin", 52.52, 13.40)),
              city("Köln", option("Deutsche Bahn", "Köln", 50.94, 6.96, sold="", mode="train"))]
    found = Found(ANTWERP, ONE_WAY, [], TEN_FLIGHTS, [], [], by_ground=cities)
    ground_fares.price(found, say=lambda text: None, transitous=Timetables([]), today=TODAY)
    assert asked == ["Warsaw", "Brussels", "Paris"]   # the longest first, for its price per km; then Berlin, at least 0.10 x 670 km: not
    assert [city.get("fare_eur") for city in cities] == [110.0, 35.0, 6.0, None, None]   # Köln: Deutsche Bahn's price is not read
    assert found.fare_stop.startswith("no city left could be among the 10 cheapest")

def test_round_prices_several_cities(monkeypatch):
    asked = priced_by(monkeypatch)
    cities = [city(name, option("FlixBus", name, *at)) for name, at in (("Warsaw", (52.23, 21.01)), ("Berlin", (52.52, 13.40)), ("Paris", (48.85, 2.35)))]
    found = Found(ANTWERP, ONE_WAY, [], TEN_FLIGHTS, [], [], by_ground=cities)
    ground_fares.price(found, say=lambda text: None, transitous=Timetables([]), today=TODAY)
    assert sorted(asked) == ["Berlin", "Paris", "Warsaw"] and len(found.fare_lookups) == 3   # one round: no price per km known yet

def test_km_prices_guide_next(monkeypatch):
    asked = priced_by(monkeypatch)
    warsaw = city("Warsaw", option("FlixBus", "Warsaw", 52.23, 21.01))
    ground_fares.price(Found(ANTWERP, ONE_WAY, [], TEN_FLIGHTS, [], [], by_ground=[warsaw]), say=lambda text: None,
                       transitous=Timetables([]), today=TODAY)
    asked.clear()
    cities = [city("Berlin", option("FlixBus", "Berlin", 52.52, 13.40)), city("Brussels", option("FlixBus", "Brussels", 50.85, 4.35))]
    ground_fares.price(Found(ANTWERP, ONE_WAY, [], TEN_FLIGHTS, [], [], by_ground=cities), say=lambda text: None,
                       transitous=Timetables([]), today=TODAY)
    assert asked == ["Brussels"]   # Warsaw's price per km, from the last map, says Berlin costs at least 62 EUR
    assert cities[0].get("fare_eur") is None and cities[1]["fare_eur"] == 6.0
    assert ground_fares.read_rates(date(2026, 12, 1)) == {"FlixBus": []}   # too old to choose anything

def test_flight_city_priced_conditionally(monkeypatch):
    monkeypatch.setattr(ground_fares, "ROUND", 1)
    asked = priced_by(monkeypatch)
    near, far = option("FlixBus", "Lille", 50.63, 3.06), option("FlixBus", "Lyon", 45.76, 4.84)
    flights = [{"to": "LIL", "total_eur": 25.0, "ground": [near]}, {"to": "LYS", "total_eur": 90.0, "ground": [far]}]
    found = Found(ANTWERP, ONE_WAY, [], flights, [], [])
    ground_fares.price(found, say=lambda text: None, transitous=Timetables([]), today=TODAY)
    assert asked == ["Lyon", "Lille"]   # no price per km known at first: Lille too, then it has its own
    assert near["price"]["total_eur"] == 30.0 and "fare_eur" not in flights[0]   # its flight keeps its own total
    monkeypatch.setattr(ground_fares, "MAX_PRICED", 1)
    near.pop("price"), far.pop("price")
    found = Found(ANTWERP, ONE_WAY, [], flights, [], [])
    ground_fares.price(found, say=lambda text: None, transitous=Timetables([]), today=TODAY)
    assert found.fare_stop == "stopped after 1 cities, the most a map prices at once"

def test_flixbus_prefetch_used(monkeypatch):
    searched = []
    monkeypatch.setattr(ground_fares, "flixbus_search_day", lambda start, end, day: searched.append((end, day)) or
                        {"price_eur": 9.0, "day": day, "leave": "08:00", "arrive": "09:00", "arrive_day": day})
    cities = [city("Paris", option("FlixBus", "Paris", 48.85, 2.35)), city("Köln", option("Deutsche Bahn", "Köln", 50.94, 6.96, sold="", mode="train"))]
    found = Found(ANTWERP, ONE_WAY, [], TEN_FLIGHTS, [], [], by_ground=cities)
    assert ground_fares.prefetch(found, threading.Event(), TODAY) == 2   # Paris, on both days to leave on; nothing for Deutsche Bahn
    assert sorted(searched) == [("eu-flixbus_paris", "2026-11-12"), ("eu-flixbus_paris", "2026-11-13")] and "price" not in cities[0]["ground"][0]
    monkeypatch.setattr(ground_fares, "flixbus_search_day", lambda start, end, day: (_ for _ in ()).throw(AssertionError("asked again")))
    ground_fares.price(found, say=lambda text: None, transitous=Timetables([]), today=TODAY)
    assert cities[0]["fare_eur"] == 9.0
    stopped = threading.Event()
    stopped.set()
    assert ground_fares.prefetch(found, stopped, TODAY) == 0   # the timetables were done before it began

class NoTrip:
    """Stands in for the trip being planned where nothing may read it."""
    def __getattr__(self, name):
        raise AssertionError(f"the trip being planned was read: {name}")

def test_prefetch_ignores_current_trip(monkeypatch):
    """prefetch runs in a thread while confirm.py makes each destination the planner's trip (current.trip): it must
    not read that trip, which is not its own. Its whole way is run, FlixBus's own module included, with no network."""
    from trip_planner import current
    from trip_planner.sources.operators import flixbus

    class Answer:
        def raise_for_status(self):
            return self
        def json(self):
            return {"trips": [{"results": {"r1": ride("2026-11-12T16:50:00+01:00", "2026-11-12T22:25:00+01:00", 20.99)}}], "stations": {}}
    monkeypatch.setattr(flixbus, "http", type("Http", (), {"get": lambda self, url, params: Answer()})())
    monkeypatch.setattr(current, "SETTINGS", NoTrip())
    monkeypatch.setattr(current, "TRIP", NoTrip())
    cities = [city("Paris", option("FlixBus", "Paris", 48.85, 2.35))]
    found = Found(ANTWERP, ONE_WAY, [], TEN_FLIGHTS, [], [], by_ground=cities)
    assert ground_fares.prefetch(found, threading.Event(), TODAY) == 2

def test_first_days_priced():
    assert ground_fares.days_of(Days("2026-10-20", "2026-11-30"), False, TODAY) == ["2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"]
    assert ground_fares.days_of(Days.clicked(["2026-11-12", "2026-11-14"], ["2026-11-13", "2026-11-20"]), True, TODAY, "2026-11-14") == ["2026-11-20"]


def test_requested_city_priced(monkeypatch):
    asked = priced_by(monkeypatch)
    berlin = city("Berlin", option("FlixBus", "Berlin", 52.52, 13.40))
    flown = {"to": "LIL", "total_eur": 20.0, "ground": [option("FlixBus", "Lille", 50.63, 3.06)]}
    found = Found(ANTWERP, ONE_WAY, [], [flown, *TEN_FLIGHTS], [], [], by_ground=[berlin])
    assert ground_fares.price_asked(found, "~Paris", transitous=Timetables([]), today=TODAY) is None   # no such city on the map
    answer = ground_fares.price_asked(found, "~Berlin", transitous=Timetables([]), today=TODAY)
    assert asked == ["Berlin"] and "price" not in berlin["ground"][0]   # nothing written until write_asked
    assert ground_fares.write_asked(found, *answer, today=TODAY) == 1
    assert berlin["fare_eur"] == 70.0 and found.fare_lookups == [{"city": "Berlin", "operators": ["FlixBus"], "priced": 1, "asked": True}]
    ground_fares.write_asked(found, *ground_fares.price_asked(found, "LIL", transitous=Timetables([]), today=TODAY), today=TODAY)
    assert flown["ground"][0]["price"]["total_eur"] == 30.0 and "fare_eur" not in flown   # a flight's city: by its airport, its total stays the flight's
    assert ground_fares.price_asked(found, "~Berlin", transitous=Timetables([]), today=TODAY)[1] == []   # priced already: not asked again

def test_train_price_waits(monkeypatch):
    asked = priced_by(monkeypatch)
    monkeypatch.setitem(FARES, "Wien", 40.0)
    both = city("Wien", option("ÖBB", "Wien", 48.20, 16.37, sold="AT", mode="train"), option("FlixBus", "Wien", 48.20, 16.37))
    both["country"] = "AT"
    found = Found(ANTWERP, ONE_WAY, [], [], [], [], by_ground=[both])
    waited = []
    ground_fares.price_asked(found, "~Wien", before_trains=lambda: waited.append(list(asked)), transitous=Timetables([]), today=TODAY)
    assert waited == [["Wien"]] and asked == ["Wien", "Wien"]   # FlixBus first, the train once the timetables are free

def test_price_request_progress(monkeypatch):
    from discover import session as discover_session
    states = []
    monkeypatch.setattr(discover_session, "map_state", lambda found, sections, running, version, **more:
                        states.append(dict(found.asked_prices)) or {"version": version})
    monkeypatch.setattr(discover_session, "breakdown", lambda found: [])
    monkeypatch.setattr(ground_fares, "price_of", lambda option, days, today, transitous, towns=None: {"out": {"price_eur": 9.0}, "back": None, "total_eur": 9.0})
    session = discover_session.Session("Antwerp", ONE_WAY)
    saved = []
    monkeypatch.setattr(session, "save", lambda: saved.append(True))
    session.found = Found(ANTWERP, ONE_WAY, [], [], [], [], by_ground=[city("Lille", option("FlixBus", "Lille", 50.63, 3.06))])
    session.price_asked("~Lille")
    session.price_asked("~Nowhere")
    assert states == [{"~Lille": "waiting"}, {"~Lille": "priced"}, {"~Lille": "priced", "~Nowhere": "waiting"},
                      {"~Lille": "priced", "~Nowhere": "none"}]
    assert session.live["state"]["version"] == 4 and len(saved) == 2   # saved: the lookups had ended

def test_flixbus_city_fallback(monkeypatch):
    searched = []

    def flixbus_search(start, end, day, by):
        searched.append(by)
        if by == "station":
            return [], {}
        city_ride = ride("2026-11-12T09:20:00.000+01:00", "2026-11-12T18:04:00.000+01:00", 39.98)
        city_ride["departure"]["station_id"], city_ride["arrival"]["station_id"] = "s1", "s2"
        return [city_ride], {"s1": "Antwerp P+R Luchtbal", "s2": "Heidelberg Hbf"}
    monkeypatch.setattr(ground_fares, "flixbus_search", flixbus_search)
    monkeypatch.setattr(ground_fares, "flixbus_city", lambda name, latitude, longitude, km: {"Antwerp": "c1", "Heidelberg": "c2"}.get(name))
    towns = (("Antwerp", 51.22, 4.40), ("Heidelberg", 49.40, 8.67))
    found = ground_fares.flixbus_day("eu-flixbus_home", "eu-flixbus_heidelberg", "2026-11-12", towns=towns)
    assert searched == ["station", "city"] and found["price_eur"] == 40.98 and found["grade"] == "A"
    assert (found["from_stop"], found["to_stop"]) == ("Antwerp P+R Luchtbal", "Heidelberg Hbf")   # FlixBus's own, shown instead
    assert ground_fares.flixbus_day("eu-flixbus_home", "eu-flixbus_x", "2026-11-12", towns=(towns[0], ("Nowhere", 0, 0))) is None
    assert ground_fares.flixbus_day("eu-flixbus_home", "eu-flixbus_x", "2026-11-12") is None   # no towns: stations only

def test_unsold_city_reaskable(monkeypatch):
    asked = priced_by(monkeypatch)
    unsold = option("FlixBus", "Berlin", 52.52, 13.40)
    unsold["price"] = {"out": None, "back": None, "total_eur": None, "stay": None}
    sold = option("FlixBus", "Paris", 48.85, 2.35)
    sold["price"] = {"out": {"price_eur": 35.0}, "back": None, "total_eur": None, "stay": None}   # the way back only missing
    found = Found(ANTWERP, ONE_WAY, [], [], [], [], by_ground=[city("Berlin", unsold), city("Paris", sold)])
    assert [option["stop"] for option, _ in ground_fares.price_asked(found, "~Berlin", transitous=Timetables([]), today=TODAY)[1]] == ["Berlin"]
    assert ground_fares.price_asked(found, "~Paris", transitous=Timetables([]), today=TODAY)[1] == [] and asked == ["Berlin"]

def test_coach_changes_limited(monkeypatch):
    def leg(leaves, arrives, station):
        return {"departure": {"date": leaves}, "arrival": {"date": arrives, "station_id": station}}
    changed = ride("2026-11-12T18:00:00+01:00", "2026-11-13T05:35:00+00:00", 30.0, transfer="Transfer")
    changed["legs"] = [leg("2026-11-12T18:00:00+01:00", "2026-11-12T20:35:00+01:00", "bru"), leg("2026-11-12T22:55:00+01:00", "2026-11-13T05:35:00+00:00", "lon")]
    straight = ride("2026-11-12T10:15:00+01:00", "2026-11-12T19:10:00+00:00", 45.0)
    assert ground_fares.cheapest_ride([changed, straight], "2026-11-12")["price_eur"] == 46.0   # 0 changes: the straight one
    monkeypatch.setattr(ground_fares, "MOST_CHANGES", 1)
    found = ground_fares.cheapest_ride([changed, straight], "2026-11-12", {"bru": "Brussels-North"})
    assert found["price_eur"] == 31.0 and found["changes"] == [{"at": "Brussels-North", "wait": 140}]
    monkeypatch.setattr(ground_fares, "MOST_CHANGES", 0)
    assert ground_fares.cheapest_ride([changed], "2026-11-12") is None
