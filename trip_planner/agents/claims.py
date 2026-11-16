"""What a model wrote about the options, checked by code against the options themselves: the reliability colour it
gives to each part, and the durations and clock times it cites.

The model is told to describe how reliable the recommended option is with the colours of the report, and never to
restate a time differently. It does not always: on 2026-10-26 it called a ground leg 🔵 that the report showed 🟢.
Nothing a model wrote may be shown as confirmed, so each colour in its text is read with what it is said of:
- which options: the ones it names by number, "the others", or else the recommended one;
- which part: the flight, the way to the airport or the way from it (words of the label or of the sentence), and the
  price or the times (the closest such word to the colour);
- how: as one part that is that colour, as all of them ("every part is 🔵"), or as none ("no part is 🟢").
A colour said of one part that has another is replaced by the right one; any other claim the options contradict is
pointed out under the text. Durations and clock times are looked for in what the model was given: one it made up
is pointed out too. The words come from the texts of every language (claim_* in texts/languages)."""
import re
from typing import Any

from trip_planner.report.formatting import listed
from trip_planner.report.summary import part_grades
from trip_planner.trip.trip_settings import LIGHTS
from trip_planner.texts import any_of, tr, words
from trip_planner.utils import count, plain

GRADE_OF = {light: grade for grade, light in LIGHTS.items()}
LIGHT = re.compile("|".join(map(re.escape, LIGHTS.values())))
SIDES = ("flight", "to", "from")
KINDS = ("times", "price")
ROUNDED_MINUTES = 30   # "about 8 hours" for 7h56: a duration in whole hours stands for anything this close to it
KIND_DISTANCE = 40     # characters around a colour where "price" or "times" says which of the two it is about

def found(key: str, text: str) -> list[re.Match]:
    """Where the words of a list text are in a plain text, whole words only."""
    return list(re.finditer(rf"(?<![a-z0-9])(?:{any_of(key)})(?![a-z0-9])", text))

def sides_in(text: str) -> set[str]:
    """The sides of the flight a piece of text speaks of: the way to the airport and the way from it are more precise
    than "the ground transport", which is both."""
    text = plain(text)
    sides = {side for side, key in (("flight", "claim_flight"), ("to", "claim_to"), ("from", "claim_from")) if found(key, text)}
    if not sides & {"to", "from"} and found("claim_ground", text):
        sides |= {"to", "from"}
    return sides

def closest_kind(before: str, after: str) -> str | None:
    """Whether a colour is said of a price or of times: the closest of those words on either side of it."""
    distances = {}
    for kind, key in (("times", "claim_times"), ("price", "claim_price")):
        near = [len(before) - match.end() for match in found(key, before)] + [match.start() for match in found(key, after)]
        if near:
            distances[kind] = min(near)
    return min(distances, key=distances.get) if distances else None

def options_named(line: str, count: int, recommended: int) -> list[int]:
    """The options a line speaks of: those it names by number, the others than the recommended one, or that one."""
    text = plain(line)
    and_ = any_of("list_and")
    named = re.finditer(rf"(?<![a-z])(?:{any_of('claim_options')})\W*(\d+(?:\W*(?:,|(?:{and_})(?![a-z]))\W*\d+)*)", text)   # "options 2 and 3"
    numbers = [int(number) for match in named for number in re.findall(r"\d+", match[1])]
    numbers = [number for number in dict.fromkeys(numbers) if 1 <= number <= count]
    if numbers:
        return numbers
    if found("claim_others", text):
        return [number for number in range(1, count + 1) if number != recommended]
    return [recommended] if recommended else []

def check_lights(text: str, shown: list[dict[str, Any]], recommended: int) -> tuple[str, list[str]]:
    """The text with the colours said of one part corrected, and what else in it the options contradict."""
    problems: list[str] = []
    corrections: set[str] = set()   # the notes of the colours put right, counted apart from those only pointed out
    corrected = list(text)
    position = 0
    for line in text.split("\n"):
        start, position = position, position + len(line) + 1
        lights = list(LIGHT.finditer(line))
        if not lights or not shown or found("claim_ground_only", plain(line)):
            continue   # the trips without a plane are numbered apart: "no plane 1" is not option 1
        options = options_named(line, len(shown), recommended)
        if not options:   # no option is recommended and none is named: nothing to check the colours against
            continue
        label = line[:line.index(":")] if ":" in line[:lights[0].start()] else ""
        previous = None
        for number, light in enumerate(lights):
            before = line[lights[number - 1].end() if number else 0:light.start()]
            after = line[light.end():lights[number + 1].start() if number + 1 < len(lights) else len(line)]
            between = plain(before).strip(" ,/()*")
            if previous is not None and (not between or between in words("list_and") + words("date_and_words")):
                sides, kind, negated, every = previous   # "no part is 🟢 or 🟠": the second colour is said the way the first was
            else:
                clause = plain(re.split(r"[.;:!?]", before)[-1])   # "not" and "all" of another clause say nothing of this colour
                negated = bool(found("claim_not", clause))
                every = bool(found("claim_all", clause))
                sides = sides_in(label) or sides_in(before + " " + after) or sides_in(line) or set(SIDES)
                # A price or times, said next to the colour. "Every part" is every part, whatever words follow
                kind = None if every else closest_kind(plain(before)[-KIND_DISTANCE:], plain(after)[:KIND_DISTANCE])
            previous = (sides, kind, negated, every)
            parts = [f"part_{side}_{each}" for side in SIDES if side in sides for each in KINDS if kind in (None, each)]
            grades = {(option, part): part_grades(shown[option - 1])[part] for option in options for part in parts}
            said = GRADE_OF[light[0]]
            if negated:
                wrong = said in grades.values()
            elif every:
                wrong = any(grade != said for grade in grades.values())
            else:
                wrong = said not in grades.values()
            if not wrong:
                continue
            if len(grades) == 1 and not negated and not every:   # one part of one option: its colour is known
                (option, part), right = next(iter(grades.items()))
                corrected[start + light.start()] = LIGHTS[right]
                count("claims_corrected")
                problems.append(tr("claim_light_corrected", said=light[0], part=tr(part), option=option, right=LIGHTS[right]))
                corrections.add(problems[-1])
            else:
                report = "; ".join(f"{tr(part)} {LIGHTS[grade]}" + (f" ({tr('option')} {option})" if len(options) > 1 else "")
                                   for (option, part), grade in grades.items())
                problems.append(tr("claim_light_none" if negated else "claim_light_every" if every else "claim_light", said=light[0],
                                   options=listed([str(option) for option in options]), report=report))
    problems = list(dict.fromkeys(problems))
    count("claims_contradicted", sum(problem not in corrections for problem in problems))
    return "".join(corrected), problems

