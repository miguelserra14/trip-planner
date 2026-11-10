"""Plan a trip: `plan_trip` runs the steps of one trip in code, shows the report as soon as it exists, then asks
the coordinator, once, for the recommendation in words, and saves the report. A round trip is two of them, the way
there and the way back, then a summary that pairs their options.

The conversation of every trip planned in a run is kept, so `ask` answers follow-up questions about a result
without searching again. The answer is written by a model from the report: the report stays the reference."""
import asyncio
import dataclasses
import re
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from langchain.messages import AIMessage, HumanMessage

from trip_planner import current
from trip_planner.agents import claims, coordinator, steps, subagents
from trip_planner.agents.steps import run_steps, steps_of
from trip_planner.trip.trip_settings import SettingError, TripSettings
from trip_planner.trip.places import ask_airports, ask_places, choose_airports, cities_only, codes, return_places, show_airports, TripPlaces
from trip_planner.report.full import keep_report, remember_duration, save_report, step_name
from trip_planner.report.summary import render_ground_round_trip, render_round_trip
from trip_planner.sources import airlines, web
from trip_planner.texts import any_of, tr
from trip_planner.utils import COUNTS, plain, progress, say, show, TIMINGS

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
    say so instead of letting the two disagree silently. When there is no option at all and it names one, it is
    describing options that do not exist (a flight of the list, on 2026-10-21): a fixed text is shown instead."""
    named = re.search(rf"(?:{any_of('option_word')})\s*\**\s*([0-9]+)", plain(text))
    if not recommended and named:
        return tr("recommendation_none", heading=tr("recommendation"))
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
            say(tr("progress_resume", error=type(error).__name__, done=state.get("step") or 0, steps=len(steps_of(current.SETTINGS)), seconds=RESUME_WAIT_SECONDS))
            await asyncio.sleep(RESUME_WAIT_SECONDS)

def results_for_model(request: str, state: dict[str, Any]) -> str:
    """The first message the coordinator reads: the traveller's request, then what each step found."""
    names = ("flight search", "ground transport", "itineraries")
    found = [f"Step {number}, {name}:\n{note}" for number, (name, note) in enumerate(zip(names, state.get("notes") or []), start=1)]
    return "\n\n".join([request, "What the search found, step by step:", *found])

def given_to_model(state: dict[str, Any]) -> str:
    """What the coordinator was given about a trip: the report and what each step found. A time or a duration it
    writes must be in there (agents/claims.py)."""
    return "\n".join([state.get("report") or "", *(state.get("notes") or [])])

async def recommend(state: dict[str, Any], messages: list[Any]) -> str:
    """The recommendation in words, written by the coordinator from the results in messages. When no model answers,
    a line of fixed text names the option the rules chose: the report above it is complete without a model."""
    recommended, report = state.get("recommended") or 0, state.get("report") or ""
    try:
        text = await coordinator.write(messages)
    except Exception as error:
        say(tr("progress_no_model", error=type(error).__name__))
        return tr("recommendation_by_rules", heading=tr("recommendation"), option=recommended) if recommended else ""
    text = check_recommendation(text, recommended)
    text = claims.check_claims(text, given_to_model(state), state.get("shown") or [], recommended)
    messages.append(AIMessage(content=text))   # what the traveller was shown is what the follow-up questions build on
    return web.only_known_links(text, report)

