"""Where to fly cheaply from a city, on a map. See __init__.py.

    python -m discover Coimbra 12/11/2026 19/11/2026                                one way, leaving between the two days
    python -m discover Coimbra 12/11/2026 14/11/2026 --back 15/11/2026 17/11/2026   there and back
    --fast        Ryanair only: a few seconds, its destinations only
    --light       fewer requests to Kiwi: the countries with most known routes only
    --max-stops N most stops each way of the flights Kiwi gives, 0 (direct only) by default
    --no-confirm  do not look the cheapest destinations up in the timetables: the ways to the airports stay estimates
    --no-browser  make the map without serving or opening it: the timetables are looked up first, then it is saved

The map is served on this computer until Ctrl+C, so that a click on a trip opens the planner's window on it
(serve.py), while the cheapest destinations are looked up in the real timetables and the page redraws itself
(confirm.py). It is also saved as a file, again once the lookups end, which works without the planner's button."""
import argparse
import sys
import threading
import time
import webbrowser
from datetime import datetime

from discover import finder
from discover.session import Session
from discover.sources import Days

def day(text: str) -> str:
    try:
        return datetime.strptime(text, "%d/%m/%Y").date().isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a day written dd/mm/yyyy, such as 12/11/2026")

def read_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m discover", description="Where to fly cheaply from a city, on a map.")
    parser.add_argument("city")
    parser.add_argument("days", nargs="+", type=day, metavar="DAY", help="the first and the last day to leave on (dd/mm/yyyy); one day is that day only")
    parser.add_argument("--back", nargs="+", type=day, metavar="DAY", help="there and back: the first and the last day to fly back on")
    parser.add_argument("--fast", action="store_true", help="Ryanair only")
    parser.add_argument("--light", action="store_true", help=f"at most {finder.KIWI_LIGHT_REQUESTS} requests to Kiwi instead of {finder.KIWI_REQUESTS}")
    parser.add_argument("--max-stops", type=int, default=finder.MAX_STOPS, choices=range(0, 4), metavar="N", help=f"most stops each way of the flights Kiwi gives (default {finder.MAX_STOPS})")
    parser.add_argument("--no-confirm", action="store_true", help="do not look the cheapest destinations up in the timetables")
    parser.add_argument("--no-browser", action="store_true", help="make the map without serving or opening it")
    asked = parser.parse_args(arguments)
    if len(asked.days) > 2 or asked.back and len(asked.back) > 2:
        parser.error("give one or two days to leave on, and one or two to come back on")
    asked.days.sort()
    if asked.back:
        asked.back.sort()
        if asked.back[-1] < asked.days[0]:
            parser.error("the days to come back on are before the days to leave on")
    return asked

def main(arguments: list[str]) -> int:
    asked = read_arguments(arguments)
    days = Days(asked.days[0], asked.days[-1], *((asked.back[0], asked.back[-1]) if asked.back else ()))
    session = Session(asked.city, days, fast=asked.fast, light=asked.light, max_stops=asked.max_stops)
    session.make()
    if asked.no_browser:
        if not asked.no_confirm:
            session.look_up()
        print(session.report())
        return 0
    print(session.report())
    address = session.serve(looking_up=not asked.no_confirm)
    print(f"The map is served at {address}: a click on a trip there opens the planner on it. Ctrl+C to stop.", flush=True)
    webbrowser.open(address)
    if not asked.no_confirm:
        def looking() -> None:
            try:
                session.look_up()
            except Exception as error:   # the map stays as it is: the lookups are a refinement of it
                print(f"[discover] the timetables could not be looked up: {type(error).__name__}: {error}", flush=True)
            print(session.report(), flush=True)
        threading.Thread(target=looking, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
    return 0

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
