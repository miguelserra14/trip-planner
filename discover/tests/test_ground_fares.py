"""The trains and buses of the map priced for the days of the trip (ground_fares.py): FlixBus's cheapest direct
ride of a day, the planner's operators asked departure by departure, and the cities priced in rounds, only the ones
that can still be among the map's cheapest. No network: FlixBus, the timetables and the operators are made up."""
import threading
from datetime import date

from discover import ground_fares
from discover.finder import Found
from discover.sources import Days
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

def test_flixbus_gives_its_cheapest_direct_ride_of_the_day_and_the_shop_page_of_it(monkeypatch):
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

def test_an_operator_of_the_planner_is_asked_for_a_few_direct_departures_of_the_day_and_the_cheapest_kept(monkeypatch):
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

def test_a_return_trip_has_a_total_only_with_both_ways_and_comes_back_after_it_leaves(monkeypatch):
    asked = []

    def flixbus_day(start, end, day):
        asked.append((start, day))
        return {"price_eur": 10.0 if start == "eu-flixbus_home" else 12.0, "day": day, "leave": "08:00", "arrive": "09:00", "arrive_day": day}
    monkeypatch.setattr(ground_fares, "flixbus_day", flixbus_day)
    bus = option("FlixBus", "Paris", 48.85, 2.35)
    price = ground_fares.price_of(bus, RETURN, TODAY, Timetables([]))
    assert price["total_eur"] == 22.0 and price["back"]["day"] == "2026-11-15" and "price" not in bus   # only price() writes into the map
    assert asked == [("eu-flixbus_home", "2026-11-12"), ("eu-flixbus_paris", "2026-11-15"), ("eu-flixbus_paris", "2026-11-16")]
    monkeypatch.setattr(ground_fares, "flixbus_day", lambda start, end, day: None if start == "eu-flixbus_paris" else flixbus_day(start, end, day))
    price = ground_fares.price_of(bus, RETURN, TODAY, Timetables([]))
    assert price["total_eur"] is None and price["out"]["price_eur"] == 10.0   # no way back sold: no total

def test_the_way_back_leaves_after_the_way_out_arrives_and_the_time_in_the_city_is_counted(monkeypatch):
    days = Days("2026-11-12", "2026-11-12", "2026-11-12", "2026-11-14")   # coming back may be the same day

    def flixbus_day(start, end, day):
        if start == "eu-flixbus_home":   # a night bus: it arrives the next morning
            return {"price_eur": 20.0, "day": day, "leave": "22:00", "arrive": "06:30", "arrive_day": "2026-11-13"}
        return {"price_eur": 5.0 if day < "2026-11-14" else 15.0, "day": day, "leave": "18:00", "arrive": "02:00", "arrive_day": day}
    monkeypatch.setattr(ground_fares, "flixbus_day", flixbus_day)
    price = ground_fares.price_of(option("FlixBus", "Paris", 48.85, 2.35), days, TODAY, Timetables([]))
    assert price["back"]["day"] == "2026-11-13" and price["total_eur"] == 25.0   # not the 12th: it leaves before the bus arrives
    assert price["stay"] == {"minutes": 690, "nights": 0}   # 06:30 to 18:00 on the 13th
    one_way = ground_fares.price_of(option("FlixBus", "Paris", 48.85, 2.35), ONE_WAY, TODAY, Timetables([]))
    assert one_way["stay"] is None and one_way["back"] is None

def test_an_operator_of_one_country_is_asked_only_for_a_trip_from_or_to_it():
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

    def price_of(option, days, today, transitous):
        asked.append(option["stop"])
        return {"out": {"price_eur": FARES[option["stop"]]}, "back": None, "total_eur": FARES[option["stop"]]}
    monkeypatch.setattr(ground_fares, "price_of", price_of)
    return asked

def test_the_cities_that_cannot_be_among_the_cheapest_are_not_priced(monkeypatch):
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

def test_a_round_prices_several_cities_at_once(monkeypatch):
    asked = priced_by(monkeypatch)
    cities = [city(name, option("FlixBus", name, *at)) for name, at in (("Warsaw", (52.23, 21.01)), ("Berlin", (52.52, 13.40)), ("Paris", (48.85, 2.35)))]
    found = Found(ANTWERP, ONE_WAY, [], TEN_FLIGHTS, [], [], by_ground=cities)
    ground_fares.price(found, say=lambda text: None, transitous=Timetables([]), today=TODAY)
    assert sorted(asked) == ["Berlin", "Paris", "Warsaw"] and len(found.fare_lookups) == 3   # one round: no price per km known yet

def test_the_prices_per_km_of_a_map_choose_what_the_next_one_asks(monkeypatch):
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

def test_a_city_a_flight_goes_to_is_priced_only_when_it_can_beat_that_flight(monkeypatch):
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

def test_flixbus_is_asked_while_the_flights_are_looked_up_and_the_rounds_take_its_answers(monkeypatch):
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

def test_the_days_priced_are_the_first_ones_of_each_way_and_none_past():
    assert ground_fares.days_of(Days("2026-10-20", "2026-11-30"), False, TODAY) == ["2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"]
    assert ground_fares.days_of(Days.clicked(["2026-11-12", "2026-11-14"], ["2026-11-13", "2026-11-20"]), True, TODAY, "2026-11-14") == ["2026-11-20"]

