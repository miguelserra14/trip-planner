"""The discover map in a window of its own: a city, the days, and a button that opens the map with every price.
Double-click Discover.bat, or: python -m discover.window

It is a program apart from the planner's window, on purpose: the lookups change the globals the planner reads its
trip from (session.py), and they use the same services (Kiwi, Gemini, the timetables). So a map is made in this
process, never in the planner's, and the planner's window opened from the map is a process of its own too.
Every map made stays served while this window is open, so the page of an earlier map still answers (its
trips still open the planner); closing the window stops them all. The planner's windows opened from a map stay open."""
import queue
import traceback
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date, datetime, timedelta
from tkinter import ttk
from types import SimpleNamespace

from discover import confirm, finder
from discover.session import Session
from discover.sources import Days
from settings import SETTINGS
from trip_planner import current, utils
from trip_planner.texts import tr

DEFAULT_DAYS_AHEAD = 21     # the form starts with a few days in a row to leave on, this far ahead...
DEFAULT_DAYS_SPAN = 3       # ... this many
DEFAULT_STAY_DAYS = 3       # and, for a return trip, as many days to come back on this long after
POLL_MS = 150               # how often the window reads the progress lines of the map being made
SOURCES = ("light", "full", "fast")   # the ways to search the flights, each with its text discover_win_source_<way>

def read_day(text: str) -> str:
    """dd/mm/yyyy as yyyy-mm-dd. Raises ValueError with what is wrong."""
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        raise ValueError(tr("discover_win_bad_day", day=text.strip()))

def read_list(text: str) -> list[str]:
    """Days written dd/mm/yyyy and separated by commas, as the calendar writes them: yyyy-mm-dd, in order, once each."""
    return sorted({read_day(entry) for entry in text.split(",") if entry.strip()})

def read_days(out: str, back: str = "", today: date | None = None) -> Days:
    """The days of the form, checked: real days, from today on, the return not before the leaving. The days clicked
    stay as they are: a map asks for the flights of those days only."""
    today = today or date.today()
    leaving = read_list(out)
    if not leaving:
        raise ValueError(tr("discover_win_no_day"))
    if date.fromisoformat(leaving[0]) < today:
        raise ValueError(tr("discover_win_past_day"))
    coming = read_list(back)
    if back.strip() and coming[-1] < leaving[0]:
        raise ValueError(tr("discover_win_back_before"))
    return Days.clicked(leaving, coming or None)

def calendar_class():
    """The planner's calendar (trip_planner.gui.DatePicker): a click on a day chooses it or not, as in the planner.
    None when the planner's window code cannot be read: the days are then typed."""
    try:
        from trip_planner.gui import DatePicker
        return DatePicker
    except Exception:
        return None

class LineWriter:
    """Stands in for the terminal: whatever the map's code prints becomes a line in the window."""

    def __init__(self, lines: queue.Queue):
        self.lines, self.pending = lines, ""

    def write(self, text: str) -> int:
        *done, self.pending = (self.pending + text).split("\n")
        for line in done:
            if line.strip():
                self.lines.put(("say", line))
        return len(text)

    def flush(self) -> None:
        pass

