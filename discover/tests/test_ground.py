"""The trains and buses with no published prices that go straight to a city of the map (ground.py): read from the
timetables, matched to the cities, checked on the way back, never priced. No network: Transitous is a made-up one."""
from datetime import date

from discover.overland import ground
from discover.flights.sources import Days
from trip_planner.trip.places import Place

COIMBRA = Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43)
TODAY = date(2026, 10, 24)

def departure(trip_id: str, headsign: str, moment: str, agency: str = "BlaBlaCar Bus", mode: str = "COACH") -> dict:
    return {"tripId": trip_id, "headsign": headsign, "agencyName": agency, "mode": mode,
            "place": {"stopId": "eu-blablacar-bus_AHX", "name": "Coimbra - Bus Station", "tz": "Europe/Lisbon", "departure": moment}}

def call(stop_id: str, name: str, moment: str, latitude: float, longitude: float, **more) -> dict:
    return {"stopId": stop_id, "name": name, "arrival": moment, "departure": moment, "lat": latitude, "lon": longitude, **more}

TRIP = {"legs": [{"from": call("eu-blablacar-bus_ALS", "Lisbon - Sete Rios", "2026-11-12T07:30:00Z", 38.74, -9.17),
                  "intermediateStops": [call("eu-blablacar-bus_AHX", "Coimbra - Bus Station", "2026-11-12T11:45:00Z", 40.22, -8.44),
                                        call("eu-blablacar-bus_ASN", "Salamanca", "2026-11-12T16:14:00Z", 40.97, -5.67),
                                        call("eu-blablacar-bus_XPB", "Paris - Bercy-Seine Bus Station", "2026-11-13T10:00:00Z", 48.84, 2.38)],
                  "to": call("eu-blablacar-bus_XMZ", "Metz - Bus Station", "2026-11-13T15:29:00Z", 49.11, 6.18)}]}
PATTERN = {"stop_id": "eu-blablacar-bus_AHX", "stop": "Coimbra - Bus Station", "operator": "BlaBlaCar Bus", "site": "https://www.blablacar.com",
           "modes": "COACH", "times": {"11:45"}, "weekdays": {"Thu"}}

def ride(agency: str, start: str, minutes: int, changes: int = 0) -> dict:
    legs = [{"mode": "COACH", "agencyName": agency, "startTime": start, "from": {"tz": "Europe/Paris"}} for _ in range(changes + 1)]
    return {"duration": minutes * 60, "legs": [{"mode": "WALK"}, *legs]}

class Timetables:
    """A Transitous that knows one bus line and its way back, and counts what it is asked."""

    def __init__(self):
        self.asked = []

    def stops(self, place):
        self.asked.append("stops")
        return [{"stopId": "eu-blablacar-bus_AHX", "lat": 40.22, "lon": -8.44}]

    def departures(self, stop_id, start):
        self.asked.append("departures")
        return [departure("a", "LUX", "2026-11-12T11:45:00Z"), departure("b", "LUX", "2026-11-14T11:45:00Z"),   # the same bus, two days
                departure("c", "Porto", "2026-11-12T09:00:00Z", agency="Rede Expressos")]   # priced by the planner: not here

    def trip(self, trip_id):
        self.asked.append(f"trip {trip_id}")
        return TRIP

    def direct(self, start, end, day, modes, days):
        self.asked.append(f"back {start} {end} {day} {days}")
        return [ride("BlaBlaCar Bus", "2026-11-16T07:00:00Z", 1340), ride("BlaBlaCar Bus", "2026-11-16T09:00:00Z", 1500, changes=1),
                ride("FlixBus-eu", "2026-11-16T12:40:00Z", 1100)]   # a change, and another operator: neither is straight back

def test_the_stations_a_train_or_coach_calls_at_after_the_city_are_the_ones_it_reaches():
    found = ground.reached(TRIP, PATTERN)
    assert [(stop["stop"], stop["minutes"]) for stop in found] == [("Salamanca", 269), ("Paris - Bercy-Seine Bus Station", 1335),
                                                                    ("Metz - Bus Station", 1664)]   # not Lisbon: it was before Coimbra
    assert found[0]["mode"] == "bus" and found[0]["from_stop_id"] == "eu-blablacar-bus_AHX"

