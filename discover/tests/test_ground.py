"""The trains and coaches with no published prices that go straight to a city of the map (ground.py): read from the
timetables, matched to the cities, checked on the way back, never priced. No network: Transitous is a made-up one."""
from datetime import date

from discover import ground
from discover.sources import Days
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
    """A Transitous that knows one coach line and its way back, and counts what it is asked."""

    def __init__(self):
        self.asked = []

    def stops(self, place):
        self.asked.append("stops")
        return [{"stopId": "eu-blablacar-bus_AHX", "lat": 40.22, "lon": -8.44}]

    def departures(self, stop_id, start):
        self.asked.append("departures")
        return [departure("a", "LUX", "2026-11-12T11:45:00Z"), departure("b", "LUX", "2026-11-14T11:45:00Z"),   # the same coach, two days
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
    assert found[0]["mode"] == "coach" and found[0]["from_stop_id"] == "eu-blablacar-bus_AHX"

def test_only_the_operators_with_no_published_prices_are_read():
    assert ground.operator_of("DB Fernverkehr AG") == ("Deutsche Bahn", "https://int.bahn.de")
    assert ground.operator_of("NMBS/SNCB")[0] == "SNCB" and ground.operator_of("SNCF VOYAGEURS")[0] == "SNCF"
    assert ground.operator_of("FlixBus-eu") is None and ground.operator_of("RENFE OPERADORA") is None   # the planner prices these

def test_one_train_or_coach_of_each_pattern_is_read_and_kept_for_the_next_maps():
    transitous = Timetables()
    found = ground.from_city(COIMBRA, TODAY, say=lambda text: None, transitous=transitous)
    assert transitous.asked == ["stops", "departures", "trip a"]   # the coach of the 14th is the same one
    assert found[0]["weekdays"] == ["Thu", "Sat"] and found[0]["times"] == ["11:45"]   # local time, Lisbon
    again = Timetables()
    assert ground.from_city(COIMBRA, TODAY, say=lambda text: None, transitous=again) == found and again.asked == []

def test_a_city_of_the_map_gets_the_quickest_of_each_operator_with_its_way_back_and_no_price():
    transitous = Timetables()
    beauvais = {"to": "BVA", "total_eur": 70.0}
    paris, rome = {"to": "ORY", "total_eur": 60.0, "also": [beauvais]}, {"to": "FCO", "total_eur": 80.0}
    centres = {"ORY": Place("Paris", "Paris", "Paris", "France", 48.86, 2.35)}
    airports = {"FCO": {"latitude": 41.8, "longitude": 12.25}}
    days = Days("2026-11-12", "2026-11-12", "2026-11-16", "2026-11-20")
    ground.look_up(COIMBRA, days, [paris, rome], centres, airports, TODAY, say=lambda text: None, transitous=transitous)
    option, = paris["ground"]
    assert option["stop"] == "Paris - Bercy-Seine Bus Station" and option["grade"] == "B"
    assert "back eu-blablacar-bus_XPB eu-blablacar-bus_AHX 2026-11-16 3" in transitous.asked   # the first 3 of the 5 days back
    assert option["back"] == {"day": "2026-11-16", "days": 3, "times": ["16/11 08:00"], "minutes": 1340}   # Paris time; straight only
    assert "price_eur" not in option and paris["total_eur"] == 60.0   # a hint to check, never a price
    assert beauvais["ground"] == [option]   # Beauvais may become the cheapest airport of Paris once looked up
    assert "ground" not in rome

def test_a_city_no_flight_goes_to_is_a_destination_by_coach_only_with_no_price():
    transitous = Timetables()
    paris = {"to": "ORY", "total_eur": 60.0}
    centres = {"ORY": Place("Paris", "Paris", "Paris", "France", 48.86, 2.35)}
    airports = {"ETZ": {"code": "ETZ", "city": "Louvigny", "country": "FR", "latitude": 48.98, "longitude": 6.25}}   # Metz's, 16 km out
    days = Days("2026-11-12", "2026-11-12", "2026-11-16", "2026-11-20")
    salamanca, metz = ground.look_up(COIMBRA, days, [paris], centres, airports, TODAY, say=lambda text: None, transitous=transitous)
    assert (salamanca["name"], salamanca["country"], salamanca["minutes"]) == ("Salamanca", "", 269)   # quickest first; no airport near it
    assert (metz["name"], metz["country"], metz["modes"]) == ("Metz", "FR", ["coach"])   # named after its station, not the airport's town
    assert "price_eur" not in metz and "total_eur" not in metz and metz["ground"][0]["back"] is not None

def test_a_station_near_the_traveller_or_near_a_city_flown_to_is_no_destination_of_its_own():
    near = {**ground.reached(TRIP, PATTERN)[0], "stop": "Mealhada", "lat": 40.38, "lon": -8.45}   # 19 km from Coimbra
    found = ground.only_by_ground([near, *ground.reached(TRIP, PATTERN)], COIMBRA, [(48.86, 2.35)], {})
    assert [city["name"] for city in found] == ["Salamanca", "Metz"]   # Paris is flown to: its coach is shown with the flight
    at_airport = ground.only_by_ground(ground.reached(TRIP, PATTERN), COIMBRA, [], {"SLM": {"latitude": 40.95, "longitude": -5.67}})
    assert [city["name"] for city in at_airport] == ["Paris", "Metz"]   # a station by an airport is the way to a flight

def test_a_city_is_named_after_its_station_without_the_words_for_a_station():
    name = lambda stop: ground.city_name({"stop": stop, "lat": 50.6, "lon": 5.6}, {})[0]
    assert [name("Liège, Guillemins Bf"), name("Praha hl.n."), name("Köln Hbf"), name("Rotterdam Centraal"), name("Hbf")] == [
        "Liège", "Praha", "Köln", "Rotterdam", "Hbf"]
