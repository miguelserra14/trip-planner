"""Open the trip planner in a window:              python main.py     (or double-click TripPlanner.bat)
Plan the trip in settings.py in the terminal:     python main.py --terminal
Check every service the program depends on:       python main.py --check
The languages there are:                          python main.py --languages
Add a language, translated by a model:            python main.py --add-language it
Remove a language:                                python main.py --remove-language it

In the window the trip is typed in a form that starts with what is in settings.py. In the terminal each trip lists
its airports and asks which ones to drop, and after the result it takes follow-up questions, until an empty line."""
import asyncio
import sys

def in_terminal(check: bool) -> int:
    from settings import EXTRA_TRIPS, SETTINGS
    from trip_planner import current
    from trip_planner.check import check_setup
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
    """List, add or remove a language of the program: see trip_planner/translate.py."""
    from trip_planner import translate
    for flag, action in (("--add-language", translate.add_language), ("--remove-language", translate.remove_language)):
        if flag in arguments:
            code = arguments[arguments.index(flag) + 1:][:1]
            if not code:
                print(f"Which language? For example: python main.py {flag} it")
                return 1
            return action(code[0])
    return translate.show_languages()

def in_window() -> int:
    try:
        from settings import SETTINGS
        from trip_planner import gui
    except Exception as error:
        # Started by double-click there is no terminal to read the error in: usually a key missing in env/.env
        # or a mistake in settings.py
        import tkinter
        from tkinter import messagebox
        tkinter.Tk().withdraw()
        messagebox.showerror("Trip Planner", f"The trip planner could not start.\n\n{type(error).__name__}: {error}")
        return 1
    return gui.run(SETTINGS)

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:   # there is none when started without a terminal
            stream.reconfigure(encoding="utf-8", errors="replace")   # the Windows console is not UTF-8 by default
    arguments = sys.argv[1:]
    if {"--languages", "--add-language", "--remove-language"} & set(arguments):
        sys.exit(languages(arguments))
    sys.exit(in_terminal("--check" in arguments) if {"--check", "--terminal"} & set(arguments) else in_window())
