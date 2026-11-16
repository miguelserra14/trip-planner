"""Rules of style the code must keep, checked on the files themselves."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_TEST_WORDS = 4   # words after test_ in a test's name (CLAUDE.md, Style)

def test_names_have_four_words():
    long = [f"{path.relative_to(ROOT)}: {name}"
            for folder in ("tests", "app", "discover") for path in (ROOT / folder).rglob("test_*.py")
            for name in re.findall(r"\bdef test_(\w+)\(", path.read_text(encoding="utf-8"))
            if len(name.split("_")) > MAX_TEST_WORDS]
    assert not long, "test names longer than four words:\n" + "\n".join(long)
