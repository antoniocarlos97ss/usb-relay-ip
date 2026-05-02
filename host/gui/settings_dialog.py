from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from host.core import config_manager
from shared.constants import DEFAULT_API_PORT, POLL_INTERVAL_DEFAULT
from shared.i18n import t


class SettingsDialog(QWidget):
    settings_applied = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.host_title"))
        self.setMinimumWidth(420)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        api_group = QGroupBox(t("settings.api_server"))
        api_layout = QFormLayout()

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(DEFAULT_API_PORT)
        api_layout.addRow(t("settings.api_port"), self._port_spin)

        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText(t("settings.api_key_placeholder"))
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        key_layout = QHBoxLayout()
        key_layout.addWidget(self._api_key_input)

        self._show_key_btn = QPushButton(t("btn.show"))
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_layout.addWidget(self._show_key_btn)

        api_layout.addRow(t("settings.api_key"), key_layout)
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        monitor_group = QGroupBox(t("settings.device_monitor"))
        monitor_layout = QFormLayout()

        self._poll_spin = QSpinBox()
        self._poll_spin.setRange(1, 60)
        self._poll_spin.setValue(POLL_INTERVAL_DEFAULT)
        self._poll_spin.setSuffix(t("settings.poll_suffix"))
        monitor_layout.addRow(t("settings.poll_interval"), self._poll_spin)

        monitor_group.setLayout(monitor_layout)
        layout.addWidget(monitor_group)

        startup_group = QGroupBox(t("settings.startup"))
        startup_layout = QVBoxLayout()

        self._autostart_check = QCheckBox(t("settings.autostart_service"))
        startup_layout.addWidget(self._autostart_check)

        startup_group.setLayout(startup_layout)
        layout.addWidget(startup_group)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        apply_btn = QPushButton(t("btn.apply"))
        apply_btn.clicked.connect(self._apply)
        btn_layout.addWidget(apply_btn)

        layout.addLayout(btn_layout)

    def _toggle_key_visibility(self, checked: bool):
        if checked:
            self._api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText(t("btn.hide"))
        else:
            self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText(t("btn.show"))

    def _load_settings(self):
        config = config_manager.load_config()
        self._port_spin.setValue(config.api_port)
        self._api_key_input.setText(config.api_key)
        self._poll_spin.setValue(config.poll_interval_seconds)
        self._autostart_check.setChecked(config.autostart_as_service)

    def _apply(self):
        config_manager.update_api_port(self._port_spin.value())
        config_manager.update_api_key(self._api_key_input.text())
        config_manager.update_poll_interval(self._poll_spin.value())
        logon_ok, boot_ok = config_manager.update_autostart(self._autostart_check.isChecked())

        if self._autostart_check.isChecked():
            lines = []
            lines.append(f"{'✔' if logon_ok else '✘'} {t('autostart_logon_ok') if logon_ok else t('autostart_logon_fail')}")
            lines.append(f"{'✔' if boot_ok else '✘'} {t('autostart_boot_ok') if boot_ok else t('autostart_boot_needs_admin')}")
            QMessageBox.information(self, t("settings.autostart_result_title"), "\n".join(lines))

        self.settings_applied.emit()
