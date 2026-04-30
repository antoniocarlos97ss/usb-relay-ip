import logging
import os

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QStatusBar,
    QTabWidget, QVBoxLayout, QWidget,
)

from host.core import config_manager, device_monitor, usbipd_wrapper
from host.gui.device_table import DeviceTable
from host.gui.log_viewer import LogViewer
from host.gui.settings_dialog import SettingsDialog
from host.gui.tray import TrayIcon
from shared.i18n import t

logger = logging.getLogger(__name__)


class HostMainWindow(QMainWindow):
    def __init__(self, tray_icon: TrayIcon):
        super().__init__()
        self._tray = tray_icon
        self._monitor: device_monitor.DeviceMonitor | None = None

        self.setWindowTitle(t("host.title"))
        self.setMinimumSize(700, 450)
        self._setup_ui()
        self._start_monitor()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton(t("btn.refresh"))
        self._refresh_btn.clicked.connect(self._refresh_devices)
        toolbar.addStretch()
        toolbar.addWidget(self._refresh_btn)
        main_layout.addLayout(toolbar)

        self._device_table = DeviceTable(is_host=True)
        self._device_table.share_requested.connect(self._share_device)
        self._device_table.unshare_requested.connect(self._unshare_device)
        self._device_table.permanent_toggle.connect(self._toggle_permanent)
        main_layout.addWidget(self._device_table)

        action_layout = QHBoxLayout()
        self._share_btn = QPushButton(t("btn.share_selected"))
        self._share_btn.clicked.connect(self._on_share_clicked)
        action_layout.addWidget(self._share_btn)

        self._unshare_btn = QPushButton(t("btn.unshare_selected"))
        self._unshare_btn.clicked.connect(self._on_unshare_clicked)
        action_layout.addWidget(self._unshare_btn)

        self._always_btn = QPushButton(t("btn.always"))
        self._always_btn.clicked.connect(self._on_always_share_clicked)
        action_layout.addWidget(self._always_btn)
        action_layout.addStretch()
        main_layout.addLayout(action_layout)

        tabs = QTabWidget()
        tabs.addTab(self._device_table, t("tab.devices"))
        tabs.addTab(LogViewer(), t("tab.log"))
        tabs.addTab(SettingsDialog(self), t("tab.settings"))
        main_layout.addWidget(tabs)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_label = QLabel(t("status.api_starting"))
        self._status_bar.addWidget(self._status_label)

    def _start_monitor(self):
        config = config_manager.load_config()
        self._monitor = device_monitor.DeviceMonitor(
            poll_interval=config.poll_interval_seconds,
        )
        self._monitor.devices_changed.connect(self._on_devices_changed)
        self._monitor.device_auto_bound.connect(self._on_device_auto_bound)
        self._monitor.start()

        QTimer.singleShot(500, self._refresh_devices)

    def _refresh_devices(self):
        try:
            devices = usbipd_wrapper.list_devices()
            for device in devices:
                device.is_permanent = config_manager.is_permanent(device.vid, device.pid)
            self._device_table.update_devices(devices)
        except Exception as exc:
            logger.error(f"Failed to refresh devices: {exc}")

    def _share_device(self, busid: str):
        devices = usbipd_wrapper.list_devices()
        device = next((d for d in devices if d.busid == busid), None)
        if device and device.state in ("Shared", "Attached"):
            state_label = t(f"state.{device.state.lower().replace(' ', '_')}", state=device.state)
            self._tray.show_notification("USBRelay", t("notify.already_shared", busid=busid, state=state_label))
            return

        result = usbipd_wrapper.bind_device(busid)
        if result.success:
            self._tray.show_notification("USBRelay", t("notify.shared", busid=busid))
        else:
            self._tray.show_notification("USBRelay", t("notify.share_failed", busid=busid, msg=result.message))
        self._refresh_devices()

    def _unshare_device(self, busid: str):
        result = usbipd_wrapper.unbind_device(busid)
        if result.success:
            self._tray.show_notification("USBRelay", t("notify.unshared", busid=busid))
        else:
            self._tray.show_notification("USBRelay", t("notify.unshare_failed", busid=busid, msg=result.message))
        self._refresh_devices()

    def _toggle_permanent(self, busid: str, make_permanent: bool):
        devices = usbipd_wrapper.list_devices()
        device = next((d for d in devices if d.busid == busid), None)
        if not device:
            return

        if make_permanent:
            config_manager.add_permanent_device(device.vid, device.pid, device.description, device.busid)
            self._tray.show_notification("USBRelay", t("notify.marked_perm", busid=busid))
        else:
            config_manager.remove_permanent_device(device.vid, device.pid)
            self._tray.show_notification("USBRelay", t("notify.unmarked_perm", busid=busid))
        self._refresh_devices()

    def _on_share_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            self._share_device(busid)

    def _on_unshare_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            self._unshare_device(busid)

    def _on_always_share_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            devices = usbipd_wrapper.list_devices()
            device = next((d for d in devices if d.busid == busid), None)
            if device:
                is_perm = config_manager.is_permanent(device.vid, device.pid)
                self._toggle_permanent(busid, not is_perm)

    def _on_devices_changed(self, devices):
        self._device_table.update_devices(devices)

    def _on_device_auto_bound(self, busid: str, description: str):
        self._tray.show_notification("USBRelay", t("notify.auto_bound", busid=busid, desc=description))

    def set_api_status(self, running: bool, port: int):
        if running:
            self._status_label.setText(t("status.api_running", port=port))
            self._tray.set_connected_state(True)
        else:
            self._status_label.setText(t("status.api_stopped"))
            self._tray.set_connected_state(False)

    def closeEvent(self, event: QCloseEvent):
        event.ignore()
        self.hide()
        self._tray.show_notification("USBRelay", t("notify.tray_host"))

    def quit_app(self):
        if self._monitor:
            self._monitor.stop()
        self._tray.hide()
