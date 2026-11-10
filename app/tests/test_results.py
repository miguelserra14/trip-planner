"""The result of a trip as cards (results.py), from its options as data, and what goes with it: the reports listed as
cards, the trip searched again, the full report kept, the pages' own style and their dark or light look. Made-up
options, no network."""
import dataclasses
import html
import re

import pytest

from app import fields
from app.report_page import shares, timeline, VEHICLE_SHARE, WAIT_SHARE
from app.results import ride_parts, vehicles_list
from app.site import Site
from app.tests.test_search import DAY, sent
from settings import SETTINGS
from tests.report.test_options import DAY as TRAVEL_DAY, NEXT_DAY, itinerary
from trip_planner import current
from trip_planner.report import full
from trip_planner.texts import listed, said, text_in, tr
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

def test_a_warning_is_shown_in_the_language_the_report_is_read_in_not_the_one_it_was_searched_in(report, monkeypatch):
    english = current.SETTINGS
    monkeypatch.setattr(current, "SETTINGS", dataclasses.replace(english, language="pt-PT"))   # the search, in Portuguese
    values = dict(source="Rede Expressos", days=71, day=listed(["23/03/2027"]), from_day=listed(["11/01/2027"]))
    warning = said("warn_not_on_sale", side=said("sale_side_to", airport=listed(["OPO", "LIS"])), **values)
    assert warning == text_in("pt-PT", "warn_not_on_sale", side=text_in("pt-PT", "sale_side_to", airport="OPO e LIS"), **values)
    searched = full.save_report({"shown": [itinerary(120, 600)], "recommended": 1, "ground_only": [],
                                 "warnings": [warning, "Kept as an older report wrote it"]}, "")
    monkeypatch.setattr(current, "SETTINGS", english)   # read later, in English
    page = Site(SETTINGS).get(address(searched)).html
    shown = text_in("en", "warn_not_on_sale", side=text_in("en", "sale_side_to", airport="OPO and LIS"), **values)
    assert html.escape(shown, quote=False) in page and "Kept as an older report wrote it" in page

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
    assert '<html lang="en" data-theme="dark">' in site.get("/").html   # dark, until the button turns it
    site.post("/theme", sent(site, shown="dark", back="/program"))
    assert '<html lang="en" data-theme="light">' in site.get("/").html
    site.post("/theme", sent(site, shown="light", back="/program"))
    assert '<html lang="en" data-theme="dark">' in site.get("/").html
    turned = site.post("/theme", sent(site, shown="light", back="https://elsewhere.example/"))
    assert turned.location == "/" and '<html lang="en" data-theme="light">' in site.get("/").html

# A coach and two trains from an airport, as the timetables give them: a walk to the coach, a change of 15 minutes
# at Bergamo, one of 29 at Vicenza, and the last train arriving at the end of the leg
THREE_RIDES = [{"time": "12:00", "arrive": "12:40", "mode": "COACH", "from": "Linate", "to": "Bergamo"},
               {"time": "12:55", "arrive": "15:45", "mode": "COACH", "from": "Bergamo", "to": "Vicenza"},
               {"time": "16:14", "arrive": "16:38", "mode": "REGIONAL_RAIL", "from": "Vicenza", "to": "Cittadella"}]

def test_a_leg_of_three_vehicles_is_three_parts_on_the_line_with_the_changes_between_them():
    points, parts = ride_parts("11:55", 16 * 60 + 38 - (11 * 60 + 55), "B", THREE_RIDES)
    assert [(grade, minutes) for grade, minutes, *_ in parts] == [(None, 5), ("B", 40), (None, 15), ("B", 170), (None, 29), ("B", 24)]
    assert [part[2] for part in parts if part[0]] == ["bus", "bus", "train"]
    assert len(points) == len(parts) - 1
    assert [point[:2] for point in points if point] == [("12:40", "Bergamo"), ("12:55", "Bergamo"), ("15:45", "Vicenza"), ("16:14", "Vicenza")]

def test_the_changes_are_dots_said_on_hover_and_the_list_says_where_and_how_long():
    points, parts = ride_parts("11:55", 283, "B", THREE_RIDES)
    line = timeline([("11:55", "MXL"), *points, ("16:38", "Cittadella")], parts)
    assert line.count('class="change"') == 4 and 'title="12:40 Bergamo"' in line
    assert line.count('class="vehicle"') == 3   # the walk is too short for an icon, and a wait has none
    listed = vehicles_list(THREE_RIDES)
    assert "Change at Bergamo: 0h15" in listed and "Change at Vicenza: 0h29" in listed

def test_short_vehicles_in_a_long_trip_each_get_room_for_their_icon():
    # Three rides of a few minutes after a flight of seven hours: to scale, each would be under 1% of the line
    parts = [("A", 420, "plane"), (None, 30), ("B", 5, "subway"), (None, 10), ("B", 9, "bus"), (None, 4), ("B", 6, "train")]
    drawn = shares(parts)
    assert sum(drawn) == pytest.approx(100)
    assert all(drawn[number] >= VEHICLE_SHARE for number in (2, 4, 6)) and all(drawn[number] >= WAIT_SHARE for number in (3, 5))
    assert timeline([("10:00", "BRU"), *[None] * 6, ("18:00", "Pavia")], parts).count('class="vehicle"') == 4

def test_a_leg_whose_vehicles_do_not_fit_in_its_times_stays_one_part():
    assert ride_parts("12:30", 60, "B", THREE_RIDES) == ([], [("B", 60, "bus")])
    assert ride_parts("12:00", 40, "A", THREE_RIDES[:1]) == ([], [("A", 40, "bus")])   # one vehicle: nothing to split

def test_a_trip_asked_without_a_plane_does_not_say_no_option_was_found(monkeypatch):
    monkeypatch.setattr(current, "TRIP", TripPlaces(Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.2, 4.4),
                                                    Place("Brussels", "Brussel", "Brussels", "Belgium", 50.8, 4.4), primary=[], fallback=[], arrival=[]))
    english = current.SETTINGS
    current.SETTINGS = dataclasses.replace(english, origin="Antwerp", destination="Brussels", ground_only=True)
    path = full.save_report({"shown": [], "recommended": 0, "warnings": [], "ground_only": [], "report": "## Without a plane"}, "")
    current.SETTINGS = english
    assert tr("app_no_options") not in Site(SETTINGS).get(address(path)).html
