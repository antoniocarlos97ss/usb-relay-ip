from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from client.core import config_manager
from shared.constants import DEFAULT_API_PORT, POLL_INTERVAL_CLIENT_DEFAULT
from shared.i18n import t


class ClientSettingsDialog(QWidget):
    settings_applied = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.client_title"))
        self.setMinimumWidth(420)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        conn_group = QGroupBox(t("settings.host_connection"))
        conn_layout = QFormLayout()

        self._host_ip_input = QLineEdit()
        self._host_ip_input.setPlaceholderText(t("settings.host_ip_placeholder"))
        conn_layout.addRow(t("settings.host_ip"), self._host_ip_input)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(DEFAULT_API_PORT)
        conn_layout.addRow(t("settings.host_port"), self._port_spin)

        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText(t("settings.api_key_placeholder"))
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)

        key_layout = QHBoxLayout()
        key_layout.addWidget(self._api_key_input)

        self._show_key_btn = QPushButton(t("btn.show"))
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_layout.addWidget(self._show_key_btn)

        conn_layout.addRow(t("settings.api_key"), key_layout)
        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)

        monitor_group = QGroupBox(t("settings.polling"))
        monitor_layout = QFormLayout()

        self._poll_spin = QSpinBox()
        self._poll_spin.setRange(1, 60)
        self._poll_spin.setValue(POLL_INTERVAL_CLIENT_DEFAULT)
        self._poll_spin.setSuffix(t("settings.poll_suffix"))
        monitor_layout.addRow(t("settings.poll_interval"), self._poll_spin)

        monitor_group.setLayout(monitor_layout)
        layout.addWidget(monitor_group)

        startup_group = QGroupBox(t("settings.startup"))
        startup_layout = QVBoxLayout()

        self._autostart_check = QCheckBox(t("settings.autostart_boot"))
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
        self._host_ip_input.setText(config.host_ip)
        self._port_spin.setValue(config.host_port)
        self._api_key_input.setText(config.api_key)
        self._poll_spin.setValue(config.poll_interval_seconds)
        self._autostart_check.setChecked(config.autostart_with_windows)

    def _apply(self):
        config_manager.update_host_ip(self._host_ip_input.text().strip())
        config_manager.update_host_port(self._port_spin.value())
        config_manager.update_api_key(self._api_key_input.text())
        config_manager.update_poll_interval(self._poll_spin.value())

        autostart_ok = config_manager.update_autostart(self._autostart_check.isChecked())

        if autostart_ok:
            if self._autostart_check.isChecked():
                QMessageBox.information(self, t("settings.autostart"), t("settings.autostart_enabled"))
            else:
                QMessageBox.information(self, t("settings.autostart"), t("settings.autostart_disabled"))
        else:
            QMessageBox.warning(self, t("settings.autostart"), t("settings.autostart_failed"))

        self.settings_applied.emit()
