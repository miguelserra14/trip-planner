"""The steps of a trip: search the flights, look up their ground transport, build and rank the itineraries.
Each step is a function that reads the state of the trip and returns what it adds to it, and needs the result of
the one before. run_steps runs them in order, in code: no model decides what comes next."""
import asyncio
import re
import traceback
from datetime import date
from typing import Any

from langchain.messages import HumanMessage

from trip_planner import current, ground_choice
from trip_planner.agents import subagents
from trip_planner.config import LIGHTS
from trip_planner.itineraries import selection
from trip_planner.itineraries.legs import Rejected
from trip_planner.itineraries.ranking import check_itinerary, closeness, price_itineraries, price_itinerary, rank_itineraries
from trip_planner.places import describe_airport
from trip_planner.report.summary import render_closest, render_report
from trip_planner.sources import airlines, flight_search, google_flights, kiwi, timetables, web
from trip_planner.texts import tr
from trip_planner.utils import logged, REQUESTS, say, timed

def iata(text: str) -> str:
    """The first IATA code in a text. The model sometimes writes junk after the airport code."""
    match = re.search(r"[A-Z]{3}", (text or "").upper())
    return match[0] if match else ""

def travel_days(flights: list[dict[str, Any]]) -> str:
    """The dates of the flights with their weekday, for the ground transport specialists."""
    days = sorted({flight["date"] for flight in flights})
    return ", ".join(f"{day} ({date.fromisoformat(day).strftime('%A')})" for day in days)

async def search_flights(state: dict[str, Any]) -> dict[str, Any]:
    """Search the flights from every departure airport to every arrival airport on every travel date."""
    with timed("1 flight search"):
        flight_search.FLIGHTS.clear()
        flight_search.SEARCHED.clear()
        flight_search.FLIGHT_GAPS.clear()
        FAULTS.clear()
        kiwi.kiwi_retries.retries = 0
        google_flights.requests = 0
        if current.SETTINGS.use_flight_agent:
            # Optional: a model searches too. Whatever it finds lands in the same list, and the code still searches every route.
            query = (
                f"Find one-way flights on these dates: {state['travel_dates']}. "
                f"Primary departure airports: {state['primary_airports']}. "
                f"Fallback departure airports: {state['fallback_airports'] or 'none'}. "
                f"Allowed arrival airports: {state['arrival_airports']}."
            )
            await subagents.flight_agent.ainvoke({"messages": [HumanMessage(content=query)]}, config={"recursion_limit": 80})
        await flight_search.search_all_flights()
        routes = len(current.TRIP.departure) * len(current.TRIP.arrival) * len(current.SETTINGS.dates)
        flights = sorted(flight_search.FLIGHTS.values(), key=lambda flight: flight["price_eur"])
        say(tr("progress_flights", flights=len(flights), searched=len(flight_search.SEARCHED), routes=routes, retries=kiwi.kiwi_retries.retries))
        if google_flights.requests:
            say(tr("progress_backup", backup=google_flights.NAME, requests=google_flights.requests))
        # Nothing found: the reason is worked out in code and shown to the traveller as it is. The flights that
        # break the limits (but land the same day) go on to the next steps, to show the closest alternatives.
        reason, found = "", len(flights)
        if not flights:
            reason, others = await flight_search.why_no_flights()
            for itinerary in others:
                flight_search.register_flight(itinerary, closest=True)
            flights = sorted(flight_search.FLIGHTS.values(), key=lambda flight: flight["price_eur"])
            if flights:
                say(tr("progress_closest", count=len(flights)))
    summary = "\n".join(flight_search.describe_flight(flight) for flight in flights[:15]) or "No flights found."
    if not found:
        summary = (f"{reason}\n" + (f"These {len(flights)} flights break the traveller's limits. They are kept to show the closest "
                                    f"alternatives: the next steps go on with them.\n{summary}" if flights else ""))
    if len(flights) > 15:
        summary += f"\n... and {len(flights) - 15} more expensive flights."
    if flight_search.FLIGHT_GAPS:
        summary += "\nThe flight list may be incomplete: " + "; ".join(flight_search.FLIGHT_GAPS)
    return {
        "flights": flights,
        "step": 1 if flights else len(STEPS),   # without flights there is nothing left to look up
        "flight_gaps": list(flight_search.FLIGHT_GAPS),
        "checked": [], "ground": {}, "transfers": [], "trains": [], "report": reason,
        "message": f"{found} flights fit the traveller's limits, cheapest first:\n{summary}"}

