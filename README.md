# Trip Planner

A multi-agent trip planner built with LangChain. Given an origin city, a destination city and travel dates, it
finds the cheapest sensible one-way trip: transport to the airport, flight, and transport at the destination.
Every figure in the result carries its source and a reliability grade (A confirmed, B typical timetable, C not verified).

The trip is described in `settings.py` and the program is the `trip_planner/` package, run through `main.py`.
`trip_planner/__init__.py` explains who does what and how far each part can be trusted.

## Set up

You need Python 3.12 and two free API keys. Everything about the environment is in `env/`.

1. Create the environment and install the exact versions the project was tested with.

   Windows:
   ```
   python -m venv env\.venv
   env\.venv\Scripts\python -m pip install --no-deps -r env\requirements.lock
   ```
   macOS or Linux:
   ```
   python3.12 -m venv env/.venv
   env/.venv/bin/python -m pip install -r env/requirements.txt
   ```
   `requirements.lock` lists every package of the tested environment, which was Windows. On another system use
   `requirements.txt`: it pins the direct dependencies and lets pip choose the rest.

2. Copy `env/.env.example` to `env/.env` and fill in `GOOGLE_API_KEY` and `TAVILY_API_KEY`. The links to get them are in the file.

3. Check that everything answers:
   ```
   env\.venv\Scripts\python main.py --check
   ```
   It makes one small request to every service the program depends on and prints one line for each. Run it again
   whenever a search fails, to see which service is the problem.

## Run

Edit `SETTINGS` in `settings.py`, then:
```
env\.venv\Scripts\python main.py
```
It lists the airports found and asks which ones to drop: type their codes or names, or press Enter to keep them all.
A search takes two to four minutes. It then prints the result, saves the report and takes follow-up questions
about the trip, answered without searching again. Press Enter on an empty line to finish.

Set `confirm_airports=False` in `SETTINGS` to search every airport without being asked.

## What is in this folder

| Path | What it is |
| :-- | :-- |
| `settings.py` | The trip to plan. The only file you need to edit |
| `main.py` | Runs the program: plans the trip, or checks the setup with `--check` |
| `trip_planner/` | The program, see below |
| `env/requirements.txt` | Direct dependencies, pinned |
| `env/requirements.lock` | Every package of the tested environment, pinned |
| `env/.env.example` | The keys the program needs, without values |
| `env/.python-version` | The Python version the project was tested with |
| `env/.env`, `env/.venv/` | Your keys and your environment. Created by you, not in the repository |
| `resources/airports.csv` | Snapshot of the OurAirports database (8 October 2026). Delete it to download the current one |
| `resources/terminals.json`, `resources/timezones.json` | Caches of terminal coordinates and time zones, filled as trips are planned |
| `reports/` | One Markdown report per run: result, sources, reasoning and timings |

## How the code is organised

```
trip_planner/
├── config.py          TripSettings: every setting of a trip and its default
├── current.py         the trip being planned, read by every other module
├── texts.py           every text the traveller reads, in each language
├── utils.py           times, text, progress lines, timings
├── places.py          cities (OpenStreetMap) and airports (OurAirports), chosen by distance
├── sources/           where the data comes from, one module per outside service
│   ├── kiwi.py            flights
│   ├── timetables.py      published timetables (Transitous) and airport terminals
│   ├── operators.py       rides and prices from operators' booking systems (FlixBus)
│   ├── airlines.py        airlines' sites and confirmed fares (Ryanair)
│   └── web.py             web search for the subagents (Tavily), cleaning of what models write
├── agents/            the models
│   ├── subagents.py       airport transfer agent, destination transport agent, their prompts and limits
│   ├── steps.py           the three steps of a trip, as tools of the coordinator
│   ├── coordinator.py     the coordinator, its prompt and its fallback between models
│   ├── schemas.py         the structured answers of the subagents
│   └── state.py           what each agent keeps between steps
├── itineraries/       code that decides the result
│   ├── legs.py            ground legs: real connection or checked estimate
│   └── ranking.py         hard constraints, cost, ranking
├── report/            the Markdown reports
│   ├── summary.py         the result shown to the traveller
│   ├── full.py            the full report saved to reports/
│   └── formatting.py      tables, grades and sources
├── planner.py         plans one trip from start to finish, follow-up questions
└── check.py           the --check of every outside service
```

A trip goes through them in this order: `main.py` → `planner.py` → the coordinator (`agents/`) runs the three
steps → each step reads `sources/` → `itineraries/` checks and ranks → `report/` writes the result.

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
