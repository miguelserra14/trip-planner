"""Web search for the subagents (Tavily), and the protections for what models bring back from the web."""
import json
import re
import time
from urllib.parse import urlparse

from langchain.messages import ToolMessage
from langchain.tools import tool, ToolRuntime
from langgraph.types import Command
from tavily import TavilyClient

from trip_planner.utils import count, logged

tavily_client: TavilyClient | None = None   # made at the first search: without its key the program still starts

def search(query: str, **options) -> dict:
    """One web search on Tavily. Raises when its key is missing or the service does not answer."""
    global tavily_client
    if tavily_client is None:
        tavily_client = TavilyClient()
    with logged("Tavily", query[:120]):
        return tavily_client.search(query, **options)

AVAILABLE_FOR_SECONDS = 600
last_check = (0.0, False)   # when the web search was last tried, and whether it answered

def available() -> bool:
    """Whether the web search answers: one small search, remembered for ten minutes. When it does not, the
    program goes on without it (see search_ground_transport)."""
    global last_check
    if time.time() - last_check[0] > AVAILABLE_FOR_SECONDS:
        try:
            search("train timetable", max_results=1)
            last_check = (time.time(), True)
        except Exception:
            last_check = (time.time(), False)
    return last_check[1]

# Web pages are untrusted text. Two protections for what the models bring back from them:
# - a link written by a model is only shown if its site really appeared in a search result (SEEN_DOMAINS)
# - text written by a model is cleaned and shortened before it reaches the report (clean_text)
SEEN_DOMAINS: set = set()
MAX_MODEL_TEXT = 220

def domain_of(url: str) -> str:
    return (urlparse(url or "").netloc or "").lower().removeprefix("www.")

def clean_text(text: str, limit: int = MAX_MODEL_TEXT) -> str:
    """Text written by a model, made safe to show: no links, no markup, one line, limited length."""
    text = re.sub(r"https?://\S+|www\.\S+", "", text or "")       # links
    text = re.sub(r"<[^>]*>|[`*_#\[\]|<>]", "", text)             # HTML and Markdown markup
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."

def safe_url(url: str) -> str:
    """A link written by a model, kept only if it is https and its site appeared in a search result."""
    url = (url or "").strip()
    if not url.lower().startswith("https://") or domain_of(url) not in SEEN_DOMAINS:
        count("links_dropped", bool(url))
        return ""
    return url.split()[0]

def secure_url(url: str) -> str:
    """A link from a data source, always opened over https. Empty if it is not a web link."""
    url = (url or "").strip()
    if url.lower().startswith("http://"):
        url = "https://" + url[7:]
    return url if url.lower().startswith("https://") else ""

LINK = r"https?://[^\s)\]>,;]+"

def only_known_links(text: str, known: str) -> str:
    """Text written by a model, without the links to sites that do not appear in known (the report computed
    by code). A model may have been told by a web page to send the traveller somewhere else."""
    sites = {domain_of(url) for url in re.findall(LINK, known)}
    count("links_dropped", sum(domain_of(url) not in sites for url in re.findall(LINK, text or "")))
    return re.sub(LINK, lambda link: link[0] if domain_of(link[0]) in sites else "", text or "")

@tool
def web_search(query: str, runtime: ToolRuntime) -> Command:
    """Search the web for information. The number of searches is limited and counted for you:
    when the limit is reached this tool is no longer available.
    Queries must use only plain text characters. Do not use accented or special characters
      (e.g., use 'Antuerpia' instead of 'Antuérpia').
    """
    # The searches are counted here, in the state (SearchState), never by the model. Several searches asked
    # for in the same turn all see the same count, so each one adds its position in that turn.
    state = runtime.state
    same_turn = [call["id"] for call in getattr(state["messages"][-1], "tool_calls", None) or [] if call["name"] == "web_search"]
    position = same_turn.index(runtime.tool_call_id) if runtime.tool_call_id in same_turn else 0
    if (state.get("searches") or 0) + position >= (state.get("search_limit") or 0):
        answer, searched = {"message": "Search limit reached. Give your final answer with what you have found."}, 0
    else:
        searched = 1   # a failed search counts too, or a failing service would be asked forever
        try:
            results = search(query).get("results", [])
            SEEN_DOMAINS.update(domain_of(result.get("url", "")) for result in results)
            answer = {"query": query, "results": [
                {"title": result.get("title", ""), "url": result.get("url", ""), "content": result.get("content", "")}
                for result in results]}
        except Exception as e:
            answer = {"error": str(e)}
    return Command(update={
        "searches": searched,
        "messages": [ToolMessage(json.dumps(answer, ensure_ascii=False), name="web_search", tool_call_id=runtime.tool_call_id)]}
        )
