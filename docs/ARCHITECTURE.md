# How Trip Planner is built

For the people who read or change the code. How to install and use the program is in the [README](../README.md).

## The rules it is built on

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

## How a trip is planned

```
 browser (app/) or terminal ── TripSettings, checked
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

A trip asked without a plane (`ground_only`) chooses no airport and searches no flight: its steps are the whole way by
train or coach and its ranking (`steps.GROUND_STEPS`), with no model. A round trip of that kind keeps the traveller at
least `min_stay_hours` at the destination: on a day trip the way back is searched from the first arrival plus that stay.

While a search runs, the planner says where it is as data (`utils.progress`): the trip and its airports, each step
that starts and what it found. The app draws it (app/waiting.py) on a map of Natural Earth's land, with about how long
is left from the searches before (`reports/durations.json`).

## How a Discover map is made

```
 browser (app/maps.py) or terminal ── session.py
            │
            ▼
 1. the airports around the city with the most routes (finder.py, flown.py)
 2. every destination from them: Ryanair's fare finder, Kiwi by country (sources.py), within a daily limit
 3. the way to and from the airports on both sides (access.py, rides.py, transfers.py)
 4. the cities reached straight by train or bus (ground.py, Transitous)
            │
            ▼
 the page (page.py), served by the app ── or saved as a file, from the terminal
            │  then, while the page redraws itself:
            ▼
 5. the trains and buses priced for the days (ground_fares.py, the planner's operators)
 6. the cheapest destinations looked up in the real timetables (confirm.py, the planner's code, no model)
            │
            ▼
 a click on a trip ── the planner's form in the app, filled in with it: nothing searched until asked
```

Discover may import the planner; the planner never imports Discover. The app imports both, and neither imports the
app. A search and a map are each made in a process of their own because the planner's code reads the trip being
planned from module globals (`trip_planner/current.py`).

## How the app works

```
 browser ── 127.0.0.1 only ── server.py, site.py   every page; a POST must carry the token of the app's own forms
                                   │
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
 a search: job.py, a process of its own     a map: job.py → map_job.py, a process of its own
   which place? which airports? questions     its page, its destinations as they change, its trips
                │                                     │
                └──────── JSON lines, both ways ──────┘
                                   │
                                   ▼
 the reports (reports.py, report_page.py)   the saved Markdown as pages: tables, colours, each option to scale
```

## How the code is organised

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
│   ├── places.py          cities (OpenStreetMap) and airports (OurAirports), chosen by distance and by the hours by road
│   └── form.py            how a form shows the settings: the main card, the tabs, the labels and hints
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
│   ├── formatting.py      tables, grades and sources
│   └── icons.py           the icons of every page, the app's and Discover's map, and the icon of each vehicle
└── tools/             the maintenance commands of main.py
    ├── check.py           --check: whether every outside service answers
    ├── verify.py          --verify: whether the connections of the kept reports really ran
    └── prices.py          --prices and Refresh prices: the ground prices over a day old, asked again before booking

app/
├── __main__.py        python -m app: the app served, the browser opened
├── server.py          the pages served on 127.0.0.1 only, and what each may do (its security headers)
├── remote.py          optional: friends through a Cloudflare Tunnel, their Access assertion checked, the email naming them
├── friends.py         each friend's own app, in a process of its own, with their folders and daily limits
├── site.py            what is answered at each address, and the token every POST must carry
├── fields.py          the form of a trip: every setting of TripSettings, read back as the browser sends it
├── search.py          a search or a job in a process of its own, and the part of the page that follows it
├── job.py             that process: the search, its questions, the program's own jobs (check, prices, languages, a map)
├── maps.py            Discover's form, and its maps served by their address
├── map_job.py         a Discover map made in that process, told to the app as it changes
├── sample.py          python -m app.sample: a map, then the planner on some of its trips, to measure the map (reports/sample)
├── demo.py            main.py --demo: the app over the reports and maps of demo/, nothing searched; python -m app.demo copies one in
├── reports.py         the saved reports, listed, kept or put back
├── report_page.py     a report as a page
├── results.py         a report as cards, from its options as data; each ground leg drawn vehicle by vehicle
├── waiting.py         the page while a search runs: the trip on a map, its steps, about how long is left
├── land.json          Natural Earth 1:110m land (public domain), the map of waiting.py
├── page.js            the only script of the app's pages, which work without it: the calendar, the parts that change
└── tests/             its tests; pytest --browser also runs them in Edge or Chrome

discover/
├── __main__.py        a map from the terminal, saved as a file
├── session.py         one map, from the city typed to the page saved and improved
├── finder.py          the departure airports, every source asked, the cheapest way to each destination; Kiwi's daily limit
├── confirm.py         the cheapest destinations checked in the real timetables, with the planner's code
├── flights/           the flights of a map
│   ├── sources.py         Ryanair's fare finder and Kiwi asked by country
│   ├── regions.py         a few more requests for the regions of Europe with fewest cities found
│   ├── flown.py           the routes flown today from each airport (Wikipedia, through Wikidata): what the map may be missing
│   └── routes.py          the routes known from OpenFlights, to choose which countries to ask first
├── airports/          the way between each airport and its city, on both sides of the trip
│   ├── access.py          the best way known for each airport, with its grade
│   ├── rides.py           the coaches sold for the day between the traveller's city and its airports (FlixBus, Rede Expressos)
│   └── transfers.py       what it costs to get from an airport into its city, read from the operators' and guides' pages
├── overland/          the trips with no flight
│   ├── ground.py          the trains and buses that go straight from the city (Transitous)
│   └── ground_fares.py    their prices for the days of the trip, with the planner's operators
├── view/              what the traveller sees
│   ├── page.py            the map page (Leaflet and OpenStreetMap), and the trips of a map the planner can be opened on
│   ├── map.js, map.css    the page's script and its look
│   └── breakdown.py       what was searched and what may have been missed, under the map
└── tests/             its own tests, run by plain pytest too
```

## What is in this folder

| Path | What it is |
| :-- | :-- |
| `TripPlanner.bat` | Double-click to open the program in the browser. Sets up the environment the first time |
| `settings.py` | The trip the form starts with, and every setting the form does not show |
| `main.py` | Runs the program: in the browser, in the terminal (`--terminal`), or checks the setup (`--check`) |
| `trip_planner/` | The program, see above |
| `app/` | The program in the browser: the form, each search in a process of its own that asks which place and which airports and takes questions about the trip, the reports, the program's own jobs, and Discover's maps, a trip of a map opening the form filled in |
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
| `reports/saved/` | The trips you chose to keep, with **Keep this report**, a folder each as above. Never deleted, and not in the repository either |
| `reports/verify/` | The vehicles of the trips deleted to make room (`.json`), kept for `--verify` alone: whether a connection ran can only be known near its day, after newer searches have pushed its report out. Not in the repository |

## Tests

```
pip install -r env/requirements-dev.txt
pytest                          replays the saved checkpoints: no network, under a minute
pytest --live                   asks the real services again and saves new checkpoints: about fifteen minutes
pytest --live tests/departure   the same for one direction
pytest --live -k BRU            the same for one airport
pytest discover                 Discover's tests only
pytest app --browser            the app's tests, the pages also in Edge or Chrome
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

Discover's tests give made-up answers, except `discover/tests/test_checkpoints.py`: what Transitous answered for the
trains and buses straight from Coimbra and Antwerp, the coaches Rede Expressos sells from Coimbra and Movelia from
Madrid, Ryanair's fares from Porto and Charleroi, the routes of the Wikipedia tables of those two airports and
the cities Wikidata says some airports serve, saved in
`discover/tests/checkpoints/` with only the fields the code reads (`discover/tests/recorder.py`). They are replayed
through the map's own code and checked against facts a sound answer shows (Antwerp has trains to Brussels and
Amsterdam). `pytest --live discover/tests -k Antwerp` records one case again: two minutes of Transitous.

## What can and cannot be reproduced

The **environment** is reproducible: the Python version and every package are pinned, and the airport database is a
saved snapshot.

The **results** are not, and cannot be. Each run reads live data, so two runs a day apart can recommend different
trips:

- Flight prices (Kiwi, Ryanair) and coach prices (FlixBus, Rede Expressos, Movelia, Obilet) change by the hour.
  A report keeps when its prices were read: once they are over 24 hours old, **Refresh prices** on the first page (or
  `--prices`) asks the operators' booking systems again for the ground prices of its options, and adds what changed
  at the end of the report. The flights are not asked again.
- The models (Gemini) do not answer the same way twice. They only write the recommendation and the unverified
  estimates (grade C): flights, timetables, checks and ranking are computed by code.
- Timetables come from the week of the run, for the same weekday as the travel date.

The record of a run is its report in `reports/`. It keeps the figures, their sources and their grades as they were
at that moment.
