#!/usr/bin/env python3
"""Render the terminal version's screens to PNG files from the demo route (needs PySide6).

    QT_QPA_PLATFORM=offscreen python tools/tui_shots.py OUTDIR

Each screen is drawn on coinroute.tui.canvas.TextCanvas (80x25) and painted with the DOS
palette, so layout and colours can be checked without a terminal.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter

from coinroute.demo import make_demo
from coinroute.importer import import_xnet
from coinroute.tui.canvas import STYLES, TextCanvas
from coinroute.tui.screens import make_app

DOS = {"black": "#000000", "blue": "#0000aa", "green": "#00aa00", "cyan": "#00aaaa", "red": "#aa0000",
       "magenta": "#aa00aa", "yellow": "#aa5500", "white": "#aaaaaa"}
BRIGHT = {"black": "#555555", "blue": "#5555ff", "green": "#55ff55", "cyan": "#55ffff", "red": "#ff5555",
          "magenta": "#ff55ff", "yellow": "#ffff55", "white": "#ffffff"}

SHOTS = {
    "main-menu": [],
    "site-picker": ["1"],
    "site-search": ["1", "C", "E", "D"],
    "site-record": ["1", "ENTER"],
    "coin-polls": ["1", "ENTER", "F3"],
    "costing-menu": ["2"],
    "options": ["3"],
    "reports-menu": ["4"],
    "report-prompt": ["4", "1", "1"],
    "coin-report": ["4", "1", "1", "ENTER", "ENTER"],
    "trouble-report": ["4", "1", "2", "ENTER", "ENTER", "ENTER"],
    "collections": ["4", "6"],
    "modems-menu": ["5"],
    "modem-status": ["5", "1"],
    "auto-polling": ["5", "2"],
    "poll-lists": ["5", "2", "1"],
    "comm-errors": ["5", "5"],
    "system-params": ["6", "2"],
    "file-menu": ["7"],
    "not-yet": ["2", "1"],
}


def paint(c: TextCanvas, path: Path) -> None:
    font = QFont("DejaVu Sans Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(16)
    fm = QFontMetrics(font)
    cw, ch = fm.horizontalAdvance("M"), fm.height()
    img = QImage(c.cols * cw, c.rows * ch, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setFont(font)
    for y, row in enumerate(c.cells):
        for x, (char, style) in enumerate(row):
            fg, bg, bold = STYLES.get(style, STYLES["normal"])
            p.fillRect(x * cw, y * ch, cw, ch, QColor(DOS[bg]))
            if char != " ":
                p.setPen(QColor((BRIGHT if bold or fg == "yellow" else DOS)[fg]))
                p.drawText(x * cw, y * ch + fm.ascent(), char)
    p.end()
    img.save(str(path))


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "tui-shots")
    out.mkdir(parents=True, exist_ok=True)
    QGuiApplication.instance() or QGuiApplication(argv[:1])
    with tempfile.TemporaryDirectory() as tmp:
        make_demo(tmp)
        db = Path(tmp) / "demo.db"
        import_xnet(Path(tmp) / "XNET", db)
        for name, keys in SHOTS.items():
            app = make_app(str(db))
            app.clock = lambda: "05/02/26  09:00"
            paint(app.feed(keys), out / f"tui-{name}.png")
            print(out / f"tui-{name}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
