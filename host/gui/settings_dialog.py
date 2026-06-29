import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from host.core import config_manager
from shared.constants import DEFAULT_API_PORT, POLL_INTERVAL_DEFAULT
from shared.i18n import t

VID_PID_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$")


class SettingsDialog(QWidget):
    settings_applied = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.host_title"))
        self.setMinimumWidth(480)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # --- API Server ---
        api_group = QGroupBox(t("settings.api_server"))
        api_layout = QFormLayout()

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(DEFAULT_API_PORT)
        api_layout.addRow(t("settings.api_port"), self._port_spin)

        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText(t("settings.api_key_placeholder"))
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.textChanged.connect(self._on_api_key_changed)

        key_layout = QHBoxLayout()
        key_layout.addWidget(self._api_key_input)

        self._show_key_btn = QPushButton(t("btn.show"))
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_layout.addWidget(self._show_key_btn)

        api_layout.addRow(t("settings.api_key"), key_layout)
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        # --- Device Monitor ---
        monitor_group = QGroupBox(t("settings.device_monitor"))
        monitor_layout = QFormLayout()

        self._poll_spin = QSpinBox()
        self._poll_spin.setRange(1, 60)
        self._poll_spin.setValue(POLL_INTERVAL_DEFAULT)
        self._poll_spin.setSuffix(t("settings.poll_suffix"))
        monitor_layout.addRow(t("settings.poll_interval"), self._poll_spin)

        monitor_group.setLayout(monitor_layout)
        layout.addWidget(monitor_group)

        # --- Startup ---
        startup_group = QGroupBox(t("settings.startup"))
        startup_layout = QVBoxLayout()

        self._autostart_check = QCheckBox(t("settings.autostart_service"))
        startup_layout.addWidget(self._autostart_check)

        startup_group.setLayout(startup_layout)
        layout.addWidget(startup_group)

        # --- Automatic Sharing ---
        auto_share_group = QGroupBox(t("settings.auto_share_group"))
        auto_share_layout = QVBoxLayout()

        self._auto_share_check = QCheckBox(t("settings.auto_share_all"))
        self._auto_share_check.toggled.connect(self._on_auto_share_toggled)
        auto_share_layout.addWidget(self._auto_share_check)

        self._auto_share_warning = QLabel(t("settings.auto_share_warning"))
        self._auto_share_warning.setStyleSheet("color: #e67e22; font-weight: bold;")
        self._auto_share_warning.setWordWrap(True)
        self._auto_share_warning.setVisible(False)
        auto_share_layout.addWidget(self._auto_share_warning)

        self._auto_share_key_warning = QLabel(t("settings.auto_share_no_key_warning"))
        self._auto_share_key_warning.setStyleSheet("color: #e74c3c; font-weight: bold;")
        self._auto_share_key_warning.setWordWrap(True)
        self._auto_share_key_warning.setVisible(False)
        auto_share_layout.addWidget(self._auto_share_key_warning)

        excl_label = QLabel(t("settings.auto_share_exclusions"))
        auto_share_layout.addWidget(excl_label)

        self._exclusion_list = QListWidget()
        self._exclusion_list.setMaximumHeight(100)
        auto_share_layout.addWidget(self._exclusion_list)

        excl_btn_layout = QHBoxLayout()
        add_excl_btn = QPushButton(t("settings.auto_share_add_btn"))
        add_excl_btn.clicked.connect(self._add_exclusion)
        excl_btn_layout.addWidget(add_excl_btn)

        remove_excl_btn = QPushButton(t("settings.auto_share_remove_btn"))
        remove_excl_btn.clicked.connect(self._remove_exclusion)
        excl_btn_layout.addWidget(remove_excl_btn)

        auto_share_layout.addLayout(excl_btn_layout)
        auto_share_group.setLayout(auto_share_layout)
        layout.addWidget(auto_share_group)

        # --- Apply button ---
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

    def _on_auto_share_toggled(self, checked: bool):
        self._auto_share_warning.setVisible(checked)
        self._update_key_warning()

    def _on_api_key_changed(self, text: str):
        if self._auto_share_check.isChecked():
            self._update_key_warning()

    def _update_key_warning(self):
        show = self._auto_share_check.isChecked() and not self._api_key_input.text()
        self._auto_share_key_warning.setVisible(show)

    def _add_exclusion(self):
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, t("settings.auto_share_add_title"), t("settings.auto_share_add_prompt")
        )
        if not ok or not text.strip():
            return
        text = text.strip().lower()
        if not VID_PID_RE.match(text):
            QMessageBox.warning(self, t("settings.auto_share_add_title"), t("settings.auto_share_invalid_format"))
            return
        # Check if already in list
        for i in range(self._exclusion_list.count()):
            if self._exclusion_list.item(i).text() == text:
                return
        self._exclusion_list.addItem(text)

    def _remove_exclusion(self):
        row = self._exclusion_list.currentRow()
        if row >= 0:
            self._exclusion_list.takeItem(row)

    def _load_settings(self):
        config = config_manager.load_config()
        self._port_spin.setValue(config.api_port)
        self._api_key_input.setText(config.api_key)
        self._poll_spin.setValue(config.poll_interval_seconds)
        self._autostart_check.setChecked(config.autostart_as_service)
        self._auto_share_check.setChecked(config.auto_share_all)
        for excl in config.auto_share_exclude:
            self._exclusion_list.addItem(excl)
        self._on_auto_share_toggled(config.auto_share_all)

    def _apply(self):
        config_manager.update_api_port(self._port_spin.value())
        config_manager.update_api_key(self._api_key_input.text())
        config_manager.update_poll_interval(self._poll_spin.value())
        logon_ok, boot_ok = config_manager.update_autostart(self._autostart_check.isChecked())

        if self._autostart_check.isChecked():
            lines = []
            lines.append(f"{'✔' if logon_ok else '✘'} {t('settings.autostart_logon_ok') if logon_ok else t('settings.autostart_logon_fail')}")
            lines.append(f"{'✔' if boot_ok else '✘'} {t('settings.autostart_boot_ok') if boot_ok else t('settings.autostart_boot_needs_admin')}")
            QMessageBox.information(self, t("settings.autostart_result_title"), "\n".join(lines))

        # Save auto-share settings
        auto_share_enabled = self._auto_share_check.isChecked()
        if auto_share_enabled and not self._api_key_input.text():
            reply = QMessageBox.warning(
                self, t("settings.auto_share_group"),
                t("settings.auto_share_no_key_warning") + "\n\n" + t("dialog.continue"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._auto_share_check.setChecked(False)
                auto_share_enabled = False

        config_manager.update_auto_share_all(auto_share_enabled)

        # Save exclusions: clear all and re-add
        config = config_manager.load_config()
        config.auto_share_exclude = []
        for i in range(self._exclusion_list.count()):
            config.auto_share_exclude.append(self._exclusion_list.item(i).text())
        config_manager.save_config(config)

        self.settings_applied.emit()
