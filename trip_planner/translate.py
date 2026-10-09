"""Add, remove and check the languages of the program. A language is one file of languages/ (see texts.py):

    python main.py --languages             the languages there are, and what is missing in each
    python main.py --add-language it       write languages/it.py, translated from English by a model
    python main.py --remove-language it    delete languages/it.py

Adding a language that is already there only fills in the texts it is missing, so it is also how to bring every
language up to date after new texts were written in en.py: python main.py --add-language all"""
import asyncio
import json
import re
import string

from trip_planner.texts import FOLDER, load_languages, REFERENCE
from trip_planner.utils import message_text

BATCH = 45    # texts translated in one request
CODE = r"[A-Za-z]{2,3}(-[A-Za-z]{2,4})?"   # what a language code looks like: it, fr, pt-BR
# Texts that are lists of a fixed size: a translation with more or fewer items is refused
FIXED_LISTS = {"cal_months": 12, "cal_weekdays": 7, "date_weekdays": 7}
# What the model must know about some texts to translate them well
NOTES = {
    "language_name": "the name of the target language as its own speakers write it, such as 'Italiano'",
    "language_english_name": "the name of the target language in English, such as 'Italian'. Do not translate it",
    "option_word": "the one word used for 'Option' in the other texts, in lowercase",
    "cal_months": "the twelve months, full names",
    "cal_weekdays": "the seven days from Monday to Sunday, abbreviated to about three letters",
    "date_weekdays": "the seven days from Monday to Sunday, full names, in lowercase unless the language capitalises them",
    "date_today": "what a traveller would type for 'today'. Several ways are welcome, separated by commas",
    "date_tomorrow": "what a traveller would type for 'tomorrow'. Several ways are welcome, separated by commas",
    "date_day_after": "what a traveller would type for 'the day after tomorrow'. Several ways are welcome, separated by commas",
    "date_range_words": ("every word that can stand between the two dates of a range, as in '17 to 19 December' or '17 until 19': "
                         "give all the forms, also the ones joined to an article (Italian: a,al,fino a,fino al; French: à,au,jusqu'au)"),
    "date_and_words": "the words between two separate dates, as in '17 and 18 December' or '17 or 18 December'",
    "date_ignored_words": ("small words that appear in written dates and carry no date: articles, 'of', 'next', 'day', and the word "
                           "that opens a range, 'from', in all its forms (Italian: da,dal; French: de,du)"),
    "ui_dates_hint": "examples of dates as a traveller would type them in the target language",
}

def problem(key: str, original: str, translated) -> str:
    """Why a translation cannot be used, or an empty text. What the code relies on must have survived."""
    if not isinstance(translated, str) or not translated.strip():
        return "empty"
    fields = lambda text: sorted(name for _, name, _, _ in string.Formatter().parse(text) if name is not None)
    try:
        if fields(translated) != fields(original):
            return f"the values between braces changed: {fields(original)} became {fields(translated)}"
    except ValueError:
        return "a brace is not closed"
    if sorted(re.findall(r"`[^`]*`", translated)) != sorted(re.findall(r"`[^`]*`", original)):
        return "a name between backticks changed"
    if key in FIXED_LISTS and len([item for item in translated.split(",") if item.strip()]) != FIXED_LISTS[key]:
        return f"must have {FIXED_LISTS[key]} items separated by commas"
    if "\n" in translated:
        return "more than one line"
    return ""

def missing(code: str, languages: dict) -> dict[str, str]:
    """The texts of the reference that a language lacks or has in a form that cannot be used: {key: reason}."""
    texts = languages[code]
    return {key: "missing" if key not in texts else problem(key, original, texts[key])
            for key, original in languages[REFERENCE].items() if key not in texts or problem(key, original, texts[key])}

