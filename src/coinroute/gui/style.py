"""Colours and value formatting shared by the views."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# status kind -> (light background, light text, dark background, dark text)
_STATUS = {
    "ok": ("#d9f2e1", "#11602a", "#1d3f28", "#93dcaa"),
    "silent": ("#fcefcf", "#7a4f00", "#4a3912", "#f3cd72"),
    "dead": ("#fadcd9", "#9c1f17", "#4d201c", "#f3a39c"),
    "never": ("#e6e8eb", "#4b5059", "#383b40", "#c3c7cd"),
}
STATUS_LABELS = {
    "ok": "Answering",
    "silent": "Silent ≤ 30 days",
    "dead": "Silent > 30 days",
    "never": "Never polled",
}
STATUS_ORDER = {"dead": 0, "silent": 1, "never": 2, "ok": 3}  # sort: worst first


def status_text(kind: str, days: int | None) -> str:
    """Short status for tables and headers: Answering / Silent 12 days / Never polled."""
    if kind == "ok":
        return "Answering"
    if kind == "never":
        return "Never polled"
    return f"Silent {days} day{'s' if days != 1 else ''}"


def is_dark(palette: QPalette | None = None) -> bool:
    pal = palette or QApplication.palette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def status_colors(kind: str, palette: QPalette | None = None) -> tuple[QColor, QColor]:
    """(background, foreground) for a status kind, suited to the current light or dark palette."""
    lb, lf, db, df = _STATUS.get(kind, _STATUS["never"])
    return (QColor(db), QColor(df)) if is_dark(palette) else (QColor(lb), QColor(lf))


def money(cents: int | None, blank: str = "") -> str:
    return blank if cents is None else f"${cents / 100:,.2f}"


def count(n: int | None) -> str:
    return "" if n is None else f"{n:,}"


def mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    return f


def muted(palette: QPalette | None = None) -> QColor:
    pal = palette or QApplication.palette()
    c = pal.color(QPalette.ColorRole.WindowText)
    c.setAlphaF(0.62)
    return c


def css(color: QColor) -> str:
    """A colour for Qt style sheets (rgba keeps the alpha)."""
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def muted_css() -> str:
    return f"color: {css(muted())};"
