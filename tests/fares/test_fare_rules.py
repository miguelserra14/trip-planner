"""The rules of the operators' prices on small made-up inputs: no service, no checkpoint."""
from datetime import date, timedelta

import pytest

from trip_planner.itineraries.legs import estimated_price, price_with_tickets
from trip_planner.sources import fares
from trip_planner.sources.operators import alsa, flixbus, movelia, obilet, rede_expressos
from trip_planner.places import Place
from trip_planner.sources.fares import FareSource, Trip

TRAVEL = (date.today() + timedelta(days=70)).isoformat()
SERVICE = (date.today() + timedelta(days=3)).isoformat()

def step(agency: str = "", rail: bool = True, time: str = "10:00", arrive: str = "11:00", from_id: str = "", to_id: str = "", km: int = 50) -> dict:
    return {"time": time, "arrive": arrive, "line": "X", "agency": agency, "from": "A", "to": "B", "rail": rail, "coach": not rail,
            "km": km, "from_id": from_id, "to_id": to_id, "from_at": [48.0, 16.0], "to_at": [48.3, 14.3]}

def trip(leave: str = "10:28", arrive: str = "11:43") -> Trip:
    return Trip("A", "B", "", "", (), (), TRAVEL, leave, arrive)

@pytest.fixture
def sells(monkeypatch):
    """Operators that answer from a table instead of the network: {(operator, day, leave): price}."""
    prices: dict = {}
    asked: list = []

    def source(name: str, through: bool) -> FareSource:
        def fare(wanted: Trip) -> float | None:
            asked.append((name, wanted.day, wanted.leave, wanted.arrive))
            return prices.get((name, wanted.day, wanted.leave))
        return FareSource(name, "XX", lambda s: s["agency"] == name, fare, through)

    monkeypatch.setattr(fares, "FARE_SOURCES", [source("Rail", True), source("Coach", False)])
    monkeypatch.setattr(fares, "CACHE", {})
    prices["asked"] = asked
    return prices

def test_a_departure_is_the_same_within_a_few_minutes():
    assert trip().matches("2026-10-29T10:28:00.000", "2026-10-29T11:43:00.000")
    assert trip().matches("10:29", "11:47")            # the two sources do not agree on the minute a train gets in
    assert not trip().matches("10:33", "11:43")        # another train
    assert not trip().matches("10:28", "12:10")        # the same departure, a slower way

def test_each_operator_is_recognised_by_its_vehicles():
    name = lambda s: getattr(fares.source_of(s), "name", None)
    assert name(step("OEBB Personenverkehr AG Kundenservice")) == "ÖBB"
    assert name(step("Trenord")) == name(step("TRENITALIA")) == "Trenitalia"
    assert name(step("MÁV", from_id="hu-mav_2121")) == "MÁV"
    assert name(step("Elron")) == "Elron"
    assert name(step("STUDENT AGENCY k.s.", rail=False)) == "RegioJet"
    assert name(step("FlixBus-eu", rail=False, from_id="eu-flixbus_a", to_id="eu-flixbus_b")) == "FlixBus"
    assert name(step("Chemins de Fer Luxembourgeois", from_id="lu-x_1", to_id="lu-x_2")).startswith("Luxembourg")
    assert name(step("NMBS/SNCB", from_id="be-sncb_8200100", to_id="be-sncb_8200120")).startswith("Luxembourg")
    assert name(step("NMBS/SNCB", from_id="be-sncb_8200100", to_id="be-sncb_8866001")) is None   # leaves Luxembourg
    assert name(step("NS")) is None
    on_alsa = step("ALSA", rail=False, from_id="es-ALSA-autobuses_0000555500000001", to_id="es-ALSA-autobuses_0000030500000001")
    assert name(on_alsa) == "ALSA" and alsa.alsa_stop(on_alsa["from_id"]) == "5555"   # its site's own stop id
    renfe = step("RENFE OPERADORA", from_id="es-RENFE-Larga-Distancia-y-AVE_17000", to_id="es-RENFE-Larga-Distancia-y-AVE_03216")
    assert name(renfe) == "Movelia" and name({**renfe, "rail": False}) is None                    # a walk out of the station is not a ticket
    assert name(step("Renfe Cercanias", from_id="es-Cercanías-Renfe_18101", to_id="es-Cercanías-Renfe_18000")) is None   # suburban

