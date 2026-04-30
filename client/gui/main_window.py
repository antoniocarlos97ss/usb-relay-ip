import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QStatusBar,
    QTabWidget, QVBoxLayout, QWidget,
)

from client.api.host_client import HostApiClient
from client.core import config_manager, usbip_wrapper
from client.gui.device_table import ClientDeviceTable
from client.gui.log_viewer import LogViewer
from client.gui.settings_dialog import ClientSettingsDialog
from client.gui.tray import ClientTrayIcon
from shared.i18n import t

logger = logging.getLogger(__name__)


class ClientMainWindow(QMainWindow):
    def __init__(self, tray_icon: ClientTrayIcon):
        super().__init__()
        self._tray = tray_icon

        config = config_manager.load_config()
        self._api_client = HostApiClient(
            host_ip=config.host_ip,
            host_port=config.host_port,
            api_key=config.api_key,
        )

        self.setWindowTitle(t("client.title"))
        self.setMinimumSize(700, 450)
        self._setup_ui()

        self._start_auto_attach()
        self._start_polling()

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

        self._device_table = ClientDeviceTable()
        self._device_table.attach_requested.connect(self._attach_device)
        self._device_table.detach_requested.connect(self._detach_device)
        self._device_table.permanent_toggle.connect(self._toggle_permanent)
        main_layout.addWidget(self._device_table)

        action_layout = QHBoxLayout()
        self._attach_btn = QPushButton(t("btn.attach_selected"))
        self._attach_btn.clicked.connect(self._on_attach_clicked)
        action_layout.addWidget(self._attach_btn)

        self._detach_btn = QPushButton(t("btn.detach_selected"))
        self._detach_btn.clicked.connect(self._on_detach_clicked)
        action_layout.addWidget(self._detach_btn)

        self._always_btn = QPushButton(t("btn.always"))
        self._always_btn.clicked.connect(self._on_always_attach_clicked)
        action_layout.addWidget(self._always_btn)
        action_layout.addStretch()
        main_layout.addLayout(action_layout)

        tabs = QTabWidget()
        tabs.addTab(self._device_table, t("tab.devices"))
        tabs.addTab(LogViewer(), t("tab.log"))
        tabs.addTab(ClientSettingsDialog(self), t("tab.settings"))
        main_layout.addWidget(tabs)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_label = QLabel(t("status.connecting"))
        self._status_bar.addWidget(self._status_label)

    def _start_polling(self):
        self._poll_timer = QTimer(self)
        config = config_manager.load_config()
        interval = config.poll_interval_seconds * 1000
        self._poll_timer.timeout.connect(self._refresh_devices)
        self._poll_timer.start(interval)

    def _refresh_devices(self):
        try:
            devices = self._api_client.get_devices()
            for device in devices:
                device.is_permanent = config_manager.is_permanent(device.vid, device.pid)
            self._device_table.update_devices(devices)

            if self._api_client.is_connected():
                config = config_manager.load_config()
                self._status_label.setText(t("status.connected", host=config.host_ip, port=config.host_port))
                self._tray.set_connected_state(True, config.host_ip)
            else:
                self._status_label.setText(t("status.offline_retry"))
                self._tray.set_connected_state(False)
        except Exception as exc:
            logger.error(f"Failed to refresh devices: {exc}")
            self._status_label.setText(t("status.offline"))
            self._tray.set_connected_state(False)

    def _attach_device(self, busid: str):
        config = config_manager.load_config()
        result = usbip_wrapper.attach_device(config.host_ip, busid)
        if result.success:
            self._tray.show_notification("USBRelay", t("notify.attached", busid=busid))
        else:
            self._tray.show_notification("USBRelay", t("notify.attach_failed", busid=busid, msg=result.message))
        self._refresh_devices()

    def _detach_device(self, busid: str):
        port = usbip_wrapper.find_port_for_busid(busid)
        if port is None:
            self._tray.show_notification("USBRelay", t("notify.no_port", busid=busid))
            return
        result = usbip_wrapper.detach_device(port)
        if result.success:
            self._tray.show_notification("USBRelay", t("notify.detached", busid=busid))
        else:
            self._tray.show_notification("USBRelay", t("notify.detach_failed", busid=busid, msg=result.message))
        self._refresh_devices()

    def _toggle_permanent(self, busid: str, make_permanent: bool):
        devices = self._api_client.get_devices()
        device = next((d for d in devices if d.busid == busid), None)
        if not device:
            return

        if make_permanent:
            config_manager.add_permanent_device(device.vid, device.pid, device.description)
            self._tray.show_notification("USBRelay", t("notify.marked_perm_client", busid=busid))
        else:
            config_manager.remove_permanent_device(device.vid, device.pid)
            self._tray.show_notification("USBRelay", t("notify.unmarked_perm_client", busid=busid))
        self._refresh_devices()

    def _on_attach_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            self._attach_device(busid)

    def _on_detach_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            self._detach_device(busid)

    def _on_always_attach_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            devices = self._api_client.get_devices()
            device = next((d for d in devices if d.busid == busid), None)
            if device:
                is_perm = config_manager.is_permanent(device.vid, device.pid)
                self._toggle_permanent(busid, not is_perm)

    def _start_auto_attach(self):
        config = config_manager.load_config()
        if not config.permanent_devices or not config.host_ip:
            return
        QTimer.singleShot(2000, self._auto_attach_permanent)

    def _auto_attach_permanent(self):
        config = config_manager.load_config()
        host_ip = config.host_ip
        if not host_ip or not config.permanent_devices:
            return

        self._api_client.host_ip = host_ip
        self._api_client.host_port = config.host_port
        self._api_client.api_key = config.api_key

        for perm_device in config.permanent_devices:
            self._tray.show_notification(
                "USBRelay", t("notify.auto_attaching", vid=perm_device.vid, pid=perm_device.pid),
            )
            self._retry_attach(perm_device.vid, perm_device.pid, host_ip, attempts=0)

    def _retry_attach(self, vid: str, pid: str, host_ip: str, attempts: int = 0):
        max_attempts = 10
        delay_ms = 3000

        if attempts >= max_attempts:
            logger.warning(f"Auto-attach timed out for {vid}:{pid}")
            self._tray.show_notification(
                "USBRelay", t("notify.auto_attach_failed", vid=vid, pid=pid),
            )
            return

        devices = self._api_client.get_devices()
        matched = None
        for d in devices:
            if d.vid.lower() == vid.lower() and d.pid.lower() == pid.lower():
                matched = d
                break

        if matched and matched.state == "Shared":
            result = usbip_wrapper.attach_device(host_ip, matched.busid)
            if result.success:
                self._tray.show_notification(
                    "USBRelay", t("notify.auto_attached", busid=matched.busid, desc=matched.description),
                )
                self._refresh_devices()
                return

        if matched and matched.state == "Attached":
            return

        QTimer.singleShot(delay_ms, lambda: self._retry_attach(vid, pid, host_ip, attempts + 1))

    def closeEvent(self, event: QCloseEvent):
        event.ignore()
        self.hide()
        self._tray.show_notification("USBRelay", t("notify.tray_client"))

    def quit_app(self):
        if hasattr(self, "_poll_timer"):
            self._poll_timer.stop()
        self._tray.hide()
