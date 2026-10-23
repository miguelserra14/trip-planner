"""The small windows the main one opens: which place a name means, the calendar of the travel dates, and the languages."""
import calendar
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from trip_planner.texts import translate
from trip_planner.trip.trip_settings import MAX_DATES, MAX_DAYS_AHEAD
from trip_planner.trip.dates import read_dates
from trip_planner.gui.style import FONT, TABLE_FONT, THEME
from trip_planner.texts import REFERENCE, TEXTS, tr

if TYPE_CHECKING:
    from trip_planner.gui.window import TripWindow

class PlaceChooser:
    """Which of the places of the same name the traveller means: one button each, the most important first."""

    def __init__(self, window: "TripWindow", setting: str, name: str, options: list[tuple]):
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

    def __init__(self, window: "TripWindow"):
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

    def __init__(self, window: "TripWindow"):
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
