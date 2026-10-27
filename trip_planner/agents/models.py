"""The models the agents run on, and the one that takes over when they fail.

Gemini is the first choice everywhere. Mistral is plan B, from another company: it is only used when
MISTRAL_API_KEY is in env/.env, and only after the Gemini models failed. Without that key nothing changes."""
import asyncio
import os

from langchain.agents.middleware import ModelFallbackMiddleware, wrap_model_call
from langchain.chat_models import init_chat_model

from trip_planner.trip import trip_settings   # reads env/.env, where the keys are

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

def backup_models() -> list[str]:
    """The plan B model, when its key is there."""
    return [BACKUP_MODEL] if os.environ.get(BACKUP_KEY) else []

def coordinator_models() -> list[str]:
    """The models the coordinator tries, in order."""
    return [*GEMINI_COORDINATOR_MODELS, *backup_models()]

@wrap_model_call
async def give_up_slow_calls(request, handler):
    """A call to the model that has not answered in MODEL_CALL_SECONDS fails, as if the model had answered with an error."""
    return await asyncio.wait_for(handler(request), MODEL_CALL_SECONDS)

def subagent_guards() -> list:
    """The middlewares around a subagent's calls to its model: plan B when its own model fails, when it has a key, and
    the time limit inside it, so that each model tried gets its own MODEL_CALL_SECONDS."""
    fallback = [ModelFallbackMiddleware(*(init_chat_model(name, max_retries=2) for name in backup_models()))] if backup_models() else []
    return [*fallback, give_up_slow_calls]
