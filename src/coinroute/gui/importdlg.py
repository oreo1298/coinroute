"""Import an ExpressNet folder: choose source and destination, run on a thread, show the result."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..importer import INDEX_STEP, SOURCE_FILES, ImportReport, find_file, find_xnet_root, import_xnet
from .style import muted_css
from .workers import Worker

TEMP_SUFFIX = ".importing"


@dataclass
class ImportJob:
    source: Path
    dest: Path
    include_calls: bool = True


@dataclass
class ImportOutcome:
    job: ImportJob
    report: ImportReport
    seconds: float
    temp_path: Path | None  # set when an existing file is to be replaced once the import succeeded


def run_import(job: ImportJob, progress) -> ImportOutcome:
    """The worker-thread side. Writes to a temporary name first if the destination exists."""
    target = job.dest
    temp = None
    if target.exists():
        temp = target.with_name(target.name + TEMP_SUFFIX)
        temp.unlink(missing_ok=True)  # left over from an import that was killed
        target = temp
    t0 = time.monotonic()
    rep = import_xnet(
        job.source, target, include_calls=job.include_calls, progress=progress, progress_every=5000
    )
    return ImportOutcome(job, rep, time.monotonic() - t0, temp)


def finish_import(outcome: ImportOutcome) -> Path:
    """GUI-thread side, after the worker finished: move a replacement into place."""
    if outcome.temp_path is not None:
        os.replace(outcome.temp_path, outcome.job.dest)
    return outcome.job.dest


def default_destination(source: Path) -> Path:
    docs = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or Path.home()
    )
    base = docs / "coinroute" / f"{source.name or 'route'}-{time.strftime('%Y-%m-%d')}"
    p = base.with_suffix(".db")
    n = 2
    while p.exists():
        p = base.with_name(f"{base.name}-{n}").with_suffix(".db")
        n += 1
    return p


class ImportDialog(QDialog):
    """Pick the ExpressNet folder, where the new database goes, and whether to include calls."""

    def __init__(self, parent=None, source: str = "", dest: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Import ExpressNet folder")
        self.setMinimumWidth(620)
        self._dest_confirmed = False

        intro = QLabel(
            "coinroute reads the ExpressNet folder (usually <b>C:\\XNET</b>) and copies everything "
            "into a new database file. The ExpressNet folder itself is never changed."
        )
        intro.setWordWrap(True)

        self.source = QLineEdit(source)
        self.source.setPlaceholderText("The XNET folder, or a folder that contains it")
        browse_src = QPushButton("Browse…")
        browse_src.clicked.connect(self._browse_source)
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)

        self.dest = QLineEdit(dest)
        self.dest.setPlaceholderText("New database file, e.g. route.db")
        browse_dest = QPushButton("Browse…")
        browse_dest.clicked.connect(self._browse_dest)
        self.dest_note = QLabel()
        self.dest_note.setWordWrap(True)

        self.calls = QCheckBox("Include individual calls (audit records)")
        self.calls.setChecked(True)
        self.calls.setToolTip(
            "Every dialed number with date and time. On a large route this is millions of "
            "rows: a bigger file and a slower import."
        )

        form = QFormLayout()
        form.addRow("ExpressNet folder:", self._row(self.source, browse_src))
        form.addRow("", self.source_note)
        form.addRow("Save database as:", self._row(self.dest, browse_dest))
        form.addRow("", self.dest_note)
        form.addRow("", self.calls)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_btn = self.buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addSpacing(6)
        lay.addLayout(form)
        lay.addStretch(1)
        lay.addWidget(self.buttons)

        self.source.textChanged.connect(self._validate)
        self.dest.textChanged.connect(self._dest_edited)
        self._validate()

    @staticmethod
    def _row(edit: QLineEdit, button: QPushButton) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        h.addWidget(button)
        return w

    def _browse_source(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the ExpressNet folder", self.source.text() or "")
        if d:
            self.source.setText(d)

    def _browse_dest(self) -> None:
        start = self.dest.text() or str(default_destination(Path(self.source.text() or "route")))
        f, _ = QFileDialog.getSaveFileName(
            self, "Save the new database as", start, "coinroute database (*.db);;All files (*)"
        )
        if f:
            if not f.lower().endswith(".db") and not Path(f).suffix:
                f += ".db"
            self.dest.setText(f)
            self._dest_confirmed = True  # the save dialog already asked about replacing

    def _dest_edited(self) -> None:
        self._dest_confirmed = False
        self._validate()

    def _validate(self) -> None:
        ok_src = False
        src = self.source.text().strip()
        if not src:
            self._note(self.source_note, "")
        else:
            try:
                root = find_xnet_root(Path(src))
            except (FileNotFoundError, OSError):
                self._note(
                    self.source_note, "✗ No ExpressNet data here (looking for SITES\\SITES.DAT).", bad=True
                )
            else:
                found = sum(1 for rel in SOURCE_FILES if find_file(root, rel))
                where = "" if root == Path(src) else f" in {root.name}"
                self._note(
                    self.source_note,
                    f"✓ ExpressNet folder found{where}: {found} of {len(SOURCE_FILES)} data files present.",
                )
                ok_src = True
                if not self.dest.text().strip():
                    self.dest.blockSignals(True)
                    self.dest.setText(str(default_destination(root)))
                    self.dest.blockSignals(False)
        dst = self.dest.text().strip()
        ok_dst = bool(dst)
        if dst and Path(dst).exists():
            self._note(
                self.dest_note, "This file exists and will be replaced once the import succeeds.", warn=True
            )
        elif dst and not Path(dst).parent.exists():
            self._note(self.dest_note, "The folder will be created.")
        else:
            self._note(self.dest_note, "")
        self.import_btn.setEnabled(ok_src and ok_dst)

    def _note(self, label: QLabel, text: str, bad: bool = False, warn: bool = False) -> None:
        label.setText(text)
        label.setVisible(bool(text))
        if bad:
            label.setStyleSheet("color: #c62828;")
        elif warn:
            label.setStyleSheet("color: #b26a00;")
        else:
            label.setStyleSheet(muted_css())

    def _accept(self) -> None:
        dest = Path(self.dest.text().strip())
        if dest.exists() and not self._dest_confirmed:
            r = QMessageBox.question(
                self,
                "Replace file?",
                f"{dest.name} already exists. Replace it with the "
                "new import?\n\nThe old file stays until the import has succeeded.",
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        if dest.exists() and dest.is_dir():
            QMessageBox.warning(self, "Not a file", f"{dest} is a folder.")
            return
        self.accept()

    def job(self) -> ImportJob:
        src = find_xnet_root(Path(self.source.text().strip()))
        dest = Path(self.dest.text().strip())
        dest.parent.mkdir(parents=True, exist_ok=True)
        return ImportJob(src, dest, self.calls.isChecked())


class ImportProgressDialog(QDialog):
    """Shows which file is being read while the import runs on a worker thread.

    Emits `done(outcome)` on success; on failure it shows the error; on cancel it just closes.
    """

    done = Signal(object)

    def __init__(self, job: ImportJob, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Importing")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.job = job
        self.outcome: ImportOutcome | None = None
        self.error: str | None = None
        self._t0 = time.monotonic()

        title = QLabel(f"Importing <b>{job.source}</b>")
        title.setWordWrap(True)
        self.step = QLabel("Starting…")
        self.bar = QProgressBar()
        self.bar.setRange(0, len(SOURCE_FILES) + 1)
        self.bar.setFormat("Step %v of %m")
        self.bar.setTextVisible(True)
        self.detail = QLabel(" ")
        self.elapsed = QLabel("0:00")
        self.elapsed.setStyleSheet(muted_css())
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addSpacing(4)
        lay.addWidget(self.step)
        lay.addWidget(self.bar)
        lay.addWidget(self.detail)
        foot = QHBoxLayout()
        foot.addWidget(self.elapsed)
        foot.addStretch(1)
        foot.addWidget(self.cancel_btn)
        lay.addLayout(foot)

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(500)

        self.worker = Worker(lambda progress: run_import(job, progress))
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)

    def start(self) -> None:
        self.worker.start()

    def _tick(self) -> None:
        s = int(time.monotonic() - self._t0)
        self.elapsed.setText(f"{s // 60}:{s % 60:02d}")

    def _progress(self, step: str, n: int) -> None:
        if step == INDEX_STEP:
            self.bar.setValue(len(SOURCE_FILES) + 1)
            self.step.setText("Building indexes…")
            self.detail.setText("Almost done.")
            return
        if step in SOURCE_FILES:
            self.bar.setValue(SOURCE_FILES.index(step) + 1)
        self.step.setText(f"Reading {step}")
        self.detail.setText(f"{n:,} records" if n else " ")

    def _cancel(self) -> None:
        self.worker.cancel()
        self.cancel_btn.setEnabled(False)
        self.step.setText("Cancelling…")

    def _finished(self, outcome: ImportOutcome) -> None:
        self.outcome = outcome
        self._end()
        self.done.emit(outcome)
        self.accept()

    def _failed(self, message: str, tb: str) -> None:
        self.error = message
        self._end()
        box = QMessageBox(
            QMessageBox.Icon.Critical, "Import failed", f"The import stopped:\n\n{message}", parent=self
        )
        box.setDetailedText(tb)
        box.exec()
        self.reject()

    def _cancelled(self) -> None:
        self._end()
        self.reject()

    def _end(self) -> None:
        self._clock.stop()
        self.worker.wait()

    def reject(self) -> None:
        if self.worker.running():
            self._cancel()  # Esc / close button: ask the worker to stop first
            return
        super().reject()


class ImportResultDialog(QDialog):
    """Counts per file, missing files and warnings after an import."""

    def __init__(self, outcome: ImportOutcome, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import finished")
        self.setMinimumSize(620, 520)
        rep = outcome.report
        dest = outcome.job.dest

        secs = outcome.seconds
        took = (
            "under a second"
            if secs < 1
            else f"{secs:.0f} seconds"
            if secs < 90
            else f"{secs // 60:.0f} min {secs % 60:.0f} s"
        )
        total = sum(rep.counts.values())
        head = QLabel(
            f"<b>Imported {outcome.job.source.name}</b> into <b>{dest.name}</b> in {took} "
            f"— {total:,} records."
        )
        head.setWordWrap(True)
        sub = QLabel(str(dest))
        sub.setStyleSheet(muted_css())
        sub.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.counts = QTableWidget(len(rep.counts), 2)
        self.counts.setHorizontalHeaderLabels(["File", "Records"])
        self.counts.verticalHeader().hide()
        self.counts.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.counts.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.counts.setShowGrid(False)
        self.counts.setAlternatingRowColors(True)
        self.counts.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for i, (name, n) in enumerate(rep.counts.items()):
            self.counts.setItem(i, 0, QTableWidgetItem(name))
            it = QTableWidgetItem(f"{n:,}")
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.counts.setItem(i, 1, it)
        hh = self.counts.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        row_h = self.fontMetrics().height() + 8
        self.counts.verticalHeader().setDefaultSectionSize(row_h)
        self.counts.setMinimumHeight(
            min(len(rep.counts), 15) * row_h + self.counts.horizontalHeader().sizeHint().height() + 4
        )
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(sub)
        lay.addSpacing(4)
        lay.addWidget(self.counts, 1)

        self.missing = QLabel()
        self.missing.setWordWrap(True)
        if rep.missing:
            self.missing.setText(
                "<b>Not in this folder</b> (skipped; normal if those features were never used): "
                + ", ".join(rep.missing)
            )
            lay.addWidget(self.missing)

        self.warnings = QListWidget()
        self.warnings.setWordWrap(True)
        self.warnings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.warnings.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.warnings.addItems(rep.warnings)
        self.warnings.setMaximumHeight((self.fontMetrics().height() + 6) * min(8, 2 * len(rep.warnings)) + 8)
        if rep.warnings:
            lab = QLabel(
                f"<b>{len(rep.warnings)} warning{'s' if len(rep.warnings) != 1 else ''}</b> — "
                "the data was read as far as possible; nothing in the folder was changed."
            )
            lab.setWordWrap(True)
            lay.addWidget(lab)
            lay.addWidget(self.warnings)

        buttons = QDialogButtonBox()
        self.open_btn = buttons.addButton("Open route", QDialogButtonBox.ButtonRole.AcceptRole)
        self.open_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
