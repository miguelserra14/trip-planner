"""The coaches of the operators with no timetable (sold.py): where they go found by asking them city to city, in
the shape the map shows, and their price on a day. No network: the operators' answers are made up."""
from datetime import date

import pytest

from discover.overland import ground, sold
from trip_planner.trip.places import Place

COIMBRA = Place("Coimbra", "Coimbra", "Coimbra", "Portugal", 40.21, -8.43)
TODAY = date(2026, 11, 9)   # the coaches are asked for a week later, Monday 16 November
CITIES = [{"name": "Coimbra", "country": "PT", "lat": 40.2056, "lon": -8.4196, "population": 143_000},
          {"name": "Lisbon", "country": "PT", "lat": 38.7167, "lon": -9.1333, "population": 517_000},
          {"name": "Faro", "country": "PT", "lat": 37.0194, "lon": -7.9322, "population": 41_000},
          {"name": "Aveiro", "country": "PT", "lat": 40.6443, "lon": -8.6455, "population": 21_000},     # too small
          {"name": "Salamanca", "country": "ES", "lat": 40.9688, "lon": -5.6639, "population": 152_000}]   # another country

def schedule(start: str, end: str, leaves: str, arrives: str, price: float, changes: int = 0) -> dict:
    return {"departureDate": leaves, "arrivalDate": arrives, "minPrice": price, "availableSeats": 10, "transfersCount": changes,
            "from": {"name": start}, "to": {"name": end}}

@pytest.fixture
def rede_expressos(monkeypatch):
    """Its answers by the name of the stop asked for: two coaches to Lisbon, one with a change only to Faro."""
    names = {stop["id"]: stop["name"] for stop in sold.rede_expressos_stops(40.21, -8.43, 10, False)
             + sold.rede_expressos_stops(38.72, -9.13, 10, False) + sold.rede_expressos_stops(37.02, -7.93, 10, False)}
    asked = []

    def schedules(start: str, end: str, day: str) -> list[dict]:
        asked.append((names.get(start), names.get(end), day))
        if names.get(end, "").startswith("Lisboa"):
            return [schedule("Coimbra", "Lisboa (Sete Rios)", f"{day} 08:00", f"{day} 10:20", 12.5),
                    schedule("Coimbra", "Lisboa (Sete Rios)", f"{day} 17:00", f"{day} 19:10", 9.9)]
        if names.get(start, "").startswith("Lisboa"):   # the way back
            return [schedule("Lisboa (Sete Rios)", "Coimbra", f"{day} 18:00", f"{day} 20:15", 8.0)]
        return [schedule("Coimbra", "Faro", f"{day} 09:00", f"{day} 15:00", 20.0, changes=1),
                schedule("Coimbra", "Loulé", f"{day} 09:00", f"{day} 14:40", 19.0)]   # a town near Faro: not Faro
    monkeypatch.setattr(sold, "rede_expressos_schedules", schedules)
    monkeypatch.setattr(ground, "cities", lambda: CITIES)
    return asked

def test_rede_expressos_portugal_cities(rede_expressos):
    stops = sold.from_city(COIMBRA, TODAY, say=lambda text: None)
    assert sorted(end.split(" ")[0] for _, end, _ in rede_expressos) == ["Faro", "Lisboa"]   # not Aveiro, not Salamanca
    assert all(day == "2026-11-16" for _, _, day in rede_expressos)
    assert len(stops) == 1   # Faro only with a change, and Loulé is another town
    lisbon = stops[0]
    assert (lisbon["operator"], lisbon["minutes"], lisbon["times"], lisbon["weekdays"]) == ("Rede Expressos", 130, ["08:00", "17:00"], ["Mon"])
    assert (lisbon["sold"], lisbon["mode"], lisbon["source"], lisbon["last"]) == ("PT", "bus", "rede-expressos", True)

def test_coaches_kept_not_reasked(rede_expressos):
    sold.from_city(COIMBRA, TODAY, say=lambda text: None)
    asked = len(rede_expressos)
    assert sold.from_city(COIMBRA, TODAY, say=lambda text: None) and len(rede_expressos) == asked

def test_operators_stay_in_country(rede_expressos, monkeypatch):
    monkeypatch.setattr(ground, "cities", lambda: CITIES + [{"name": "Antwerpen", "country": "BE", "lat": 51.22, "lon": 4.40, "population": 529_000}])
    assert sold.from_city(Place("Antwerp", "Antwerpen", "Antwerp", "Belgium", 51.22, 4.40), TODAY, say=lambda text: None) == []
    assert rede_expressos == []

def test_day_price_cheapest_coach(rede_expressos):
    lisbon = sold.from_city(COIMBRA, TODAY, say=lambda text: None)[0]
    out = sold.day_price(lisbon, False, "2026-11-20")
    assert (out["price_eur"], out["leave"], out["seller"], out["grade"], out["to_stop"]) == (9.9, "17:00", "Rede Expressos", "A", "Lisboa (Sete Rios)")
    back = sold.day_price(lisbon, True, "2026-11-22")
    assert (back["price_eur"], back["leave"], back["from_stop"]) == (8.0, "18:00", "Lisboa (Sete Rios)")

def test_movelia_coaches_by_company(monkeypatch):
    combination = lambda company, kind="1", direct="1": {"busOrBoat": kind, "salable": "1", "basicPrice": "15,5", "companyName": company,
                                                         "departureDate": "16/11/2026", "departureTime": "08:00", "arrivalDate": "16/11/2026",
                                                         "arrivalTime": "11:30", "originName": "SEVILLA", "destinationName": "GRANADA", "direct": direct}
    monkeypatch.setattr(sold, "movelia_search", lambda start, end, day: [
        combination("NEX CONTINENTAL HOLDINGS S L U"), combination("RENFE VIAJEROS", "3"), combination("DAMAS S.A."),
        combination("BADAJOZ SEVILLA BUS S.L."), combination("SOCIBUS", direct="0")])
    rides = sold.Movelia().rides("SEVILLA", "GRANADA", "2026-11-16")
    assert [(ride["operator"], ride["direct"]) for ride in rides] == [("ALSA", True), ("Damas", True), ("Badajoz Sevilla Bus", True), ("Socibus", False)]
    assert (rides[0]["minutes"], rides[0]["price_eur"], rides[0]["to"]) == (210, 15.5, "Granada")
