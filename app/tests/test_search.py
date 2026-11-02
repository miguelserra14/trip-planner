"""A search from the browser: the form and its token (fields.py, site.py), the conversation with the process
(search.py, job.py), keeping a report and the language. No network, and no model asked: the planner and the places
are stood in for, or the search is refused before it starts."""
import dataclasses
import io
import json
import subprocess
import sys
import time
from datetime import date, timedelta
from urllib.parse import urlencode

from app import fields, job, reports, search
from app.site import Site
from settings import SETTINGS
from trip_planner import utils
from trip_planner.texts import tr
from trip_planner.trip.ground_choice import NO_CHANGE_LIMIT

DAY = f"{date.today() + timedelta(days=30):%d/%m/%Y}"
AIRPORTS = {"groups": [{"title": "Departure", "airports": [{"code": "ANR", "km": 6, "name": "Antwerp", "city": "Antwerp", "country": "BE"},
                                                          {"code": "BRU", "km": 36, "name": "Brussels", "city": "Zaventem", "country": "BE"}]},
                       {"title": "Arrival", "airports": [{"code": "OPO", "km": 117, "name": "Porto", "city": "Porto", "country": "PT"}]}],
            "notes": []}

class StandIn(search.Job):
    """A search without a process: it asks what it is told to, and keeps what it is answered. The rest is Job's."""

    def __init__(self, first):
        self.first, self.values, self.lines, self.shown, self.reports, self.answers = first, first.get("settings") or {}, [], [], [], []
        self.failure, self.refusal, self.stopped, self.asked, self.alive, self.done = "", "", False, None, True, ""
        self.conversation = []

    running = property(lambda self: self.alive)
    working = property(lambda self: self.alive and self.asked is None)

    def tell(self, message):
        self.answers.append(message)

    def stop(self):
        self.alive, self.stopped = False, True

def site_and_searches():
    started = []
    site = Site(SETTINGS, start=lambda values: started.append(StandIn(values)) or started[-1])
    return site, started

def sent(site, **changes) -> bytes:
    """The form as it starts, sent as a browser sends it (no unticked box), with some fields changed."""
    values = fields.values_of(SETTINGS)
    form = {name: value for name, value in values.items() if not isinstance(value, bool)}
    form.update({name: "on" for name, value in values.items() if value is True})
    form.update({"departure_scale": "on"} if values["departure_scale"] == "wide" else {})
    form.update({"arrival_scale": "on"} if values["arrival_scale"] == "wide" else {})
    form.update({fields.DIRECT: "on"} if str(values["max_stopovers"]) == "0" else {})
    for name in ("departure_scale", "arrival_scale"):
        if values[name] != "wide":
            form.pop(name, None)
    form.pop("max_stopovers", None)
    form.update({"token": site.token, "origin": "Coimbra", "destination": "Antwerp", "dates": DAY, **changes})
    return urlencode({name: value for name, value in form.items() if value is not None}, doseq=True).encode()

def test_the_form_as_it_starts_gives_the_settings_of_settings_py():
    site, started = site_and_searches()
    assert site.post("/search", sent(site)).location == "/search"
    settings = job.settings_for(started[0].values, SETTINGS)
    expected = dataclasses.replace(SETTINGS, origin="Coimbra", destination="Antwerp", dates=DAY)
    assert vars(settings) == vars(expected)

def test_an_unticked_box_and_no_limit_of_changes_are_read_as_the_window_reads_them():
    read = fields.read({"max_changes_to_airport": "any", "max_changes_from_airport": "2", fields.DIRECT: "on"}, SETTINGS)
    assert read["max_changes_to_airport"] == NO_CHANGE_LIMIT and read["max_changes_from_airport"] == 2
    assert read["max_stopovers"] == 0 and read["confirm_airports"] is False and read["departure_scale"] == "near"

