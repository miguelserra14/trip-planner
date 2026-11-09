"""A round trip: the way back is a trip of its own on the same airports, and the options of the two ways are paired
in a summary. Made-up settings and options, no network."""
import asyncio
import dataclasses
import os
from datetime import date, timedelta

import pytest

from trip_planner import planner
from trip_planner.report import full
from trip_planner.report.summary import ground_round_trip_pairs, render_ground_round_trip, render_round_trip, round_trip_pairs
from trip_planner.trip.places import Place, return_places, TripPlaces
from trip_planner.trip.trip_settings import SettingError, TripSettings

day = lambda days: f"{date.today() + timedelta(days=days):%d/%m/%Y}"
iso = lambda days: (date.today() + timedelta(days=days)).isoformat()

def round_trip(**changes) -> TripSettings:
    return TripSettings(**{"origin": "Coimbra", "destination": "Antwerp", "dates": [day(30)], "return_dates": [day(34), day(35)],
                           "confirm_airports": False, **changes})

def places() -> TripPlaces:
    return TripPlaces(Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.2, -8.4), Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.2, 4.4),
                      primary=[{"code": "OPO", "km": 110}], fallback=[{"code": "LIS", "km": 180}],
                      arrival=[{"code": "BRU", "km": 45}, {"code": "CRL", "km": 90}], dropped=["FAO"])

def option(leave_day: int, leave: str, arrive_day: int, arrive: str, cost: float, time_cost: float = 0) -> dict:
    return {"flight": {"date": iso(leave_day), "route": "OPO → BRU"}, "leave_date": iso(leave_day), "leave_time": leave,
            "arrival_date": iso(arrive_day), "arrival_time": arrive, "total_cost": cost, "time_cost": time_cost,
            "schedule_grade": "B", "confirmed_eur": cost / 2, "total_minutes": 480}

# ---------------------------------------------------------------- the settings of the way back
def test_the_way_back_goes_from_the_destination_with_each_choice_on_its_own_side():
    settings = round_trip(departure_scale="near", arrival_scale="wide", to_airport_by="train", from_airport_by="bus",
                          free_trains_at_origin=True, arrival_airports=["BRU"], primary_airports=["OPO"], fallback_airports=["LIS"])
    back = settings.return_trip()
    assert (back.origin, back.destination, back.dates, back.return_dates) == ("Antwerp", "Coimbra", settings.return_dates, [])
    assert (back.departure_scale, back.arrival_scale, back.to_airport_by, back.from_airport_by) == ("wide", "near", "bus", "train")
    assert back.free_trains_at_destination and not back.free_trains_at_origin
    assert (back.primary_airports, back.fallback_airports, back.arrival_airports) == (["BRU"], [], ["OPO", "LIS"])
    assert (back.earliest_leave, back.latest_arrival) == (settings.earliest_leave, settings.latest_arrival)   # the same limits

def test_the_way_back_of_the_way_back_is_the_way_there():
    """Every choice of a side goes back where it was: none is lost, or swapped on one way only."""
    settings = round_trip(departure_scale="near", to_airport_by="train", max_changes_to_airport=1, free_trains_at_origin=True,
                          return_dates=[day(30)])   # the same day both ways, so that the way back of the way back may be planned
    again = dataclasses.replace(settings.return_trip(), return_dates=settings.dates).return_trip()
    assert again == dataclasses.replace(settings, return_dates=[])

def test_without_return_dates_the_trip_is_one_way():
    assert TripSettings(origin="Coimbra", destination="Antwerp", dates=[day(30)]).return_dates == []

def test_return_dates_are_read_like_the_dates_there():
    assert round_trip(return_dates=f"{day(45)}, {day(40)}").return_dates == [day(40), day(45)]   # one text with commas, put in order
    first, last = date.today() + timedelta(days=40), date.today() + timedelta(days=42)
    if first.month == last.month:   # a range of days: "10-12/12/2026"
        assert round_trip(return_dates=f"{first:%d}-{last:%d/%m/%Y}").return_dates == [day(40), day(41), day(42)]

def test_a_way_back_on_the_day_of_the_way_there_is_allowed():
    assert round_trip(return_dates=[day(30)]).return_dates == [day(30)]

def test_a_way_back_before_the_way_there_is_refused():
    with pytest.raises(SettingError) as refused:
        round_trip(return_dates=[day(29)], language="pt-PT")
    assert day(29) in str(refused.value) and "ida" in str(refused.value)

