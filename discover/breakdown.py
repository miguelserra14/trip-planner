"""What was searched, and what may have been missed. Each section is {"title", "note", "header", "rows"}, shown
under the map and printed in the terminal.

The last section is the radar: the routes known to be flown from the traveller's airports that no answer
mentioned, each with the likely reason. The routes are OpenFlights' (openflights.org, public, last updated in
2014): routes opened since are not there and some of the ones there are no longer flown, so the radar points at
gaps to look into, it does not prove them."""
from collections import Counter
from typing import Any

from discover.finder import COUNTRIES_KM, Found, KIWI_REQUESTS
from discover.routes import airline_names, known_routes
from trip_planner.places import distance_km, load_airports
from trip_planner.sources import kiwi

NEARBY_KM = 100  # an airport this close to one on the map serves the same city: Stansted and Gatwick are 75 km apart

def section(title: str, header: list[str], rows: list[list[Any]], note: str = "") -> dict[str, Any]:
    return {"title": title, "note": note, "header": header, "rows": [[str(cell) for cell in row] for row in rows]}

def country_of(code: str, airports: dict[str, Any]) -> str:
    return (airports.get(code) or {}).get("country", "?")

def requests_section(found: Found) -> dict[str, Any]:
    rows = []
    for source in ("Ryanair", "Kiwi"):
        for airport in found.departures:
            searches = [s for s in found.searches if s["source"] == source and s["from"] == airport["code"]]
            if not searches:
                continue
            full = sum(1 for s in searches if source == "Kiwi" and len(s["flights"]) >= kiwi.RESULT_CAP)
            rows.append([source, airport["code"], len(searches), sum(not s["answered"] for s in searches), full,
                         sum(len(s["flights"]) for s in searches), len({f["to"] for s in searches for f in s["flights"]})])
    return section("Requests", ["Source", "From", "Requests", "No answer", "Full answers", "Flights", "Destinations"], rows,
                   f"A full answer is one with Kiwi's maximum of {kiwi.RESULT_CAP} flights, one per city: that country may have more cities "
                   "than it shows. Ryanair answers with all of its destinations at once.")

def countries_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    rows = []
    by_country: dict[str, list[dict[str, Any]]] = {}
    for search in found.searches:
        if search["source"] == "Kiwi":
            by_country.setdefault(search["to"], []).append(search)
    for country, searches in sorted(by_country.items(), key=lambda item: -sum(len(s["flights"]) for s in item[1])):
        flights = [f for s in searches for f in s["flights"]]
        airlines = Counter(f["airline"] for f in flights)
        main, count = airlines.most_common(1)[0] if airlines else ("-", 0)
        rows.append([country, len(searches), sum(not s["answered"] for s in searches),
                     sum(len(s["flights"]) >= kiwi.RESULT_CAP for s in searches), len(flights), len({f["to"] for f in flights}),
                     len(airlines), f"{main} {round(100 * count / len(flights)) if flights else 0}%",
                     sum(1 for f in flights if country_of(f["to"], airports) != country)])
    note = ("The main airline is the one with most of the country's flights: when it has nearly all of them, the other airlines "
            "to that country may only be dearer, or hidden behind it in a full answer. ")
    if found.countries_left_out:
        note += f"Not asked, beyond the {KIWI_REQUESTS} requests to Kiwi a map may make: {', '.join(found.countries_left_out)}. "
    if found.kiwi_options:
        note += f"Kiwi was asked for {found.kiwi_options}."
    return section("Kiwi, by country asked", ["Country", "Requests", "No answer", "Full", "Flights", "Destinations", "Airlines",
                                              "Main airline", "Elsewhere"], rows,
                   note + " Elsewhere counts the flights Kiwi gave to another country than the one asked.")

def airlines_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    on_map = Counter(f["airline"] for f in found.destinations)
    by_airline: dict[str, list[dict[str, Any]]] = {}
    for flight in found.flights:
        by_airline.setdefault(flight["airline"], []).append(flight)
    rows = [[airline, len(flights), len({f["to"] for f in flights}), len({country_of(f["to"], airports) for f in flights}),
             f"{min(f['price_eur'] for f in flights):.0f}", on_map.get(airline, 0), ", ".join(sorted({f["source"] for f in flights}))]
            for airline, flights in sorted(by_airline.items(), key=lambda item: -len(item[1]))]
    return section("Flights found, by airline", ["Airline", "Flights", "Destinations", "Countries", "Cheapest (EUR)", "Cheapest to a destination", "Source"],
                   rows, "Every flight of every answer, before the cheapest one to each destination was kept. The airline is the one of the "
                         "first flight of the trip.")

