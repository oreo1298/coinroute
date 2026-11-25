"""A layout that places widgets left to right and wraps to a new line when the width runs out.

Used for rows of chips and controls so they never force a wide minimum window:
the narrowest it needs is the widest single item.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int) -> QLayoutItem | None:
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int) -> QLayoutItem | None:
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, dry=False)

    def sizeHint(self) -> QSize:
        shown = [i for i in self._items if not i.isEmpty()]
        w = sum(i.sizeHint().width() for i in shown) + self._spacing * max(0, len(shown) - 1)
        h = max((i.sizeHint().height() for i in shown), default=0)
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        size = QSize()
        for i in self._items:
            if not i.isEmpty():
                size = size.expandedTo(i.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, dry: bool) -> int:
        """Lay the items out in lines (each item at its size hint, centred in its line); return the height."""
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines: list[list[tuple[QLayoutItem, QSize]]] = [[]]
        x = area.x()
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if lines[-1] and x + hint.width() > area.right() + 1:
                lines.append([])
                x = area.x()
            lines[-1].append((item, hint))
            x += hint.width() + self._spacing
        y = area.y()
        for line in lines:
            line_h = max((h.height() for _, h in line), default=0)
            x = area.x()
            for item, hint in line:
                if not dry:
                    top = y + (line_h - hint.height()) // 2
                    item.setGeometry(QRect(QPoint(x, top), hint.boundedTo(QSize(area.width(), hint.height()))))
                x += hint.width() + self._spacing
            y += line_h + self._spacing
        return max(0, y - self._spacing - area.y()) + m.top() + m.bottom()
