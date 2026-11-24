"""A saved report as a web page. The reports are Markdown, and this reads the part of it the planner writes:
headings, lists, quotes, tables, bold, links, the reliability lights and the line of each option.

Part of every report was written by a model, so every text is escaped and only https and http links become links:
nothing in a report can add markup or script to the page."""
import html
import re
from pathlib import Path

from trip_planner.report.icons import icon, sprite
from trip_planner.report.formatting import read_timeline
from trip_planner.trip.trip_settings import GRADES, LIGHT_COLOURS, LIGHTS

STYLE = Path(__file__).with_name("page.css")   # the look of every page of the app, served at /page.css
LINK = r"https?://[^\s,;]+"
# The colours of the grades, the planner's, in a line of every page: the rest of the look is in page.css
GRADE_COLOURS = ":root { " + " ".join(f"--light-{grade}: {colour};" for grade, colour in LIGHT_COLOURS.items()) + " }"

def inline(text: str) -> str:
    """One line of text as HTML: its **bold** parts, its `code`, its links and its lights, every other text escaped."""
    parts = []
    for part in re.split(rf"(\*\*.+?\*\*|`[^`]+`|{LINK})", text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            parts.append(f"<strong>{inline(part[2:-2])}</strong>")
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            parts.append(f"<code>{html.escape(part[1:-1])}</code>")
        elif re.fullmatch(LINK, part):
            url = part.rstrip(").")
            parts.append(f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(url)}</a>'
                         f"{html.escape(part[len(url):])}")
        else:
            parts.append(lights(html.escape(part)))
    return "".join(parts)

def lights(text: str) -> str:
    """The coloured symbols of the grades as dots of the grades' colours (LIGHT_COLOURS), with what the grade means kept as the
    symbol itself for a reader that does not see colours."""
    for grade in GRADES:
        text = text.replace(LIGHTS[grade], f'<span class="light-{grade}" title="{LIGHTS[grade]}">●</span>')
    return text

# The least width of a part of the line, in pixels, as Discover's map draws it (discover/view/map.js, drawLine): one
# between two points with their time and place has room for both labels, a vehicle for its icon, a wait for a gap
# that shows. A line whose parts need more than LINE_PX is that much wider and scrolls, instead of squeezing four
# trains to an airport into one green stroke
SHORTEST_PX, SHORTEST_RIDE_PX, SHORTEST_WAIT_PX = 78, 34, 12
LINE_PX = 560          # the width a line is drawn for, at least: wider screens stretch it
ICON_PX = SHORTEST_RIDE_PX - 4   # a part narrower than this has no room for its vehicle's icon

def widths(points: list[tuple | None], parts: list[tuple]) -> tuple[list[float], int]:
    """Where each point of the line is, in % of its width, and the least width of the line in pixels: each part at
    least its shortest width (SHORTEST_PX...), what is left shared by the minutes."""
    labelled = lambda point: point is not None and len(point) < 3
    least = [SHORTEST_PX if labelled(points[number]) and labelled(points[number + 1]) else
             SHORTEST_RIDE_PX if len(part) > 2 and part[2] else SHORTEST_WAIT_PX for number, part in enumerate(parts)]
    room = max(LINE_PX, sum(least))
    total = sum(max(1, part[1]) for part in parts) or 1
    free = room - sum(least)
    edges = [0.0]
    for width, part in zip(least, parts):
        edges.append(edges[-1] + 100 * (width + free * max(1, part[1]) / total) / room)
    return edges, room