def destinations_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    by_country: dict[str, list[dict[str, Any]]] = {}
    for flight in found.destinations:
        by_country.setdefault(country_of(flight["to"], airports), []).append(flight)
    rows = [[country, len(flights), f"{min(f['total_eur'] for f in flights):.0f}", sum(1 for f in flights if f["stops"]),
             ", ".join(f"{airline} {count}" for airline, count in Counter(f["airline"] for f in flights).most_common())]
            for country, flights in sorted(by_country.items(), key=lambda item: -len(item[1]))]
    return section("On the map, by country", ["Country", "Destinations", "Cheapest total (EUR)", "With a stop", "Cheapest airline"], rows)

def radar_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    known = known_routes({airport["code"] for airport in found.departures}, airports)
    if known is None:
        return section("Radar: known routes not found", [], [], "OpenFlights could not be read: the radar was not checked.")
    names = airline_names()
    mine = {airport["code"] for airport in found.departures}
    seen = {f["to"] for f in found.flights}
    asked = {s["to"] for s in found.searches if s["source"] == "Kiwi"}
    full = {s["to"] for s in found.searches if s["source"] == "Kiwi" and len(s["flights"]) >= kiwi.RESULT_CAP}
    ryanair = {s["from"] for s in found.searches if s["source"] == "Ryanair" and s["answered"]}
    origin = found.place
    on_map = [f["to"] for f in found.destinations if f["to"] in airports]

    def nearby(code: str) -> str:
        """A destination on the map that serves the same city: Kiwi gives one airport per city."""
        here = airports[code]
        close = [(distance_km(here["latitude"], here["longitude"], airports[other]["latitude"], airports[other]["longitude"]), other)
                 for other in on_map]
        km, other = min(close, default=(999.0, ""))
        return f"{other}, {km:.0f} km away" if km <= NEARBY_KM else ""

    def reason(code: str, carriers: set) -> str:
        country = country_of(code, airports)
        airport = airports[code]
        if "FR" in carriers and ryanair and carriers == {"FR"}:
            return "Ryanair's own answer has no fare to it these days (or no longer flies it)"
        if nearby(code):
            return f"its city is on the map by another airport ({nearby(code)})"
        if not found.kiwi:
            return "Kiwi was not asked"
        if distance_km(origin.latitude, origin.longitude, airport["latitude"], airport["longitude"]) > COUNTRIES_KM or country not in asked and country not in found.countries_left_out:
            return f"its country, {country}, is not among the ones asked (beyond {COUNTRIES_KM} km, or no large airport)"
        if country in found.countries_left_out:
            return f"its country, {country}, was left out of the requests"
        if country in full:
            return f"Kiwi's answer for {country} was full: it may be among the cities not shown"
        return f"Kiwi was asked about {country} and did not give it: no flight those days, or the route is no longer flown"

    rows = sorted(([code, airports[code]["city"] or airports[code]["name"], country_of(code, airports),
                    ", ".join(sorted(names.get(carrier, carrier) for carrier in carriers)), reason(code, carriers)]
                   for code, carriers in known.items() if code not in seen), key=lambda row: (row[4], row[2], row[0]))
    found_carriers = {f["carrier"] for f in found.flights if f.get("carrier")}
    silent = sorted({names.get(carrier, carrier) for carriers in known.values() for carrier in carriers} - {names.get(c, c) for c in found_carriers})
    note = (f"{len(known)} destinations are known to be flown directly from {', '.join(sorted(mine))} (OpenFlights, 2014). "
            f"{len(known) - len(rows)} of them were found, {len(rows)} were not. "
            + (f"Airlines known to fly from there that appear in no answer: {', '.join(silent)}." if silent else "Every airline known to fly from there appears in some answer."))
    return section("Radar: known routes not found", ["Code", "Destination", "Country", "Airlines (2014)", "Likely reason"], rows, note)

def breakdown(found: Found) -> list[dict[str, Any]]:
    airports = load_airports()
    stops = Counter(min(f["stops"], 2) for f in found.flights)
    return [requests_section(found), countries_section(found, airports), airlines_section(found, airports),
            destinations_section(found, airports),
            section("Stops", ["Stops", "Flights found", "On the map"],
                    [[label, stops.get(number, 0), sum(1 for f in found.destinations if min(f["stops"], 2) == number)]
                     for number, label in ((0, "direct"), (1, "one"), (2, "two or more"))]),
            radar_section(found, airports)]

def as_text(sections: list[dict[str, Any]], rows: int = 12) -> str:
    """The sections for the terminal, each one cut to its first rows."""
    lines = []
    for part in sections:
        lines += ["", f"== {part['title']}"]
        if part["header"]:
            widths = [max(len(str(cell)) for cell in column)
                      for column in zip(part["header"], *(row for row in part["rows"][:rows]))]
            for row in [part["header"], *part["rows"][:rows]]:
                lines.append("  " + "  ".join(cell.ljust(width) if index == len(row) - 1 else cell[:40].ljust(min(width, 40))
                                              for index, (cell, width) in enumerate(zip(row, widths))))
            if len(part["rows"]) > rows:
                lines.append(f"  ... {len(part['rows']) - rows} more rows in the page")
        if part["note"]:
            lines.append("  " + part["note"])
    return "\n".join(lines)
