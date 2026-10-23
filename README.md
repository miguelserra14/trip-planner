# Trip Planner

A multi-agent trip planner built with LangChain. Given an origin city, a destination city and travel dates, it
finds the cheapest sensible one-way trip: transport to the airport, flight, and transport at the destination.
Every figure in the result carries its source and a reliability light (🔵 confirmed, 🟢 typical timetable, 🟠 not verified; A, B and C in the settings and the code).

The trip is described in `settings.py` and the program is the `trip_planner/` package, run through `main.py`.
`trip_planner/__init__.py` explains who does what and how far each part can be trusted.

## Run

**On Windows, double-click `TripPlanner.bat`.** A window opens:

1. Fill in the trip (origin, destination, dates) and press **Find airports**. The form starts with what is in `settings.py`.
2. Untick the airports you do not want and press **Search**. It takes two to four minutes, and the progress is shown.
3. Read the result in the window. Links open in the browser, and **Open the full report** opens the file saved in `reports/`.
   Only the reports of the last 5 runs stay there: tick **Keep this report** to move this one to `reports/saved/`, where nothing is deleted.
4. Ask about the trip in the box at the bottom. Questions are answered without searching again.

The first time, `TripPlanner.bat` creates the environment (a few minutes, Python 3.12 must be installed) and opens
`env/.env` for you to paste the free API keys: `GOOGLE_API_KEY`, which is required, and `TAVILY_API_KEY` and `MISTRAL_API_KEY`, which are optional. The links to get them are in that file.

**Check the services** in the window makes one small request to every service the program depends on and says
which ones answer. Use it whenever a search fails.

### When nothing fits

Every setting is checked before anything is searched: a date that does not exist or has passed, a time that is not
a time, limits that contradict each other. The reason is shown in the window, and settings that are allowed but will
probably disappoint (such as leaving at 06:30 and arriving by 09:00) are pointed out before the search starts.

If no trip respects every requirement, the program shows the closest ones instead, each with what it breaks, what
speaks for and against it, and the setting that would make it valid. They are ordered by how little they break,
by this order of importance:

1. Arriving within the window asked for
2. The minimum time at the airport (never under 45 minutes)
3. The number of stopovers
4. The times you accept to leave at

An option that breaks a requirement higher in the list always comes after one that only breaks lower ones. The
dates, the airports you allowed and what the real timetables say are never given up.

### How far the result can be trusted

- A typical timetable (🟢) is that of the same weekday of a nearer week. Holidays and the timetable change of
  mid-December make the day itself different: confirm those connections before booking.
- When an operator that can be read by code (FlixBus, ALSA) runs other rides on the day of the trip but not the one
  a timetable of another week shows, that connection is dropped: it does not run that day.
