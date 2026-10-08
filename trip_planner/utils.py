"""Small helpers used everywhere: times, text, progress lines and timings."""
import re
import time
import unicodedata
from contextlib import contextmanager
from typing import Dict

def to_minutes(hhmm: str) -> int:
    """'HH:MM' -> minutes since midnight. Raises ValueError if the text is not a time."""
    match = re.fullmatch(r"([0-9]{1,2})[:hH]([0-9]{2})", (hhmm or "").strip())
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"'{hhmm}' is not a HH:MM time")
    return int(match[1]) * 60 + int(match[2])

def to_time(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

def as_duration(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}"

def plain(text: str) -> str:
    """Lowercase text without accents, to compare names written in different ways."""
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().strip()

def say(text: str) -> None:
    """Print a progress line."""
    print(text, flush=True)

# Where the time goes: seconds spent in each step of the current trip
TIMINGS: Dict[str, float] = {}

@contextmanager
def timed(step: str):
    started = time.time()
    try:
        yield
    finally:
        TIMINGS[step] = TIMINGS.get(step, 0.0) + time.time() - started

def message_text(message) -> str:
    """Return the text of a message, whether its content is a string or a list of content blocks."""
    if isinstance(message.content, str):
        return message.content
    return "".join(block.get("text", "") for block in message.content if isinstance(block, dict))



def show(markdown: str) -> None:
    """Show formatted text. It is Markdown, printed as it is."""
    print("\n" + markdown + "\n", flush=True)