def test_a_request_without_the_token_of_the_page_changes_nothing():
    site, started = site_and_searches()
    for path in ("/search", "/stop", "/place", "/airports", "/ask", "/keep", "/language"):
        assert site.post(path, sent(site, token="another")).status == 403
        assert site.post(path, b"origin=Coimbra").status == 403
    assert started == [] and site.token in site.get("/").html

def test_a_setting_that_is_not_valid_is_answered_on_the_form_as_it_was_sent():
    site, started = site_and_searches()
    reply = site.post("/search", sent(site, destination="Coimbra", extra_wishes="a window seat"))
    assert reply.status == 400 and 'role="alert"' in reply.html and started == []
    assert 'name="destination" value="Coimbra"' in reply.html and 'value="a window seat"' in reply.html

def test_one_search_works_at_a_time_and_one_waiting_for_questions_gives_way():
    site, started = site_and_searches()
    site.post("/search", sent(site))
    assert site.post("/search", sent(site, destination="Ghent")).location == "/search" and len(started) == 1
    started[0].asked = ("ready", "")
    site.post("/search", sent(site, destination="Ghent"))
    assert len(started) == 2 and started[0].stopped and started[1].values["destination"] == "Ghent"

def test_the_traveller_chooses_the_place_and_the_airports_when_asked_and_only_then():
    site, started = site_and_searches()
    site.post("/search", sent(site))
    search_job = started[0]
    site.post("/place", sent(site, index="1"))
    assert search_job.answers == []   # nothing was asked yet
    search_job.asked = ("unclear", {"setting": "origin", "name": "Nazaré", "options": ["Nazaré, Leiria, Portugal", "Nazaré, Bahia, Brasil"]})
    page = site.get("/search").html
    assert "Nazaré, Bahia, Brasil" in page and 'action="/place"' in page and "data-working" not in page
    site.post("/place", sent(site, index="1"))
    search_job.asked = ("airports", AIRPORTS)
    assert site.get("/search/part").html.count('name="keep"') == 3
    site.post("/airports", urlencode({"token": site.token, "keep": ["ANR", "OPO"]}, doseq=True).encode())
    assert search_job.answers == [{"index": 1}, {"dropped": ["BRU"]}]

def test_a_question_is_passed_on_once_the_trip_is_planned():
    site, started = site_and_searches()
    site.post("/search", sent(site))
    site.post("/ask", sent(site, question="Is there an earlier train?"))
    assert started[0].answers == []
    started[0].asked = ("ready", "")
    assert 'action="/ask"' in site.get("/search").html
    assert site.post("/ask", sent(site, question="Is there an earlier train?")).location == "/search#conversation"
    assert started[0].answers == [{"question": "Is there an earlier train?"}]

def test_a_question_is_seen_at_once_with_its_answer_under_the_result_and_apart_from_it():
    site, started = site_and_searches()
    site.post("/search", sent(site))
    search_job = started[0]
    search_job.shown.append("# Final result")
    search_job.lines.append("Done in 217 s. Full report saved in reports/...")
    search_job.asked = ("ready", "")
    site.post("/ask", sent(site, question="Is there an earlier train?"))
    thinking = site.get("/search/part").html
    # Asked: the question is there, and the page says the model is thinking, not the last line of the search
    assert '<section id="conversation">' in thinking and "Is there an earlier train?" in thinking
    thinking_words = tr("ui_asking")   # in the trip's language, which the pages follow
    assert thinking.count(thinking_words) == 2 and "Done in 217 s" not in thinking.split("<details")[0]
    search_job.read_event("show", "**Is there an earlier train?**\n\nYes, at 06:10.")
    search_job.asked = ("ready", "")
    answered = site.get("/search/part").html
    result, conversation = answered.split('<section id="conversation">')
    assert "Yes, at 06:10." in conversation and "Yes, at 06:10." not in result and search_job.shown == ["# Final result"]
    assert 'action="/ask"' in conversation and thinking_words not in answered