class DiscoverWindow:
    def __init__(self):
        self.root = tk.Tk()
        current.SETTINGS = current.SETTINGS or SETTINGS   # the language of every text, and of the planner's calendar
        self.root.title(tr("discover_win_title"))
        self.lines: queue.Queue = queue.Queue()
        self.session: Session | None = None   # the last map
        self.sessions: list[Session] = []    # every map made, each still served
        self.working = False
        try:   # the planner's look, when its window's code can be read; the plain one otherwise
            from trip_planner.gui import style
            style(self.root)
        except Exception:
            pass
        self.picker = calendar_class()
        utils.LISTENERS["say"] = lambda text: self.lines.put(("say", text))
        sys.stdout = sys.stderr = LineWriter(self.lines)
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(POLL_MS, self.poll)

    # ---------------------------------------------------------------- the form
    def build(self) -> None:
        start = date.today() + timedelta(days=DEFAULT_DAYS_AHEAD)
        form = ttk.Frame(self.root, padding=16)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text=tr("discover_win_intro"), wraplength=560, style="Muted.TLabel"
                  ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))
        self.city = tk.StringVar(value=SETTINGS.origin)
        written = lambda first: ", ".join(f"{first + timedelta(days=offset):%d/%m/%Y}" for offset in range(DEFAULT_DAYS_SPAN))
        self.out_days = tk.StringVar(value=written(start))
        self.round_trip = tk.BooleanVar(value=False)
        self.back_days = tk.StringVar(value=written(start + timedelta(days=DEFAULT_STAY_DAYS)))
        self.sources = tk.StringVar(value="light")
        self.look_up = tk.BooleanVar(value=True)

        ttk.Label(form, text=tr("discover_win_from")).grid(row=1, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.city, width=28).grid(row=1, column=1, columnspan=3, sticky="we", pady=3)
        ttk.Label(form, text=tr("discover_win_leave_on")).grid(row=2, column=0, sticky="w")
        self.day_field(form, self.out_days).grid(row=2, column=1, columnspan=3, sticky="we", pady=3)
        ttk.Checkbutton(form, text=tr("discover_win_back_on"), variable=self.round_trip, command=self.show_return).grid(row=3, column=0, sticky="w")
        self.back_entry = self.day_field(form, self.back_days)
        self.back_entry.grid(row=3, column=1, columnspan=3, sticky="we", pady=3)
        sources = ttk.LabelFrame(form, text=tr("discover_win_flights"), padding=8)
        sources.grid(row=4, column=0, columnspan=4, sticky="we", pady=(10, 4))
        for value in SOURCES:
            ttk.Radiobutton(sources, text=tr(f"discover_win_source_{value}"), value=value, variable=self.sources).pack(anchor="w")
        ttk.Checkbutton(form, text=tr("discover_win_look_up", cities=confirm.MAX_LOOKUPS),
                        variable=self.look_up).grid(row=5, column=0, columnspan=4, sticky="w", pady=4)
        ttk.Label(form, text=tr("discover_win_shared"),
                  wraplength=560, style="Muted.TLabel").grid(row=6, column=0, columnspan=4, sticky="w", pady=(2, 10))
        buttons = ttk.Frame(form)
        buttons.grid(row=7, column=0, columnspan=4, sticky="we")
        self.open_button = ttk.Button(buttons, text=tr("discover_win_open"), command=self.open_map, style="Accent.TButton")
        self.open_button.pack(side="left")
        self.again_button = ttk.Button(buttons, text=tr("discover_win_again"), command=self.show_again, state="disabled")
        self.again_button.pack(side="left", padx=8)
        self.status = ttk.Label(form, text="", wraplength=560)
        self.status.grid(row=8, column=0, columnspan=4, sticky="w", pady=(10, 4))
        self.log = tk.Text(form, height=12, width=80, wrap="word", state="disabled", relief="flat", borderwidth=1)
        self.log.grid(row=9, column=0, columnspan=4, sticky="nsew")
        form.columnconfigure(3, weight=1)
        form.rowconfigure(9, weight=1)
        self.show_return()

    def day_field(self, form: ttk.Frame, variable: tk.StringVar) -> ttk.Entry:
        """The days of one side. With the planner's calendar the field is only clicked, as in the planner, and each
        day is clicked in it; without the calendar the days are typed, separated by commas."""
        entry = ttk.Entry(form, textvariable=variable, width=40, state="readonly" if self.picker else "normal",
                          cursor="hand2" if self.picker else "xterm")
        if self.picker:
            entry.bind("<Button-1>", lambda event: self.open_calendar(variable))
        return entry

    def open_calendar(self, variable: tk.StringVar) -> None:
        if variable is self.back_days and not self.round_trip.get():
            return
        # The planner's calendar reads the days from window.dates and writes them back there
        self.picker(SimpleNamespace(root=self.root, dates=variable))

    def show_return(self) -> None:
        self.back_entry.configure(state=("readonly" if self.picker else "normal") if self.round_trip.get() else "disabled")

    def say(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#b42318" if error else "")

    def add_line(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------------------------------------------------------------- the map
    def open_map(self) -> None:
        if self.working:
            return
        try:
            days = read_days(self.out_days.get(), self.back_days.get() if self.round_trip.get() else "")
        except ValueError as error:
            return self.say(tr("discover_win_days_wrong", error=error), error=True)
        if not self.city.get().strip():
            return self.say(tr("discover_win_no_city"), error=True)
        source = self.sources.get()
        self.session = Session(self.city.get().strip(), days, fast=source == "fast", light=source == "light", max_stops=finder.MAX_STOPS)
        self.sessions.append(self.session)
        self.working = True
        self.open_button.configure(state="disabled")
        self.again_button.configure(state="disabled")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.say(tr("discover_win_finding"))
        threading.Thread(target=self.make, args=(self.session, self.look_up.get()), daemon=True).start()

    def make(self, session: Session, look_up: bool) -> None:
        """In the background: the map, then its page served and opened, then the lookups."""
        try:
            session.make()
            address = session.serve(looking_up=True)
            self.lines.put(("opened", address))
            # The trains and buses are priced whether or not the flights are looked up in the timetables
            session.look_up(timetables=look_up)
            self.lines.put(("done", str(session.path)))
        except Exception as error:
            # The whole trace in the log below, so that what went wrong can be told apart from its last line
            for line in traceback.format_exc().splitlines():
                self.lines.put(("say", line))
            self.lines.put(("failed", f"{type(error).__name__}: {error}"))

    def poll(self) -> None:
        try:
            while True:
                kind, text = self.lines.get_nowait()
                if kind == "say":
                    self.add_line(text)
                elif kind == "opened":
                    webbrowser.open(text)
                    self.again_button.configure(state="normal")
                    why = self.session.found.kiwi_why if self.session and self.session.found else ""
                    self.say(tr("discover_win_opened", address=text) + (" " + tr("discover_win_only_ryanair", why=why) if why else "") + " "
                             + tr("discover_win_updating_lookups" if self.look_up.get() else "discover_win_updating"))
                elif kind == "done":
                    self.finish(tr("discover_win_done", file=text))
                elif kind == "failed":
                    self.finish(tr("discover_win_failed", error=text), error=True)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self.poll)

    def finish(self, text: str, error: bool = False) -> None:
        self.working = False
        self.open_button.configure(state="normal")
        self.say(text, error)

    def show_again(self) -> None:
        if self.session and self.session.address:
            webbrowser.open(self.session.address)

    def close(self) -> None:
        for session in self.sessions:
            session.stop()
        self.root.destroy()

def main() -> int:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on Windows screens that are scaled
    except Exception:
        pass
    DiscoverWindow().root.mainloop()
    return 0

if __name__ == "__main__":
    sys.exit(main())
