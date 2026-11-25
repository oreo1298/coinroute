"""The building blocks of the terminal screens: menus, tables, text views, forms and prompts.

A screen draws itself on a Canvas and handles one key at a time. `key()` returns what should
happen next: None (stay), POP (go back), or another Screen to open on top. Keys arrive as
names: "UP", "DOWN", "PGUP", "PGDN", "HOME", "END", "ENTER", "ESC", "BACKSPACE", "TAB",
"F1".."F10", or a single printable character.
"""
from __future__ import annotations

import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .canvas import Canvas

POP = "pop"
QUIT = "quit"


def as_screen(title: str, r):
    """What an action returned, as the app expects it: a message string becomes a Message box."""
    if isinstance(r, str) and r not in (POP, QUIT):
        return Message(title, r)
    return r


class Screen:
    title = ""
    keys: Sequence[tuple[str, str]] = (("ESC", "Back"),)  # shown in the key bar
    help = ""  # F1 text

    def draw(self, c: Canvas, top: int, height: int) -> None:
        raise NotImplementedError

    def key(self, k: str):
        if k == "ESC":
            return POP
        if k == "F1" and self.help:
            return Message("Help", self.help)
        return None

    def status(self) -> str:
        """The description line above the key bar."""
        return ""


# --- menus ------------------------------------------------------------------------------------


@dataclass
class Item:
    label: str
    describe: str
    action: Callable[[], object] | None = None  # returns a Screen, a str (shown as a message) or None
    hotkey: str = ""


