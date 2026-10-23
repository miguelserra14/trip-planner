"""Published tariffs of city networks, and the free public transport of Luxembourg."""
from collections.abc import Callable

from trip_planner.places import distance_km
from trip_planner.sources.operators.base import agency, FareSource, in_eur, Trip

def luxembourg_fare(trip: Trip) -> float | None:
    """Public transport inside Luxembourg is free for everyone, in second class."""
    return 0.0

# ---------------------------------------------------------------- which operator prices a vehicle
LUXEMBOURG_STOPS = ("lu-", "be-sncb_82")   # Luxembourg's own timetable, and its stations in the Belgian one (82 is its railway code)

# ---------------------------------------------------------------- published tariffs
# City networks sell one ticket at one price, whatever the stops: no booking system to ask, the price is the one
# the network publishes. Each tariff below was read on the page given, on the day given, and stops being used
# TARIFF_DAYS later: the vehicles then go back to an estimate until someone reads the page again and updates it.
# A ticket valid for a time covers the vehicles of the network in a row, as long as they fit in that time.
PRAGUE = (50.0755, 14.4378)
PRAGUE_KM = 17   # the airport is 11 km from the centre: further than this is another zone of the region, and another price

def timed(prices: list[tuple], currency: str = "EUR") -> Callable[[Trip], float | None]:
    """A tariff of tickets valid for a time: [(minutes, price)], shortest first. None when the ride is longer than all."""
    def fare(trip: Trip) -> float | None:
        price = next((price for minutes, price in prices if 0 <= trip.minutes <= minutes), None)
        return in_eur(price, currency) if price is not None else None
    return fare

def prague_fare(trip: Trip) -> float | None:
    in_prague = all(at and distance_km(at[0], at[1], *PRAGUE) <= PRAGUE_KM for at in (trip.start_at, trip.end_at))
    return timed([(30, 39), (90, 50)], "CZK")(trip) if in_prague else None

def budapest_fare(trip: Trip) -> float | None:
    """One ticket per vehicle. The airport bus 100E has its own."""
    return in_eur(2500 if trip.line.strip().upper() == "100E" else 500, "HUF")

TARIFFS = [
    # https://pid.cz/en/new-pid-tariff-from-1-january-2026-dont-pay-more-than-you-have-to/ : paper tickets, zones P, 0 and B
    FareSource("PID Prague (published tariff)", "CZ", lambda step: agency(step) == "prazska integrovana doprava", prague_fare, through=True, checked="2026-10-13"),
    # https://bkk.hu/en/tickets-and-passes/prices/ : single ticket 500 Ft, airport shuttle bus single ticket 2 500 Ft
    FareSource("BKK Budapest (published tariff)", "HU", lambda step: agency(step) == "bkk", budapest_fare, checked="2026-10-13"),
    # https://www.vrt.be/vrtnws/en/2026/01/27/brussels-public-transport-company-to-increase-fare-prices-from-1 : 2.40 from 1 February 2026.
    # Line 12 to the airport has a dearer ticket, which was not read: it is left to the estimate
    FareSource("STIB Brussels (published tariff)", "BE", lambda step: agency(step) == "stib" and str(step.get("line")) != "12",
               timed([(60, 2.40)]), through=True, checked="2026-10-13"),
    # https://www.vrt.be/vrtnws/en/2026/01/02/de-lijn-to-increase-its-fares-from-february : the single ticket stays at 3.00, 60 minutes
    FareSource("De Lijn (published tariff)", "BE", lambda step: agency(step) == "de lijn", timed([(60, 3.00)]), through=True, checked="2026-10-13"),
    # https://www.belgiantrain.be/en/tickets-and-railcards/bus-ticket-charleroi-airport : the bus between the airport and Charleroi station
    FareSource("TEC Charleroi Airport (published tariff)", "BE", lambda step: agency(step) == "letec" and str(step.get("line")) == "A1",
               lambda trip: 6.00, checked="2026-10-13"),
    # https://www.metrolisboa.pt/wp-content/uploads/2025/12/Tarifas_site_2026_PT.pdf : Carris/Metro ticket, one hour on both networks
    FareSource("Carris/Metro Lisbon (published tariff)", "PT", lambda step: agency(step) in ("metropolitano de lisboa, e.p.e.", "carris"),
               timed([(60, 1.90)]), through=True, checked="2026-10-13"),
]
