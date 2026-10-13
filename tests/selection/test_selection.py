"""Which flights get their ground transport looked up (itineraries/selection.py), on made-up trips: no service.

The last tests play whole searches in a made-up world where the price and the time of the transport of every
airport are known, so the best itinerary of all is known too, and compare it with what the rounds find."""
import random

import pytest

from trip_planner import current
from trip_planner.itineraries import selection
from trip_planner.itineraries.selection import next_flights
from trip_planner.sources import timetables

def flight(number: int, price: float, departure: str = "OPO", arrival: str = "BRU", day: str = "2026-12-17", minutes: int = 150) -> dict:
    return {"id": f"F{number}", "price_eur": price, "departure_airport": departure, "arrival_airport": arrival, "date": day,
            "duration_minutes": minutes, "arrival_time": "12:00", "stops": 0}

def itinerary(of: dict, to_price: float = 10, from_price: float = 10, to_minutes: int = 120, from_minutes: int = 60,
              flight_price: float | None = None, grade: str = "B") -> dict:
    settings = current.SETTINGS
    cost = (of["price_eur"] if flight_price is None else flight_price) + to_price + from_price
    minutes = to_minutes + settings.min_airport_minutes + of["duration_minutes"] + settings.min_landing_minutes + from_minutes
    return {"flight": of, "to": {"price_eur": to_price, "leave": 0, "arrive": to_minutes}, "from": {"price_eur": from_price, "leave": 0, "arrive": from_minutes},
            "total_cost": cost, "total_minutes": minutes, "schedule_grade": grade}

def ids(found: tuple) -> list:
    return [chosen["id"] for chosen in found[0]]

def test_the_first_round_is_the_cheapest_flights_with_one_of_every_departure_airport():
    flights = [flight(n, 20 + n) for n in range(12)] + [flight(50, 90, "LIS"), flight(51, 95, "VGO")]
    chosen, reason = next_flights(flights, {})
    assert reason == "" and len(chosen) == selection.BATCH
    assert {"OPO", "LIS", "VGO"} <= {picked["departure_airport"] for picked in chosen}
    assert chosen[0]["id"] == "F0"

def test_a_flight_whose_ticket_alone_costs_more_than_the_best_itinerary_is_left_out():
    cheap, dear = flight(1, 30), flight(2, 200)
    assert next_flights([cheap, dear], {"F1": itinerary(cheap)}) == ([], "beaten")

def test_a_dearer_flight_of_an_airport_not_seen_yet_is_still_looked_up():
    """The transport of its airport may cost nothing: 15 EUR more on the ticket does not rule it out."""
    cheap, other = flight(1, 30, "OPO"), flight(2, 45, "LIS")
    assert ids(next_flights([cheap, other], {"F1": itinerary(cheap, to_price=30)})) == ["F2"]

def test_a_dearer_flight_of_an_airport_known_to_be_dear_to_reach_is_left_out():
    cheap, same_airport = flight(1, 30, "OPO"), flight(2, 45, "OPO")
    assert next_flights([cheap, same_airport], {"F1": itinerary(cheap, to_price=30)}) == ([], "beaten")

def test_a_much_faster_flight_is_looked_up_even_when_it_cannot_be_cheaper():
    """The recommended itinerary is the cheapest once the extra hours are priced: four hours less are worth a dearer ticket."""
    slow, fast = flight(1, 30, minutes=420), flight(2, 60, minutes=120)
    assert ids(next_flights([slow, fast], {"F1": itinerary(slow, to_price=20)})) == ["F2"]

def test_the_airlines_own_price_may_be_below_the_one_found():
    """50 EUR in all is the best found. A ticket found at 37 EUR may be 29.60 on the airline's site: with the
    20 EUR of transport it could still be cheaper, so it is looked up. One found at 60 EUR could not."""
    cheap = flight(1, 30)
    known = {"F1": itinerary(cheap, to_price=10, from_price=10)}
    assert ids(next_flights([cheap, flight(2, 37)], known)) == ["F2"]
    assert next_flights([cheap, flight(3, 60)], known) == ([], "beaten")

def test_an_itinerary_below_the_reliability_asked_for_does_not_stop_the_search():
    unreliable, other = flight(1, 30), flight(2, 300, "LIS")
    assert ids(next_flights([unreliable, other], {"F1": itinerary(unreliable, grade="C")})) == []
    reliable = itinerary(flight(3, 500, "VGO"), grade="B")
    assert ids(next_flights([unreliable, other, reliable["flight"]], {"F1": itinerary(unreliable, grade="C"), "F3": reliable})) == ["F2"]

def test_an_airport_and_date_whose_flights_all_fail_is_given_up():
    flights = [flight(n, 20 + n) for n in range(6)] + [flight(9, 80, "LIS")]
    failed = {f"F{n}": None for n in range(selection.FAILURES_PER_AIRPORT_DAY)}
    assert ids(next_flights(flights, failed)) == ["F9"]

