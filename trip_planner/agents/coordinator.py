"""The coordinator: the model that writes the recommendation, and answers the questions about a trip.

It decides nothing about the search. The three steps run in code (steps.run_steps) and the report they produce
is shown to the traveller as it is. The coordinator is then called once, with the request and the results in
front of it, to say in words which option to take and why. When no model answers, the trip is not lost: the
report is already on the screen, and the planner says which option the rules recommend (planner.recommend)."""
import asyncio
import warnings
from typing import Any

from langchain.chat_models import init_chat_model
from langchain.messages import SystemMessage

from trip_planner import current
from trip_planner.agents import models, subagents
from trip_planner.config import LANGUAGES, LIGHTS
from trip_planner.texts import tr
from trip_planner.utils import message_text

# The Google client leaves this warning behind when a model answers with an error. The error itself is handled
# by write, so the warning only adds noise.
warnings.filterwarnings("ignore", message="coroutine 'ClientResponse.json' was never awaited")

# The models the coordinator tries, best first (models.py): the Gemini models, then plan B when it has a key.
COORDINATOR_MODELS = models.coordinator_models()

# A model that fails is not insisted with: the next one is asked at once, and the list is gone through again
# after a short pause. Insisting on one busy model cost over a minute; the others answer in a second or two.
coordinator_models = {name: init_chat_model(name, max_retries=0) for name in COORDINATOR_MODELS}
EXHAUSTED_MODELS: set = set()   # models out of daily quota, skipped for the rest of this session
ANSWER_SECONDS = 30             # a model that has not answered by then is given up, for this round
ROUNDS = 2                      # times the list of models is gone through
ROUND_WAIT_SECONDS = 3          # pause before the second round: what fails is usually busy for a moment

async def write(messages: list[Any]) -> str:
    """What the coordinator answers to a conversation about the trip being planned: the best model that answers.
    Raises when none does."""
    conversation = [SystemMessage(content=coordinator_prompt()), *messages]
    failure: Exception = RuntimeError("No coordinator model is available.")
    for attempt in range(ROUNDS):
        for name in COORDINATOR_MODELS:
            if name in EXHAUSTED_MODELS:
                continue
            try:
                answer = coordinator_models[name].ainvoke(conversation, config={"tags": ["TP"]})   # the tag finds the traces in LangSmith
                return message_text(await asyncio.wait_for(answer, ANSWER_SECONDS))
            except Exception as error:
                failure = error
                if "PerDay" in str(error):
                    EXHAUSTED_MODELS.add(name)   # the daily quota only comes back tomorrow
        if attempt < ROUNDS - 1:
            await asyncio.sleep(ROUND_WAIT_SECONDS)
    raise failure

def coordinator_prompt() -> str:
    s = current.SETTINGS
    return f"""
    You are a trip coordinator. The search is already done, by code, in three steps: every route was searched for
    flights, the real timetables and the operators' prices of the ground transport were read, and the itineraries
    were checked against the hard constraints, priced and ranked. The traveller's request and what each step found
    are in the first message. You cannot search or call any tool: you write.
    If no flight fits, the first step says which of the traveller's limits the existing flights break, counted by
    code. When it kept flights that break those limits, the other steps went on as usual.
    If no itinerary respects every requirement, the last step gives the closest ones instead, ordered by how little
    they break, with what speaks for and against each. Say plainly that nothing respects every requirement, then,
    in 3 or 4 sentences: which option is the closest and what it breaks, how it compares with the next one, and
    the setting and value that make it valid. Use only what the report says, never suggest a cause it does not
    mention, and do not use the heading below.
    If it gives no option at all, tell the traveller which single setting to relax, from the reasons it lists.

    The hard constraints of this trip, enforced by code:
    - {subagents.stopovers_rule(s)}
    - The traveller leaves {s.origin} between {s.earliest_leave} and {s.latest_leave}.
    - The traveller is at the airport at least {s.min_airport_minutes} minutes before the flight departs.
    - The traveller needs {s.min_landing_minutes} minutes to leave the arrival airport after landing.
    - The traveller reaches {s.destination} {s.arrival_window}.
    - Schedules must reach reliability grade {s.min_reliability} (A = confirmed for the travel date, B = typical timetable of
      the same weekday, C = not verified, found by a model in web search).

    The traveller's priorities, in this order:
    1. Lowest total cost. The traveller accepts sacrifices on everything else to save money, such as a more distant
       airport or a longer wait at the airport. A fallback airport is only worth it when it saves enough for the
       extra hours the trip takes: {s.extra_hours_table(s.fallback_saving_eur_per_hour)}.
    2. Shortest total door-to-door duration. Extra hours of travel cost the traveller more and more:
       {s.extra_hours_table(s.tiebreak_eur_per_hour)}. An itinerary is preferred when its price plus the cost of the
       hours it takes beyond the fastest itinerary is the lowest.
    The code already applied both rules and says which option they recommend. Recommend that same option.

    The report of the last step is shown to the traveller automatically, right above your answer.
    Do not repeat its tables or its steps, and never restate a time or a price differently from the report.
    Your first answer is in {LANGUAGES[s.language]}, formatted as Markdown, with only:
    - a level 2 heading "{tr('recommendation')}"
    - which option you recommend and why, in 2 to 4 short sentences, referring to the options by their number
    - how reliable that option is: say which of its parts are {LIGHTS['B']} or {LIGHTS['C']} and must be confirmed before booking.
      The traveller sees reliability as a colour, {LIGHTS['A']} {LIGHTS['B']} {LIGHTS['C']}: use those symbols and never the letters A, B or C
    - the main trade-off against the other options, in 1 or 2 sentences
    Do not use any other emojis.

    After that answer the traveller may ask follow-up questions about the trip:
    answer in a few sentences, only from the report and the results above, without the heading. Never state
    a time or a price that is not in them. You cannot search again, so never offer to. If the question needs a new
    search (other dates, airports or limits), name the setting in settings.py to change, such as `dates` or
    `latest_leave`, and say that the traveller must run the search again.
    """
