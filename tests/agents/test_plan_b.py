"""Plan B for the two services the agents depend on. No network.

- The models: Mistral takes over when the Gemini models fail, and only when its key is there.
- The web search: when Tavily does not answer, the timetables are read first and the specialists are only asked
  about the airports no timetable covers, without searching."""
import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from trip_planner import current
from trip_planner.agents import models, steps
from trip_planner.places import Place, TripPlaces
from trip_planner.sources import web

# ---------------------------------------------------------------- the models
def test_without_its_key_plan_b_changes_nothing(monkeypatch):
    monkeypatch.delenv(models.BACKUP_KEY, raising=False)
    assert models.backup_models() == [] and models.subagent_fallback() == []
    assert models.coordinator_models() == models.GEMINI_COORDINATOR_MODELS

def test_with_its_key_mistral_is_tried_last(monkeypatch):
    monkeypatch.setenv(models.BACKUP_KEY, "a key")
    names = models.coordinator_models()
    assert names == [*models.GEMINI_COORDINATOR_MODELS, models.BACKUP_MODEL]
    assert len(models.subagent_fallback()) == 1

def test_a_subagent_that_fails_is_answered_by_plan_b():
    """The fallback of the agent library, as the subagents use it: the first model fails, the second answers."""
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelFallbackMiddleware

    class Failing(GenericFakeChatModel):
        def _generate(self, *args, **kwargs):
            raise RuntimeError("503 UNAVAILABLE")

    backup = GenericFakeChatModel(messages=iter([AIMessage(content="answered by plan B")]))
    agent = create_agent(model=Failing(messages=iter([])), tools=[], middleware=[ModelFallbackMiddleware(backup)])
    answer = asyncio.run(agent.ainvoke({"messages": [("user", "hello")]}))
    assert answer["messages"][-1].content == "answered by plan B"

# ---------------------------------------------------------------- the web search
@pytest.fixture
def no_web(monkeypatch):
    def refuse(query, **options):
        raise RuntimeError("the web search does not answer")
    monkeypatch.setattr(web, "search", refuse)
    monkeypatch.setattr(web, "last_check", (0.0, False))

def test_a_search_that_fails_is_remembered(no_web, monkeypatch):
    assert web.available() is False
    monkeypatch.setattr(web, "search", lambda query, **options: {"results": []})
    assert web.available() is False        # not asked again for ten minutes
    monkeypatch.setattr(web, "last_check", (0.0, False))
    assert web.available() is True

def test_only_the_airports_no_timetable_covers():
    flights = [{"id": "a", "departure_airport": "OPO", "arrival_airport": "BRU"},
               {"id": "b", "departure_airport": "OPO", "arrival_airport": "MLA"},
               {"id": "c", "departure_airport": "LIS", "arrival_airport": "BRU"}]
    side = lambda covered: {"covered": covered, "options": []}
    ground = {"a": {"to_airport": side(True), "from_airport": side(True)},
              "b": {"to_airport": side(True), "from_airport": side(False)},
              "c": {"to_airport": side(False), "from_airport": side(True)}}
    assert steps.not_covered(flights, ground, "to_airport") == {"LIS"}
    assert steps.not_covered(flights, ground, "from_airport") == {"MLA"}

def test_a_specialist_with_no_airport_to_look_at_is_not_asked(monkeypatch):
    place = Place("a", "a", "a", "", 0.0, 0.0)
    airport = lambda code: {"code": code, "name": code, "city": code, "country": "", "km": 10}
    monkeypatch.setattr(current, "TRIP", TripPlaces(place, place, [airport("OPO")], [], [airport("BRU")]))
    flights = [{"id": "a", "date": "2030-01-01", "departure_airport": "OPO", "arrival_airport": "BRU"}]

    class Unreachable:
        async def ainvoke(self, *args, **kwargs):
            raise AssertionError("the specialist was asked")
    monkeypatch.setattr(steps.subagents, "airport_transfer_agent", Unreachable())
    monkeypatch.setattr(steps.subagents, "destination_transport_agent", Unreachable())
    assert asyncio.run(steps.ask_transfer_agent(flights, only=set(), searches=0)) == []
    assert asyncio.run(steps.ask_destination_agent(flights, only=set(), searches=0)) == []