class Menu(Screen):
    """A numbered menu in a centred box: digits or arrows and ENTER choose; the item's description
    shows on the status line. Item 0 (Exit / Return) comes last, as in ExpressNet."""

    keys = (("ESC", "Back"), ("F1", "Help"), ("↑↓", "Move"), ("ENTER", "Select"))

    def __init__(self, title: str, items: list[Item], help: str = ""):
        self.title = title
        self.items = items
        self.help = help
        self.sel = 0
        for i, it in enumerate(items):
            it.hotkey = it.hotkey or (str(i + 1) if i < len(items) - 1 else "0")

    def draw(self, c: Canvas, top: int, height: int) -> None:
        _, cols = c.size()
        w = max(len(self.title) + 8, *(len(it.label) + 12 for it in self.items))
        h = len(self.items) + 4
        y = top + max(0, (height - h) // 2)
        x = max(0, (cols - w) // 2)
        c.fill(y, x, h, w, "normal")
        c.box(y, x, h, w, title=self.title)
        for i, it in enumerate(self.items):
            line = f"  {it.hotkey}.  {it.label}".ljust(w - 4)
            style = "select" if i == self.sel else "normal"
            c.put(y + 2 + i, x + 2, line, style)
            c.put(y + 2 + i, x + 4, it.hotkey, "select" if i == self.sel else "title")

    def status(self) -> str:
        return self.items[self.sel].describe

    def key(self, k: str):
        n = len(self.items)
        if k == "UP":
            self.sel = (self.sel - 1) % n
        elif k == "DOWN":
            self.sel = (self.sel + 1) % n
        elif k == "HOME":
            self.sel = 0
        elif k == "END":
            self.sel = n - 1
        elif k == "ENTER":
            return self._choose(self.items[self.sel])
        else:
            for i, it in enumerate(self.items):
                if k.upper() == it.hotkey.upper():
                    self.sel = i
                    return self._choose(it)
            return super().key(k)
        return None

    @staticmethod
    def _choose(it: Item):
        if it.action is None:
            return POP
        return as_screen(it.label, it.action())


# --- tables -----------------------------------------------------------------------------------


@dataclass
class Col:
    title: str
    key: str | Callable[[dict], object]
    width: int
    right: bool = False
    fmt: Callable[[object], str] | None = None

    def text(self, row: dict) -> str:
        v = self.key(row) if callable(self.key) else row.get(self.key)
        s = self.fmt(v) if self.fmt else ("" if v is None else str(v))
        s = s[: self.width]
        return s.rjust(self.width) if self.right else s.ljust(self.width)


class Table(Screen):
    """A scrolling list of rows with a header. Typing filters it (when `search` is on)."""

    keys = (("ESC", "Back"), ("↑↓ PgUp PgDn", "Move"), ("ENTER", "Select"))

    def __init__(self, title: str, cols: list[Col], rows: list[dict],
                 on_enter: Callable[[dict], object] | None = None, note: str = "",
                 search: bool = False, more: Callable[[dict], list[dict]] | None = None,
                 on_key: Callable[[str], object] | None = None, extra_keys=()):
        self.title = title
        self.on_key = on_key
        self.cols = cols
        self.all_rows = rows
        self.rows = rows
        self.on_enter = on_enter
        self.note = note
        self.search = search
        self.query = ""
        self.more = more  # fetch the next page after the last row (keyset paging)
        self.sel = 0
        self.top = 0
        if search:
            self.keys = (("ESC", "Back"), ("type", "Search"), ("↑↓ PgUp PgDn", "Move"), ("ENTER", "Select"))
        self.keys = (*self.keys, *extra_keys)

    def _visible(self, height: int) -> int:
        return max(1, height - 5)

    def draw(self, c: Canvas, top: int, height: int) -> None:
        _, cols = c.size()
        w = min(cols, sum(col.width + 1 for col in self.cols) + 3)
        x = max(0, (cols - w) // 2)
        c.fill(top, x, height, w, "normal")
        c.box(top, x, height, w, title=self.title)
        header = " ".join(col.title[: col.width].rjust(col.width) if col.right else
                          col.title[: col.width].ljust(col.width) for col in self.cols)
        c.put(top + 1, x + 1, (" " + header).ljust(w - 2)[: w - 2], "header")
        n = self._visible(height)
        self.top = min(self.top, self.sel)
        if self.sel >= self.top + n:
            self.top = self.sel - n + 1
        for i in range(n):
            r = self.top + i
            if r >= len(self.rows):
                break
            line = " " + " ".join(col.text(self.rows[r]) for col in self.cols)
            c.put(top + 2 + i, x + 1, line.ljust(w - 2)[: w - 2], "select" if r == self.sel else "normal")
        if not self.rows:
            c.put(top + 3, x + 3, "(nothing to show)", "dim")
        foot = self.note
        if self.search:
            foot = f"Search: {self.query}_" + (f"   {self.note}" if self.note else "")
        count = f" {len(self.rows)} " if not self.more else f" {len(self.rows)}+ "
        c.put(top + height - 2, x + 2, foot[: w - 4 - len(count)], "status")
        c.put(top + height - 1, x + w - 2 - len(count), count, "dim")

    def selected(self) -> dict | None:
        return self.rows[self.sel] if 0 <= self.sel < len(self.rows) else None

    def status(self) -> str:
        return f"{self.sel + 1} of {len(self.rows)}" if self.rows else ""

    def _filter(self) -> None:
        q = self.query.lower()
        self.rows = [r for r in self.all_rows if q in " ".join(str(v) for v in r.values()
                                                                if isinstance(v, str | int)).lower()]
        self.sel = self.top = 0

    def _need(self, upto: int) -> None:
        while self.more and upto >= len(self.rows) - 1 and self.rows:
            extra = self.more(self.rows[-1])
            if not extra:
                self.more = None
                break
            self.all_rows = self.rows = self.rows + extra

    def key(self, k: str):
        page = 15
        if self.on_key and k.startswith("F") and len(k) > 1:
            r = self.on_key(k)
            if r is not None:
                return as_screen(self.title, r)
        if k == "UP":
            self.sel = max(0, self.sel - 1)
        elif k == "DOWN":
            self._need(self.sel + 1)
            self.sel = min(len(self.rows) - 1, self.sel + 1)
        elif k == "PGUP":
            self.sel = max(0, self.sel - page)
        elif k == "PGDN":
            self._need(self.sel + page)
            self.sel = min(len(self.rows) - 1, self.sel + page)
        elif k == "HOME":
            self.sel = 0
        elif k == "END":
            self.sel = len(self.rows) - 1
        elif k == "ENTER" and self.on_enter and self.selected():
            return as_screen(self.title, self.on_enter(self.selected()))
        elif self.search and k == "BACKSPACE":
            self.query = self.query[:-1]
            self._filter()
        elif self.search and len(k) == 1 and k.isprintable():
            self.query += k
            self._filter()
        else:
            return super().key(k)
        self.sel = max(0, self.sel)
        return None


# --- text and forms ---------------------------------------------------------------------------


class TextView(Screen):
    """Scrolling text (reports, help). F2 saves it to a file when `save` is given."""

    def __init__(self, title: str, text: str, save: Callable[[str], str] | None = None):
        self.title = title
        self.lines = text.splitlines() or [""]
        self.save = save
        self.top = 0
        self.left = 0
        self.keys = (("ESC", "Back"), ("↑↓ PgUp PgDn ←→", "Scroll")) + ((("F2", "Save to file"),) if save else ())

    def draw(self, c: Canvas, top: int, height: int) -> None:
        _, cols = c.size()
        c.fill(top, 0, height, cols, "normal")
        c.box(top, 0, height, cols, title=self.title)
        self._page = height - 2
        for i in range(height - 2):
            n = self.top + i
            if n < len(self.lines):
                c.put(top + 1 + i, 2, self.lines[n][self.left:self.left + cols - 4], "normal")

    def status(self) -> str:
        last = min(len(self.lines), self.top + getattr(self, "_page", 20))
        return f"Lines {self.top + 1}-{last} of {len(self.lines)}"

    def key(self, k: str):
        page = max(1, getattr(self, "_page", 20) - 1)
        last = max(0, len(self.lines) - page)
        moves = {"UP": -1, "DOWN": 1, "PGUP": -page, "PGDN": page}
        if k in moves:
            self.top = min(last, max(0, self.top + moves[k]))
        elif k == "HOME":
            self.top = 0
        elif k == "END":
            self.top = last
        elif k == "LEFT":
            self.left = max(0, self.left - 10)
        elif k == "RIGHT":
            self.left += 10
        elif k == "F2" and self.save:
            return Prompt("Save to file", [("File name", "")], lambda v: self.save(v[0]))
        else:
            return super().key(k)
        return None


@dataclass
class Section:
    title: str
    fields: list[tuple[str, object]] = field(default_factory=list)


class Form(TextView):
    """Labelled fields in sections, laid out like ExpressNet's record screens (read-only)."""

    def __init__(self, title: str, sections: list[Section], extra_keys=(), on_key=None):
        lines: list[str] = []
        width = max((len(lbl) for s in sections for lbl, _ in s.fields), default=10) + 2
        self_value_col = width + 3  # where values start: two spaces, the label and its colon
        for s in sections:
            if lines:
                lines.append("")
            lines.append(f"── {s.title} " + "─" * max(0, 60 - len(s.title)))
            for label, value in s.fields:
                v = "" if value is None else str(value)
                lines.append(f"  {(label + ':').ljust(width)} {v}")
        super().__init__(title, "\n".join(lines))
        self.value_col = self_value_col
        self.keys = (("ESC", "Back"), ("↑↓", "Scroll"), *extra_keys)
        self.on_key = on_key

    def draw(self, c: Canvas, top: int, height: int) -> None:
        super().draw(c, top, height)
        _, cols = c.size()
        for i in range(height - 2):  # section rules in the title colour, values brighter than labels
            n = self.top + i
            if n >= len(self.lines):
                break
            line = self.lines[n]
            if line.startswith("──"):
                c.put(top + 1 + i, 2, line[: cols - 4], "title")
            elif line.startswith("  ") and ":" in line:
                at = self.value_col
                c.put(top + 1 + i, 2 + at, line[at: at + cols - 4 - at], "box")

    def key(self, k: str):
        if self.on_key:
            r = self.on_key(k)
            if r is not None:
                return as_screen(self.title, r)
        return super().key(k)


class Message(Screen):
    """A box with a message; any key closes it."""

    keys = (("any key", "Continue"),)

    def __init__(self, title: str, text: str, style: str = "normal"):
        self.title = title
        self.text = text
        self.style = style

    def draw(self, c: Canvas, top: int, height: int) -> None:
        _, cols = c.size()
        w = min(cols - 4, max(40, len(self.title) + 8))
        lines = [ln for p in self.text.split("\n") for ln in (textwrap.wrap(p, w - 6) or [""])]
        w = min(cols - 4, max(w, max(len(ln) for ln in lines) + 6))
        h = len(lines) + 4
        y, x = top + max(0, (height - h) // 2), (cols - w) // 2
        c.fill(y, x, h, w, self.style)
        c.box(y, x, h, w, style="box" if self.style == "normal" else self.style, title=self.title)
        for i, ln in enumerate(lines):
            c.put(y + 2 + i, x + 3, ln, self.style)

    def key(self, k: str):
        return POP


class Invalid(Exception):
    """Raised by a Prompt's `done` to keep the form open and show what is wrong."""


class Prompt(Screen):
    """Ask for one or more values. ENTER moves to the next field and, on the last (or F2 anywhere),
    calls `done` with the values; its result (a Screen, a message string or None) replaces the
    prompt. If `done` raises Invalid (or ValueError), the form stays open with the message shown.
    `compact` puts one field per line, for long forms like the site record."""

    def __init__(self, title: str, fields: list[tuple[str, str]], done: Callable[[list[str]], object],
                 note: str = "", compact: bool = False):
        self.title = title
        self.labels = [f[0] for f in fields]
        self.values = [f[1] for f in fields]
        self.done = done
        self.note = note
        self.error = ""
        self.compact = compact
        self.cur = 0
        self.replace = True  # the app swaps the prompt for what `done` returns
        self.keys = (("ESC", "Cancel"), ("TAB ↑↓", "Field"), ("ENTER", "Next / OK"), ("F2", "Save"))

    def draw(self, c: Canvas, top: int, height: int) -> None:
        _, cols = c.size()
        lw = max(len(lbl) for lbl in self.labels) + 2
        w = min(cols - 2, max(60, lw + 40, len(self.note) + 6))
        step = 1 if self.compact else 2
        extra = sum(2 for t in (self.note, self.error) if t)
        h = len(self.labels) * step + 3 + extra
        y, x = top + max(0, (height - h) // 2), (cols - w) // 2
        c.fill(y, x, h, w, "normal")
        c.box(y, x, h, w, title=self.title)
        fw = w - lw - 6
        for i, (lbl, val) in enumerate(zip(self.labels, self.values)):
            row = y + 2 + step * i
            c.put(row, x + 2, (lbl + ":").ljust(lw), "title" if i == self.cur and self.compact else "normal")
            shown = val[-(fw - 1):] + ("_" if i == self.cur else "")
            c.put(row, x + 2 + lw, shown.ljust(fw), "field")
        foot = y + h - 2
        if self.error:
            c.put(foot, x + 2, self.error[: w - 4].ljust(w - 4), "warn")
            foot -= 2
        if self.note:
            c.put(foot, x + 2, self.note[: w - 4], "dim")

    def _finish(self):
        try:
            r = self.done(self.values)
        except (Invalid, ValueError) as e:
            self.error = str(e)
            return None
        if r is None:
            return POP
        return ("replace", as_screen(self.title, r))

    def key(self, k: str):
        if k == "ESC":
            return POP
        if k == "TAB" or k == "DOWN":
            self.cur = (self.cur + 1) % len(self.values)
        elif k == "UP":
            self.cur = (self.cur - 1) % len(self.values)
        elif k == "BACKSPACE":
            self.values[self.cur] = self.values[self.cur][:-1]
        elif k == "F2":
            return self._finish()
        elif k == "ENTER":
            if self.cur < len(self.values) - 1:
                self.cur += 1
                return None
            return self._finish()
        elif len(k) == 1 and k.isprintable():
            self.values[self.cur] += k
        return None
