"""Which trips of a map a batch of app/sample.py plans, and where its reports go. Made-up inputs, no network."""
from app import sample
from trip_planner.report import full
from trip_planner.tools import funnel

def point(code: str, total: float, ring: bool) -> dict:
    return {"to": code, "total_eur": total, "time": "10:00", "confirmed": ring}

def test_sample_mixes_kinds():
    points = [point(f"R{number}", 10.0 + number, True) for number in range(9)] + [point(f"E{number}", 30.0 + number, False) for number in range(5)]
    planned = {f"{entry['to']}-out": ["A", entry["to"], "03/12/2026"] for entry in points}
    picked = [entry["to"] for entry in sample.chosen(points, planned, 6)]
    assert picked == ["R0", "R4", "R8", "E0", "E2", "E4"]

def test_estimates_fill_sample():
    points = [point("R0", 10.0, True)] + [point(f"E{number}", 20.0 + number, False) for number in range(8)]
    planned = {f"{entry['to']}-out": ["A", entry["to"], "03/12/2026"] for entry in points}
    picked = [entry["to"] for entry in sample.chosen(points, planned, 4)]
    assert picked[0] == "R0" and len(picked) == 4 and all(code.startswith("E") for code in picked[1:])

def test_unopenable_trips_skipped():
    points = [point("OK", 10.0, True), point("NOPLAN", 5.0, True), {**point("NOTIME", 6.0, False), "time": ""}]
    planned = {"OK-out": ["A", "OK", "03/12/2026"], "NOTIME-out": ["A", "NOTIME", "03/12/2026"]}
    assert [entry["to"] for entry in sample.chosen(points, planned, 6)] == ["OK"]

def test_batch_validates_first(capsys):
    assert sample.main(["Coimbra", "2026-12-03"]) == 1
    assert sample.main(["Coimbra", "03/12/2026", str(sample.MAX_TRIPS + 1)]) == 1
    assert sample.main(["Coimbra"]) == 1

def test_sample_kept_apart():
    # The tests move full.REPORTS to a folder of their own: the sample's place was set from the real one
    assert sample.SAMPLE == full.ROOT / "reports" / "sample" and not sample.SAMPLE.name.startswith("trip_")

def test_funnel_reads_other_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(full, "REPORTS", full.REPORTS)   # put back after the test
    monkeypatch.setattr(full, "SAVED_REPORTS", full.SAVED_REPORTS)
    assert funnel.main(tmp_path / "none") == 1 and "is not a folder" in capsys.readouterr().out
    assert funnel.main(tmp_path) == 1 and full.REPORTS == tmp_path
