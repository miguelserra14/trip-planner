"""python -m discover CITY FIRST_DAY [LAST_DAY] [--fast] [--no-browser], the days as dd/mm/yyyy. See __init__.py."""
import asyncio
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path

from discover.finder import discover
from discover.page import map_page
from trip_planner import current
from settings import SETTINGS
from trip_planner.places import geocode
from trip_planner.utils import plain

MAPS = Path(__file__).parent / "maps"   # the maps made, in this folder: nothing of the planner's is written to

def main(arguments: list[str]) -> int:
    flags = {argument for argument in arguments if argument.startswith("--")}
    words = [argument for argument in arguments if not argument.startswith("--")]
    if not words:
        print(__doc__)
        return 1
    try:
        days = [datetime.strptime(day, "%d/%m/%Y").date().isoformat() for day in words[1:3]] or [date.today().isoformat()]
    except ValueError:
        print("The days are written dd/mm/yyyy, for example 12/11/2026.")
        return 1
    first_day, last_day = days[0], days[-1]
    current.SETTINGS = SETTINGS   # the planner's code reads its language from here: the one of settings.py
    place = geocode(words[0])
    destinations = asyncio.run(discover(place, first_day, last_day, fast="--fast" in flags))
    sources = "Ryanair (all its destinations)" if "--fast" in flags else "Ryanair (all its destinations) and Kiwi (the cheapest of every airline, by country)"
    MAPS.mkdir(exist_ok=True)
    path = MAPS / f"{plain(place.english_name).replace(' ', '-')}_{first_day}_{last_day}.html"
    path.write_text(map_page(place, destinations, first_day, last_day, sources), encoding="utf-8")
    for flight in destinations[:10]:
        print(f"{flight['total_eur']:6.0f} EUR  {flight['to']}  from {flight['from']} on {flight['date']}, {flight['airline']} ({flight['source']})")
    print(f"{len(destinations)} destinations on the map: {path}")
    if "--no-browser" not in flags:
        webbrowser.open(path.as_uri())
    return 0

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
