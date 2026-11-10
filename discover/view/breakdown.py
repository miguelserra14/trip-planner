"""What was searched, and what may have been missed. Each section is {"title", "note", "header", "rows"}, shown
under the map and printed in the terminal.

The last section is the radar: the routes known to be flown from the traveller's airports that no answer
mentioned, each with the likely reason. The routes are OpenFlights' (openflights.org, public, last updated in
2014): routes opened since are not there and some of the ones there are no longer flown, so the radar points at
gaps to look into, it does not prove them."""
from collections import Counter
from typing import Any

from discover.flights import flown, regions
from discover.finder import COUNTRIES_KM, Found, KIWI_ANSWER_HOURS
from discover.flights.routes import airline_names, known_routes
from trip_planner.trip.places import distance_km, load_airports
from trip_planner.agents.models import CALL_SERVICE
from trip_planner.sources import kiwi
from trip_planner.texts import tr
from trip_planner.utils import failed

NEARBY_KM = 100  # an airport this close to one on the map serves the same city: Stansted and Gatwick are 75 km apart
# What the log of a run holds that is not a source of the map: a mistake of the program, an agent as a whole
NOT_SOURCES = ("program", "model")

def section(title: str, header: list[str], rows: list[list[Any]], note: str = "") -> dict[str, Any]:
    return {"title": title, "note": note, "header": header, "rows": [[str(cell) for cell in row] for row in rows]}

def country_of(code: str, airports: dict[str, Any]) -> str:
    return (airports.get(code) or {}).get("country", "?")

