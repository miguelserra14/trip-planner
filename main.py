"""Open the trip planner in the browser:           python main.py     (or double-click TripPlanner.bat)
Plan the trip in settings.py in the terminal:     python main.py --terminal
Check every service the program depends on:       python main.py --check
The languages there are:                          python main.py --languages
Add a language, translated by a model:            python main.py --add-language it
Remove a language:                                python main.py --remove-language it
Locate the terminal of every airport again:       python main.py --terminals all   (once a year is plenty)
Check whether the connections shown really ran:   python main.py --verify          (see trip_planner/tools/verify.py)
Ask again the ground prices over a day old:        python main.py --prices          (see trip_planner/tools/prices.py)

In the browser (app/) the trip is typed in a form that starts with what is in settings.py, and Discover's map is
there too; the program runs until its Quit button or until its terminal window is closed. In the terminal each trip
lists its airports and asks which ones to drop, and after the result it takes follow-up questions, until an empty line."""
import asyncio
import sys

def in_terminal(check: bool) -> int:
    from settings import EXTRA_TRIPS, SETTINGS
    from trip_planner import current
    from trip_planner.tools.check import check_setup
    from trip_planner.planner import ask_to_keep, follow_up, plan_all, responses, show_timings
    from trip_planner.sources import flight_search

    async def plan() -> int:
        # The trip in SETTINGS first, then the ones in EXTRA_TRIPS
        trips = [SETTINGS, *EXTRA_TRIPS]
        await flight_search.connect()
        await plan_all(trips)
        show_timings()
        if len(responses) < len(trips):
            return 1   # a trip failed, its error is printed above
        if sys.stdin.isatty():
            ask_to_keep()
            await follow_up()
        return 0

    current.SETTINGS = SETTINGS   # the texts are in the language of the first trip until a trip starts
    return asyncio.run(check_setup() if check else plan())

def languages(arguments: list[str]) -> int:
    """List, add or remove a language of the program: see trip_planner/texts/translate.py."""
    from trip_planner.texts import translate
    for flag, action in (("--add-language", translate.add_language), ("--remove-language", translate.remove_language)):
        if flag in arguments:
            code = arguments[arguments.index(flag) + 1:][:1]
            if not code:
                print(f"Which language? For example: python main.py {flag} it")
                return 1
            return action(code[0])
    return translate.show_languages()

def in_browser() -> int:
    try:
        from settings import SETTINGS
        from trip_planner import current
        from app import server
        from app.site import Site
    except Exception as error:
        # Usually a mistake in settings.py: said in the terminal window, which TripPlanner.bat then keeps open
        print(f"The trip planner could not start.\n\n{type(error).__name__}: {error}")
        return 1
    current.SETTINGS = SETTINGS   # the pages are in the language of settings.py
    server.serve(Site(SETTINGS))
    return 0

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:   # there is none when started without a terminal
            stream.reconfigure(encoding="utf-8", errors="replace")   # the Windows console is not UTF-8 by default
    arguments = sys.argv[1:]
    if {"--languages", "--add-language", "--remove-language"} & set(arguments):
        sys.exit(languages(arguments))
    if "--verify" in arguments:
        from settings import SETTINGS
        from trip_planner import current
        from trip_planner.tools import verify
        current.SETTINGS = SETTINGS   # some operators read the limits of a trip
        sys.exit(verify.main())
    if "--prices" in arguments:
        from settings import SETTINGS
        from trip_planner import current
        from trip_planner.tools import prices
        current.SETTINGS = SETTINGS   # the operators read the limits of a trip, and the texts its language
        sys.exit(prices.main())
    if "--terminals" in arguments:
        from trip_planner.sources import timetables
        sys.exit(1 if timetables.fill_terminals(again="all" in arguments) else 0)
    sys.exit(in_terminal("--check" in arguments) if {"--check", "--terminal"} & set(arguments) else in_browser())
