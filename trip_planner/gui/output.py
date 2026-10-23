"""The text of the window where the progress, the result and the answers are written: plain lines, and the
Markdown of the reports with its headings, lists, tables, bold, links and reliability lights."""
import re
import tkinter as tk
import webbrowser
from tkinter import ttk

from trip_planner.config import GRADES
from trip_planner.gui.style import FONT, LIGHT_COLOURS, LIGHT_DOT, LIGHT_MARKS, TABLE_FONT, THEME

class OutputView:
    """The text and its two scrollbars, filling parent."""

    def __init__(self, parent: tk.Widget):
        self.links = 0
        self.text = tk.Text(parent, wrap="word", state="disabled", width=1, height=1, padx=16, pady=10, relief="flat", borderwidth=0,
                            highlightthickness=1, highlightbackground=THEME["border"], highlightcolor=THEME["border"],
                            background=THEME["surface"], foreground=THEME["text"], spacing1=2, spacing3=2)
        vertical = ttk.Scrollbar(parent, orient="vertical", command=self.text.yview)
        horizontal = ttk.Scrollbar(parent, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("h1", font=(FONT, 17, "bold"), spacing1=14, spacing3=6)
        self.text.tag_configure("h2", font=(FONT, 14, "bold"), spacing1=14, spacing3=4)
        self.text.tag_configure("h3", font=(FONT, 11, "bold"), spacing1=12, spacing3=2)
        self.text.tag_configure("bold", font=(FONT, 10, "bold"))
        self.text.tag_configure("quote", foreground=THEME["muted"], lmargin1=16, lmargin2=16)
        self.text.tag_configure("bullet", lmargin1=14, lmargin2=28)
        self.text.tag_configure("log", foreground=THEME["muted"])
        self.text.tag_configure("table", font=(TABLE_FONT, 9), wrap="none")
        self.text.tag_configure("table_head", font=(TABLE_FONT, 9, "bold"), wrap="none")
        self.text.tag_configure("link", foreground=THEME["accent"], underline=True)
        for grade, colour in LIGHT_COLOURS.items():   # last, so that the colour wins over the tag of the line
            self.text.tag_configure(f"light_{grade}", foreground=colour)

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def mark_lights(self, text: str) -> str:
        for light, mark in LIGHT_MARKS.items():
            text = text.replace(light, mark)
        return text

    def paint_lights(self, start: str) -> None:
        """Turn the marks written since start into dots of their colour."""
        for grade, mark in zip(GRADES, LIGHT_MARKS.values()):
            index = self.text.search(mark, start, "end")
            while index:
                tags = self.text.tag_names(index)
                self.text.delete(index)
                self.text.insert(index, LIGHT_DOT, (*tags, f"light_{grade}"))
                index = self.text.search(mark, index, "end")

    def write(self, text: str, *tags: str) -> None:
        self.text.configure(state="normal")
        start = self.text.index("end-1c")
        self.text.insert("end", self.mark_lights(text), tags)
        self.paint_lights(start)
        self.text.configure(state="disabled")
        self.text.see("end")

    def write_inline(self, text: str, *tags: str) -> None:
        """One line of text, with its **bold** parts and its links."""
        for part in re.split(r"(\*\*.+?\*\*|https?://[^\s,;]+)", text.replace("`", "")):
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                self.text.insert("end", part[2:-2], (*tags, "bold"))
            elif part.startswith("http"):
                url = part.rstrip(").")
                self.links += 1
                link = f"link{self.links}"
                self.text.tag_bind(link, "<Button-1>", lambda event, url=url: webbrowser.open(url))
                self.text.tag_bind(link, "<Enter>", lambda event: self.text.configure(cursor="hand2"))
                self.text.tag_bind(link, "<Leave>", lambda event: self.text.configure(cursor=""))
                self.text.insert("end", url, (*tags, "link", link))
                self.text.insert("end", part[len(url):], tags)
            else:
                self.text.insert("end", part, tags)

    def write_markdown(self, markdown: str) -> None:
        """The reports are Markdown. Show the part of it they use: headings, lists, tables, bold, links, quotes."""
        self.text.configure(state="normal")
        self.text.insert("end", "\n")
        start = self.text.index("end-1c")
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
                    self.text.insert("end", text + "\n", "table_head" if number == 0 else "table")
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
            self.text.insert("end", "\n")
            index += 1
        self.paint_lights(start)
        self.text.configure(state="disabled")
        self.text.see("end")
        self.text.see(start)   # a result is read from its first line