def test_the_operators_read_and_where_code_reads_their_prices():
    assert ground.operator_of("DB Fernverkehr AG") == ("Deutsche Bahn", "https://int.bahn.de", "")   # its timetable only
    assert ground.operator_of("NMBS/SNCB")[0] == "SNCB" and ground.operator_of("SNCF VOYAGEURS")[0] == "SNCF"
    assert ground.operator_of("FlixBus-eu") == ("FlixBus", "https://global.flixbus.com", "EU") and ground.operator_of("FlixBus-gb")[0] == "FlixBus"
    # The ones the planner prices (fares.py), recognised as it recognises them, with the country each sells in
    assert ground.operator_of("OEBB Personenverkehr AG", "LONG_DISTANCE", "at-wien") == ("ÖBB", "", "AT")
    assert ground.operator_of("RENFE OPERADORA", "HIGHSPEED_RAIL", "es-RENFE-Larga-Distancia-y-AVE_17000") == ("Renfe Operadora", "", "ES")
    assert ground.operator_of("RENFE OPERADORA", "REGIONAL_RAIL", "es-renfe-cercanias_1") is None   # Movelia does not sell the Cercanías
    assert ground.operator_of("Rede Expressos", "COACH", "pt-rede_1") is None
    assert ground.operator_of("Österreichische Bundesbahnen", "LONG_DISTANCE", "sk-zsr_1") == ("ÖBB", "", "AT")

def test_the_long_distance_trains_of_any_other_operator_are_read_by_their_timetable():
    assert ground.operator_of("Schweizerische Bundesbahnen SBB", "LONG_DISTANCE", "ch-x_1") == ("SBB", "https://www.sbb.ch", "")
    assert ground.operator_of("České dráhy, a.s.", "HIGHSPEED_RAIL", "sk-zsr_1")[0] == ground.operator_of("Ceske Drahy", "LONG_DISTANCE")[0] == "České dráhy"
    assert ground.operator_of("Dänische Staatsbahnen", "NIGHT_RAIL", "no-Entur_1")[0] == "DSB"
    assert ground.operator_of("Some Railway AB", "LONG_DISTANCE", "se-x_1") == ("Some Railway", "", "")   # unknown: its own name
    assert ground.operator_of("Some Railway AB", "REGIONAL_RAIL", "se-x_1") is None   # a local train is no trip
    assert ground.operator_of("Some Coaches", "COACH", "pl-x_1") is None

def test_one_train_named_after_several_railways_is_shown_once():
    train = {"lat": 52.52, "lon": 13.37, "mode": "train", "stop": "Berlin Hbf"}
    stops = [{**train, "operator": "DSB", "minutes": 238, "times": ["06:28", "10:28"], "sold": ""},
             {**train, "operator": "České dráhy", "minutes": 238, "times": ["06:28", "08:28", "10:28"], "sold": ""},
             {**train, "operator": "FlixBus", "minutes": 290, "times": ["06:28"], "sold": "EU", "mode": "bus"},
             {**train, "operator": "European Sleeper", "minutes": 288, "times": ["18:00"], "sold": ""},
             {**train, "operator": "SBB", "minutes": 240, "times": ["08:28", "09:00", "11:00", "13:00"], "sold": ""}]   # one in common only
    assert [option["operator"] for option in ground.to_city(stops, 52.52, 13.40)] == ["České dráhy", "SBB", "European Sleeper", "FlixBus"]

def test_the_trains_and_coaches_read_are_shared_out_between_the_operators():
    found = [{"operator": "SBB", "n": n} for n in range(100)] + [{"operator": "FlixBus", "n": 0}]
    taken = ground.shared(found)
    assert len(taken) == ground.MAX_TRIPS and {"operator": "FlixBus", "n": 0} in taken

def test_a_city_read_before_flixbus_was_added_is_read_again(monkeypatch):
    ground.write({ground.place_key(COIMBRA): {"checked": TODAY.isoformat(), "place": "Coimbra", "reached": []}})
    transitous = Timetables()
    assert ground.from_city(COIMBRA, TODAY, say=lambda text: None, transitous=transitous) and transitous.asked[0] == "stops"

