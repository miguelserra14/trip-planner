"""What each agent keeps between its steps."""
import operator
from typing import Annotated, Any, Dict, List

from langchain.agents import AgentState

class SearchState(AgentState):
    """State of a subagent that searches the web."""
    searches: Annotated[int, operator.add]   # web searches done so far: each search adds one
    search_limit: int                        # web searches allowed in this run

class TripState(AgentState):
    origin: str
    final_destination: str
    primary_airports: str
    fallback_airports: str
    arrival_airports: str
    travel_dates: str
    step: int                          # steps finished so far, it decides which tool the coordinator is offered
    flights: List[Dict[str, Any]]      # every flight found, copied from Kiwi by code
    flight_gaps: List[str]             # what may be missing from the flight list
    checked: List[str]                 # ids of the flights whose ground transport was looked up
    ground: Dict[str, Any]             # real ground transport of each checked flight, from the timetables
    transfers: List[Dict[str, Any]]    # what the airport transfer agent found, one per departure airport (unverified)
    trains: List[Dict[str, Any]]       # what the destination transport agent found, one per arrival airport (unverified)
    rejected: List[str]                # flights discarded by build_itineraries, with the reason
    warnings: List[str]                # integrity warnings for the traveller
    recommended: int                   # number of the recommended option in the report
    report: str                        # Markdown report built by build_itineraries
