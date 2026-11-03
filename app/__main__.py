"""The trip planner in the browser, until Ctrl+C. See __init__.py.

    python -m app                the form and the reports open in the browser
    python -m app --no-browser   only served: the address is printed"""
import sys

from trip_planner.trip.trip_settings import settings_file

SETTINGS = settings_file().SETTINGS   # before the app's modules: settings.py read with its passed dates moved

from app import server
from app.site import Site
from trip_planner import current

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")   # the Windows console is not UTF-8 by default
    current.SETTINGS = SETTINGS   # the pages are in the language of settings.py
    server.serve(Site(SETTINGS), browser="--no-browser" not in sys.argv[1:])
