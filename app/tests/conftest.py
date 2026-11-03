"""What every test of the app shares: the planner's settings of settings.py, in English, so that no test depends on
another one having set them or on the language chosen, and no report of the traveller's: each test has its own
folders, the ones a report is kept in included."""
import dataclasses

import pytest

from app import reports
from trip_planner import current
from trip_planner.report import full
from trip_planner.trip.trip_settings import settings_file

SETTINGS = settings_file().SETTINGS   # read first here, as the app reads it: the tests' "from settings import" get this one

@pytest.fixture(autouse=True)
def english(monkeypatch, tmp_path):
    monkeypatch.setattr(current, "SETTINGS", dataclasses.replace(current.SETTINGS or SETTINGS, language="en"))
    for module in (reports, full):
        monkeypatch.setattr(module, "REPORTS", tmp_path / "reports")
        monkeypatch.setattr(module, "SAVED_REPORTS", tmp_path / "reports" / "saved")
