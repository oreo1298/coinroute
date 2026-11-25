"""Where the terminal screens draw: curses for real use, plain text for tests and screenshots.

Screens only call `size()`, `put(y, x, text, style)` and `fill(y, x, h, w, style)`, so they run the
same against a terminal and against TextCanvas (which keeps characters and styles in arrays).
"""
from __future__ import annotations

# Named styles: (foreground, background, bold). The palette is the classic DOS one ExpressNet
# users know: white on blue, yellow titles, a cyan bar for the selection and the key bar.
STYLES: dict[str, tuple[str, str, bool]] = {
    "normal": ("white", "blue", False),
    "title": ("yellow", "blue", True),
    "box": ("white", "blue", True),
    "dim": ("cyan", "blue", False),
    "select": ("black", "cyan", False),
    "header": ("black", "white", False),
    "bar": ("black", "cyan", False),
    "key": ("red", "cyan", False),
    "status": ("yellow", "blue", False),
    "field": ("white", "black", False),
    "warn": ("white", "red", True),
    "good": ("green", "blue", True),
    "bad": ("red", "blue", True),
}

# single-line box drawing; the text canvas and curses both get these characters
BOX = {"h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "lt": "├", "rt": "┤"}


class Canvas:
    def size(self) -> tuple[int, int]:
        raise NotImplementedError

    def put(self, y: int, x: int, text: str, style: str = "normal") -> None:
        raise NotImplementedError

    def fill(self, y: int, x: int, h: int, w: int, style: str = "normal") -> None:
        for r in range(y, y + h):
            self.put(r, x, " " * w, style)

    def box(self, y: int, x: int, h: int, w: int, style: str = "box", title: str = "",
            title_style: str = "title") -> None:
        b = BOX
        self.put(y, x, b["tl"] + b["h"] * (w - 2) + b["tr"], style)
        for r in range(y + 1, y + h - 1):
            self.put(r, x, b["v"], style)
            self.put(r, x + w - 1, b["v"], style)
        self.put(y + h - 1, x, b["bl"] + b["h"] * (w - 2) + b["br"], style)
        if title:
            t = f" {title} "[: max(0, w - 4)]
            self.put(y, x + (w - len(t)) // 2, t, title_style)


class TextCanvas(Canvas):
    """A screen in memory: `text()` for tests, `cells` (char, style) for screenshots."""

    def __init__(self, rows: int = 25, cols: int = 80):
        self.rows, self.cols = rows, cols
        self.cells = [[(" ", "normal") for _ in range(cols)] for _ in range(rows)]

    def size(self) -> tuple[int, int]:
        return self.rows, self.cols

    def put(self, y: int, x: int, text: str, style: str = "normal") -> None:
        if not 0 <= y < self.rows:
            return
        for i, ch in enumerate(text):
            if 0 <= x + i < self.cols:
                self.cells[y][x + i] = (ch, style)

    def text(self) -> str:
        return "\n".join("".join(c for c, _ in row).rstrip() for row in self.cells)

    def style_at(self, y: int, x: int) -> str:
        return self.cells[y][x][1]


class CursesCanvas(Canvas):
    COLORS = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")

    def __init__(self, scr):
        import curses

        self.curses = curses
        self.scr = scr
        self.attr: dict[str, int] = {}
        if curses.has_colors():
            curses.start_color()
            for i, (name, (fg, bg, bold)) in enumerate(STYLES.items(), start=1):
                curses.init_pair(i, self.COLORS.index(fg), self.COLORS.index(bg))
                self.attr[name] = curses.color_pair(i) | (curses.A_BOLD if bold else 0)
        else:
            for name in STYLES:
                self.attr[name] = curses.A_REVERSE if name in ("select", "bar", "header") else 0

    def size(self) -> tuple[int, int]:
        return self.scr.getmaxyx()

    def put(self, y: int, x: int, text: str, style: str = "normal") -> None:
        rows, cols = self.size()
        if not 0 <= y < rows or x >= cols:
            return
        if x < 0:
            text, x = text[-x:], 0
        text = text[: cols - x]
        if y == rows - 1 and x + len(text) >= cols:  # the bottom-right cell can't be written
            text = text[: cols - x - 1]
        try:
            self.scr.addstr(y, x, text, self.attr.get(style, 0))
        except self.curses.error:
            pass
