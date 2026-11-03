"""A saved report as a web page. The reports are Markdown, and this reads the part of it the planner writes:
headings, lists, quotes, tables, bold, links, the reliability lights and the line of each option.

Part of every report was written by a model, so every text is escaped and only https and http links become links:
nothing in a report can add markup or script to the page."""
import html
import re

from trip_planner.report.formatting import read_timeline
from trip_planner.trip.trip_settings import GRADES, LIGHT_COLOURS, LIGHTS

# The colours of the pages
THEME = {"background": "#f5f6f8", "surface": "#ffffff", "text": "#1f2328", "muted": "#6b7280", "border": "#d5d9e0",
         "hover": "#eceff3", "accent": "#2563eb", "accent_hover": "#1d4ed8", "accent_off": "#a9c0f5", "error": "#b42318"}

LINK = r"https?://[^\s,;]+"
# The page's look, from those colours
STYLE = """
:root { --background: %(background)s; --surface: %(surface)s; --text: %(text)s; --muted: %(muted)s;
        --border: %(border)s; --hover: %(hover)s; --accent: %(accent)s; --accent-hover: %(accent_hover)s; %(lights)s }
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html { font-size: 15px; }
body { margin: 0; background: var(--background); color: var(--text); font: 1rem/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 0 20px 48px; }
h1 { font-size: 1.5em; line-height: 1.25; margin: 1.4em 0 .5em; } h2 { font-size: 1.2em; margin: 1.4em 0 .5em; }
h3 { font-size: 1.05em; margin: 1em 0 .3em; }
a { color: var(--accent); overflow-wrap: anywhere; text-underline-offset: 2px; }
article, form.search, form.ask, .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
                                        padding: 20px 24px; margin: 0 0 20px; }
:is(article, form.ask, .card) > :first-child { margin-top: 0; } :is(article, form.ask, .card) > :last-child { margin-bottom: 0; }
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
header { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 24px; padding: 14px 0; margin-bottom: 24px;
         border-bottom: 1px solid var(--border); }
header .name { font-weight: 700; font-size: 1.1em; color: var(--text); text-decoration: none; }
header nav.inline { display: flex; flex-wrap: wrap; gap: 4px 20px; margin-right: auto; }
header nav a { text-decoration: none; } header nav a:hover { text-decoration: underline; }
header form { display: flex; align-items: center; }
header select { height: 32px; } header button { height: 32px; padding: 0 14px; }
form.search { display: flex; flex-direction: column; gap: 18px; }
form.search > * { margin: 0; }
form.search > h1 { margin-bottom: -4px; }
/* Where from ↔ where to, and the days there and back under them, in the same columns */
.pair { display: grid; grid-template-columns: 1fr 38px 1fr; gap: 12px; align-items: end; }
.pair > label:first-child { grid-column: 1; } .pair > label:last-child { grid-column: 3; }
.row { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 16px; align-items: start; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px; }
.actions > p { margin: 0; } .actions > label.inline { margin-left: auto; }
label { display: flex; flex-direction: column; gap: 5px; font-size: .9rem; font-weight: 600; position: relative; }
label small, form small { font-size: .85rem; font-weight: normal; color: var(--muted); }
input, select { font: inherit; font-size: 1rem; font-weight: normal; color: var(--text); background: var(--surface);
                border: 1px solid var(--border); border-radius: 6px; }
input:not([type=checkbox]):not([type=radio]), select { width: 100%%; height: 38px; padding: 0 10px; }
input:focus, select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37, 99, 235, .15); }
input[type=checkbox], input[type=radio] { width: 16px; height: 16px; margin: 2px 0 0; flex: none; accent-color: var(--accent); }
button { font: inherit; font-weight: 600; height: 38px; padding: 0 20px; border: 1px solid var(--accent); border-radius: 6px;
         background: var(--accent); color: #fff; cursor: pointer; white-space: nowrap; }
button:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
button.quiet { background: var(--surface); color: var(--accent); font-weight: normal; }
button.quiet:hover:not(:disabled) { background: var(--hover); border-color: var(--accent); }
button:disabled { opacity: .5; cursor: default; }
button.swap { width: 38px; padding: 0; font-size: 1.1em; }
.refusal { color: %(error)s; font-weight: 600; }
pre.log { white-space: pre-wrap; font: .85em/1.4 Consolas, monospace; color: var(--muted); }
details { margin: 12px 0; } summary { cursor: pointer; }
a.open { font-weight: 700; font-size: 1.1em; }
label.inline { flex-direction: row; align-items: center; gap: 8px; font-size: 1rem; font-weight: normal; }
label.inline select { width: auto; }
.ticks { display: flex; flex-wrap: wrap; gap: 8px 28px; }
/* The box, its words beside it however long, and what it means under the words */
label.tick { display: grid; grid-template-columns: 16px 1fr; align-items: start; gap: 4px 8px; font-size: 1rem; font-weight: normal; }
label.tick small { grid-column: 2; }
fieldset { border: 1px solid var(--border); border-radius: 8px; margin: 0; padding: 10px 16px 14px; min-width: 0; }
legend { font-weight: 600; padding: 0 6px; }
details.advanced { border-top: 1px solid var(--border); padding-top: 14px; }
details.advanced summary { color: var(--muted); font-weight: 600; }
details.advanced[open] summary { margin-bottom: 4px; }
details.advanced fieldset { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px 20px;
                            align-items: start; margin: 14px 0; }
form.ask label.tick { margin: 6px 0; }
form.ask fieldset { margin: 12px 0; }
form.ask > button { margin-top: 8px; }
/* A line to write in and its button beside it: a question about the trip, a language to add */
form.question { display: grid; grid-template-columns: minmax(0, 30em) auto; justify-content: start; align-items: end; gap: 6px 12px; }
form.question > small { grid-column: 1 / -1; width: 0; min-width: 100%%; }   /* the note under both does not widen them */
form.question > button { margin: 0; }
form.keep { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin: 0 0 12px; }
nav ul { font-size: .9em; } nav ul a { text-decoration: none; } nav ul a:hover { text-decoration: underline; }
ul.reports { list-style: none; padding: 0; margin: 0 0 4px; }
ul.reports li { padding: 7px 0; border-bottom: 1px solid var(--border); } ul.reports li:last-child { border-bottom: 0; }
ul.reports a { text-decoration: none; } ul.reports a:hover { text-decoration: underline; }
.card h2 { font-size: 1.05em; }
.calendar { position: absolute; top: 100%%; left: 0; z-index: 2; background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
            padding: 10px; margin-top: 4px; width: max-content; box-shadow: 0 4px 16px rgba(0, 0, 0, .12); font-size: 1rem; font-weight: normal; }
.calendar .month { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 700; margin-bottom: 4px; }
.calendar table { font-size: .9em; } .calendar th, .calendar td { border: 0; padding: 1px; text-align: center; }
.calendar th { background: none; color: var(--muted); font-weight: normal; }
.calendar button { height: auto; padding: 4px 0; width: 2.3em; border: 0; background: var(--surface); color: var(--text);
                   font-weight: normal; border-radius: 4px; }
.calendar button:hover:not(:disabled) { background: var(--hover); }
.calendar button.chosen, .calendar button.chosen:hover { background: var(--accent); color: #fff; font-weight: 700; }
.calendar button:disabled { color: var(--border); opacity: 1; }
.calendar .month button { width: auto; padding: 2px 10px; border: 1px solid var(--border); }
.calendar small { display: block; max-width: 18em; margin-top: 4px; }
.calendar button.quiet { width: auto; padding: 4px 12px; margin-top: 6px; border: 1px solid var(--accent); color: var(--accent); }
.tools { display: flex; flex-wrap: wrap; gap: 16px 32px; align-items: flex-start; }
.tools > p { margin: 0; line-height: 38px; }
form.tool { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; max-width: 22em; }
td form.tool { flex-direction: row; align-items: center; max-width: none; gap: 8px; }
td button { height: 30px; padding: 0 12px; }
.card td { vertical-align: middle; }
#conversation { margin-top: 20px; }
#conversation .answer { background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent);
                        border-radius: 8px; padding: 2px 20px; margin: 10px 0; }
@media (max-width: 600px) {
  main { padding: 0 14px 32px; }
  article, form.search, form.ask, .card { padding: 16px; }
  .pair { grid-template-columns: 1fr; gap: 8px; }
  .pair > label:first-child, .pair > label:last-child { grid-column: 1; }
  button.swap { justify-self: center; transform: rotate(90deg); }
  .actions > label.inline { margin-left: 0; }
  form.question { grid-template-columns: 1fr; } form.question > button { justify-self: start; }
}
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
    """The coloured symbols of the grades as dots of the grades' colours (LIGHT_COLOURS), with what the grade means kept as the
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
