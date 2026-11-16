"""The app may import the planner and Discover; neither imports the app, so that each still runs from the terminal
on its own (main.py --terminal, python -m discover). main.py is the door to all three: it may. No network."""
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]

def test_app_never_imported():
    code = [*(ROOT / "trip_planner").rglob("*.py"), *(ROOT / "discover").rglob("*.py"), ROOT / "settings.py"]
    assert not [path.relative_to(ROOT).as_posix() for path in code
                if re.search(r"^\s*(from|import) app\b", path.read_text(encoding="utf-8"), re.M)]
