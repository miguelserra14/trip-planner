"""What every test of the map shares: no request to Transitous for the trains and buses (ground.py), no list of
cities downloaded, no answer of Kiwi kept from another test (finder.KIWI_ANSWERS), no file of theirs written
outside the test's own folder, and the planner's settings of settings.py, as the app and the command start with,
in English, so that no test depends on another one having set them or on the language chosen. A test that wants them gives its
own Transitous and its own cities."""
import dataclasses

import pytest

from trip_planner.trip.trip_settings import settings_file

SETTINGS = settings_file().SETTINGS   # read first here, as the map reads it (discover/session.py imports it too)

from discover import finder
from discover.overland import ground, ground_fares
from trip_planner import current

@pytest.fixture(autouse=True)
def no_ground(monkeypatch, tmp_path):
    monkeypatch.setattr(current, "SETTINGS", dataclasses.replace(current.SETTINGS or SETTINGS, language="en"))
    monkeypatch.setattr(ground, "GROUND", tmp_path / "ground.json")
    monkeypatch.setattr(finder, "KIWI_ANSWERS", tmp_path / "kiwi_answers.json")
    monkeypatch.setattr(ground_fares, "RATES", tmp_path / "ground_rates.json")
    ground_fares.FLIXBUS_ANSWERS.clear()
    monkeypatch.setattr(ground.Transitous, "get", lambda self, path, **params: [] if path == "map/stops" else {})
    monkeypatch.setattr(ground, "cities", lambda: [])