@pytest.mark.parametrize("dates", [[day(-1)], [day(400)], [day(31 + number) for number in range(8)], ["the 31st of nowhere"]],
                         ids=["past", "too far", "too many", "unreadable"])
def test_return_dates_that_cannot_be_searched_are_refused(dates):
    with pytest.raises(SettingError):
        round_trip(return_dates=dates)

def test_a_far_way_back_is_warned_about_like_a_far_way_there():
    assert any("320" in advice for advice in round_trip(return_dates=[day(320)]).advice())

# ---------------------------------------------------------------- the airports of the way back
def test_the_way_back_flies_from_the_airports_of_the_destination_to_those_of_the_origin():
    back = return_places(places())
    assert (back.origin.written, back.destination.written) == ("Antwerp", "Coimbra")
    assert [a["code"] for a in back.primary] == ["BRU", "CRL"] and back.fallback == []
    assert [a["code"] for a in back.arrival] == ["OPO", "LIS"] and back.dropped == ["FAO"]

def test_the_airports_of_the_way_back_are_copies():
    """Dropping an airport from one way (remove_airports changes the lists in place) does not drop it from the other."""
    there = places()
    back = return_places(there)
    back.primary.pop()
    back.arrival.pop()
    assert len(there.arrival) == 2 and len(there.departure) == 2

# ---------------------------------------------------------------- pairing the options
def test_a_way_back_that_leaves_before_the_way_there_arrives_is_not_paired():
    going = [option(30, "06:00", 30, "14:00", 80), option(34, "06:00", 34, "14:00", 40)]
    coming = [option(34, "09:00", 34, "18:00", 70)]
    assert [(there, back) for there, back, _, _ in round_trip_pairs(going, coming)] == [(1, 1)]   # option 2 there arrives after 09:00

def test_the_pairs_are_ranked_by_cost_and_the_value_of_time_of_both_ways():
    going = [option(30, "06:00", 30, "14:00", 80), option(30, "08:00", 30, "13:00", 75, time_cost=20)]
    coming = [option(34, "09:00", 34, "18:00", 70), option(35, "09:00", 35, "18:00", 60)]
    pairs = round_trip_pairs(going, coming)
    assert [(there, back) for there, back, _, _ in pairs][:2] == [(1, 2), (1, 1)]   # 80 + 60 first; 75 + 20 of time is worth more

def test_a_way_back_just_after_midnight_is_paired_with_a_late_arrival_only_if_it_leaves_after_it():
    late = option(30, "18:00", 31, "00:30", 80)   # arrives after midnight, on the next day
    assert round_trip_pairs([late], [option(31, "00:10", 31, "09:00", 70)]) == []
    assert len(round_trip_pairs([late], [option(31, "01:00", 31, "09:00", 70)])) == 1

def test_a_way_back_that_leaves_the_evening_before_its_flight_counts_from_that_evening():
    there = option(30, "06:00", 30, "14:00", 80)
    back = {**option(31, "20:30", 31, "09:00", 70), "leave_date": iso(30)}   # leaves the evening before an early flight
    assert len(round_trip_pairs([there], [back])) == 1
    assert round_trip_pairs([there], [{**back, "leave_time": "13:00"}]) == []

def test_the_closest_options_have_no_value_of_time_and_are_paired_on_their_cost():
    without_time = lambda itinerary: {key: value for key, value in itinerary.items() if key != "time_cost"}
    there = without_time(option(30, "06:00", 30, "14:00", 80))
    coming = [without_time(option(34, "09:00", 34, "18:00", cost)) for cost in (90, 60)]
    assert [back for _, back, _, _ in round_trip_pairs([there], coming)] == [2, 1]

# ---------------------------------------------------------------- the summary
def test_the_summary_names_the_best_pair_and_the_nights_at_the_destination(settings):
    summary = render_round_trip([option(30, "06:00", 30, "14:00", 80)], [option(34, "09:00", 34, "18:00", 70)], "Coimbra", "Antwerp")
    assert "Coimbra" in summary and "150 EUR" in summary and "4 night" in summary

def test_the_summary_shows_three_pairs_at_most_and_the_best_one_first(settings):
    going = [option(30, "06:00", 30, "14:00", cost) for cost in (80, 90)]
    coming = [option(34, "09:00", 34, "18:00", cost) for cost in (60, 70, 75)]
    rows = [line for line in render_round_trip(going, coming, "Coimbra", "Antwerp").split("\n") if line.startswith("| ") and "EUR" in line]
    assert len(rows) == 3 and "140 EUR" in rows[0] and "recommended" in rows[0]

