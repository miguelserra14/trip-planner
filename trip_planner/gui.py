"""The window: the trip is typed in a form, the airports are ticked in a list, and the progress, the result and the
follow-up questions all happen in the same window. Nothing is asked in the terminal.

The planner itself does not change. It runs on its own thread (an asyncio loop), and what it says and shows
(`say` and `show` in utils.py) is handed to the window through a queue, because only the main thread may touch it."""
import asyncio
import calendar
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
from tkinter import messagebox, ttk

from trip_planner import current, translate, utils
from trip_planner.config import GRADES, LANGUAGES, LIGHTS, MAX_DATES, MAX_DAYS_AHEAD, SCALES, TripSettings
from trip_planner.dates import read_dates
from trip_planner.places import airport_groups, choose_airports, choose_place, unclear_places, remove_airports, too_far_notice, TripPlaces
from trip_planner.texts import REFERENCE, TEXTS, tr

FONT = "Segoe UI"
TABLE_FONT = "Consolas"
# The reliability lights. The text of the window cannot draw the coloured symbols of the reports, so each one is
# written as a mark that takes one character and then painted as a dot of its colour.
LIGHT_COLOURS = dict(zip(GRADES, ("#1f6fd0", "#2e9e4f", "#e67e22")))
LIGHT_MARKS = {LIGHTS[grade]: chr(0xE000 + number) for number, grade in enumerate(GRADES)}
LIGHT_DOT = "●"
# The colours of the window, in one place: every widget takes them from here or from the styles of style()
THEME = {"background": "#f5f6f8", "surface": "#ffffff", "text": "#1f2328", "muted": "#6b7280", "border": "#d5d9e0",
         "hover": "#eceff3", "accent": "#2563eb", "accent_hover": "#1d4ed8", "accent_off": "#a9c0f5", "error": "#b42318"}

def screen_scale(root: tk.Tk) -> float:
    """How much the screen is scaled: 1 at 100%, 1.5 at 150%. Sizes in pixels are multiplied by it."""
    return float(root.tk.call("tk", "scaling")) / (96 / 72)

