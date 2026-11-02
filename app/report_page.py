"""A saved report as a web page. The reports are Markdown, and this reads the same part of it the window does
(gui/output.py): headings, lists, quotes, tables, bold, links, the reliability lights and the line of each option.

Part of every report was written by a model, so every text is escaped and only https and http links become links:
nothing in a report can add markup or script to the page."""
import html
import re

from trip_planner.gui.style import LIGHT_COLOURS, THEME
from trip_planner.report.formatting import read_timeline
from trip_planner.trip.trip_settings import GRADES, LIGHTS

LINK = r"https?://[^\s,;]+"
# The page's look, from the window's colours, so that the two read alike while both exist
STYLE = """
:root { --background: %(background)s; --surface: %(surface)s; --text: %(text)s; --muted: %(muted)s;
        --border: %(border)s; --accent: %(accent)s; %(lights)s }
* { box-sizing: border-box; }
body { margin: 0; background: var(--background); color: var(--text); font: 15px/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 16px; }
article { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 24px 24px; }
h1 { font-size: 1.6em; margin: 1.2em 0 .4em; } h2 { font-size: 1.3em; margin: 1.2em 0 .3em; }
h3 { font-size: 1.05em; margin: 1em 0 .2em; }
a { color: var(--accent); overflow-wrap: anywhere; }
blockquote { margin: .5em 0; padding-left: 12px; border-left: 3px solid var(--border); color: var(--muted); }
code { font-family: Consolas, monospace; font-size: .92em; }
.muted { color: var(--muted); }
.light-A { color: var(--light-A); } .light-B { color: var(--light-B); } .light-C { color: var(--light-C); }
.table { overflow-x: auto; margin: .6em 0; }
table { border-collapse: collapse; font-size: .9em; }
th, td { border-bottom: 1px solid var(--border); padding: 4px 10px; text-align: left; vertical-align: top; }
th { background: var(--background); }
nav ul { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
.timeline { position: relative; height: 64px; margin: 8px 12px; }
.timeline .part { position: absolute; top: 27px; height: 7px; }
.timeline .wait { top: 29px; height: 3px; background: var(--border); }
.timeline .point { position: absolute; top: 0; height: 64px; transform: translateX(-50%%); text-align: center;
                   font-size: .8em; white-space: nowrap; }
.timeline .point.first { transform: none; text-align: left; } .timeline .point.last { transform: translateX(-100%%); text-align: right; }
.timeline .point b { display: block; height: 22px; }
.timeline .point i { display: block; width: 12px; height: 12px; margin: 2px auto; border: 2px solid var(--text);
                     border-radius: 50%%; background: var(--surface); }
.timeline .first i { margin-left: -6px; } .timeline .last i { margin-right: -6px; }
.timeline .point span { color: var(--muted); }
form.search { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 4px 24px 20px;
              margin-bottom: 16px; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px 16px; }
form.search h1, form.search p, form.search .refusal { grid-column: 1 / -1; margin-bottom: 0; }
label { display: flex; flex-direction: column; gap: 4px; font-weight: 600; position: relative; }
label small { font-weight: normal; color: var(--muted); }
input { font: inherit; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }
button { font: inherit; padding: 8px 20px; border: 0; border-radius: 6px; background: var(--accent); color: #fff; cursor: pointer;
         justify-self: start; }
.refusal { color: %(error)s; font-weight: 600; }
pre.log { white-space: pre-wrap; font: .85em/1.4 Consolas, monospace; color: var(--muted); }
details { margin: 12px 0; }
header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
header .name { font-weight: 700; font-size: 1.15em; color: var(--text); text-decoration: none; }
label.inline { flex-direction: row; align-items: center; font-weight: normal; gap: 6px; }
select { font: inherit; padding: 5px 6px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }
.ticks, details.advanced, form.search > button { grid-column: 1 / -1; }
.ticks { display: flex; flex-wrap: wrap; gap: 6px 20px; }
label.tick { flex-direction: row; align-items: baseline; flex-wrap: wrap; font-weight: normal; gap: 6px; }
label.tick small { flex-basis: 100%%; }
details.advanced fieldset { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px 16px; }
fieldset { border: 1px solid var(--border); border-radius: 6px; margin: 10px 0; padding: 8px 14px 12px; }
legend { font-weight: 700; padding: 0 4px; }
form.ask { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 4px 20px 16px; margin: 12px 0; }
form.ask label.tick { display: flex; margin: 4px 0; }
form.question { display: grid; gap: 8px; }
form.keep { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
button.quiet { background: var(--surface); color: var(--accent); border: 1px solid var(--accent); }
.calendar { position: absolute; top: 100%%; left: 0; z-index: 2; background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
            padding: 8px; margin-top: 4px; width: max-content; box-shadow: 0 4px 16px rgba(0, 0, 0, .12); font-weight: normal; }
.calendar .month { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 700; margin-bottom: 4px; }
.calendar table { font-size: .9em; } .calendar th, .calendar td { border: 0; padding: 1px; text-align: center; }
.calendar button { padding: 4px 0; width: 2.3em; background: var(--surface); color: var(--text); border-radius: 4px; }
.calendar button.chosen { background: var(--accent); color: #fff; font-weight: 700; }
.calendar button:disabled { color: var(--border); cursor: default; }
.calendar .month button { width: auto; padding: 2px 10px; border: 1px solid var(--border); }
.calendar small { display: block; max-width: 18em; margin-top: 4px; color: var(--muted); }
.calendar button.quiet { width: auto; padding: 4px 12px; margin-top: 6px; }
.tools { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: flex-start; }
form.tool { display: flex; flex-direction: column; gap: 4px; max-width: 26em; }
form.tool small { color: var(--muted); }
td form.tool { flex-direction: row; align-items: center; max-width: none; gap: 8px; }
button:disabled { opacity: .5; cursor: default; }
#conversation { margin-top: 20px; }
#conversation .answer { background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent);
                        border-radius: 8px; padding: 2px 20px; margin: 10px 0; }
""" % {**THEME, "lights": " ".join(f"--light-{grade}: {colour};" for grade, colour in LIGHT_COLOURS.items())}

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
    """The coloured symbols of the grades as dots of the window's colours, with what the grade means kept as the
    symbol itself for a reader that does not see colours."""
    for grade in GRADES:
        text = text.replace(LIGHTS[grade], f'<span class="light-{grade}" title="{LIGHTS[grade]}">●</span>')
    return text