def test_the_summary_is_in_the_travellers_language(settings):
    settings.language = "pt-PT"
    summary = render_round_trip([option(30, "06:00", 30, "14:00", 80)], [option(34, "09:00", 34, "18:00", 70)], "Coimbra", "Antuérpia")
    assert "Ida e volta" in summary and "recomendado" in summary and "4 noite(s)" in summary

def test_with_no_option_one_way_the_summary_says_there_is_no_round_trip(settings):
    assert "way back" in render_round_trip([option(30, "06:00", 30, "14:00", 80)], [], "Coimbra", "Antwerp")
    assert "way there" in render_round_trip([], [option(34, "09:00", 34, "18:00", 70)], "Coimbra", "Antwerp")

def test_no_pair_at_all_says_to_come_back_later(settings):
    assert "later" in render_round_trip([option(34, "06:00", 34, "14:00", 80)], [option(30, "09:00", 30, "18:00", 70)], "Coimbra", "Antwerp")

# ---------------------------------------------------------------- planning both ways
@pytest.fixture
def no_search(monkeypatch):
    """plan_trip with each way made up: what was planned, and what was shown."""
    record = {"planned": [], "shown": [], "fail": None}

    async def plan_one_way(settings, places, after=None, beside=None):
        if record["fail"] == settings.origin:
            raise RuntimeError("the search failed")
        when = 30 if settings.origin == "Coimbra" else 34
        found = {"shown": [option(when, "09:00", when, "14:00", 50)], "report": f"{settings.origin} https://{settings.origin.lower()}.example/"}
        planner.PLANNED.append({"settings": settings, "places": places, "messages": [settings.origin], "state": found,
                                "report": f"{settings.origin}.md"})
        record["planned"].append((settings.origin, settings.dates, places.origin.written, beside))
        if after:
            record["shown"].append(after(found))
        return found
    monkeypatch.setattr(planner, "plan_one_way", plan_one_way)
    monkeypatch.setattr(planner, "show", lambda markdown: record["shown"].append(markdown))
    monkeypatch.setattr(planner, "PLANNED", [])
    return record

def test_one_way_is_planned_once_with_no_summary(no_search):
    asyncio.run(planner.plan_trip(round_trip(return_dates=[]), places()))
    assert [origin for origin, *_ in no_search["planned"]] == ["Coimbra"] and no_search["shown"] == []
    assert "there" not in planner.PLANNED[-1]

def test_the_way_back_is_planned_on_the_return_dates_and_keeps_the_report_there(no_search):
    settings = round_trip()
    asyncio.run(planner.plan_trip(settings, places()))
    assert no_search["planned"] == [("Coimbra", settings.dates, "Coimbra", None), ("Antwerp", settings.return_dates, "Antwerp", "Coimbra.md")]
    assert no_search["shown"][0].startswith("# ") and "Coimbra → Antwerp" in no_search["shown"][0]   # each way has its heading
    assert "100 EUR" in no_search["shown"][-1]   # the summary pairs the two ways
    assert planner.PLANNED[-1]["messages"] == ["Coimbra", "Antwerp"] and planner.PLANNED[-1]["there"] is planner.PLANNED[0]

def test_when_the_way_there_fails_the_way_back_is_not_planned(no_search):
    no_search["fail"] = "Coimbra"
    with pytest.raises(RuntimeError):
        asyncio.run(planner.plan_trip(round_trip(), places()))
    assert no_search["planned"] == []

def test_a_question_after_a_round_trip_reads_both_ways_and_keeps_the_links_of_both(no_search, monkeypatch):
    asyncio.run(planner.plan_trip(round_trip(), places()))
    read = {}

    async def write(messages):
        read["messages"] = list(messages)
        return "See https://coimbra.example/ and https://antwerp.example/ and https://elsewhere.example/"
    monkeypatch.setattr(planner.coordinator, "write", write)
    asyncio.run(planner.ask("Which pair is best?"))
    assert read["messages"][:2] == ["Coimbra", "Antwerp"]
    answer = no_search["shown"][-1]
    assert "coimbra.example" in answer and "antwerp.example" in answer and "elsewhere.example" not in answer

