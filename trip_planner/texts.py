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
from pathlib import Path

from trip_planner import current
from trip_planner.utils import plain

FOLDER = Path(__file__).parent / "languages"
REFERENCE = "en"   # the language every text exists in

def load_languages() -> dict[str, dict[str, str]]:
    """The texts of every language of the folder, the reference first: {language code: {key: text}}."""
    files = sorted(FOLDER.glob("*.py"), key=lambda file: (file.stem != REFERENCE, file.stem))
    return {file.stem: runpy.run_path(str(file))["TEXTS"] for file in files}

TEXTS = load_languages()
# Language code -> its name in English, which is how the models are told what language to write in
LANGUAGES = {code: texts.get("language_english_name", code) for code, texts in TEXTS.items()}

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
    return text_in(current.SETTINGS.language, key, **values)

def words(key: str) -> list[str]:
    """Every word of a list text ("and,or") in every language, without accents or capitals: a traveller may write
    a date, or a model its answer, in any of them."""
    found = [plain(word) for texts in TEXTS.values() for word in texts.get(key, "").split(",")]
    return list(dict.fromkeys(word for word in found if word))

def any_of(key: str) -> str:
    """The words of a list text as a regular expression that matches any of them, the longest first."""
    return "|".join(re.escape(word) for word in sorted(words(key), key=len, reverse=True))
