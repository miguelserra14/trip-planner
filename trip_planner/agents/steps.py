"""The steps of a trip, as tools of the coordinator: search the flights, look up their ground transport, build and
rank the itineraries. Each step needs the result of the one before."""
import asyncio
import re
from datetime import date
from typing import Any

from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import tool, ToolRuntime
from langgraph.types import Command

from trip_planner import current
from trip_planner.agents import subagents
from trip_planner.config import LIGHTS
from trip_planner.itineraries.ranking import check_itinerary, closeness, price_itineraries, price_itinerary, rank_itineraries
from trip_planner.places import describe_airport
from trip_planner.report.summary import render_closest, render_report
from trip_planner.sources import airlines, google_flights, kiwi, timetables, web
from trip_planner.texts import tr
from trip_planner.utils import say, timed

# The steps of a trip, in order. Each one is a tool of the coordinator and needs the result of the one before.
STEPS = ["search_flights", "search_ground_transport", "build_itineraries"]

def iata(text: str) -> str:
    """The first IATA code in a text. The model sometimes writes junk after the airport code."""
    match = re.search(r"[A-Z]{3}", (text or "").upper())
    return match[0] if match else ""

def travel_days(flights: list[dict[str, Any]]) -> str:
    """The dates of the flights with their weekday, for the ground transport specialists."""
    days = sorted({flight["date"] for flight in flights})
    return ", ".join(f"{day} ({date.fromisoformat(day).strftime('%A')})" for day in days)

@tool
async def search_flights(runtime: ToolRuntime) -> Command:
    """Search the flights from every departure airport to every arrival airport on every travel date."""
    with timed("1 flight search"):
        kiwi.FLIGHTS.clear()
        kiwi.SEARCHED.clear()
        kiwi.FLIGHT_GAPS.clear()
        kiwi.kiwi_retries.retries = 0
        google_flights.requests = 0
        if current.SETTINGS.use_flight_agent:
            # Optional: a model searches too. Whatever it finds lands in the same list, and the code still searches every route.
            state = runtime.state
            query = (
                f"Find one-way flights on these dates: {state['travel_dates']}. "
                f"Primary departure airports: {state['primary_airports']}. "
                f"Fallback departure airports: {state['fallback_airports'] or 'none'}. "
                f"Allowed arrival airports: {state['arrival_airports']}."
            )
            await subagents.flight_agent.ainvoke({"messages": [HumanMessage(content=query)]}, config={"recursion_limit": 80})
        await kiwi.search_all_flights()
        routes = len(current.TRIP.departure) * len(current.TRIP.arrival) * len(current.SETTINGS.dates)
        flights = sorted(kiwi.FLIGHTS.values(), key=lambda flight: flight["price_eur"])
        say(tr("progress_flights", flights=len(flights), searched=len(kiwi.SEARCHED), routes=routes, retries=kiwi.kiwi_retries.retries))
        if google_flights.requests:
            say(tr("progress_backup", backup=google_flights.NAME, requests=google_flights.requests))
        # Nothing found: the reason is worked out in code and shown to the traveller as it is. The flights that
        # break the limits (but land the same day) go on to the next steps, to show the closest alternatives.
        reason, found = "", len(flights)
        if not flights:
            reason, others = await kiwi.why_no_flights()
            for itinerary in others:
                kiwi.register_flight(itinerary, closest=True)
            flights = sorted(kiwi.FLIGHTS.values(), key=lambda flight: flight["price_eur"])
            if flights:
                say(tr("progress_closest", count=len(flights)))
    summary = "\n".join(kiwi.describe_flight(flight) for flight in flights[:15]) or "No flights found."
    if not found:
        summary = (f"{reason}\n" + (f"These {len(flights)} flights break the traveller's limits. They are kept to show the closest "
                                    f"alternatives: continue with search_ground_transport.\n{summary}" if flights else ""))
    if len(flights) > 15:
        summary += f"\n... and {len(flights) - 15} more expensive flights."
    if kiwi.FLIGHT_GAPS:
        summary += "\nThe flight list may be incomplete: " + "; ".join(kiwi.FLIGHT_GAPS)
    return Command(update={
        "flights": flights,
        "step": 1 if flights else len(STEPS),   # without flights there is nothing left to look up
        "flight_gaps": list(kiwi.FLIGHT_GAPS),
        "checked": [], "ground": {}, "transfers": [], "trains": [], "report": reason,
        "messages": [ToolMessage(f"{found} flights fit the traveller's limits, cheapest first:\n{summary}", tool_call_id=runtime.tool_call_id)]}
        )