def test_one_train_or_coach_of_each_pattern_is_read_and_kept_for_the_next_maps():
    transitous = Timetables()
    found = ground.from_city(COIMBRA, TODAY, say=lambda text: None, transitous=transitous)
    assert transitous.asked == ["stops", "departures", "trip a"]   # the bus of the 14th is the same one
    assert found[0]["weekdays"] == ["Thu", "Sat"] and found[0]["times"] == ["11:45"]   # local time, Lisbon
    again = Timetables()
    assert ground.from_city(COIMBRA, TODAY, say=lambda text: None, transitous=again) == found and again.asked == []

def stations() -> list[dict]:
    return ground.from_city(COIMBRA, TODAY, say=lambda text: None, transitous=Timetables())

def test_a_city_of_the_map_gets_the_quickest_of_each_operator_with_nothing_asked_and_no_price():
    beauvais = {"to": "BVA", "total_eur": 70.0}
    paris, rome = {"to": "ORY", "total_eur": 60.0, "also": [beauvais]}, {"to": "FCO", "total_eur": 80.0}
    centres = {"ORY": Place("Paris", "Paris", "Paris", "France", 48.86, 2.35)}
    airports = {"FCO": {"latitude": 41.8, "longitude": 12.25}}
    ground.look_up(COIMBRA, stations(), [paris, rome], centres, airports, say=lambda text: None)
    option, = paris["ground"]
    assert option["stop"] == "Paris - Bercy-Seine Bus Station" and option["grade"] == "B" and "back" not in option   # asked once the map is open
    assert "price_eur" not in option and paris["total_eur"] == 60.0   # a hint to check, never a price
    assert beauvais["ground"] == [option]   # Beauvais may become the cheapest airport of Paris once looked up
    assert "ground" not in rome
    assert ground.look_up(COIMBRA, None, [rome], centres, airports, say=lambda text: None) == []   # Transitous failed: none

def test_the_way_back_is_asked_once_the_map_is_open_and_not_for_a_way_priced_already():
    transitous = Timetables()
    days = Days("2026-11-12", "2026-11-12", "2026-11-16", "2026-11-20")
    salamanca, paris, metz = [{**stop, "grade": "B"} for stop in ground.reached(TRIP, PATTERN)]
    paris["price"] = {"out": {"price_eur": 30.0}, "back": {"price_eur": 32.0}, "total_eur": 62.0}
    assert ground.ways_back([[metz], [paris], [salamanca]], days, say=lambda text: None, transitous=transitous) == 2
    assert transitous.asked == ["back eu-blablacar-bus_ASN eu-blablacar-bus_AHX 2026-11-16 3",   # the quickest city first
                                "back eu-blablacar-bus_XMZ eu-blablacar-bus_AHX 2026-11-16 3"]   # the first 3 of the 5 days back
    assert metz["back"] == {"day": "2026-11-16", "days": 3, "times": ["16/11 08:00"], "minutes": 1340}   # Paris time; straight only
    assert "back" not in paris   # sold for the day already
    assert ground.ways_back([[metz]], Days("2026-11-12", "2026-11-12"), transitous=transitous) == 0   # one way: none

def test_the_last_stop_is_marked_and_the_site_is_the_one_the_timetable_gives():
    found = ground.reached(TRIP, PATTERN)
    assert [stop.get("last") for stop in found] == [None, None, True] and found[0]["site"] == "https://www.blablacar.com"
    sold = {"legs": [{**TRIP["legs"][0], "agencyUrl": "http://www.blablacar.pt"}]}
    assert ground.reached(sold, PATTERN)[0]["site"] == "https://www.blablacar.pt"   # always https

def city(name: str, country: str, latitude: float, longitude: float, population: int) -> dict:
    return {"name": name, "country": country, "lat": latitude, "lon": longitude, "population": population}

