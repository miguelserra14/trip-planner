"""The app's icons: drawn here, inline in every page (report_page.page), since a page loads nothing from elsewhere. Each
is a symbol of one sprite, used as <svg><use href="#i-name"></svg>, in the colour of the text around it."""
import html

# Each icon on a 24 x 24 grid: lines in the colour of the text, the plane filled
SYMBOLS = {
    "plane": '<path fill="currentColor" stroke="none" d="M21 15.5v-2L13.5 9V4a1.5 1.5 0 0 0-3 0v5L3 13.5v2l7.5-2.3V18'
             'l-2 1.5V21l3.5-1 3.5 1v-1.5l-2-1.5v-4.8z"/>',
    "train": '<rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 10h14M8.5 21l2-4M15.5 21l-2-4"/>'
             '<circle cx="9" cy="13.5" r=".8" fill="currentColor"/><circle cx="15" cy="13.5" r=".8" fill="currentColor"/>',
    "bus": '<rect x="4" y="3" width="16" height="15" rx="2.5"/><path d="M4 11h16M7 18v3M17 18v3M9 6.5h6"/>'
           '<circle cx="8" cy="14.5" r=".8" fill="currentColor"/><circle cx="16" cy="14.5" r=".8" fill="currentColor"/>',
    "tram": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 3h6M12 3v3M6 12h12M9 21l1.5-3M15 21l-1.5-3"/>',
    "ferry": '<path d="M3 18c2 1.4 4 1.4 6 0s4-1.4 6 0 4 1.4 6 0M5 15l1.2-5h11.6l1.2 5M9 10V6h6v4"/>',
    "walk": '<circle cx="13" cy="4.5" r="1.6"/><path d="M11 21l2-6-3-3 1-4 4 3h3M10 8l-3 3v3"/>',
    "route": '<path stroke-dasharray="2.5 3" d="M4 19c4 0 4-7 8-7s4-7 8-7"/><circle cx="4" cy="19" r="1.6"/><circle cx="20" cy="5" r="1.6"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8'
           'M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>',
    "swap": '<path d="M4 8h15l-3.5-3.5M20 16H5l3.5 3.5"/>',
}

def sprite() -> str:
    """Every icon, once, hidden: what each <use> of the page points to."""
    symbols = "".join(f'<symbol id="i-{name}" viewBox="0 0 24 24">{drawing}</symbol>' for name, drawing in SYMBOLS.items())
    return (f'<svg xmlns="http://www.w3.org/2000/svg" hidden fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round">{symbols}</svg>')

def icon(name: str, label: str = "") -> str:
    """One icon. With a label it is said to who does not see it, otherwise it is left out as decoration."""
    said = f' role="img" aria-label="{html.escape(label)}"' if label else ' aria-hidden="true"'
    return f'<svg class="icon icon-{name}"{said}><use href="#i-{name}"/></svg>'

def mode_of(vehicle: dict) -> str:
    """The icon of a vehicle, from how the timetables call it (sources/timetables/client.py)."""
    mode = (vehicle.get("mode") or "").upper()
    if vehicle.get("rail") or "RAIL" in mode:
        return "train"
    if vehicle.get("coach") or "BUS" in mode or mode == "COACH":
        return "bus"
    if mode in ("SUBWAY", "TRAM", "METRO", "CABLE_CAR", "FUNICULAR"):
        return "tram"
    if mode == "FERRY":
        return "ferry"
    if mode == "WALK":
        return "walk"
    return "route"