def test_the_search_ends_at_the_limit_and_says_so():
    flights = [flight(n, 20, "OPO" if n % 2 else "LIS", day=f"2026-12-{10 + n % 9}") for n in range(60)]
    looked_up = {f"F{n}": itinerary(flights[n], to_price=0, from_price=0) for n in range(selection.MAX_FLIGHTS_CHECKED)}
    assert next_flights(flights, looked_up) == ([], "limit")

def test_it_gives_up_when_nothing_makes_an_itinerary():
    flights = [flight(n, 20, f"A{n:02d}"[:3] + str(n % 10), day=f"2026-12-{10 + n % 9}") for n in range(40)]
    assert next_flights(flights, {f"F{n}": None for n in range(selection.STOP_WITHOUT_ITINERARY)}) == ([], "none")

def test_everything_looked_up_is_the_end():
    only = flight(1, 30)
    assert next_flights([only], {"F1": itinerary(only)}) == ([], "all")

# ---------------------------------------------------------------- whole searches in a made-up world
def world(seed: int) -> tuple:
    """Flights between random airports, and what the transport of each airport costs and takes. An airport may be
    out of reach. The airline's own price is the one found or up to 12% less."""
    rng = random.Random(seed)
    departures = {f"D{n}": (rng.choice([3, 8, 15, 25, 40]), rng.choice([60, 120, 200, 320])) if rng.random() > 0.15 else None for n in range(rng.randint(2, 7))}
    arrivals = {f"L{n}": (rng.choice([0, 4, 9, 18, 35]), rng.choice([30, 60, 120, 240])) for n in range(rng.randint(1, 5))}
    days = [f"2026-12-{17 + n}" for n in range(rng.randint(1, 3))]
    flights = [flight(n, rng.choice([19, 25, 30, 38, 45, 60, 80, 110, 150]) + rng.randint(0, 9), rng.choice(list(departures)), rng.choice(list(arrivals)),
                      rng.choice(days), rng.choice([120, 150, 180, 240])) for n in range(rng.randint(10, 70))]
    def look_up(of: dict) -> dict | None:
        if departures[of["departure_airport"]] is None:
            return None
        (to_price, to_minutes), (from_price, from_minutes) = departures[of["departure_airport"]], arrivals[of["arrival_airport"]]
        own_price = round(of["price_eur"] * random.Random(of["id"] + str(seed)).choice([1, 1, 0.95, 0.88]), 2)
        return itinerary(of, to_price, from_price, to_minutes, from_minutes, own_price)
    return sorted(flights, key=lambda f: f["price_eur"]), look_up

def missed(looked_up: dict, flights: list, look_up) -> list:
    """The flights never looked up that would have been the recommended itinerary, had they been."""
    found = selection.counted([made for made in looked_up.values() if made])
    others = [look_up(of) for of in flights if of["id"] not in looked_up]
    return [made["flight"]["id"] for made in others if made and selection.could_win(made["total_cost"], made["total_minutes"], found)]

def in_rounds(flights: list, look_up) -> tuple:
    looked_up: dict = {}
    while True:
        batch, reason = next_flights(flights, looked_up)
        if not batch:
            return looked_up, reason
        looked_up.update({chosen["id"]: look_up(chosen) for chosen in batch})

SEEDS = range(400)

def test_no_flight_left_out_would_have_been_recommended_whenever_the_rounds_say_so():
    sure = 0
    for seed in SEEDS:
        flights, look_up = world(seed)
        looked_up, reason = in_rounds(flights, look_up)
        if reason in ("beaten", "all"):
            sure += 1
            assert missed(looked_up, flights, look_up) == [], f"world {seed}"
    assert sure > 0.9 * len(SEEDS), f"only {sure} of {len(SEEDS)} searches ended sure of their result"

def test_the_rounds_miss_less_and_look_up_little_more_than_the_fixed_number_did():
    """The rule before: the 16 cheapest flights, three per departure airport and date."""
    missed_before = missed_now = looked_before = looked_now = 0
    for seed in SEEDS:
        flights, look_up = world(seed)
        before = {of["id"]: look_up(of) for of in timetables.flights_to_check(flights)}
        looked_up, reason = in_rounds(flights, look_up)
        missed_before += bool(missed(before, flights, look_up))
        missed_now += bool(missed(looked_up, flights, look_up))
        looked_before, looked_now = looked_before + len(before), looked_now + len(looked_up)
    print(f"searches that missed the recommended itinerary: {missed_now} now, {missed_before} before, of {len(SEEDS)}; "
          f"flights looked up: {looked_now} now, {looked_before} before")
    assert missed_before > 0, "the made-up worlds are too easy to tell the two rules apart"
    assert missed_now < missed_before / 4, f"missed {missed_now} of {len(SEEDS)}, {missed_before} before"
    assert looked_now <= looked_before * 1.5, f"{looked_now} flights looked up, {looked_before} before"
