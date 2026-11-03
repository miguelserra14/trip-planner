"""The result of a trip as cards (results.py), from its options as data, and what goes with it: the reports listed as
cards, the trip searched again, the full report kept, the pages' own style and their dark or light look. Made-up
options, no network."""
import re

import pytest

from app import fields
from app.site import Site
from app.tests.test_search import DAY, sent
from settings import SETTINGS
from tests.report.test_options import DAY as TRAVEL_DAY, NEXT_DAY, itinerary
from trip_planner import current
from trip_planner.report import full
from trip_planner.trip.places import Place, TripPlaces

@pytest.fixture
def report(monkeypatch):
    """A one-way trip with two options, the dearer one recommended, saved as the planner saves it."""
    monkeypatch.setattr(current, "TRIP", TripPlaces(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.2, -8.4),
                                                    Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.2, 4.4), primary=[], fallback=[], arrival=[]))
    cheap, quick = itinerary(120, 600), itinerary(150, 420)
    return full.save_report({"shown": [cheap, quick], "recommended": 2, "warnings": ["Check <b>this</b>"],
                             "recommendation": "Take **option 2** <script>x</script>", "by_date": [(TRAVEL_DAY, cheap), (NEXT_DAY, None)],
                             "ground_only": [], "verify": {"options": []}}, "")

def address(path) -> str:
    return f"/report/recent/{path.stem}"

def order(page: str) -> list[str]:
    return re.findall(r'id="option-(\d)"', page)

def test_a_report_with_its_options_as_data_is_shown_as_cards_in_the_order_of_each_tab(report):
    site = Site(SETTINGS)
    best = site.get(address(report)).html
    assert order(best) == ["2", "1"]   # the recommended one first, then the report's order
    assert order(site.get(address(report) + "?sort=cheapest").html) == ["1", "2"]
    assert order(site.get(address(report) + "?sort=fastest").html) == ["2", "1"]
    assert 'href="?sort=best#options" aria-current="true"' in best and "+30 EUR on the cheapest" in best
    assert 'class="badge best">Recommended' in best and 'class="badge cheap">Cheapest' in best
    assert "10 of 150 EUR confirmed" in best and 'class="light-B"' in best   # every card says how sure it is, closed

def test_what_a_model_or_a_service_wrote_stays_text_and_the_models_words_are_said_to_be_a_models(report):
    page = Site(SETTINGS).get(address(report)).html
    assert "<script>x" not in page and "&lt;b&gt;this&lt;/b&gt;" in page
    assert "Written by a model" in page and "<strong>option 2</strong>" in page

def test_each_day_searched_says_what_it_would_cost(report):
    page = Site(SETTINGS).get(address(report)).html
    assert '<a href="#option-1">option 1</a>' in page and "no possible itinerary" in page

def test_the_full_report_is_kept_and_a_report_without_data_is_shown_as_it_was(report):
    site = Site(SETTINGS)
    whole = site.get(address(report) + "?view=full").html
    assert "<article>" in whole and "The results as cards" in whole and 'id="option-1"' not in whole
    full.options_of(report).unlink()
    assert "<article>" in site.get(address(report)).html

def test_the_search_is_changed_from_the_report_on_the_form_and_nothing_is_searched(report):
    site = Site(SETTINGS, start=lambda first: pytest.fail("nothing is searched"))
    moved = site.get(address(report) + "?edit=1")
    assert moved.status == 303 and moved.location == "/"
    trip = (site.values["origin"], site.values["destination"], site.values["dates"], site.values["return_dates"])
    assert trip == (current.SETTINGS.origin, current.SETTINGS.destination, ", ".join(current.SETTINGS.dates), "")

def test_the_reports_are_listed_with_the_price_of_the_option_recommended(report):
    home = Site(SETTINGS).get("/").html
    town = lambda written: written.split(",")[0]
    assert f"{town(current.SETTINGS.origin)} → {town(current.SETTINGS.destination)}" in home and "150 EUR" in home and "OPO → BRU" in home

def test_one_way_leaves_the_days_to_come_back_out_whatever_is_written_there():
    site = Site(SETTINGS)
    values = fields.read({"origin": "Coimbra", "destination": "Antwerp", "dates": DAY, "return_dates": DAY, "trip_kind": "one"}, SETTINGS)
    assert values["return_dates"] == ""
    assert fields.read({"origin": "Coimbra", "dates": DAY, "return_dates": DAY, "trip_kind": "round"}, SETTINGS)["return_dates"] == DAY
    assert 'value="one" checked' in site.get("/").html

def test_the_pages_have_their_own_style_and_turn_dark_or_light_when_asked():
    site = Site(SETTINGS)
    style = site.get("/page.css")
    assert style.status == 200 and style.kind.startswith("text/css") and '<link rel="stylesheet" href="/page.css">' in site.get("/").html
    assert "data-theme" not in site.get("/").html.split(">")[1]   # the computer's choice, until one is made
    site.post("/theme", sent(site, shown="light", back="/program"))
    assert '<html lang="en" data-theme="dark">' in site.get("/").html
    turned = site.post("/theme", sent(site, shown="light", back="https://elsewhere.example/"))
    assert turned.location == "/" and '<html lang="en" data-theme="light">' in site.get("/").html