def test_keeping_a_round_trip_keeps_both_reports(tmp_path, monkeypatch, settings):
    monkeypatch.setattr(full, "REPORTS", tmp_path)
    monkeypatch.setattr(full, "SAVED_REPORTS", tmp_path / "saved")
    monkeypatch.setattr(full, "render_full_report", lambda response, recommendation: "the report")
    settings.recent_reports = 1
    destination, settings.destination = settings.destination, "Paris"
    older = full.save_report({}, "")   # a trip of before, which the round trip makes room for
    os.utime(older.parent, (1, 1))
    settings.destination = destination
    there = full.save_report({}, "")
    settings.origin, settings.destination = settings.destination, settings.origin
    back = full.save_report({}, "", beside=there)
    assert there.exists() and back.exists()   # one trip is kept, and both ways are that trip
    assert not older.parent.exists() and full.trip_folders(tmp_path) == [there.parent] == [back.parent]
    assert sorted(path.name for path in there.parent.iterdir()) == sorted(
        path.name for report in (there, back) for path in (report, full.log_of(report), full.options_of(report)))
    trip = {"report": back, "there": {"report": there}}
    planner.keep_trip(trip, True)
    for report in (trip["report"], trip["there"]["report"]):
        assert report.parent.parent.name == "saved" and report.exists() and full.log_of(report).exists()
    assert full.trip_folders(tmp_path) == []
    planner.keep_trip(trip, False)   # unticked: back among the recent ones, both ways together
    assert trip["report"].parent == trip["there"]["report"].parent == tmp_path / there.parent.name and trip["report"].exists()

def test_a_round_trip_planned_saves_both_ways_in_one_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(full, "REPORTS", tmp_path)
    monkeypatch.setattr(full, "SAVED_REPORTS", tmp_path / "saved")
    monkeypatch.setattr(full, "render_full_report", lambda response, recommendation: f"{full.current.SETTINGS.origin} -> {full.current.SETTINGS.destination}")
    monkeypatch.setattr(planner, "PLANNED", [])
    monkeypatch.setattr(planner, "say", lambda text: None)
    monkeypatch.setattr(planner, "show", lambda markdown: None)

    async def run_search(state):
        return {**state, "report": "", "shown": [], "recommended": 0, "verify": {"options": []}}

    async def no_model(messages):
        raise RuntimeError("no model in tests")
    monkeypatch.setattr(planner, "run_search", run_search)
    monkeypatch.setattr(planner, "build_request", lambda settings, places: "")
    monkeypatch.setattr(planner.coordinator, "write", no_model)
    asyncio.run(planner.plan_trip(round_trip(), places()))
    there, back = (trip["report"] for trip in planner.PLANNED)
    assert there.parent == back.parent and full.trip_folders(tmp_path) == [there.parent]
    assert there.read_text(encoding="utf-8") == "Coimbra -> Antwerp" and back.read_text(encoding="utf-8") == "Antwerp -> Coimbra"
    assert len(full.report_files("trip_*.json")) == 2 and len(full.report_files("trip_*.log")) == 2

def test_each_trip_is_a_folder_of_its_own_and_only_the_newest_stay(tmp_path, monkeypatch, settings):
    monkeypatch.setattr(full, "REPORTS", tmp_path)
    monkeypatch.setattr(full, "SAVED_REPORTS", tmp_path / "saved")
    monkeypatch.setattr(full, "render_full_report", lambda response, recommendation: "the report")
    settings.recent_reports = 2
    reports = []
    for age, destination in enumerate(("Paris", "Madrid", "Rome")):
        settings.destination = destination
        reports.append(full.save_report({"verify": {"options": []}}, ""))
        os.utime(reports[-1].parent, (age, age))
    assert sorted(full.trip_folders(tmp_path)) == sorted(report.parent for report in reports[1:])
    assert not reports[0].parent.exists() and all(report.exists() and full.vehicles_of(report).exists() for report in reports[1:])
    kept = full.keep_report(reports[1], True)
    assert [path.name for path in full.report_files("trip_*.json")] == [full.vehicles_of(reports[2]).name, full.vehicles_of(kept).name]

# ---------------------------------------------------------------- a round trip without a plane
def by_ground(days: int, leave: str, arrive: str, price: float | None = 20.0) -> dict:
    """A trip by train or coach all the way, as ground_only.py keeps it: its times are minutes from its day's midnight."""
    minutes = lambda time: int(time[:2]) * 60 + int(time[3:])
    return {"day": iso(days), "leave": minutes(leave), "arrive": minutes(arrive), "minutes": minutes(arrive) - minutes(leave),
            "grade": "B", "price_eur": price, "confirmed_eur": price or 0.0}