def check_box(root: tk.Tk, size: int, ticked: bool) -> tk.PhotoImage:
    """A square for the check buttons: the one of the theme is a few pixels wide whatever the screen."""
    t, image = THEME, tk.PhotoImage(master=root, width=size, height=size)
    image.put(t["accent"] if ticked else t["muted"], to=(0, 0, size, size))
    image.put(t["accent"] if ticked else t["surface"], to=(1, 1, size - 1, size - 1))
    if ticked:   # a tick in two strokes, as thick as a tenth of the square
        thick = max(2, size // 10)
        stroke = lambda x, y: image.put("#ffffff", to=(x, y, x + thick, y + thick))
        for x in range(size * 2 // 10, size * 4 // 10):
            stroke(x, size // 2 + (x - size * 2 // 10) - thick // 2)
        for x in range(size * 4 // 10, size * 8 // 10):
            stroke(x, size * 7 // 10 - (x - size * 4 // 10) - thick // 2)
    return image

def style(root: tk.Tk) -> None:
    """A flat, light look for the ttk widgets: "clam" is the theme that lets every colour be set.
    A primary button is ttk.Button(..., style="Accent.TButton"), a secondary text ttk.Label(..., style="Muted.TLabel")."""
    t, ttk_style = THEME, ttk.Style(root)
    arrow = round(14 * screen_scale(root))   # 14 pixels at 100%, more on a scaled screen
    ttk_style.theme_use("clam")
    root.configure(background=t["background"])
    root.option_add("*Toplevel.background", t["background"])
    ttk_style.configure(".", background=t["background"], foreground=t["text"], font=(FONT, 10), bordercolor=t["border"],
                        lightcolor=t["border"], darkcolor=t["border"], troughcolor=t["border"], focuscolor=t["accent"])
    for field in ("TEntry", "TCombobox", "TSpinbox"):
        ttk_style.configure(field, fieldbackground=t["surface"], padding=5, arrowcolor=t["muted"], arrowsize=arrow)
        ttk_style.map(field, bordercolor=[("focus", t["accent"])], lightcolor=[("focus", t["accent"])],
                      fieldbackground=[("readonly", t["surface"]), ("disabled", t["background"])],
                      selectbackground=[("readonly", t["surface"])], selectforeground=[("readonly", t["text"])])
    ttk_style.configure("TScrollbar", arrowsize=arrow, background=t["hover"], arrowcolor=t["muted"])
    ttk_style.configure("TButton", background=t["surface"], padding=(12, 5))
    ttk_style.map("TButton", background=[("disabled", t["background"]), ("active", t["hover"])], foreground=[("disabled", t["muted"])])
    ttk_style.configure("Accent.TButton", background=t["accent"], foreground="#ffffff", bordercolor=t["accent"],
                        lightcolor=t["accent"], darkcolor=t["accent"], font=(FONT, 10, "bold"))
    ttk_style.map("Accent.TButton", background=[("disabled", t["accent_off"]), ("active", t["accent_hover"])],
                  foreground=[("disabled", "#ffffff")])
    ttk_style.configure("Muted.TLabel", foreground=t["muted"])
    for choice in ("TCheckbutton", "TRadiobutton"):
        ttk_style.configure(choice, indicatorbackground=t["surface"])
        ttk_style.map(choice, indicatorbackground=[("selected", t["accent"])], background=[("active", t["background"])])
    # The check buttons draw their own square, as large as the text
    size = round(15 * screen_scale(root))
    root.check_boxes = [check_box(root, size, False), check_box(root, size, True)]   # kept, or Tk forgets the images
    ttk_style.element_create("Box.indicator", "image", root.check_boxes[0], ("selected", root.check_boxes[1]), padding=(0, 0, 6, 0))
    ttk_style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Box.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [("Checkbutton.label", {"sticky": "nswe"})]})]})])
    ttk_style.configure("TLabelframe.Label", font=(FONT, 10, "bold"))
    ttk_style.configure("TProgressbar", background=t["accent"], bordercolor=t["border"])

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
        self.links = 0
        self.advice = ""
        self.manager: LanguageManager | None = None   # the list of languages, while it is open
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
        top = ttk.Frame(self.root, padding=(14, 12, 14, 4))
        top.pack(fill="x")
        self.form = ttk.Frame(top)
        self.form.pack(fill="x")
        self.airports = ttk.Frame(top)    # takes the place of the form while the airports are being chosen
        self.make_variables()
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
        self.output = tk.Text(middle, wrap="word", state="disabled", width=1, height=1, padx=16, pady=10, relief="flat", borderwidth=0,
                              highlightthickness=1, highlightbackground=THEME["border"], highlightcolor=THEME["border"],
                              background=THEME["surface"], foreground=THEME["text"], spacing1=2, spacing3=2)
        vertical = ttk.Scrollbar(middle, orient="vertical", command=self.output.yview)
        horizontal = ttk.Scrollbar(middle, orient="horizontal", command=self.output.xview)
        self.output.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.output.pack(fill="both", expand=True)
        self.output.tag_configure("h1", font=(FONT, 17, "bold"), spacing1=14, spacing3=6)
        self.output.tag_configure("h2", font=(FONT, 14, "bold"), spacing1=14, spacing3=4)
        self.output.tag_configure("h3", font=(FONT, 11, "bold"), spacing1=12, spacing3=2)
        self.output.tag_configure("bold", font=(FONT, 10, "bold"))
        self.output.tag_configure("quote", foreground=THEME["muted"], lmargin1=16, lmargin2=16)
        self.output.tag_configure("bullet", lmargin1=14, lmargin2=28)
        self.output.tag_configure("log", foreground=THEME["muted"])
        self.output.tag_configure("table", font=(TABLE_FONT, 9), wrap="none")
        self.output.tag_configure("table_head", font=(TABLE_FONT, 9, "bold"), wrap="none")
        self.output.tag_configure("link", foreground=THEME["accent"], underline=True)
        for grade, colour in LIGHT_COLOURS.items():   # last, so that the colour wins over the tag of the line
            self.output.tag_configure(f"light_{grade}", foreground=colour)
        self.busy(False)
        self.asking(False)

    def make_variables(self) -> None:
        """What the form holds. Made once: the form is built again when the language changes, and keeps what was typed."""
        s = self.defaults
        self.origin = tk.StringVar(value=s.origin)
        self.destination = tk.StringVar(value=s.destination)
        self.dates = tk.StringVar(value="")   # clicked in the calendar every time
        # The whole day is read as 00:00 to 23:59: the form leaves it empty, as the traveller would
        self.earliest_leave = tk.StringVar(value="" if s.earliest_leave == "00:00" else s.earliest_leave)
        self.latest_leave = tk.StringVar(value="" if s.latest_leave == "23:59" else s.latest_leave)
        self.latest_arrival = tk.StringVar(value=s.latest_arrival)
        self.max_stopovers = tk.StringVar(value=str(s.max_stopovers))
        self.min_reliability = tk.StringVar(value=s.min_reliability)
        self.language = tk.StringVar(value=s.language)
        self.free_trains_origin = tk.BooleanVar(value=s.free_trains_at_origin)
        self.free_trains = tk.BooleanVar(value=s.free_trains_at_destination)
        self.scales: dict[str, str] = {}
        self.departure_scale = tk.StringVar(value=s.departure_scale)
        self.arrival_scale = tk.StringVar(value=s.arrival_scale)

    def build_form(self) -> None:
        s, form = self.defaults, self.form
        for child in form.winfo_children():
            child.destroy()
        # The scale of each side is chosen by what it means: how far the airports are looked for, and how many.
        # The list shows the meaning in the language of the window, so the scale chosen is kept by its name.
        chosen = [self.scales.get(variable.get(), variable.get()) for variable in (self.departure_scale, self.arrival_scale)]
        self.scales = {tr(f"ui_scale_{name}", km=s.scale(name)[0], count=s.scale(name)[1]): name for name in SCALES}
        labels = {name: label for label, name in self.scales.items()}
        self.departure_scale.set(labels[chosen[0]])
        self.arrival_scale.set(labels[chosen[1]])

        def field(row: int, column: int, label: str, widget: tk.Widget) -> None:
            """Three fields a row, each one under its label."""
            ttk.Label(form, text=label).grid(row=2 * row, column=column // 2, sticky="w", padx=(0, 16), pady=(4, 1))
            widget.grid(row=2 * row + 1, column=column // 2, sticky="we", padx=(0, 16), pady=(0, 4))

        field(0, 0, tr("origin"), ttk.Entry(form, textvariable=self.origin, width=12))
        field(0, 2, tr("destination"), ttk.Entry(form, textvariable=self.destination, width=12))
        # The dates are only clicked in the calendar: the field shows them, and a click on it opens the calendar too
        dates = ttk.Frame(form)
        ttk.Button(dates, text=tr("ui_calendar"), command=lambda: DatePicker(self)).grid(row=0, column=0, padx=(0, 6))
        dates_entry = ttk.Entry(dates, textvariable=self.dates, width=14, state="readonly", cursor="hand2")
        dates_entry.grid(row=0, column=1, sticky="we")
        dates_entry.bind("<Button-1>", lambda event: DatePicker(self))
        dates.columnconfigure(1, weight=1)
        field(0, 4, tr("ui_dates"), dates)
        field(1, 0, tr("ui_earliest_leave"), ttk.Entry(form, textvariable=self.earliest_leave, width=8))
        field(1, 2, tr("ui_latest_leave"), ttk.Entry(form, textvariable=self.latest_leave, width=8))
        field(1, 4, tr("ui_latest_arrival"), ttk.Entry(form, textvariable=self.latest_arrival, width=8))
        field(2, 0, tr("ui_stopovers"), ttk.Spinbox(form, textvariable=self.max_stopovers, from_=0, to=3, width=6))
        lights = ttk.Frame(form)   # the minimum is chosen by its colour
        for grade in GRADES:
            tk.Radiobutton(lights, text=LIGHT_DOT, variable=self.min_reliability, value=grade, indicatoron=False,
                           foreground=LIGHT_COLOURS[grade], activeforeground=LIGHT_COLOURS[grade], selectcolor=THEME["border"],
                           background=THEME["background"], activebackground=THEME["hover"],
                           font=(FONT, 15), width=2, borderwidth=1, offrelief="flat", cursor="hand2").pack(side="left", padx=(0, 4))
        field(2, 2, tr("ui_reliability"), lights)
        languages = ttk.Frame(form)   # the language is chosen in the list, and the list is changed with the button
        self.language_box = ttk.Combobox(languages, textvariable=self.language, values=list(LANGUAGES), state="readonly", width=8)
        self.language_box.pack(side="left")
        self.language_box.bind("<<ComboboxSelected>>", lambda event: self.change_language())
        ttk.Button(languages, text=tr("ui_languages"), command=lambda: LanguageManager(self)).pack(side="left", padx=(6, 0))
        field(2, 4, tr("ui_language"), languages)
        field(3, 0, tr("ui_departure_scale"),
              ttk.Combobox(form, textvariable=self.departure_scale, values=list(self.scales), state="readonly", width=21))
        field(3, 2, tr("ui_arrival_scale"),
              ttk.Combobox(form, textvariable=self.arrival_scale, values=list(self.scales), state="readonly", width=21))
        trains = ttk.Frame(form)
        trains.grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 4))
        ttk.Checkbutton(trains, text=tr("ui_free_trains_origin"), variable=self.free_trains_origin).pack(side="left")
        ttk.Checkbutton(trains, text=tr("ui_free_trains"), variable=self.free_trains).pack(side="left", padx=(24, 0))
        for column in range(3):
            form.columnconfigure(column, weight=1, uniform="field")

        buttons = ttk.Frame(form)
        buttons.grid(row=9, column=0, columnspan=3, sticky="we", pady=(8, 2))
        self.find_button = ttk.Button(buttons, text=tr("ui_find_airports"), command=self.find_airports, style="Accent.TButton")
        self.find_button.pack(side="left")
        self.check_button = ttk.Button(buttons, text=tr("ui_check"), command=self.check)
        self.check_button.pack(side="left", padx=8)
        ttk.Label(buttons, text=tr("ui_more_settings"), style="Muted.TLabel").pack(side="right")
        for entry in form.winfo_children():
            if isinstance(entry, ttk.Entry):
                entry.bind("<Return>", lambda event: self.find_airports())
        if getattr(self, "running", False):   # the new buttons as the old ones were
            for button in (self.find_button, self.check_button):
                button.configure(state="disabled")

    def change_language(self) -> None:
        """A language was chosen in the list: the window speaks it at once, not only once the trip is searched.
        A search that is running carries on in its own language, and the window changes when it ends."""
        if self.running or self.language.get() == current.SETTINGS.language:
            return
        current.SETTINGS = dataclasses.replace(current.SETTINGS, language=self.language.get())
        self.root.title(tr("ui_title"))
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
        if self.manager is not None:
            self.manager.close()   # its texts are in the language before: opened again, it speaks the new one

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
        """The trip in the form. Raises ValueError, with the reason, if it is not valid."""
        return dataclasses.replace(
            self.defaults,
            origin=self.origin.get(), destination=self.destination.get(),
            dates=self.dates.get(),
            earliest_leave=self.earliest_leave.get().strip(), latest_leave=self.latest_leave.get().strip(),
            latest_arrival=self.latest_arrival.get().strip(), max_stopovers=self.max_stopovers.get(),
            min_reliability=self.min_reliability.get(), language=self.language.get(),
            free_trains_at_origin=self.free_trains_origin.get(), free_trains_at_destination=self.free_trains.get(),
            departure_scale=self.scales[self.departure_scale.get()], arrival_scale=self.scales[self.arrival_scale.get()],
            confirm_airports=False,   # they are confirmed here, with the boxes
        )

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
        self.clear()
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
        self.clear()
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
                    self.write(value + "\n", "log")
                elif kind == "show":
                    self.write_markdown(value)
                elif kind == "unclear":   # (setting, name, meanings)
                    self.done("")
                    PlaceChooser(self, *value)
                elif kind == "airports":
                    self.places = value
                    self.build_airports(value)
                    self.form.pack_forget()
                    self.airports.pack(fill="x")
                    self.done(self.advice)   # what is allowed but will probably disappoint, before the search starts
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
        for button in (self.find_button, self.check_button, getattr(self, "search_button", None)):
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

    # ---------------------------------------------------------------- the output
    def clear(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def mark_lights(self, text: str) -> str:
        for light, mark in LIGHT_MARKS.items():
            text = text.replace(light, mark)
        return text

    def paint_lights(self, start: str) -> None:
        """Turn the marks written since start into dots of their colour."""
        for grade, mark in zip(GRADES, LIGHT_MARKS.values()):
            index = self.output.search(mark, start, "end")
            while index:
                tags = self.output.tag_names(index)
                self.output.delete(index)
                self.output.insert(index, LIGHT_DOT, (*tags, f"light_{grade}"))
                index = self.output.search(mark, index, "end")

    def write(self, text: str, *tags: str) -> None:
        self.output.configure(state="normal")
        start = self.output.index("end-1c")
        self.output.insert("end", self.mark_lights(text), tags)
        self.paint_lights(start)
        self.output.configure(state="disabled")
        self.output.see("end")

    def write_inline(self, text: str, *tags: str) -> None:
        """One line of text, with its **bold** parts and its links."""
        for part in re.split(r"(\*\*.+?\*\*|https?://[^\s,;]+)", text.replace("`", "")):
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                self.output.insert("end", part[2:-2], (*tags, "bold"))
            elif part.startswith("http"):
                url = part.rstrip(").")
                self.links += 1
                link = f"link{self.links}"
                self.output.tag_bind(link, "<Button-1>", lambda event, url=url: webbrowser.open(url))
                self.output.tag_bind(link, "<Enter>", lambda event: self.output.configure(cursor="hand2"))
                self.output.tag_bind(link, "<Leave>", lambda event: self.output.configure(cursor=""))
                self.output.insert("end", url, (*tags, "link", link))
                self.output.insert("end", part[len(url):], tags)
            else:
                self.output.insert("end", part, tags)

    def write_markdown(self, markdown: str) -> None:
        """The reports are Markdown. Show the part of it they use: headings, lists, tables, bold, links, quotes."""
        self.output.configure(state="normal")
        self.output.insert("end", "\n")
        start = self.output.index("end-1c")
        lines = self.mark_lights(markdown).strip("\n").split("\n")
        index = 0
        while index < len(lines):
            line = lines[index]
            if line.startswith("|"):
                rows = []
                while index < len(lines) and lines[index].startswith("|"):
                    cells = [cell.strip().replace("**", "").replace("`", "") for cell in lines[index].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):   # the line under the header
                        rows.append(cells)
                    index += 1
                widths = [max(len(row[column]) for row in rows if column < len(row)) for column in range(max(map(len, rows)))]
                for number, row in enumerate(rows):
                    text = "   ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
                    self.output.insert("end", text + "\n", "table_head" if number == 0 else "table")
                continue
            heading = re.match(r"(#{1,3}) (.*)", line)
            if heading:
                self.write_inline(heading[2], f"h{len(heading[1])}")
            elif line.startswith("- "):
                self.write_inline("•  " + line[2:], "bullet")
            elif line.startswith("> "):
                self.write_inline(line[2:], "quote")
            else:
                self.write_inline(line)
            self.output.insert("end", "\n")
            index += 1
        self.paint_lights(start)
        self.output.configure(state="disabled")
        self.output.see("end")
        self.output.see(start)   # a result is read from its first line

class PlaceChooser:
    """Which of the places of the same name the traveller means: one button each, the most important first."""

    def __init__(self, window: TripWindow, setting: str, name: str, options: list[tuple]):
        self.window, self.setting = window, setting
        self.top = tk.Toplevel(window.root)
        self.top.title(tr("ui_place_title"))
        self.top.transient(window.root)
        self.top.resizable(False, False)
        self.top.geometry(f"+{window.root.winfo_rootx() + 120}+{window.root.winfo_rooty() + 90}")
        body = ttk.Frame(self.top, padding=14)
        body.pack()
        ttk.Label(body, text=tr("place_several", name=name), font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.chosen = tk.IntVar(value=0)
        for number, (label, place) in enumerate(options):
            ttk.Radiobutton(body, text=label, variable=self.chosen, value=number).pack(anchor="w", pady=1)
        self.options = options
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text=tr("ui_place_use"), command=self.use, style="Accent.TButton").pack(side="right")
        ttk.Button(buttons, text=tr("ui_back"), command=self.top.destroy).pack(side="right", padx=8)
        self.top.bind("<Return>", lambda event: self.use())
        self.top.grab_set()

    def use(self) -> None:
        label, place = self.options[self.chosen.get()]
        self.top.destroy()
        self.window.place_chosen(self.setting, label, place)

class DatePicker:
    """A calendar to click the travel dates in. It starts with the dates of the form and writes back to it."""
    CHOSEN, FREE, OFF = THEME["accent"], THEME["surface"], THEME["background"]

    def __init__(self, window: TripWindow):
        self.window, self.today = window, date.today()
        try:
            self.chosen = set(read_dates(window.dates.get(), self.today))
        except ValueError:
            self.chosen = set()   # what is written is not a date yet: start empty
        self.chosen = {day for day in self.chosen if day >= self.today}
        self.month = min(self.chosen, default=self.today).replace(day=1)

        self.top = tk.Toplevel(window.root)
        self.top.title(tr("ui_dates"))
        self.top.transient(window.root)
        self.top.resizable(False, False)
        self.top.geometry(f"+{window.root.winfo_rootx() + 420}+{window.root.winfo_rooty() + 60}")
        header = ttk.Frame(self.top, padding=(12, 10, 12, 4))
        header.pack(fill="x")
        self.back = ttk.Button(header, text="<", width=3, command=lambda: self.move(-1))
        self.back.pack(side="left")
        ttk.Button(header, text=">", width=3, command=lambda: self.move(1)).pack(side="right")
        self.title = ttk.Label(header, anchor="center", font=(FONT, 11, "bold"))
        self.title.pack(fill="x")
        self.days = ttk.Frame(self.top, padding=(12, 0))
        self.days.pack()
        ttk.Label(self.top, text=tr("ui_calendar_hint", maximum=MAX_DATES), style="Muted.TLabel", padding=(12, 6)).pack(anchor="w")
        buttons = ttk.Frame(self.top, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=tr("ui_calendar_use"), command=self.use, style="Accent.TButton").pack(side="right")
        ttk.Button(buttons, text=tr("ui_calendar_clear"), command=self.clear).pack(side="right", padx=8)
        self.draw()
        self.top.grab_set()

    def draw(self) -> None:
        """The month being shown: one button per day, the chosen ones coloured, the ones that cannot be searched off."""
        for child in self.days.winfo_children():
            child.destroy()
        self.title.configure(text=f"{tr('cal_months').split(',')[self.month.month - 1]} {self.month.year}")
        self.back.configure(state="normal" if self.month > self.today.replace(day=1) else "disabled")
        for column, name in enumerate(tr("cal_weekdays").split(",")):
            ttk.Label(self.days, text=name, width=5, anchor="center", style="Muted.TLabel").grid(row=0, column=column, pady=(4, 2))
        last = self.today + timedelta(days=MAX_DAYS_AHEAD)
        for row, week in enumerate(calendar.Calendar(firstweekday=0).monthdatescalendar(self.month.year, self.month.month), start=1):
            for column, day in enumerate(week):
                if day.month != self.month.month:
                    continue
                possible, chosen = self.today <= day <= last, day in self.chosen
                tk.Button(
                    self.days, text=str(day.day), width=4, relief="flat", borderwidth=0, font=(FONT, 10, "bold" if chosen else "normal"),
                    bg=self.CHOSEN if chosen else self.FREE if possible else self.OFF, fg="#ffffff" if chosen else THEME["text"], activeforeground="#ffffff",
                    activebackground=self.CHOSEN, state="normal" if possible else "disabled", cursor="hand2" if possible else "",
                    command=lambda day=day: self.toggle(day),
                ).grid(row=row, column=column, padx=1, pady=1, ipady=3)

    def toggle(self, day: date) -> None:
        if day in self.chosen:
            self.chosen.remove(day)
        elif len(self.chosen) < MAX_DATES:
            self.chosen.add(day)
        self.draw()

    def move(self, months: int) -> None:
        month = self.month.month - 1 + months
        self.month = date(self.month.year + month // 12, month % 12 + 1, 1)
        self.draw()

    def clear(self) -> None:
        self.chosen.clear()
        self.draw()

    def use(self) -> None:
        self.window.dates.set(", ".join(f"{day:%d/%m/%Y}" for day in sorted(self.chosen)))
        self.top.destroy()

class LanguageManager:
    """The languages of the program, to add one or remove one without leaving the window (translate.py does it).
    What happens is told by the window, here and in its own status line."""

    def __init__(self, window: TripWindow):
        self.window, self.codes = window, []
        self.top = tk.Toplevel(window.root)
        self.top.title(tr("ui_languages").rstrip("."))
        self.top.transient(window.root)
        self.top.resizable(False, False)
        self.top.geometry(f"+{window.root.winfo_rootx() + 380}+{window.root.winfo_rooty() + 90}")
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.top, padding=12)
        body.pack()
        self.list = tk.Listbox(body, height=7, width=58, font=(TABLE_FONT, 10), activestyle="none", exportselection=False)
        self.list.grid(row=0, column=0, sticky="we")
        self.list.bind("<<ListboxSelect>>", lambda event: self.draw_buttons())
        self.remove_button = ttk.Button(body, text=tr("ui_language_remove"), command=self.remove)
        self.remove_button.grid(row=0, column=1, sticky="n", padx=(8, 0))
        self.name = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.name)
        entry.grid(row=1, column=0, sticky="we", pady=(10, 0))
        entry.bind("<Return>", lambda event: self.add())
        self.add_button = ttk.Button(body, text=tr("ui_language_add"), command=self.add)
        self.add_button.grid(row=1, column=1, padx=(8, 0), pady=(10, 0))
        ttk.Label(body, text=tr("ui_languages_hint"), style="Muted.TLabel", wraplength=520, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.message = ttk.Label(body, wraplength=520, justify="left")
        self.message.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        window.manager = self
        self.draw()
        self.top.grab_set()
        entry.focus_set()

    def draw(self) -> None:
        """The languages there are now, each with how complete it is."""
        chosen = self.selected()
        self.codes = list(TEXTS)
        self.list.delete(0, "end")
        for code in self.codes:
            gaps = len(translate.missing(code, TEXTS))
            state = tr("ui_language_reference") if code == REFERENCE else tr("ui_language_incomplete", count=gaps) if gaps else tr("ui_language_complete")
            self.list.insert("end", f" {code:7} {TEXTS[code].get('language_name', code):18} {state}")
        if chosen in self.codes:
            self.list.selection_set(self.codes.index(chosen))
        self.draw_buttons()

    def draw_buttons(self) -> None:
        free = not self.window.running   # one thing at a time: not while a search or a translation is running
        self.add_button.configure(state="normal" if free else "disabled")
        self.remove_button.configure(state="normal" if free and self.selected() not in (None, REFERENCE) else "disabled")

    def selected(self) -> str | None:
        rows = self.list.curselection()
        return self.codes[rows[0]] if rows else None

    def add(self) -> None:
        name = self.name.get().strip()
        if name and not self.window.running:
            self.name.set("")
            self.window.add_language(name)

    def remove(self) -> None:
        code = self.selected()
        if code in (None, REFERENCE) or self.window.running:
            return
        language = TEXTS[code].get("language_name", code)
        if messagebox.askyesno(self.top.title(), tr("ui_language_confirm", name=language), parent=self.top):
            self.window.remove_language(code)

    def close(self) -> None:
        self.window.manager = None
        self.top.destroy()

def run(settings: TripSettings) -> int:
    """Open the window on the trip in settings.py and stay until it is closed."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on Windows screens that are scaled
    except Exception:
        pass
    TripWindow(settings).root.mainloop()
    return 0
