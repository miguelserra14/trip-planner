"""Where to fly cheaply from a city, on a map. See __init__.py.

    python -m discover Coimbra 12/11/2026 19/11/2026                                one way, leaving between the two days
    python -m discover Coimbra 12/11/2026 14/11/2026 --back 15/11/2026 17/11/2026   there and back
    --fast        Ryanair only: a few seconds, its destinations only
    --ground      no plane: the cities reached by train or coach only, no flight searched
    --light       fewer requests to Kiwi: the countries with most known routes only
    --max-stops N most stops each way of the flights Kiwi gives, 0 (direct only) by default
    --no-confirm  do not look the cheapest destinations up in the timetables: the ways to the airports stay estimates

The map is saved as a file in discover/maps, once its flights are found and again once the lookups end (confirm.py).
In the app (python -m app) the same map opens as it is made, and a click on a trip opens the planner's form."""
import argparse
import sys
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
    parser.add_argument("--ground", action="store_true", help="no plane: the cities reached by train or coach only")
    parser.add_argument("--light", action="store_true", help=f"at most {finder.KIWI_LIGHT_REQUESTS} requests to Kiwi instead of {finder.KIWI_REQUESTS}")
    parser.add_argument("--max-stops", type=int, default=finder.MAX_STOPS, choices=range(0, 4), metavar="N", help=f"most stops each way of the flights Kiwi gives (default {finder.MAX_STOPS})")
    parser.add_argument("--no-confirm", action="store_true", help="do not look the cheapest destinations up in the timetables")
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
    session = Session(asked.city, days, fast=asked.fast, light=asked.light, max_stops=asked.max_stops, ground_only=asked.ground)
    session.make()
    print(session.report(), flush=True)
    # The trains and buses are priced whether or not the flights are looked up in the timetables
    try:
        session.look_up(timetables=not asked.no_confirm)
    except KeyboardInterrupt:   # look_up saved what it found before it stopped
        pass
    print(session.report())
    return 0

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
