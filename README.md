# Trip Planner

A multi-agent trip planner built with LangChain. Given an origin city, a destination city and travel dates, it
finds the cheapest sensible one-way trip: transport to the airport, flight, and transport at the destination.
Every figure in the result carries its source and a reliability grade (A confirmed, B typical timetable, C not verified).

Everything is in one notebook, `trip_planner.ipynb`. Its first cells explain who does what and how far each part can be trusted.

## Set up

You need Python 3.12 and two free API keys.

1. Create the environment and install the exact versions the project was tested with.

   Windows:
   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install --no-deps -r requirements.lock
   ```
   macOS or Linux:
   ```
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```
   `requirements.lock` lists every package of the tested environment, which was Windows. On another system use
   `requirements.txt`: it pins the direct dependencies and lets pip choose the rest.

2. Copy `.env.example` to `.env` and fill in `GOOGLE_API_KEY` and `TAVILY_API_KEY`. The links to get them are in the file.

## Run

**In the notebook.** Open `trip_planner.ipynb`, choose the interpreter in `.venv` as the kernel, edit the `SETTINGS`
cell and run all cells. The last step lists the airports: tick the ones you do not want and press the button.
A search takes about two minutes.

**From the command line**, to check that the setup works:
```
.venv\Scripts\python scripts\run_headless.py
```
It plans the trip in the `SETTINGS` cell without the airport checklist, prints the result and saves the report.

## What is in this folder

| Path | What it is |
| :-- | :-- |
| `trip_planner.ipynb` | The program |
| `requirements.txt` | Direct dependencies, pinned |
| `requirements.lock` | Every package of the tested environment, pinned |
| `.env.example` | The keys the program needs, without values |
| `resources/airports.csv` | Snapshot of the OurAirports database (6 October 2026). Delete it to download the current one |
| `resources/terminals.json`, `resources/timezones.json` | Caches of terminal coordinates and time zones, filled as trips are planned |
| `reports/` | One Markdown report per run: result, sources, reasoning and timings |
| `scripts/run_headless.py` | Runs the notebook from the command line |

## What can and cannot be reproduced

The **environment** is reproducible: the Python version and every package are pinned, and the airport database is a
saved snapshot.

The **results** are not, and cannot be. Each run reads live data, so two runs a day apart can recommend different
trips:

- Flight prices (Kiwi, Ryanair) and coach prices (FlixBus) change by the hour.
- The models (Gemini) do not answer the same way twice. They only write the recommendation and the unverified
  estimates (grade C): flights, timetables, checks and ranking are computed by code.
- Timetables come from the week of the run, for the same weekday as the travel date.

The record of a run is its report in `reports/`. It keeps the figures, their sources and their grades as they were
at that moment.

## External services

The program depends on these services. None of them is under the project's control, and most are free with no guarantee.

| Service | Used for | Key |
| :-- | :-- | :-- |
| Google Gemini | Coordinator and the two subagents | `GOOGLE_API_KEY` |
| Tavily | Web search of the subagents | `TAVILY_API_KEY` |
| Kiwi (MCP server) | Flights | none |
| Transitous | Published timetables of trains, coaches and local transport | none |
| FlixBus, Ryanair | Prices confirmed for the travel date (unofficial endpoints, they can change without notice) | none |
| OpenStreetMap (Nominatim, Overpass), OurAirports, Wikidata | Cities, terminals, airports, airline sites | none |

The free tier of Gemini allows about 20 requests a day on the better coordinator model. After that the coordinator
falls back to a lighter model by itself.