def show_languages() -> int:
    languages = load_languages()
    for code, texts in languages.items():
        gaps = missing(code, languages)
        status = "the reference" if code == REFERENCE else "complete" if not gaps else f"{len(gaps)} texts shown in English"
        print(f"{code:8} {texts.get('language_name', '?'):20} {len(texts):4} texts   {status}")
        for key, reason in list(gaps.items())[:8]:
            print(f"           {key}: {reason}")
        if len(gaps) > 8:
            print(f"           ... and {len(gaps) - 8} more")
    print("\nAdd one:    python main.py --add-language <code>      Remove one: python main.py --remove-language <code>")
    return 0

def write_language(code: str, texts: dict[str, str]) -> None:
    """Write a language file in the order and with the sections of the reference, one text per line."""
    lines = [f'"""{texts.get("language_name", code)} ({texts.get("language_english_name", code)}). Translated from en.py by a model, '
             f'with `python main.py --add-language {code}`:\nread it once. One text per line: a text that is missing here is shown in English.\n'
             'Keep what is between {braces} and between `backticks` exactly as in en.py."""\n', "TEXTS = {"]
    for line in (FOLDER / f"{REFERENCE}.py").read_text(encoding="utf-8").splitlines():
        entry = re.fullmatch(r'    ("[^"]+"): ".*",', line)
        if entry and json.loads(entry[1]) in texts:
            lines.append(f"    {entry[1]}: {json.dumps(texts[json.loads(entry[1])], ensure_ascii=False)},")
        elif line.startswith("    # ----"):
            lines.append(line)
    (FOLDER / f"{code}.py").write_text("\n".join(lines + ["}"]) + "\n", encoding="utf-8", newline="\n")

async def translate(model, target: str, batch: dict[str, str], complaints: dict[str, str]) -> dict:
    """One request: the texts of batch into the target language. complaints are what was wrong with a first attempt."""
    notes = {key: NOTES[key] for key in batch if key in NOTES}
    prompt = f"""You translate the texts of a trip planner from English into {target}.
Answer with one JSON object and nothing else: the same keys, each with its text translated.

Rules:
- Whatever is between curly braces, such as {{airport}} or {{count}}, stays exactly as it is, braces included.
- Whatever is between backticks, such as `latest_arrival`, stays exactly as it is.
- Markdown marks (** and |), emojis, numbers, EUR, airport codes and example times stay as they are.
- A text that is a list separated by commas stays a list separated by commas, in the same order.
- Address the traveller informally, in the second person singular, and keep the texts as short and plain as the English.
- Every text stays on one line.
{"Some texts need more than a translation:" + chr(10) + chr(10).join(f"- {key}: {note}" for key, note in notes.items()) if notes else ""}
{"A first attempt at these was refused, mind the reason:" + chr(10) + chr(10).join(f"- {key}: {reason}" for key, reason in complaints.items()) if complaints else ""}

The texts:
{json.dumps(batch, ensure_ascii=False, indent=0)}"""
    answer = message_text(await model.ainvoke(prompt))
    try:
        found = json.loads(answer[answer.index("{"):answer.rindex("}") + 1])
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}

