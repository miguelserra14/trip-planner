"""The map and its form in the traveller's language (texts/languages, discover_...): every text the page's script
asks for exists, English and Portuguese have the same texts with the same {values}, and the code writes them. No network."""
import dataclasses
import re

from discover.view import page
from discover.flights.sources import Days
from trip_planner import current
from trip_planner.texts import TEXTS

def test_page_texts_exist():
    script = page.SCRIPT.read_text(encoding="utf-8")
    asked = set(re.findall(r'\bt\(\s*"([a-z_]+)"', script)) | {key for pair in re.findall(r'\?\s*"([a-z_]+)"\s*:\s*"([a-z_]+)"\)', script) for key in pair}
    assert asked and asked <= set(page.page_texts())

def test_discover_languages_match():
    values = lambda text: sorted(set(re.findall(r"\{(\w+)\}", text)))
    english = {key: text for key, text in TEXTS["en"].items() if key.startswith("discover_")}
    portuguese = {key: text for key, text in TEXTS["pt-PT"].items() if key.startswith("discover_")}
    assert set(english) == set(portuguese)
    assert [key for key in english if values(english[key]) != values(portuguese[key])] == []

def test_page_in_traveller_language(monkeypatch):
    monkeypatch.setattr(current, "SETTINGS", dataclasses.replace(current.SETTINGS, language="pt-PT"))
    assert str(Days("2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17")) == "2026-11-12 a 2026-11-13, volta 2026-11-16 a 2026-11-17"
    texts = page.page_texts()
    assert texts["still_searching"] == "Ainda a pesquisar." and texts["when"] == "{day} às {time}"

def test_no_duplicate_texts():
    # A key written twice is not an error in Python: the second one wins, silently. "Back" became "← Discover" so.
    from trip_planner.texts import FOLDER
    for name in ("en", "pt-PT"):
        keys = re.findall(r'^    "([A-Za-z0-9_]+)":', (FOLDER / f"{name}.py").read_text(encoding="utf-8"), flags=re.M)
        assert sorted({key for key in keys if keys.count(key) > 1}) == [], name

def test_language_changed_per_thread():
    """A page written in every language (page.sections_in) changes the language of its own thread only: the
    map's other threads go on writing its progress in the trip's."""
    import threading
    from trip_planner.texts import in_language, tr
    english, heard = tr("discover_page_language"), []
    with in_language("pt-PT"):
        thread = threading.Thread(target=lambda: heard.append(tr("discover_page_language")))
        thread.start()
        thread.join()
        assert tr("discover_page_language") == "Língua"
    assert heard == [english] and tr("discover_page_language") == english