def test_a_question_the_model_did_not_answer_says_so():
    site, started = site_and_searches()
    site.post("/search", sent(site))
    started[0].asked = ("ready", "")
    site.post("/ask", sent(site, question="Is there an earlier train?"))
    started[0].read_event("unanswered", "The model did not answer this question.")
    started[0].asked = ("ready", "")
    part = site.get("/search/part").html
    assert 'role="alert">The model did not answer this question.' in part and 'action="/ask"' in part

def test_the_part_of_a_working_search_is_marked_to_be_asked_again_until_it_waits_or_ends():
    site, started = site_and_searches()
    assert site.get("/search").location == "/"   # no search yet
    site.post("/search", sent(site))
    started[0].lines.append("Looking up the flights")
    part = site.get("/search/part").html
    assert part.startswith('<section id="live" data-working="1">') and "Looking up the flights" in part
    assert 'http-equiv="refresh"' in site.get("/search").html   # for a browser without the script
    started[0].alive = False
    assert "data-working" not in site.get("/search/part").html and 'http-equiv="refresh"' not in site.get("/search").html

def test_a_report_is_kept_and_put_back_with_its_folder(tmp_path):
    path = reports.REPORTS / "trip_coimbra_antwerp_2026-11-02_1310" / "trip_coimbra_antwerp_2026-11-02_1310.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Trip plan: Coimbra → Antwerp", encoding="utf-8")
    path.with_suffix(".log").write_text("log", encoding="utf-8")
    site, _ = site_and_searches()
    assert 'name="keep" value="1"' in site.get("/report/recent/trip_coimbra_antwerp_2026-11-02_1310").html
    kept = site.post("/keep", sent(site, report="recent/trip_coimbra_antwerp_2026-11-02_1310", keep="1"))
    assert kept.location == "/report/saved/trip_coimbra_antwerp_2026-11-02_1310"
    assert (reports.SAVED_REPORTS / path.parent.name / "trip_coimbra_antwerp_2026-11-02_1310.log").exists() and not path.exists()
    assert 'name="keep" value="0"' in site.get(kept.location).html
    back = site.post("/keep", sent(site, report="saved/trip_coimbra_antwerp_2026-11-02_1310", keep="0"))
    assert back.location == "/report/recent/trip_coimbra_antwerp_2026-11-02_1310" and path.exists()
    assert site.post("/keep", sent(site, report="../settings", keep="1")).status == 404

def test_the_pages_follow_the_language_chosen():
    site, _ = site_and_searches()
    site.post("/language", sent(site, language="pt-PT"))
    assert 'lang="pt-PT"' in site.get("/").html and "Planear uma viagem" in site.get("/").html
    site.post("/language", sent(site, language="xx"))
    assert 'lang="pt-PT"' in site.get("/").html

def test_the_only_script_is_the_apps_own_file():
    site, _ = site_and_searches()
    script = site.get("/page.js")
    assert script.status == 200 and script.kind.startswith("text/javascript") and "innerHTML" not in script.html
    home = site.get("/").html
    assert home.count("<script") == 1 and '<script src="/page.js" defer></script>' in home

# A process that says where it is, asks which place, says back the answer it was given, and ends
TALKING = """import json, sys
say = lambda kind, text: print(json.dumps({"kind": kind, "text": text}), flush=True)
sys.stdin.readline()
say("say", "Locating")
say("unclear", {"setting": "origin", "name": "Nazaré", "options": ["a", "b"]})
say("say", sys.stdin.readline().strip())
print("a warning of a library")
"""

def wait_until(condition, seconds: float = 20) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False

def test_the_app_and_the_process_talk_line_by_line():
    found = search.Job({"origin": "Nazaré"}, [sys.executable, "-c", TALKING])
    assert wait_until(lambda: found.waiting("unclear"))
    assert found.lines == ["Locating"] and not found.working
    found.answer({"index": 1})
    assert wait_until(lambda: not found.running)
    assert found.lines == ["Locating", '{"index": 1}', "a warning of a library"] and found.failure == ""

