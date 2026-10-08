"""The window: the trip is typed in a form, the airports are ticked in a list, and the progress, the result and the
follow-up questions all happen in the same window. Nothing is asked in the terminal.

The planner itself does not change. It runs on its own thread (an asyncio loop), and what it says and shows
(`say` and `show` in utils.py) is handed to the window through a queue, because only the main thread may touch it."""
import asyncio
import dataclasses
import os
import queue
import re
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from tkinter import ttk

from trip_planner import current, planner, utils
from trip_planner.check import check_setup
from trip_planner.config import GRADES, LANGUAGES, LIGHTS, SCALES, TripSettings
from trip_planner.places import airport_groups, choose_airports, remove_airports, TripPlaces
from trip_planner.report.full import keep_report, RECENT_REPORTS
from trip_planner.sources import kiwi
from trip_planner.texts import tr

FONT = "Segoe UI"
TABLE_FONT = "Consolas"
# The reliability lights. The text of the window cannot draw the coloured symbols of the reports, so each one is
# written as a mark that takes one character and then painted as a dot of its colour.
LIGHT_COLOURS = dict(zip(GRADES, ("#2e9e4f", "#e0a800", "#c0392b")))
LIGHT_MARKS = {LIGHTS[grade]: chr(0xE000 + number) for number, grade in enumerate(GRADES)}
LIGHT_DOT = "●"

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
        self.root.geometry("1150x820")
        self.root.minsize(820, 560)
        self.root.option_add("*Font", (FONT, 10))
        self.root.report_callback_exception = lambda *error: self.failed("".join(traceback.format_exception(*error)))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.root.after(100, self.poll)

    # ---------------------------------------------------------------- the widgets
    def build(self) -> None:
        top = ttk.Frame(self.root, padding=(14, 12, 14, 4))
        top.pack(fill="x")
        self.form = ttk.Frame(top)
        self.form.pack(fill="x")
        self.airports = ttk.Frame(top)    # takes the place of the form while the airports are being chosen
        self.build_form()

        status = ttk.Frame(self.root, padding=(14, 2))
        status.pack(fill="x")
        self.status = ttk.Label(status, wraplength=1000, justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=160)

        bottom = ttk.Frame(self.root, padding=(14, 4, 14, 12))
        bottom.pack(side="bottom", fill="x")
        ttk.Label(bottom, text=tr("ui_question")).pack(side="left")
        self.question = ttk.Entry(bottom)
        self.question.pack(side="left", fill="x", expand=True, padx=8)
        self.question.bind("<Return>", lambda event: self.ask())
        self.ask_button = ttk.Button(bottom, text=tr("ui_ask"), command=self.ask)
        self.ask_button.pack(side="left")
        self.report_button = ttk.Button(bottom, text=tr("ui_open_report"), command=self.open_report)
        self.report_button.pack(side="left", padx=(8, 0))
        self.keep = tk.BooleanVar(value=False)
        self.keep_box = ttk.Checkbutton(bottom, text=tr("ui_keep_report"), variable=self.keep, command=self.keep_report)
        self.keep_box.pack(side="left", padx=(10, 0))

        middle = ttk.Frame(self.root, padding=(14, 4))
        middle.pack(fill="both", expand=True)
        self.output = tk.Text(middle, wrap="word", state="disabled", padx=12, pady=8, relief="flat", borderwidth=1,
                              highlightthickness=1, highlightbackground="#c8c8c8", spacing1=1, spacing3=1)
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
        self.output.tag_configure("quote", foreground="#666666", lmargin1=16, lmargin2=16)
        self.output.tag_configure("bullet", lmargin1=14, lmargin2=28)
        self.output.tag_configure("log", foreground="#555555")
        self.output.tag_configure("table", font=(TABLE_FONT, 9), wrap="none")
        self.output.tag_configure("table_head", font=(TABLE_FONT, 9, "bold"), wrap="none")
        self.output.tag_configure("link", foreground="#0b5cad", underline=True)
        for grade, colour in LIGHT_COLOURS.items():   # last, so that the colour wins over the tag of the line
            self.output.tag_configure(f"light_{grade}", foreground=colour)
        self.busy(False)
        self.asking(False)

    def build_form(self) -> None:
        s, form = self.defaults, self.form
        self.origin = tk.StringVar(value=s.origin)
        self.destination = tk.StringVar(value=s.destination)
        self.dates = tk.StringVar(value=", ".join(s.dates))
        self.earliest_leave = tk.StringVar(value=s.earliest_leave)
        self.latest_leave = tk.StringVar(value=s.latest_leave)
        self.latest_arrival = tk.StringVar(value=s.latest_arrival)
        self.max_stopovers = tk.StringVar(value=str(s.max_stopovers))
        self.min_reliability = tk.StringVar(value=s.min_reliability)
        self.language = tk.StringVar(value=s.language)
        self.free_trains_origin = tk.BooleanVar(value=s.free_trains_at_origin)
        self.free_trains = tk.BooleanVar(value=s.free_trains_at_destination)
        # The scale of each side is chosen by what it means: how far the airports are looked for, and how many
        self.scales = {tr(f"ui_scale_{name}", km=s.scale(name)[0], count=s.scale(name)[1]): name for name in SCALES}
        labels = {name: label for label, name in self.scales.items()}
        self.departure_scale = tk.StringVar(value=labels[s.departure_scale])
        self.arrival_scale = tk.StringVar(value=labels[s.arrival_scale])

        def field(row: int, column: int, label: str, widget: tk.Widget, span: int = 1) -> None:
            ttk.Label(form, text=label).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=4)
            widget.grid(row=row, column=column + 1, columnspan=span, sticky="we", padx=(0, 18), pady=4)

        field(0, 0, tr("origin"), ttk.Entry(form, textvariable=self.origin, width=26))
        field(0, 2, tr("destination"), ttk.Entry(form, textvariable=self.destination, width=26))
        field(0, 4, tr("ui_dates"), ttk.Entry(form, textvariable=self.dates, width=30))
        field(1, 0, tr("ui_earliest_leave"), ttk.Entry(form, textvariable=self.earliest_leave, width=8))
        field(1, 2, tr("ui_latest_leave"), ttk.Entry(form, textvariable=self.latest_leave, width=8))
        field(1, 4, tr("ui_latest_arrival"), ttk.Entry(form, textvariable=self.latest_arrival, width=8))
        field(2, 0, tr("ui_stopovers"), ttk.Spinbox(form, textvariable=self.max_stopovers, from_=0, to=3, width=6))
        lights = ttk.Frame(form)   # the minimum is chosen by its colour
        for grade in GRADES:
            tk.Radiobutton(lights, text=LIGHT_DOT, variable=self.min_reliability, value=grade, indicatoron=False,
                           foreground=LIGHT_COLOURS[grade], activeforeground=LIGHT_COLOURS[grade], selectcolor="#d6d6d6",
                           font=(FONT, 15), width=2, borderwidth=1, offrelief="flat", cursor="hand2").pack(side="left", padx=(0, 4))
        field(2, 2, tr("ui_reliability"), lights)
        field(2, 4, tr("ui_language"),
              ttk.Combobox(form, textvariable=self.language, values=list(LANGUAGES), state="readonly", width=8))
        field(3, 0, tr("ui_departure_scale"),
              ttk.Combobox(form, textvariable=self.departure_scale, values=list(self.scales), state="readonly", width=30))
        field(3, 2, tr("ui_arrival_scale"),
              ttk.Combobox(form, textvariable=self.arrival_scale, values=list(self.scales), state="readonly", width=30))
        ttk.Checkbutton(form, text=tr("ui_free_trains_origin"), variable=self.free_trains_origin).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(form, text=tr("ui_free_trains"), variable=self.free_trains).grid(
            row=4, column=2, columnspan=2, sticky="w", pady=4)
        for column in (1, 3, 5):
            form.columnconfigure(column, weight=1)

        buttons = ttk.Frame(form)
        buttons.grid(row=5, column=0, columnspan=6, sticky="we", pady=(8, 2))
        self.find_button = ttk.Button(buttons, text=tr("ui_find_airports"), command=self.find_airports)
        self.find_button.pack(side="left")
        self.check_button = ttk.Button(buttons, text=tr("ui_check"), command=self.check)
        self.check_button.pack(side="left", padx=8)
        ttk.Label(buttons, text=tr("ui_more_settings"), foreground="#666666").pack(side="right")
        for entry in form.winfo_children():
            if isinstance(entry, ttk.Entry):
                entry.bind("<Return>", lambda event: self.find_airports())

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
                      foreground="#b03a2e").pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(self.airports)
        buttons.pack(fill="x", pady=(10, 2))
        self.search_button = ttk.Button(buttons, text=tr("ui_search"), command=self.search)
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
            dates=[day for day in re.split(r"[,;\s]+", self.dates.get()) if day],
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
        try:
            self.settings = self.read_form()
        except ValueError as error:
            self.say_status(str(error), error=True)
            return
        current.SETTINGS = self.settings   # from here on the texts are in the language of this trip
        self.advice = " ".join(f"{tr('advice')}: {advice}" for advice in self.settings.advice())

        async def work() -> None:
            self.events.put(("airports", await asyncio.to_thread(choose_airports, self.settings)))
        self.start(work(), tr("ui_finding"))

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
            if kiwi.kiwi_search is None:
                await kiwi.connect_kiwi()
            await planner.plan_trip(settings, places)
            self.events.put(("planned", None))
        self.clear()
        self.asking(False)
        self.show_form()
        self.start(work(), tr("ui_searching"))

    def ask(self) -> None:
        question = self.question.get().strip()
        if self.running or not question or not planner.PLANNED:
            return

        async def work() -> None:
            await planner.ask(question)
            self.events.put(("answered", None))
        self.question.delete(0, "end")
        self.start(work(), tr("ui_asking"))

    def check(self) -> None:
        if self.running:
            return

        async def work() -> None:
            await check_setup()
            self.events.put(("checked", None))
        self.clear()
        self.start(work(), tr("ui_checking"))

    def keep_report(self) -> None:
        """The box was ticked or unticked: the report of the last trip moves to saved/, or back to the recent ones."""
        if not planner.PLANNED or planner.PLANNED[-1].get("report") is None:
            return
        trip = planner.PLANNED[-1]
        trip["report"] = keep_report(trip["report"], self.keep.get())
        if self.keep.get():
            self.say_status(tr("ui_report_kept", folder=trip["report"].parent))
        else:
            self.say_status(tr("ui_report_not_kept", limit=RECENT_REPORTS))

    def open_report(self) -> None:
        path = planner.PLANNED[-1].get("report") if planner.PLANNED else None
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
        self.asking(bool(planner.PLANNED) and not running)

    def asking(self, possible: bool) -> None:
        """Questions and the report only make sense once a trip was planned."""
        for widget in (self.question, self.ask_button, self.report_button, self.keep_box):
            widget.configure(state="normal" if possible else "disabled")

    def say_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#b03a2e" if error else "#222222")

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

def run(settings: TripSettings) -> int:
    """Open the window on the trip in settings.py and stay until it is closed."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on Windows screens that are scaled
    except Exception:
        pass
    TripWindow(settings).root.mainloop()
    return 0
