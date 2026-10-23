"""Small helpers used everywhere: times, text, progress lines and timings."""
import re
import time
import unicodedata
from contextlib import contextmanager

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

# Progress lines and results are printed in the terminal, unless something else asked for them: the window (gui/window.py)
# puts its own functions here, under "say" and "show".
LISTENERS: dict = {}

def say(text: str) -> None:
    """A progress line, wherever the traveller is looking."""
    if "say" in LISTENERS:
        LISTENERS["say"](text)
    else:
        print(text, flush=True)

# Where the time goes: seconds spent in each step of the current trip
TIMINGS: dict[str, float] = {}

@contextmanager
def timed(step: str):
    started = time.time()
    try:
        yield
    finally:
        TIMINGS[step] = TIMINGS.get(step, 0.0) + time.time() - started

# The log of the run: every request to a service, with how long it took and how it ended. It is saved next to the
# report (report/full.py), so a slow or failed search can be read afterwards instead of being measured again.
REQUESTS: list[dict] = []

@contextmanager
def logged(service: str, what: str):
    """One request to a service, kept in the log of the run. The request may change entry["result"]: it stays
    "ok" (or starts with it) when the service answered, and becomes the error when the request raises."""
    entry = {"at": time.strftime("%H:%M:%S"), "service": service, "what": what, "seconds": 0.0, "result": "ok"}
    started = time.time()
    try:
        yield entry
    except BaseException as error:   # a request given up on (cancelled) is not an Exception
        entry["result"] = f"{type(error).__name__}: {error}".strip(": ")[:300]
        raise
    finally:
        entry["seconds"] = time.time() - started
        REQUESTS.append(entry)

def failed(entry: dict) -> bool:
    return not entry["result"].startswith("ok")

def message_text(message) -> str:
    """Return the text of a message, whether its content is a string or a list of content blocks."""
    if isinstance(message.content, str):
        return message.content
    return "".join(block.get("text", "") for block in message.content if isinstance(block, dict))



def show(markdown: str) -> None:
    """Formatted text, wherever the traveller is looking. It is Markdown: the terminal prints it as it is."""
    if "show" in LISTENERS:
        LISTENERS["show"](markdown)
    else:
        print("\n" + markdown + "\n", flush=True)
