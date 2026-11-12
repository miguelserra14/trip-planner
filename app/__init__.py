"""The trip planner in the browser: one app, served on this computer, for the planner and for Discover.

    python main.py               (or TripPlanner.bat) the app, the browser opened on it, until Quit or its window closed
    python -m app --no-browser   only served: the address is printed

The package, part by part:
- server.py: serves the pages on 127.0.0.1 only, and tells the browser what they may do.
- site.py: what is answered at each path, and the token every POST must carry.
- fields.py: the form of a trip, every setting of TripSettings, laid out by trip_planner/trip/form.py.
- search.py and job.py: a search in a process of its own, and the conversation with it.
- maps.py and map_job.py: a Discover map, in a process of its own, and its page served here.
- reports.py and report_page.py: the saved reports as pages: as cards (results.py) when they have their options as
  data (trip_planner/report/options.py), as their Markdown otherwise.
- page.css: the look of every page, light or dark; its icons are drawn in trip_planner/report/icons.py, shared with Discover.
- page.js: the only script of the app's own pages, which work without it.
- demo.py: main.py --demo, the app over the reports and maps kept in demo/, with no key and nothing searched.

A search and a map each have a process of their own (job.py), because the planner keeps the trip in globals
(trip_planner/current.py); the two talk to the app in JSON lines. A map is asked to stop, not killed, unless it does
not end in time (maps.STOP_SECONDS), and the caches are written whole (utils.write_whole), so that nothing stopped
halfway leaves a file cut short. The terminal stays as it was: main.py --terminal, --check, --verify, --prices and
python -m discover, with planner.ask_to_keep, follow_up and places.ask_places and ask_airports, the text versions of
the questions the app asks in its pages; utils.LISTENERS is how job.py hears the planner.

How it came to be (2026-10 to 2026-11), in phases each usable on its own, the Tk windows (trip_planner/gui,
discover/window.py, and discover/plan.py and serve.py between the map and the planner's window) working until the last:
1. The saved reports as web pages, with real tables, colours, links and the line of each option drawn to scale.
2. A search from the browser, in a process of its own, its progress and then its report; the token of every POST.
3. The whole form: the main card and every advanced setting, the calendar, "which Nazaré?" and the airports to
   untick, questions about the trip planned, keeping a report, the languages of the pages and of the trip, and the
   window's buttons for the program itself (the ground prices, the check of the services, the languages).
4. Discover in the app: its form, the map made by discover's own Session in its process, served by the app at an
   address of its own; a trip of the map opens the planner's form filled in, by its key, and searches nothing.
5. Without Tk, decided by the user on 2026-11-02: the app is closed by its small terminal window or its Quit button;
   python -m discover makes the map as a file only; the window's small extras came first (↔, "Advanced settings
   (n changed)", "Back to settings.py", the kind of trip). What the app read from the windows moved where it stays
   (trip/form.py, trip_settings.LIGHT_COLOURS, discover/sources.read_days, discover/page.trips); a review of what the
   windows did that the app did not brought the last few maps kept by address, a map asked to stop, a refused
   setting named as the form names it, an unforeseen error told in one line, and the missing key said at once.
   Then the windows went, with 33 texts only they used, and main.py and TripPlanner.bat start the app. Last, the
   map's script and style became files (discover/view/map.js, map.css), so that maps.MAP_POLICY runs no inline script,
   and the caches are written whole.

The app may import the planner and Discover; neither imports the app (app/tests/test_independence.py)."""
