"""What every test of the map shares: no request to Transitous for the trains and coaches (ground.py), and no file
of theirs written outside the test's own folder. A test that wants them gives its own Transitous."""
import pytest

from discover import ground

@pytest.fixture(autouse=True)
def no_ground(monkeypatch, tmp_path):
    monkeypatch.setattr(ground, "GROUND", tmp_path / "ground.json")
    monkeypatch.setattr(ground.Transitous, "get", lambda self, path, **params: [] if path == "map/stops" else {})