def timeline(points: list[tuple[str, str]], parts: list[tuple[str | None, int]]) -> str:
    """The line of an option drawn to scale: each part as long as its share of the squares, in the colour of its
    grade, a wait as a thin grey line, and at each point its time above and its place below."""
    total = sum(count for _, count in parts) or 1
    edges = [0.0]
    for _, count in parts:
        edges.append(edges[-1] + 100 * count / total)
    drawn = []
    for (grade, _), start, end in zip(parts, edges, edges[1:]):
        place = f"left: {start:.2f}%; width: {end - start:.2f}%"
        drawn.append(f'<div class="part" style="background: var(--light-{grade}); {place}"></div>' if grade
                     else f'<div class="part wait" style="{place}"></div>')
    for number, ((time, place), x) in enumerate(zip(points, edges)):
        side = " first" if number == 0 else " last" if number == len(points) - 1 else ""
        drawn.append(f'<div class="point{side}" style="left: {x:.2f}%"><b>{inline(time)}</b><i></i><span>{inline(place)}</span></div>')
    return f'<div class="timeline">{"".join(drawn)}</div>'

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

def page(title: str, body: str, language: str, refresh: int = 0) -> str:
    """A whole page of the app, with nothing loaded from elsewhere but its own script. refresh, in seconds, makes
    a browser without the script ask for the page again: how a running search shows its progress then."""
    again = f'<noscript><meta http-equiv="refresh" content="{int(refresh)}"></noscript>' if refresh else ""
    return (f'<!doctype html><html lang="{html.escape(language)}"><head><meta charset="utf-8">{again}'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>'
            f'<style>{STYLE}</style><script src="/page.js" defer></script></head><body><main>{body}</main></body></html>')
