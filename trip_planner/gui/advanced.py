"""The advanced settings: every setting that is not on the main page, in tabs, and how the window shows a setting."""
import tkinter as tk
from tkinter import ttk
from typing import Any, TYPE_CHECKING

from trip_planner.trip.trip_settings import GRADES, RANGES, SCALES
from trip_planner.trip.ground_choice import CHOICES, NO_CHANGE_LIMIT
from trip_planner.gui.dialogs import LanguageManager
from trip_planner.gui.style import FONT, LIGHT_COLOURS, LIGHT_DOT, screen_scale, THEME
from trip_planner.texts import REFERENCE, TEXTS, tr

if TYPE_CHECKING:
    from trip_planner.gui.window import TripWindow

# The main page holds what the first page of a flight search site holds: where from, where to, when, and three ticks
# (airports near each city, direct flights only). Every other setting is in AdvancedSettings. Both hold the same
# variables, one per setting of TripSettings, so the two never disagree.
MAIN_SETTINGS = ("origin", "destination", "departure_scale", "arrival_scale", "max_stopovers")
CHANGES = ("max_changes_to_airport", "max_changes_from_airport")   # shown with how to travel on each side
NOT_IN_WINDOW = ("dates", "return_dates", "language")   # the two calendars and the list of languages hold these

def shown(name: str, value: Any) -> Any:
    """A setting as the window shows it: a list as text with commas, a whole day without its times, 5.0 as 5."""
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, float):
        return f"{value:g}"
    if (name, value) in (("earliest_leave", "00:00"), ("latest_leave", "23:59")):
        return ""
    return str(value)

def same(first: Any, second: Any) -> bool:
    """Whether two values of a setting are the same, "5" and "5.0" included."""
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return first == second

