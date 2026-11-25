"""Theme fallback: pip PySide6 on KDE gets Qt's default palette; we replace it. Headless (pytest-qt)."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QPalette  # noqa: E402

from coinroute.gui import theme  # noqa: E402


@pytest.fixture
def restore_look(qapp):
    style, palette = qapp.style().name(), qapp.palette()
    yield qapp
    qapp.setStyle(style)
    qapp.setPalette(palette)


def fallback(qapp):
    p = QPalette(QColor("#d6d2d0"))
    p.setColor(QPalette.ColorRole.Highlight, theme.QT_FALLBACK_HIGHLIGHT)
    qapp.setPalette(p)


def test_fallback_palette_is_replaced_light(restore_look, monkeypatch, tmp_path):
    qapp = restore_look
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no kdeglobals: light
    monkeypatch.delenv("QT_STYLE_OVERRIDE", raising=False)
    fallback(qapp)
    assert theme.fix_fallback_theme(qapp)
    assert qapp.style().name() == "fusion"
    assert qapp.palette().color(QPalette.ColorRole.Highlight) != theme.QT_FALLBACK_HIGHLIGHT
    assert qapp.palette().color(QPalette.ColorRole.Window).lightness() > 200
    assert not theme.fix_fallback_theme(qapp)  # a real palette is left alone


def test_fallback_palette_is_replaced_dark_from_kdeglobals(restore_look, monkeypatch, tmp_path):
    qapp = restore_look
    (tmp_path / "kdeglobals").write_text(
        "[General]\nfont=Noto Sans,10\n\n[Colors:Window]\n"
        "BackgroundNormal=32,35,38\nForegroundNormal=252,252,252\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("QT_STYLE_OVERRIDE", raising=False)
    assert theme.kde_prefers_dark() is True
    fallback(qapp)
    assert theme.fix_fallback_theme(qapp)
    assert qapp.palette().color(QPalette.ColorRole.Window).lightness() < 60


def test_style_override_is_respected(restore_look, monkeypatch):
    qapp = restore_look
    monkeypatch.setenv("QT_STYLE_OVERRIDE", "windows")
    fallback(qapp)
    assert not theme.fix_fallback_theme(qapp)


def test_kdeglobals_parsing(tmp_path):
    assert theme.kde_prefers_dark(tmp_path) is None
    (tmp_path / "kdeglobals").write_text("[Colors:Window]\nBackgroundNormal=239,240,241\n")
    assert theme.kde_prefers_dark(tmp_path) is False
    (tmp_path / "kdeglobals").write_text("[Colors:Window]\nBackgroundNormal=not a colour\n")
    assert theme.kde_prefers_dark(tmp_path) is None
