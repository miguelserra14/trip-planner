"""The estimate of a price from the distance, for the vehicles no operator prices.

checkpoints/observed_prices.json holds real prices read from the operators (fares.py) on real routes, with the
distance of each. The rates were chosen from these same prices, so the tests below say that the estimate fits
what it was made from, not that it is right in the countries where no price could be read."""
import json
import math
import statistics
from pathlib import Path

from trip_planner.itineraries import estimates
from trip_planner.itineraries.estimates import estimated_price, eur_per_km, step_price

OBSERVED = json.loads((Path(__file__).parent / "checkpoints" / "observed_prices.json").read_text(encoding="utf-8"))

def error(row: dict, rate: float) -> float:
    """How many times off an estimate is, whichever way: 2 is double or half."""
    return math.exp(abs(math.log(rate / row["per_km"])))

def test_a_measured_country_uses_what_was_measured():
    assert eur_per_km("AT") == 0.20 and eur_per_km("HU") == 0.05

def test_other_countries_follow_their_price_level():
    assert eur_per_km("DK") > eur_per_km("FR") > eur_per_km("PT") > eur_per_km("BG")
    assert eur_per_km("PT") == round(0.106 * 0.806 ** 2, 3)

def test_a_country_nothing_is_known_about_keeps_the_general_rate():
    assert eur_per_km("BR") == eur_per_km("") == estimates.ESTIMATE_EUR_PER_KM

def test_coaches_cost_the_same_per_km_everywhere():
    assert eur_per_km("AT", coach=True) == eur_per_km("BG", coach=True) == estimates.COACH_EUR_PER_KM

def test_no_ticket_is_cheaper_than_the_minimum():
    assert estimated_price(1, "HU") == estimates.MIN_TICKET_EUR

def test_a_vehicle_is_estimated_at_the_rate_of_its_country_and_kind():
    train = {"km": 100, "rail": True, "coach": False}
    coach = {"km": 100, "rail": False, "coach": True}
    assert step_price(train, False, "AT") == 26.0 and step_price(train, False, "HU") == 6.0
    assert step_price(coach, False, "AT") == 6.0
    assert step_price(train, True, "AT") == 0.0   # trains free for this traveller

def test_trains_are_estimated_closer_than_with_one_rate_for_europe():
    trains = [row for row in OBSERVED if row["kind"].startswith("rail")]
    before = statistics.median(error(row, estimates.ESTIMATE_EUR_PER_KM) for row in trains)
    now = statistics.median(error(row, eur_per_km(row["country"])) for row in trains)
    assert len(trains) >= 25 and now < before and now < 1.4, f"off by {now:.2f} times, {before:.2f} with one rate"

def test_coaches_are_estimated_closer_than_with_one_rate_for_europe():
    coaches = [row for row in OBSERVED if row["kind"] == "coach"]
    before = statistics.median(error(row, estimates.ESTIMATE_EUR_PER_KM) for row in coaches)
    now = statistics.median(error(row, eur_per_km(row["country"], coach=True)) for row in coaches)
    assert len({row["country"] for row in coaches}) >= 15 and now < before and now < 1.6, f"off by {now:.2f} times, {before:.2f} with one rate"