def test_a_city_no_flight_goes_to_is_a_destination_by_coach_only_with_no_price(monkeypatch):
    monkeypatch.setattr(ground, "cities", lambda: [city("Salamanca", "ES", 40.96, -5.66, 144_000)])
    paris = {"to": "ORY", "total_eur": 60.0}
    centres = {"ORY": Place("Paris", "Paris", "Paris", "France", 48.86, 2.35)}
    salamanca, metz = ground.look_up(COIMBRA, stations(), [paris], centres, {}, say=lambda text: None)
    assert (salamanca["name"], salamanca["country"], salamanca["minutes"]) == ("Salamanca", "ES", 269)   # on the way, quickest first
    assert (metz["name"], metz["country"], metz["modes"]) == ("Metz", "", ["bus"])   # the end of the line: no city known, its station
    assert "price_eur" not in metz and "total_eur" not in metz

def test_a_stop_on_the_way_is_shown_only_when_it_is_a_city(monkeypatch):
    stops = ground.reached(TRIP, PATTERN)
    monkeypatch.setattr(ground, "cities", lambda: [city("Salamanca", "ES", 40.96, -5.66, 30_000), city("Metz", "FR", 49.12, 6.18, 117_000)])
    assert [found["name"] for found in ground.only_by_ground(stops, COIMBRA, [], {})] == ["Metz"]   # a town, and a stop with no city near
    monkeypatch.setattr(ground, "cities", lambda: [city("Paris", "FR", 48.85, 2.35, 2_100_000), city("Montreuil", "FR", 48.86, 2.44, 110_000)])
    paris, metz = ground.only_by_ground(stops, COIMBRA, [], {})
    assert paris["name"] == "Paris" and metz["name"] == "Metz"   # the largest city near the station; Salamanca is no city: left out

def test_a_stop_in_a_suburb_is_the_large_city_next_to_it_and_a_station_in_a_city_is_that_city():
    grid = ground.city_grid([city("Amsterdam", "NL", 52.37, 4.89, 741_000), city("Diemen", "NL", 52.34, 4.96, 25_000),
                             city("The Hague", "NL", 52.08, 4.30, 474_000), city("Delft", "NL", 52.01, 4.36, 101_000)])
    assert ground.city_of({"lat": 52.335, "lon": 4.98}, grid)["name"] == "Amsterdam"   # 7 km out, in Diemen
    assert ground.city_of({"lat": 52.007, "lon": 4.357}, grid)["name"] == "Delft"      # The Hague is 9 km away

def test_a_city_of_a_million_people_is_shown_even_closer_than_the_trips_are(monkeypatch):
    near = lambda name, latitude: {**ground.reached(TRIP, PATTERN)[0], "stop": name, "lat": latitude, "lon": -8.43, "minutes": 30}
    stops = [near("Big", 40.58), near("Middle", 40.60), near("Home", 40.30)]   # 41 km, 43 km and 10 km from Coimbra
    monkeypatch.setattr(ground, "cities", lambda: [city("Big", "PT", 40.58, -8.43, 1_200_000), city("Middle", "PT", 40.60, -8.43, 500_000),
                                                   city("Home", "PT", 40.30, -8.43, 2_000_000)])
    assert [found["name"] for found in ground.only_by_ground(stops, COIMBRA, [], {})] == ["Big"]   # Home is the traveller's own city

def test_a_station_near_the_traveller_or_near_a_city_flown_to_is_no_destination_of_its_own():
    near = {**ground.reached(TRIP, PATTERN)[0], "stop": "Mealhada", "lat": 40.38, "lon": -8.45}   # 19 km from Coimbra
    found = ground.only_by_ground([near, *ground.reached(TRIP, PATTERN)], COIMBRA, [(48.86, 2.35)], {})
    assert [city["name"] for city in found] == ["Salamanca", "Metz"]   # Paris is flown to: its bus is shown with the flight
    at_airport = ground.only_by_ground(ground.reached(TRIP, PATTERN), COIMBRA, [], {"SLM": {"latitude": 40.95, "longitude": -5.67}})
    assert [city["name"] for city in at_airport] == ["Paris", "Metz"]   # a station by an airport is the way to a flight

def test_a_city_is_named_after_its_station_without_the_words_for_a_station():
    name = lambda stop: ground.station_name({"stop": stop})
    assert [name("Liège, Guillemins Bf"), name("Praha hl.n."), name("Köln Hbf"), name("Rotterdam Centraal"), name("Hbf")] == [
        "Liège", "Praha", "Köln", "Rotterdam", "Hbf"]
