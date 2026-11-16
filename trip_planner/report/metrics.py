"""What one search did, as numbers: saved with its options as data (report/options.py, under "metrics") and added up
over every report kept by tools/funnel.py (python main.py --funnel). Nothing here is shown to the traveller.

- funnel: how the flights found became the options. How many were found and had their ground transport looked up,
  the cheapest ticket, and each flight left out with its ticket and the kind of reason (legs.Rejected, and three
  kinds of the ranking: below_minimum, not_worth_it, program). The kinds in legs.PROVEN are the ones a real timetable
  or an operator proves: what a flight search site cannot see.
- checks: what code did to what the models wrote (utils.COUNTS): the colours of a part put right and the claims only
  pointed out (agents/claims.py), the times, durations and prices no step gave, the links left out (sources/web.py),
  and the ways a model estimated where an operator's rides did not fit (steps.note_rides_missed).
- models: every call to a model in the log of the run, how many failed, how many went to plan B (agents/models.py)
  and which model wrote the recommendation.
- from_map: when a Discover map opened this search, what the map said of the trip (app/maps.estimate_of) and what
  the planner made of the same flight (watched): how far the map can be trusted, measured."""
from typing import Any

from trip_planner.agents.models import CALL_SERVICE, is_backup
from trip_planner.report.formatting import price_source
from trip_planner.utils import COUNTS, failed, REQUESTS

COORDINATOR = "coordinator, "   # how the coordinator's calls are named in the log (agents/coordinator.py)

def funnel(flights: list[dict[str, Any]], checked: set[str], kinds: dict[str, str],
           built: list[dict[str, Any]] = ()) -> dict[str, Any]:
    """The funnel of a search: flights is every flight found, checked the ids looked up, kinds the kind of reason of
    each flight left out, by id, built every itinerary that could be made, shown or not (steps.build_itineraries).
    Each itinerary keeps its total, its ticket and how much of its ways to and from the airports is priced by a
    guess (guessed_eur: a price of grade C, a model's or one from the distance)."""
    price = {flight["id"]: flight["price_eur"] for flight in flights}
    cheapest = min(price, key=price.get, default=None)
    return {"found": len(flights), "checked": len(checked),
            "cheapest_flight": cheapest, "cheapest_ticket_eur": price.get(cheapest),
            "left_out": [{"flight": flight, "ticket_eur": price.get(flight), "why": why} for flight, why in kinds.items()],
            "itineraries": {itinerary["flight"]["id"]: {"total_eur": round(itinerary["total_cost"], 2),
                                                        "flight_eur": round(itinerary["flight_price"], 2),
                                                        "guessed_eur": guessed(itinerary)} for itinerary in built}}

def guessed(itinerary: dict[str, Any]) -> float:
    """How much of an itinerary's ways to and from its airports is priced by a guess (grade C)."""
    legs = [itinerary.get(end) or {} for end in ("to", "from")]
    return round(sum(leg.get("price_eur") or 0 for leg in legs if leg.get("price_kind") and price_source(leg["price_kind"])[1] == "C"), 2)

def watched(estimate: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """What the planner made of the flight a Discover map opened it on (app/maps.estimate_of): whether it found that
    flight, its ticket then, the itinerary it made of it, or the kind of reason it left it out; and the cheapest
    option it shows, whatever the flight."""
    flights = {flight["id"]: flight for flight in response.get("flights") or []}
    found = response.get("funnel") or {}
    flight = estimate.get("flight_id")
    outcome: dict[str, Any] = {"found": flight in flights,
                               "cheapest_eur": min((round(shown["total_cost"], 2) for shown in response.get("shown") or []), default=None)}
    if flight in flights:
        outcome.update(ticket_eur=flights[flight]["price_eur"], checked=flight in set(response.get("checked") or []))
        outcome.update((found.get("itineraries") or {}).get(flight) or {})
        outcome["left_out"] = next((entry["why"] for entry in found.get("left_out") or [] if entry["flight"] == flight), None)
    return outcome

def model_use(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """The calls to a model in a log of requests: the subagents' calls one by one (models.give_up_slow_calls) and the
    coordinator's, one per model it tried."""
    calls = [entry for entry in requests
             if entry["service"] == CALL_SERVICE or (entry["service"] == "model" and entry["what"].startswith(COORDINATOR))]
    named = lambda entry: entry["what"].removeprefix(COORDINATOR)
    wrote = [named(entry) for entry in calls if entry["what"].startswith(COORDINATOR) and not failed(entry)]
    return {"calls": len(calls), "failed": sum(map(failed, calls)),
            "plan_b": sum(is_backup(named(entry)) for entry in calls if not failed(entry)),
            "recommendation_by": wrote[-1] if wrote else None}

def of(response: dict[str, Any]) -> dict[str, Any]:
    """The numbers of the search that response is the result of, read before its log is cleared (full.save_report)."""
    estimate = response.get("from_map")
    return {"funnel": response.get("funnel"), "checks": dict(COUNTS), "models": model_use(REQUESTS),
            "from_map": {**estimate, "planner": watched(estimate, response)} if estimate else None}