def durations(text: str) -> list[tuple[str, int, bool]]:
    """The durations written in a text: (as written, minutes, whether in whole hours only)."""
    hours, minutes, joined = any_of("claim_hours"), any_of("claim_minutes"), any_of("list_and")
    pattern = (rf"(?<![\d:])(\d{{1,2}})\s*(?:{hours})\s*(\d{{2}})(?!\d)"                      # 7h56
               rf"|(?<![\d:])(\d{{1,3}})\s*(?:{hours})(?![a-z0-9])(?:\s*(?:{joined})?\s*(\d{{1,2}})\s*(?:{minutes})(?![a-z0-9]))?"   # 2 horas e 5 minutos
               rf"|(?<![\d:])(\d{{1,3}})\s*(?:{minutes})(?![a-z0-9])")                        # 45 min
    found_ = []
    for match in re.finditer(pattern, plain(text)):
        if match[1]:
            found_.append((match[0], int(match[1]) * 60 + int(match[2]), False))
        elif match[3]:
            found_.append((match[0], int(match[3]) * 60 + int(match[4] or 0), not match[4]))
        else:
            found_.append((match[0], int(match[5]), False))
    return found_

def check_numbers(text: str, known: str, shown: list[dict[str, Any]]) -> list[str]:
    """The durations and clock times of the text that appear nowhere in what the model was given. A difference
    between the total durations of two options is a duration too: the model may compare them."""
    allowed = {minutes for _, minutes, _ in durations(known)}
    totals = [itinerary["total_minutes"] for itinerary in shown]
    allowed |= {abs(first - second) for first in totals for second in totals}
    unknown = [written.strip() for written, minutes, rounded in durations(text)
               if minutes not in allowed and not (rounded and any(abs(minutes - value) <= ROUNDED_MINUTES for value in allowed))]
    clock = set(re.findall(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)", known))
    clock |= {f"{int(time.split(':')[0]):02d}:{time.split(':')[1]}" for time in clock}
    times = [time for time in re.findall(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)", text)
             if time not in clock and f"{int(time.split(':')[0]):02d}:{time.split(':')[1]}" not in clock]
    count("numbers_unknown", len(set(unknown)) + len(set(times)))
    problems = []
    if unknown:
        problems.append(tr("claim_unknown_durations", values=listed(list(dict.fromkeys(unknown)))))
    if times:
        problems.append(tr("claim_unknown_times", values=listed(list(dict.fromkeys(times)))))
    return problems

# An amount in euros as the report writes it ("45 EUR", "45.50 EUR") or as a model may ("45 €", "€45", "45,50 euros")
AMOUNT = re.compile(r"€\s*(\d+(?:[.,]\d{1,2})?)(?![\d.,]*\d)|(?<![\d.,])(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur(?:os?)?\b)", re.IGNORECASE)
ROUNDED_EUR = 1.0   # "about 45 EUR" for 44.60: a whole amount stands for anything this close to it

def amounts(text: str) -> list[tuple[str, float]]:
    """The amounts in euros written in a text: (as written, value)."""
    return [(match[0], float((match[1] or match[2]).replace(",", "."))) for match in AMOUNT.finditer(text or "")]

def check_prices(text: str, known: str, shown: list[dict[str, Any]]) -> list[str]:
    """The amounts in euros of the text that appear nowhere in what the model was given. As for durations, the
    difference between the totals of two options may be compared, and a whole amount may round one given."""
    allowed = {value for _, value in amounts(known)}
    totals = [itinerary["total_cost"] for itinerary in shown if itinerary.get("total_cost") is not None]
    allowed |= {abs(first - second) for first in totals for second in totals}
    unknown = [written.strip() for written, value in amounts(text)
               if not any(abs(value - known_value) < 0.01 for known_value in allowed)
               and not (value == int(value) and any(abs(value - known_value) <= ROUNDED_EUR for known_value in allowed))]
    count("prices_unknown", len(set(unknown)))
    return [tr("claim_unknown_prices", values=listed(list(dict.fromkeys(unknown))))] if unknown else []

def check_claims(text: str, known: str, shown: list[dict[str, Any]], recommended: int, colours: bool = True) -> str:
    """The model's text, with the colours it got wrong for one part corrected, and a note under it for each claim
    the options or the report contradict. known is everything the model was given: the reports and the notes.
    Without colours only the durations and times are checked: in a round trip "option 2" may be of either way."""
    if not text:
        return text
    text, problems = check_lights(text, shown, recommended) if colours else (text, [])
    problems += check_numbers(text, known, shown) + check_prices(text, known, shown)
    return text + "".join(f"\n\n> {problem}" for problem in problems)
