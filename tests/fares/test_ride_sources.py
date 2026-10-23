"""The operators that list what they sell between a city and an airport (fares.RIDE_SOURCES), on real answers.

With --live each route is asked of the operator's site, and every answer it gives (and the exchange rates, and the
place Nominatim finds for an airport's city) is saved as a checkpoint. Without it the saved answers are given back
to today's code instead of the network: it must still read the rides out of them. The sites are not official
services and change without notice: a live run is how a change is found."""
import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from tests.conftest import trip_settings
from trip_planner import current
from trip_planner.trip import places
from trip_planner.trip.places import load_airports, Place
from trip_planner.sources.operators import base, flixbus, movelia, obilet, rede_expressos

CHECKPOINTS = Path(__file__).parent / "checkpoints" / "rides"
DAYS_AHEAD = 16   # close enough for every operator to sell the day
# (the name of the checkpoint, the operator's module and function, the city, the airport, towards the airport)
ROUTES = [
    ("rede_expressos-OPO-Coimbra", rede_expressos.rede_expressos_rides, Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.2111, -8.4291), "OPO", False),
    ("flixbus-OPO-Coimbra", flixbus.flixbus_rides, Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.2111, -8.4291), "OPO", True),
    ("movelia-MAD-Salamanca", movelia.movelia_rides, Place("Salamanca", "Salamanca", "Salamanca", "Spain", 40.9701, -5.6635), "MAD", False),
    ("obilet-IST-Bursa", obilet.obilet_rides, Place("Bursa", "Bursa", "Bursa", "Turkey", 40.1885, 29.0610), "IST", False),
    ("obilet-TGD-Kotor", obilet.obilet_rides, Place("Kotor", "Kotor", "Kotor", "Montenegro", 42.4247, 18.7712), "TGD", False),
]

def key_of(request: httpx.Request) -> str:
    """What tells one request from another: not its headers, which carry tokens and the time."""
    return f"{request.method} {request.url} {request.content.decode('utf-8', 'replace')}"

class Recording(httpx.BaseTransport):
    """Asks the network and keeps each answer."""
    def __init__(self, kept: dict):
        self.real, self.kept = httpx.HTTPTransport(), kept

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.real.handle_request(request)
        content = response.read()
        json_like = content[:1] in (b"{", b"[") or b"<Cube" in content[:2000]
        self.kept[key_of(request)] = {"status": response.status_code, "content": content.decode("utf-8", "replace") if json_like else ""}
        plain_headers = [(k, v) for k, v in response.headers.multi_items()   # every Set-Cookie: Obilet's session is in them
                         if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(response.status_code, headers=plain_headers, content=content, request=request)   # read: no longer compressed

class Replay(httpx.BaseTransport):
    """Gives back the answers kept, and fails a request that was not made when they were."""
    def __init__(self, kept: dict):
        self.kept = kept

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        answer = self.kept.get(key_of(request))
        if answer is None:
            raise httpx.ConnectError(f"not in the checkpoint: {key_of(request)[:200]}", request=request)
        return httpx.Response(answer["status"], content=answer["content"].encode("utf-8"), request=request)

@pytest.fixture(scope="module", params=ROUTES, ids=lambda route: route[0])
def sold(request, live):
    name, source, city, code, to_airport = request.param
    path = CHECKPOINTS / f"{name}.json"
    record = {} if live else (json.loads(path.read_text(encoding="utf-8")) if path.exists() else None)
    if record is None:
        pytest.skip("no checkpoint saved: run pytest --live -k " + name)
    answers, centres = record.setdefault("answers", {}), record.setdefault("centres", {})
    transport = Recording(answers) if live else Replay(answers)
    client = httpx.Client(headers={"User-Agent": "trip-planner tests"}, transport=transport, timeout=60, follow_redirects=True)
    patch = pytest.MonkeyPatch()
    patch.setattr(current, "SETTINGS", trip_settings())   # this module's fixture comes before the settings of each test
    for module in (base, flixbus, movelia, rede_expressos):
        if hasattr(module, "http"):
            patch.setattr(module, "http", client)
    patch.setattr(base, "RATES", {})
    patch.setattr(flixbus, "FLIXBUS_PLACES", {})
    names = json.loads(movelia.MOVELIA_PLACES_FILE.read_text(encoding="utf-8"))   # its place names, from resources/operators
    patch.setattr(movelia, "MOVELIA_SESSION", {"places": {base.plain(part): n for n in names for part in [n, *n.split("/")] if base.plain(part)}})
    patch.setattr(movelia, "MOVELIA_SEARCHES", {})
    patch.setattr(obilet, "OBILET_SESSION", {"client": httpx.Client(headers=obilet.OBILET_BROWSER, transport=transport, timeout=60,
                                                                    follow_redirects=True)})
    patch.setattr(obilet, "OBILET_SPACING_SECONDS", 1.0 if live else 0.0)
    real_geocode = places.geocode

    def geocode(written: str) -> Place:
        """Nominatim's place for an airport's city (Obilet's estimated way): kept like the operators' answers."""
        if live:
            centres[written] = vars(real_geocode(written))
        return Place(**centres[written])
    patch.setattr(places, "geocode", geocode)
    try:
        day = (date.today() + timedelta(days=DAYS_AHEAD)).isoformat() if live else record["day"]
        rides = source(city, {**load_airports()[code], "km": 100}, day, to_airport)
    finally:
        patch.undo()
    if live:
        CHECKPOINTS.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"recorded": date.today().isoformat(), "day": day, "answers": answers, "centres": centres},
                                   ensure_ascii=False, indent=0), encoding="utf-8")
    return name, rides, to_airport

def test_the_operator_sells_rides_on_the_day(sold):
    name, rides, to_airport = sold
    assert rides, f"{name}: no ride read out of its answers"

def test_every_ride_is_complete_and_believable(sold):
    name, rides, to_airport = sold
    for ride in rides:
        assert ride["grade"] in ("A", "C") and ride["leave"] < ride["arrive"] and 0 < ride["price_eur"] < 200, ride
        assert all(step["from"] and step["to"] and step["time"] and step["arrive"] for step in ride["steps"]), ride

def test_an_estimated_way_is_only_where_the_airport_is_not_a_stop(sold):
    """Obilet's coaches from Podgorica start from the city's station, with the way there estimated; Istanbul's
    airport is a stop of its own."""
    name, rides, to_airport = sold
    estimated = [ride for ride in rides if ride.get("estimated_km")]
    if name == "obilet-TGD-Kotor":
        assert estimated and all(ride["grade"] == "C" and ride["steps"][0]["estimated"] for ride in estimated)
    else:
        assert not estimated
