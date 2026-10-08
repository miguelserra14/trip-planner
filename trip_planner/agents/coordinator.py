"""The coordinator: the model that supervises the steps and writes the recommendation.
It is only offered the tool of the next step (next_step_only, dynamic tools), so the steps always run in order.
Its state is saved after every step (a checkpointer, one thread per trip): a run that fails continues from its
last finished step, and follow-up questions are answered without searching again."""
import warnings

from langchain.agents import create_agent
from langchain.agents.middleware import dynamic_prompt, ModelRequest, wrap_model_call
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

from trip_planner import current
from trip_planner.agents import subagents
from trip_planner.agents.state import TripState
from trip_planner.agents.steps import build_itineraries, search_flights, search_ground_transport, STEPS
from trip_planner.config import LANGUAGES
from trip_planner.texts import tr

# The Google client leaves this warning behind when a model answers with an error. The error itself is handled
# by best_available_model, so the warning only adds noise.
warnings.filterwarnings("ignore", message="coroutine 'ClientResponse.json' was never awaited")

# Coordinator models, best first. The free tier gives the better models very few requests per day
# (20 for gemini-3.8-flash), so the coordinator uses the best one that still has quota
# and only falls back to flash-lite when the others are exhausted.
COORDINATOR_MODELS = [
    #"google_genai:gemini-3.1-pro-preview",    this one is too slow
    "google_genai:gemini-3.8-flash",
    "google_genai:gemini-3.1-flash-lite",
]

# Coordinator model: the best model that still has quota today.
coordinator_models = {
    # Only the last model retries: the others must fail fast, so that the next one takes over.
    name: init_chat_model(name, max_retries=6 if name == COORDINATOR_MODELS[-1] else 1)
    for name in COORDINATOR_MODELS
}
EXHAUSTED_MODELS: set = set()   # models out of daily quota, skipped for the rest of this session

@wrap_model_call
async def best_available_model(request: ModelRequest, handler):
    for name in COORDINATOR_MODELS:
        if name in EXHAUSTED_MODELS:
            continue
        try:
            return await handler(request.override(model=coordinator_models[name]))
        except Exception as error:
            if name == COORDINATOR_MODELS[-1]:
                raise
            if "PerDay" in str(error):
                EXHAUSTED_MODELS.add(name)   # the daily quota only comes back tomorrow
    raise RuntimeError("No coordinator model is available.")

@wrap_model_call
async def next_step_only(request: ModelRequest, handler):
    """Dynamic tools: the coordinator is only offered the tool of the next step, read from the state.
    The order of the steps is then guaranteed, whichever model is coordinating. When every step is done
    no tool is left: the coordinator can only write, the recommendation or the answer to a follow-up question."""
    done = request.state.get("step") or 0
    offered = STEPS[done] if done < len(STEPS) else ""
    return await handler(request.override(tools=[t for t in request.tools if getattr(t, "name", "") == offered]))

@dynamic_prompt
def coordinator_prompt(request: ModelRequest) -> str:
    s = current.SETTINGS
    return f"""
    You are a trip coordinator supervising a flight search and two ground transport specialists.
    The cities, the airports and the dates of the trip are already in the state, confirmed by the traveller.
    Follow these steps in order, one tool call at a time. Only the tool of the next step is available to you:
    1. Call search_flights. It searches every route.
    2. Call search_ground_transport. It reads the real timetables and asks the two transport specialists.
    3. Call build_itineraries. It checks the hard constraints, grades how reliable each itinerary is, computes the
       totals and ranks the itineraries.
    If build_itineraries finds no valid itinerary, do not call the tools again: read why the flights were rejected
    and tell the traveller which single setting to relax to get a result.

    The hard constraints of this trip, enforced by build_itineraries:
    - {subagents.stopovers_rule(s)}
    - The traveller leaves {s.origin} between {s.earliest_leave} and {s.latest_leave}.
    - The traveller is at the airport at least {s.min_airport_minutes} minutes before the flight departs.
    - The traveller needs {s.min_landing_minutes} minutes to leave the arrival airport after landing.
    - The traveller reaches {s.destination} {s.arrival_window}.
    - Schedules must reach reliability grade {s.min_reliability} (A = confirmed for the travel date, B = typical timetable of
      the same weekday, C = not verified, found by a model in web search).

    The traveller's priorities, in this order:
    1. Lowest total cost. The traveller accepts sacrifices on everything else to save money, such as a more distant
       airport or a longer wait at the airport. A fallback airport is only worth it when it saves enough for the
       extra hours the trip takes: {s.extra_hours_table(s.fallback_saving_eur_per_hour)}.
    2. Shortest total door-to-door duration. Extra hours of travel cost the traveller more and more:
       {s.extra_hours_table(s.tiebreak_eur_per_hour)}. An itinerary is preferred when its price plus the cost of the
       hours it takes beyond the fastest itinerary is the lowest.
    build_itineraries already applies both rules and tells you which option they recommend. Recommend that same option.

    The report from build_itineraries is shown to the traveller automatically, right above your answer.
    Do not repeat its tables or its steps, and never restate a time or a price differently from the report.
    Your final answer is in {LANGUAGES[s.language]}, formatted as Markdown, with only:
    - a level 2 heading "{tr('recommendation')}"
    - which option you recommend and why, in 2 to 4 short sentences, referring to the options by their number
    - how reliable that option is: say which of its parts are grade B or C and must be confirmed before booking
    - the main trade-off against the other options, in 1 or 2 sentences
    Do not use emojis.

    After that answer the traveller may ask follow-up questions about the trip. No tool is available then:
    answer in a few sentences, only from the report and the tool results above, without the heading. Never state
    a time or a price that is not in them. You cannot search again, so never offer to. If the question needs a new
    search (other dates, airports or limits), name the setting in settings.py to change, such as `dates` or
    `latest_leave`, and say that the traveller must run the search again.
    """

coordinator = create_agent(
    model=coordinator_models[COORDINATOR_MODELS[-1]],
    tools=[search_flights, search_ground_transport, build_itineraries],
    state_schema=TripState,
    middleware=[best_available_model, next_step_only, coordinator_prompt],
    # The state is saved after every step, one thread per trip: a run that fails continues from its last
    # finished step (see run_coordinator), and the traveller can ask follow-up questions (see ask).
    checkpointer=InMemorySaver(),
)