def test_a_process_that_ends_badly_without_saying_why_is_a_failure():
    found = search.Job({}, [sys.executable, "-c", "import sys; sys.exit(3)"])
    assert wait_until(lambda: not found.running) and "3" in found.failure

def test_a_stopped_search_is_not_a_failure():
    found = search.Job({}, [sys.executable, "-c", "import time; time.sleep(60)"])
    found.stop()
    assert found.stopped and not found.running and found.failure == ""

def test_the_process_refuses_a_trip_that_is_not_valid_before_it_searches():
    asked = json.dumps({"settings": {"origin": "Coimbra", "destination": "Antwerp", "dates": "01/01/2020"}}) + "\n"
    done = subprocess.run(search.COMMAND, cwd=search.ROOT, input=asked, capture_output=True, encoding="utf-8", timeout=60)
    events = [json.loads(line) for line in done.stdout.splitlines()]
    assert done.returncode == 1 and [event["kind"] for event in events] == ["failed"] and "2020" in events[0]["text"]

def test_the_process_asks_plans_reports_and_answers(monkeypatch, tmp_path):
    from trip_planner import planner
    from trip_planner.sources import flight_search
    from trip_planner.trip import places as where

    chosen, refusals = [], ["Every departure airport or every arrival airport was removed."]
    place = where.Place("Nazaré", "Nazaré", "Nazaré", "Portugal", 39.6, -9.07)
    found = where.TripPlaces(place, place, [{"code": "LIS", "km": 100, "name": "Lisbon", "city": "Lisbon", "country": "PT"}], [],
                             [{"code": "ANR", "km": 6, "name": "Antwerp", "city": "Antwerp", "country": "BE"}])

    def remove_airports(places, removed):
        if refusals:
            raise ValueError(refusals.pop())
        return places

    async def plan_trip(settings, places):
        assert places is found and settings.confirm_airports
        utils.say("Looking up the flights")
        utils.show("# Final result")
        print("printed by a library")
        planner.PLANNED.append({"report": tmp_path / "trip.md"})

    async def ask(question):
        if question != "Silent?":   # the model does not answer that one: planner.ask only says why
            utils.show(f"**{question}**")

    async def connect():
        pass
    monkeypatch.setattr(where, "unclear_places", lambda settings: [] if chosen else [("origin", "Nazaré", [("Nazaré, Leiria", place), ("Nazaré, Bahia", place)])])
    monkeypatch.setattr(where, "choose_place", lambda name, meant: chosen.append(name))
    monkeypatch.setattr(where, "choose_airports", lambda settings: found)
    monkeypatch.setattr(where, "remove_airports", remove_airports)
    monkeypatch.setattr(utils, "LISTENERS", {})
    monkeypatch.setattr(planner, "PLANNED", [])
    monkeypatch.setattr(planner, "plan_trip", plan_trip)
    monkeypatch.setattr(planner, "ask", ask)
    monkeypatch.setattr(flight_search, "connect", connect)
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    settings = fields.read({"origin": "Nazaré", "destination": "Antwerp", "dates": DAY, "confirm_airports": "on"}, SETTINGS)
    answers = [json.dumps({"settings": settings}), '{"index": 1}', '{"dropped": ["LIS", "ANR"]}', '{"dropped": []}',
               '{"question": "Earlier?"}', '{"question": "Silent?"}']
    assert job.main(iter(answers), events) == 0
    said = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [event["kind"] for event in said] == ["unclear", "airports", "refused", "airports", "say", "show", "say", "report",
                                                 "ready", "show", "ready", "unanswered", "ready"]
    assert said[0]["text"]["options"] == ["Nazaré, Leiria", "Nazaré, Bahia"] and chosen == ["Nazaré"]
    assert [airport["code"] for group in said[1]["text"]["groups"] for airport in group["airports"]] == ["LIS", "ANR"]
    assert said[6]["text"] == "printed by a library" and said[9]["text"] == "**Earlier?**"
