"""Poll one phone now: pick the modem port (or SIM for a simulated phone), watch the steps."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..db import RouteDB
from ..fields import phone_display
from ..poller import PollStore, site_for_poll
from .importdlg import fit_to_contents
from .workers import Worker


def serial_ports() -> list[str]:
    """Serial ports pyserial can see (none when it isn't installed)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]


class PollDialog(QDialog):
    polled = Signal(str)  # phone, after a result was saved

    def __init__(self, db_path: str, phone: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        with RouteDB(db_path) as db:
            self.site = site_for_poll(db, phone)
        self.setWindowTitle(f"Poll {phone_display(phone)}")
        self.worker: Worker | None = None

        with PollStore(db_path) as st:
            port, baud = st.setting("port", "SIM"), st.setting("baud", "9600")
        self.port = QComboBox()
        self.port.setEditable(True)
        for p in dict.fromkeys([port, "SIM", *serial_ports()]):
            self.port.addItem(p)
        self.port.setCurrentText(port)
        self.port.setToolTip("The modem's serial port (e.g. /dev/ttyUSB0 or COM1); SIM polls a simulated phone")
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(["9600", "2400", "1200", "19200"])
        self.baud.setCurrentText(baud)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(self.fontMetrics().height() * 10)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.go = QPushButton("Poll")
        self.go.setDefault(True)
        self.close_btn = QPushButton("Close")
        self.go.clicked.connect(self.start)
        self.close_btn.clicked.connect(self.reject)

        form = QFormLayout()
        desc = (self.site or {}).get("description") or "(no site record: can't be polled)"
        form.addRow("Site:", QLabel(f"{phone_display(phone)}  {desc}"))
        form.addRow("Modem port:", self.port)
        form.addRow("PC-to-modem speed:", self.baud)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.go)
        buttons.addWidget(self.close_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.log, 1)
        lay.addWidget(self.result)
        lay.addLayout(buttons)
        self.go.setEnabled(self.site is not None)
        fit_to_contents(self, 520)

    def start(self) -> None:
        port, baud = self.port.currentText().strip() or "SIM", self.baud.currentText().strip()
        if not baud.isdigit():
            self.result.setText("The speed must be a number, e.g. 9600.")
            return
        with PollStore(self.db_path) as st:
            st.set_setting("port", port)
            st.set_setting("baud", baud)
        self.log.clear()
        self.result.setText("Polling…")
        self.go.setEnabled(False)
        self.close_btn.setEnabled(False)
        site, path = self.site, self.db_path

        def job(progress):
            from ..modem import UPMS1200, Modem, open_line
            from ..poller import Poller, simulated_phone
            from ..simphone import SimModemLine

            if port.upper() == "SIM":
                line = SimModemLine(simulated_phone(path, site))
                progress("(simulated phone: nothing is dialled)", 0)
            else:
                line = open_line(port, int(baud))
            try:
                out = Poller(Modem(line, UPMS1200), progress=lambda s: progress(s, 0)).poll(site)
            finally:
                if hasattr(line, "close"):
                    line.close()
            with PollStore(path) as st:
                st.record(site, out)
            return out

        # connect to this dialog's own methods (not lambdas) so Qt runs them on the GUI thread
        self.worker = Worker(job)
        self.worker.progress.connect(self._step)
        self.worker.finished.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _step(self, step: str, _n: int) -> None:
        self.log.appendPlainText(step)

    def _failed(self, msg: str, _tb: str) -> None:
        self._stopped(msg)

    def _done(self, out) -> None:
        st = out.status
        if out.ok and st:
            self.result.setText(f"Poll complete and saved. Coin box {st.percent_full or 0}% full.")
        else:
            self.result.setText(f"Poll failed: {out.message} (code {out.code}). Saved as a failed call.")
        self._finish()
        self.polled.emit(self.site["phone"])

    def _stopped(self, msg: str) -> None:
        self.result.setText(f"Poll stopped: {msg}")
        self._finish()

    def _finish(self) -> None:
        self.go.setEnabled(True)
        self.close_btn.setEnabled(True)

    def reject(self) -> None:
        if self.worker and self.worker.running():
            return  # a call in progress finishes first (it hangs up by itself)
        super().reject()
