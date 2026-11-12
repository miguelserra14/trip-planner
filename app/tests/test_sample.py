"""Which trips of a map a batch of app/sample.py plans, and where its reports go. Made-up inputs, no network."""
from app import sample
from trip_planner.report import full
from trip_planner.tools import funnel

def point(code: str, total: float, ring: bool) -> dict:
    return {"to": code, "total_eur": total, "time": "10:00", "confirmed": ring}

def test_the_trips_planned_are_half_looked_up_and_half_estimated_spread_over_the_prices():
    points = [point(f"R{number}", 10.0 + number, True) for number in range(9)] + [point(f"E{number}", 30.0 + number, False) for number in range(5)]
    planned = {f"{entry['to']}-out": ["A", entry["to"], "03/12/2026"] for entry in points}
    picked = [entry["to"] for entry in sample.chosen(points, planned, 6)]
    assert picked == ["R0", "R4", "R8", "E0", "E2", "E4"]

def test_when_too_few_were_looked_up_the_estimated_ones_make_up_the_batch():
    points = [point("R0", 10.0, True)] + [point(f"E{number}", 20.0 + number, False) for number in range(8)]
    planned = {f"{entry['to']}-out": ["A", entry["to"], "03/12/2026"] for entry in points}
    picked = [entry["to"] for entry in sample.chosen(points, planned, 4)]
    assert picked[0] == "R0" and len(picked) == 4 and all(code.startswith("E") for code in picked[1:])

def test_a_trip_the_planner_cannot_be_opened_on_or_without_a_time_is_never_chosen():
    points = [point("OK", 10.0, True), point("NOPLAN", 5.0, True), {**point("NOTIME", 6.0, False), "time": ""}]
    planned = {"OK-out": ["A", "OK", "03/12/2026"], "NOTIME-out": ["A", "NOTIME", "03/12/2026"]}
    assert [entry["to"] for entry in sample.chosen(points, planned, 6)] == ["OK"]

def test_a_batch_refuses_a_wrong_day_or_too_many_trips_before_asking_anything(capsys):
    assert sample.main(["Coimbra", "2026-12-03"]) == 1
    assert sample.main(["Coimbra", "03/12/2026", str(sample.MAX_TRIPS + 1)]) == 1
    assert sample.main(["Coimbra"]) == 1

def test_the_sample_is_kept_apart_from_the_travellers_reports():
    # The tests move full.REPORTS to a folder of their own: the sample's place was set from the real one
    assert sample.SAMPLE == full.ROOT / "reports" / "sample" and not sample.SAMPLE.name.startswith("trip_")

def test_the_funnel_reads_another_folder_when_given_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(full, "REPORTS", full.REPORTS)   # put back after the test
    monkeypatch.setattr(full, "SAVED_REPORTS", full.SAVED_REPORTS)
    assert funnel.main(tmp_path / "none") == 1 and "is not a folder" in capsys.readouterr().out
    assert funnel.main(tmp_path) == 1 and full.REPORTS == tmp_path
