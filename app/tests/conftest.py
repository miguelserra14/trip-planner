"""What every test of the app shares: the planner's settings of settings.py, in English, so that no test depends on
another one having set them or on the language chosen, and no report of the traveller's: each test writes its own."""
import dataclasses

import pytest

from app import reports
from settings import SETTINGS
from trip_planner import current

@pytest.fixture(autouse=True)
def english(monkeypatch, tmp_path):
    monkeypatch.setattr(current, "SETTINGS", dataclasses.replace(current.SETTINGS or SETTINGS, language="en"))
    monkeypatch.setattr(reports, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(reports, "SAVED_REPORTS", tmp_path / "reports" / "saved")
