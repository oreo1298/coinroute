"""Make the app look right when Qt couldn't load the desktop's own theme.

PySide6 installed with pip carries its own Qt, which can't load KDE's theme
plugin (that comes with the system Qt). Qt then falls back to its built-in KDE
theme, which takes the font from KDE's settings but a 1990s default palette:
grey windows and a navy (#000080) selection. When we see that palette we
switch to Qt's Fusion style with a light or dark palette, whichever the
desktop prefers. A real theme (Breeze from the system PySide6, Windows,
macOS, GNOME via the portal) is left alone.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

QT_FALLBACK_HIGHLIGHT = QColor(0, 0, 128)  # Qt::darkBlue, which no real desktop theme uses


def dark_palette() -> QPalette:
    """A dark palette for Fusion, matched to the dark status colours in style.py."""
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#202124",
        QPalette.ColorRole.WindowText: "#e8eaed",
        QPalette.ColorRole.Base: "#1a1a19",
        QPalette.ColorRole.AlternateBase: "#232322",
        QPalette.ColorRole.Text: "#e8eaed",
        QPalette.ColorRole.Button: "#2b2c2f",
        QPalette.ColorRole.ButtonText: "#e8eaed",
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: "#3b6fb6",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Light: "#3f4044",
        QPalette.ColorRole.Midlight: "#3a3b3f",
        QPalette.ColorRole.Mid: "#4a4b4f",
        QPalette.ColorRole.Dark: "#151516",
        QPalette.ColorRole.Shadow: "#000000",
        QPalette.ColorRole.ToolTipBase: "#2b2c2f",
        QPalette.ColorRole.ToolTipText: "#e8eaed",
        QPalette.ColorRole.PlaceholderText: "#9aa0a6",
        QPalette.ColorRole.Link: "#8ab4f8",
    }
    for role, c in roles.items():
        p.setColor(role, QColor(c))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#7b7e83"))
    return p


def kde_prefers_dark(config_home: str | os.PathLike | None = None) -> bool | None:
    """True/False from the window colour in ~/.config/kdeglobals; None if there is no KDE setting."""
    base = Path(config_home or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / "kdeglobals"
    if not path.is_file():
        return None
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cfg.read(path, encoding="utf-8")
        rgb = cfg.get("Colors:Window", "BackgroundNormal", fallback=None)
        if rgb is None:
            return None
        r, g, b = (int(x) for x in rgb.split(",")[:3])
    except (configparser.Error, ValueError, OSError):
        return None
    return QColor(r, g, b).lightness() < 128


def desktop_prefers_dark(app: QApplication) -> bool:
    scheme = app.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return bool(kde_prefers_dark())


def uses_qt_fallback_palette(app: QApplication) -> bool:
    return app.palette().color(QPalette.ColorRole.Highlight) == QT_FALLBACK_HIGHLIGHT


def fix_fallback_theme(app: QApplication) -> bool:
    """Switch to Fusion + a proper palette if Qt fell back to its default one. Returns True if it did."""
    if os.environ.get("QT_STYLE_OVERRIDE") or not uses_qt_fallback_palette(app):
        return False
    app.setStyle("Fusion")
    app.setPalette(dark_palette() if desktop_prefers_dark(app) else app.style().standardPalette())
    return True
