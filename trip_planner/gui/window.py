"""The main window: the main page, the list of airports, the progress, the result and the follow-up questions."""
import asyncio
import dataclasses
import importlib
import os
import queue
import re
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from datetime import date, timedelta
from tkinter import ttk
from typing import Any

from trip_planner import current, translate, utils
from trip_planner.config import LANGUAGES, TripSettings
from trip_planner.ground_choice import NO_CHANGE_LIMIT
from trip_planner.gui.advanced import AdvancedSettings, CHANGES, MAIN_SETTINGS, NOT_IN_WINDOW, same, shown
from trip_planner.gui.dialogs import DatePicker, LanguageManager, PlaceChooser
from trip_planner.gui.output import OutputView
from trip_planner.gui.style import FONT, screen_scale, style, THEME
from trip_planner.places import airport_groups, choose_airports, choose_place, unclear_places, remove_airports, too_far_notice, TripPlaces
from trip_planner.texts import REFERENCE, TEXTS, tr

# The search and the models take several seconds to load, so the window opens without them. They are loaded behind
# it as soon as it is open (load_search), and whatever needs them takes them on the planner's thread (loaded).
SEARCH_MODULES = ("planner", "check", "sources.flight_search")

def load_search() -> list:
    return [importlib.import_module(f"trip_planner.{name}") for name in SEARCH_MODULES]

async def loaded() -> list:
    """The modules of SEARCH_MODULES, in that order. Waits for them when they are still being loaded."""
    return await asyncio.to_thread(load_search)

def planned() -> list:
    """Every trip planned in this window (planner.PLANNED). None before the search is loaded."""
    return getattr(sys.modules.get("trip_planner.planner"), "PLANNED", [])

class LineWriter:
    """Stands in for the terminal: whatever is printed becomes a line in the window."""

    def __init__(self, events: queue.Queue):
        self.events, self.pending = events, ""

    def write(self, text: str) -> int:
        *lines, self.pending = (self.pending + text).split("\n")
        for line in lines:
            self.events.put(("say", line))
        return len(text)

    def flush(self) -> None:
        pass

