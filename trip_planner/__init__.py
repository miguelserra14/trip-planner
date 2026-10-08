"""Trip Planner

A multi-agent trip planner built with LangChain: a coordinator, two web-search subagents and code that checks what they say.
It plans a one-way trip from any city to any city: transport to the airport, flight, and transport at the destination.

To plan a trip, edit settings.py and run main.py. Nothing else needs to change.

Who does what, and where:

- Places and airports (code, places.py): the cities are located with OpenStreetMap, whatever language they are
  written in, and the airports are found by distance.
- Flight search (code, sources/kiwi.py): every departure airport against every arrival airport on every date, on
  Kiwi. Nothing is retyped by a model.
- Real timetables (code, sources/timetables.py and sources/operators.py): the ground transport of every flight is
  looked up in published timetables.
- Airport transfer agent and destination transport agent (models, agents/subagents.py): they search the web for
  prices, and for the transport that no timetable covers.
- Coordinator (model, agents/coordinator.py): supervises the steps (agents/steps.py) and writes the recommendation.
  The ranking itself is computed in code (itineraries/).
- Report (code, report/): the result shown to the traveller and the full report saved to reports/.

Data integrity: what can be trusted
Every figure in the report carries its source and one of three grades. Nothing a model wrote is ever shown as confirmed.

- A, confirmed for the travel date: read by code from the operator's own system (PRICE_CHECKERS, FlixBus), from a
  timetable published for that date, or from Kiwi for flight times.
- B, typical timetable: read by code from a published timetable, but for the same weekday of a nearer week, because
  the travel date is not published yet.
- C, not verified: found by a model in web search, or estimated by code from the distance.

- min_reliability (default B) keeps only itineraries whose schedules reach that grade. If none does, the lower ones
  are shown with a warning.
- When a real timetable exists and shows that a connection is impossible, the itinerary is discarded even if a model
  claimed it works.
- Text and links written by models are cleaned before they are shown: links are only kept when they came from a
  search result.
- The comparison shows how many euros of each total are confirmed.

Every run saves the full reasoning, the sources and the time each step took to reports/.
"""
from trip_planner import config
