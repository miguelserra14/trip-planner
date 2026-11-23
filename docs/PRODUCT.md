# Trip Planner as a product

[ARCHITECTURE.md](ARCHITECTURE.md) says how it is built. This says what it is for, how it is measured, what it
leaves out and why it was built the way it is. [COMPETITION.md](COMPETITION.md) compares it with the tools people
already use.

## The problem

Flight search sites stop at the airport. For someone who lives two hours from one, the ticket is often the smaller
part of the trip and the riskier one: the coach to the airport may cost more than the flight, the last train may
leave before the plane lands, the cheap airport may be the wrong one. No site answers the question they actually
have: **what is the cheapest trip from my door to that door, on that day, that I can really make?** Today they answer
it by hand, with a flight search in one tab and four timetables in the others.

## Who it is for

Students on Erasmus first. They travel often, pay for it themselves, and many live in cities far from an airport
(Coimbra, two hours from Porto and Lisbon; Antwerp, between Brussels, Charleroi and Eindhoven). Their trips are
short and booked late, so the travel date is known and the timetables of that day matter. They are also the users
of the next tool on the list, round trips with lodging for groups (on standby).

## What it does

- **The planner**: from A to B on the dates given, up to five options from door to door, each part with its times,
  its price, where they come from and how far to trust them, and the link to book it.
- **Discover** (beta): from a city on a few free days, a map of every place reachable cheaply by plane, train or
  bus, priced door to door. A click opens the planner on that trip.

## How it is measured

The numbers come from `python main.py --funnel reports/sample`, which adds up what every report keeps. The sample
is 54 searches made from 10 to 23 November 2026 for trips from 3 to 12 December, from Antwerp (14), Coimbra (12),
Lisbon (11), Berlin (6), Ljubljana (6) and Moimenta da Beira (5), to destinations taken from Discover maps (the
cheapest 30 of each map, half looked up in the timetables and half estimated). It is not a random sample of trips
people make: destinations a map shows as cheap are over-represented. One search was left out because the network
dropped while it ran (Lisbon to Bristol, 23 November): a timetable with no answer must not count as a proof.

| Question | Measure | Now |
| :-- | :-- | :-- |
| Is the cheapest ticket the cheapest trip? (the claim the product rests on) | Share of trips where the cheapest flight found is not the cheapest trip door to door | 24 of 51 (47%). Shown beyond doubt in 12 of 51 (24%): the cheapest ticket cannot be connected, by a real timetable or operator (6), or another flight is cheaper door to door by more than every guessed euro of both trips (6, a median 13 EUR less, at most 19). The rest rests on a model's data (6), on the minimum reliability asked (3), or on a saving smaller than the euros guessed (3). |
| Does it catch what a flight site cannot? | Flights looked up that a real timetable or operator proves impossible to connect | 112 of 481 (23%); the cheapest ticket itself in 7 of 54 trips |
| Can the result be trusted? | Share of the recommended trip's price confirmed for the travel date (grade A) | Median 79% over 52 trips (the traveller's own six trips: 9%, mostly coach fares no system sells) |
| Do the lights deserve their colour? | Vehicles shown that really ran, checked on the day (`main.py --verify`) | Not known yet: the trips are from 3 to 12 December, checked after them |
| Is Discover's map honest? | Map price against the planner's for the same trip, door to door | 54 pairs: median +0%, 88% within 20% |
| Do the models behave? | Calls, failures, plan B, links a model wrote that code removed | 414 calls, 27 failed (7%), 9 answered by plan B; 151 links removed |

Two measures are still missing because they need other people: **whether the result changed a decision** (book a
different flight, leave from another airport, not go) and **whether people come back for a second trip**. They are
the questions of the first interviews with users.

## What it does not do

- **It does not book.** It gives the link of each part. Booking would mean handling payments, refunds and missed
  connections, which is another company.
- **It does not handle lodging.** Discover counts transport only. Lodging is the next tool, kept apart.
- **It does not cover the world.** Europe only, where timetables are published and operators can be read.
- **It does not price bags or seats.** A flight without a cabin bag is pointed out, not priced.
- **It is not a public website.** It runs on the traveller's computer, with their own free API keys.

## Decisions and their trade-offs

**Code ranks; models only fill gaps.** Finding flights, reading timetables and fares, checking connections and
ranking are code. A model only searches the web where no timetable or booking system reaches (an airport shuttle,
a local bus) and writes the recommendation in words. Why: the same search gives the same ranking, every rule has
a test, and a model's invented price cannot move a trip to the top. The cost: where no source exists, the program
knows less than a model would claim to, and says so instead of guessing confidently. Without any model the result
is still computed.

**Every figure carries a grade.** 🔵 confirmed for the travel date, 🟢 the typical timetable of the same weekday,
🟠 not verified. Nothing a model wrote is ever shown as confirmed, and a link it wrote is kept only if its site
appeared in a search result. Why: the traveller's real risk is not the price but the connection that does not
exist; a total with no source is worth little. The cost: a busier page, and a total that sometimes says "between
40 and 55 EUR". The grades are measured afterwards (`--verify`), so the lights can be held to account.

**A real timetable overrules a model.** When a published timetable shows a connection cannot be made, the trip is
dropped, whatever a model said. Why: this is what makes the product different from a flight search, and it is
provable. The cost: a typical timetable can be wrong on a holiday; those connections carry a warning.

**The closest alternatives instead of nothing.** When no trip respects every limit, it shows the closest ones, each
with what it breaks and the setting that would make it valid, in a fixed order of importance (arrival window, time
at the airport, stopovers, departure times). The dates, the airports allowed and what a real timetable says are
never given up. Why: an empty page teaches the traveller nothing, and most limits are preferences, not walls. The
cost: the page must make very clear that these trips break something.

**Cheaper but slower counts, within reason.** A flight from a far airport that saves a little money and costs
hours is left out, by a price per extra hour the traveller sets. Why: "cheapest" alone sends students on a
six-hour detour to save five euros. The cost: the rule hides some trips that are cheaper in money.

**Thorough before fast.** Ground transport is looked up in rounds until no flight left could still win, not for a
fixed number of the cheapest. Why: the best trip is often not among the cheapest tickets. The cost: a search takes
minutes. A light mode (a fixed 16 flights) is kept in the code to be offered as an option.

**On the traveller's own computer.** The app serves its pages only to that computer, and the keys never leave it.
Why: free API quotas and unofficial sources cannot carry a public site, and a local app has no accounts to protect.
The cost: it is hard to try, which is the next thing to change, for a few users first.

## What comes next

1. Check the lights: `--verify` once the sample's travel days are past (after 12 December 2026).
2. A handful of real users: friends on Erasmus reach the app on its author's computer through a Cloudflare Tunnel,
   behind a login by email, with their reports kept apart and a daily limit each. Five short interviews: how they plan
   today, a trip that went wrong, whether the result changed a decision. One or two changes from what they say,
   recorded in a changelog.
3. Speed: start the timetable lookups while the traveller is still choosing the airports.
