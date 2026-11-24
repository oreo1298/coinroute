"""Coin chart: cash in box and lifetime coin total over time, drawn with QPainter.

Two panels on one shared time axis rather than one chart with two y-scales
(the two values differ by orders of magnitude). Days the box was emptied are
marked. Moving the mouse shows a crosshair that snaps to the nearest poll and
one readout with every value at that date. Gaps of more than GAP_DAYS without
a good poll break the lines instead of drawing a straight line across them.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .style import is_dark, money

GAP_DAYS = 3
FMT = "%Y-%m-%d %H:%M"

# from the reference data-viz palette: slot 1 blue, slot 2 orange; chrome per mode
_COLORS = {
    False: {
        "line": "#2a78d6",
        "drop": "#eb6834",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "muted": "#6f6d68",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "tip": "#ffffff",
        "tipline": "#d5d3cc",
    },
    True: {
        "line": "#3987e5",
        "drop": "#d95926",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "muted": "#9a988f",
        "ink": "#f0efec",
        "ink2": "#c3c2b7",
        "tip": "#262624",
        "tipline": "#45443f",
    },
}


def _nice_step(span: float, target: int = 4) -> float:
    raw = span / max(1, target)
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _axis_money(cents: float, step: float) -> str:
    d = cents / 100
    if abs(d) >= 10_000:
        return f"${d / 1000:,.0f}K" if step >= 100_000 else f"${d / 1000:,.1f}K"
    return f"${d:,.0f}" if step >= 100 else f"${d:,.2f}"


def _month_ticks(t0: _dt.datetime, t1: _dt.datetime, max_ticks: int) -> list[_dt.datetime]:
    months = (t1.year - t0.year) * 12 + t1.month - t0.month + 1
    for step in (1, 2, 3, 6, 12, 24, 60):
        if months / step <= max_ticks:
            break
    y, m = t0.year, t0.month
    out = []
    while True:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        d = _dt.datetime(y, m, 1)
        if d > t1:
            break
        if ((y * 12 + m - 1) % step) == 0:
            out.append(d)
    return out


class CoinChart(QWidget):
    """Set data with set_polls(rows from RouteDB.coin_polls); limit the time range with set_days()."""

    hovered = Signal(int)  # index into polls, -1 when the pointer leaves

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(QSize(420, 260))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.polls: list[dict] = []
        self._t: list[float] = []  # poll times, seconds since epoch
        self.days: int | None = None
        self.hover: int = -1
        self._plot_x = (0.0, 1.0)
        self._range = (0.0, 1.0)

    # --- data ---

    def set_polls(self, polls: list[dict]) -> None:
        self.polls = [p for p in polls if p.get("at")]
        self._t = [_dt.datetime.strptime(p["at"], FMT).timestamp() for p in self.polls]
        self.hover = -1
        self.update()

    def set_days(self, days: int | None) -> None:
        self.days = days
        self.hover = -1
        self.update()

    def visible(self) -> tuple[int, int]:
        """Index range [i0, i1) of polls inside the chosen time range."""
        if not self._t:
            return 0, 0
        if self.days is None:
            return 0, len(self._t)
        start = self._t[-1] - self.days * 86400
        return bisect.bisect_left(self._t, start), len(self._t)

    def poll_at(self, x: float) -> int:
        """Nearest visible poll to a widget x coordinate (-1 if none)."""
        i0, i1 = self.visible()
        if i0 >= i1:
            return -1
        (px0, px1), (t0, t1) = self._plot_x, self._range
        t = t0 + (x - px0) / max(1.0, px1 - px0) * (t1 - t0)
        j = bisect.bisect_left(self._t, t, i0, i1)
        cands = [k for k in (j - 1, j) if i0 <= k < i1]
        return min(cands, key=lambda k: abs(self._t[k] - t))

    # --- events ---

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        x = e.position().x()
        px0, px1 = self._plot_x
        i = self.poll_at(min(max(x, px0), px1)) if px0 - 20 <= x <= px1 + 20 else -1
        if i != self.hover:
            self.hover = i
            self.hovered.emit(i)
            self.update()

    def leaveEvent(self, e) -> None:
        if self.hover != -1:
            self.hover = -1
            self.hovered.emit(-1)
            self.update()

    # --- painting ---

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = is_dark(self.palette())
        c = {k: QColor(v) for k, v in _COLORS[dark].items()}
        surface = self.palette().color(QPalette.ColorRole.Base)
        p.fillRect(self.rect(), surface)
        font = QFont(self.font())
        fm = QFontMetrics(font)
        small = QFont(font)
        small.setPointSizeF(max(7.0, font.pointSizeF() * 0.9))
        sfm = QFontMetrics(small)

        i0, i1 = self.visible()
        if i1 - i0 < 1:
            p.setPen(c["muted"])
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No successful polls to chart.")
            return
        rows = self.polls[i0:i1]
        ts = self._t[i0:i1]
        t0, t1 = ts[0], ts[-1]
        if t1 - t0 < 86400:
            t0, t1 = t0 - 43200, t1 + 43200
        self._range = (t0, t1)

        box_max = max(r["cash_box_cents"] for r in rows)
        box_step = _nice_step(max(box_max, 100))
        box_top = math.ceil(max(box_max, 1) / box_step) * box_step
        tot_lo, tot_hi = min(r["coin_total_cents"] for r in rows), max(r["coin_total_cents"] for r in rows)
        tot_step = _nice_step(max(tot_hi - tot_lo, 100), 3)
        tot_bot = math.floor(tot_lo / tot_step) * tot_step
        tot_top = max(math.ceil(tot_hi / tot_step) * tot_step, tot_bot + tot_step)

        ylab_w = (
            max(
                sfm.horizontalAdvance(_axis_money(v, s))
                for v, s in ((box_top, box_step), (tot_top, tot_step), (tot_bot, tot_step))
            )
            + 10
        )
        left, right = 12 + ylab_w, self.width() - 16
        title_h = fm.height() + 6
        axis_h = sfm.height() + 10
        gap = 14
        avail = self.height() - 8 - axis_h
        h1 = (avail - 2 * title_h - gap) * 0.58
        h2 = (avail - 2 * title_h - gap) - h1
        top1 = 6 + title_h
        top2 = top1 + h1 + gap + title_h
        self._plot_x = (left, right)

        def X(t: float) -> float:
            return left + (t - t0) / (t1 - t0) * (right - left)

        def panel(top, h, lo, hi, step, title, key, legend):
            def Y(v: float) -> float:
                return top + h - (v - lo) / (hi - lo) * h

            # title + legend
            p.setFont(font)
            p.setPen(c["ink"])
            p.drawText(QPointF(left, top - 7), title)
            if legend:
                lx = left + fm.horizontalAdvance(title) + 18
                for kind, label in legend:
                    p.setFont(small)
                    if kind == "line":
                        p.setPen(QPen(c["line"], 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                        p.drawLine(
                            QPointF(lx, top - 7 - sfm.ascent() / 3),
                            QPointF(lx + 14, top - 7 - sfm.ascent() / 3),
                        )
                    else:
                        p.setPen(QPen(surface, 2))
                        p.setBrush(c["drop"])
                        p.drawEllipse(QPointF(lx + 7, top - 7 - sfm.ascent() / 3), 4.5, 4.5)
                    p.setPen(c["ink2"])
                    p.drawText(QPointF(lx + 19, top - 7), label)
                    lx += 19 + sfm.horizontalAdvance(label) + 16
            # grid + y labels
            p.setFont(small)
            v = lo
            while v <= hi + step / 1000:
                y = Y(v)
                p.setPen(QPen(c["axis"] if v == lo else c["grid"], 1))
                p.drawLine(QPointF(left, y), QPointF(right, y))
                p.setPen(c["muted"])
                p.drawText(
                    QRectF(4, y - sfm.height() / 2, ylab_w, sfm.height()),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    _axis_money(v, step),
                )
                v += step
            # the series, broken at gaps
            path, area, started, seg_start = QPainterPath(), QPainterPath(), False, 0.0
            prev_t = None
            last_x = left
            base = Y(lo)
            segments = []
            for t, r in zip(ts, rows):
                x, y = X(t), Y(r[key])
                if prev_t is None or t - prev_t > GAP_DAYS * 86400:
                    if started:
                        segments.append((path, seg_start, last_x))
                    path = QPainterPath(QPointF(x, y))
                    seg_start, started = x, True
                else:
                    path.lineTo(x, y)
                last_x = x
                prev_t = t
            if started:
                segments.append((path, seg_start, last_x))
            wash = QColor(c["line"])
            wash.setAlphaF(0.10)
            for seg, sx, ex in segments:
                if key == "cash_box_cents":
                    area = QPainterPath(seg)
                    area.lineTo(ex, base)
                    area.lineTo(sx, base)
                    area.closeSubpath()
                    p.fillPath(area, wash)
                p.setPen(
                    QPen(
                        c["line"],
                        2,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    )
                )
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(seg)
                if seg.elementCount() == 1:  # a lone poll between gaps
                    p.setBrush(c["line"])
                    p.setPen(Qt.PenStyle.NoPen)
                    pt = seg.elementAt(0)
                    p.drawEllipse(QPointF(pt.x, pt.y), 2.5, 2.5)
            return Y

        Y1 = panel(
            top1,
            h1,
            0,
            box_top,
            box_step,
            "Cash in box",
            "cash_box_cents",
            [("line", "Cash in box"), ("dot", "Box emptied")],
        )
        # emptied-box markers
        p.setPen(QPen(surface, 2))
        p.setBrush(c["drop"])
        for t, r in zip(ts, rows):
            if r.get("collected_cents") is not None:
                p.drawEllipse(QPointF(X(t), Y1(r["cash_box_cents"])), 5, 5)
        Y2 = panel(top2, h2, tot_bot, tot_top, tot_step, "Lifetime coin total", "coin_total_cents", None)

        # time axis
        p.setFont(small)
        p.setPen(c["muted"])
        d0, d1 = _dt.datetime.fromtimestamp(t0), _dt.datetime.fromtimestamp(t1)
        label_w = sfm.horizontalAdvance("Sep 2026") + 18
        ticks = _month_ticks(d0, d1, max(2, int((right - left) / label_w)))
        ybase = top2 + h2
        for d in ticks:
            x = X(d.timestamp())
            p.setPen(QPen(c["grid"], 1))
            p.drawLine(QPointF(x, top1), QPointF(x, top1 + h1))
            p.drawLine(QPointF(x, top2), QPointF(x, ybase))
            p.setPen(c["muted"])
            text = (
                d.strftime("%b %Y") if d.month == 1 or len(ticks) <= 8 or d == ticks[0] else d.strftime("%b")
            )
            p.drawText(
                QRectF(x - label_w / 2, ybase + 4, label_w, sfm.height()), Qt.AlignmentFlag.AlignHCenter, text
            )

        # crosshair + readout
        if self.hover >= 0 and i0 <= self.hover < i1:
            r = self.polls[self.hover]
            x = X(self._t[self.hover])
            p.setPen(QPen(c["ink2"], 1))
            p.drawLine(QPointF(x, top1), QPointF(x, ybase))
            for yv in (Y1(r["cash_box_cents"]), Y2(r["coin_total_cents"])):
                p.setPen(QPen(surface, 2))
                p.setBrush(c["line"])
                p.drawEllipse(QPointF(x, yv), 4.5, 4.5)
            lines = [(money(r["cash_box_cents"]), "in box"), (money(r["coin_total_cents"]), "lifetime total")]
            if r.get("collected_cents") is not None:
                lines.append((money(r["collected_cents"]), "taken out (box emptied)"))
            when = _dt.datetime.strptime(r["at"], FMT).strftime("%d %b %Y %H:%M")
            bold = QFont(font)
            bold.setBold(True)
            bfm = QFontMetrics(bold)
            w = (
                max(
                    [sfm.horizontalAdvance(when)]
                    + [bfm.horizontalAdvance(v) + 8 + sfm.horizontalAdvance(lab) for v, lab in lines]
                )
                + 20
            )
            hh = sfm.height() + 6 + len(lines) * (bfm.height() + 2) + 12
            bx = x + 12 if x + 12 + w < right else x - 12 - w
            by = top1 + 4
            p.setPen(QPen(c["tipline"], 1))
            p.setBrush(c["tip"])
            p.drawRoundedRect(QRectF(bx, by, w, hh), 6, 6)
            p.setFont(small)
            p.setPen(c["ink2"])
            p.drawText(QPointF(bx + 10, by + 8 + sfm.ascent()), when)
            yy = by + 8 + sfm.height() + 6
            for v, lab in lines:
                p.setFont(bold)
                p.setPen(c["ink"])
                p.drawText(QPointF(bx + 10, yy + bfm.ascent()), v)
                p.setFont(small)
                p.setPen(c["ink2"])
                p.drawText(QPointF(bx + 10 + bfm.horizontalAdvance(v) + 8, yy + bfm.ascent()), lab)
                yy += bfm.height() + 2