async def ask_transfer_agent(flights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the airport transfer agent found for each departure airport. Cleaned, and graded C by whoever uses it."""
    with timed("2a airport transfer agent"):
        used = {flight["departure_airport"] for flight in flights}
        airports = [airport for airport in current.TRIP.departure if airport["code"] in used]
        query = (
            f"Origin city: {current.TRIP.origin.label}. Travel dates: {travel_days(flights)}.\n"
            "Find the bus/train from the origin city to each of these airports "
            "(the distance is the straight line from the origin city):\n"
            + "\n".join(f"- {describe_airport(airport)}" for airport in airports)
        )
        try:
            response = await subagents.airport_transfer_agent.ainvoke(
                {"messages": [HumanMessage(content=query)], "search_limit": subagents.TRANSFER_SEARCH_LIMIT}, config={"recursion_limit": 40})
            plan = response.get("structured_response")
        except Exception as e:
            say(f"[airport transfer agent] {type(e).__name__}: no answer, prices are estimated from the distance")
            plan = None
        return [
            {**transfer.model_dump(), "airport": iata(transfer.airport),
             "description": web.clean_text(transfer.description), "notes": web.clean_text(transfer.notes),
             "operator_url": web.safe_url(transfer.operator_url)}
            for transfer in (plan.transfers if plan else [])
        ]

async def ask_destination_agent(flights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the destination transport agent found for each arrival airport. Cleaned, and graded C by whoever uses it."""
    with timed("2b destination transport agent"):
        used = {flight["arrival_airport"] for flight in flights}
        airports = [airport for airport in current.TRIP.arrival if airport["code"] in used]
        query = (
            f"Final destination: {current.TRIP.destination.label}. Travel dates: {travel_days(flights)}.\n"
            "Find the link to the train station and the train timetable to the final destination for each of these airports "
            "(the distance is the straight line to the final destination):\n"
            + "\n".join(f"- {describe_airport(airport)}" for airport in airports)
        )
        try:
            response = await subagents.destination_transport_agent.ainvoke(
                {"messages": [HumanMessage(content=query)], "search_limit": subagents.DESTINATION_SEARCH_LIMIT}, config={"recursion_limit": 40})
            plan = response.get("structured_response")
        except Exception as e:
            say(f"[destination transport agent] {type(e).__name__}: no answer")
            plan = None
        return [
            {**route.model_dump(), "airport": iata(route.airport), "station": web.clean_text(route.station, 60),
             "link_description": web.clean_text(route.link_description), "notes": web.clean_text(route.notes),
             "operator_url": web.safe_url(route.operator_url),
             "departures": [{**departure, "train_type": web.clean_text(departure["train_type"], 60)} for departure in route.model_dump()["departures"]]}
            for route in (plan.routes if plan else [])
        ]

async def read_timetables(flights: list[dict[str, Any]]) -> dict[str, Any]:
    with timed("2c real timetables"):
        return await timetables.ground_transport_for(flights)

@tool
async def search_ground_transport(runtime: ToolRuntime) -> Command | str:
    """Find the transport to the departure airport and from the arrival airport of the cheapest flights:
    real timetables read by code, and the two transport specialists for prices and for what no timetable covers."""
    flights = runtime.state.get("flights") or []
    if not flights:
        return "There are no flights yet. Call search_flights first, wait for its answer, then call this tool again."
    checked = timetables.flights_to_check(flights)
    with timed("2 ground transport (the three below run at the same time)"):
        ground, transfers, trains = await asyncio.gather(
            read_timetables(checked), ask_transfer_agent(checked), ask_destination_agent(checked),
        )
    legs = [ground[flight["id"]][side] for flight in checked for side in ("to_airport", "from_airport")]
    covered = sum(leg["covered"] for leg in legs)
    say(tr("progress_ground", flights=len(checked), covered=covered, legs=len(legs), requests=ground.get("requests", 0)))
    return Command(update={
        "step": 2,
        "checked": [flight["id"] for flight in checked],
        "ground": ground,
        "transfers": transfers,
        "trains": trains,
        "messages": [ToolMessage(
            f"Ground transport looked up for the {len(checked)} cheapest flights. Real timetables cover {covered} of {len(legs)} legs; "
            f"the others rely on the specialists' unverified estimates. Prices found by the specialists for "
            f"{sum(t['price_known'] for t in transfers)} of {len(transfers)} departure airports.",
            tool_call_id=runtime.tool_call_id)]}
        )

@tool
def build_itineraries(runtime: ToolRuntime) -> Command | str:
    """Combine the flights with their ground transport into door-to-door itineraries, discard the ones that break
    a hard constraint or are not reliable enough, and rank the rest. Call it after search_ground_transport."""
    state = runtime.state
    if not state.get("flights") or not state.get("checked"):
        return ("The flights or their ground transport are missing. Call search_flights, then search_ground_transport, "
                "wait for their answers, then call this tool again.")
    with timed("3 checks, airline prices and ranking"):
        transfers = {transfer["airport"]: transfer for transfer in state.get("transfers") or []}
        routes = {route["airport"]: route for route in state.get("trains") or []}
        ground, checked = state.get("ground") or {}, set(state["checked"])

        itineraries, rejected, warnings = [], [], []
        for flight in sorted(state["flights"], key=lambda flight: flight["price_eur"]):
            if flight["id"] not in checked:
                continue
            try:
                itinerary = check_itinerary(flight, ground, transfers.get(flight["departure_airport"]), routes.get(flight["arrival_airport"]))
            except (ValueError, KeyError, TypeError) as reason:
                rejected.append(f"{flight['id']} ({flight['price_eur']:.0f} EUR): {reason}")
                continue
            itineraries.append(itinerary)
        price_itineraries(itineraries)

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
        if skipped:
            warnings.append(tr("warn_flights_not_checked", skipped=skipped, checked=len(checked)))
        if state.get("flight_gaps"):
            warnings.append(tr("warn_flight_gaps", count=len(state["flight_gaps"])))

        if not itineraries:
            # Nothing respects every requirement. The same flights again, letting give way the requirements that
            # can (REQUIREMENTS in ranking.py): the closest itineraries are shown, with what each one breaks.
            no_flight_fits = state.get("report") or ""   # written by search_flights when that was the case
            near = []
            for flight in state["flights"]:
                if flight["id"] in checked:
                    try:
                        itinerary = check_itinerary(flight, ground, transfers.get(flight["departure_airport"]),
                                                    routes.get(flight["arrival_airport"]), closest=True)
                    except (ValueError, KeyError, TypeError):
                        continue   # no real connection at all: it is already among the rejected, with the reason
                    if itinerary["breaches"]:
                        price_itinerary(itinerary)
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
                return Command(update={
                    "step": 3, "report": report, "rejected": rejected, "warnings": warnings, "recommended": 1,
                    "messages": [ToolMessage(
                        f"{report}\n\nNo itinerary respects every requirement. The options above are the closest ones, each "
                        "breaking at least one requirement. By the order of requirements, option 1 is the closest.",
                        tool_call_id=runtime.tool_call_id)]}
                    )
            message = "No itinerary respects the hard constraints. Rejected flights:\n" + "\n".join(rejected[:12])
            # The reasons are shown to the traveller as the code found them, above whatever the coordinator writes
            report = "\n".join(([no_flight_fits, ""] if no_flight_fits else [])
                               + [f"## {tr('no_itinerary')}", "", tr("no_itinerary_reasons"), ""] + [f"- {reason}" for reason in rejected])
            return Command(update={
                "step": 3, "report": report, "rejected": rejected, "warnings": warnings, "recommended": 0,
                "messages": [ToolMessage(message, tool_call_id=runtime.tool_call_id)]}
                )

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
    return Command(update={
        "step": 3,
        "report": report,
        "rejected": rejected + not_worth_it,
        "warnings": warnings,
        "recommended": number,
        "messages": [ToolMessage(f"{report}\n\nThe rules recommend option {number}.", tool_call_id=runtime.tool_call_id)]}
        )
