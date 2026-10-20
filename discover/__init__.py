"""Discover: from one city, where can the traveller fly cheaply? A map of every destination with the cheapest flight
found to it from the airports around the city, like the "discover" pages of the low-cost companies.

A feature apart from the trip planner, and a draft. It uses some of the planner's code (places, Kiwi, the price
estimate) without changing anything in it, and the planner never imports it. What it makes stays in this folder.

    python -m discover Coimbra 12/11/2026 19/11/2026          from the repository's folder
    python -m discover Coimbra 12/11/2026 --fast              Ryanair only: a few seconds, its destinations only
    pytest discover                                           its tests, apart from the planner's

Two sources (sources.py):
- Ryanair's fare finder: the cheapest fare to each of its destinations from an airport, in one request.
- Kiwi, one request per departure airport and country: its cheapest flights of every airline to that country.
  Kiwi answers at most 15 flights per request, so asking for each country is what brings the other airlines in:
  asked for "anywhere", its 15 cheapest were all Ryanair's.
A destination missing from the map may still have flights: an airline that is never among Kiwi's 15 cheapest to a
country is not there. The price of reaching each departure airport is an estimate from the distance."""
