"""The line at the start of each option of the report: from home to the destination, its parts to scale and in the
colour of their grade, written in the Markdown and read back by the window to draw it. Made-up options, no network."""
from trip_planner.report.formatting import read_timeline, SQUARES, timeline, TIMELINE_SQUARES
from trip_planner.report.summary import option_lines, option_timeline

DAY = "2026-11-12"

def itinerary(to_grade: str = "B", from_grade: str = "C") -> dict:
    """Coimbra to Kotor: a coach to Porto airport, a flight to Podgorica, an estimated way and a coach to Kotor."""
    leg = lambda grade, leave, arrive: {"grade": grade, "service_day": DAY, "kind": "timetable", "source": "", "leave": leave, "arrive": arrive,
                                        "price_eur": 10.0, "price_kind": "estimate", "steps": [], "urls": [], "text": ""}
    flight = {"id": "F", "date": DAY, "route": "OPO → TGD", "departure_airport": "OPO", "arrival_airport": "TGD", "departure_time": "09:00",
              "arrival_time": "13:30", "duration_minutes": 210, "stops": 0, "flights": "FR 1234", "airline": "Ryanair", "source": "Kiwi",
              "price_eur": 60.0, "cabin_bag_included": True, "timezone_shift_minutes": 60}
    return {"flight": flight, "booking": {"verified": False, "url": "https://www.ryanair.com"}, "to": leg(to_grade, 6 * 60, 7 * 60 + 30),
            "from": leg(from_grade, 14 * 60 + 15, 16 * 60 + 45), "flight_price": 60.0, "total_cost": 80.0, "total_minutes": 10 * 60 + 45,
            "leave_time": "06:00", "leave_date": DAY, "at_airport_time": "07:30", "airport_wait": 90, "wait_after_landing": 45,
            "arrival_time": "16:45", "arrival_date": DAY, "notes": []}

def test_option_starts_with_line(settings):
    lines = option_lines(1, itinerary())
    heading = next(number for number, line in enumerate(lines) if line.startswith("###"))
    assert read_timeline(lines[heading + 2])

def test_line_times_and_colours(settings):
    points, parts = read_timeline(option_timeline(itinerary(to_grade="B", from_grade="C")))
    assert [time for time, _ in points] == ["06:00", "07:30", "09:00", "13:30", "14:15", "16:45"]
    assert [place for _, place in points][1:4] == ["OPO", "✈", "TGD"]
    assert [grade for grade, _ in parts] == ["B", None, "A", None, "C"]   # the flight is confirmed, a wait has no colour

def test_train_and_bus_apart(settings):
    """Antwerp to Charleroi airport: a train to Charleroi-Central, ten minutes there, and the A1 bus. The line shows
    both vehicles and the change, as the app's cards do."""
    option = itinerary()
    option["to"].update(leave=10 * 60 + 54, arrive=13 * 60 + 8, steps=[
        {"time": "10:54", "arrive": "12:35", "mode": "RAIL", "from": "Anvers-Central", "to": "Charleroi-Central"},
        {"time": "12:45", "arrive": "13:03", "mode": "BUS", "from": "Charleroi Gare Centrale", "to": "CHARLEROI Airport"}])
    option["leave_time"], option["at_airport_time"] = "10:54", "13:08"
    points, parts = read_timeline(option_timeline(option))
    assert points[:4] == [("10:54", "Coimbra"), ("12:35", "Charleroi-Central"), ("12:45", ""), ("13:08", "OPO")]
    assert [grade for grade, _ in parts[:4]] == ["B", None, "B", None]   # the train, the change, the bus, the wait at the airport

def test_parts_drawn_to_scale():
    line = timeline([("06:00", "A"), ("06:05", ""), ("12:05", "B")], [("A", 5), ("C", 360)])
    points, parts = read_timeline(line)
    assert parts[0][1] == 1 and parts[1][1] == TIMELINE_SQUARES   # five minutes still get a square
    assert points == [("06:00", "A"), ("06:05", ""), ("12:05", "B")]

def test_non_option_line_untouched():
    assert read_timeline("**Cost**: 80 EUR") is None
    assert read_timeline(f"The line: {SQUARES['A']} confirmed, {SQUARES[None]} waiting") is None
