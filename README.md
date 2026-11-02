# Trip Planner

**From any city to any city in Europe, the cheapest trip that really works, door to door. And, when you have a few
free days, where you could go.**

Flight search sites stop at the airport. The cheapest flight is often not the cheapest trip: the airport may be two
hours away, the last bus may leave before you land, the coach to it may cost more than the flight. Trip Planner looks
at the whole journey: the bus or train to the departure airport, the flight, and the way from the arrival airport to
where you are going, on the day you travel. It reads the timetables the operators publish and the prices their
booking systems sell, drops the flights you could not catch, and ranks what is left by what it costs you in money
and in hours.

Every figure says where it comes from and how far to trust it: 🔵 confirmed for the travel date, 🟢 the typical
timetable of the same weekday, 🟠 not verified (A, B and C in the settings and the code). Code searches, checks
and ranks; AI models only search the web where no timetable reaches and write the recommendation in words, and
nothing they write is ever shown as confirmed.

Two tools share the same code:

- **The trip planner** (`TripPlanner.bat`): from A to B on the dates you give, one way or there and back. Up to
  five options, compared side by side, each with its timeline, its sources and the links to book every part.
- **Discover** (`Discover.bat`, a draft): from your city, on the free days you give, a map of every place you can
  reach cheaply, by plane, train or bus, with the way to and from the airports counted in the price. A click on a
  destination opens the planner on that trip, to check it in the real timetables.

## How to use it

### The first time (Windows)

