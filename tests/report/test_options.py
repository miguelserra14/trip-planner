"""The options of a report as data, saved beside it for a page to show as cards: the same values as the Markdown
report, the cheapest and the fastest, what each date would cost, and the ground prices asked again since read over
the ones written. Made-up options, no network."""
import json

import pytest

from trip_planner import current
from trip_planner.itineraries.ranking import cheapest_by_date
from trip_planner.report import full, options
from trip_planner.trip.places import Place, TripPlaces

DAY, NEXT_DAY = "2026-11-12", "2026-11-13"

def itinerary(cost: float, minutes: int, day: str = DAY, guessed: float = 0.0, to_grade: str = "B") -> dict:
    """Coimbra to Antwerp: a coach to Porto airport, a flight to Brussels and a train to Antwerp."""
    leg = lambda grade, leave, arrive, price_kind: {
        "grade": grade, "service_day": day, "kind": "timetable", "source": "", "leave": leave, "arrive": arrive, "price_eur": 10.0,
        "price_kind": price_kind, "urls": ["https://www.example.com"], "text": "", "later": ["16:10"],
        "steps": [{"time": "06:00", "arrive": "07:30", "line": "Coach", "agency": "Rede Expressos", "from": "Coimbra", "to": "OPO",
                   "mode": "COACH", "rail": False, "coach": True, "from_id": "x"}],
        "tickets": [{"operator": "Rede Expressos", "price_eur": 10.0}]}
    flight = {"id": f"F{cost:.0f}", "date": day, "route": "OPO → BRU", "departure_airport": "OPO", "arrival_airport": "BRU",
              "departure_time": "09:00", "arrival_time": "12:30", "duration_minutes": 150, "stops": 0, "flights": "FR 1234",
              "airline": "Ryanair", "source": "Kiwi", "price_eur": cost - 20, "cabin_bag_included": False, "timezone_shift_minutes": 60}
    return {"flight": flight, "booking": {"verified": False, "url": "https://www.ryanair.com"},
            "to": leg(to_grade, 6 * 60, 7 * 60 + 30, "operator"), "from": leg("A", 13 * 60 + 15, 14 * 60, "estimate"),
            "flight_price": cost - 20, "total_cost": cost, "total_minutes": minutes, "guessed_eur": guessed, "confirmed_eur": 10.0,
            "leave_time": "06:00", "leave_date": day, "at_airport_time": "07:30", "airport_wait": 90, "wait_after_landing": 45,
            "arrival_time": "14:00", "arrival_date": day, "notes": [], "schedule_grade": to_grade, "extra_minutes": 0, "time_cost": 0.0}

@pytest.fixture
def trip(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(full, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(full, "SAVED_REPORTS", tmp_path / "reports" / "saved")
    current.TRIP = TripPlaces(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.2, -8.4),
                              Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.2, 4.4), primary=[], fallback=[], arrival=[])
    cheap, quick = itinerary(120, 600), itinerary(150, 420)
    return {"shown": [cheap, quick], "recommended": 2, "warnings": ["A warning in words"], "recommendation": "Take option 2.",
            "by_date": [(DAY, cheap), (NEXT_DAY, None)], "ground_only": [], "verify": {"options": [
                {"number": 1, "total": 120, "legs": [{"side": "to", "price_eur": 10.0}, {"side": "from", "price_eur": 10.0}]},
                {"number": 2, "total": 150, "legs": [{"side": "to", "price_eur": 10.0}, {"side": "from", "price_eur": 10.0}]}]}}

def test_options_saved_apart(trip):
    report = full.save_report(trip, "")
    assert full.options_of(report).exists() and full.options_of(report).parent == report.parent
    assert full.report_files("trip_*.json") == [full.vehicles_of(report)]   # --verify and --prices read only their own file

def test_options_keep_report_values(trip):
    data = full.read_options(full.save_report(trip, ""))
    first = data["options"][0]
    assert [option["number"] for option in data["options"]] == [1, 2] and data["recommended"] == 2
    assert (first["total_eur"], first["minutes"], first["flight"]["route"], first["schedule_grade"]) == (120, 600, "OPO → BRU", "B")
    assert first["to"]["vehicles"][0]["line"] == "Coach" and "from_id" not in first["to"]["vehicles"][0]   # only what a page shows
    assert (first["to"]["leave_time"], first["to"]["arrive_time"], first["to"]["minutes"]) == ("06:00", "07:30", 90)
    assert first["to"]["operators"] == ["Rede Expressos"] and first["from"]["later"] == ["16:10"]
    assert first["grades"] == {"part_flight_times": "A", "part_flight_price": "B", "part_to_times": "B", "part_to_price": "A",
                               "part_from_times": "A", "part_from_price": "C"}   # a guessed price is never confirmed
    assert data["origin"] == {"written": "Coimbra", "label": current.TRIP.origin.label}
    assert data["warnings"] == ["A warning in words"] and data["recommendation"] == "Take option 2."   # the model's words, apart

def test_option_gap_to_best(trip):
    data = full.read_options(full.save_report(trip, ""))
    assert (data["cheapest"], data["fastest"]) == (1, 2)
    assert [option["versus"] for option in data["options"]] == [{"cheapest_eur": 0, "fastest_minutes": 180},
                                                                {"cheapest_eur": 30, "fastest_minutes": 0}]

def test_guessed_total_has_range(trip):
    trip["shown"][0]["guessed_eur"] = 20.0
    data = full.read_options(full.save_report(trip, ""))
    lowest, highest = data["options"][0]["range_eur"]
    assert lowest < 120 < highest and data["options"][1]["range_eur"] is None

def test_cost_per_travel_date(trip):
    data = full.read_options(full.save_report(trip, ""))
    assert data["by_date"] == [{"date": DAY, "option": 1, "route": "OPO → BRU", "total_eur": 120, "minutes": 600},
                               {"date": NEXT_DAY, "option": None, "route": None, "total_eur": None, "minutes": None}]

def test_date_tie_goes_faster():
    slow, fast, other = itinerary(100, 600), itinerary(100, 500), itinerary(90, 700, day=NEXT_DAY)
    assert cheapest_by_date([slow, fast, other], [DAY, NEXT_DAY, "2026-11-14"]) == [(DAY, fast), (NEXT_DAY, other), ("2026-11-14", None)]

def test_refreshed_prices_replace_old(trip):
    report = full.save_report(trip, "")
    kept = json.loads(full.vehicles_of(report).read_text(encoding="utf-8"))
    kept["options"][1] |= {"total": 110, "legs": [{"side": "to", "price_eur": 2.0}, {"side": "from", "price_eur": 8.0}]}
    kept["prices_read"] = "2026-11-10T09:00"
    full.vehicles_of(report).write_text(json.dumps(kept), encoding="utf-8")
    data = full.read_options(report)
    second = data["options"][1]
    assert (second["total_eur"], second["to"]["price_eur"], second["prices_read"]) == (110, 2.0, "2026-11-10T09:00")
    assert "prices_read" not in data["options"][0]   # unchanged: the price it was planned with
    assert data["cheapest"] == 2 and second["versus"]["cheapest_eur"] == 0   # compared again with the new total

def test_old_report_shown_raw(trip):
    report = full.save_report(trip, "")
    data = json.loads(full.options_of(report).read_text(encoding="utf-8"))
    full.options_of(report).write_text(json.dumps({**data, "version": options.VERSION + 1}), encoding="utf-8")
    assert full.read_options(report) is None
    full.options_of(report).unlink()
    assert full.read_options(report) is None
