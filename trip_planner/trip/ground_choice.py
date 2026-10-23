"""How the traveller accepts to travel on the ground, on each side of the flight: by any vehicle, by train only or
by bus only, and with at most so many changes of vehicle (to_airport_by, from_airport_by, max_changes_to_airport
and max_changes_from_airport in the settings).

The timetables are only asked for what the traveller accepts (allowed_modes, max_transfers), and every connection
is checked again before it is used (accepts): what an answer contains is not taken on trust. A leg that cannot be
checked, a model's estimate or a stretch estimated from the distance, is never taken when the traveller chose.

Metro, tram, ferries and lifts are the city's own transport: they are accepted with either choice, as the way to
the station or the bus station. A train is any railway; a bus is a city bus or a long-distance coach."""
from typing import Any

from trip_planner import current
from trip_planner.texts import tr

CHOICES = ("any", "train", "bus")
NO_CHANGE_LIMIT = 9   # max_changes_* at this value means no limit: nobody changes vehicle nine times to reach an airport

# The kinds of vehicle as the timetables write them (Transitous): in a question, and on each leg of an answer
RAIL_MODES = {"RAIL", "HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL", "REGIONAL_RAIL", "REGIONAL_FAST_RAIL", "SUBURBAN"}
ROAD_MODES = {"BUS", "COACH"}
CITY_MODES = {"SUBWAY", "METRO", "TRAM", "FERRY", "FUNICULAR", "AERIAL_LIFT", "CABLE_CAR", "OTHER"}

def is_rail(mode: str) -> bool:
    return "RAIL" in mode or mode in ("SUBURBAN", "METRO", "LONG_DISTANCE")

def side(to_airport: bool) -> tuple:
    """(how, most changes) on one side of the flight: to the departure airport, or from the arrival airport."""
    s = current.SETTINGS
    return (s.to_airport_by, s.max_changes_to_airport) if to_airport else (s.from_airport_by, s.max_changes_from_airport)

def restricted(to_airport: bool) -> bool:
    """Whether the traveller limited one side at all."""
    how, changes = side(to_airport)
    return how != "any" or changes < NO_CHANGE_LIMIT

def mode_allowed(mode: str, how: str) -> bool:
    if how == "train":
        return mode in RAIL_MODES or is_rail(mode) or mode in CITY_MODES
    if how == "bus":
        return mode in ROAD_MODES or mode in CITY_MODES
    return True

def allowed_modes(modes: str, to_airport: bool) -> str:
    """The kinds of vehicle of a question to the timetables (as "RAIL,BUS,..."), less the ones the traveller does
    not accept on that side. Empty when none is left: then there is nothing to ask."""
    how = side(to_airport)[0]
    return ",".join(mode for mode in modes.split(",") if mode_allowed(mode, how))

# Transitous counts one more than there are: asked for at most 3 transfers it gives connections with 2 at most, and
# asked for 2 it gave nothing where the 2-change train to Düsseldorf airport exists (measured on 2026-10-21). If it is
# ever corrected, the connections with one change too many are dropped anyway by accepts.
TRANSFERS_ASKED_OVER = 1

def max_transfers(to_airport: bool) -> int | None:
    """The limit to put in a question to the timetables, None when there is no limit."""
    changes = side(to_airport)[1]
    return changes + TRANSFERS_ASKED_OVER if changes < NO_CHANGE_LIMIT else None

def step_mode(step: dict[str, Any]) -> str:
    """The kind of vehicle of one step of a connection. Steps read before the kind was kept say only whether they
    are a train or a coach; a stretch estimated from the distance is by road (a bus or a taxi)."""
    return step.get("mode") or ("RAIL" if step.get("rail") else "COACH" if step.get("coach") else "BUS")

def changes(connection: dict[str, Any]) -> int:
    """Changes of vehicle in a connection. Walking is not a vehicle."""
    return max(0, len(connection.get("steps") or []) - 1)

def accepts(connection: dict[str, Any], to_airport: bool) -> bool:
    """Whether a connection is one the traveller accepts on that side: every vehicle of an accepted kind, and no
    more changes than allowed. A connection with no steps cannot be checked: it is accepted only without a choice."""
    how, most = side(to_airport)
    steps = connection.get("steps") or []
    if not steps:
        return not restricted(to_airport)
    return changes(connection) <= most and all(mode_allowed(step_mode(step), how) for step in steps)

def road_allowed(to_airport: bool) -> bool:
    """Whether a stretch by road (a bus, a coach, or one estimated from the distance) is acceptable on that side."""
    return side(to_airport)[0] != "train"

def describe(to_airport: bool) -> str:
    """The choice on one side, in the traveller's language, such as "by train, at most 1 change"."""
    how, most = side(to_airport)
    parts = [tr(f"ground_by_{how}")]
    if most < NO_CHANGE_LIMIT:
        parts.append(tr("ground_changes", count=most))
    return ", ".join(parts)
