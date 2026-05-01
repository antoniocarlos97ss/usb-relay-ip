import logging

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QStatusBar,
    QTabWidget, QVBoxLayout, QWidget,
)

from client.api.host_client import HostApiClient
from client.core import config_manager, device_poller, usbip_worker, usbip_wrapper
from client.gui.device_table import ClientDeviceTable
from client.gui.log_viewer import LogViewer
from client.gui.settings_dialog import ClientSettingsDialog
from client.gui.tray import ClientTrayIcon
from shared.i18n import t

logger = logging.getLogger(__name__)


class ClientSettingsTab(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dialog = ClientSettingsDialog(self)
        self._dialog.settings_applied.connect(self._on_settings_applied)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._dialog)

    def _on_settings_applied(self):
        self.settings_changed.emit()


class ClientMainWindow(QMainWindow):
    def __init__(self, tray_icon: ClientTrayIcon):
        super().__init__()
        self._tray = tray_icon
        self._port_map: dict[str, int] = {}

        config = config_manager.load_config()
        self._api_client = HostApiClient(
            host_ip=config.host_ip,
            host_port=config.host_port,
            api_key=config.api_key,
        )

        self.setWindowTitle(t("client.title"))
        self.setMinimumSize(700, 450)
        self._workers: list = []
        self._setup_ui()
        self._start_auto_attach()
        self._start_polling()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton(t("btn.refresh"))
        self._refresh_btn.clicked.connect(self._poller_refresh)
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

        self._settings_tab = ClientSettingsTab(self)
        self._settings_tab.settings_changed.connect(self._on_settings_changed)
        tabs.addTab(self._settings_tab, t("tab.settings"))
        main_layout.addWidget(tabs)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_label = QLabel(t("status.connecting"))
        self._status_bar.addWidget(self._status_label)

    def _on_settings_changed(self):
        config = config_manager.load_config()
        self._api_client.host_ip = config.host_ip
        self._api_client.host_port = config.host_port
        self._api_client.api_key = config.api_key
        logger.info(f"Settings updated, reconnecting to {config.host_ip}:{config.host_port}")
        self._restart_poller()

    def _poller_refresh(self):
        if hasattr(self, "_poller") and self._poller:
            self._poller.refresh_now()

    def _start_polling(self):
        config = config_manager.load_config()
        self._poller = device_poller.DevicePoller(
            self._api_client,
            poll_interval=config.poll_interval_seconds,
        )
        self._poller.devices_fetched.connect(self._on_devices_fetched)
        self._poller.connection_changed.connect(self._on_connection_changed)
        self._poller.start()

    def _restart_poller(self):
        if hasattr(self, "_poller") and self._poller:
            self._poller.stop()
        self._start_polling()

    def _on_devices_fetched(self, devices):
        for device in devices:
            device.is_permanent = config_manager.is_permanent(device.vid, device.pid)
        self._device_table.update_devices(devices)

    def _on_connection_changed(self, connected: bool, host: str):
        if connected:
            config = config_manager.load_config()
            self._status_label.setText(t("status.connected", host=config.host_ip, port=config.host_port))
            self._tray.set_connected_state(True, config.host_ip)
        else:
            self._status_label.setText(t("status.offline_retry"))
            self._tray.set_connected_state(False)

    def _attach_device(self, busid: str):
        config = config_manager.load_config()
        logger.info(f"Attaching device {busid} from {config.host_ip}")
        worker = usbip_worker.AttachWorker(config.host_ip, busid)
        worker.finished.connect(self._on_attach_finished)
        worker.finished.connect(worker.deleteLater)
        worker.destroyed.connect(lambda obj=None, w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()

    def _cleanup_worker(self, worker):
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def _on_attach_finished(self, success: bool, message: str, busid: str, port: int = 0):
        if success:
            if port:
                self._port_map[busid] = port
            logger.info(f"Device {busid} attached successfully.")
            self._tray.show_notification("USBRelay", t("notify.attached", busid=busid))
        else:
            logger.error(f"Attach failed for {busid}: {message}")
            self._tray.show_notification("USBRelay", t("notify.attach_failed", busid=busid, msg=message))
            self._retry_attach_stale(busid)
        self._poller_refresh()

    def _retry_attach_stale(self, busid: str):
        logger.info(f"Trying to recover stale device {busid} via host unbind+rebind")
        self._api_client.unbind_device(busid)
        import time
        time.sleep(2)
        self._api_client.bind_device(busid)
        time.sleep(2)
        config = config_manager.load_config()
        worker = usbip_worker.AttachWorker(config.host_ip, busid)
        worker.finished.connect(self._on_attach_finished)
        worker.finished.connect(worker.deleteLater)
        worker.destroyed.connect(lambda obj=None, w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()

    def _detach_device(self, busid: str):
        logger.info(f"Detaching device {busid}")
        port = self._port_map.get(busid)
        worker = usbip_worker.DetachWorker(busid, port=port)
        worker.finished.connect(self._on_detach_finished)
        worker.finished.connect(worker.deleteLater)
        worker.destroyed.connect(lambda obj=None, w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()

    def _on_detach_finished(self, success: bool, message: str, busid: str):
        if success:
            logger.info(f"Device {busid} detached.")
            self._tray.show_notification("USBRelay", t("notify.detached", busid=busid))
        else:
            logger.error(f"Detach failed for {busid}: {message}")
            self._tray.show_notification("USBRelay", t("notify.detach_failed", busid=busid, msg=message))
        self._poller_refresh()

    def _toggle_permanent(self, busid: str, make_permanent: bool):
        device = self._find_device_in_cache(busid)
        if not device:
            return

        if make_permanent:
            config_manager.add_permanent_device(device.vid, device.pid, device.description)
            self._tray.show_notification("USBRelay", t("notify.marked_perm_client", busid=busid))
        else:
            config_manager.remove_permanent_device(device.vid, device.pid)
            self._tray.show_notification("USBRelay", t("notify.unmarked_perm_client", busid=busid))
        self._poller_refresh()

    def _find_device_in_cache(self, busid: str):
        for dev in self._device_table._devices:
            if dev.busid == busid:
                return dev
        return None

    def _on_attach_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            logger.info(f"Attach button clicked for busid={busid}")
            self._attach_device(busid)
        else:
            logger.warning("Attach button clicked but no device selected")

    def _on_detach_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            logger.info(f"Detach button clicked for busid={busid}")
            self._detach_device(busid)
        else:
            logger.warning("Detach button clicked but no device selected")

    def _on_always_attach_clicked(self):
        busid = self._device_table.get_selected_busid()
        if busid:
            device = self._find_device_in_cache(busid)
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

        matched = None
        for d in self._device_table._devices:
            if d.vid.lower() == vid.lower() and d.pid.lower() == pid.lower():
                matched = d
                break

        if matched and matched.state == "Shared":
            desc = matched.description
            busid = matched.busid
            worker = usbip_worker.AttachWorker(host_ip, busid)
            worker.finished.connect(
                lambda success, msg, b=busid, port=0, d=desc: self._on_auto_attach_finished(success, b, d, port),
            )
            worker.finished.connect(worker.deleteLater)
            worker.destroyed.connect(lambda obj=None, w=worker: self._cleanup_worker(w))
            self._workers.append(worker)
            worker.start()
            self._poller_refresh()
            return

        if matched and matched.state == "Attached":
            return

        QTimer.singleShot(delay_ms, lambda: self._retry_attach(vid, pid, host_ip, attempts + 1))

    def _on_auto_attach_finished(self, success: bool, busid: str, description: str, port: int = 0):
        if success:
            if port:
                self._port_map[busid] = port
            self._tray.show_notification(
                "USBRelay", t("notify.auto_attached", busid=busid, desc=description),
            )
        self._poller_refresh()

    def closeEvent(self, event: QCloseEvent):
        event.ignore()
        self.hide()
        self._tray.show_notification("USBRelay", t("notify.tray_client"))

    def _detach_all_devices(self):
        if not self._port_map:
            return
        logger.info(f"Detaching {len(self._port_map)} devices before quit")
        from client.core import usbip_wrapper
        import time
        for busid, port in list(self._port_map.items()):
            logger.info(f"Detaching {busid} (port {port})")
            result = usbip_wrapper.detach_device(port)
            if result.success:
                logger.info(f"Detached {busid}")
                self._tray.show_notification("USBRelay", t("notify.detached", busid=busid))
            else:
                logger.warning(f"Detach failed for {busid}: {result.message}")
            time.sleep(0.5)
        self._port_map.clear()

    def quit_app(self):
        if hasattr(self, "_poller") and self._poller:
            self._poller.devices_fetched.disconnect()
            self._poller.connection_changed.disconnect()
            self._poller.stop()
            self._poller = None

    def quit_app_with_detach(self):
        logger.info("Quitting app with detach")
        self._detach_all_devices()
        self.quit_app()