def source_health(found: Found, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each source the map asked, for the line of sources above it: {"name", "asked", "failed", "error", "why"}.
    error is the last failure, why says a source was not asked at all, or no more after it refused; a source with
    neither answered every request. Read from the log of the run (utils.REQUESTS), which every request of a map goes
    into; the sources that failed come first."""
    rows: dict[str, dict[str, Any]] = {}
    row = lambda name: rows.setdefault(name, {"name": name, "asked": 0, "failed": 0, "error": "", "why": ""})
    for entry in requests:
        if entry["service"] in NOT_SOURCES:
            continue
        kept = row(tr("discover_source_model") if entry["service"] == CALL_SERVICE else entry["service"])
        kept["asked"] += 1
        if failed(entry):
            kept["failed"] += 1
            kept["error"] = entry["result"][:160]
    if found.kiwi_why or found.kiwi_refused:
        row("Kiwi")["why"] = found.kiwi_why or tr("discover_source_refused", error=found.kiwi_refused)
    return sorted(rows.values(), key=lambda kept: (not (kept["failed"] or kept["why"]), kept["name"].lower()))

def requests_section(found: Found) -> dict[str, Any]:
    rows = []
    # Ryanair is asked airport by airport, Kiwi for several airports at once ("BRU,CRL,EIN"): one row for each
    for source in ("Ryanair", "Kiwi"):
        for start in dict.fromkeys(s["from"] for s in found.searches if s["source"] == source):
            searches = [s for s in found.searches if s["source"] == source and s["from"] == start]
            full = sum(1 for s in searches if source == "Kiwi" and len(s["flights"]) >= kiwi.RESULT_CAP)
            kept = [s["kept"] for s in searches if s.get("kept")]
            rows.append([source, start.replace(",", "+"), len(searches), sum(not s["answered"] for s in searches), full,
                         sum(len(s["flights"]) for s in searches), len({f["to"] for s in searches for f in s["flights"]}),
                         tr("discover_bd_kept_cell", count=len(kept), read=min(kept).replace("T", " ")) if kept else "-"])
    kept = sum(1 for s in found.searches if s.get("kept"))
    return section(tr("discover_bd_requests"), tr("discover_bd_requests_header").split("|"), rows,
                   tr("discover_bd_requests_note", cap=kiwi.RESULT_CAP)
                   + (" " + tr("discover_bd_requests_kept", count=kept, hours=KIWI_ANSWER_HOURS) if kept else ""))

def errors_section(found: Found) -> dict[str, Any]:
    """The requests without an answer, by source and error: a failure of the service, a refusal, and an answer
    the code could not read (the service changed) are not the same problem."""
    errors = Counter((s["source"], s.get("error") or tr("discover_bd_no_answer")) for s in found.searches if not s["answered"])
    note = tr("discover_bd_kiwi_refused", error=found.kiwi_refused) + " " if found.kiwi_refused else ""
    if not errors:
        return section(tr("discover_bd_errors"), [], [], note + tr("discover_bd_all_answered"))
    return section(tr("discover_bd_errors"), tr("discover_bd_errors_header").split("|"),
                   [[source, count, error] for (source, error), count in errors.most_common()], note)

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
    note = tr("discover_bd_countries_note") + " "
    if found.countries_left_out:
        note += tr("discover_bd_left_out", requests=found.kiwi_allowed, countries=", ".join(found.countries_left_out)) + " "
    if found.kiwi_options:
        note += tr("discover_bd_kiwi_options", options=found.kiwi_options)
    return section(tr("discover_bd_countries"), tr("discover_bd_countries_header").split("|"), rows,
                   note + " " + tr("discover_bd_elsewhere"))

def airlines_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    on_map = Counter(f["airline"] for f in found.destinations)
    by_airline: dict[str, list[dict[str, Any]]] = {}
    for flight in found.flights:
        by_airline.setdefault(flight["airline"], []).append(flight)
    rows = [[airline, len(flights), len({f["to"] for f in flights}), len({country_of(f["to"], airports) for f in flights}),
             f"{min(f['price_eur'] for f in flights):.0f}", on_map.get(airline, 0), ", ".join(sorted({f["source"] for f in flights}))]
            for airline, flights in sorted(by_airline.items(), key=lambda item: -len(item[1]))]
    return section(tr("discover_bd_airlines"), tr("discover_bd_airlines_header").split("|"), rows, tr("discover_bd_airlines_note"))

def destinations_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    by_country: dict[str, list[dict[str, Any]]] = {}
    for flight in found.destinations:
        by_country.setdefault(country_of(flight["to"], airports), []).append(flight)
    rows = [[country, len(flights), f"{min(f['total_eur'] for f in flights):.0f}", sum(1 for f in flights if f["stops"]),
             ", ".join(f"{airline} {count}" for airline, count in Counter(f["airline"] for f in flights).most_common())]
            for country, flights in sorted(by_country.items(), key=lambda item: -len(item[1]))]
    return section(tr("discover_bd_on_map"), tr("discover_bd_on_map_header").split("|"), rows)

def radar_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    known = known_routes({airport["code"] for airport in found.departures}, airports)
    if known is None:
        return section(tr("discover_bd_radar"), [], [], tr("discover_bd_radar_unread"))
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
        return tr("discover_bd_km_away", airport=other, km=f"{km:.0f}") if km <= NEARBY_KM else ""

    def reason(code: str, carriers: set) -> str:
        country = country_of(code, airports)
        airport = airports[code]
        if "FR" in carriers and ryanair and carriers == {"FR"}:
            return tr("discover_bd_why_ryanair")
        if nearby(code):
            return tr("discover_bd_why_nearby", nearby=nearby(code))
        if not found.kiwi:
            return tr("discover_bd_why_no_kiwi")
        if distance_km(origin.latitude, origin.longitude, airport["latitude"], airport["longitude"]) > COUNTRIES_KM or country not in asked and country not in found.countries_left_out:
            return tr("discover_bd_why_not_asked", country=country, km=COUNTRIES_KM)
        if country in found.countries_left_out:
            return tr("discover_bd_why_left_out", country=country)
        if country in full:
            return tr("discover_bd_why_full", country=country)
        return tr("discover_bd_why_none", country=country)

    rows = sorted(([code, airports[code]["city"] or airports[code]["name"], country_of(code, airports),
                    ", ".join(sorted(names.get(carrier, carrier) for carrier in carriers)), reason(code, carriers)]
                   for code, carriers in known.items() if code not in seen), key=lambda row: (row[4], row[2], row[0]))
    found_carriers = {f["carrier"] for f in found.flights if f.get("carrier")}
    silent = sorted({names.get(carrier, carrier) for carriers in known.values() for carrier in carriers} - {names.get(c, c) for c in found_carriers})
    note = (tr("discover_bd_radar_note", known=len(known), airports=", ".join(sorted(mine)), found=len(known) - len(rows), missed=len(rows)) + " "
            + (tr("discover_bd_radar_silent", airlines=", ".join(silent)) if silent else tr("discover_bd_radar_all_airlines")))
    return section(tr("discover_bd_radar"), tr("discover_bd_radar_header").split("|"), rows, note)

def lookups_section(found: Found) -> dict[str, Any]:
    """The cities looked up in the real timetables (confirm.py), and what their ways to and from the airports were."""
    if not found.lookups:
        return section(tr("discover_bd_lookups"), [], [], found.confirm_stop or tr("discover_bd_no_lookup"))
    rows = [[lookup["city"], ", ".join(lookup["airports"]), lookup["requests"], lookup["found"], lookup["impossible"], lookup["not covered"]]
            for lookup in found.lookups]
    note = tr("discover_bd_lookups_note") + (" " + tr("discover_bd_lookups_stop", stop=found.confirm_stop) if found.confirm_stop else "")
    return section(tr("discover_bd_lookups"), tr("discover_bd_lookups_header").split("|"), rows, note)

def coverage_section(found: Found, airports: dict[str, Any]) -> dict[str, Any]:
    """How many of the destinations flown today from the traveller's airports the map found, and the ones it did not,
    with who flies there (flown.py: the airports' Wikipedia articles)."""
    seen = found.coverage
    note = (tr("discover_bd_coverage_note", found=seen["found"], flown=seen["flown"], airports=", ".join(seen["airports"]),
               ryanair=seen["ryanair_no_fare"], missing=len(seen["missing"])) + " "
            + (tr("discover_bd_coverage_asked", count=len(seen["missing"]) - len(seen["not_asked"])) + " "
               if len(seen["missing"]) > len(seen["not_asked"]) else "")
            + (tr("discover_bd_coverage_not_asked", count=len(seen["not_asked"])) + " " if seen.get("not_asked") else "")
            + tr("discover_bd_coverage_seasonal", count=len(seen["missing_seasonal"])))
    gaps = {code: routes for code, routes in flown.missing(found.flown, set(), set()).items() if code in seen["missing"]}
    rows = [[code, (airports.get(code) or {}).get("city") or (airports.get(code) or {}).get("name", code), country_of(code, airports),
             ", ".join(sorted({route["airline"] for route in routes})), ", ".join(sorted({route["from"] for route in routes})),
             tr("discover_bd_seasonal") if code in seen["missing_seasonal"] else tr("discover_bd_all_year"),
             tr("no") if code in seen.get("not_asked", []) else tr("yes")]
            for code, routes in sorted(gaps.items(), key=lambda item: (country_of(item[0], airports), item[0]))]
    return section(tr("discover_bd_coverage"), tr("discover_bd_coverage_header").split("|"), rows, note)

def regions_section(found: Found) -> dict[str, Any]:
    """How many cities of each region of Europe within reach the map found, and the requests of its own each got."""
    asked = [row for row in found.regions if row["requests"]]
    note = (tr("discover_bd_regions_note", regions=regions.MAX_REGIONS, share=f"{regions.MIN_SHARE:.0%}", cities=regions.MIN_CITIES,
               requests=regions.REGION_REQUESTS, stops=regions.REGION_STOPS, asked=len(asked), km=regions.HOME_KM) if asked else
            tr("discover_bd_regions_enough") if found.kiwi else tr("discover_bd_regions_no_kiwi"))
    return section(tr("discover_bd_regions"), tr("discover_bd_regions_header").split("|"),
                   [[regions.region_name(row["region"]), row["large"], row["before"], row["requests"] or "-", row["after"]]
                    for row in found.regions], note)

def breakdown(found: Found) -> list[dict[str, Any]]:
    airports = load_airports()
    stops = Counter(min(f["stops"], 2) for f in found.flights)
    # What was missed comes first when today's routes are known; without them, the 2014 radar comes last
    current = [coverage_section(found, airports)] if found.coverage.get("flown") else []
    return [*current, *([regions_section(found)] if found.regions else []), lookups_section(found), requests_section(found), errors_section(found), countries_section(found, airports),
            airlines_section(found, airports), destinations_section(found, airports),
            section(tr("discover_bd_stops"), tr("discover_bd_stops_header").split("|"),
                    [[tr(label), stops.get(number, 0), sum(1 for f in found.destinations if min(f["stops"], 2) == number)]
                     for number, label in ((0, "discover_bd_direct"), (1, "discover_bd_one_stop"), (2, "discover_bd_two_stops"))]),
            *([] if current else [radar_section(found, airports)])]

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
                lines.append("  " + tr("discover_bd_more_rows", count=len(part["rows"]) - rows))
        if part["note"]:
            lines.append("  " + part["note"])
    return "\n".join(lines)
