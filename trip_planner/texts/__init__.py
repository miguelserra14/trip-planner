"""Everything the traveller reads (the airport list, the progress lines and the final report) is written through
`tr`, in the language chosen in the settings.

Each language is one file of the folder languages/, named after its code: en.py, pt-PT.py. Whatever file is there is
a language of the program, with nothing else to change:
- to add one,     python main.py --add-language it      (translated by a model: read it once)
- to remove one,  python main.py --remove-language it   (or delete its file)
- to see them,    python main.py --languages            (and what is missing in each)
A text that a language does not have is shown in English."""
import re
import runpy
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trip_planner import current
from trip_planner.utils import plain

FOLDER = Path(__file__).parent / "languages"
REFERENCE = "en"   # the language every text exists in
# The language tr speaks for a while instead of the trip's (in_language), in this thread only: the other threads of
# a map's process go on writing its progress in the trip's language meanwhile
SPOKEN = threading.local()

def load_languages() -> dict[str, dict[str, str]]:
    """The texts of every language of the folder, the reference first: {language code: {key: text}}."""
    files = sorted(FOLDER.glob("*.py"), key=lambda file: (file.stem != REFERENCE, file.stem))
    return {file.stem: runpy.run_path(str(file))["TEXTS"] for file in files}

TEXTS: dict[str, dict[str, str]] = {}
# Language code -> its name in English, which is how the models are told what language to write in
LANGUAGES: dict[str, str] = {}

def read_languages() -> None:
    """Fill TEXTS and LANGUAGES from the folder. In place: the other modules hold these same two dictionaries, so
    a language added or removed while the program is open (translate.refresh) reaches all of them."""
    found = load_languages()
    for held, new in ((TEXTS, found), (LANGUAGES, {code: texts.get("language_english_name", code) for code, texts in found.items()})):
        held.clear()
        held.update(new)

read_languages()

def text_in(language: str, key: str, **values) -> str:
    """A text in one language. In English if that language does not have it, or has it with the wrong {values}."""
    for code in (language, REFERENCE):
        text = TEXTS.get(code, {}).get(key)
        if text is not None:
            try:
                return text.format(**values) if values else text
            except (KeyError, IndexError, ValueError):
                continue   # a translation that lost or invented a {value}
    raise KeyError(key)

def tr(key: str, **values) -> str:
    """A text in the language of the trip being planned."""
    return text_in(getattr(SPOKEN, "language", None) or current.SETTINGS.language, key, **values)

@contextmanager
def in_language(language: str):
    """While it lasts, tr speaks this language: a page written once in every language (Discover's map, whose
    traveller may change it after the map was made), without touching the trip's settings."""
    before, SPOKEN.language = getattr(SPOKEN, "language", None), language
    try:
        yield
    finally:
        SPOKEN.language = before

class Said(str):
    """A text in the trip's language that remembers how it was made, so that a page can say it again in the language
    it is read in (kept, say_again): the warnings of a report, read later under another language than the search's."""
    key: str
    values: dict

class Listed(str):
    """Items in a sentence ("LIS and OPO"), remembering the items, for the same reason."""
    items: list

def said(key: str, **values) -> Said:
    """tr, remembering the key and the values. A value may itself be Said or Listed: it is said again too."""
    text = Said(tr(key, **values))
    text.key, text.values = key, values
    return text

def listed(items: list[str]) -> Listed:
    """Items in a sentence: "LIS", "LIS and OPO", "LIS, OPO and FAO", in the language of the trip."""
    text = Listed(items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {tr('list_and')} {items[-1]}")
    text.items = list(items)
    return text

def kept(text: str) -> Any:
    """A text as data, to be said again: {"key", "values"} for one made by said, {"list"} for one made by listed,
    the text itself for any other."""
    if isinstance(text, Said):
        return {"key": text.key, "values": {name: kept(value) if isinstance(value, str) else value for name, value in text.values.items()}}
    if isinstance(text, Listed):
        return {"list": [kept(item) for item in text.items]}
    return str(text)

def say_again(text: Any, language: str) -> str:
    """A text kept as data said in a language. A text kept as it was (an older report) is said as it was."""
    if isinstance(text, dict) and "list" in text:
        items = [say_again(item, language) for item in text["list"]]
        return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {text_in(language, 'list_and')} {items[-1]}"
    if isinstance(text, dict) and "key" in text:
        try:
            return text_in(language, text["key"], **{name: say_again(value, language) for name, value in text["values"].items()})
        except KeyError:
            return ""   # a key no language has any more
    return text if isinstance(text, str) else str(text)

def words(key: str) -> list[str]:
    """Every word of a list text ("and,or") in every language, without accents or capitals: a traveller may write
    a date, or a model its answer, in any of them."""
    found = [plain(word) for texts in TEXTS.values() for word in texts.get(key, "").split(",")]
    return list(dict.fromkeys(word for word in found if word))

def any_of(key: str) -> str:
    """The words of a list text as a regular expression that matches any of them, the longest first."""
    return "|".join(re.escape(word) for word in sorted(words(key), key=len, reverse=True))
