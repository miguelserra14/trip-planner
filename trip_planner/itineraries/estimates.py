"""Estimates from the distance: how long a ground stretch takes and what it costs where nothing better is known.
Kept apart from legs.py because the timetables and the operators use them too, and legs.py reads both."""
from typing import Any

ROAD_FACTOR = 1.3                 # a real trip is about this much longer than the straight line
ESTIMATE_SPEED_KMH = 70           # door-to-door speed of public transport, changes included
ESTIMATE_FIXED_MINUTES = 20       # getting to the station and waiting
ESTIMATE_EUR_PER_KM = 0.12        # price of bus and train where nothing better is known about the country
MIN_TICKET_EUR = 2.0              # no ticket is cheaper than this

# What a km of train costs differs several times over between countries, so the estimate has a rate per country.
# Measured where the railway's own prices can be read (fares.py): the median, over a handful of routes, of the
# price divided by the distance by road. Read on 13 October 2026.
MEASURED_EUR_PER_KM = {"AT": 0.20, "IT": 0.09, "HU": 0.05, "EE": 0.065, "CZ": 0.047}
# Everywhere else, deduced from how expensive transport is in the country: Eurostat's price level of transport
# services (prc_ppp_ind, A010703, 2024, European Union = 100). The curve that fits the five measured countries is
# FITTED_EUR_PER_KM x (level / 100) squared: where transport costs twice as much, trains cost four times as much.
TRANSPORT_PRICE_LEVEL = {
    "AL": 55.7, "AT": 118.2, "BA": 118.4, "BE": 118.1, "BG": 57.2, "CH": 151.7, "CY": 97.3, "CZ": 73.6, "DE": 118.7, "DK": 159.9,
    "EE": 97.9, "GR": 89.5, "ES": 81.5, "FI": 141.2, "FR": 116.3, "HR": 87.0, "HU": 61.6, "IE": 115.1, "IS": 169.6, "IT": 84.8,
    "LT": 77.4, "LU": 98.4, "LV": 71.5, "ME": 72.0, "MK": 55.2, "MT": 89.8, "NL": 118.4, "NO": 161.6, "PL": 68.7, "PT": 80.6,
    "RO": 73.4, "RS": 77.4, "SE": 144.1, "SI": 98.9, "SK": 91.0, "TR": 63.5,
}
FITTED_EUR_PER_KM = 0.106
# Long-distance coaches cost about the same per km everywhere: the median of FlixBus's direct rides in 21 countries
COACH_EUR_PER_KM = 0.05

def estimated_minutes(km: float) -> int:
    return round(km * ROAD_FACTOR / ESTIMATE_SPEED_KMH * 60 + ESTIMATE_FIXED_MINUTES)

def eur_per_km(country: str, coach: bool = False) -> float:
    """What a km by road costs on public transport in a country (two-letter code), for the estimates."""
    if coach:
        return COACH_EUR_PER_KM
    if country in MEASURED_EUR_PER_KM:
        return MEASURED_EUR_PER_KM[country]
    if country in TRANSPORT_PRICE_LEVEL:
        return round(FITTED_EUR_PER_KM * (TRANSPORT_PRICE_LEVEL[country] / 100) ** 2, 3)
    return ESTIMATE_EUR_PER_KM

def estimated_price(km: float, country: str = "", coach: bool = False) -> float:
    return max(MIN_TICKET_EUR, float(round(km * ROAD_FACTOR * eur_per_km(country, coach))))

def step_price(step: dict[str, Any], free_trains: bool, country: str = "") -> float:
    """The price of one vehicle of a timetable connection. Timetables give no prices: a train is free for the
    travellers who said so, and everything else is estimated from the distance."""
    if step["rail"] and free_trains:
        return 0.0
    return estimated_price(step["km"], country, bool(step.get("coach")))
