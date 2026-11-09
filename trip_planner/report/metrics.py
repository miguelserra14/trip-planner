"""What one search did, as numbers: saved with its options as data (report/options.py, under "metrics") and added up
over every report kept by tools/funnel.py (python main.py --funnel). Nothing here is shown to the traveller.

- funnel: how the flights found became the options. How many were found and had their ground transport looked up,
  the cheapest ticket, and each flight left out with its ticket and the kind of reason (legs.Rejected, and three
  kinds of the ranking: below_minimum, not_worth_it, program). The kinds in legs.PROVEN are the ones a real timetable
  or an operator proves: what a flight search site cannot see.
- checks: what code did to what the models wrote (utils.COUNTS): the colours of a part put right and the claims only
  pointed out (agents/claims.py), the times and durations no step gave, the links left out (sources/web.py).
- models: every call to a model in the log of the run, how many failed, how many went to plan B (agents/models.py)
  and which model wrote the recommendation."""
from typing import Any

from trip_planner.agents.models import CALL_SERVICE, is_backup
from trip_planner.utils import COUNTS, failed, REQUESTS

COORDINATOR = "coordinator, "   # how the coordinator's calls are named in the log (agents/coordinator.py)

def funnel(flights: list[dict[str, Any]], checked: set[str], kinds: dict[str, str]) -> dict[str, Any]:
    """The funnel of a search: flights is every flight found, checked the ids looked up, kinds the kind of reason of
    each flight left out, by id (steps.build_itineraries)."""
    price = {flight["id"]: flight["price_eur"] for flight in flights}
    cheapest = min(price, key=price.get, default=None)
    return {"found": len(flights), "checked": len(checked),
            "cheapest_flight": cheapest, "cheapest_ticket_eur": price.get(cheapest),
            "left_out": [{"flight": flight, "ticket_eur": price.get(flight), "why": why} for flight, why in kinds.items()]}

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
    return {"funnel": response.get("funnel"), "checks": dict(COUNTS), "models": model_use(REQUESTS)}
