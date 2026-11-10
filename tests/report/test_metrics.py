"""The numbers kept with each report (report/metrics.py) and added up by python main.py --funnel (tools/funnel.py).
Made-up inputs, no network.

- Each flight left out keeps the kind of its reason; a real timetable's and an operator's are told apart.
- The calls to the models are counted from the log of the run, plan B and failures included.
- An older report, which has its funnel only in words, is read in any language there is a file for."""
from collections import Counter

import pytest

from trip_planner import utils
from trip_planner.agents import steps
from trip_planner.itineraries.legs import PROVEN, Rejected
from trip_planner.report import metrics
from trip_planner.texts import text_in
from trip_planner.tools import funnel as tool

CHEAP = {"id": "CHEAP", "price_eur": 20.0, "departure_airport": "OPO", "arrival_airport": "BRU"}
DEAR = {"id": "DEAR", "price_eur": 90.0, "departure_airport": "OPO", "arrival_airport": "BRU"}
LANDING_TOO_LATE = dict(airport="BRU", landing="23:40", limits="by 23:59", day="2026-11-10", real="none")

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(steps, "FAULTS", {})
    monkeypatch.setattr(utils, "REQUESTS", [])
    monkeypatch.setattr(steps, "REQUESTS", utils.REQUESTS)
    monkeypatch.setitem(utils.LISTENERS, "say", lambda text: None)

def failing_with(monkeypatch, errors: dict[str, Exception]) -> None:
    def check(flight, *arguments):
        raise errors[flight["id"]]
    monkeypatch.setattr(steps, "check_itinerary", check)

# ---------------------------------------------------------------- the funnel of a search
def test_a_flight_left_out_keeps_the_kind_of_its_reason(monkeypatch):
    failing_with(monkeypatch, {"CHEAP": Rejected("lands too late", "timetable"), "DEAR": KeyError("to_airport")})
    kinds = {}
    steps.itinerary_or_reason(CHEAP, {}, {}, {}, kinds=kinds)
    steps.itinerary_or_reason(DEAR, {}, {}, {}, kinds=kinds)
    assert kinds == {"CHEAP": "timetable", "DEAR": "program"}

def test_the_search_keeps_its_funnel_with_the_cheapest_ticket(monkeypatch):
    failing_with(monkeypatch, {"CHEAP": Rejected("the coach does not run that day", "not_running")})
    found = steps.build_itineraries({"flights": [DEAR, CHEAP], "checked": ["CHEAP"], "ground": {}, "transfers": [], "trains": []})
    assert found["funnel"] == {"found": 2, "checked": 1, "cheapest_flight": "CHEAP", "cheapest_ticket_eur": 20.0,
                               "left_out": [{"flight": "CHEAP", "ticket_eur": 20.0, "why": "not_running"}], "itineraries": {}}
    assert tool.cheapest_was_impossible(found["funnel"])

def test_a_rejection_by_the_travellers_limits_is_not_proven_impossible():
    funnel = metrics.funnel([CHEAP, DEAR], {"CHEAP", "DEAR"}, {"CHEAP": "ground_choice"})
    assert "ground_choice" not in PROVEN and not tool.cheapest_was_impossible(funnel)

# ---------------------------------------------------------------- a map against the planner
ESTIMATE = {"map": "/discover/map/1/", "key": "BRU-out", "clicked": "2026-11-10T10:00", "flight_id": "CHEAP", "round_trip": False,
            "flight_eur": 20.0, "total_eur": 35.0, "ground_eur": 15.0, "looked_up": True, "legs": []}

def searched(itineraries: dict, left_out: list, checked=("CHEAP", "DEAR")) -> dict:
    """A search's response as metrics.of reads it, with the cheapest option it showed at 40 EUR."""
    return {"flights": [CHEAP, DEAR], "checked": list(checked), "shown": [{"total_cost": 40.0}, {"total_cost": 95.0}],
            "funnel": {"left_out": left_out, "itineraries": itineraries}}

def test_the_planner_says_what_it_made_of_the_flight_a_map_opened_it_on():
    made = metrics.watched(ESTIMATE, searched({"CHEAP": {"total_eur": 38.0, "flight_eur": 21.0}}, []))
    assert made == {"found": True, "cheapest_eur": 40.0, "ticket_eur": 20.0, "checked": True, "total_eur": 38.0, "flight_eur": 21.0, "left_out": None}
    gone = metrics.watched(ESTIMATE, searched({}, [{"flight": "CHEAP", "ticket_eur": 20.0, "why": "timetable"}]))
    assert gone["left_out"] == "timetable" and "total_eur" not in gone
    assert metrics.watched({**ESTIMATE, "flight_id": "SOLD-OUT"}, searched({}, [])) == {"found": False, "cheapest_eur": 40.0}
    assert metrics.of({"from_map": ESTIMATE, **searched({}, [])})["from_map"]["planner"]["found"]

