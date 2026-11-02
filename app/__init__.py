"""The trip planner in the browser: one app, served on this computer, for the planner and for Discover.

    python -m app

Today there are three interfaces: the planner's Tk window (trip_planner/gui), Discover's Tk window (discover/window.py)
and the map in the browser (discover/page.py), with a bridge between them (discover/plan.py starts the planner's
window in a new process). The map needs the browser anyway, so the app moves everything there, in phases, each one
usable on its own and leaving the Tk windows working until the last.

The package, part by part:
- server.py: serves the pages on 127.0.0.1 only, and tells the browser what they may do.
- site.py: what is answered at each path, and the token every POST must carry.
- fields.py: the form of a trip, every setting of TripSettings, as the window has them.
- search.py and job.py: a search in a process of its own, and the conversation with it.
- maps.py and map_job.py: a Discover map, in a process of its own, and its page served here.
- reports.py and report_page.py: the saved reports as pages.
- page.js: the only script of the app's own pages, which work without it.

1. The results (done). The saved reports as web pages, with real tables, colours, links and the line of each
   option drawn to scale.
2. A search from the browser (done). A form that starts the planner in a process of its own (job.py), because of
   the globals in trip_planner/current.py, and shows its progress and then its report (search.py). Every POST
   carries the token of the app's forms (site.py).
3. The whole form (done). The main card and every advanced setting, in the window's tabs, with its label and its
   hint (fields.py; TripSettings stays the only check); the calendar (page.js); "which Nazaré?" and the airports to
   untick, asked by the process and answered in the page; questions about the trip planned, to the same process,
   which keeps the conversation; keeping a report or putting it back; the language of the pages and of the trip.
   The page of a search is put in place by the script, without a reload. And the window's buttons for the
   program itself: the ground prices asked again when some are too old, the check of the services, a language
   added (each run by job.py, as a search is) or removed (by the app, once confirmed in the page).
4. Discover in the app (done). Discover's form at /discover; the map made by discover's own Session in a process of
   its own (map_job.py), which tells the app its page and each change instead of serving them; the page served
   by the app with what it alone may do (maps.MAP_POLICY: its inline script, Leaflet and the map's tiles). A trip
   of the map opens the planner's form in the app, filled in, by its key: nothing is searched until the traveller
   asks, and a search can run while the map goes on looking up. discover/page.py only learnt to link there.
   Left for phase 5, because discover/window.py and session.py hold changes not committed yet: removing the window,
   plan.py, serve.py and Discover.bat.
5. Without Tk. trip_planner/gui and its tests go, one .bat starts the app, the README says how to use it.

What goes, or changes, once the app does all of it (phase 5):
- Goes: trip_planner/gui/window.py, output.py and dialogs.py (the window, its Markdown text, the calendar and the
  place chooser, the language manager), tests/window, discover/window.py and discover/tests/test_window.py,
  discover/plan.py (a click opens the form in the same app), discover/serve.py and Session.serve (the app serves the
  map), main.py's in_window and its Tk error box, Discover.bat; TripPlanner.bat starts the app.
- Moves before that, because the app reads them from the windows today: the tabs, labels and hints of the
  settings (gui/advanced.py: TABS, LABELS, HINTS, CHANGES, MAIN_SETTINGS, shown) into app/fields.py; the colours
  (gui/style.py: THEME, LIGHT_COLOURS, which discover/page.py reads too) into app/report_page.py; how Discover's form
  reads its days (discover/window.py: read_days) into app/maps.py.
- Changes: discover/page.py, its HTML and script in Python strings, its button and its command for the planner,
  becomes a page of the app with its script in a file beside page.js, and maps.MAP_POLICY loses 'unsafe-inline';
  the ui_* and discover_win_* texts the windows alone used go (texts/languages), the ones the app shares stay; the
  README's "How to use it" describes the app.
- Stays: the terminal (main.py --terminal, --check, --verify, --prices; python -m discover, which then makes the map
  as a file only, as --no-browser does today, a served map being the app's), with
  planner.ask_to_keep, follow_up and places.ask_places and ask_airports, its text versions of the same questions;
  utils.LISTENERS, through which job.py hears the planner.

The app may import the planner and Discover; neither imports the app."""