1. Install [Python 3.12](https://www.python.org/downloads/) (tick "Add python.exe to PATH").
2. Get a free Google AI Studio key at <https://aistudio.google.com/apikey>. Two more keys are optional: Tavily (web
   search, <https://app.tavily.com>) and Mistral (a second model when Gemini has no quota left, <https://console.mistral.ai>).
3. Double-click `TripPlanner.bat`. It creates the environment, which takes a few minutes, then opens `env/.env` in
   Notepad: paste the keys after the `=` signs, save and close. The window opens.

Nothing else needs installing. Your keys stay on your computer (`env/.env` is not in the repository).
On macOS or Linux, see [Set up by hand](#set-up-by-hand).

### Plan a trip

1. Write where you leave from and where you are going, choose the dates in the calendar and press **Search**. The
   form starts with what is in `settings.py`. A city is found whatever language it is written in; when a name means
   two places, you are asked which.
2. The airports around both cities are listed: untick the ones you do not want and press **Search** again. It takes
   five to eight minutes, and the progress is shown.
3. Read the result in the window: the comparison first, then each option from door to door. Links open in the
   browser, and **Open the full report** opens the file saved in `reports/`, in a folder of its own for each trip.
   Only the last 5 trips stay there: tick **Keep this report** to move this one to `reports/saved/`, where nothing is deleted.
4. Ask about the trip in the box at the bottom ("and if I leave a day later?"). Questions are answered without searching again.
5. Prices change. Once a report's prices are over a day old, **Refresh prices** asks the coach and train operators
   again, before you book. Flight prices are not asked again: check them on the airline's site.

**Advanced settings** in the window, or `settings.py`, hold every other rule: how long before a flight to be at
the airport, by train or by bus only, how many changes, how much an extra hour of travel is worth to you. Point at
a setting to see what it means.

**Check the services** in the window makes one small request to every service the program depends on and says
which ones answer. Use it whenever a search fails.

### Find where to go (Discover)

1. Double-click `Discover.bat` (after `TripPlanner.bat` has created the environment once).
2. Write your city, click the days you could leave on and, for a return trip, the days you could come back on.
3. Press **Open the map**. The map opens in the browser in a minute or two: one dot per destination, its colour by
   the total price. Then it improves by itself for a few minutes, while the cheapest destinations are looked up in
   the real timetables and the trains and buses are priced. Keep the window open while you use the map.
4. Use the filters above the map (flights, trains, buses, total price, hours on board, country), click a dot to see
   the trip, and press the button there to open the planner on it.

The first map of a place takes longer than the next ones: what it finds about each airport is kept for 90 days.
Discover asks Kiwi, its source for every airline but Ryanair, at most 150 times a day for all the maps together, so
that the planner keeps working: after two or three maps in a day, the next ones show Ryanair's flights only, and say so.
Better not to make a map while the planner is searching: they use the same services.

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
- The recommendation in words is written by a model from the figures computed by code. The comparison table is
  what counts: when the two disagree, trust the table.
- Whether the lights deserved it can be measured: every report keeps the vehicles of its options next to it (`.json`),
  and `main.py --verify` asks again, once the day of the trip is on sale or published, whether each one ran. Run it
  in the week before the trips: it counts, by light and by source, the vehicles that ran and the ones that did not.

### What it does not do (yet)

- It plans trips with a flight. A trip by coach or train all the way is not compared with them in the planner
  (Discover does show the cities reached by train or bus).
- It books nothing: it gives the link of each part, to book on the operator's own site.
- Discover counts the transport only, not where to sleep.
- Dutch and Belgian rail fares are estimated from the distance: their operators do not let a program read them.
- Discover's window and map are in English only; the planner speaks every language of `trip_planner/texts/languages/`.
- The one-click launchers are for Windows.

### From the command line

```
env\.venv\Scripts\python main.py              the window
env\.venv\Scripts\python main.py --terminal   no window: the trip in settings.py, questions asked in the terminal
env\.venv\Scripts\python main.py --check      check the services
env\.venv\Scripts\python main.py --verify     whether the connections of the reports kept really ran (trip_planner/tools/verify.py)
env\.venv\Scripts\python main.py --prices     ask again the ground prices of the reports read over a day ago (trip_planner/tools/prices.py)

env\.venv\Scripts\python -m discover.window                         the Discover window
env\.venv\Scripts\python -m discover Coimbra 12/11/2026 19/11/2026  a map from the terminal, leaving between the two days
env\.venv\Scripts\python -m discover Coimbra 12/11/2026 14/11/2026 --back 15/11/2026 17/11/2026   there and back
env\.venv\Scripts\python -m discover Coimbra 12/11/2026 --fast      Ryanair only: a few seconds

env\.venv\Scripts\python -m app               the planner and Discover in the browser: the whole form, the search, questions about it, the reports, the maps (see app/)
```

`python -m discover --help` lists the other options (`--light`, `--max-stops`, `--no-confirm`, `--no-browser`).

### Languages

Each language is one file in `trip_planner/texts/languages/`, named after its code (`en.py`, `pt-PT.py`). Whatever file is
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
   macOS or Linux (the window needs Tk: `python3-tk` on Debian and Ubuntu):
   ```
   python3.12 -m venv env/.venv
   env/.venv/bin/python -m pip install -r env/requirements.txt
   ```
   `requirements.lock` lists every package of the tested environment, which was Windows. On another system use
   `requirements.txt`: it pins the direct dependencies and lets pip choose the rest.

2. Copy `env/.env.example` to `env/.env` and fill in the keys: `GOOGLE_API_KEY` is required, the others are optional.

## Architecture

### The rules it is built on

- **Code decides, models assist.** Finding flights, reading timetables and prices, checking that a connection can
  be made, and ranking are all done by code. Models do only what code cannot: search the web for the transport and
  the prices no timetable or booking system gives, and write the recommendation in words. Without any model the
  result is still computed and shown.
- **Every figure carries its source and a grade** (A, B, C). Nothing a model wrote is shown as confirmed. A link
  written by a model is kept only if its site appeared in a search result, and its text is stripped of markup.
- **A real timetable overrules a model.** When a published timetable shows a connection is impossible, the
  itinerary is discarded, whatever a model claimed.
- **One module per outside service.** An operator's booking system is one file in `sources/operators/` and one
  entry in `sources/fares.py`; adding a country's coaches touches nothing else.
- **Settings are checked once, at the start** (`TripSettings`): every number has an allowed range, and a bad value
  is explained in the traveller's language before anything is searched.

### How a trip is planned

```
 window (gui/) or terminal ── TripSettings, checked
            │
            ▼
 1. places (trip/places.py)       the two cities on OpenStreetMap; the airports around each, by distance and
            │                     by hours on the road (an airport across a sea is left out)
            ▼
 2. flights (sources/flight_search.py)   every departure airport × every arrival airport × every date,
            │                            on Kiwi, on Google Flights when Kiwi does not answer
            ▼
 3. ground transport, at the same time:
      ├─ real timetables (sources/timetables/, Transitous) and operators' prices (sources/fares.py)   → A or B
      ├─ airport transfer agent      ┐ models with web search, for what no timetable covers           → C
      ├─ destination transport agent ┘ (agents/subagents.py)
      └─ the whole trip by train or coach, no plane (sources/timetables/all_the_way.py), priced by the operators
    in rounds, cheapest flights first, until no flight left can beat the best option (itineraries/selection.py)
            │
            ▼
 4. checks and ranking (itineraries/)    impossible connections out, hard limits, total cost + the value of the
            │                            extra hours, the closest options when nothing fits; the trips without a
            │                            plane compared with the recommended one by the same rule (itineraries/ground_only.py)
            ▼
 5. report (report/)                     shown at once, saved to reports/ with its sources, log and vehicles
            │
            ▼
 6. coordinator (agents/coordinator.py)  one model call: the recommendation in words, its links, colours, durations
                                         and times checked against the options (agents/claims.py)
```

A round trip is two one-way trips, the way back on the same airports, then a summary that pairs them.

### How a Discover map is made

```
 window (discover/window.py) or terminal ── session.py
            │
            ▼
 1. the airports around the city with the most routes (finder.py, flown.py)
 2. every destination from them: Ryanair's fare finder, Kiwi by country (sources.py), within a daily limit
 3. the way to and from the airports on both sides (access.py, rides.py, transfers.py)
 4. the cities reached straight by train or bus (ground.py, Transitous)
            │
            ▼
 page served on 127.0.0.1 (page.py, serve.py) ── opens in the browser
            │  then, while the page redraws itself:
            ▼
 5. the trains and buses priced for the days (ground_fares.py, the planner's operators)
 6. the cheapest destinations looked up in the real timetables (confirm.py, the planner's code, no model)
            │
            ▼
 a click on a trip ── plan.py ── the planner's window, in a process of its own, with the form filled
```

Discover may import the planner; the planner never imports Discover. A map is made in a process of its own because
the planner's code reads the trip being planned from module globals (`trip_planner/current.py`).

### How the code is organised

```
trip_planner/
├── planner.py         plans one trip from start to finish, follow-up questions
├── current.py         the trip being planned, read by every other module
├── utils.py           times, text, progress lines, timings
├── shapes.py          what the dicts passed between modules hold: a vehicle, a connection, a side of a flight
├── trip/              what defines a trip
│   ├── trip_settings.py   TripSettings: every setting of a trip, its default and its check
│   ├── dates.py           travel dates as travellers write them
│   ├── ground_choice.py   by train, by bus or either, and how many changes, on each side of the flight
│   └── places.py          cities (OpenStreetMap) and airports (OurAirports), chosen by distance and by the hours by road
├── texts/             every text the traveller reads
│   ├── __init__.py        tr(): reads the languages and gives each text in the right one
│   ├── translate.py       adds, removes and checks languages
│   └── languages/         one file per language: en.py (the reference), pt-PT.py, it.py...
├── sources/           where the data comes from, one module per outside service
│   ├── flight_search.py   the flight search: every route, on Kiwi first, then on Google Flights
│   ├── kiwi.py            flights from Kiwi
│   ├── google_flights.py  flights from Google Flights, when Kiwi does not answer
│   ├── timetables/        published timetables (Transitous), one module per part
│   │   ├── lookup.py          ground_transport_for: the ground transport of every flight, where a search starts
│   │   ├── client.py          Timetables: the requests, and the mixed legs where no timetable reaches one end
│   │   ├── joined.py          an operator's coach joined to a published connection to its station
│   │   ├── all_the_way.py     the whole trip by train or coach: the timetables across the day, and FlixBus city to city
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
│   ├── claims.py          what a model wrote, checked by code: the colours it gives each part, its durations and times
│   ├── models.py          which model each agent runs on, plan B (Mistral) and the time limit of a call
│   ├── schemas.py         the structured answers of the subagents
│   └── state.py           what each agent keeps between steps
├── itineraries/       code that decides the result
│   ├── legs.py            ground legs: real connection or checked estimate
│   ├── estimates.py       time and price estimated from the distance, where nothing better is known
│   ├── ranking.py         hard constraints, cost, ranking
│   ├── ground_only.py     the trips without a plane: the traveller's limits, the operators' prices, compared with flying
│   └── selection.py       which flights get their ground transport looked up: the ones that can still win
├── report/            the Markdown reports
│   ├── summary.py         the result shown to the traveller
│   ├── full.py            the full report saved to reports/
│   └── formatting.py      tables, grades and sources
├── gui/               the window: form, airport list, progress, result, questions
│   ├── window.py          the main window, and the work it hands to the planner's thread
│   ├── output.py          the text where the progress and the result are written
│   ├── advanced.py        the advanced settings
│   ├── dialogs.py         which place a name means, the calendar, the languages
│   └── style.py           colours, fonts and the styles of the widgets
└── tools/             the maintenance commands of main.py
    ├── check.py           --check: whether every outside service answers
    ├── verify.py          --verify: whether the connections of the kept reports really ran
    └── prices.py          --prices and Refresh prices: the ground prices over a day old, asked again before booking

discover/
├── window.py          the Discover window: the city, the days, the button that opens the map
├── __main__.py        the same from the terminal
├── session.py         one map, from the city typed to the page served and improved
├── finder.py          the departure airports, every source asked, the cheapest way to each destination; Kiwi's daily limit
├── sources.py         Ryanair's fare finder and Kiwi asked by country
├── regions.py         a few more requests for the regions of Europe with fewest cities found
├── flown.py           the routes flown today from each airport (Wikipedia, through Wikidata): what the map may be missing
├── routes.py          the routes known from OpenFlights, to choose which countries to ask first
├── access.py          the way between each airport and its city, on both sides of the trip
├── rides.py           the coaches sold for the day between the traveller's city and its airports (FlixBus, Rede Expressos)
├── transfers.py       what it costs to get from an airport into its city, read from the operators' and guides' pages
├── ground.py          the trains and buses that go straight from the city, with no flight (Transitous)
├── ground_fares.py    their prices for the days of the trip, with the planner's operators
├── confirm.py         the cheapest destinations checked in the real timetables, with the planner's code
├── breakdown.py       what was searched and what may have been missed, under the map
├── page.py            the map page (Leaflet and OpenStreetMap)
├── serve.py           the page served on this computer only, and the button that opens the planner
├── plan.py            the planner's window opened on a trip of the map
└── tests/             its own tests, run by plain pytest too
```

### Security and privacy

- The keys are read from `env/.env`, which is in `.gitignore`, and are sent only to their own service. No key is
  written to a report or a log.
- Web pages are untrusted text: what the models bring back from them is cleaned, its links are kept only when they
  came from a search result, and its figures are graded C. The number of web searches is counted by code, not by the model.
- The Discover map is served on `127.0.0.1` only, answers only requests addressed to that address (no DNS
  rebinding), and opens the planner only for a trip of the map, by its key, with the page's random token: nothing a
  page sends becomes part of a command. The map's script (Leaflet) is checked against its known hash.
- Wikidata gets a contact address only if you put one in `env/.env` as `WIKIMEDIA_CONTACT`.
- Reports and maps stay on your computer. The ones in `reports/saved/` are in the repository: keep it private if
  your trips are.

## What is in this folder

| Path | What it is |
| :-- | :-- |
| `TripPlanner.bat` | Double-click to open the program. Sets up the environment the first time |
| `Discover.bat` | Double-click to open Discover, once the environment is there |
| `settings.py` | The trip the form starts with, and every setting the form does not show |
| `main.py` | Runs the program: in a window, in the terminal (`--terminal`), or checks the setup (`--check`) |
| `trip_planner/` | The program, see above |
| `app/` | The program in the browser, being built in phases to replace the two windows (the plan is in `app/__init__.py`). Today it has the whole form of the window, each search in a process of its own that asks which place and which airports, takes questions about the trip, and shows and keeps the reports; and Discover's maps, a trip of a map opening the form filled in |
| `discover/` | Discover, see above. Its maps go to `discover/maps/` and what it remembers to `discover/cache/`, both left out of the repository |
| `env/requirements.txt` | Direct dependencies, pinned |
| `env/requirements.lock` | Every package of the tested environment, pinned |
| `env/.env.example` | The keys the program needs, without values |
| `env/.python-version` | The Python version the project was tested with |
| `env/.env`, `env/.venv/` | Your keys and your environment. Created by you, not in the repository |
| `resources/airports.csv` | The airports with scheduled flights of the OurAirports database (snapshot of 8 October 2026). Delete it to download the current one |
| `resources/airfields.json` | Airports left out of the automatic choice because no airline flies from them (Viseu, Cascais...). Worked out once from OurAirports runways and OpenFlights routes. Delete it to work it out again |
| `resources/terminals.json` | Where the passenger terminal of every airport is, from OpenStreetMap, worked out once so that a search does not depend on it. `python main.py --terminals all` works it out again |
| `resources/airport_stops.json` | The stops travellers use at each airport, filled as trips are planned |
| `resources/operators/` | What the coach and train operators' booking systems need and do not give with each answer, one file per operator (see the README there) |
| `resources/timezones.json` | Cache of time zones, filled as trips are planned |
| `reports/` | One folder for each of the last 5 trips, older ones deleted. In it the Markdown report (result, sources, reasoning and timings), its log (`.log`) and the vehicles and prices of its options for `--verify` and `--prices` (`.json`). A round trip is one folder with both ways. Not in the repository |
| `reports/saved/` | The trips you chose to keep, with **Keep this report** in the window, a folder each as above. Never deleted |

## Tests

```
pip install -r env/requirements-dev.txt
pytest                          replays the saved checkpoints: no network, under a minute
pytest --live                   asks the real services again and saves new checkpoints: about fifteen minutes
pytest --live tests/departure   the same for one direction
pytest --live -k BRU            the same for one airport
pytest discover                 Discover's tests only
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
  A report keeps when its prices were read: once they are over 24 hours old, **Refresh prices** in the window (or
  `--prices`) asks the operators' booking systems again for the ground prices of its options, and adds what changed
  at the end of the report. The flights are not asked again.
- The models (Gemini) do not answer the same way twice. They only write the recommendation and the unverified
  estimates (grade C): flights, timetables, checks and ranking are computed by code.
- Timetables come from the week of the run, for the same weekday as the travel date.

The record of a run is its report in `reports/`. It keeps the figures, their sources and their grades as they were
at that moment.

## External services

The program depends on these services. None of them is under the project's control, and most are free with no guarantee.

| Service | Used for | Key |
| :-- | :-- | :-- |
| Google Gemini | Coordinator and the two subagents; Discover's reading of airport transfer pages | `GOOGLE_API_KEY` |
| Mistral | Optional plan B for the models: answers when the Gemini models fail or have no quota left | `MISTRAL_API_KEY` |
| Tavily | Web search of the subagents and of Discover. When it does not answer, the timetables and the operators' prices are used on their own | `TAVILY_API_KEY` |
| Kiwi (MCP server) | Flights. Discover asks it at most 150 times a day | none |
| Google Flights, through the `fast-flights` library | Flights when Kiwi does not answer. Not an official service: it reads the page a browser gets, so it can stop working or be refused without notice | none |
| Transitous | Published timetables of trains, coaches and local transport | none |
| FlixBus, Ryanair | Prices confirmed for the travel date (unofficial endpoints, they can change without notice). Ryanair's fare finder gives Discover all its destinations from an airport | none |
| Rede Expressos | The national coaches of Portugal, which publish no timetable: rides and prices for the travel date between a city and the airports of Lisbon, Porto and Faro, read with the requests its own site makes (unofficial too) | none |
| Movelia | The booking site of the Spanish coach companies (Monbus, Damas, Bacoma, Subus, ALSA...) and of Renfe: direct rides and prices for the travel date between a city and an airport of Spain, read with the requests its own site makes (unofficial too) | none |
| Obilet | The booking site of the coach companies of Turkey, which also sells those of Italy (Itabus, MarinoBus), Poland (Szwagropol, Majer), Moldova, Greece (the KTEL of the mainland), Croatia, Slovenia, Serbia, Montenegro, North Macedonia, Kosovo, Albania, Bosnia, Bulgaria and Romania: rides and prices for the travel date. Where the airport is not one of its stops, from the coach station of the airport's city, with the way there estimated (not verified). Read with the requests its own site makes (unofficial too) | none |
| ÖBB (Austria), Trenitalia and Trenord (Italy), MÁV (Hungary), Elron (Estonia), ALSA and Movelia (Spain: Renfe, Ouigo, coaches), RegioJet, FlixBus | Ticket prices of the trains and coaches found in the timetables, see `sources/operators/` (unofficial endpoints too). Public transport inside Luxembourg is free and priced as such. Everywhere else the price of ground transport is estimated from the distance, at a rate per country (`itineraries/legs.py`) | none |
| Eurostat | Price level of transport services per country, copied into the code: the rate per km of the countries where no railway price can be read | none |
| European Central Bank | Exchange rates, for prices in forints and crowns | none |
| PID (Prague), BKK (Budapest), STIB and De Lijn (Belgium), TEC (Charleroi airport bus), Carris and Metro (Lisbon) | Published tariffs of city networks, copied into `sources/operators/tariffs.py` with the page and the day each was read. A tariff is no longer used a year after it was read | none |
| OpenStreetMap (Nominatim, Overpass, map tiles), OurAirports, Wikidata, Wikipedia | Cities, terminals, airports, airline sites; for Discover, the routes flown from each airport and the map itself | none |
| OpenFlights, GeoNames | Discover: the routes known in 2014, to choose which countries to ask first, and the cities reached by train or bus | none |
| OSRM (public server) | Hours by road from a city to its airports, so that one across a sea is left out (`max_road_hours`). Without it the airports are chosen by the straight line alone | none |
| unpkg | Leaflet, the library that draws Discover's map, checked against its known hash | none |

The free tier of Gemini allows about 20 requests a day on the better coordinator model. After that the coordinator
falls back to a lighter model by itself.
