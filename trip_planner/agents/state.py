"""What each agent keeps between its steps."""
import operator
from typing import Annotated

from langchain.agents import AgentState

class SearchState(AgentState):
    """State of a subagent that searches the web."""
    searches: Annotated[int, operator.add]   # web searches done so far: each search adds one
    search_limit: int                        # web searches allowed in this run