async def plan_trip(settings: TripSettings, places: TripPlaces | None = None, from_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """Plan a trip, show the result and save the report. places are the airports already chosen for it;
    without them the airports are chosen here and, if the settings ask for it, confirmed as text. With return
    dates the way back follows, on the same airports, and the summary of the round trip comes last. from_map is what
    a Discover map said of this one-way trip when it opened the planner (app/maps.estimate_of): kept with the report,
    to measure the map against what the planner finds (report/metrics.py), never used to plan."""
    current.SETTINGS = settings   # the tools, the checks and the texts read the trip from here
    if places is None:
        if settings.confirm_airports:
            ask_places(settings)   # which Nazaré, when the name is that of several places
        if settings.ground_only:
            places = cities_only(settings)
        else:
            places = choose_airports(settings)
            if settings.confirm_airports:
                ask_airports(places)
            else:
                show_airports(places)
    if not settings.return_dates:
        return await plan_one_way(settings, places, from_map=from_map)
    show(f"# {tr('way_there', origin=settings.origin, destination=settings.destination)}")
    going = await plan_one_way(settings, places)
    there = PLANNED[-1]
    back_settings = settings.return_trip()
    if settings.ground_only:
        back_settings = after_the_stay(back_settings, going.get("ground_only") or [])
        summary = lambda found: render_ground_round_trip(going.get("ground_only") or [], found.get("ground_only") or [],
                                                         settings.origin, settings.destination, settings.min_stay_hours)
    else:
        summary = lambda found: render_round_trip(going.get("shown") or [], found.get("shown") or [], settings.origin, settings.destination)
    current.SETTINGS = back_settings
    show(f"# {tr('way_back', origin=back_settings.origin, destination=back_settings.destination)}")
    coming = await plan_one_way(back_settings, return_places(places), after=summary, beside=there.get("report"))
    # The questions that follow are about the round trip: the conversation of both ways, the report of both
    back = PLANNED[-1]
    back["there"], back["messages"] = there, there["messages"] + back["messages"]
    return {**coming, "there": going}

def after_the_stay(back: TripSettings, going: list[dict[str, Any]]) -> TripSettings:
    """The way back of a day trip without a plane, searched from the first arrival there plus the shortest stay
    (min_stay_hours). The way back is searched as a trip of its own, with the same hours to leave as the way there:
    on the day of arrival its fastest trips would be those of the morning, before the traveller even arrives, and
    none of them would pair. Only when every return date is that day: on a later one the whole day is worth searching."""
    arrivals = [datetime.fromisoformat(option["day"]) + timedelta(minutes=option["arrive"]) for option in going]
    if not arrivals:
        return back
    earliest = min(arrivals) + timedelta(hours=back.min_stay_hours)
    days = {datetime.strptime(day, "%d/%m/%Y").date() for day in back.dates}
    if days != {earliest.date()} or f"{earliest:%H:%M}" <= back.earliest_leave:
        return back
    try:
        return dataclasses.replace(back, earliest_leave=f"{earliest:%H:%M}")
    except SettingError:   # later than the last hour to leave: nothing back that day, as the summary says
        return back

def start_trip(settings: TripSettings, places: TripPlaces) -> None:
    """Make a one-way trip the current one, and forget what the last one found. Each way of a round trip is a trip.
    What a session keeps from one trip to the next (places, airports, the operators' sessions and prices, the models
    out of quota) is not touched. Two parts of a trip are cleared elsewhere: the flight search by its own step, so
    that a step run again starts clean (steps.search_flights), and the log of requests when its report is saved,
    because it starts before this, when the places and the airports are chosen (report/full.py)."""
    current.SETTINGS = settings
    current.TRIP = places
    TIMINGS.clear()
    COUNTS.clear()
    steps.FAULTS.clear()
    airlines.CHECKED.clear()
    web.SEEN_DOMAINS.clear()   # a link of this trip's models is kept only if its site came up in this trip's searches

async def plan_one_way(settings: TripSettings, places: TripPlaces, after: Callable[[dict], str] | None = None,
                       beside: Path | None = None, from_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """Plan one way: search, show the result, write the recommendation and save the report. after, if given, writes
    from the result what is shown once the recommendation is (the summary of a round trip), saved at the end of the
    report; beside is the report of the way there, which saving this one must not delete to make room. from_map: see
    plan_trip."""
    start_trip(settings, places)
    started = time.time()
    for advice in settings.advice():
        say(f"{tr('advice')}: {advice}")
    point = lambda place: {"name": place.written.split(",")[0].strip(), "lat": place.latitude, "lon": place.longitude}
    airports = lambda group: [{"code": airport["code"], "lat": airport["latitude"], "lon": airport["longitude"]} for airport in group
                              if "latitude" in airport]
    progress(trip={"origin": point(current.TRIP.origin), "destination": point(current.TRIP.destination), "ground_only": settings.ground_only,
                   "departure": airports(current.TRIP.departure), "arrival": airports(current.TRIP.arrival)})
    if settings.ground_only:
        say(tr("progress_start_ground", origin=settings.origin, destination=settings.destination))
    else:
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
    if from_map:
        response["from_map"] = from_map   # only kept with the report's numbers (report/metrics.py)
    # The result is the traveller's as soon as the code has it. What the model adds comes after, if it comes.
    show(response.get("report") or "")
    messages = [HumanMessage(content=results_for_model(build_request(settings, current.TRIP), response))]
    PLANNED.append({"settings": settings, "places": current.TRIP, "messages": messages, "state": response})
    writing = time.time()
    # Without a plane there is no option the rules recommend to put in words: the list is in the rules' order,
    # and the model's quota is kept for the trips that need it
    if not settings.ground_only:
        progress(step="recommend")
    recommendation = "" if settings.ground_only else await recommend(response, messages)
    response["recommendation"] = recommendation   # kept apart in the options as data (report/options.py): a model wrote it
    TIMINGS["4"] = time.time() - writing
    total = TIMINGS["total"] = time.time() - started
    remember_duration(total, "ground" if settings.ground_only else "plane")
    progress(step="done")
    if recommendation:
        show(recommendation)
    summary = after(response) if after else ""
    if summary:
        show(summary)
    PLANNED[-1]["report"] = save_report(response, "\n\n".join(part for part in (recommendation, summary) if part), beside).resolve()
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
    # The answer is written by a model: it is shown as such, and the report stays the reference (both, for a round trip)
    trips = [trip for trip in (planned.get("there"), planned) if trip]
    answer = web.only_known_links(answer, "\n".join(trip["state"].get("report") or "" for trip in trips))
    answer = claims.check_claims(answer, "\n".join(given_to_model(trip["state"]) for trip in trips),
                                 [itinerary for trip in trips for itinerary in trip["state"].get("shown") or []],
                                 planned["state"].get("recommended") or 0, colours=len(trips) == 1)
    markdown = (f"**{web.clean_text(question)}**\n\n{answer}\n\n> {tr('followup_note')}")
    show(markdown)

PLANNED: list[dict[str, Any]] = []   # every trip run in this session, with what was found and the conversation about it

async def plan_all(trips: list[TripSettings]) -> list[dict[str, Any]]:
    """Plan every trip, one after the other. Returns what each trip planned found: fewer than the trips if one failed."""
    planned = []
    try:
        for settings in trips:
            planned.append(await plan_trip(settings))
    except Exception:
        say(traceback.format_exc())
    return planned

def show_timings(planned: list[dict[str, Any]]) -> None:
    """The trips planned, and where the time of the last one went."""
    for trip in planned:
        print(f"{trip.get('origin')} -> {trip.get('final_destination')}")
    for step, seconds in sorted(TIMINGS.items()):
        print(f"{seconds:6.0f} s  {step_name(step)}")



def keep_trip(planned: dict[str, Any], keep: bool) -> None:
    """Keep the report of a trip in saved/, or put it back among the recent ones: both reports of a round trip."""
    for trip in (planned.get("there"), planned):
        if trip and trip.get("report") is not None:
            trip["report"] = keep_report(trip["report"], keep)

def ask_to_keep() -> None:
    """Ask whether the report of the last trip is to be kept. Otherwise it goes when newer ones arrive."""
    if not PLANNED or PLANNED[-1].get("report") is None:
        return
    try:
        answer = input("\n" + tr("keep_report_prompt", limit=current.SETTINGS.recent_reports) + " ")
    except (EOFError, KeyboardInterrupt):
        return
    if plain(answer) in ("y", "yes", "s", "sim"):
        keep_trip(PLANNED[-1], True)
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