# A specialist that could not search found nothing in a source, whatever it says
NOT_SEARCHED = {"found_in_source": False, "price_known": False}

def not_covered(flights: list[dict[str, Any]], ground: dict[str, Any], side: str) -> set:
    """The airports of one side ("to_airport" or "from_airport") that no timetable covers for some flight."""
    end = "departure_airport" if side == "to_airport" else "arrival_airport"
    return {flight[end] for flight in flights if not ground[flight["id"]][side]["covered"]}

async def ask_transfer_agent(flights: list[dict[str, Any]], only: set | None = None,
                             searches: int = subagents.TRANSFER_SEARCH_LIMIT) -> list[dict[str, Any]]:
    """What the airport transfer agent found for each departure airport. Cleaned, and graded C by whoever uses it.
    only limits it to some airports, searches is how many web searches it may make."""
    with timed("2a airport transfer agent"):
        used = {flight["departure_airport"] for flight in flights} & (only if only is not None else {airport["code"] for airport in current.TRIP.departure})
        airports = [airport for airport in current.TRIP.departure if airport["code"] in used]
        if not airports:
            return []
        query = (
            f"Origin city: {current.TRIP.origin.label}. Travel dates: {travel_days(flights)}.\n"
            "Find the bus/train from the origin city to each of these airports "
            "(the distance is the straight line from the origin city):\n"
            + "\n".join(f"- {describe_airport(airport)}" for airport in airports)
        )
        try:
            with logged("model", f"airport transfer agent, {len(airports)} airports, {searches} web searches allowed"):
                response = await subagents.airport_transfer_agent.ainvoke(
                    {"messages": [HumanMessage(content=query)], "search_limit": searches}, config={"recursion_limit": 40})
            plan = response.get("structured_response")
        except Exception as e:
            say(f"[airport transfer agent] {type(e).__name__}: no answer, prices are estimated from the distance")
            plan = None
        return [
            {**transfer.model_dump(), **({} if searches else NOT_SEARCHED), "airport": iata(transfer.airport),
             "description": web.clean_text(transfer.description), "notes": web.clean_text(transfer.notes),
             "operator_url": web.safe_url(transfer.operator_url)}
            for transfer in (plan.transfers if plan else [])
        ]

async def ask_destination_agent(flights: list[dict[str, Any]], only: set | None = None,
                                searches: int = subagents.DESTINATION_SEARCH_LIMIT) -> list[dict[str, Any]]:
    """What the destination transport agent found for each arrival airport. Cleaned, and graded C by whoever uses it.
    only limits it to some airports, searches is how many web searches it may make."""
    with timed("2b destination transport agent"):
        used = {flight["arrival_airport"] for flight in flights} & (only if only is not None else {airport["code"] for airport in current.TRIP.arrival})
        airports = [airport for airport in current.TRIP.arrival if airport["code"] in used]
        if not airports:
            return []
        query = (
            f"Final destination: {current.TRIP.destination.label}. Travel dates: {travel_days(flights)}.\n"
            "Find the link to the train station and the train timetable to the final destination for each of these airports "
            "(the distance is the straight line to the final destination):\n"
            + "\n".join(f"- {describe_airport(airport)}" for airport in airports)
        )
        try:
            with logged("model", f"destination transport agent, {len(airports)} airports, {searches} web searches allowed"):
                response = await subagents.destination_transport_agent.ainvoke(
                    {"messages": [HumanMessage(content=query)], "search_limit": searches}, config={"recursion_limit": 40})
            plan = response.get("structured_response")
        except Exception as e:
            say(f"[destination transport agent] {type(e).__name__}: no answer")
            plan = None
        return [
            {**route.model_dump(), **({} if searches else NOT_SEARCHED), "airport": iata(route.airport), "station": web.clean_text(route.station, 60),
             "link_description": web.clean_text(route.link_description), "notes": web.clean_text(route.notes),
             "operator_url": web.safe_url(route.operator_url),
             "departures": [{**departure, "train_type": web.clean_text(departure["train_type"], 60)} for departure in route.model_dump()["departures"]]}
            for route in (plan.routes if plan else [])
        ]

async def read_timetables(flights: list[dict[str, Any]]) -> dict[str, Any]:
    with timed("2c real timetables"):
        return await timetables.ground_transport_for(flights)