class AdvancedSettings:
    """Every setting that is not on the main page, in tabs. They hold the same variables as the window: what is
    changed here is what the next search uses, and the main page says how many settings differ from settings.py.
    Closing checks them, and says what is wrong instead of closing.
    Kept small: a label is one column of two lines at most, a choice is a list, and what a setting means is said in
    one line at the bottom when the mouse is over it, instead of a column of its own."""
    LABEL_WIDTH = 230   # pixels at 100%, before a label goes on to a second line
    TABS = (
        ("ui_tab_times", ("earliest_leave", "latest_leave", "earliest_arrival", "latest_arrival", "next_day_arrivals",
                          "max_stopovers", "min_airport_minutes", "ideal_airport_minutes", "min_landing_minutes")),
        ("ui_tab_airports", ("departure_scale", "arrival_scale", "confirm_airports", "primary_radius_km", "fallback_radius_km",
                             "arrival_radius_km", "max_departure_airports", "max_arrival_airports", "max_road_hours")),
        ("ui_tab_airport_lists", ("include_airports", "exclude_airports", "primary_airports", "fallback_airports", "arrival_airports")),
        ("ui_tab_ground", ("to_airport_by", "max_changes_to_airport", "from_airport_by", "max_changes_from_airport",
                           "free_trains_at_origin", "free_trains_at_destination")),
        ("ui_tab_estimates", ("allow_estimated_prices", "max_estimate_km", "small_estimate_eur", "unverified_price_factor",
                              "city_walk_minutes", "wide_city_walk_minutes", "airport_walk_minutes")),
        ("ui_tab_preferences", ("min_reliability", "tiebreak_eur_per_hour", "fallback_saving_eur_per_hour", "extra_hour_growth",
                                "options_shown", "extra_wishes")),
        ("ui_tab_program", ("airport_floor_minutes", "night_minutes", "min_trip_km", "city_airport_radius_km",
                            "ambiguous_importance", "recent_reports", "use_flight_agent")),
    )
    # The settings that already had a label in the window keep it; the others are called set_<name> in the texts
    LABELS = {"earliest_leave": "ui_earliest_leave", "latest_leave": "ui_latest_leave", "latest_arrival": "ui_latest_arrival",
              "max_stopovers": "ui_stopovers", "min_reliability": "ui_reliability", "departure_scale": "ui_departure_scale",
              "arrival_scale": "ui_arrival_scale", "free_trains_at_origin": "ui_free_trains_origin",
              "free_trains_at_destination": "ui_free_trains", "to_airport_by": "ui_to_airport_by", "from_airport_by": "ui_from_airport_by"}
    HINTS = {**dict.fromkeys(("earliest_leave", "latest_leave", "earliest_arrival", "latest_arrival"), "hint_times"),
             **dict.fromkeys(("include_airports", "exclude_airports", "primary_airports", "fallback_airports", "arrival_airports"), "hint_airport_list"),
             **dict.fromkeys(("to_airport_by", "from_airport_by", *CHANGES), "hint_ground_choice"),
             **{name: f"hint_{name}" for name in ("min_reliability", "tiebreak_eur_per_hour", "fallback_saving_eur_per_hour",
                                                  "extra_hour_growth", "unverified_price_factor", "max_estimate_km", "extra_wishes")}}

    def __init__(self, window: "TripWindow"):
        self.window = window
        self.traces: list[tuple] = []   # (variable, trace) of the lists below, removed on closing
        self.top = tk.Toplevel(window.root)
        self.top.title(tr("ui_advanced"))
        self.top.transient(window.root)
        self.top.resizable(False, False)
        self.top.geometry(f"+{window.root.winfo_rootx() + 60}+{window.root.winfo_rooty() + 60}")
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        window.advanced = self
        window.show_limits()
        body = ttk.Frame(self.top, padding=10)
        body.pack(fill="both", expand=True)
        self.scale = screen_scale(window.root)
        tabs = ttk.Notebook(body)
        tabs.pack(fill="both", expand=True)
        for title, names in self.TABS:
            tab = ttk.Frame(tabs, padding=(10, 8))
            tabs.add(tab, text=tr(title))
            for row, name in enumerate(names):
                self.add(tab, row, name)
            if title == "ui_tab_program":   # what the program can do besides searching
                buttons = ttk.Frame(tab)
                buttons.grid(row=len(names), column=0, columnspan=2, sticky="w", pady=(10, 0))
                ttk.Button(buttons, text=tr("ui_languages"), command=lambda: LanguageManager(window)).pack(side="left")
                self.check_button = ttk.Button(buttons, text=tr("ui_check"), command=self.check)
                self.check_button.pack(side="left", padx=8)
                if window.running:
                    self.check_button.configure(state="disabled")
        # What the setting under the mouse means, or what this window is for: one line for every setting
        self.hint = ttk.Label(body, text=tr("ui_advanced_hint"), style="Muted.TLabel", wraplength=round(440 * self.scale), justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(6, 0))
        self.message = ttk.Label(body, wraplength=round(440 * self.scale), justify="left", foreground=THEME["error"])
        self.message.pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text=tr("ui_close"), command=self.close, style="Accent.TButton").pack(side="right")
        ttk.Button(buttons, text=tr("ui_restore"), command=self.restore).pack(side="left")

    @classmethod
    def label(cls, name: str) -> str:
        """A setting as the window names it, in the traveller's language. Its own name when it has no label."""
        key = cls.LABELS.get(name, f"set_{name}")
        return tr(key) if key in TEXTS[REFERENCE] else name

    def choice_list(self, parent: tk.Widget, variable: tk.Variable, options: dict[str, str]) -> ttk.Combobox:
        """A list to choose one value from, shown in words ({value: words}). It holds the window's own variable,
        so the main page and these settings stay the same, whichever is changed."""
        words = tk.StringVar(value=options.get(variable.get(), variable.get()))
        values = {shown: value for value, shown in options.items()}
        box = ttk.Combobox(parent, textvariable=words, values=list(options.values()), state="readonly",
                           width=max(map(len, options.values())) + 1)
        box.bind("<<ComboboxSelected>>", lambda event: variable.set(values[words.get()]))
        trace = variable.trace_add("write", lambda *changed: words.set(options.get(variable.get(), variable.get())))
        self.traces.append((variable, trace, words))
        return box

    def explain(self, widgets: list[tk.Widget], name: str) -> None:
        """The line at the bottom says what a setting means while the mouse is over it."""
        meaning = tr(self.HINTS[name]) if name in self.HINTS else self.label(name)
        for widget in widgets:
            for part in (widget, *widget.winfo_children()):
                part.bind("<Enter>", lambda event: self.hint.configure(text=meaning), add="+")
                part.bind("<Leave>", lambda event: self.hint.configure(text=tr("ui_advanced_hint")), add="+")

    def add(self, tab: ttk.Frame, row: int, name: str) -> None:
        """One setting on one row: its label, on two lines at most, and the way to change it."""
        window, variable = self.window, self.window.values[name]
        default = getattr(window.defaults, name)
        label = ttk.Label(tab, text=self.label(name), wraplength=round(self.LABEL_WIDTH * self.scale), justify="left")
        label.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
        if isinstance(default, bool):
            widget = ttk.Checkbutton(tab, variable=variable)
            label.bind("<Button-1>", lambda event: variable.set(not variable.get()))   # the label ticks it too
        elif name in ("departure_scale", "arrival_scale"):
            widget = self.choice_list(tab, variable, {scale: tr(f"ui_scale_{scale}", km=window.defaults.scale(scale)[0],
                                                                count=window.defaults.scale(scale)[1]) for scale in SCALES})
        elif name in ("to_airport_by", "from_airport_by"):
            widget = self.choice_list(tab, variable, {choice: tr(f"ui_choice_{choice}") for choice in CHOICES})
        elif name in CHANGES:   # a number, or no limit in words
            widget = ttk.Spinbox(tab, textvariable=variable, values=[*map(str, range(NO_CHANGE_LIMIT)), tr("ui_changes_any")],
                                 state="readonly", width=10, wrap=True)
        elif name == "min_reliability":   # chosen by its colour, as the reports show it
            widget = ttk.Frame(tab)
            for grade in GRADES:
                tk.Radiobutton(widget, text=LIGHT_DOT, variable=variable, value=grade, indicatoron=False,
                               foreground=LIGHT_COLOURS[grade], activeforeground=LIGHT_COLOURS[grade], selectcolor=THEME["border"],
                               background=THEME["background"], activebackground=THEME["hover"],
                               font=(FONT, 13), width=2, borderwidth=1, offrelief="flat", cursor="hand2").pack(side="left", padx=(0, 4))
        elif name in RANGES:
            lowest, highest = RANGES[name]
            step = 1 if isinstance(default, int) else 0.05 if highest <= 1 else 0.1 if highest <= 10 else 1
            widget = ttk.Spinbox(tab, textvariable=variable, from_=lowest, to=highest, increment=step, width=7)
        else:   # a time, a list of airports, or free text
            widget = ttk.Entry(tab, textvariable=variable, width=8 if self.HINTS.get(name) == "hint_times" else 26)
        widget.grid(row=row, column=1, sticky="w", pady=2)
        self.explain([label, widget], name)

    def restore(self) -> None:
        """Every setting back to what settings.py says, the main page's ticks included. The places stay."""
        window = self.window
        for name, variable in window.values.items():
            if name not in ("origin", "destination"):
                variable.set(shown(name, getattr(window.defaults, name)))
        window.show_limits()
        self.message.configure(text=tr("ui_restored"), foreground=THEME["text"])

    def check(self) -> None:
        self.window.check()

    def close(self, check: bool = True) -> None:
        """Closed only with settings that can be used: otherwise it says which one is wrong, and stays."""
        problem = self.window.check_advanced() if check else ""
        if problem:
            self.message.configure(text=problem, foreground=THEME["error"])
            return
        for variable, trace, words in self.traces:
            variable.trace_remove("write", trace)
        self.window.advanced = None
        self.top.destroy()
        self.window.count_changes()
