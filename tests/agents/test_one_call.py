"""The steps of a trip run in code, and the model is called once, at the end. No network.

- The steps run in order, and a run that failed continues from the step that failed.
- The coordinator is the best model that answers.
- When no model answers, the traveller still gets the option the rules chose."""
import asyncio

import pytest
from langchain.messages import HumanMessage

from trip_planner import planner
from trip_planner.agents import coordinator, steps

# ---------------------------------------------------------------- the steps
def test_the_steps_run_in_order_and_leave_their_notes(monkeypatch):
    async def first(state):
        return {"step": 1, "flights": ["a flight"], "message": "one flight"}
    def second(state):   # a step may be a plain function: it then runs on a thread
        return {"step": 2, "seen": list(state["flights"]), "message": "its transport"}
    monkeypatch.setattr(steps, "STEPS", [first, second])
    state = asyncio.run(steps.run_steps({"origin": "A"}))
    assert state["step"] == 2 and state["seen"] == ["a flight"] and state["origin"] == "A"
    assert state["notes"] == ["one flight", "its transport"] and "message" not in state

def test_a_run_that_failed_continues_from_the_step_that_failed(monkeypatch):
    calls = []
    async def first(state):
        calls.append("first")
        return {"step": 1, "message": "done"}
    async def second(state):
        calls.append("second")
        if calls.count("second") == 1:
            raise RuntimeError("a service is down")
        return {"step": 2, "message": "done too"}
    monkeypatch.setattr(steps, "STEPS", [first, second])
    monkeypatch.setattr(planner, "STEPS", steps.STEPS)
    monkeypatch.setattr(planner, "RESUME_WAIT_SECONDS", 0)
    state = asyncio.run(planner.run_search({}))
    assert calls == ["first", "second", "second"] and state["step"] == 2   # the first step was not run again

def test_a_step_that_ends_the_trip_skips_the_others(monkeypatch):
    async def no_flights(state):
        return {"step": 2, "report": "no flight fits", "message": "nothing"}
    async def never(state):
        raise AssertionError("there was nothing to look up")
    monkeypatch.setattr(steps, "STEPS", [no_flights, never])
    assert asyncio.run(steps.run_steps({}))["report"] == "no flight fits"

# ---------------------------------------------------------------- the model
class Model:
    def __init__(self, answer: str | None):
        self.answer, self.asked = answer, []

    async def ainvoke(self, conversation, config=None):
        self.asked.append(conversation)
        if self.answer is None:
            raise RuntimeError("429 quota exceeded")
        return HumanMessage(content=self.answer)

@pytest.fixture
def two_models(monkeypatch):
    def use(first: str | None, second: str | None) -> tuple:
        found = {"first": Model(first), "second": Model(second)}
        monkeypatch.setattr(coordinator, "COORDINATOR_MODELS", list(found))
        monkeypatch.setattr(coordinator, "coordinator_models", found)
        monkeypatch.setattr(coordinator, "EXHAUSTED_MODELS", set())
        return found["first"], found["second"]
    return use

def test_the_next_model_answers_when_the_first_fails(two_models):
    first, second = two_models(None, "## Recommendation\n\nOption 2.")
    answer = asyncio.run(coordinator.write([HumanMessage(content="the results")]))
    assert answer.endswith("Option 2.") and len(first.asked) == len(second.asked) == 1
    system, results = second.asked[0]
    assert "You cannot search or call any tool" in system.content and results.content == "the results"

def test_the_recommendation_is_kept_for_the_questions_that_follow(two_models):
    two_models("## Recommendation\n\nOption 1 is the cheapest.", None)
    messages = [HumanMessage(content="the results")]
    text = asyncio.run(planner.recommend({"recommended": 1, "report": "the report"}, messages))
    assert "Option 1" in text and len(messages) == 2 and messages[-1].content == text

def test_without_any_model_the_rules_still_name_their_option(two_models):
    two_models(None, None)
    messages = [HumanMessage(content="the results")]
    text = asyncio.run(planner.recommend({"recommended": 3, "report": "the report"}, messages))
    assert "3" in text and text.startswith("## ") and len(messages) == 1   # nothing a model said was added

def test_without_a_model_and_without_an_option_nothing_is_added(two_models):
    two_models(None, None)
    assert asyncio.run(planner.recommend({"recommended": 0, "report": "nothing fits"}, [HumanMessage(content="x")])) == ""

def test_the_model_reads_the_request_and_what_each_step_found():
    text = planner.results_for_model("I want to go to Antwerp.", {"notes": ["8 flights", "timetables cover 6 of 8 legs", "the report"]})
    assert text.startswith("I want to go to Antwerp.") and "Step 1, flight search:\n8 flights" in text and "Step 3, itineraries:\nthe report" in text
