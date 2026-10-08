"""Plan the trip in settings.py:  python main.py
Check that every service the program depends on answers:  python main.py --check

Each trip lists its airports and asks which ones to drop; trips with `confirm_airports=False` start right away.
After the result it takes follow-up questions about the last trip, until an empty line."""
import asyncio
import sys

from settings import EXTRA_TRIPS, SETTINGS
from trip_planner import current
from trip_planner.check import check_setup
from trip_planner.planner import follow_up, plan_all, responses, show_timings
from trip_planner.sources import kiwi

async def main() -> int:
    # The trip in SETTINGS first, then the ones in EXTRA_TRIPS
    trips = [SETTINGS, *EXTRA_TRIPS]
    await kiwi.connect_kiwi()
    await plan_all(trips)
    show_timings()
    if len(responses) < len(trips):
        return 1   # a trip failed, its error is printed above
    if sys.stdin.isatty():
        await follow_up()
    return 0

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")   # the Windows console is not UTF-8 by default
    current.SETTINGS = SETTINGS   # the texts are in the language of the first trip until a trip starts
    sys.exit(asyncio.run(check_setup() if "--check" in sys.argv[1:] else main()))