class TripWindow:
    def __init__(self, settings: TripSettings):
        self.defaults = settings          # settings.py: what the form starts with, and every setting it does not show
        self.settings = settings          # the trip in the form, once it is valid
        self.places: TripPlaces | None = None
        self.boxes: dict[str, tk.BooleanVar] = {}
        self.events: queue.Queue = queue.Queue()
        self.advice = ""
        self.manager: LanguageManager | None = None   # the list of languages, while it is open
        self.advanced: AdvancedSettings | None = None  # the advanced settings, while they are open
        current.SETTINGS = settings

        # The planner's thread. One loop for the whole session: the saved trips and the Kiwi connection live in it.
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        utils.LISTENERS["say"] = lambda text: self.events.put(("say", text))
        utils.LISTENERS["show"] = lambda markdown: self.events.put(("show", markdown))
        sys.stdout = LineWriter(self.events)
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")   # started without a terminal: warnings have nowhere to go

        self.root = tk.Tk()
        self.root.title(tr("ui_title"))
        self.root.minsize(820, 560)
        self.root.option_add("*Font", (FONT, 10))
        style(self.root)
        self.root.report_callback_exception = lambda *error: self.failed("".join(traceback.format_exception(*error)))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        # As wide as the form asks for, so that no label is cut on a scaled screen, and room for the results
        self.root.update_idletasks()
        width = min(max(self.root.winfo_reqwidth(), round(800 * screen_scale(self.root))), self.root.winfo_screenwidth() - 80)
        self.root.geometry(f"{width}x{min(max(820, self.root.winfo_reqheight()), self.root.winfo_screenheight() - 120)}")
        self.root.after(100, self.poll)
        self.root.after(200, self.load)

    def load(self) -> None:
        """Load the search behind the window, once it is on the screen. What fails here is usually a key missing
        in env/.env: it is said in the window, and said again by whatever is tried next."""
        def work() -> None:
            try:
                load_search()
            except Exception as error:
                self.events.put(("say", traceback.format_exc()))
                self.events.put(("failed", f"{type(error).__name__}: {error}"))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- the widgets
    def build(self) -> None:
        self.header = ttk.Frame(self.root, padding=(14, 10, 14, 0))
        self.header.pack(fill="x")
        top = ttk.Frame(self.root, padding=(14, 8, 14, 4))
        top.pack(fill="x")
        self.form = ttk.Frame(top)
        self.form.pack(fill="x")
        self.airports = ttk.Frame(top)    # takes the place of the form while the airports are being chosen
        self.make_variables()
        self.build_header()
        self.build_form()

        status = ttk.Frame(self.root, padding=(14, 2))
        status.pack(fill="x")
        self.status = ttk.Label(status, wraplength=1000, justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=160)

        bottom = ttk.Frame(self.root, padding=(14, 4, 14, 12))
        bottom.pack(side="bottom", fill="x")
        self.question_label = ttk.Label(bottom, text=tr("ui_question"))
        self.question_label.pack(side="left")
        self.question = ttk.Entry(bottom, width=10)   # it takes the room left: the form decides how wide the window is
        self.question.pack(side="left", fill="x", expand=True, padx=8)
        self.question.bind("<Return>", lambda event: self.ask())
        self.ask_button = ttk.Button(bottom, text=tr("ui_ask"), command=self.ask, style="Accent.TButton")
        self.ask_button.pack(side="left")
        self.report_button = ttk.Button(bottom, text=tr("ui_open_report"), command=self.open_report)
        self.report_button.pack(side="left", padx=(8, 0))
        self.keep = tk.BooleanVar(value=False)
        self.keep_box = ttk.Checkbutton(bottom, text=tr("ui_keep_report"), variable=self.keep, command=self.keep_report)
        self.keep_box.pack(side="left", padx=(10, 0))

        middle = ttk.Frame(self.root, padding=(14, 4))
        middle.pack(fill="both", expand=True)
        self.output = OutputView(middle)
        self.busy(False)
        self.asking(False)
        self.output.write(tr("ui_welcome") + "\n", "log")   # what to do, until the first search clears it

    def make_variables(self) -> None:
        """One variable per setting the window shows, on the main page or in the advanced settings, starting with
        what settings.py says. Made once: the widgets are built again when the language changes, and keep them."""
        self.values: dict[str, tk.Variable] = {}
        for setting in dataclasses.fields(TripSettings):
            if setting.name in NOT_IN_WINDOW:
                continue
            value = shown(setting.name, getattr(self.defaults, setting.name))
            variable = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=value)
            variable.trace_add("write", lambda *changed: self.count_changes())
            self.values[setting.name] = variable
        self.origin, self.destination = self.values["origin"], self.values["destination"]
        self.dates = tk.StringVar(value="")   # clicked in the calendar every time
        self.language = tk.StringVar(value=self.defaults.language)

    def value(self, name: str) -> Any:
        """A setting as the window holds it, as TripSettings reads it: "no limit" of the changes is its number."""
        value = self.values[name].get()
        if name in CHANGES:
            return int(value) if str(value).isdigit() else NO_CHANGE_LIMIT
        return value

    def changed(self) -> list[str]:
        """The settings of the advanced settings that differ from settings.py."""
        default = lambda name: getattr(self.defaults, name) if name in CHANGES else shown(name, getattr(self.defaults, name))
        return [name for name in self.values if name not in MAIN_SETTINGS and not same(self.value(name), default(name))]

    def count_changes(self) -> None:
        """The advanced settings button says how many settings differ from settings.py: they are not on the screen."""
        if getattr(self, "advanced_button", None) is not None and self.advanced_button.winfo_exists():
            count = len(self.changed())
            self.advanced_button.configure(text=tr("ui_advanced_changed", count=count) if count else tr("ui_advanced"))

    def show_limits(self) -> None:
        """No limit of changes is shown in words, in the language of the window."""
        for name in CHANGES:
            value = self.values[name].get()
            if not str(value).isdigit() or int(value) >= NO_CHANGE_LIMIT:
                self.values[name].set(tr("ui_changes_any"))

    def build_header(self) -> None:
        """The name of the program, and the language on the right, as on a travel site."""
        for child in self.header.winfo_children():
            child.destroy()
        ttk.Label(self.header, text=tr("ui_title"), font=(FONT, 15, "bold")).pack(side="left")
        self.language_box = ttk.Combobox(self.header, textvariable=self.language, values=list(LANGUAGES), state="readonly", width=8)
        self.language_box.pack(side="right")
        self.language_box.bind("<<ComboboxSelected>>", lambda event: self.change_language())
        ttk.Label(self.header, text=tr("ui_language"), style="Muted.TLabel").pack(side="right", padx=(0, 6))

    def build_form(self) -> None:
        """The main page: where from, where to, when, and three ticks under them, in a white card with the search
        button, as on the first page of a flight search site. Everything else is in the advanced settings."""
        form = self.form
        for child in form.winfo_children():
            child.destroy()
        self.show_limits()
        border = tk.Frame(form, background=THEME["border"])   # a one-pixel border around the card
        border.pack(fill="x")
        card = ttk.Frame(border, style="Card.TFrame", padding=(14, 10, 14, 12))
        card.pack(fill="both", padx=1, pady=1)
        for column, key in ((0, "ui_from"), (2, "ui_to"), (3, "ui_dates")):
            ttk.Label(card, text=tr(key), style="Card.TLabel").grid(row=0, column=column, sticky="w", padx=(12 if column == 3 else 0, 0), pady=(0, 2))
        origin = ttk.Entry(card, textvariable=self.origin, font=(FONT, 11), width=14)
        origin.grid(row=1, column=0, sticky="we", ipady=4)
        ttk.Button(card, text="↔", width=2, command=self.swap, style="Swap.TButton").grid(row=1, column=1, padx=6)
        destination = ttk.Entry(card, textvariable=self.destination, font=(FONT, 11), width=14)
        destination.grid(row=1, column=2, sticky="we", ipady=4)
        # The dates are only clicked in the calendar: a click on the field opens it
        dates = ttk.Entry(card, textvariable=self.dates, font=(FONT, 11), width=16, state="readonly", cursor="hand2")
        dates.grid(row=1, column=3, sticky="we", padx=(12, 0), ipady=4)
        dates.bind("<Button-1>", lambda event: DatePicker(self))
        self.find_button = ttk.Button(card, text=tr("ui_search"), command=self.find_airports, style="Big.Accent.TButton")
        self.find_button.grid(row=1, column=4, sticky="ns", padx=(12, 0))
        # The ticks hold the same variables as the advanced settings: the scale of each side, and the stopovers
        for column, key, name, on, off in ((0, "ui_nearby", "departure_scale", "wide", "near"), (2, "ui_nearby", "arrival_scale", "wide", "near"),
                                           (3, "ui_direct", "max_stopovers", "0", "1")):
            ttk.Checkbutton(card, text=tr(key), variable=self.values[name], onvalue=on, offvalue=off, style="Card.TCheckbutton").grid(
                row=2, column=column, sticky="w", padx=(12 if column == 3 else 0, 0), pady=(10, 0))
        for column in (0, 2, 3):
            card.columnconfigure(column, weight=1, uniform="field")
        under = ttk.Frame(form)
        under.pack(fill="x", pady=(6, 0))
        ttk.Label(under, text=tr("ui_trip_kind"), style="Muted.TLabel").pack(side="left")
        self.advanced_button = ttk.Button(under, text=tr("ui_advanced"), command=self.open_advanced)
        self.advanced_button.pack(side="right")
        self.count_changes()
        for entry in (origin, destination):
            entry.bind("<Return>", lambda event: self.find_airports())
        if getattr(self, "running", False):   # the new button as the old one was
            self.find_button.configure(state="disabled")

    def swap(self) -> None:
        """Where from and where to change places, as on a flight search site."""
        origin = self.origin.get()
        self.origin.set(self.destination.get())
        self.destination.set(origin)

    def open_advanced(self) -> None:
        if self.advanced is not None:
            self.advanced.top.lift()
        else:
            AdvancedSettings(self)

    def check_advanced(self) -> str:
        """Whether the settings can be used, whatever the main page holds yet: the reason when they cannot, else ""."""
        tomorrow = f"{date.today() + timedelta(days=1):%d/%m/%Y}"
        try:
            dataclasses.replace(self.defaults, **({name: self.value(name) for name in self.values}
                                                  | {"origin": "A", "destination": "B", "dates": tomorrow, "language": self.language.get()}))
        except ValueError as error:
            # The reason names the setting as settings.py does: here it is named as the window shows it
            return re.sub(r"`([a-z_]+)`", lambda name: f"«{AdvancedSettings.label(name[1])}»", str(error))
        return ""

    def change_language(self) -> None:
        """A language was chosen in the list: the window speaks it at once, not only once the trip is searched.
        A search that is running carries on in its own language, and the window changes when it ends."""
        if self.running or self.language.get() == current.SETTINGS.language:
            return
        current.SETTINGS = dataclasses.replace(current.SETTINGS, language=self.language.get())
        self.root.title(tr("ui_title"))
        self.build_header()
        self.build_form()
        for widget, key in ((self.question_label, "ui_question"), (self.ask_button, "ui_ask"),
                            (self.report_button, "ui_open_report"), (self.keep_box, "ui_keep_report")):
            widget.configure(text=tr(key))
        if self.places is not None and self.airports.winfo_ismapped():
            ticked = {code: box.get() for code, box in self.boxes.items()}
            self.build_airports(self.places)
            for code, box in self.boxes.items():
                box.set(ticked.get(code, True))
        self.say_status("")
        for opened in (self.manager, self.advanced):
            if opened is not None:
                opened.close(check=False) if opened is self.advanced else opened.close()   # opened again, they speak the new one

    def build_airports(self, places: TripPlaces) -> None:
        """The airports found, a box each: ticked ones are searched."""
        for child in self.airports.winfo_children():
            child.destroy()
        self.boxes = {}
        ttk.Label(self.airports, text=f"{places.origin.label}  →  {places.destination.label}",
                  font=(FONT, 11, "bold")).pack(anchor="w")
        ttk.Label(self.airports, text=tr("ui_airports_instruction")).pack(anchor="w", pady=(0, 6))
        groups = ttk.Frame(self.airports)
        groups.pack(fill="x")
        for column, (title, airports) in enumerate(airport_groups(places)):
            group = ttk.LabelFrame(groups, text=title, padding=8)
            group.grid(row=0, column=column, sticky="nwe", padx=(0, 10))
            groups.columnconfigure(column, weight=1)
            if not airports:
                ttk.Label(group, text=f"({tr('none')})").pack(anchor="w")
            for airport in airports:
                self.boxes[airport["code"]] = tk.BooleanVar(value=True)
                ttk.Checkbutton(group, variable=self.boxes[airport["code"]],
                                text=f"{airport['code']} · {airport['km']} km · {airport['name']} ({airport['city']}, {airport['country']})",
                                ).pack(anchor="w")
        if places.unknown:
            ttk.Label(self.airports, text=tr("unknown_airports", entries=", ".join(places.unknown)),
                      foreground=THEME["error"]).pack(anchor="w", pady=(6, 0))
        if places.too_far:
            ttk.Label(self.airports, text=too_far_notice(places)).pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(self.airports)
        buttons.pack(fill="x", pady=(10, 2))
        self.search_button = ttk.Button(buttons, text=tr("ui_search"), command=self.search, style="Accent.TButton")
        self.search_button.pack(side="left")
        ttk.Button(buttons, text=tr("ui_back"), command=self.show_form).pack(side="left", padx=8)

    def show_form(self) -> None:
        self.airports.pack_forget()
        self.form.pack(fill="x")

    # ---------------------------------------------------------------- what the buttons do
    def read_form(self) -> TripSettings:
        """The trip on the main page and in the advanced settings. Raises ValueError, with the reason, if it is not valid."""
        return dataclasses.replace(self.defaults, **{name: self.value(name) for name in self.values},
                                   dates=self.dates.get(), language=self.language.get())

    def find_airports(self) -> None:
        if self.running:
            return
        if not self.dates.get():
            DatePicker(self)   # the dates are only chosen there
            return
        try:
            self.settings = self.read_form()
        except ValueError as error:
            self.say_status(str(error), error=True)
            return
        current.SETTINGS = self.settings   # from here on the texts are in the language of this trip
        self.dates.set(", ".join(self.settings.dates))   # how the dates were read, for the traveller to see
        self.advice = " ".join(f"{tr('advice')}: {advice}" for advice in self.settings.advice())

        async def work() -> None:
            # A name that several places have is chosen first, in a small window: then the airports are looked for again
            unclear = await asyncio.to_thread(unclear_places, self.settings)
            if unclear:
                self.events.put(("unclear", unclear[0]))
                return
            self.events.put(("airports", await asyncio.to_thread(choose_airports, self.settings)))
        self.start(work(), tr("ui_finding"))

    def place_chosen(self, setting: str, label: str, place) -> None:
        """The traveller chose which place a name means: the field says it, and the airports are looked for."""
        choose_place(label, place)
        (self.origin if setting == "origin" else self.destination).set(label)
        self.find_airports()

    def search(self) -> None:
        if self.running:
            return
        dropped = [code for code, box in self.boxes.items() if not box.get()]
        try:
            remove_airports(self.places, dropped)
        except ValueError as error:
            self.say_status(str(error), error=True)
            return
        settings, places = self.settings, self.places

        async def work() -> None:
            planner, _, flight_search = await loaded()
            await flight_search.connect()
            await planner.plan_trip(settings, places)
            self.events.put(("planned", None))
        self.output.clear()
        if self.advice:   # what is allowed but will probably disappoint, before the search starts
            self.output.write(self.advice + "\n", "log")
        self.asking(False)
        self.show_form()
        self.start(work(), tr("ui_searching"))

    def ask(self) -> None:
        question = self.question.get().strip()
        if self.running or not question or not planned():
            return

        async def work() -> None:
            planner, _, _ = await loaded()
            await planner.ask(question)
            self.events.put(("answered", None))
        self.question.delete(0, "end")
        self.start(work(), tr("ui_asking"))

    def check(self) -> None:
        if self.running:
            return

        async def work() -> None:
            _, check, _ = await loaded()
            await check.check_setup()
            self.events.put(("checked", None))
        self.output.clear()
        self.start(work(), tr("ui_checking"))

    def keep_report(self) -> None:
        """The box was ticked or unticked: the report of the last trip moves to saved/, or back to the recent ones."""
        if not planned() or planned()[-1].get("report") is None:
            return
        trip = planned()[-1]
        from trip_planner.report.full import keep_report   # loaded with the search: a report exists
        trip["report"] = keep_report(trip["report"], self.keep.get())
        note = tr("ui_report_kept", folder=trip["report"].parent) if self.keep.get() else tr("ui_report_not_kept", limit=current.SETTINGS.recent_reports)
        self.say_status(note)
        self.confirm(note, self.keep_box)

    def confirm(self, text: str, over: tk.Widget, seconds: float = 4) -> None:
        """A small note that shows up over a widget and goes away by itself: what was just clicked did happen."""
        if getattr(self, "note", None) is not None and self.note.winfo_exists():
            self.note.destroy()   # one at a time
        self.note = note = tk.Toplevel(self.root)
        note.overrideredirect(True)   # no title bar: it is not a window to deal with
        note.attributes("-topmost", True)
        tk.Label(note, text="✓  " + text, bg="#2e7d32", fg="#ffffff", padx=12, pady=7, wraplength=420, justify="left").pack()
        note.update_idletasks()
        # Just above the widget, without leaving the window on the right
        left = min(over.winfo_rootx(), self.root.winfo_rootx() + self.root.winfo_width() - note.winfo_reqwidth() - 14)
        note.geometry(f"+{max(left, self.root.winfo_rootx())}+{over.winfo_rooty() - note.winfo_reqheight() - 6}")
        note.after(int(seconds * 1000), note.destroy)

    def add_language(self, name: str) -> None:
        """Translate the program into one more language. A model does it, so it runs like a search does."""
        async def work() -> None:
            try:
                self.events.put(("language", await translate.add(name)))
            except translate.UnknownLanguage:
                raise ValueError(tr("ui_language_unknown", name=name)) from None
        self.start(work(), tr("ui_language_adding", name=name))

    def remove_language(self, code: str) -> None:
        language = TEXTS[code].get("language_name", code)
        translate.remove(code)
        self.languages_changed()
        self.say_status(" ".join([tr("ui_language_removed", name=language)]
                                 + [tr("ui_language_in_settings", code=code)] * translate.in_settings(code)))

    def languages_changed(self) -> None:
        """A language was added or removed: read them again and show them wherever they are listed."""
        translate.refresh()
        self.language_box.configure(values=list(LANGUAGES))
        if self.language.get() not in LANGUAGES:
            self.language.set(REFERENCE)
        # A trip in a language that is gone carries on in English
        for settings in (self.defaults, self.settings, current.SETTINGS, *(trip["settings"] for trip in planned())):
            if settings.language not in LANGUAGES:
                settings.language = REFERENCE
        if self.manager is not None:
            self.manager.draw()

    def open_report(self) -> None:
        path = planned()[-1].get("report") if planned() else None
        if path is None:
            return
        if hasattr(os, "startfile"):
            os.startfile(path)   # Windows: with whatever program opens .md files
        else:
            webbrowser.open(path.as_uri())

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()

    # ---------------------------------------------------------------- between the window and the planner's thread
    def start(self, work, status: str) -> None:
        """Hand a piece of work to the planner's thread. Whatever goes wrong comes back as an event."""
        async def guarded() -> None:
            try:
                await work
            except ValueError as error:      # a reason written for the traveller, such as a city that was not found
                self.events.put(("failed", str(error)))
            except Exception as error:
                self.events.put(("say", traceback.format_exc()))
                self.events.put(("failed", f"{type(error).__name__}: {error}"))
        self.busy(True)
        self.say_status(status)
        asyncio.run_coroutine_threadsafe(guarded(), self.loop)

    def poll(self) -> None:
        """Take what the planner's thread left in the queue, a few times a second."""
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "say":
                    self.output.write(value + "\n", "log")
                elif kind == "show":
                    self.output.write_markdown(value)
                elif kind == "unclear":   # (setting, name, meanings)
                    self.done("")
                    PlaceChooser(self, *value)
                elif kind == "airports":
                    self.places = value
                    if self.settings.confirm_airports:   # the traveller asked to review them first (advanced settings)
                        self.build_airports(value)
                        self.form.pack_forget()
                        self.airports.pack(fill="x")
                        self.done(self.advice)   # what is allowed but will probably disappoint, before the search starts
                    else:                                # as on a flight search site: the search goes on with all of them
                        self.boxes = {}
                        self.done("")
                        self.search()
                elif kind == "planned":
                    self.keep.set(False)   # a new report: it is only kept if the box is ticked again
                    self.asking(True)
                    self.done(tr("ui_done"))
                elif kind == "language":   # {code: texts that stayed in English}
                    self.languages_changed()
                    self.done(" ".join(
                        tr("ui_language_added", name=TEXTS[code].get("language_name", code))
                        + (f" ({tr('ui_language_incomplete', count=left)})" if left else "") for code, left in value.items()))
                elif kind in ("answered", "checked"):
                    self.done("")
                elif kind == "failed":
                    self.failed(value)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def done(self, status: str) -> None:
        self.busy(False)
        self.say_status(status)
        if self.language.get() != current.SETTINGS.language:   # chosen while the search was running
            self.change_language()
            self.say_status(status)

    def failed(self, reason: str) -> None:
        self.busy(False)
        self.say_status(tr("ui_failed", error=reason.strip().splitlines()[-1]), error=True)

    def busy(self, running: bool) -> None:
        self.running = running
        for button in (getattr(self, "find_button", None), getattr(self, "search_button", None),
                       getattr(self.advanced, "check_button", None)):
            if button is not None and button.winfo_exists():
                button.configure(state="disabled" if running else "normal")
        if running:
            self.progress.pack(side="right")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        self.asking(bool(planned()) and not running)
        if self.manager is not None:
            self.manager.draw()

    def asking(self, possible: bool) -> None:
        """Questions and the report only make sense once a trip was planned."""
        for widget in (self.question, self.ask_button, self.report_button, self.keep_box):
            widget.configure(state="normal" if possible else "disabled")

    def say_status(self, text: str, error: bool = False) -> None:
        for label in (self.status, self.manager.message if self.manager is not None else None):
            if label is not None:
                label.configure(text=text, foreground=THEME["error"] if error else THEME["text"])

def run(settings: TripSettings) -> int:
    """Open the window on the trip in settings.py and stay until it is closed."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on Windows screens that are scaled
    except Exception:
        pass
    TripWindow(settings).root.mainloop()
    return 0
