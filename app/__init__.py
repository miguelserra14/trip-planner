"""The trip planner in the browser: one app, served on this computer, for the planner and for Discover.

Today there are three interfaces: the planner's Tk window (trip_planner/gui), Discover's Tk window (discover/window.py)
and the map in the browser (discover/page.py), with a bridge between them (discover/plan.py starts the planner's
window in a new process). The map needs the browser anyway, so the app moves everything there, in phases, each one
usable on its own and leaving the Tk windows working until the last:

1. The results (done). The saved reports as web pages, with real tables, colours, links and the line of each
   option drawn to scale. This is the test of the whole path: if it is not clearly better than the window's text,
   the rest is not worth doing.
2. A search from the browser (done). A short form (origin, destination, days; the rest from settings.py) that
   starts the planner in a process of its own (job.py), because of the globals in trip_planner/current.py, and
   shows its progress and then its report (search.py). The server takes POST requests, each with the token of the
   app's forms (site.py). Still no script: the page of a running search refreshes itself.
3. The whole form: the calendar, the airports to leave out, the advanced settings with their hints and their
   ranges (TripSettings stays the only check), "which Nazaré?", the languages, keeping a report.
4. Discover in the app. The map becomes a page of the app, and a click on a trip opens the form filled in, in the
   same page: discover/plan.py as a process, discover/window.py and Discover.bat go.
5. Without Tk. trip_planner/gui and its tests go, one .bat starts the app, the README says how to use it.

The app may import the planner and Discover; neither imports the app."""