async def ground_transport(checked: list[dict[str, Any]], asked: set = frozenset()) -> tuple:
    """The timetables and what the two specialists found, for the flights whose ground transport is looked up.
    asked is the airports the specialists were already asked about, as ("to" or "from", code): not asked again."""
    leaving = {flight["departure_airport"] for flight in checked if ("to", flight["departure_airport"]) not in asked}
    landing = {flight["arrival_airport"] for flight in checked if ("from", flight["arrival_airport"]) not in asked}
    # On a side where the traveller chose how to travel, what a model finds cannot be checked and is not used: not asked
    leaving, landing = (set() if ground_choice.restricted(True) else leaving), (set() if ground_choice.restricted(False) else landing)
    if await asyncio.to_thread(web.available):
        return tuple(await asyncio.gather(read_timetables(checked), ask_transfer_agent(checked, leaving), ask_destination_agent(checked, landing)))
    # Plan B, without the web search: the timetables first, then the specialists only for the airports no
    # timetable covers. They cannot search, so they answer from what the model knows, graded C as always.
    ground = await read_timetables(checked)
    leaving, landing = leaving & not_covered(checked, ground, "to_airport"), landing & not_covered(checked, ground, "from_airport")
    say(tr("progress_no_web", count=len(leaving) + len(landing)))
    transfers, trains = await asyncio.gather(ask_transfer_agent(checked, leaving, 0), ask_destination_agent(checked, landing, 0))
    return ground, transfers, trains

# The flights the program failed on while working out their itinerary: {flight id: the error}. Such a flight is
# left out like a rejected one, so that one mistake does not lose the whole search, but it is never passed off as
# one: it is said at once, kept in the log of the run with where it happened, and counted in the report.
FAULTS: dict[str, str] = {}

def itinerary_or_reason(flight: dict[str, Any], ground: dict[str, Any], transfers: dict[str, Any], routes: dict[str, Any],
                        closest: bool = False) -> tuple:
    """(the itinerary of a flight, "") or (None, why it cannot make one). transfers and routes are what the
    specialists found, by airport."""
    try:
        return check_itinerary(flight, ground, transfers.get(flight["departure_airport"]), routes.get(flight["arrival_airport"]), closest), ""
    except Rejected as reason:
        return None, str(reason)
    except Exception as error:
        fault = f"{type(error).__name__}: {error}"
        if flight["id"] not in FAULTS:
            FAULTS[flight["id"]] = fault
            say(f"[itineraries] {flight['id']}: the program failed on this flight, it is left out. {fault}")
            where = " | ".join(line.strip() for line in traceback.format_exc().splitlines()[-7:])
            REQUESTS.append({"at": "", "service": "program", "what": f"itinerary of {flight['id']}: {where}", "seconds": 0.0, "result": fault})
        return None, tr("reject_program_error", error=fault)

