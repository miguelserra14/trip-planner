"""The look of the window: its colours and fonts, and the styles of the ttk widgets."""
import tkinter as tk
from tkinter import ttk

from trip_planner.config import GRADES, LIGHTS

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
    # The search card of the main page: white, with its labels, ticks and a larger search button
    ttk_style.configure("Card.TFrame", background=t["surface"])
    ttk_style.configure("Card.TLabel", background=t["surface"], foreground=t["muted"])
    ttk_style.configure("Card.TCheckbutton", background=t["surface"])
    ttk_style.map("Card.TCheckbutton", background=[("active", t["surface"])])
    ttk_style.configure("Big.Accent.TButton", padding=(26, 9), font=(FONT, 11, "bold"))
    ttk_style.configure("Swap.TButton", padding=(6, 5))
    ttk_style.configure("TNotebook", background=t["background"])
    ttk_style.configure("TNotebook.Tab", padding=(12, 5))
    ttk_style.configure("TProgressbar", background=t["accent"], bordercolor=t["border"])
