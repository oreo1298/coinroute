"""Reports dialog: pick Coin or Trouble report and a period, preview, print or save."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from ..db import RouteDB
from ..reports import coin_report_html, default_period, trouble_report_html

FMT = "%Y-%m-%d %H:%M"
QFMT = "yyyy-MM-dd HH:mm"


class ReportsDialog(QDialog):
    def __init__(self, db: RouteDB, parent=None, kind: str = "coin"):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Reports")
        self.resize(1000, 700)
        self.company = (db.system() or {}).get("company")
        self.html = ""

        self.kind = QComboBox()
        self.kind.addItem("Coin report", "coin")
        self.kind.addItem("Trouble report", "trouble")
        self.start = QDateTimeEdit()
        self.end = QDateTimeEdit()
        for w in (self.start, self.end):
            w.setDisplayFormat(QFMT)
            w.setCalendarPopup(True)
        self.flags = QLineEdit()
        self.flags.setPlaceholderText("all flags (or e.g. RJ, HG, LB)")
        self.flags.setToolTip("Trouble report: only show these flag codes")
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("Report:"))
        top.addWidget(self.kind)
        top.addSpacing(12)
        top.addWidget(QLabel("From:"))
        top.addWidget(self.start)
        top.addWidget(QLabel("to"))
        top.addWidget(self.end)
        top.addSpacing(12)
        self.flags_label = QLabel("Flags:")
        top.addWidget(self.flags_label)
        top.addWidget(self.flags, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.print_btn = QPushButton("Print…")
        self.pdf_btn = QPushButton("Save PDF…")
        self.html_btn = QPushButton("Save HTML…")
        close = QPushButton("Close")
        for b in (self.print_btn, self.pdf_btn, self.html_btn, close):
            buttons.addWidget(b)
        self.print_btn.clicked.connect(self.print_report)
        self.pdf_btn.clicked.connect(lambda: self.save_pdf())
        self.html_btn.clicked.connect(lambda: self.save_html())
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.preview, 1)
        lay.addLayout(buttons)

        self.kind.setCurrentIndex(0 if kind == "coin" else 1)
        self._set_default_period()
        self.kind.currentIndexChanged.connect(lambda _: (self._set_default_period(), self.refresh()))
        self.start.dateTimeChanged.connect(lambda _: self.refresh())
        self.end.dateTimeChanged.connect(lambda _: self.refresh())
        self.flags.editingFinished.connect(self.refresh)
        self.refresh()

    def _set_default_period(self) -> None:
        days = 7 if self.kind.currentData() == "coin" else 1
        a, b = default_period(self.db.meta().get("data_end"), days)
        for w, v in ((self.start, a), (self.end, b)):
            w.blockSignals(True)
            w.setDateTime(QDateTime.fromString(v, QFMT))
            w.blockSignals(False)
        trouble = self.kind.currentData() == "trouble"
        self.flags.setVisible(trouble)
        self.flags_label.setVisible(trouble)

    def period(self) -> tuple[str, str]:
        a = self.start.dateTime().toString(QFMT)
        b = self.end.dateTime().toString(QFMT)
        return (a, b) if a <= b else (b, a)

    def refresh(self) -> None:
        start, end = self.period()
        if self.kind.currentData() == "coin":
            self.html = coin_report_html(self.db.coin_report(start, end), self.company)
        else:
            codes = [c.strip().upper() for c in self.flags.text().replace(" ", ",").split(",") if c.strip()]
            self.html = trouble_report_html(self.db.trouble_report(start, end, codes or None), self.company)
        self.preview.setHtml(self.html)

    def _document(self) -> QTextDocument:
        doc = QTextDocument()
        doc.setHtml(self.html)
        return doc

    def _printer(self, mode=QPrinter.PrinterMode.HighResolution) -> QPrinter:
        p = QPrinter(mode)
        p.setPageOrientation(QPageLayout.Orientation.Landscape)
        p.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
        return p

    def _name(self, ext: str) -> str:
        kind = self.kind.currentData()
        day = _dt.datetime.strptime(self.period()[1], FMT).strftime("%Y-%m-%d")
        return f"{kind}-report-{day}.{ext}"

    def print_report(self) -> None:
        printer = self._printer()
        dlg = QPrintDialog(printer, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._document().print_(printer)

    def save_pdf(self, path: str | None = None) -> str | None:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save report as PDF", self._name("pdf"), "PDF (*.pdf)"
            )
            if not path:
                return None
        printer = self._printer()
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        self._document().print_(printer)
        return path

    def save_html(self, path: str | None = None) -> str | None:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save report as HTML", self._name("html"), "Web page (*.html)"
            )
            if not path:
                return None
        try:
            Path(path).write_text(self.html, encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "Can't save", str(e))
            return None
        return path

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):  # Enter in the flags box shouldn't close
            self.refresh()
            return
        super().keyPressEvent(e)
