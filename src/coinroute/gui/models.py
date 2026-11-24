"""Generic table models: a list of dict rows, and a lazily paged query."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt

SortRole = Qt.ItemDataRole.UserRole + 1  # a Python value to sort by (None sorts last)
RowRole = Qt.ItemDataRole.UserRole + 2  # the whole row dict

Index = QModelIndex | QPersistentModelIndex
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


@dataclass
class Column:
    title: str
    key: str
    fmt: Callable[[Any], str] | None = None  # value -> display text
    align: Qt.AlignmentFlag = LEFT
    tip: str | None = None  # header tooltip
    mono: bool = False

    def text(self, row: dict) -> str:
        v = row.get(self.key)
        if self.fmt:
            return self.fmt(v)
        return "" if v is None else str(v)


class RowsModel(QAbstractTableModel):
    """A read-only table over a list of dicts, described by Columns."""

    def __init__(self, columns: list[Column], rows: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = rows or []
        self._mono = None

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(self.columns):
            return None
        col = self.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return col.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return col.tip
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return col.align
        return None

    def cell(self, row: dict, col: Column, role: int):
        """Hook for subclasses: return a value for `role`, or NotImplemented for the default."""
        return NotImplemented

    def data(self, index: Index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = self.columns[index.column()]
        custom = self.cell(row, col, role)
        if custom is not NotImplemented:
            return custom
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            text = col.text(row)
            return text if role == Qt.ItemDataRole.DisplayRole or len(text) > 40 else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return col.align
        if role == SortRole:
            return row.get(col.key)
        if role == RowRole:
            return row
        if role == Qt.ItemDataRole.FontRole and col.mono:
            if self._mono is None:
                from .style import mono_font

                self._mono = mono_font()
            return self._mono
        return None


class PagedModel(RowsModel):
    """Rows fetched a page at a time from `fetch(after=key, limit=n)`; the view asks for more on scroll.

    Each row must carry a 'key' (see coinroute.db keyset paging). Nothing is
    loaded until `fetchMore` is called; `restart` loads the first page.
    """

    def __init__(self, columns: list[Column], page_size: int = 200, parent=None):
        super().__init__(columns, [], parent)
        self.page_size = page_size
        self._fetch: Callable[..., list[dict]] | None = None
        self._more = False

    def restart(self, fetch: Callable[..., list[dict]] | None) -> None:
        self.beginResetModel()
        self.rows = []
        self._fetch = fetch
        self._more = fetch is not None
        self.endResetModel()
        if self._more:
            self.fetchMore(QModelIndex())

    def canFetchMore(self, parent: Index = QModelIndex()) -> bool:
        return not parent.isValid() and self._more

    def fetchMore(self, parent: Index = QModelIndex()) -> None:
        if parent.isValid() or not self._more or self._fetch is None:
            return
        after = self.rows[-1]["key"] if self.rows else None
        page = self._fetch(after=after, limit=self.page_size)
        self._more = len(page) == self.page_size
        if page:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(page) - 1)
            self.rows.extend(page)
            self.endInsertRows()