def timeline(points: list[tuple | None], parts: list[tuple]) -> str:
    """The line of an option nearly to scale: each part as long as its share of the minutes (widths() widens the
    short ones, and the line when they need it), in the colour of its grade, a wait as a thin grey line, and at each point its time above and its
    place below. A part may name its vehicle's icon as a third item, drawn on the line when there is room. A point may
    be a change of vehicle, (time, place, "change"): a small dot whose time and place are said on hover, since the
    changes of a ground leg are too close together for their labels. A point that is None is a boundary with nothing drawn."""
    edges, room = widths(points, parts)
    drawn = []
    for part, start, end in zip(parts, edges, edges[1:]):
        grade, mark = part[0], part[2] if len(part) > 2 else None
        place = f"left: {start:.2f}%; width: {end - start:.2f}%"
        drawn.append(f'<div class="part" style="background: var(--light-{grade}); {place}"></div>' if grade
                     else f'<div class="part wait" style="{place}"></div>')
        if mark and (end - start) * room / 100 >= ICON_PX:
            drawn.append(f'<div class="vehicle" style="left: {(start + end) / 2:.2f}%">{icon(mark)}</div>')
    for number, (point, x) in enumerate(zip(points, edges)):
        if point is None:
            continue
        if len(point) > 2:   # a change of vehicle
            said = html.escape(f"{point[0]} {point[1]}".strip())
            drawn.append(f'<div class="change" style="left: {x:.2f}%" title="{said}"><i></i><span class="visually-hidden">{said}</span></div>')
            continue
        time, place = point
        side = " first" if number == 0 else " last" if number == len(points) - 1 else ""
        drawn.append(f'<div class="point{side}" style="left: {x:.2f}%"><b>{inline(time)}</b><i></i><span>{inline(place)}</span></div>')
    return f'<div class="timeline-scroll"><div class="timeline" style="min-width: {room}px">{"".join(drawn)}</div></div>'

def table(rows: list[list[str]]) -> str:
    head, *body = rows
    cells = lambda row, tag: "".join(f"<{tag}>{inline(cell)}</{tag}>" for cell in row)
    rows_html = "".join(f"<tr>{cells(row, 'td')}</tr>" for row in body)
    return f'<div class="table"><table><thead><tr>{cells(head, "th")}</tr></thead><tbody>{rows_html}</tbody></table></div>'

def to_html(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """The body of the page, and its sections as (anchor, title) for a list of contents: every # and ## heading."""
    lines, index, out, sections, bullets = markdown.strip("\n").split("\n"), 0, [], [], []
    flush = lambda: (out.append(f"<ul>{''.join(bullets)}</ul>"), bullets.clear()) if bullets else None
    while index < len(lines):
        line = lines[index]
        rule = re.fullmatch(r"\s*(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})", line)   # which the model's text sometimes has
        bullet = line.startswith("- ") and not rule
        if not bullet:
            flush()
        if line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):   # the line under the header
                    rows.append(cells)
                index += 1
            out.append(table(rows))
            continue
        drawn = read_timeline(line)
        heading = re.match(r"(#{1,3}) (.*)", line)
        if drawn:
            out.append(timeline(*drawn))
        elif heading:
            level, anchor = len(heading[1]), f"s{len(sections) + 1}"
            if level < 3:
                sections.append((anchor, heading[2].replace("**", "")))
            out.append(f'<h{level} id="{anchor}">{inline(heading[2])}</h{level}>')
        elif bullet:
            bullets.append(f"<li>{inline(line[2:])}</li>")
        elif rule:
            out.append("<hr>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
        index += 1
    flush()
    return "\n".join(out), sections

def page(title: str, body: str, language: str, refresh: int = 0, theme: str = "") -> str:
    """A whole page of the app, with nothing loaded from elsewhere but its own style and script. refresh, in seconds,
    makes a browser without the script ask for the page again: how a running search shows its progress then. theme
    is "light" or "dark" when the traveller chose one, "" to follow the computer."""
    again = f'<noscript><meta http-equiv="refresh" content="{int(refresh)}"></noscript>' if refresh else ""
    chosen = f' data-theme="{theme}"' if theme in ("light", "dark") else ""
    return (f'<!doctype html><html lang="{html.escape(language)}"{chosen}><head><meta charset="utf-8">{again}'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>'
            f'<link rel="stylesheet" href="/page.css"><style>{GRADE_COLOURS}</style><script src="/page.js" defer></script></head>'
            f'<body>{sprite()}<main>{body}</main></body></html>')