def test_a_day_trip_without_a_plane_keeps_the_shortest_stay_at_the_destination():
    there = [by_ground(30, "07:00", "09:00")]
    back = [by_ground(30, "10:00", "12:00"), by_ground(30, "18:00", "20:00", 30.0)]
    assert [other for _, other, _, _ in ground_round_trip_pairs(there, back, 6 * 60)] == [2]   # one hour in the city is not a day trip
    assert [other for _, other, _, _ in ground_round_trip_pairs(there, back, 0)] == [1, 2]

def test_the_pairs_without_a_plane_are_the_cheapest_first_and_those_without_a_price_last():
    there = [by_ground(30, "07:00", "09:00"), by_ground(30, "06:00", "08:00", None)]
    back = [by_ground(31, "18:00", "20:00", 30.0), by_ground(31, "19:00", "21:00", 10.0)]
    assert [(number, other) for number, other, _, _ in ground_round_trip_pairs(there, back, 6 * 60)] == [(1, 2), (1, 1), (2, 1), (2, 2)]

def test_the_summary_without_a_plane_says_the_time_at_the_destination(settings):
    text = render_ground_round_trip([by_ground(30, "07:00", "09:00")], [by_ground(30, "18:00", "20:00")], "Antwerp", "Brussels", 6)
    assert "40 EUR" in text and "9h00" in text
    assert "6 h" in render_ground_round_trip([by_ground(30, "07:00", "09:00")], [by_ground(30, "10:00", "12:00")], "Antwerp", "Brussels", 6)

def test_the_way_back_of_a_day_trip_without_a_plane_is_searched_from_the_first_arrival_and_the_stay():
    back = round_trip(dates=[day(30)], return_dates=[day(30)], ground_only=True).return_trip()
    later = planner.after_the_stay(back, [by_ground(30, "07:00", "10:30"), by_ground(30, "08:00", "09:15")])
    assert later.earliest_leave == "15:15"   # the first arrival, 09:15, and six hours
    assert planner.after_the_stay(back, []) is back

def test_a_way_back_on_another_day_is_searched_all_day():
    back = round_trip(dates=[day(30)], return_dates=[day(31)], ground_only=True).return_trip()
    assert planner.after_the_stay(back, [by_ground(30, "07:00", "09:00")]).earliest_leave == "00:00"

def test_a_stay_past_the_last_hour_to_leave_leaves_the_way_back_as_it_was():
    back = round_trip(dates=[day(30)], return_dates=[day(30)], ground_only=True, latest_leave="12:00").return_trip()
    assert planner.after_the_stay(back, [by_ground(30, "07:00", "09:00")]).earliest_leave == "00:00"

def test_a_round_trip_without_a_plane_pairs_the_trips_by_ground_after_the_stay(no_search, monkeypatch):
    async def plan_one_way(settings, places, after=None, beside=None):
        found = {"shown": [], "ground_only": [by_ground(30, "07:00", "09:00")] if settings.origin == "Antwerp"
                 else [by_ground(30, settings.earliest_leave, "20:00")]}
        no_search["planned"].append(settings.earliest_leave)
        planner.PLANNED.append({"settings": settings, "places": places, "messages": [], "state": found, "report": "r.md"})
        if after:
            no_search["shown"].append(after(found))
        return found
    monkeypatch.setattr(planner, "plan_one_way", plan_one_way)
    asyncio.run(planner.plan_trip(round_trip(origin="Antwerp", destination="Brussels", dates=[day(30)], return_dates=[day(30)],
                                             ground_only=True), places()))
    assert no_search["planned"] == ["00:00", "15:00"]
    assert "40 EUR" in no_search["shown"][-1] and "6h00" in no_search["shown"][-1]

def test_the_steps_of_the_timings_and_the_sources_of_the_timetables_are_in_the_travellers_language(settings):
    from trip_planner.report.full import step_name
    from trip_planner.sources.timetables import timetable_sources
    settings.language = "pt-PT"
    assert step_name("2c") == "2c horários reais" and step_name("total") == "total"
    assert "horários publicados" in timetable_sources() and "published" not in timetable_sources()

def test_a_published_tariff_is_said_in_the_travellers_language_also_in_a_report_saved_before(settings):
    from trip_planner.report.formatting import ticket_operator
    settings.language = "pt-PT"
    assert ticket_operator({"operator": "De Lijn", "tariff": True}) == "De Lijn (tarifa publicada)"
    assert ticket_operator({"operator": "De Lijn (published tariff)", "tariff": True}) == "De Lijn (tarifa publicada)"
    assert ticket_operator({"operator": "FlixBus", "tariff": False}) == "FlixBus"