def itineraries_of(flights: list[dict[str, Any]], ground: dict[str, Any], transfers: list[dict[str, Any]],
                   trains: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """The priced itinerary of each flight, or None when the flight cannot make one."""
    transfer_of = {transfer["airport"]: transfer for transfer in transfers}
    route_of = {route["airport"]: route for route in trains}
    made: dict[str, dict[str, Any] | None] = {}
    for flight in flights:
        made[flight["id"]] = itinerary_or_reason(flight, ground, transfer_of, route_of)[0]
    price_itineraries([itinerary for itinerary in made.values() if itinerary])
    # One whose operator says a connection does not run that day cannot win, nor stop the search for the others
    return {flight_id: itinerary if itinerary and not itinerary["not_running"] else None for flight_id, itinerary in made.items()}

async def look_up_in_rounds(flights: list[dict[str, Any]]) -> tuple:
    """The ground transport of the flights that can still win, a few at a time (see selection.py).
    Returns (the flights looked up, ground, transfers, trains, why it stopped)."""
    checked: list[dict[str, Any]] = []
    ground: dict[str, Any] = {"operators": {}, "operators_from": {}, "requests": 0}
    transfers: list[dict[str, Any]] = []
    trains: list[dict[str, Any]] = []
    looked_up: dict[str, dict[str, Any] | None] = {}
    while True:
        batch, reason = selection.next_flights(flights, looked_up)
        if not batch:
            return checked, ground, transfers, trains, reason
        asked = {("to", transfer["airport"]) for transfer in transfers} | {("from", route["airport"]) for route in trains}
        found, new_transfers, new_trains = await ground_transport(batch, asked)
        for side in ("operators", "operators_from"):
            for code, days in (found.pop(side, None) or {}).items():
                for day, rides in days.items():
                    ground[side].setdefault(code, {}).setdefault(day, rides)
        ground["requests"] += found.pop("requests", 0)
        ground.update(found)
        transfers += new_transfers
        trains += new_trains
        checked += batch
        looked_up.update(await asyncio.to_thread(itineraries_of, batch, ground, transfers, trains))
        say(tr("progress_round", checked=len(checked), itineraries=sum(1 for itinerary in looked_up.values() if itinerary)))

async def search_ground_transport(state: dict[str, Any]) -> dict[str, Any]:
    """Find the transport to the departure airport and from the arrival airport of the cheapest flights:
    real timetables read by code, and the two transport specialists for prices and for what no timetable covers."""
    flights = state["flights"]
    airlines.CHECKED.clear()
    with timed("2 ground transport (the three below run at the same time)"):
        if state.get("report"):
            # No flight fits the traveller's limits: the closest ones are looked up, the ones that break the least first
            checked, stopped = timetables.flights_to_check(flights), "limit"
            ground, transfers, trains = await ground_transport(checked)
        else:
            checked, ground, transfers, trains, stopped = await look_up_in_rounds(sorted(flights, key=lambda flight: flight["price_eur"]))
    legs = [ground[flight["id"]][side] for flight in checked for side in ("to_airport", "from_airport")]
    covered = sum(leg["covered"] for leg in legs)
    say(tr("progress_ground", flights=len(checked), covered=covered, legs=len(legs), requests=ground.get("requests", 0)))
    return {
        "step": 2,
        "checked": [flight["id"] for flight in checked],
        "stopped": stopped,
        "ground": ground,
        "transfers": transfers,
        "trains": trains,
        "message": f"Ground transport looked up for {len(checked)} flights"
            + (": every other flight costs too much to beat the best itinerary found. " if stopped == "beaten" else ". ") +
            f"Real timetables cover {covered} of {len(legs)} legs; "
            f"the others rely on the specialists' unverified estimates. Prices found by the specialists for "
            f"{sum(t['price_known'] for t in transfers)} of {len(transfers)} departure airports."}

def build_itineraries(state: dict[str, Any]) -> dict[str, Any]:
    """Combine the flights with their ground transport into door-to-door itineraries, discard the ones that break
    a hard constraint or are not reliable enough, and rank the rest."""
    with timed("3 checks, airline prices and ranking"):
        transfers = {transfer["airport"]: transfer for transfer in state.get("transfers") or []}
        routes = {route["airport"]: route for route in state.get("trains") or []}
        ground, checked = state.get("ground") or {}, set(state["checked"])

        itineraries, rejected, warnings = [], [], []
        for flight in sorted(state["flights"], key=lambda flight: flight["price_eur"]):
            if flight["id"] not in checked:
                continue
            itinerary, reason = itinerary_or_reason(flight, ground, transfers, routes)
            if itinerary is None:
                rejected.append(f"{flight['id']} ({flight['price_eur']:.0f} EUR): {reason}")
                continue
            itineraries.append(itinerary)
        price_itineraries(itineraries)
        rejected += [f"{itinerary['flight']['id']} ({itinerary['total_cost']:.0f} EUR): {itinerary['not_running']}"
                     for itinerary in itineraries if itinerary["not_running"]]
        itineraries = [itinerary for itinerary in itineraries if not itinerary["not_running"]]

        # Reliability comes before price: an itinerary below the minimum grade is only shown when nothing better exists
        reliable = [itinerary for itinerary in itineraries if itinerary["schedule_grade"] <= current.SETTINGS.min_reliability]
        if reliable:
            rejected += [f"{itinerary['flight']['id']} ({itinerary['total_cost']:.0f} EUR): "
                         + tr("reject_below_minimum", grade=LIGHTS[itinerary["schedule_grade"]], minimum=LIGHTS[current.SETTINGS.min_reliability])
                         for itinerary in itineraries if itinerary not in reliable]
            itineraries = reliable
        elif itineraries:
            warnings.append(tr("warn_below_minimum", minimum=LIGHTS[current.SETTINGS.min_reliability]))
        skipped = len(state["flights"]) - len(checked)
        if skipped and state.get("stopped") == "beaten":
            warnings.append(tr("note_flights_beaten", skipped=skipped, checked=len(checked)))
        elif skipped:
            warnings.append(tr("warn_flights_not_checked", skipped=skipped, checked=len(checked)))
        if state.get("flight_gaps"):
            warnings.append(tr("warn_flight_gaps", count=len(state["flight_gaps"])))
        if FAULTS:
            warnings.append(tr("warn_program_errors", count=len(FAULTS)))

        if not itineraries:
            # Nothing respects every requirement. The same flights again, letting give way the requirements that
            # can (REQUIREMENTS in ranking.py): the closest itineraries are shown, with what each one breaks.
            no_flight_fits = state.get("report") or ""   # written by search_flights when that was the case
            near = []
            for flight in state["flights"]:
                if flight["id"] in checked:
                    itinerary, _ = itinerary_or_reason(flight, ground, transfers, routes, closest=True)
                    if itinerary is None:
                        continue   # no real connection at all: it is already among the rejected, with the reason
                    if itinerary["breaches"]:
                        price_itinerary(itinerary)
                        if not itinerary["not_running"]:
                            near.append(itinerary)
            reliable = [itinerary for itinerary in near if itinerary["schedule_grade"] <= current.SETTINGS.min_reliability]
            if near and not reliable:
                warnings.append(tr("warn_below_minimum", minimum=LIGHTS[current.SETTINGS.min_reliability]))
            shown = sorted(reliable or near, key=closeness)[:current.SETTINGS.options_shown]
            if shown:
                for itinerary in shown:
                    if not itinerary["booking"]["url"]:
                        itinerary["booking"]["url"] = airlines.airline_site(itinerary["flight"].get("carrier", ""), itinerary["flight"]["airline"])
                report = "\n\n".join(part for part in (no_flight_fits, render_closest(shown, warnings)) if part)
                return {
                    "step": 3, "report": report, "rejected": rejected, "warnings": warnings, "recommended": 1,
                    "message": f"{report}\n\nNo itinerary respects every requirement. The options above are the closest ones, each "
                        "breaking at least one requirement. By the order of requirements, option 1 is the closest."}
            message = "No itinerary respects the hard constraints. Rejected flights:\n" + "\n".join(rejected[:12])
            # The reasons are shown to the traveller as the code found them, above whatever the coordinator writes
            report = "\n".join(([no_flight_fits, ""] if no_flight_fits else [])
                               + [f"## {tr('no_itinerary')}", "", tr("no_itinerary_reasons"), ""] + [f"- {reason}" for reason in rejected])
            return {
                "step": 3, "report": report, "rejected": rejected, "warnings": warnings, "recommended": 0,
                "message": message}

        kept, recommended, not_worth_it = rank_itineraries(itineraries)
        limit = current.SETTINGS.options_shown
        shown = kept[:limit] if any(itinerary is recommended for itinerary in kept[:limit]) else kept[:limit - 1] + [recommended]
        shown.sort(key=lambda itinerary: (itinerary["total_cost"], itinerary["total_minutes"]))
        for itinerary in shown:
            if not itinerary["booking"]["url"]:
                # Not confirmed by the airline: a looked-up site, shown as such
                itinerary["booking"]["url"] = airlines.airline_site(itinerary["flight"].get("carrier", ""), itinerary["flight"]["airline"])
        number = next(index for index, itinerary in enumerate(shown, start=1) if itinerary is recommended)
        report = render_report(shown, recommended, warnings)
    return {
        "step": 3,
        "report": report,
        "rejected": rejected + not_worth_it,
        "warnings": warnings,
        "recommended": number,
        "message": f"{report}\n\nThe rules recommend option {number}."}

# The steps of a trip, in order. Each one needs the result of the one before.
STEPS = [search_flights, search_ground_transport, build_itineraries]

async def run_steps(state: dict[str, Any]) -> dict[str, Any]:
    """Run the steps that are left, in order. state["step"] is how many are done: a run that failed in the middle
    is called again with the same state and continues from there. What each step found, in words, is kept under
    "notes" for the model that writes the recommendation."""
    while (state.get("step") or 0) < len(STEPS):
        step = STEPS[state.get("step") or 0]
        found = await step(state) if asyncio.iscoroutinefunction(step) else await asyncio.to_thread(step, state)
        state.setdefault("notes", []).append(found.pop("message"))
        state.update(found)
    return state
