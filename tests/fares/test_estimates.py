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

def test_measured_country_rate():
    assert eur_per_km("AT") == 0.20 and eur_per_km("HU") == 0.05

def test_price_level_rate():
    assert eur_per_km("DK") > eur_per_km("FR") > eur_per_km("PT") > eur_per_km("BG")
    assert eur_per_km("PT") == round(0.106 * 0.806 ** 2, 3)

def test_unknown_country_general_rate():
    assert eur_per_km("BR") == eur_per_km("") == estimates.ESTIMATE_EUR_PER_KM

def test_coach_rate_everywhere():
    assert eur_per_km("AT", coach=True) == eur_per_km("BG", coach=True) == estimates.COACH_EUR_PER_KM

def test_minimum_ticket_price():
    assert estimated_price(1, "HU") == estimates.MIN_TICKET_EUR

def test_vehicle_rate_by_country():
    train = {"km": 100, "rail": True, "coach": False}
    coach = {"km": 100, "rail": False, "coach": True}
    assert step_price(train, False, "AT") == 26.0 and step_price(train, False, "HU") == 6.0
    assert step_price(coach, False, "AT") == 6.0
    assert step_price(train, True, "AT") == 0.0   # trains free for this traveller

def test_train_rates_beat_flat():
    trains = [row for row in OBSERVED if row["kind"].startswith("rail")]
    before = statistics.median(error(row, estimates.ESTIMATE_EUR_PER_KM) for row in trains)
    now = statistics.median(error(row, eur_per_km(row["country"])) for row in trains)
    assert len(trains) >= 25 and now < before and now < 1.4, f"off by {now:.2f} times, {before:.2f} with one rate"

def test_coach_rate_beats_flat():
    coaches = [row for row in OBSERVED if row["kind"] == "coach"]
    before = statistics.median(error(row, estimates.ESTIMATE_EUR_PER_KM) for row in coaches)
    now = statistics.median(error(row, eur_per_km(row["country"], coach=True)) for row in coaches)
    assert len({row["country"] for row in coaches}) >= 15 and now < before and now < 1.6, f"off by {now:.2f} times, {before:.2f} with one rate"
