# Trip Planner against the tools people already use

One question decides whether Trip Planner is worth anything: **when you leave from a city far from an airport, which
tool tells you what the whole trip costs and whether it can really be done?** Five tools are compared on the three
things that answer it. The comparison was made on 2026-11-17 from each tool's own pages, help centre and press
releases, not from searches on every route; coverage changes, so it should be checked again by hand before it is
quoted.

## The three things compared

1. **Airport transfer counted**: the way from where you are to the departure airport, and from the arrival airport
   to where you are going, is part of the trip and of its price, not left to you.
2. **Timetable confirmed for the travel date**: the times of every part are those of that day, not a typical day or a
   frequency ("every 30 minutes").
3. **Ground legs priced**: the bus or train parts have a real fare, not a range or nothing.

| | Transfer counted | Times for the travel date | Ground legs priced |
| :-- | :-- | :-- | :-- |
| **Google Flights** | No: airport to airport. Trains appear on some European routes as an alternative to the flight, not as a way to the airport | Flights yes; nothing else | Train fares only where a train replaces the flight |
| **Skyscanner** | No: flights, hotels and car hire. Trains are being tested on a few countries' routes, again as an alternative to flying | Flights yes | Only those test train routes |
| **Kiwi.com** | Partly: it joins flights, trains and buses it sells into one booking ("virtual interlining"), including some airport links (Lyon). A metro or local bus it does not sell is not counted | Yes, for what it sells | Yes, for what it sells |
| **Rome2Rio** | Yes: door to door, every mode, from anywhere to anywhere | Partly: some operators' schedules, otherwise a duration and a frequency | Mostly an indicative range; an exact fare only where a partner sells it |
| **Omio** | No: it sells trains, buses and flights, and suggests adding a transfer after a flight is booked, as a separate booking | Yes, for what it sells | Yes, for what it sells |
| **Trip Planner** | Yes: the trip is the house to the door, and it is ranked by that total | Graded on every part: 🔵 confirmed for the date (the operator's booking system), 🟢 the same weekday of a nearer week, 🟠 not verified | Yes where an operator can be read by code; otherwise an estimate, marked 🟠 and shown in brackets |

## Where Trip Planner wins

- **It ranks by the trip, not the ticket.** Over 35 trips of the sample (docs/PRODUCT.md), the cheapest ticket was
  not the cheapest trip door to door in about half of them: it could not be connected, or another flight cost less
  once the ways to and from the airports were added.
- **It proves what cannot be done.** A flight whose last bus leaves before it lands is dropped, with the timetable
  that shows it: 22% of the flights looked up in the sample were. A flight search site shows them all.
- **It says how far each figure can be trusted.** Rome2Rio, the only other door-to-door tool, gives a range and a
  frequency without saying which parts are firm. Here every part carries its grade, and the total says which share
  of it is confirmed.
- **It does not answer "nothing".** When no trip respects every limit, it shows the closest ones and what each one
  breaks.
- **Discover prices the whole trip on a map.** "Where can I go from Coimbra for 60 euros" counts the coach to Porto
  airport; flight search maps count from the airport.

## Where it loses

- **It books nothing.** Kiwi.com and Omio sell the whole trip in one payment, Kiwi.com with a guarantee for a missed
  connection. Here every part is booked on its own site, and a missed connection is the traveller's problem.
- **Coverage.** Europe only, and fares only where an operator's booking system can be read: Dutch and Belgian rail
  are estimates, Turkey's timetables are not published. Rome2Rio covers the world.
- **Speed.** A search takes two to seven minutes, against seconds for every other tool: it asks a free timetable
  service for every flight worth checking.
- **The flight price is not confirmed.** It is the price a flight search gives; only Ryanair is checked on its own
  site, and bags are not priced. Kiwi.com, Omio and the airlines' sites sell at the price they show.
- **It is hard to try.** It runs on the traveller's own computer, with Python and free API keys, and several of its
  sources are unofficial: fine for its author and a few friends, not for the public.

## What this means for the product

Trip Planner is not a booking site and should not try to become one: its value is the check before booking. The
closest competitor on that is Rome2Rio, and the difference is the grade on every figure and the proof that a
connection does not exist. The losses that matter most for the target user, a student far from an airport, are
speed and the setup: those are the next things to fix, not more operators.

Sources (read 2026-11-17): [Google: trains in Google Flights](https://blog.google/intl/de-de/produkte/suchen-entdecken/google-flights-mit-zugverbindungen-in-europa),
[Google: train and bus queries](https://support.google.com/travel/answer/14701381?hl=en),
[Skyscanner: trains](https://www.skyscanner.net/news/trains-on-skyscanner),
[Kiwi.com: how it works](https://www.kiwi.com/stories/this-is-how-kiwi-com-works/),
[Kiwi.com: Lyon ground-to-air](https://media.kiwi.com/?p=195),
[Rome2Rio: how prices are determined](https://help.rome2rio.com/en/support/solutions/articles/22000239374),
[Omio: how it works](https://www.omio.com/how-it-works), [Omio: about](https://omio.com/about).
