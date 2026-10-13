"""What each agent keeps between its steps."""
import operator
from typing import Annotated, Any

from langchain.agents import AgentState

class SearchState(AgentState):
    """State of a subagent that searches the web."""
    searches: Annotated[int, operator.add]   # web searches done so far: each search adds one
    search_limit: int                        # web searches allowed in this run

class TripState(AgentState):
    """What a trip gathers as its steps run (steps.run_steps): a plain dictionary with these keys."""
    origin: str
    final_destination: str
    primary_airports: str
    fallback_airports: str
    arrival_airports: str
    travel_dates: str
    step: int                          # steps finished so far: run_steps continues from here
    notes: list[str]                   # what each finished step found, in words, for the model that writes the recommendation
    flights: list[dict[str, Any]]      # every flight found, copied from Kiwi by code
    flight_gaps: list[str]             # what may be missing from the flight list
    checked: list[str]                 # ids of the flights whose ground transport was looked up
    stopped: str                       # why no more flights were looked up, see selection.next_flights
    ground: dict[str, Any]             # real ground transport of each checked flight, from the timetables
    transfers: list[dict[str, Any]]    # what the airport transfer agent found, one per departure airport (unverified)
    trains: list[dict[str, Any]]       # what the destination transport agent found, one per arrival airport (unverified)
    rejected: list[str]                # flights discarded by build_itineraries, with the reason
    warnings: list[str]                # integrity warnings for the traveller
    recommended: int                   # number of the recommended option in the report
    report: str                        # Markdown report built by build_itineraries
