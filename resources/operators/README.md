# Operators' data

What a coach or train operator's booking system needs to be asked and does not give with each answer: where its
stops are, the names it searches by. One file per operator, read by its module in `trip_planner/sources/operators/`. To add an
operator of another country, put its file here, write its module there and add it to `RIDE_SOURCES` in `sources/fares.py`.

| File | What it is | How it is kept up to date |
| :-- | :-- | :-- |
| `rede_expressos_stops.json` | Every stop of Rede Expressos (Portugal) with its position: its site lists them without one. From the site's points of sale (most are at the coach terminal), the airport database and Nominatim | Worked out once, on 21 October 2026 |
| (none for Obilet) | Its places are asked by name during each search |  |
| `movelia_places.json` | The names of the places in Spain that Movelia searches by | Downloaded again when older than 30 days (`MOVELIA_PLACES_DAYS`) |