def opened(planner: dict, written: str = "2026-11-10T10:20", **estimate) -> dict:
    return {"from_map": {**ESTIMATE, **estimate, "planner": planner}, "written": written}

def test_the_map_is_measured_against_the_planner_only_for_searches_run_soon_after_the_click():
    reports = [opened({"found": True, "total_eur": 38.0, "flight_eur": 21.0}),                        # 3 EUR dearer: within 10%
               opened({"found": True, "total_eur": 50.0, "flight_eur": 20.0}, looked_up=False),       # 15 EUR dearer
               opened({"found": True, "left_out": "timetable"}),                                       # the map's flight cannot be made
               opened({"found": False}, looked_up=False),
               opened({"found": True, "total_eur": 90.0, "flight_eur": 70.0}, written="2026-11-12T10:00")]   # two days later: not the map's fault
    lines = tool.map_part(reports)
    assert "4 searches opened from a map within 24 hours (1 later ones left out)" in lines[0]
    assert "every dot (4): flight not found by the planner 1, proven impossible 1 (25%), itinerary made 2" in lines[1]
    assert "To and from the airports: median +8 EUR, 50% within 5 EUR, 50% within 10 EUR" in lines[1]
    assert "dark ring: looked up in the timetables (2)" in lines[2] and "proven impossible 1 (50%)" in lines[2]
    assert "estimated ways (2)" in lines[3]
    assert tool.map_part([]) == ["Discover against the planner: no search opened from a map yet."]

def test_a_map_there_and_back_is_compared_on_its_ways_to_the_airports_only():
    lines = tool.map_part([opened({"found": True, "total_eur": 38.0, "flight_eur": 21.0}, round_trip=True)])
    assert "To and from the airports" in lines[1] and "Door to door" not in lines[1]
    assert "Door to door, one-way maps: median +8%" in tool.map_part([opened({"found": True, "total_eur": 38.0, "flight_eur": 21.0})])[1]

# ---------------------------------------------------------------- the models
def test_plan_b_and_the_failed_calls_are_counted_from_the_log():
    log = [{"service": "model call", "what": "gemini-3.1-flash-lite", "result": "error: 429"},
           {"service": "model call", "what": "ministral-14b-latest", "result": "ok"},
           {"service": "model", "what": "coordinator, google_genai:gemini-3-pro", "result": "error: timeout"},
           {"service": "model", "what": "coordinator, mistralai:ministral-14b-latest", "result": "ok"},
           {"service": "model", "what": "airport transfer agent, 3 airports", "result": "ok"},   # the subagent as a whole: its calls are above
           {"service": "Kiwi", "what": "OPO -> BRU", "result": "ok: 15 flights"}]
    assert metrics.model_use(log) == {"calls": 4, "failed": 2, "plan_b": 2, "recommendation_by": "mistralai:ministral-14b-latest"}

def test_the_checks_of_the_models_text_are_kept_with_the_report(monkeypatch):
    monkeypatch.setattr(metrics, "COUNTS", Counter(links_dropped=2, claims_corrected=1))
    assert metrics.of({"funnel": None})["checks"] == {"links_dropped": 2, "claims_corrected": 1}

# ---------------------------------------------------------------- older reports, in words
@pytest.mark.parametrize("language", ["en", "pt-PT"])
def test_a_connection_a_real_timetable_rules_out_is_read_in_every_language(language):
    assert tool.kind_of(text_in(language, "reject_no_connection_after_landing", **LANDING_TOO_LATE)) == "timetable"

def test_a_connection_ruled_out_by_a_models_timetable_is_not_proven():
    said = text_in("en", "unverified_basis") + " " + text_in("en", "reject_no_connection_after_landing", **LANDING_TOO_LATE)
    assert tool.kind_of(said) == "estimate"

def test_the_funnel_of_an_older_report_is_read_from_its_words(tmp_path):
    report = tmp_path / "trip_coimbra_antuerpia_2026-10-08_1845.md"
    reason = text_in("pt-PT", "reject_no_connection_after_landing", **LANDING_TOO_LATE)
    report.write_text("\n".join([
        "## Raciocínio", "### 2. Voos encontrados", "", text_in("pt-PT", "flights_found", count=40, checked=12), "",
        "| Voo | Preço | Consultado |", "| :-- | :-- | :-- |", "| OPO-BRU-1 | 20 EUR | sim |", "| OPO-BRU-2 | 35 EUR | sim |", "",
        "### 3. Transporte terrestre", "", "### 4. Voos excluídos e porquê", "", f"- OPO-BRU-1 (20 EUR): {reason}", "",
        "### 5. Onde foi gasto o tempo", ""]), encoding="utf-8")
    funnel = tool.from_markdown(report)
    assert (funnel["found"], funnel["checked"], funnel["cheapest_ticket_eur"]) == (40, 12, 20.0)
    assert funnel["left_out"] == [{"flight": "OPO-BRU-1", "ticket_eur": 20.0, "why": "timetable"}]
    assert tool.cheapest_was_impossible(funnel)
