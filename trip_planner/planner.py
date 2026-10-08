"""Plan a trip: `plan_trip` runs the coordinator for one trip, shows the result and saves the report.

The state of every trip planned in a run is saved (the coordinator has a checkpointer), so `ask` answers
follow-up questions about a result without searching again. The answer is written by a model from the report:
the report stays the reference."""
import asyncio
import re
import time
import traceback
import uuid
from typing import Any, Dict, List

from langchain.messages import HumanMessage

from trip_planner import current
from trip_planner.agents.coordinator import coordinator
from trip_planner.agents.steps import STEPS
from trip_planner.agents.subagents import stopovers_rule
from trip_planner.config import TripSettings
from trip_planner.places import ask_airports, choose_airports, codes, show_airports, TripPlaces
from trip_planner.report.full import save_report
from trip_planner.sources.web import clean_text
from trip_planner.texts import tr
from trip_planner.utils import message_text, plain, say, show, TIMINGS

def build_request(settings: TripSettings, places: TripPlaces) -> str:
    """The traveller's request to the coordinator, written from the settings and the confirmed airports."""
    airports = lambda group: ", ".join(f"{a['code']} ({a['name']})" for a in group) or "none"
    lines = [
        f"I am in {places.origin.label} and I want to go to {places.destination.label} on one of these dates: {', '.join(settings.dates)}.",
        f"Primary departure airports: {airports(places.primary)}.",
        f"More distant departure airports, only if they are worth it: {airports(places.fallback)}.",
        f"Airports I can land at: {airports(places.arrival)}.",
        stopovers_rule(settings),
        f"I leave between {settings.earliest_leave} and {settings.latest_leave} and I must reach my destination {settings.arrival_window}.",
        "Trains at the destination are free for me." if settings.free_trains_at_destination
        else "Trains at the destination are paid and count for the cost.",
        "Plan the trip and recommend the best option.",
    ]
    if settings.extra_wishes:
        lines.append(settings.extra_wishes)
    return "\n".join(lines)

def check_recommendation(text: str, recommended: int) -> str:
    """The recommendation is written by a model. If it names another option than the one the rules chose,
    say so instead of letting the two disagree silently."""
    named = re.search(r"(?:option|op[cç][aã]o)\s*\**\s*([0-9]+)", plain(text))
    if recommended and named and int(named[1]) != recommended:
        return text + "\n\n> " + tr("warn_recommendation_differs", model=named[1], rules=recommended)
    return text

RESUME_ATTEMPTS = 2        # times a failed run is continued from its last finished step
RESUME_WAIT_SECONDS = 20   # pause before continuing: what fails is usually a service that is briefly unavailable

async def run_coordinator(request: Dict[str, Any] | None, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run the coordinator on the thread in config. If it fails, the finished steps are not lost: the
    checkpointer saved the state after each one, and the run continues from there."""
    for attempt in range(RESUME_ATTEMPTS + 1):
        try:
            return await coordinator.ainvoke(request, config=config)
        except Exception as error:
            if attempt == RESUME_ATTEMPTS:
                raise
            saved = await coordinator.aget_state(config)
            done = saved.values.get("step") or 0
            say(tr("progress_resume", error=type(error).__name__, done=done, steps=len(STEPS), seconds=RESUME_WAIT_SECONDS))
            await asyncio.sleep(RESUME_WAIT_SECONDS)
            if saved.next:
                request = None   # no new input: continue the thread from what was saved

async def plan_trip(settings: TripSettings, places: TripPlaces | None = None) -> Dict[str, Any]:
    """Plan one trip, show the result and save the report. places are the airports already chosen for it;
    without them the airports are chosen here and, if the settings ask for it, confirmed as text."""
    current.SETTINGS = settings   # the tools, the checks and the texts read the trip from here
    if places is None:
        places = choose_airports(settings)
        if settings.confirm_airports:
            ask_airports(places)
        else:
            show_airports(places)
    current.TRIP = places
    TIMINGS.clear()
    started = time.time()
    say(tr("progress_start", origin=settings.origin, destination=settings.destination,
           departure=codes(current.TRIP.departure), arrival=codes(current.TRIP.arrival)))
    # One thread per run: the saved state of a trip is never mixed with another one
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "tags": ["TP"], "recursion_limit": 30}  #tag traces to make them easy to find in Langsmith
    PLANNED.append({"settings": settings, "places": current.TRIP, "config": config})
    response = await run_coordinator(
        {
            "messages": [HumanMessage(content=build_request(settings, current.TRIP))],
            "origin": settings.origin,
            "final_destination": settings.destination,
            "primary_airports": codes(current.TRIP.primary),
            "fallback_airports": codes(current.TRIP.fallback),
            "arrival_airports": codes(current.TRIP.arrival),
            "travel_dates": ",".join(settings.dates),
        },
        config,
    )
    total = time.time() - started
    steps = sum(seconds for step, seconds in TIMINGS.items() if step[0] in "123" and step[1] == " ")
    TIMINGS["4 coordinator model (thinking between the steps and writing the recommendation)"] = max(0.0, total - steps)
    TIMINGS["total"] = total
    recommendation = check_recommendation(message_text(response["messages"][-1]), response.get("recommended") or 0)
    show((response.get("report") or "") + "\n\n" + recommendation)
    say(tr("progress_done", seconds=round(total), path=save_report(response, recommendation).resolve()))
    return response

async def ask(question: str, trip: int = -1) -> None:
    """A follow-up question about a trip already planned (the last one by default). It is answered from the
    saved state of that trip, without searching again."""
    if not PLANNED:
        print(tr("followup_no_trip"))
        return
    planned = PLANNED[trip]
    current.SETTINGS, current.TRIP = planned["settings"], planned["places"]
    response = await coordinator.ainvoke({"messages": [HumanMessage(content=question)]}, config=planned["config"])
    # The answer is written by a model: it is shown as such, and the report stays the reference
    markdown = (f"**{clean_text(question)}**\n\n{message_text(response['messages'][-1])}\n\n> {tr('followup_note')}")
    show(markdown)

responses: List[Dict[str, Any]] = []
PLANNED: List[Dict[str, Any]] = []   # every trip run in this session, with the thread its state is saved under

async def plan_all(trips: List[TripSettings]) -> None:
    """Plan every trip, one after the other."""
    try:
        for settings in trips:
            responses.append(await plan_trip(settings))
    except Exception:
        say(traceback.format_exc())

def show_timings() -> None:
    """What the coordinator called, and where the time of the last trip went."""
    for trip in responses:
        print(f"{trip.get('origin')} -> {trip.get('final_destination')}")
        for message in trip["messages"]:
            for tool_call in getattr(message, "tool_calls", None) or []:
                print(f"  -> {tool_call['name']}")
    for step, seconds in sorted(TIMINGS.items()):
        print(f"{seconds:6.0f} s  {step}")



async def follow_up() -> None:
    """Take questions about the last trip planned, until an empty line."""
    while True:
        try:
            question = input("\n" + tr("followup_prompt") + " ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not question:
            return
        await ask(question)
