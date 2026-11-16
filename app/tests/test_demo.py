"""The demo (app/demo.py): the reports and maps kept in demo/ shown as the traveller's own, with nothing searched and
no file changed. No network."""
import shutil

import pytest

from app import demo, maps, reports
from app.site import Site
from settings import SETTINGS
from trip_planner.report import full

@pytest.fixture
def in_demo(monkeypatch, tmp_path):
    """The app pointed at a copy of demo/ as main.py --demo points it (demo.use), undone after the test: a test that
    fails cannot move a file of the repository."""
    copied = tmp_path / "demo"
    shutil.copytree(demo.DEMO, copied)
    monkeypatch.setattr(demo, "DEMO_REPORTS", copied / "reports")
    monkeypatch.setattr(demo, "DEMO_MAPS", copied / "maps")
    for module, names in ((full, ("REPORTS", "SAVED_REPORTS")), (reports, ("REPORTS", "SAVED_REPORTS")), (maps, ("SAVED_MAPS",))):
        for name in names:
            monkeypatch.setattr(module, name, getattr(module, name))   # demo.use sets them: monkeypatch puts them back
    demo.use()
    started = []
    site = Site(SETTINGS, start=lambda first: started.append(first), stale=list, demo=True)
    return site, started

def test_demo_pages_still_open(in_demo):
    site, _ = in_demo
    found = [key for group in reports.reports().values() for key in group]
    shown = maps.saved_maps()
    assert found and shown
    home = site.get("/")
    assert home.status == 200 and "Demo:" in home.html and "GOOGLE_API_KEY" not in home.html
    for key in found:
        page = site.get(reports.REPORT_PATH + key)
        assert page.status == 200, key
        assert site.get(reports.REPORT_PATH + key + "?view=full").status == 200, key
    for path in shown:
        page = site.get(maps.SAVED + path.stem)
        assert page.status == 200 and site.token not in page.html, path.name   # no token: the page asks for no price

def test_demo_changes_nothing(in_demo):
    site, started = in_demo
    key = next(iter(reports.reports()["recent"]))
    asked = {"/search": "origin=Coimbra&destination=Faro&dates=03/12/2026", "/check": "", "/prices": "report=x",
             "/language/add": "name=Italiano", "/language/remove": "code=xx&confirm=1",
             "/discover": "city=Coimbra&dates=03/12/2026", "/keep": f"report={key}&keep=1"}
    for path, body in asked.items():
        answer = site.post(path, f"token={site.token}&{body}".encode())
        assert answer.status == 403 and "Not in the demo" in answer.html, path
    assert started == [] and key in reports.reports()["recent"]

def test_demo_switches_theme_language(in_demo):
    site, _ = in_demo
    assert site.post("/theme", f"token={site.token}&shown=dark&back=/".encode()).status == 303 and site.theme == "light"
    assert site.post("/language", f"token={site.token}&language=pt-PT".encode()).status == 303

def test_demo_copy_drops_log(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "DEMO_REPORTS", tmp_path / "demo")
    trip = tmp_path / "reports" / "trip_coimbra_faro_2026-11-12_1031"
    trip.mkdir(parents=True)
    for name in ("trip_coimbra_faro_2026-11-12_1031.md", "trip_coimbra_faro_2026-11-12_1031.log", "options_trip_coimbra_faro_2026-11-12_1031.json"):
        (trip / name).write_text("x", encoding="utf-8")
    copied = demo.copy(trip)
    assert sorted(path.suffix for path in copied.iterdir()) == [".json", ".md"]
    with pytest.raises(ValueError):
        demo.copy(tmp_path / "reports")
