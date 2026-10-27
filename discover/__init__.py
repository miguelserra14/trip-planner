"""Discover: from one city, where can the traveller fly cheaply? A map of every destination with the cheapest flight
found to it from the airports around the city, like the "discover" pages of the low-cost companies.

A feature apart from the trip planner, and a draft. It uses some of the planner's code (places, Kiwi, the price
estimate) without changing anything in it, and the planner never imports it. What it makes stays in this folder.

    Discover.bat, or python -m discover.window               its own window: a city, the days, a button that opens the map
    python -m discover Coimbra 12/11/2026 19/11/2026          the same from the terminal, in the repository's folder
    python -m discover Coimbra 12/11/2026 --fast              Ryanair only: a few seconds, its destinations only
    python -m discover Coimbra 12/11/2026 --light             fewer requests to Kiwi
    pytest discover                                           its tests only (plain pytest runs them too)

Two sources (sources.py):
- Ryanair's fare finder: the cheapest fare to each of its destinations from an airport, in one request.
- Kiwi, one request per departure airport and country: its cheapest flights of every airline to that country.
  Kiwi answers at most 15 flights per request, so asking for each country is what brings the other airlines in:
  asked for "anywhere", its 15 cheapest were all Ryanair's.
A destination missing from the map may still have flights: an airline that is never among Kiwi's 15 cheapest to a
country is not there.

Every destination counts the ways to and from its airports, on both sides (access.py), each with its source:
- the traveller's side: the rides FlixBus and Rede Expressos sell for the day of each flight, at an hour that fits
  it (rides.py, grade A);
- the destination's side: the city each airport serves, from Wikidata (Marseille, not Marignane), and the prices
  the operators and the guides publish for the way into it, found by a web search, pointed at by a model and
  read and checked by code (transfers.py, grade B);
- what earlier maps looked up in the timetables, and an estimate from the distance only when nothing else is known.
So the dot of a city is its cheapest airport all included, and the map opens with prices that have a source.
The first map of a place asks a web search (Tavily) and a model (Gemini flash-lite) once per destination airport;
what they find is kept 90 days, so the next maps ask nothing. Wikimedia asks for a contact in each program's
name: one put in env/.env as WIKIMEDIA_CONTACT is sent to Wikidata, and nothing is sent without it. Then the cheapest destinations are looked up in the real timetables, a city at a
time, until no destination left can change them (confirm.py): with the planner's own code, without its models.
What a lookup finds is remembered by airport for the next maps, so each map makes the next ones more exact.

The trains and buses that go straight from the city are read from their published timetables (ground.py,
Transitous, grade B): next to a city a flight goes to, and as cities of their own, a purple ring on the map, when no
flight goes there. Their price for the days of the trip (ground_fares.py, grade A) is read where code can: FlixBus,
and the operators the planner prices (ÖBB, RegioJet, Trenitalia, MÁV, ALSA, Renfe and Ouigo...), for the cities that
can be among the map's cheapest, after the flights' timetables. A city priced is ranked with the flights; the others
(BlaBlaCar Bus, DB, SNCF, Eurostar...) keep their timetable. Above the map, filters (flights, those cities by train or
by bus, the total, the hours on board, the country) hide what the traveller does not want to see.

Kiwi is shared with the planner, so it is asked politely (finder.py): never again in a map after it refused a
request, and at most KIWI_DAILY times a day by all the maps together.

The map shows flights, not trips: it does not know whether the airport can be reached at that hour. A click on a
trip opens the planner's window on it (plan.py, through the page served by serve.py), which checks that in real
timetables. The planner is opened by a new process, so it still never imports this folder."""