- Part of a total may be an estimate (a model's price, or one from the distance). The total then shows in brackets
  what the option may really cost, and the options that cannot be told apart on price are said to be tied.
- Whether the lights deserved it can be measured: every report keeps the vehicles of its options next to it (`.json`),
  and `main.py --verify` asks again, once the day of the trip is on sale or published, whether each one ran. Run it
  in the week before the trips: it counts, by light and by source, the vehicles that ran and the ones that did not.

### From the command line

```
env\.venv\Scripts\python main.py              the window
env\.venv\Scripts\python main.py --terminal   no window: the trip in settings.py, questions asked in the terminal
env\.venv\Scripts\python main.py --check      check the services
env\.venv\Scripts\python main.py --verify     whether the connections of the reports kept really ran (trip_planner/verify.py)
```

### Languages

Each language is one file in `trip_planner/languages/`, named after its code (`en.py`, `pt-PT.py`). Whatever file is
there is a language of the program: it appears in the window and can be used in `settings.py`, with nothing else to change.

In the window, **Languages...** next to the language list adds one (write its name or its code) and removes one.
Only `en.py` and `pt-PT.py`, written by hand, are in the repository: a language added by a model stays on your
computer (`.gitignore`), because it is made again with one click.
From the command line:

```
env\.venv\Scripts\python main.py --languages              the languages there are, and what is missing in each
env\.venv\Scripts\python main.py --add-language italiano  add one, by name or by code (it, fr, pt-BR)
env\.venv\Scripts\python main.py --remove-language it     remove one (or delete its file)
env\.venv\Scripts\python main.py --add-language all       translate what is missing in every language
```

A language is added by a model that translates `en.py`: it takes under a minute, and the result is checked by code
(nothing between `{braces}` or backticks may change). Read the file once, a model's translation is not proofread.
A text that a language does not have is shown in English, so a language still works while it is incomplete.
New texts are written in `en.py`; `--add-language all` then brings the other languages up to date.

### Set up by hand

`TripPlanner.bat` does this for you on Windows. Everything about the environment is in `env/`.

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

2. Copy `env/.env.example` to `env/.env` and fill in the two keys.

## What is in this folder

| Path | What it is |
| :-- | :-- |
| `TripPlanner.bat` | Double-click to open the program. Sets up the environment the first time |
| `settings.py` | The trip the form starts with, and every setting the form does not show |
| `main.py` | Runs the program: in a window, in the terminal (`--terminal`), or checks the setup (`--check`) |
| `trip_planner/` | The program, see below |
| `env/requirements.txt` | Direct dependencies, pinned |
| `env/requirements.lock` | Every package of the tested environment, pinned |
| `env/.env.example` | The keys the program needs, without values |
| `env/.python-version` | The Python version the project was tested with |
| `env/.env`, `env/.venv/` | Your keys and your environment. Created by you, not in the repository |
| `resources/airports.csv` | The airports with scheduled flights of the OurAirports database (snapshot of 8 October 2026). Delete it to download the current one |
| `resources/airfields.json` | Airports left out of the automatic choice because no airline flies from them (Viseu, Cascais...). Worked out once from OurAirports runways and OpenFlights routes. Delete it to work it out again |
| `resources/terminals.json` | Where the passenger terminal of every airport is, from OpenStreetMap, worked out once so that a search does not depend on it. `python main.py --terminals all` works it out again |
| `resources/operators/` | What the coach and train operators' booking systems need and do not give with each answer, one file per operator (see the README there) |
| `resources/timezones.json` | Cache of time zones, filled as trips are planned |
| `reports/` | The Markdown report of each of the last 5 runs: result, sources, reasoning and timings. Older ones are deleted. Next to each, its log (`.log`) and the vehicles of its options for `--verify` (`.json`) |
| `reports/saved/` | The reports you chose to keep, with **Keep this report** in the window. Never deleted |

## How the code is organised

```
trip_planner/
├── config.py          TripSettings: every setting of a trip and its default
├── current.py         the trip being planned, read by every other module
├── texts.py           reads the languages and gives every text the traveller reads in the right one
├── languages/         one file per language: en.py (the reference), pt-PT.py, it.py...
├── translate.py       adds, removes and checks languages
├── dates.py           travel dates as travellers write them
├── utils.py           times, text, progress lines, timings
├── places.py          cities (OpenStreetMap) and airports (OurAirports), chosen by distance and by the hours by road
├── sources/           where the data comes from, one module per outside service
│   ├── flight_search.py   the flight search: every route, on Kiwi first, then on Google Flights
│   ├── kiwi.py            flights from Kiwi
│   ├── google_flights.py  flights from Google Flights, when Kiwi does not answer
│   ├── timetables/        published timetables (Transitous), one module per part
│   │   ├── lookup.py          ground_transport_for: the ground transport of every flight, where a search starts
│   │   ├── client.py          Timetables: the requests, and the mixed legs where no timetable reaches one end
│   │   ├── joined.py          an operator's coach joined to a published connection to its station
│   │   ├── airport_stops.py   airport terminals (OpenStreetMap) and the stops travellers use there
│   │   ├── days.py            which day is read for a travel date, and the local time of each place
│   │   └── modes.py           the kinds of vehicle asked for
│   ├── fares.py           which operator prices which vehicle, and the operators that list rides (FARE_SOURCES, RIDE_SOURCES)
│   ├── operators/         one module per operator's booking system (FlixBus, ALSA, Movelia, Obilet...) and the city tariffs
│   ├── airlines.py        airlines' sites and confirmed fares (Ryanair)
│   └── web.py             web search for the subagents (Tavily), cleaning of what models write
├── agents/            the models
│   ├── subagents.py       airport transfer agent, destination transport agent, their prompts and limits
│   ├── steps.py           the three steps of a trip, run in order by code
│   ├── coordinator.py     the model that writes the recommendation, its prompt and its fallback between models
│   ├── schemas.py         the structured answers of the subagents
│   └── state.py           what each agent keeps between steps
├── itineraries/       code that decides the result
│   ├── legs.py            ground legs: real connection or checked estimate
│   ├── estimates.py       time and price estimated from the distance, where nothing better is known
│   ├── ranking.py         hard constraints, cost, ranking
│   └── selection.py       which flights get their ground transport looked up: the ones that can still win
├── report/            the Markdown reports
│   ├── summary.py         the result shown to the traveller
│   ├── full.py            the full report saved to reports/
│   └── formatting.py      tables, grades and sources
├── planner.py         plans one trip from start to finish, follow-up questions
├── gui.py             the window: form, airport list, progress, result, questions
└── check.py           the --check of every outside service
```

A trip goes through them in this order: `main.py` → `gui.py` (or the terminal) → `planner.py` runs the three steps (`agents/steps.py`) →
each step reads `sources/` → `itineraries/` checks and ranks → `report/` writes the result, shown at once → the coordinator
(`agents/coordinator.py`) is asked once for the recommendation in words. Without a model the result is still shown.

## Tests

```
pip install -r env/requirements-dev.txt
pytest                          replays the saved checkpoints: no network, a few seconds
pytest --live                   asks the real services again and saves new checkpoints: about fifteen minutes
pytest --live tests/departure   the same for one direction
pytest --live -k BRU            the same for one airport
```

The tests follow the ground transport between an airport and a place, in both directions: `tests/arrival/` from
the arrival airport to the destination, `tests/departure/` from the origin to the departure airport. The places
are in `tests/cases.py`: a city of every country of the European Union, and a small town of every country, far
from the airport, where what the country's regional trains and buses publish decides the result.

Each test is one stage: locating the place, the terminal, the airport's stops, the connections, and the leg the
itinerary gets. What the services answered at each stage is saved in the `checkpoints/` folder of each direction,
one file per place, so a failure says at which stage a country breaks and the stages can be run again on today's
code without asking anyone. A route that cannot work is marked with `known_gap` and its reason, such as Malta,
whose buses are in no published timetable.

## What can and cannot be reproduced

The **environment** is reproducible: the Python version and every package are pinned, and the airport database is a
saved snapshot.

The **results** are not, and cannot be. Each run reads live data, so two runs a day apart can recommend different
trips:

- Flight prices (Kiwi, Ryanair) and coach prices (FlixBus, Rede Expressos, Movelia, Obilet) change by the hour.
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
| Mistral | Optional plan B for the models: answers when the Gemini models fail or have no quota left | `MISTRAL_API_KEY` |
| Tavily | Web search of the subagents. When it does not answer, the timetables and the operators' prices are used on their own | `TAVILY_API_KEY` |
| Kiwi (MCP server) | Flights | none |
| Google Flights, through the `fast-flights` library | Flights when Kiwi does not answer. Not an official service: it reads the page a browser gets, so it can stop working or be refused without notice | none |
| Transitous | Published timetables of trains, coaches and local transport | none |
| FlixBus, Ryanair | Prices confirmed for the travel date (unofficial endpoints, they can change without notice) | none |
| Rede Expressos | The national coaches of Portugal, which publish no timetable: rides and prices for the travel date between a city and the airports of Lisbon, Porto and Faro, read with the requests its own site makes (unofficial too) | none |
| Movelia | The booking site of the Spanish coach companies (Monbus, Damas, Bacoma, Subus, ALSA...) and of Renfe: direct rides and prices for the travel date between a city and an airport of Spain, read with the requests its own site makes (unofficial too) | none |
| Obilet | The booking site of the coach companies of Turkey, which also sells those of Italy (Itabus, MarinoBus), Poland (Szwagropol, Majer), Moldova, Greece (the KTEL of the mainland), Croatia, Slovenia, Serbia, Montenegro, North Macedonia, Kosovo, Albania, Bosnia, Bulgaria and Romania: rides and prices for the travel date. Where the airport is not one of its stops, from the coach station of the airport's city, with the way there estimated (not verified). Read with the requests its own site makes (unofficial too) | none |
| ÖBB (Austria), Trenitalia and Trenord (Italy), MÁV (Hungary), Elron (Estonia), ALSA and Movelia (Spain: Renfe, Ouigo, coaches), RegioJet, FlixBus | Ticket prices of the trains and coaches found in the timetables, see `sources/operators/` (unofficial endpoints too). Public transport inside Luxembourg is free and priced as such. Everywhere else the price of ground transport is estimated from the distance, at a rate per country (`itineraries/legs.py`) | none |
| Eurostat | Price level of transport services per country, copied into the code: the rate per km of the countries where no railway price can be read | none |
| European Central Bank | Exchange rates, for prices in forints and crowns | none |
| PID (Prague), BKK (Budapest), STIB and De Lijn (Belgium), TEC (Charleroi airport bus), Carris and Metro (Lisbon) | Published tariffs of city networks, copied into `sources/operators/tariffs.py` with the page and the day each was read. A tariff is no longer used a year after it was read | none |
| OpenStreetMap (Nominatim, Overpass), OurAirports, Wikidata | Cities, terminals, airports, airline sites | none |
| OSRM (public server) | Hours by road from a city to its airports, so that one across a sea is left out (`max_road_hours`). Without it the airports are chosen by the straight line alone | none |

The free tier of Gemini allows about 20 requests a day on the better coordinator model. After that the coordinator
falls back to a lighter model by itself.
