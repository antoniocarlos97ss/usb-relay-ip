import logging
from datetime import datetime

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from shared.i18n import t


class QTextEditLogger(logging.Handler):
    def __init__(self, widget: QPlainTextEdit):
        super().__init__(level=logging.DEBUG)
        self._widget = widget
        self._min_level = logging.DEBUG

    def set_level(self, level: int):
        self._min_level = level

    def emit(self, record: logging.LogRecord):
        if record.levelno < self._min_level:
            return
        msg = self.format(record)
        self._widget.appendPlainText(msg)


class LogViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._setup_log_handler()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(t("log.level")))

        self._level_combo = QComboBox()
        self._level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._level_combo.setCurrentText("DEBUG")
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        toolbar.addWidget(self._level_combo)

        toolbar.addStretch()

        clear_btn = QPushButton(t("btn.clear"))
        clear_btn.clicked.connect(self._clear_log)
        toolbar.addWidget(clear_btn)

        export_btn = QPushButton(t("btn.export_log"))
        export_btn.clicked.connect(self._export_log)
        toolbar.addWidget(export_btn)

        layout.addLayout(toolbar)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        font = QFont("Consolas", 9)
        self._log_view.setFont(font)
        self._log_view.setMaximumBlockCount(5000)
        layout.addWidget(self._log_view)

    def _setup_log_handler(self):
        self._handler = QTextEditLogger(self._log_view)
        self._handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        self._handler.setFormatter(formatter)
        logging.getLogger().addHandler(self._handler)

    def _on_level_changed(self, level_str: str):
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        self._handler.set_level(level_map.get(level_str, logging.DEBUG))

    def _clear_log(self):
        self._log_view.clear()

    def _export_log(self):
        from PyQt6.QtWidgets import QFileDialog

        default_name = f"usbrelay_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        path, _ = QFileDialog.getSaveFileName(
            self, t("log.export_title"), default_name, t("log.export_filter"),
        )
        if path:
            try:
                text = self._log_view.toPlainText()
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as exc:
                logging.getLogger(__name__).error(f"Failed to export log: {exc}")

    def closeEvent(self, event):
        if hasattr(self, "_handler"):
            logging.getLogger().removeHandler(self._handler)
        super().closeEvent(event)