async def fill_language(model, code: str, languages: dict) -> int:
    """Translate what a language is missing and write its file. Returns how many texts are left in English."""
    reference, texts = languages[REFERENCE], dict(languages.get(code, {}))
    if "language_english_name" not in texts:
        # What language is this? The code alone must be enough for the model, or nothing good will follow
        answer = message_text(await model.ainvoke(
            f"Which language has the code '{code}' (ISO 639, possibly with a country)? Answer with one JSON object: "
            '{"known": true or false, "language_english_name": its name in English, "language_name": its name in itself}'))
        try:
            identity = json.loads(answer[answer.index("{"):answer.rindex("}") + 1])
        except ValueError:
            identity = {}
        if not identity.get("known") or not identity.get("language_english_name"):
            print(f"'{code}' is not a language code the model knows. Use a code such as it, fr, de, es or pt-BR.")
            return -1
        texts["language_english_name"] = str(identity["language_english_name"])
        texts["language_name"] = str(identity.get("language_name") or identity["language_english_name"])
    target = texts["language_english_name"]
    wanted = {key: original for key, original in reference.items() if key not in texts or problem(key, original, texts[key])}
    if not wanted:
        print(f"{code}: complete, nothing to translate.")
        return 0   # and the file stays as it is, with whatever was written in it by hand
    print(f"{code}: translating {len(wanted)} texts into {target}...", flush=True)
    complaints: dict[str, str] = {}
    for attempt in range(2):   # what is refused the first time is asked for once more, with the reason
        keys = list(wanted)
        batches = [{key: wanted[key] for key in keys[start:start + BATCH]} for start in range(0, len(keys), BATCH)]
        answers = await asyncio.gather(*(translate(model, target, batch, {k: complaints[k] for k in batch if k in complaints})
                                         for batch in batches))
        complaints = {}
        for batch, answer in zip(batches, answers):
            for key, original in batch.items():
                reason = problem(key, original, answer.get(key))
                if reason:
                    complaints[key] = reason
                else:
                    texts[key] = answer[key].strip()
        wanted = {key: reference[key] for key in complaints}
        if not wanted:
            break
    write_language(code, texts)
    print(f"{code}: written to {FOLDER / (code + '.py')}, {len(reference) - len(wanted)} of {len(reference)} texts in {target}.")
    for key, reason in wanted.items():
        print(f"    left in English: {key} ({reason})")
    return len(wanted)

def add_language(code: str) -> int:
    languages = load_languages()
    codes = [other for other in languages if other != REFERENCE] if code == "all" else [code]
    if code == REFERENCE:
        print(f"{REFERENCE} is the reference: its texts are written by hand in {FOLDER / (REFERENCE + '.py')}.")
        return 1

    async def work() -> int:
        # Only translating needs a model: listing and removing languages work without the API keys
        from langchain.chat_models import init_chat_model
        from trip_planner.agents.subagents import SUBAGENT_MODEL
        model = init_chat_model(SUBAGENT_MODEL, max_retries=4)
        if code != "all" and not re.fullmatch(CODE, code):
            # A name instead of a code, such as "italiano" or "French": the model says which code it is
            answer = message_text(await model.ainvoke(
                f"Which language is meant by '{code}'? Answer with its ISO 639-1 code only, such as it, or with ? if it is not a language."))
            found = answer.strip().strip("`'\". ").lower()
            if not re.fullmatch(CODE, found):
                print(f"'{code}' is not a language the model knows. Use a name or a code such as it, fr, de, es or pt-BR.")
                return 1
            codes[0] = found
        left = [await fill_language(model, each, languages) for each in codes]
        return 1 if any(count < 0 for count in left) else 0
    result = asyncio.run(work())
    if result == 0:
        print('It is ready to use: choose it in the window, or set language="' + codes[-1] + '" in settings.py.' if code != "all" else "Every language is up to date.")
    return result

def remove_language(code: str) -> int:
    # By its code, or by its name in itself or in English: "it", "italiano" or "Italian"
    for other, texts in load_languages().items():
        if code.lower() in (other.lower(), texts.get("language_name", "").lower(), texts.get("language_english_name", "").lower()):
            code = other
    file = FOLDER / f"{code}.py"
    if code == REFERENCE:
        print(f"{REFERENCE} cannot be removed: it is the language the others fall back to.")
        return 1
    if not file.exists():
        print(f"There is no language '{code}'. The languages are: {', '.join(load_languages())}.")
        return 1
    file.unlink()
    print(f"{code} removed. The languages are now: {', '.join(load_languages())}.")
    settings = (FOLDER.parent.parent / "settings.py").read_text(encoding="utf-8")
    if re.search(rf"language\s*=\s*[\"']{re.escape(code)}[\"']", settings):
        print(f'settings.py still asks for language="{code}": change it there, or the program will not start.')
    return 0
