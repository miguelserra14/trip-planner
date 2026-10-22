"""The discover map in a window of its own: a city, the days, and a button that opens the map with every price.
Double-click Discover.bat, or: python -m discover.window

It is a program apart from the planner's window, on purpose: the lookups change the globals the planner reads its
trip from (session.py), and they use the same services (Kiwi, Gemini, the timetables). So a map is made in this
process, never in the planner's, and the planner's window opened from the map is a process of its own too.
Closing this window stops the map's server; the planner's windows opened from it stay open."""
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date, datetime, timedelta
from tkinter import ttk

from discover import confirm, finder
from discover.session import Session
from discover.sources import Days
from settings import SETTINGS
from trip_planner import utils

DEFAULT_DAYS_AHEAD = 21     # the form starts with a week of days to leave on, this far ahead...
DEFAULT_DAYS_SPAN = 7       # ... this long
DEFAULT_STAY_DAYS = 3       # and, for a return trip, days to come back on this long after
POLL_MS = 150               # how often the window reads the progress lines of the map being made
SOURCES = (("light", "Ryanair, and Kiwi for the other airlines with fewer requests"),
           ("full", "Ryanair, and Kiwi for the other airlines (more destinations, more requests)"),
           ("fast", "Ryanair only (a few seconds)"))

def read_day(text: str) -> str:
    """dd/mm/yyyy as yyyy-mm-dd. Raises ValueError with what is wrong."""
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        raise ValueError(f"'{text.strip()}' is not a day written dd/mm/yyyy, such as 12/11/2026")

