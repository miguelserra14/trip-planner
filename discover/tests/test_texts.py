"""The map and its form in the traveller's language (texts/languages, discover_...): every text the page's script
asks for exists, English and Portuguese have the same texts with the same {values}, and the code writes them. No network."""
import dataclasses
import re

from discover.view import page
from discover.flights.sources import Days
from trip_planner import current
from trip_planner.texts import TEXTS

def test_every_text_the_page_asks_for_exists():
    script = page.SCRIPT.read_text(encoding="utf-8")
    asked = set(re.findall(r'\bt\(\s*"([a-z_]+)"', script)) | {key for pair in re.findall(r'\?\s*"([a-z_]+)"\s*:\s*"([a-z_]+)"\)', script) for key in pair}
    assert asked and asked <= set(page.page_texts())

def test_english_and_portuguese_have_the_same_texts_with_the_same_values():
    values = lambda text: sorted(set(re.findall(r"\{(\w+)\}", text)))
    english = {key: text for key, text in TEXTS["en"].items() if key.startswith("discover_")}
    portuguese = {key: text for key, text in TEXTS["pt-PT"].items() if key.startswith("discover_")}
    assert set(english) == set(portuguese)
    assert [key for key in english if values(english[key]) != values(portuguese[key])] == []

def test_the_days_and_the_page_are_written_in_the_travellers_language(monkeypatch):
    monkeypatch.setattr(current, "SETTINGS", dataclasses.replace(current.SETTINGS, language="pt-PT"))
    assert str(Days("2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17")) == "2026-11-12 a 2026-11-13, volta 2026-11-16 a 2026-11-17"
    texts = page.page_texts()
    assert texts["still_searching"] == "Ainda a pesquisar." and texts["when"] == "{day} às {time}"
