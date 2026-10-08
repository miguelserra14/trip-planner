"""Web search for the subagents (Tavily), and the protections for what models bring back from the web."""
import json
import re
from urllib.parse import urlparse

from langchain.messages import ToolMessage
from langchain.tools import tool, ToolRuntime
from langgraph.types import Command
from tavily import TavilyClient

tavily_client = TavilyClient()

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
        return ""
    return url.split()[0]

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
            results = tavily_client.search(query).get("results", [])
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
