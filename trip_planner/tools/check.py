"""`python main.py --check` asks every service the program depends on for something small and says which ones
answer. Run it after installing, and whenever a search fails, to see which service is the problem."""
import asyncio
import os
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx
from langchain.chat_models import init_chat_model

from trip_planner.agents import models, subagents
from trip_planner.agents.coordinator import COORDINATOR_MODELS
from trip_planner.trip.trip_settings import ROOT, USER_AGENT
from trip_planner.trip.places import airport_from, geocode, load_airports, Place, road_hours
from trip_planner.sources import airlines, fares, google_flights, kiwi, timetables, web
from trip_planner.sources.operators import elron, flixbus, mav, oebb, regiojet, trenitalia
from trip_planner.utils import message_text

async def check_setup() -> int:
    """One small request to every external service. Returns 0 if every required one answered."""
    day = (date.today() + timedelta(days=30)).isoformat()
    antwerp = Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.2211, 4.3997)

    def keys() -> str:
        if not os.environ.get("GOOGLE_API_KEY"):
            raise RuntimeError(f"GOOGLE_API_KEY missing in {ROOT / 'env' / '.env'}")
        optional = {name: bool(os.environ.get(name)) for name in ("TAVILY_API_KEY", models.BACKUP_KEY)}
        return "GOOGLE_API_KEY is set; " + ", ".join(f"{name} {'is set' if there else 'is not set'}" for name, there in optional.items())

    def nominatim() -> str:
        return geocode("Lisboa").label

    def ourairports() -> str:
        return f"{len(load_airports())} airports with scheduled flights"

    def roads() -> str:
        hours = road_hours(antwerp, [airport_from(antwerp, "BRU")])
        if not hours:
            raise RuntimeError("no answer")
        return f"Antwerp -> Brussels Airport in {hours['BRU'] * 60:.0f} minutes"

    def overpass() -> str:
        query = "[out:json][timeout:25];nwr[aeroway=terminal](around:3500,38.7813,-9.1359);out center tags;"
        errors = []
        for server in timetables.OVERPASS_SERVERS:
            try:
                response = httpx.post(server, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=25)
                response.raise_for_status()
                return f"{len(response.json()['elements'])} terminals at Lisbon airport, from {urlparse(server).netloc}"
            except Exception as e:
                errors.append(f"{urlparse(server).netloc}: {type(e).__name__}")
        raise RuntimeError("; ".join(errors))

    answered: list[str] = []   # the flight sources that work: one is enough

    async def flights() -> str:
        await kiwi.connect_kiwi()
        if kiwi.kiwi_search is None:
            raise RuntimeError("the MCP server did not let the search tool be loaded")
        found = await kiwi.ask_kiwi("LIS", "OPO", day, day)
        if found is None:
            raise RuntimeError("the search tool loaded, but a flight search got no answer")
        answered.append(kiwi.NAME)
        return f"{len(found)} flights Lisbon -> Porto on {day}"

    def plan_b_flights() -> str:
        found = google_flights.read_route("LIS", "OPO", day, 0)
        answered.append(google_flights.NAME)
        return f"{len(found)} flights Lisbon -> Porto on {day}"

    def any_flights() -> str:
        if not answered:
            raise RuntimeError(f"neither {kiwi.NAME} nor {google_flights.NAME} answered")
        return f"from {answered[0]}"

    async def transitous() -> str:
        transit = timetables.Timetables()
        try:
            found = await transit.get("reverse-geocode", {"place": "40.2056,-8.4196"})
            return "time zone of Coimbra: " + next((place["tz"] for place in found if place.get("tz")), "not given")
        finally:
            await transit.close()

    def flixbus() -> str:
        city = flixbus.flixbus_city(antwerp.english_name, antwerp.latitude, antwerp.longitude, 25)
        if not city:
            raise RuntimeError("Antwerp was not found")
        return "Antwerp found"

    def ryanair() -> str:
        response = httpx.get(
            "https://www.ryanair.com/api/farfnd/v4/oneWayFares",
            params={"departureAirportIataCode": "CRL", "arrivalAirportIataCode": "OPO", "currency": "EUR",
                    "outboundDepartureDateFrom": day, "outboundDepartureDateTo": day},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        return f"{len(response.json()['fares'])} fares Charleroi -> Porto on {day}"

    def operator_prices() -> str:
        # A departure taken from each operator's own search must come back with its price
        answered = [name for name, check in (
            ("ÖBB", lambda: fares.http.get(f"{oebb.OEBB}/domain/v4/init").raise_for_status().json()["accessToken"]),
            ("Trenitalia", lambda: fares.http.get(f"{trenitalia.TRENITALIA}/locations/search", params={"name": "Roma Termini", "limit": 1}).raise_for_status().json()[0]["id"]),
            ("MÁV", lambda: fares.http.post(f"{mav.MAV}/GetStationList", json={}, headers=mav.MAV_SESSION).raise_for_status().json()["stations"][0]),
            ("Elron", lambda: fares.http.get(f"{elron.ELRON}/originstops").raise_for_status().json()[0]),
            ("RegioJet", lambda: fares.http.get(f"{regiojet.REGIOJET}/consts/locations").raise_for_status().json()[0]),
            ("exchange rates", lambda: fares.in_eur(1000, "HUF") or 1 / 0),
        ) if not isinstance(attempt(check), Exception)]
        if len(answered) < 4:
            raise RuntimeError(f"only {', '.join(answered) or 'none'} answered")
        return f"{len(answered)} of 6 answer: {', '.join(answered)}"

    def attempt(check):
        try:
            return check()
        except Exception as error:
            return error

    def wikidata() -> str:
        return airlines.wikidata_site("FR", "Ryanair") or "no site recorded"

    def tavily() -> str:
        return f"{len(web.search('Ryanair official site', max_results=3)['results'])} results"

    def model(name: str):
        async def ask_model() -> str:
            answer = await init_chat_model(name, max_retries=1).ainvoke("Answer with the single word: ready")
            answering.append(name)
            return web.clean_text(message_text(answer), 40) or "answered"
        return ask_model

    answering: list[str] = []
    def any_model() -> str:
        if not answering:
            raise RuntimeError("no model answered")
        return "from " + answering[0].split(":")[1]

    def used_for(name: str) -> str:
        if name == subagents.SUBAGENT_MODEL:
            return "subagents, and coordinator when the better model has no quota"
        return "plan B, when the Gemini models do not answer" if name == models.BACKUP_MODEL else "coordinator"

    # (service, what it is used for, the check, required). Without an optional service the program still runs.
    checks = [
        ("API keys", "Gemini; Tavily and Mistral are optional", keys, True),
        ("Nominatim", "locating the cities", nominatim, True),
        ("OurAirports", "airport database", ourairports, True),
        ("OSRM", "hours by road to the airports", roads, False),
        (kiwi.NAME, "flights", flights, False),
        (google_flights.NAME, "flights, when Kiwi does not answer", plan_b_flights, False),
        ("Flight search", "one of the two above", any_flights, True),
        ("Transitous", "published timetables", transitous, True),
        ("FlixBus", "coach rides and prices", flixbus, False),
        ("Ryanair", "confirmed flight prices", ryanair, False),
        ("Rail and coach operators", "ticket prices of ground transport", operator_prices, False),
        ("Overpass", "airport terminals", overpass, False),
        ("Wikidata", "airline sites", wikidata, False),
        ("Tavily", "web search of the subagents; without it only the timetables are used", tavily, False),
        *((name.split(":")[1], used_for(name), model(name), False) for name in dict.fromkeys([subagents.SUBAGENT_MODEL, *COORDINATOR_MODELS])),
        ("A model", "one of the above", any_model, True),
    ]
    failed = 0
    for service, used_for, check, required in checks:
        try:
            result = await check() if asyncio.iscoroutinefunction(check) else await asyncio.to_thread(check)
            status = "ok"
        except Exception as e:
            result = f"{type(e).__name__}: {web.clean_text(str(e), 150)}"
            status = "FAILED" if required else "failed (optional)"
            failed += required
        print(f"{status:<18}{service} ({used_for}): {result}", flush=True)
    print("\nEvery required service answered." if not failed else f"\n{failed} required service(s) failed.")
    return 1 if failed else 0
