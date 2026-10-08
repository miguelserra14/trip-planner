"""Travel dates the way a traveller writes them: "17/12", "17 dez", "17-19/12", "17 e 18 de dezembro", "sexta",
"amanhã". Read into real days by `read_dates`. Whatever has no year is the first such day from today on."""
import re
from datetime import date, timedelta

from trip_planner.utils import plain

# The first three letters of a month or of a weekday, in Portuguese and in English, without accents
MONTHS = {"jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7,
          "ago": 8, "aug": 8, "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12}
WEEKDAYS = {"seg": 0, "mon": 0, "ter": 1, "tue": 1, "qua": 2, "wed": 2, "qui": 3, "thu": 3, "sex": 4, "fri": 4,
            "sab": 5, "sat": 5, "dom": 6, "sun": 6}
RELATIVE = {"hoje": 0, "today": 0, "amanha": 1, "tomorrow": 1, "depois amanha": 2, "day after tomorrow": 2}
FILLER = r"\b(de|do|da|dia|of|the|on|na|no|proxima|proximo|next|feira)\b|-feira"   # words that carry no date
RANGE_WORD = r"(?:-|–|\ba\b|\bate\b|\bao\b|\bto\b|\buntil\b)"                       # between the two ends of a range
MAX_RANGE_DAYS = 60   # a longer range is a mistake, not a choice of travel dates
OVER_MONTH_END_DAYS = 10   # "30-2/1" runs over the end of a month: only believed when it is this short

class UnreadableDate(ValueError):
    """Something written as a date that is not one. key is a text of texts.py and values what it needs."""

    def __init__(self, key: str, **values):
        super().__init__(key)
        self.key, self.values = key, values

def month_day(month: int, day: int, year: int | None, after: date) -> date:
    """A day of a month. Without a year it is the first one on or after `after`. Raises ValueError if there is none."""
    if year is not None:
        return date(year, month, day)
    for candidate in (after.year, after.year + 1):
        try:
            if date(candidate, month, day) >= after:
                return date(candidate, month, day)
        except ValueError:
            continue   # 29 February in a year that has none
    raise ValueError(f"no {day}/{month}")

def tidy(written: str) -> str:
    """What was written without accents, capitals or the words that carry no date: "17 de Dezembro" is "17 dezembro"."""
    return re.sub(r"\s+", " ", re.sub(FILLER, " ", plain(written))).strip()

def one_day(written: str, today: date, after: date) -> date:
    """One day as it was written. Raises ValueError if it is not a day. after is the day it cannot come before:
    today, or the first day of the range it ends."""
    text = tidy(written)
    if text in RELATIVE:
        return today + timedelta(days=RELATIVE[text])
    if re.fullmatch(r"[a-z]+", text) and text[:3] in WEEKDAYS:
        # "sexta" is the next Friday, never today: today is written "hoje"
        return after + timedelta(days=(WEEKDAYS[text[:3]] - after.weekday() - 1) % 7 + 1)
    if found := re.fullmatch(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})", text):
        return date(int(found[1]), int(found[2]), int(found[3]))
    if found := re.fullmatch(r"([0-9]{1,2}) ?[/.-] ?([0-9]{1,2})(?: ?[/.-] ?([0-9]{4}))?", text):
        return month_day(int(found[2]), int(found[1]), int(found[3]) if found[3] else None, after)
    named = (re.fullmatch(r"(?P<day>[0-9]{1,2}) ?(?P<month>[a-z]{3,})(?: (?P<year>[0-9]{4}))?", text)
             or re.fullmatch(r"(?P<month>[a-z]{3,}) (?P<day>[0-9]{1,2})(?: (?P<year>[0-9]{4}))?", text))
    if named and named["month"][:3] in MONTHS:
        return month_day(MONTHS[named["month"][:3]], int(named["day"]), int(named["year"]) if named["year"] else None, after)
    if re.fullmatch(r"[0-9]{1,2}", text):
        # Only the day: this month if it is still to come, otherwise the first month after it that has that day
        for months_ahead in range(3):
            month = after.month - 1 + months_ahead
            try:
                day = date(after.year + month // 12, month % 12 + 1, int(text))
            except ValueError:
                continue   # the 31st of a month that has 30 days
            if day >= after:
                return day
    raise ValueError(written)

def one_entry(written: str, today: date) -> list[date]:
    """One day, or every day of a range such as "17-19/12" or "17/12 a 19/12"."""
    text, clean = written.strip(), tidy(written)
    try:
        # "17-19/12", "17 a 19 dez": a day number, then a whole date that gives the month to both
        compact = re.fullmatch(rf"([0-9]{{1,2}}) ?{RANGE_WORD} ?([0-9]{{1,2}} ?(?:[/.] ?[0-9]|[a-z]{{3}}).*)", clean)
        ends = None
        if compact:
            try:
                end = one_day(compact[2], today, today)
                start = end.replace(day=1) - timedelta(days=1) if int(compact[1]) > end.day else end   # "30-2/1" starts in December
                ends = (start.replace(day=int(compact[1])), end)
            except ValueError:
                ends = None   # not a range after all: "17-12" is the 17th of December
            if ends and ends[0].month != end.month and (end - ends[0]).days > OVER_MONTH_END_DAYS:
                # "19-17/12" is two days the wrong way round, not a month of travel dates
                raise UnreadableDate("bad_range", value=text, start=f"{end.replace(day=int(compact[1])):%d/%m/%Y}",
                                     end=f"{end:%d/%m/%Y}", maximum=MAX_RANGE_DAYS)
        if ends is None:
            # "17/12 a 19/12", "sexta até domingo", "17/12 - 19/12": two whole dates
            parts = re.split(r" +(?:a|ate|ao|to|until) +| +[-–] +|–|-(?=[0-9]{1,2} ?[/.] ?[0-9])", clean)
            if len(parts) == 2:
                start = one_day(parts[0], today, today)
                ends = (start, one_day(parts[1], today, start))
        if ends is None:
            return [one_day(text, today, today)]
    except UnreadableDate:
        raise
    except ValueError:
        raise UnreadableDate("bad_date", value=text)
    days = (ends[1] - ends[0]).days
    if not 0 <= days <= MAX_RANGE_DAYS:
        raise UnreadableDate("bad_range", value=text, start=f"{ends[0]:%d/%m/%Y}", end=f"{ends[1]:%d/%m/%Y}", maximum=MAX_RANGE_DAYS)
    return [ends[0] + timedelta(days=number) for number in range(days + 1)]

def read_dates(written: str | list[str], today: date | None = None) -> list[date]:
    """Every day written, in order and without repeats. Raises UnreadableDate for what is not a date.
    written is one text, with the dates separated by commas, semicolons, "e" or "and", or a list of such texts."""
    today = today or date.today()
    text = written if isinstance(written, str) else ", ".join(written)
    entries = [entry.strip() for entry in re.split(r"[,;\n]| +(?:e|and|ou|or) +", text) if entry.strip()]
    days: list[date] = []
    following: date | None = None   # "17, 18 e 19/12": a lone day number takes the month of the date after it
    for entry in reversed(entries):
        if re.fullmatch(r"[0-9]{1,2}", entry) and following is not None:
            try:
                found = [following.replace(day=int(entry))]
            except ValueError:
                raise UnreadableDate("bad_date", value=entry)
        else:
            found = one_entry(entry, today)
        following = found[0]
        days += found
    return sorted(set(days))
