"""The reports of the trips planned, in the browser, until Ctrl+C. See __init__.py.

    python -m app                the list of reports opens in the browser
    python -m app --no-browser   only served: the address is printed"""
import sys

from app import reports, server
from settings import SETTINGS
from trip_planner import current

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")   # the Windows console is not UTF-8 by default
    current.SETTINGS = SETTINGS   # the pages are in the language of settings.py
    server.serve(reports.answer, browser="--no-browser" not in sys.argv[1:])
