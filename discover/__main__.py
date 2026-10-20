"""Where to fly cheaply from a city, on a map. See __init__.py.

    python -m discover Coimbra 12/11/2026 19/11/2026                                one way, leaving between the two days
    python -m discover Coimbra 12/11/2026 14/11/2026 --back 15/11/2026 17/11/2026   there and back
    --fast        Ryanair only: a few seconds, its destinations only
    --max-stops N most stops each way of the flights Kiwi gives, 0 (direct only) by default
    --no-browser  make the map without opening it"""
import argparse
import asyncio
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from discover.breakdown import as_text, breakdown
from discover import finder
from discover.finder import discover
from discover.page import map_page
from discover.sources import Days
from trip_planner import current
from settings import SETTINGS
from trip_planner.places import geocode
from trip_planner.utils import plain

MAPS = Path(__file__).parent / "maps"   # the maps made, in this folder: nothing of the planner's is written to

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
    parser.add_argument("--max-stops", type=int, default=finder.MAX_STOPS, choices=range(0, 4), metavar="N", help=f"most stops each way of the flights Kiwi gives (default {finder.MAX_STOPS})")
    parser.add_argument("--no-browser", action="store_true", help="make the map without opening it")
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
    current.SETTINGS = SETTINGS   # the planner's code reads its language from here: the one of settings.py
    place = geocode(asked.city)
    found = asyncio.run(discover(place, days, fast=asked.fast, max_stops=asked.max_stops))
    sources = "Ryanair (all its destinations)" + (" and Kiwi (the cheapest of every airline, by country)" if found.kiwi else "")
    sections = breakdown(found)
    MAPS.mkdir(exist_ok=True)
    name = f"{plain(place.english_name).replace(' ', '-')}_{days.first}_{days.last}" + (f"_back_{days.back_first}_{days.back_last}" if days.round_trip else "")
    path = MAPS / f"{name}.html"
    path.write_text(map_page(found, sources, sections, SETTINGS.min_airport_minutes), encoding="utf-8")
    # Every request and every flight it answered, to look into afterwards
    path.with_suffix(".json").write_text(json.dumps({"days": str(days), "departures": found.departures, "searches": found.searches},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    for flight in found.destinations[:10]:
        back = f", back {flight['back']['date']}" if flight.get("back") else ""
        print(f"{flight['total_eur']:6.0f} EUR  {flight['to']}  from {flight['from']} on {flight['date']}{back}, {flight['airline']} ({flight['source']})")
    print(as_text(sections))
    print(f"\n{len(found.destinations)} destinations on the map: {path}\nEvery request and its flights: {path.with_suffix('.json')}")
    if not asked.no_browser:
        webbrowser.open(path.as_uri())
    return 0

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
