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

- **The trip planner**: from A to B on the dates you give, one way or there and back. Up to five options, compared
  side by side, each with its timeline, its sources and the links to book every part.
- **Discover** (a draft): from your city, on the free days you give, a map of every place you can reach cheaply, by
  plane, train or bus, with the way to and from the airports counted in the price. A click on a destination opens
  the planner on that trip, to check it in the real timetables.

Both open in your browser, served by the program on your own computer only (`TripPlanner.bat`).

## How to use it

### The first time (Windows)

1. Install [Python 3.12](https://www.python.org/downloads/) (tick "Add python.exe to PATH").
2. Get a free Google AI Studio key at <https://aistudio.google.com/apikey>. Two more keys are optional: Tavily (web
   search, <https://app.tavily.com>) and Mistral (a second model when Gemini has no quota left, <https://console.mistral.ai>).
3. Double-click `TripPlanner.bat`. It creates the environment, which takes a few minutes, then opens `env/.env` in
   Notepad: paste the keys after the `=` signs, save and close. The program opens in your browser.

The program runs in a small window of its own, started minimised, which shows its address. Close that window, or
press **Quit** at the top of the page, to close the program. If a page is closed by mistake, the address in that
window opens it again.

Nothing else needs installing. Your keys stay on your computer (`env/.env` is not in the repository).
On macOS or Linux, see [Set up by hand](#set-up-by-hand).

### Plan a trip

1. Write where you leave from and where you are going, choose the dates in the calendar and press **Search**. The
   form starts with what is in `settings.py`. A city is found whatever language it is written in; when a name means
   two places, you are asked which.
2. The airports around both cities are listed: untick the ones you do not want and press **Search** again. It takes
   five to eight minutes, and the progress is shown.
3. Read the result: the comparison first, then each option from door to door, and the link to the full report,
   saved in `reports/`, in a folder of its own for each trip. Every report is listed on the first page. Only the last
   5 trips stay there: **Keep this report** moves one to `reports/saved/`, where nothing is deleted.
4. Ask about the trip in the box under the result ("and if I leave a day later?"). Questions are answered without
   searching again.
5. Prices change. Once a report's prices are over a day old, **Refresh prices** on the first page asks the coach and
   train operators again, before you book. Flight prices are not asked again: check them on the airline's site.

**Advanced settings**, under the form, or `settings.py`, hold every other rule: how long before a flight to be at
the airport, by train or by bus only, how many changes, how much an extra hour of travel is worth to you. The ones
that need it say what they mean under them, and **Back to settings.py** undoes every change.

**Check the services**, on the first page, makes one small request to every service the program depends on and
says which ones answer. Use it whenever a search fails.

### Find where to go (Discover)

1. Press **Discover** at the top of the page.
2. Write your city, click the days you could leave on and, for a return trip, the days you could come back on.
3. Press **Open the map**. In a minute or two the link to the map appears: one dot per destination, its colour by
   the total price. Then it improves by itself for a few minutes, while the cheapest destinations are looked up in
   the real timetables and the trains and buses are priced. A new map waits for the last one to save what it found;
   the pages of the last five maps keep working.
4. Use the filters above the map (flights, trains, buses, total price, hours on board, country), click a dot to see
   the trip, and follow the link there: the planner's form opens filled in with that trip, and nothing is searched
   until you press **Search**.

The first map of a place takes longer than the next ones: what it finds about each airport is kept for 90 days.
Discover asks Kiwi, its source for every airline but Ryanair, at most 150 times a day for all the maps together, so
that the planner keeps working: after two or three maps in a day, the next ones show Ryanair's flights only, and say so.
Better not to make a map while the planner is searching: they use the same services.

### When nothing fits

Every setting is checked before anything is searched: a date that does not exist or has passed, a time that is not
a time, limits that contradict each other. The reason is shown on the form, and settings that are allowed but will
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

- The trip by coach or train all the way is compared with flying, but only where a timetable reaches the two cities
  and an operator can be read for its price; otherwise it is shown without a price, or not at all.
- It books nothing: it gives the link of each part, to book on the operator's own site.
- Discover counts the transport only, not where to sleep.
- Dutch and Belgian rail fares are estimated from the distance: their operators do not let a program read them.
- The one-click launcher is for Windows: elsewhere, `python main.py` opens the program.

### From the command line

```
env\.venv\Scripts\python main.py              the program in the browser, until Quit or Ctrl+C (as TripPlanner.bat)
env\.venv\Scripts\python -m app --no-browser  the same, served without opening the browser: the address is printed
env\.venv\Scripts\python main.py --terminal   the trip in settings.py, in the terminal, questions asked there
env\.venv\Scripts\python main.py --check      check the services
env\.venv\Scripts\python main.py --verify     whether the connections of the reports kept really ran (trip_planner/tools/verify.py)
env\.venv\Scripts\python main.py --prices     ask again the ground prices of the reports read over a day ago (trip_planner/tools/prices.py)

env\.venv\Scripts\python -m discover Coimbra 12/11/2026 19/11/2026  a map as a file (discover/maps/), leaving between the two days
env\.venv\Scripts\python -m discover Coimbra 12/11/2026 14/11/2026 --back 15/11/2026 17/11/2026   there and back
env\.venv\Scripts\python -m discover Coimbra 12/11/2026 --fast      Ryanair only: a few seconds
env\.venv\Scripts\python -m discover Coimbra 12/11/2026 --ground    no plane: the cities the trains and coaches reach
```

`python -m discover --help` lists the other options (`--light`, `--max-stops`, `--no-confirm`). A map made this way
is saved once its timetables are looked up; it has no planner to open, only the trips.

### Languages

Each language is one file in `trip_planner/texts/languages/`, named after its code (`en.py`, `pt-PT.py`). Whatever file is
there is a language of the program: it appears in the list at the top of the page and can be used in `settings.py`,
with nothing else to change.

**More languages**, on the first page, adds one (write its name or its code) and removes one.
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

2. Copy `env/.env.example` to `env/.env` and fill in the keys: `GOOGLE_API_KEY` is required, the others are optional.

## Security and privacy

- The keys are read from `env/.env`, which is in `.gitignore`, and are sent only to their own service. No key is
  written to a report or a log.
- Web pages are untrusted text: what the models bring back from them is cleaned, its links are kept only when they
  came from a search result, and its figures are graded C. The number of web searches is counted by code, not by the model.
- The app is served on `127.0.0.1` only and answers only requests addressed to that address (no DNS rebinding).
  Whatever changes something (a search, an answer, a report kept, a language) must carry the random token of the
  app's own forms, which another site cannot read: no page elsewhere can start a search that spends your quota.
  Each page says what it may do (Content-Security-Policy). A trip of a map opens the planner's form by its key among
  that map's own trips, and fills it in only: nothing is searched until you ask. The map's script (Leaflet) is
  checked against its known hash.
- Wikidata gets a contact address only if you put one in `env/.env` as `WIKIMEDIA_CONTACT`.
- Reports and maps stay on your computer, and none of them goes in the repository, not even the ones kept in
  `reports/saved/`.

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

## Disclaimer

Trip Planner is a personal project, made to learn and for its author's own trips. It is not affiliated with,
endorsed by or connected to any of the airlines, operators or sites above; their names and trademarks belong to
their owners. Several of them are read through the requests their own sites make, not through an official service:
use the program for your own trips, at a gentle pace, and read the terms of each site before relying on it. A
source can change or refuse the program at any time, and then the result may be missing options.

Prices and timetables are read at one moment and change. Always check a trip on the operator's own site before
booking: the program books nothing and gives no guarantee about any figure it shows.

## How it is built

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the rules the program is built on, how a trip and a map are
made, how the code is organised, what is in each folder, and how to run the tests.

## License

[MIT](LICENSE).
