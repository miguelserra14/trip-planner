"""The models the agents run on, and the one that takes over when they fail.

Gemini is the first choice everywhere. Mistral is plan B, from another company: it is only used when
MISTRAL_API_KEY is in env/.env, and only after the Gemini models failed. Without that key nothing changes.

Both can be asked through a Cloudflare AI Gateway, when AI_GATEWAY_URL is in env/.env: the gateway keeps a log of
every call with its tokens and time, can answer a request it has seen from its cache, and can limit the requests
of a day. Plan B stays here in code: the gateway's own fallback needs requests in its Universal or OpenAI format,
which the clients of LangChain do not send. Without that address the providers are asked directly."""
import asyncio
import json
import os

import httpx
from langchain.agents.middleware import ModelFallbackMiddleware, wrap_model_call
from langchain.chat_models import init_chat_model

from trip_planner.trip import trip_settings   # reads env/.env, where the keys are
from trip_planner.utils import logged

SUBAGENT_MODEL = "google_genai:gemini-3.1-flash-lite"
# Coordinator models, best first. The free tier gives the better models very few requests per day
# (20 for gemini-3.8-flash), so the coordinator uses the best one that still has quota
# and only falls back to flash-lite when the others are exhausted.
GEMINI_COORDINATOR_MODELS = [
    #"google_genai:gemini-3.1-pro-preview",    this one is too slow
    "google_genai:gemini-3.8-flash",
    "google_genai:gemini-3.1-flash-lite",
]
# Free tier: https://console.mistral.ai. Of the models that tier serves (read on 2026-10-14: the small, medium and
# large ones answer "rate limit 0" or "not in your tier"), the most capable one that calls tools.
BACKUP_MODEL = "mistralai:ministral-14b-latest"
BACKUP_KEY = "MISTRAL_API_KEY"
# A subagent's call to its model, retries of the client included, is given up after this long: one call to Gemini
# took 26 minutes on 2026-10-26 and held the whole search. Then plan B is asked, with the same limit.
MODEL_CALL_SECONDS = 240
# Cloudflare AI Gateway, optional: https://gateway.ai.cloudflare.com/v1/<account id>/<gateway name>
GATEWAY_KEY = "AI_GATEWAY_URL"
GATEWAY_TOKEN_KEY = "AI_GATEWAY_TOKEN"   # only for a gateway set to authenticated
# Where each provider is behind the gateway, by the prefix of the model's name (read on 2026-11-17 in its docs)
GATEWAY_PATHS = {"google_genai": "google-ai-studio", "mistralai": "mistral/v1"}
# An answer kept in the gateway's cache, when the cache is turned on there, is given again for at most this long: a
# price a model found is no longer believed after a day, as an operator's (tools/prices.PRICE_VALID_HOURS)
CACHE_SECONDS = 24 * 3600
MISTRAL_SECONDS = 120   # the time limit of the Mistral client, as it sets it when it makes its own

def gateway_options(name: str, part: str) -> dict:
    """What a model's client needs to be asked through the gateway, or nothing without AI_GATEWAY_URL or for a
    provider the gateway is not set up for here. part names, in the gateway's log, the part of the program asking."""
    url = os.environ.get(GATEWAY_KEY, "").strip().rstrip("/")
    provider = name.split(":", 1)[0]
    if not url or provider not in GATEWAY_PATHS:
        return {}
    base = f"{url}/{GATEWAY_PATHS[provider]}"
    headers = {"cf-aig-metadata": json.dumps({"app": "trip-planner", "part": part}), "cf-aig-cache-ttl": str(CACHE_SECONDS)}
    if token := os.environ.get(GATEWAY_TOKEN_KEY, "").strip():
        headers["cf-aig-authorization"] = f"Bearer {token}"
    if provider == "google_genai":
        return {"base_url": base, "additional_headers": headers}
    # The Mistral client takes no headers of its own: it is given its clients, made as it makes them
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "Authorization": f"Bearer {os.environ.get(BACKUP_KEY, '')}", **headers}
    return {"base_url": base, "client": httpx.Client(base_url=base, headers=headers, timeout=MISTRAL_SECONDS),
            "async_client": httpx.AsyncClient(base_url=base, headers=headers, timeout=MISTRAL_SECONDS)}

def chat_model(name: str, part: str, **options):
    """A chat model by its name, as init_chat_model makes it, through the gateway when there is one."""
    return init_chat_model(name, **options, **gateway_options(name, part))

def backup_models() -> list[str]:
    """The plan B model, when its key is there."""
    return [BACKUP_MODEL] if os.environ.get(BACKUP_KEY) else []

def coordinator_models() -> list[str]:
    """The models the coordinator tries, in order."""
    return [*GEMINI_COORDINATOR_MODELS, *backup_models()]

CALL_SERVICE = "model call"   # each call of a subagent to a model, in the log of the run: plan B shows there by its name

def model_name(model) -> str:
    """The name of a chat model as its provider calls it ("gemini-3.1-flash-lite", "ministral-14b-latest")."""
    return str(getattr(model, "model", None) or getattr(model, "model_name", None) or type(model).__name__).removeprefix("models/")

@wrap_model_call
async def give_up_slow_calls(request, handler):
    """A call to the model that has not answered in MODEL_CALL_SECONDS fails, as if the model had answered with an error.
    Each call is kept in the log of the run with the model it went to: inside the plan B, it sees each model tried."""
    with logged(CALL_SERVICE, model_name(request.model)):
        return await asyncio.wait_for(handler(request), MODEL_CALL_SECONDS)

def is_backup(name: str) -> bool:
    """Whether a model named in the log of the run is plan B."""
    return BACKUP_MODEL.split(":", 1)[1] in name

def subagent_guards() -> list:
    """The middlewares around a subagent's calls to its model: plan B when its own model fails, when it has a key, and
    the time limit inside it, so that each model tried gets its own MODEL_CALL_SECONDS."""
    fallback = [ModelFallbackMiddleware(*(chat_model(name, "plan_b", max_retries=2) for name in backup_models()))] if backup_models() else []
    return [*fallback, give_up_slow_calls]
