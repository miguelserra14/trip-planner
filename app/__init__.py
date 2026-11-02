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
- reports.py and report_page.py: the saved reports as pages.
- page.js: the only script, which every page works without.

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
4. Discover in the app. The map becomes a page of the app, and a click on a trip opens the form filled in, in the
   same page: discover/plan.py as a process, discover/window.py and Discover.bat go.
5. Without Tk. trip_planner/gui and its tests go, one .bat starts the app, the README says how to use it.

What goes, or changes, once the app does all of it (phases 4 and 5):
- Goes: trip_planner/gui/window.py, output.py and dialogs.py (the window, its Markdown text, the calendar and the
  place chooser, the language manager), tests/window, discover/window.py and discover/tests/test_window.py,
  discover/plan.py (a click opens the form in the same app), discover/serve.py (app/server.py serves the map),
  main.py's in_window and its Tk error box, Discover.bat; TripPlanner.bat starts the app.
- Moves before that, because the app reads them from the window today: the tabs, labels and hints of the
  settings (gui/advanced.py: TABS, LABELS, HINTS, CHANGES, MAIN_SETTINGS, shown) into app/fields.py, and the
  colours (gui/style.py: THEME, LIGHT_COLOURS, which discover/page.py reads too) into app/report_page.py.
- Changes: discover/page.py, its HTML and script in Python strings and its own "open in the planner" button, becomes
  a page of the app with its script beside page.js; the ui_* texts the window alone used go (texts/languages),
  the ones the app shares stay; the README's "How to use it" describes the app.
- Stays: the terminal (main.py --terminal, --check, --verify, --prices), with planner.ask_to_keep, follow_up and
  places.ask_places and ask_airports, its text versions of the same questions; utils.LISTENERS, through which
  job.py hears the planner.

The app may import the planner and Discover; neither imports the app."""
