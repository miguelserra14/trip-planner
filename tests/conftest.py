"""`pytest` replays the checkpoints saved in the tests folders: no network, a few seconds.
`pytest --live` asks the real services again and saves new checkpoints: a few minutes.
`pytest --live -k BRU` does that for one airport."""
from datetime import date, timedelta

import pytest

from trip_planner import current
from trip_planner.config import TripSettings

def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="ask the real services and save new checkpoints")

@pytest.fixture(scope="session")
def live(request) -> bool:
    return request.config.getoption("--live")

@pytest.fixture(autouse=True)
def settings() -> TripSettings:
    """A trip far enough ahead to be looked up in a typical timetable, with the default limits."""
    day = date.today() + timedelta(days=70)
    current.SETTINGS = TripSettings(origin="Coimbra", destination="Antwerp", dates=[f"{day:%d/%m/%Y}"],
                                    earliest_leave="", latest_leave="")
    return current.SETTINGS
