"""The program's own jobs in the app, its buttons: the ground prices asked again, the check of
the services, a language added (job.py runs them, as a search) or removed (the app does it, once confirmed).
No network and no model: the jobs are stood in for, and a language is removed from a copy of the folder."""
import io
import json
import shutil
import sys

import pytest

from app import job, search
from app.site import Site
from app.tests.test_search import StandIn, sent
from settings import SETTINGS
from trip_planner import texts
from trip_planner.texts import translate

def site_with(stale=()):
    started = []
    site = Site(SETTINGS, start=lambda first: started.append(StandIn(first)) or started[-1], stale=lambda: list(stale))
    return site, started

def test_the_prices_can_only_be_asked_again_when_some_are_too_old():
    site, started = site_with()
    assert '<button type="submit" class="quiet" disabled>Refresh prices</button>' in site.get("/").html
    site, started = site_with(stale=["a.json", "b.json"])
    assert "Refresh prices (2)" in site.get("/").html
    assert site.post("/prices", sent(site)).location == "/search"
    assert started[0].first["task"] == "prices" and "Refresh prices" in site.get("/search").html

def test_the_check_runs_as_a_search_does_and_not_while_one_works():
    site, started = site_with()
    site.post("/search", sent(site))
    assert site.post("/check", sent(site)).location == "/search" and len(started) == 1   # the search goes on
    started[0].alive = False
    site.post("/check", sent(site))
    assert started[1].first == {"task": "check", "language": SETTINGS.language}   # the pages followed the trip's language
    site.post("/language", sent(site, language="en"))
    assert "Working..." in site.get("/search/part").html and "Searching" not in site.get("/search/part").html
    started[1].alive, started[1].done = False, "Every service answered."
    assert "Every service answered." in site.get("/search/part").html

def test_a_language_added_is_read_again_once_its_job_ended(monkeypatch):
    read = []
    monkeypatch.setattr(translate, "refresh", lambda: read.append(True))
    site, started = site_with()
    site.post("/language/add", sent(site, name="italiano"))
    assert started[0].first == {"task": "language", "name": "italiano", "language": "en"}
    site.get("/search")
    assert read == []   # still translating
    started[0].alive = False
    site.get("/languages")
    site.get("/languages")
    assert read == [True]

@pytest.fixture
def languages(tmp_path, monkeypatch):
    """A copy of the folder of languages, with a third language in it, and a settings.py that asks for it."""
    folder = tmp_path / "texts" / "languages"
    folder.mkdir(parents=True)
    for code in ("en", "pt-PT"):
        shutil.copy(texts.FOLDER / f"{code}.py", folder)
    (folder / "it.py").write_text('TEXTS = {"language_name": "italiano", "language_english_name": "Italian"}\n', encoding="utf-8")
    (tmp_path / "settings.py").write_text('language="it"\n', encoding="utf-8")
    for module in (texts, translate):
        monkeypatch.setattr(module, "FOLDER", folder)
    texts.read_languages()
    yield folder
    monkeypatch.undo()
    texts.read_languages()   # the program's own languages again

def test_a_language_is_removed_only_once_confirmed_and_never_the_reference(languages):
    site, _ = site_with()
    asking = site.post("/language/remove", sent(site, code="it"))
    assert asking.status == 200 and "Remove italiano?" in asking.html and (languages / "it.py").exists()
    site.post("/language/remove", sent(site, code="en", confirm="1"))
    assert (languages / "en.py").exists()
    removed = site.post("/language/remove", sent(site, code="it", confirm="1"))
    assert not (languages / "it.py").exists() and "it" not in texts.LANGUAGES
    assert "italiano was removed." in removed.html and "still asks for the language it" in removed.html

def test_the_language_of_the_pages_falls_back_to_english_when_it_is_removed(languages):
    site, _ = site_with()
    site.post("/language", sent(site, language="it"))
    site.post("/language/remove", sent(site, code="it", confirm="1"))
    assert 'lang="en"' in site.get("/").html and site.values["language"] == "en"

def run(monkeypatch, asked: dict) -> list[dict]:
    from trip_planner import utils
    monkeypatch.setattr(utils, "LISTENERS", {})
    out = io.StringIO()
    events = job.Events(out)
    monkeypatch.setattr(sys, "stdout", job.Printed(events))
    job.main(iter([json.dumps(asked)]), events)
    return [json.loads(line) for line in out.getvalue().splitlines()]

def test_the_process_runs_each_job_and_says_how_it_ended(monkeypatch):
    from trip_planner.tools import check, prices

    async def check_setup():
        print("Kiwi        ok")
        return 1
    monkeypatch.setattr(check, "check_setup", check_setup)
    monkeypatch.setattr(prices, "refresh", lambda: "## Ground prices asked again")
    said = run(monkeypatch, {"task": "check", "language": "en"})
    assert [event["kind"] for event in said] == ["say", "done"] and "did not answer" in said[1]["text"]
    said = run(monkeypatch, {"task": "prices", "language": "en"})
    assert [(event["kind"], event["text"]) for event in said] == [("show", "## Ground prices asked again"), ("done", "Done.")]

def test_a_language_the_model_does_not_know_is_said_in_the_travellers_words(monkeypatch):
    async def add(name):
        raise translate.UnknownLanguage(name)
    monkeypatch.setattr(translate, "add", add)
    said = run(monkeypatch, {"task": "language", "name": "klingon", "language": "pt-PT"})
    assert said == [{"kind": "failed", "text": texts.text_in("pt-PT", "ui_language_unknown", name="klingon")}]

def test_the_app_reads_how_a_job_ended():
    script = 'import json, sys; sys.stdin.readline(); print(json.dumps({"kind": "done", "text": "Done."}))'
    found = search.Job({"task": "check"}, [sys.executable, "-c", script])
    found.reader.join(timeout=20)
    assert found.done == "Done." and found.failure == "" and found.values == {}