def test_trains_in_a_row_of_one_railway_are_one_ticket(sells):
    steps = [step("Rail", time="10:00", arrive="10:30"), step("Rail", time="10:40", arrive="11:30"), step("City bus", rail=False),
             step("Rail", time="12:00", arrive="12:30"), step("Coach", rail=False), step("Coach", rail=False)]
    assert [(source.name, positions) for source, positions in fares.runs(steps)] == [
        ("Rail", [0, 1]), ("Rail", [3]), ("Coach", [4]), ("Coach", [5])]

def test_the_travel_date_is_asked_first_then_the_day_of_the_timetable(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    sells[("Coach", SERVICE, "12:00")] = 7.0
    steps = [step("Rail"), step("Coach", rail=False, time="12:00", arrive="13:00")]
    found = fares.tickets(steps, TRAVEL, SERVICE)
    assert found == [{"positions": [0], "operator": "Rail", "price_eur": 20.0, "travel_day": True, "tariff": False, "confirms": False},
                     {"positions": [1], "operator": "Coach", "price_eur": 7.0, "travel_day": False, "tariff": False, "confirms": False}]
    assert ("Rail", SERVICE, "10:00", "11:00") not in sells["asked"]   # found for the travel date: not asked again

def test_an_operator_is_asked_once_for_the_same_ticket(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    fares.tickets([step("Rail")], TRAVEL, SERVICE)
    fares.tickets([step("Rail")], TRAVEL, SERVICE)
    assert len(sells["asked"]) == 1

def test_an_operator_that_fails_leaves_no_ticket(monkeypatch):
    def broken(wanted: Trip) -> float | None:
        raise RuntimeError("the site changed")
    monkeypatch.setattr(fares, "FARE_SOURCES", [FareSource("Rail", "XX", lambda s: True, broken)])
    monkeypatch.setattr(fares, "CACHE", {})
    assert fares.tickets([step("Rail")], TRAVEL, SERVICE) == []

def leg(steps: list, kind: str = "estimate") -> dict:
    return {"kind": "timetable", "steps": steps, "service_day": SERVICE, "price_kind": kind,
            "price_eur": sum(estimated_price(s["km"]) for s in steps)}

FLIGHT = {"date": TRAVEL}

def test_every_vehicle_priced_for_the_travel_date_is_a_confirmed_price(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail")])
    price_with_tickets(priced, FLIGHT["date"], False)
    assert (priced["price_eur"], priced["price_kind"], priced["confirmed_eur"]) == (20.0, "operator", 20.0)

def test_a_price_of_another_day_is_typical_and_not_confirmed(sells):
    sells[("Rail", SERVICE, "10:00")] = 20.0
    priced = leg([step("Rail")])
    price_with_tickets(priced, FLIGHT["date"], False)
    assert (priced["price_eur"], priced["price_kind"], priced["confirmed_eur"]) == (20.0, "operator_typical", 0)

def test_vehicles_nobody_priced_keep_their_estimate(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail"), step("City bus", rail=False, km=10)])
    price_with_tickets(priced, FLIGHT["date"], False)
    assert priced["price_kind"] == "partly" and priced["price_eur"] == 20.0 + estimated_price(10) and priced["confirmed_eur"] == 20.0

def test_free_trains_are_not_asked_for(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail")], kind="free")
    price_with_tickets(priced, FLIGHT["date"], True)
    assert sells["asked"] == [] and "tickets" not in priced

def test_a_models_price_for_the_whole_way_is_kept_when_only_part_is_sold(sells):
    sells[("Rail", TRAVEL, "10:00")] = 20.0
    priced = leg([step("Rail"), step("City bus", rail=False)], kind="model")
    before = priced["price_eur"]
    price_with_tickets(priced, FLIGHT["date"], False)
    assert (priced["price_eur"], priced["price_kind"]) == (before, "model")

def test_a_leg_without_vehicles_is_left_alone(sells):
    model_leg = {"kind": "model", "steps": [], "price_kind": "model", "price_eur": 9.0}
    price_with_tickets(model_leg, FLIGHT["date"], False)
    assert model_leg["price_eur"] == 9.0

# ---------------------------------------------------------------- Rede Expressos, on a made-up answer
LAGOS = Place("Lagos", "Lagos", "Lagos", "Portugal", 37.1028, -8.6730)
FARO_AIRPORT = {"code": "FAO", "country": "PT", "latitude": 37.0144, "longitude": -7.9659, "km": 70}

def coach(leave: str, arrive: str, to: str, price: float = 7.3, changes: int = 0) -> dict:
    return {"departureDate": leave, "arrivalDate": arrive, "from": {"name": "Faro Aeroporto"}, "to": {"name": to},
            "departureCarrier": {"name": "Rede Expressos"}, "minPrice": price, "availableSeats": 40, "transfersCount": changes, "distance": 90}

@pytest.fixture
def coaches(monkeypatch):
    """Rede Expressos answers every search with these rides, and keeps what it was asked."""
    answer = {"rides": [], "asked": []}

    class Answer:
        def raise_for_status(self):
            return self

        def json(self):
            return {"outgoingSchedules": answer["rides"]}

    def post(url, headers=None, json=None):
        answer["asked"].append(json)
        return Answer()
    monkeypatch.setattr(fares.http, "post", post)
    return answer

def test_only_the_rides_of_the_town_asked_are_kept(coaches):
    coaches["rides"] = [coach("2026-12-17 09:10", "2026-12-17 12:10", "Lagos"), coach("2026-12-17 13:40", "2026-12-17 17:05", "Alvor")]
    rides = rede_expressos.rede_expressos_rides(LAGOS, FARO_AIRPORT, "2026-12-17", to_airport=False)
    assert [ride["steps"][0]["to"] for ride in rides] == ["Lagos"]   # Alvor is 8 km away: another town
    assert rides[0]["grade"] == "A" and rides[0]["price_eur"] == 7.3

def test_a_coach_that_arrives_after_midnight_counts_past_24_hours(coaches):
    coaches["rides"] = [coach("2026-12-17 22:10", "2026-12-18 01:30", "Lagos", changes=1)]
    ride = rede_expressos.rede_expressos_rides(LAGOS, FARO_AIRPORT, "2026-12-17", to_airport=False)[0]
    assert (ride["leave"], ride["arrive"]) == (22 * 60 + 10, 25 * 60 + 30)
    assert "1" in ride["steps"][0]["line"]   # the change of coach is said

def test_rede_expressos_is_not_asked_outside_portugal(coaches):
    madrid = Place("Madrid", "Madrid", "Madrid", "Spain", 40.4168, -3.7038)
    assert rede_expressos.rede_expressos_rides(madrid, {**FARO_AIRPORT, "country": "ES", "latitude": 40.49, "longitude": -3.57}, "2026-12-17") is None
    assert coaches["asked"] == []

# ---------------------------------------------------------------- Movelia, on a made-up answer
SPANISH_PLACES = {"madrid": "MADRID", "salamanca": "SALAMANCA", "sevilla": "SEVILLA", "aeropuerto alicante-elche": "AEROPUERTO ALICANTE-ELCHE",
                  "alicante": "ALICANTE / ALACANT", "murcia": "MURCIA"}
MADRID_AIRPORT = {"code": "MAD", "country": "ES", "name": "Adolfo Suárez Madrid–Barajas Airport", "city": "Madrid", "km": 200,
                  "search_text": "adolfo suarez madrid barajas airport madrid"}

def test_an_airport_is_searched_by_the_name_movelia_gives_it():
    alicante = {**MADRID_AIRPORT, "name": "Alicante-Elche Miguel Hernández Airport", "city": "Alicante"}
    seville = {**MADRID_AIRPORT, "name": "Seville Airport", "city": "Seville", "search_text": "seville airport seville sevilla, sevilla airport"}
    assert movelia.movelia_airport(alicante, SPANISH_PLACES) == "AEROPUERTO ALICANTE-ELCHE"   # its own name for the airport
    assert movelia.movelia_airport(MADRID_AIRPORT, SPANISH_PLACES) == "MADRID"                 # the city, then only the airport's stations
    assert movelia.movelia_airport(seville, SPANISH_PLACES) == "SEVILLA"                       # a name of the airport database

def combination(leave: str, start: str, direct: str = "1", kind: str = "1", price: str = "22,93") -> dict:
    return {"salable": "1", "direct": direct, "busOrBoat": kind, "departureDate": "05/11/2026", "arrivalDate": "05/11/2026",
            "departureTime": leave, "arrivalTime": "23:00", "originName": start, "destinationName": "SALAMANCA",
            "companyName": "MONBUS LARGA DISTANCIA S.L.U.", "basicPrice": price}

def test_only_direct_rides_from_a_station_of_the_airport_are_kept(monkeypatch):
    answer = [combination("10:15", "Aeropuerto Adolfo Suárez T4"), combination("06:40", "Madrid Estación Sur"),
              combination("11:00", "Aeropuerto Adolfo Suárez T4", direct="0"), combination("12:00", "AEROPUERTO T4 TREN", kind="3", price="18,05"),
              combination("13:00", "Aeropuerto Adolfo Suárez T4", kind="4")]   # a flight

    class Answer:
        status_code = 200

        def raise_for_status(self):
            return self

        def json(self):
            return {"schedules": {"responseData": {"travel": {"outbound": {"section": [{"combination": answer}]}}}}}
    monkeypatch.setattr(movelia, "MOVELIA_SESSION", {"places": SPANISH_PLACES, "token": "x"})
    monkeypatch.setattr(movelia, "MOVELIA_SEARCHES", {})
    monkeypatch.setattr(fares.http, "post", lambda url, json=None, headers=None, timeout=None: Answer())
    rides = movelia.movelia_rides(Place("Salamanca", "Salamanca", "Salamanca", "Spain", 40.96, -5.66), MADRID_AIRPORT, "2026-11-05", to_airport=False)
    assert [(ride["leave"], ride["price_eur"], ride["steps"][0]["rail"]) for ride in rides] == [(10 * 60 + 15, 22.93, False), (12 * 60, 18.05, True)]

# ---------------------------------------------------------------- Obilet, on a made-up answer
def test_an_airport_of_obilet_is_the_one_whose_name_shares_most_words(monkeypatch):
    istanbul = [{"id": 6664, "text": "İstanbul Havalimanı - İstanbul", "isDomestic": True},
                {"id": 6746, "text": "İstanbul Sabiha Gökçen Havalimanı - İstanbul", "isDomestic": True},
                {"id": 350, "text": "İstanbul Anadolu - İstanbul", "isDomestic": True}]
    skopje = [{"id": 1818, "text": "Üsküp - Makedonya Cumhuriyeti", "isDomestic": False}]
    monkeypatch.setattr(obilet, "OBILET_SESSION", {"places": {"istanbul": istanbul, "skopje": skopje}})
    assert obilet.obilet_place(["Istanbul"], "TR", True)["id"] == 6664                           # the shorter of two that tie
    assert obilet.obilet_place(["Sabiha Gokcen", "Istanbul"], "TR", True)["id"] == 6746
    assert obilet.obilet_place(["Istanbul"], "TR", False)["id"] == 350
    assert obilet.obilet_place(["Skopje"], "MK", False)["id"] == 1818                           # its country, written in Turkish
    assert obilet.obilet_place(["Skopje"], "AL", False) is None

def test_a_coach_from_the_airports_city_starts_with_the_way_there_estimated(monkeypatch):
    journey = {"journey": {"departure": "2026-11-05T07:05:00", "arrival": "2026-11-05T09:10:00", "internet-price": 11.0, "currency": "EUR",
                           "origin": "Podgorica Central Bus Station", "destination": "Kotor", "kind": "Bus"},
               "partner-name": "Royal Travel", "is-active": True, "available-seats": 30}

    class Answer:
        def json(self):
            return {"journeys": [journey]}
    monkeypatch.setattr(obilet, "OBILET_SESSION", {"opened": True})
    monkeypatch.setattr(obilet, "obilet_ask", lambda method, url, **options: Answer())
    airport = {"code": "TGD", "country": "ME", "km": 70}
    ride = obilet.obilet_search({"id": 7303, "display": "Kotor"}, {"id": 7304, "display": "Podgorica"}, 13.0, airport, "2026-11-05", False)[0]
    first, coach = ride["steps"]
    assert first["estimated"] and first["from"] == "TGD" and first["to"] == "Podgorica Central Bus Station"
    assert ride["grade"] == "C" and ride["confirmed_eur"] == 11.0 and ride["price_eur"] > 11.0   # only the coach is a real price
    assert ride["leave"] == 7 * 60 + 5 - first["minutes"] and coach["time"] == "07:05"

def test_obilet_means_the_city_of_a_district_and_reads_its_turkish_countries(monkeypatch):
    places = {"rethymno": [{"id": 149739, "text": "Agia Triada Rethymno - Resmo, Yunanistan", "display": "Agia Triada Rethymno"}],
              "resmo": [{"id": 9057, "text": "Resmo - Yunanistan", "display": "Resmo"}],
              "novi sad": [{"id": 7309, "text": "Novi Sad - Sırbistan", "display": "Novi Sad"}]}
    monkeypatch.setattr(obilet, "OBILET_SESSION", {"places": places})
    assert obilet.obilet_place(["Ρέθυμνο", "Rethymno"], "GR", False)["id"] == 9057   # another alphabet is not asked, a district is its city
    assert obilet.obilet_place(["Novi Sad"], "RS", False)["id"] == 7309             # Sırbistan, with a Turkish dotless i

def test_obilet_rides_to_the_towns_around_the_one_asked_are_left_out(monkeypatch):
    def journey(destination: str, hour: int) -> dict:
        return {"journey": {"departure": f"2026-11-05T{hour:02d}:00:00", "arrival": f"2026-11-05T{hour + 2:02d}:00:00", "internet-price": 600.0, "currency": "RSD",
                            "sorting-price": 5.11, "origin": "Athens Airport", "destination": destination, "kind": "Bus"},
                "partner-name": "KTEL", "is-active": True, "available-seats": 30}

    class Answer:
        def json(self):
            return {"journeys": [journey("Nemea Barbecue Restaurant", 6), journey("Nafplio Tripoleos 30 Bus Stop", 7), journey("Main Bus Station (Otogar)", 8)]}
    monkeypatch.setattr(obilet, "OBILET_SESSION", {"opened": True})
    monkeypatch.setattr(obilet, "obilet_ask", lambda method, url, **options: Answer())
    rides = obilet.obilet_search({"id": 9158, "display": "Nafplion"}, {"id": 1, "display": "Athens Airport"}, 0.0,
                                {"code": "ATH", "country": "GR", "km": 140}, "2026-11-05", False, ("Nafplio", "Nafplio"))
    assert [ride["steps"][0]["to"] for ride in rides] == ["Nafplio Tripoleos 30 Bus Stop", "Main Bus Station (Otogar)"]   # Nemea is 45 km away
    assert rides[0]["price_eur"] == 5.11   # its own price in EUR, where the central bank has no rate

def test_flixbus_is_not_asked_from_a_city_to_itself(monkeypatch):
    monkeypatch.setattr(flixbus, "flixbus_city", lambda *args: "innsbruck")   # its airport is a stop of the city
    monkeypatch.setattr(flixbus, "flixbus_search", lambda *args: pytest.fail("asked from Innsbruck to Innsbruck"))
    airport = {"city": "Innsbruck", "name": "Innsbruck Airport", "latitude": 47.26, "longitude": 11.34, "km": 3}
    assert flixbus.flixbus_rides(Place("Innsbruck", "Innsbruck", "Innsbruck", "Austria", 47.27, 11.39), airport, "2026-11-12", False) is None

def test_obilet_takes_no_airport_of_the_same_city_that_shares_no_name(monkeypatch):
    rome = [{"id": 1, "text": "Giovan Battista Pastine International Airport - Roma, Italya"}]   # Ciampino, not Fiumicino
    monkeypatch.setattr(obilet, "OBILET_SESSION", {"places": {"rome-fiumicino leonardo da vinci international airport": rome, "rome": rome}})
    assert obilet.obilet_place(["Rome-Fiumicino Leonardo da Vinci International Airport", "Rome"], "IT", True) is None

def test_a_coach_from_a_stop_of_the_airport_needs_no_estimated_way(monkeypatch):
    journey = {"journey": {"departure": "2026-11-12T08:45:00", "arrival": "2026-11-12T09:55:00", "internet-price": 5.26, "currency": "EUR",
                           "origin": "Naples Airport", "destination": "Salerno Bus Station", "kind": "Bus"},
               "partner-name": "Sita", "is-active": True, "available-seats": 30}

    class Answer:
        def json(self):
            return {"journeys": [journey]}
    monkeypatch.setattr(obilet, "OBILET_SESSION", {"opened": True})
    monkeypatch.setattr(obilet, "obilet_ask", lambda method, url, **options: Answer())
    airport = {"code": "NAP", "name": "Naples International Airport", "city": "Naples", "country": "IT", "km": 50}
    ride = obilet.obilet_search({"id": 2, "display": "Salerno"}, {"id": 1, "display": "Napoli"}, 9.0, airport, "2026-11-12", False, ("Salerno",))[0]
    assert ride["grade"] == "A" and len(ride["steps"]) == 1 and ride["leave"] == 8 * 60 + 45

# ---------------------------------------------------------------- days not on sale yet
def test_a_day_not_on_sale_says_from_when_it_can_be_known():
    from trip_planner.agents.steps import not_on_sale_warnings
    day = (date.today() + timedelta(days=100)).isoformat()
    ground = {"not_on_sale": [{"source": "Rede Expressos", "airport": "OPO", "day": day, "to_airport": False, "days": 71},
                              {"source": "Obilet", "airport": "IST", "day": day, "to_airport": False, "days": 120}]}
    shown = [{"flight": {"date": day, "departure_airport": "CRL", "arrival_airport": "OPO"}}]
    warnings = not_on_sale_warnings(shown, ground)
    assert len(warnings) == 1 and "OPO" in warnings[0]   # Istanbul is not in the options shown
    assert (date.fromisoformat(day) - timedelta(days=71)).strftime("%d/%m/%Y") in warnings[0]

def test_movelia_says_when_a_day_is_not_on_sale_and_when_nothing_runs(monkeypatch):
    answers = []

    class Refusal:
        status_code = 400

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body
    monkeypatch.setattr(movelia, "MOVELIA_SESSION", {"places": SPANISH_PLACES, "token": "x"})
    monkeypatch.setattr(movelia, "MOVELIA_SEARCHES", {})
    monkeypatch.setattr(fares.http, "post", lambda url, json=None, headers=None, timeout=None: answers.pop(0))
    salamanca = Place("Salamanca", "Salamanca", "Salamanca", "Spain", 40.96, -5.66)
    answers.append(Refusal({"NoSchedules": True, "ShowMsgNotSchedulesYet": False}))
    assert movelia.movelia_rides(salamanca, MADRID_AIRPORT, "2026-11-05", to_airport=False) == []
    answers.append(Refusal({"NoSchedules": False, "ShowMsgNotSchedulesYet": True}))
    with pytest.raises(fares.NotOnSale):
        movelia.movelia_rides(salamanca, MADRID_AIRPORT, "2027-03-05", to_airport=False)

def test_movelia_knows_a_stop_by_the_longest_start_of_its_name():
    places = {"madrid": "MADRID", "barcelona": "BARCELONA", "san sebastian": "SAN SEBASTIAN / DONOSTIA"}
    assert movelia.movelia_town("Madrid-Puerta de Atocha-Almudena Grandes", places) == ("MADRID", {"puerta", "de", "atocha", "almudena", "grandes"})
    assert movelia.movelia_town("Barcelona El Clot", places)[0] == "BARCELONA"
    assert movelia.movelia_town("Sevilla-Santa Justa", places) == (None, set())
