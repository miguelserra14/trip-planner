"""What every operator module shares: the HTTP client, the ticket asked for (Trip), the currencies and the
description of an operator that prices vehicles (FareSource)."""
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from trip_planner.trip.trip_settings import ROOT, USER_AGENT
from trip_planner.utils import plain, to_minutes

TIMEOUT = 20
SAME_MINUTES = 2          # an operator's departure is the timetable's when it leaves within this of it...
SAME_ARRIVAL_MINUTES = 6  # ... and arrives within this: the two do not always agree on the minute a train gets in
SAME_STATION_KM = 1.5     # an operator's station is the timetable's stop when it is this close
OPERATOR_DATA = ROOT / "resources" / "operators"   # what operators' sites do not give with each answer: their stops, their place names
http = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=TIMEOUT, follow_redirects=True)

@dataclass(frozen=True)
class Trip:
    """What is asked of an operator: one ticket from the first stop of a run of its vehicles to the last."""
    start: str            # name of the stop, as the timetable writes it
    end: str
    start_id: str         # the timetable's id of the stop: "eu-flixbus_<FlixBus's own id>"
    end_id: str
    start_at: tuple       # (latitude, longitude), or () when the timetable did not give it
    end_at: tuple
    day: str              # yyyy-mm-dd
    leave: str            # hh:mm on the local clock
    arrive: str
    line: str = ""        # the line of the first vehicle, for the tariffs that price one line apart

    @property
    def minutes(self) -> int:
        return to_minutes(self.arrive) - to_minutes(self.leave)

    def matches(self, leave: str, arrive: str) -> bool:
        """Whether a departure of the operator ("...T10:28...", or "10:28") is this trip."""
        clock = lambda moment: to_minutes(moment[11:16] if "T" in moment else moment[:5])
        return (abs(clock(leave) - to_minutes(self.leave)) <= SAME_MINUTES
                and abs(clock(arrive) - to_minutes(self.arrive)) <= SAME_ARRIVAL_MINUTES)

class NotRunning(Exception):
    """An operator's answer for a day it already runs rides on between the two stops: the departure asked for is
    not among them. Raised only by operators that recognise the stops by the timetable's own ids, so that a stop
    named differently on their site cannot pass for a departure that does not run."""

class NotOnSale(Exception):
    """An operator's answer that the day asked for is not on sale yet: whether it runs then is not known."""

# How far ahead an operator sells: each module that lists rides (RIDE_SOURCES) has a SALES_DAYS, measured on a busy
# route on the day written next to it. A day further away than that, with nothing found, is not on sale yet: the
# report says from when it can be known (steps.not_on_sale_warnings).

# ---------------------------------------------------------------- currencies
RATES: dict[str, float] = {}

def in_eur(amount: float, currency: str) -> float | None:
    """An amount in EUR at the European Central Bank's rate of the day. None if the rate is not known."""
    if currency == "EUR":
        return amount
    if not RATES:
        page = http.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml").raise_for_status().text
        RATES.update({cube.get("currency"): float(cube.get("rate")) for cube in ElementTree.fromstring(page).iter() if cube.get("currency")})
    return round(amount / RATES[currency], 2) if currency in RATES else None

@dataclass(frozen=True)
class FareSource:
    name: str                              # shown in the report
    country: str                           # where it prices: a country code, or "EU" for an operator of many
    sells: Callable[[dict[str, Any]], bool]    # whether a vehicle of a connection is one of its own
    fare: Callable[[Trip], float | None]
    through: bool = False                  # one ticket covers several of its vehicles in a row
    checked: str = ""                      # a published tariff, not a booking system: the day it was read (yyyy-mm-dd)
    confirms: bool = False                 # its price is only given for a departure it sells: finding one confirms the timetable

    @property
    def tariff(self) -> bool:
        return bool(self.checked)

    @property
    def expired(self) -> bool:
        """A tariff read too long ago is no longer believed: prices change every year, some more often."""
        return self.tariff and (date.today() - date.fromisoformat(self.checked)).days > TARIFF_DAYS

agency = lambda step: plain(step.get("agency") or "")

TARIFF_DAYS = 365   # a published tariff (tariffs.py) is no longer believed this long after it was read
