"""The routes known to be flown from an airport, from OpenFlights (openflights.org, public, last updated in 2014).
Routes opened since are not there and some of the ones there are no longer flown: they decide which countries Kiwi
is asked about first (finder.py) and what the radar looks for (breakdown.py), they are not taken as flights."""
import csv
from pathlib import Path
from typing import Any

import httpx

from trip_planner.config import USER_AGENT

OPENFLIGHTS = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/"
CACHE = Path(__file__).parent / "cache"   # the two files of OpenFlights, downloaded once
# A file is kept only when it looks like OpenFlights': this many rows at least, nearly all of them with the columns
# the code reads. An error page answered with 200 would otherwise be kept, and read, for ever.
MIN_ROWS = 1000
MIN_COLUMNS = 8
MIN_GOOD_SHARE = 0.95

def rows_of(text: str) -> list[list[str]] | None:
    """The rows of a file of OpenFlights, or None when the text is not one."""
    rows = list(csv.reader(text.splitlines()))
    good = sum(1 for row in rows if len(row) >= MIN_COLUMNS)
    return rows if len(rows) >= MIN_ROWS and good >= MIN_GOOD_SHARE * len(rows) else None

def openflights(name: str) -> list[list[str]] | None:
    """The rows of one file of OpenFlights ("routes.dat", "airlines.dat"), or None when it cannot be had."""
    path = CACHE / name
    if path.exists():
        rows = rows_of(path.read_text(encoding="utf-8", errors="replace"))
        if rows is not None:
            return rows
    try:
        response = httpx.get(OPENFLIGHTS + name, headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
        response.raise_for_status()
    except Exception:
        return None
    rows = rows_of(response.content.decode("utf-8", errors="replace"))
    if rows is not None:
        CACHE.mkdir(exist_ok=True)
        path.write_bytes(response.content)
    return rows

def airline_names() -> dict[str, str]:
    """Airline names by IATA code, the active airline first when a code was used by several."""
    names: dict[str, str] = {}
    for row in sorted(openflights("airlines.dat") or [], key=lambda row: row[7:8] != ["Y"]):
        if len(row) > 7 and len(row[3]) == 2 and row[3] not in names:
            names[row[3]] = row[1]
    return names

def known_routes(departures: set, airports: dict[str, Any]) -> dict[str, set] | None:
    """{destination: the airlines known to fly to it} from any of the departure airports, direct and not shared
    with another airline (codeshares), the departure airports themselves left out. None without OpenFlights."""
    routes = openflights("routes.dat")
    if routes is None:
        return None
    known: dict[str, set] = {}
    for row in routes:
        if len(row) > 7 and row[2] in departures and row[4] not in departures and row[6] != "Y" and row[7] == "0" and row[4] in airports:
            known.setdefault(row[4], set()).add(row[0])
    return known

def route_counts() -> dict[str, int] | None:
    """How many destinations each airport is known to fly to directly, codeshares left out: how much an airport
    is used, to choose the traveller's airports by. None without OpenFlights."""
    routes = openflights("routes.dat")
    if routes is None:
        return None
    destinations: dict[str, set] = {}
    for row in routes:
        if len(row) > 7 and row[6] != "Y" and row[7] == "0":
            destinations.setdefault(row[2], set()).add(row[4])
    return {code: len(found) for code, found in destinations.items()}