def read_days(first: str, last: str, back_first: str = "", back_last: str = "", today: date | None = None) -> Days:
    """The days of the form, checked: real days, from today on, in order, the return not before the leaving."""
    today = today or date.today()
    out = sorted([read_day(first), read_day(last or first)])
    if date.fromisoformat(out[0]) < today:
        raise ValueError("the first day to leave on is already past")
    if not back_first.strip():
        return Days(*out)
    back = sorted([read_day(back_first), read_day(back_last or back_first)])
    if back[-1] < out[0]:
        raise ValueError("the days to come back on are before the days to leave on")
    return Days(*out, *back)

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
        self.root.title("Discover: where to fly cheaply")
        self.lines: queue.Queue = queue.Queue()
        self.session: Session | None = None
        self.working = False
        try:   # the planner's look, when its window's code can be read; the plain one otherwise
            from trip_planner.gui import style
            style(self.root)
        except Exception:
            pass
        utils.LISTENERS["say"] = lambda text: self.lines.put(("say", text))
        sys.stdout = sys.stderr = LineWriter(self.lines)
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(POLL_MS, self.poll)

    # ---------------------------------------------------------------- the form
    def build(self) -> None:
        start = date.today() + timedelta(days=DEFAULT_DAYS_AHEAD)
        end = start + timedelta(days=DEFAULT_DAYS_SPAN)
        form = ttk.Frame(self.root, padding=16)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="Where to fly cheaply from a city: every destination on a map, with the flight and the way to and "
                             "from the airports on both sides, each price with its source.", wraplength=560, style="Muted.TLabel"
                  ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))
        self.city = tk.StringVar(value=SETTINGS.origin)
        self.first, self.last = tk.StringVar(value=f"{start:%d/%m/%Y}"), tk.StringVar(value=f"{end:%d/%m/%Y}")
        self.round_trip = tk.BooleanVar(value=False)
        self.back_first = tk.StringVar(value=f"{start + timedelta(days=DEFAULT_STAY_DAYS):%d/%m/%Y}")
        self.back_last = tk.StringVar(value=f"{end + timedelta(days=DEFAULT_STAY_DAYS):%d/%m/%Y}")
        self.sources = tk.StringVar(value="light")
        self.look_up = tk.BooleanVar(value=True)

        ttk.Label(form, text="From").grid(row=1, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.city, width=28).grid(row=1, column=1, columnspan=3, sticky="we", pady=3)
        ttk.Label(form, text="Leave between").grid(row=2, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.first, width=12).grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(form, text="and").grid(row=2, column=2, padx=6)
        ttk.Entry(form, textvariable=self.last, width=12).grid(row=2, column=3, sticky="w", pady=3)
        ttk.Checkbutton(form, text="Return between", variable=self.round_trip, command=self.show_return).grid(row=3, column=0, sticky="w")
        self.back_entries = [ttk.Entry(form, textvariable=self.back_first, width=12), ttk.Entry(form, textvariable=self.back_last, width=12)]
        self.back_entries[0].grid(row=3, column=1, sticky="w", pady=3)
        self.back_and = ttk.Label(form, text="and")
        self.back_and.grid(row=3, column=2, padx=6)
        self.back_entries[1].grid(row=3, column=3, sticky="w", pady=3)
        sources = ttk.LabelFrame(form, text="Flights", padding=8)
        sources.grid(row=4, column=0, columnspan=4, sticky="we", pady=(10, 4))
        for value, label in SOURCES:
            ttk.Radiobutton(sources, text=label, value=value, variable=self.sources).pack(anchor="w")
        ttk.Checkbutton(form, text=f"Look the cheapest destinations up in the real timetables (a few minutes, at most "
                                   f"{confirm.MAX_LOOKUPS} cities)",
                        variable=self.look_up).grid(row=5, column=0, columnspan=4, sticky="w", pady=4)
        ttk.Label(form, text="It asks Kiwi, Gemini and the timetables the planner uses too: better not while the planner is searching.",
                  wraplength=560, style="Muted.TLabel").grid(row=6, column=0, columnspan=4, sticky="w", pady=(2, 10))
        buttons = ttk.Frame(form)
        buttons.grid(row=7, column=0, columnspan=4, sticky="we")
        self.open_button = ttk.Button(buttons, text="Open the map", command=self.open_map, style="Accent.TButton")
        self.open_button.pack(side="left")
        self.again_button = ttk.Button(buttons, text="Show the map again", command=self.show_again, state="disabled")
        self.again_button.pack(side="left", padx=8)
        self.status = ttk.Label(form, text="", wraplength=560)
        self.status.grid(row=8, column=0, columnspan=4, sticky="w", pady=(10, 4))
        self.log = tk.Text(form, height=12, width=80, wrap="word", state="disabled", relief="flat", borderwidth=1)
        self.log.grid(row=9, column=0, columnspan=4, sticky="nsew")
        form.columnconfigure(3, weight=1)
        form.rowconfigure(9, weight=1)
        self.show_return()

    def show_return(self) -> None:
        state = "normal" if self.round_trip.get() else "disabled"
        for entry in self.back_entries:
            entry.configure(state=state)

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
            days = read_days(self.first.get(), self.last.get(), *((self.back_first.get(), self.back_last.get()) if self.round_trip.get() else ()))
        except ValueError as error:
            return self.say(f"The days: {error}.", error=True)
        if not self.city.get().strip():
            return self.say("Write the city to leave from.", error=True)
        if self.session:
            self.session.stop()   # one map at a time: the previous one's page stops answering
        source = self.sources.get()
        self.session = Session(self.city.get().strip(), days, fast=source == "fast", light=source == "light", max_stops=finder.MAX_STOPS)
        self.working = True
        self.open_button.configure(state="disabled")
        self.again_button.configure(state="disabled")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.say("Finding the flights and the ways to and from the airports. The first map of a place takes a few minutes; the next ones less.")
        threading.Thread(target=self.make, args=(self.session, self.look_up.get()), daemon=True).start()

    def make(self, session: Session, look_up: bool) -> None:
        """In the background: the map, then its page served and opened, then the lookups."""
        try:
            session.make()
            address = session.serve(looking_up=look_up)
            self.lines.put(("opened", address))
            if look_up:
                session.look_up()
            self.lines.put(("done", str(session.path)))
        except Exception as error:
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
                    self.say(f"The map is open in your browser ({text}). "
                             + ("The cheapest destinations are being looked up in the timetables: the map updates itself."
                                if self.look_up.get() else "Keep this window open while you use it."))
                elif kind == "done":
                    self.finish(f"Done. The map stays open while this window is. It is also saved as a file: {text}")
                elif kind == "failed":
                    self.finish(f"The map could not be made: {text}", error=True)
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
        if self.session:
            self.session.stop()
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
