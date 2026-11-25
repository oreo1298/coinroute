"""The terminal app: a stack of screens under a title bar, with a description line and a key bar
at the bottom, the way ExpressNet's screens were laid out.

    coinroute tui [route.db]

Runs on any terminal of 80x24 or more. Windows needs the `windows-curses` package
(`pip install coinroute[tui]`); Linux and macOS have curses built in.
"""
from __future__ import annotations

import datetime as _dt

from .canvas import Canvas, TextCanvas
from .widgets import POP, QUIT, Message, Screen

CURSES_KEYS = {
    "KEY_UP": "UP", "KEY_DOWN": "DOWN", "KEY_LEFT": "LEFT", "KEY_RIGHT": "RIGHT",
    "KEY_PPAGE": "PGUP", "KEY_NPAGE": "PGDN", "KEY_HOME": "HOME", "KEY_END": "END",
    "KEY_ENTER": "ENTER", "KEY_BACKSPACE": "BACKSPACE", "KEY_DC": "DELETE",
    "\n": "ENTER", "\r": "ENTER", "\x1b": "ESC", "\t": "TAB", "\x7f": "BACKSPACE", "\x08": "BACKSPACE",
    "KEY_B2": "", "KEY_RESIZE": "",
    # windows-curses names for the keypad
    "PADENTER": "ENTER", "KEY_A2": "UP", "KEY_C2": "DOWN", "KEY_B1": "LEFT", "KEY_B3": "RIGHT",
}


class App:
    def __init__(self, root: Screen, heading: str = "coinroute", clock=None):
        self.stack: list[Screen] = [root]
        self.heading = heading
        self.clock = clock or (lambda: _dt.datetime.now().strftime("%m/%d/%y  %H:%M"))
        self.running = True

    # --- drawing ------------------------------------------------------------------------------

    def draw(self, c: Canvas) -> None:
        rows, cols = c.size()
        c.fill(0, 0, rows, cols, "normal")
        right = self.clock()
        c.put(0, 0, (" " + self.heading)[: max(0, cols - len(right) - 3)].ljust(cols), "bar")
        c.put(0, max(0, cols - len(right) - 1), right, "bar")
        # draw the stack from the last full-screen one up, so boxes sit over their parent
        start = 0
        for i, s in enumerate(self.stack):
            if not isinstance(s, Message) and not getattr(s, "replace", False):
                start = i
        for s in self.stack[start:]:
            s.draw(c, 2, rows - 4)
        top = self.stack[-1]
        c.put(rows - 2, 0, (" " + top.status()).ljust(cols)[:cols], "status")
        x = 1
        c.put(rows - 1, 0, " " * cols, "bar")
        for k, what in top.keys:
            if x + len(k) + len(what) + 3 > cols:
                break
            c.put(rows - 1, x, k, "key")
            c.put(rows - 1, x + len(k) + 1, what, "bar")
            x += len(k) + len(what) + 4

    # --- keys -----------------------------------------------------------------------------------

    def key(self, k: str) -> None:
        if not k:
            return
        try:
            r = self.stack[-1].key(k)
        except Exception as e:  # noqa: BLE001 - a screen's error shouldn't end the program
            r = Message("Error", f"{type(e).__name__}: {e}", style="warn")
        if r == POP:
            if len(self.stack) > 1:  # the main menu stays; only its Exit item leaves
                self.stack.pop()
        elif r == QUIT:
            self.running = False
        elif isinstance(r, tuple) and r[0] == "replace":
            self.stack[-1] = r[1]
        elif isinstance(r, Screen):
            self.stack.append(r)

    def feed(self, keys, canvas: TextCanvas | None = None) -> TextCanvas:
        """Press keys and return the screen as text (tests and screenshots)."""
        c = canvas or TextCanvas()
        for k in keys:
            self.key(k)
            if not self.running:
                break
        self.draw(c)
        return c

    # --- curses ---------------------------------------------------------------------------------

    def run(self) -> None:
        import curses

        def main(scr):
            from .canvas import CursesCanvas

            try:
                curses.curs_set(0)
            except curses.error:
                pass
            scr.keypad(True)
            curses.raw()  # F-keys and ESC straight through; Ctrl-C handled below
            try:
                curses.set_escdelay(25)
            except AttributeError:
                pass
            c = CursesCanvas(scr)
            while self.running:
                rows, cols = c.size()
                scr.erase()
                if rows < 20 or cols < 60:
                    c.put(0, 0, "Make the terminal at least 80x24.")
                else:
                    self.draw(c)
                scr.refresh()
                self.key(self._read(scr))

        curses.wrapper(main)

    @staticmethod
    def _read(scr) -> str:
        try:
            ch = scr.get_wch()
        except KeyboardInterrupt:
            return "ESC"
        if isinstance(ch, int):
            import curses

            name = curses.keyname(ch).decode("ascii", "replace")
            if name.startswith("KEY_F(") and name.endswith(")"):
                return "F" + name[6:-1]
            return CURSES_KEYS.get(name, "")
        if ch == "\x03":  # Ctrl-C
            return "ESC"
        return CURSES_KEYS.get(ch, ch if ch.isprintable() else "")
