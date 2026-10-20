"""Plan a trip: `plan_trip` runs the steps of one trip in code, shows the report as soon as it exists, then asks
the coordinator, once, for the recommendation in words, and saves the report.

The conversation of every trip planned in a run is kept, so `ask` answers follow-up questions about a result
without searching again. The answer is written by a model from the report: the report stays the reference."""
import asyncio
import re
import time
import traceback
from typing import Any

from langchain.messages import AIMessage, HumanMessage

from trip_planner import current
from trip_planner.agents import coordinator, subagents
from trip_planner.agents.steps import run_steps, STEPS
from trip_planner.config import TripSettings
from trip_planner.places import ask_airports, ask_places, choose_airports, codes, show_airports, TripPlaces
from trip_planner.report.full import keep_report, save_report
from trip_planner.sources import web
from trip_planner.texts import any_of, tr
from trip_planner.utils import plain, say, show, TIMINGS

def build_request(settings: TripSettings, places: TripPlaces) -> str:
    """The traveller's request to the coordinator, written from the settings and the confirmed airports."""
    airports = lambda group: ", ".join(f"{a['code']} ({a['name']})" for a in group) or "none"
    lines = [
        f"I am in {places.origin.label} and I want to go to {places.destination.label} on one of these dates: {', '.join(settings.dates)}.",
        f"Primary departure airports: {airports(places.primary)}.",
        f"More distant departure airports, only if they are worth it: {airports(places.fallback)}.",
        f"Airports I can land at: {airports(places.arrival)}.",
        subagents.stopovers_rule(settings),
        f"I leave between {settings.earliest_leave} and {settings.latest_leave} and I must reach my destination {settings.arrival_window}.",
        "Trains around my origin are free for me." if settings.free_trains_at_origin
        else "Trains around my origin are paid and count for the cost.",
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
    named = re.search(rf"(?:{any_of('option_word')})\s*\**\s*([0-9]+)", plain(text))
    if recommended and named and int(named[1]) != recommended:
        return text + "\n\n> " + tr("warn_recommendation_differs", model=named[1], rules=recommended)
    return text

RESUME_ATTEMPTS = 2        # times a failed run is continued from the step that failed
RESUME_WAIT_SECONDS = 20   # pause before continuing: what fails is usually a service that is briefly unavailable

async def run_search(state: dict[str, Any]) -> dict[str, Any]:
    """Run the steps of a trip. If one fails, the finished steps are not lost: the state keeps what they found,
    and the run continues from the step that failed."""
    for attempt in range(RESUME_ATTEMPTS + 1):
        try:
            return await run_steps(state)
        except Exception as error:
            if attempt == RESUME_ATTEMPTS:
                raise
            say(tr("progress_resume", error=type(error).__name__, done=state.get("step") or 0, steps=len(STEPS), seconds=RESUME_WAIT_SECONDS))
            await asyncio.sleep(RESUME_WAIT_SECONDS)

def results_for_model(request: str, state: dict[str, Any]) -> str:
    """The first message the coordinator reads: the traveller's request, then what each step found."""
    names = ("flight search", "ground transport", "itineraries")
    found = [f"Step {number}, {name}:\n{note}" for number, (name, note) in enumerate(zip(names, state.get("notes") or []), start=1)]
    return "\n\n".join([request, "What the search found, step by step:", *found])

async def recommend(state: dict[str, Any], messages: list[Any]) -> str:
    """The recommendation in words, written by the coordinator from the results in messages. When no model answers,
    a line of fixed text names the option the rules chose: the report above it is complete without a model."""
    recommended, report = state.get("recommended") or 0, state.get("report") or ""
    try:
        text = await coordinator.write(messages)
    except Exception as error:
        say(tr("progress_no_model", error=type(error).__name__))
        return tr("recommendation_by_rules", heading=tr("recommendation"), option=recommended) if recommended else ""
    messages.append(AIMessage(content=text))
    return web.only_known_links(check_recommendation(text, recommended), report)

async def plan_trip(settings: TripSettings, places: TripPlaces | None = None) -> dict[str, Any]:
    """Plan one trip, show the result and save the report. places are the airports already chosen for it;
    without them the airports are chosen here and, if the settings ask for it, confirmed as text."""
    current.SETTINGS = settings   # the tools, the checks and the texts read the trip from here
    if places is None:
        if settings.confirm_airports:
            ask_places(settings)   # which Nazaré, when the name is that of several places
        places = choose_airports(settings)
        if settings.confirm_airports:
            ask_airports(places)
        else:
            show_airports(places)
    current.TRIP = places
    TIMINGS.clear()
    started = time.time()
    for advice in settings.advice():
        say(f"{tr('advice')}: {advice}")
    say(tr("progress_start", origin=settings.origin, destination=settings.destination,
           departure=codes(current.TRIP.departure), arrival=codes(current.TRIP.arrival)))
    response = await run_search({
        "origin": settings.origin,
        "final_destination": settings.destination,
        "primary_airports": codes(current.TRIP.primary),
        "fallback_airports": codes(current.TRIP.fallback),
        "arrival_airports": codes(current.TRIP.arrival),
        "travel_dates": ",".join(settings.dates),
    })
    # The result is the traveller's as soon as the code has it. What the model adds comes after, if it comes.
    show(response.get("report") or "")
    messages = [HumanMessage(content=results_for_model(build_request(settings, current.TRIP), response))]
    PLANNED.append({"settings": settings, "places": current.TRIP, "messages": messages, "state": response})
    writing = time.time()
    recommendation = await recommend(response, messages)
    TIMINGS["4 coordinator model (writing the recommendation)"] = time.time() - writing
    total = TIMINGS["total"] = time.time() - started
    if recommendation:
        show(recommendation)
    PLANNED[-1]["report"] = save_report(response, recommendation).resolve()
    say(tr("progress_done", seconds=round(total), path=PLANNED[-1]["report"]))
    return response

async def ask(question: str, trip: int = -1) -> None:
    """A follow-up question about a trip already planned (the last one by default). It is answered from the
    saved state of that trip, without searching again."""
    if not PLANNED:
        print(tr("followup_no_trip"))
        return
    planned = PLANNED[trip]
    current.SETTINGS, current.TRIP = planned["settings"], planned["places"]
    planned["messages"].append(HumanMessage(content=question))
    try:
        answer = await coordinator.write(planned["messages"])
    except Exception as error:
        planned["messages"].pop()
        say(tr("progress_no_model", error=type(error).__name__))
        return
    planned["messages"].append(AIMessage(content=answer))
    # The answer is written by a model: it is shown as such, and the report stays the reference
    answer = web.only_known_links(answer, planned["state"].get("report") or "")
    markdown = (f"**{web.clean_text(question)}**\n\n{answer}\n\n> {tr('followup_note')}")
    show(markdown)

responses: list[dict[str, Any]] = []
PLANNED: list[dict[str, Any]] = []   # every trip run in this session, with what was found and the conversation about it

async def plan_all(trips: list[TripSettings]) -> None:
    """Plan every trip, one after the other."""
    try:
        for settings in trips:
            responses.append(await plan_trip(settings))
    except Exception:
        say(traceback.format_exc())

def show_timings() -> None:
    """The trips planned, and where the time of the last one went."""
    for trip in responses:
        print(f"{trip.get('origin')} -> {trip.get('final_destination')}")
    for step, seconds in sorted(TIMINGS.items()):
        print(f"{seconds:6.0f} s  {step}")



def ask_to_keep() -> None:
    """Ask whether the report of the last trip is to be kept. Otherwise it goes when newer ones arrive."""
    if not PLANNED or PLANNED[-1].get("report") is None:
        return
    try:
        answer = input("\n" + tr("keep_report_prompt", limit=current.SETTINGS.recent_reports) + " ")
    except (EOFError, KeyboardInterrupt):
        return
    if plain(answer) in ("y", "yes", "s", "sim"):
        PLANNED[-1]["report"] = keep_report(PLANNED[-1]["report"], True)
        say(tr("ui_report_kept", folder=PLANNED[-1]["report"].parent))

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
