import logging
import time

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QMainWindow, QPushButton, QStatusBar,
    QTabWidget, QVBoxLayout, QWidget,
)

from client.api.host_client import HostApiClient
from client.core import (
    config_manager,
    device_poller,
    operation_coordinator,
    pnp_recovery,
    scheduled_reconnect,
    usbip_worker,
    usbip_wrapper,
)
from client.core.lifecycle import resolve_live_shutdown_sessions
from client.gui.device_table import ClientDeviceTable
from client.gui.log_viewer import LogViewer
from client.gui.settings_dialog import ClientSettingsDialog
from client.gui.tray import ClientTrayIcon
from shared.i18n import t

logger = logging.getLogger(__name__)
TRANSACTION_SHUTDOWN_WAIT_MS = 15000


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
        self._shutting_down = False
        self._service_ok: bool = True  # Optimistic until first health check

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
        self._start_scheduled_reconnect()
        self._start_polling()
        self._start_pnp_recovery()

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
        self._device_table.scheduled_reconnect_requested.connect(self._on_scheduled_reconnect_requested)
        self._device_table.scheduled_reconnect_disable_requested.connect(self._on_scheduled_reconnect_disable_requested)
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
        self._api_client.update_config(config.host_ip, config.host_port, config.api_key)
        if hasattr(self, "_scheduled_reconnect"):
            self._scheduled_reconnect.update_host_config(
                config.host_ip,
                config.host_port,
                config.api_key,
            )
        if hasattr(self, "_pnp_recovery"):
            self._pnp_recovery.update_host_config(
                config.host_ip,
                config.host_port,
                config.api_key,
            )
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
        self._poller.service_status_changed.connect(self._on_service_status_changed)
        self._poller.start()

    def _restart_poller(self):
        if hasattr(self, "_poller") and self._poller:
            self._poller.stop()
        self._start_polling()

    def _on_devices_fetched(self, devices):
        config = config_manager.load_config()
        scheduled = {
            (dev.vid, dev.pid)
            for dev in config.permanent_devices
            if dev.scheduled_reconnect_enabled
        }
        self._device_table.set_scheduled_reconnect_devices(scheduled)
        if hasattr(self, "_scheduled_reconnect"):
            self._scheduled_reconnect.update_devices(devices)
        if hasattr(self, "_pnp_recovery"):
            self._pnp_recovery.update_devices(devices)
        for device in devices:
            device.is_permanent = config_manager.is_permanent(device.vid, device.pid)
        self._device_table.update_devices(devices)

    def _on_connection_changed(self, connected: bool, host: str):
        if connected:
            config = config_manager.load_config()
            if self._service_ok:
                self._status_label.setText(t("status.connected", host=config.host_ip, port=config.host_port))
                self._tray.set_connected_state(True, config.host_ip)
            else:
                self._status_label.setText(t("status.host_service_down"))
                self._tray.set_connected_state(False)
        else:
            self._status_label.setText(t("status.offline_retry"))
            self._tray.set_connected_state(False)
        if hasattr(self, "_scheduled_reconnect"):
            self._scheduled_reconnect.update_connection_state(connected)

    def _on_service_status_changed(self, service_ok: bool):
        old = self._service_ok
        self._service_ok = service_ok

        if old is None:
            logger.info(f"Host usbipd service initial state: {service_ok}")
        elif old != service_ok:
            logger.info(f"Host usbipd service status changed: {old} -> {service_ok}")

        if service_ok:
            self._attach_btn.setEnabled(True)
            self._always_btn.setEnabled(True)
            if old is False:
                self._tray.show_notification("USBRelay", t("notify.host_service_recovered"))
        else:
            self._attach_btn.setEnabled(False)
            self._always_btn.setEnabled(False)
            if old is not False:
                self._tray.show_notification("USBRelay", t("notify.host_service_down"))

        # Refresh status bar
        connected = self._api_client.is_connected()
        if connected:
            config = config_manager.load_config()
            if service_ok:
                self._status_label.setText(t("status.connected", host=config.host_ip, port=config.host_port))
            else:
                self._status_label.setText(t("status.host_service_down"))
        if hasattr(self, "_scheduled_reconnect"):
            self._scheduled_reconnect.update_service_state(service_ok)

    def _attach_device(self, busid: str):
        if not self._service_ok:
            logger.warning(f"Cannot attach {busid}: host usbipd service is down")
            self._tray.show_notification("USBRelay", t("notify.attach_failed_service_down", busid=busid))
            return
        device = self._find_device_in_cache(busid)
        if not device:
            logger.warning("Cannot attach %s: device identity unavailable", busid)
            return
        if not operation_coordinator.try_acquire(device.vid, device.pid, busid):
            logger.warning(f"Cannot attach {busid}: another reconnect operation is running")
            return
        config = config_manager.load_config()
        logger.info(f"Attaching device {busid} from {config.host_ip}")
        worker = usbip_worker.AttachWorker(
            config.host_ip,
            busid,
            vid=device.vid,
            pid=device.pid,
        )
        worker.finished.connect(
            lambda *args, vid=device.vid, pid=device.pid, b=busid: operation_coordinator.release(vid, pid, b)
        )
        # Release the inter-process operation lock before the completion
        # callback can schedule stale-session recovery.
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
        device = self._find_device_in_cache(busid)
        if not device:
            logger.warning(f"Cannot recover stale device {busid}: device identity unavailable")
            return

        logger.info("Starting verified host/client recovery for stale device %s", busid)
        worker = scheduled_reconnect.ScheduledReconnectWorker(
            self._api_client,
            device,
            self,
            record_completion=False,
        )
        worker.result.connect(self._on_stale_reconnect_finished)
        worker.result.connect(worker.deleteLater)
        worker.destroyed.connect(lambda obj=None, w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()

    def _on_stale_reconnect_finished(self, success: bool, busid: str, message: str):
        if success:
            logger.info("Verified host/client recovery succeeded for %s", busid)
            self._tray.show_notification("USBRelay", t("notify.attached", busid=busid))
        else:
            logger.error("Verified host/client recovery failed for %s: %s", busid, message)
            self._tray.show_notification(
                "USBRelay",
                t("notify.attach_failed", busid=busid, msg=message),
            )
        self._poller_refresh()

    def _detach_device(self, busid: str):
        device = self._find_device_in_cache(busid)
        if not device:
            logger.warning(f"Cannot detach {busid}: device identity unavailable")
            return
        if not operation_coordinator.try_acquire(device.vid, device.pid, busid):
            logger.warning(f"Cannot detach {busid}: another reconnect operation is running")
            return
        logger.info(f"Detaching device {busid}")
        # Always resolve the current port: a reattach may allocate a new one.
        worker = usbip_worker.DetachWorker(
            busid,
            port=None,
            expected_vid=device.vid,
            expected_pid=device.pid,
        )
        worker.finished.connect(self._on_detach_finished)
        worker.finished.connect(
            lambda *args, vid=device.vid, pid=device.pid, b=busid: operation_coordinator.release(vid, pid, b)
        )
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

    def _find_client_permanent_device(self, vid: str, pid: str):
        config = config_manager.load_config()
        for perm in config.permanent_devices:
            if perm.vid.lower() == vid.lower() and perm.pid.lower() == pid.lower():
                return perm
        return None

    def _on_scheduled_reconnect_requested(self, busid: str):
        device = self._find_device_in_cache(busid)
        if not device:
            return

        perm_device = self._find_client_permanent_device(device.vid, device.pid)
        default_hours = 24
        if perm_device:
            default_hours = max(1, min(168, perm_device.scheduled_reconnect_interval_hours or 24))

        hours, accepted = QInputDialog.getInt(
            self,
            t("dialog.scheduled_reconnect_title"),
            t("dialog.scheduled_reconnect_prompt"),
            default_hours,
            1,
            168,
        )
        if not accepted:
            return

        was_enabled = bool(perm_device and perm_device.scheduled_reconnect_enabled)
        if perm_device:
            config_manager.update_scheduled_reconnect(device.vid, device.pid, hours, device.description)
        else:
            config_manager.enable_scheduled_reconnect(device.vid, device.pid, hours, device.description)

        if was_enabled:
            message = t("notify.scheduled_reconnect_updated", busid=busid, hours=hours)
        else:
            message = t("notify.scheduled_reconnect_enabled", busid=busid, hours=hours)
        self._tray.show_notification("USBRelay", message)
        self._refresh_scheduled_reconnect_cache()
        self._poller_refresh()

    def _on_scheduled_reconnect_disable_requested(self, busid: str):
        device = self._find_device_in_cache(busid)
        if not device:
            return

        perm_device = self._find_client_permanent_device(device.vid, device.pid)
        if not perm_device or not perm_device.scheduled_reconnect_enabled:
            return

        config_manager.disable_scheduled_reconnect(device.vid, device.pid)
        self._tray.show_notification("USBRelay", t("notify.scheduled_reconnect_disabled", busid=busid))
        self._refresh_scheduled_reconnect_cache()
        self._poller_refresh()

    def _start_scheduled_reconnect(self):
        self._scheduled_reconnect = scheduled_reconnect.ScheduledReconnectController(
            self._api_client,
            self,
        )
        self._scheduled_reconnect.reconnect_failed.connect(self._on_scheduled_reconnect_failed)
        self._scheduled_reconnect.update_host_config(
            self._api_client.host_ip,
            self._api_client.host_port,
            self._api_client.api_key,
        )
        self._scheduled_reconnect.update_connection_state(False)
        self._scheduled_reconnect.update_service_state(False)
        self._scheduled_reconnect.start()

    def _on_scheduled_reconnect_failed(self, busid: str, message: str):
        logger.warning(f"Scheduled reconnect failed for {busid}: {message}")
        self._tray.show_notification("USBRelay", t("notify.scheduled_reconnect_failed", busid=busid, msg=message))

    def _start_pnp_recovery(self):
        self._pnp_recovery = pnp_recovery.PnpRecoveryMonitor(self._api_client, self)
        self._pnp_recovery.recovery_succeeded.connect(self._on_pnp_recovery_succeeded)
        self._pnp_recovery.recovery_failed.connect(self._on_pnp_recovery_failed)
        self._pnp_recovery.start()

    def _on_pnp_recovery_succeeded(self, busid: str, message: str):
        logger.info(f"Automatic PnP recovery succeeded for {busid}: {message}")
        self._poller_refresh()

    def _on_pnp_recovery_failed(self, busid: str, message: str):
        logger.error(f"Automatic PnP recovery failed for {busid}: {message}")
        self._tray.show_notification(
            "USBRelay",
            t("notify.pnp_recovery_failed", busid=busid, msg=message),
        )
        self._poller_refresh()

    def _refresh_scheduled_reconnect_cache(self):
        config = config_manager.load_config()
        self._device_table.set_scheduled_reconnect_devices({
            (dev.vid, dev.pid)
            for dev in config.permanent_devices
            if dev.scheduled_reconnect_enabled
        })

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

        self._api_client.update_config(host_ip, config.host_port, config.api_key)

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

        matched = scheduled_reconnect.find_unique_identity_match(
            self._device_table._devices,
            vid,
            pid,
        )

        if matched and matched.state == "Shared":
            desc = matched.description
            busid = matched.busid
            if not operation_coordinator.try_acquire(matched.vid, matched.pid, busid):
                QTimer.singleShot(delay_ms, lambda: self._retry_attach(vid, pid, host_ip, attempts + 1))
                return
            worker = usbip_worker.AttachWorker(
                host_ip,
                busid,
                vid=matched.vid,
                pid=matched.pid,
            )
            worker.finished.connect(
                lambda success, msg, b=busid, port=0, d=desc: self._on_auto_attach_finished(success, b, d, port),
            )
            worker.finished.connect(worker.deleteLater)
            worker.finished.connect(
                lambda *args, v=matched.vid, p=matched.pid, b=busid: operation_coordinator.release(v, p, b)
            )
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

    def _resolve_live_shutdown_sessions(
        self,
        local_timeout: float = 2.0,
        host_timeout: float = 1.0,
    ):
        """Return only live sessions whose identity is safe to mutate."""
        local_query = usbip_wrapper.query_attached_devices(
            timeout=max(0.1, local_timeout)
        )
        if not local_query.success:
            logger.warning(
                "Cannot enumerate live USB/IP sessions during shutdown: %s",
                local_query.error,
            )
            return []

        host_devices = []
        host_reachable = False
        try:
            host_devices = self._api_client.get_devices(timeout=max(0.1, host_timeout))
            host_reachable = self._api_client.is_connected()
        except Exception:
            logger.warning(
                "Host is unreachable during shutdown identity validation; "
                "using unique cache identities only",
                exc_info=True,
            )

        cached_devices = list(getattr(self._device_table, "_devices", []) or [])
        sessions, rejected = resolve_live_shutdown_sessions(
            local_query.devices,
            host_devices=host_devices,
            cached_devices=cached_devices,
            host_reachable=host_reachable,
        )
        for reason in rejected:
            logger.warning("Refusing shutdown detach: %s", reason)
        return sessions

    def detach_all_async(
        self,
        local_timeout: float = 2.0,
        host_timeout: float = 1.0,
    ):
        """Start identity-checked detach workers for all live local sessions."""
        sessions = self._resolve_live_shutdown_sessions(
            local_timeout=local_timeout,
            host_timeout=host_timeout,
        )
        if not sessions:
            self._port_map.clear()
            return
        logger.info("Async detaching %s verified live session(s)", len(sessions))
        usbip_worker.set_shutting_down()
        self._port_map.clear()
        for session in sessions:
            if not operation_coordinator.try_acquire(
                session.vid, session.pid, session.busid
            ):
                logger.warning(
                    "Cannot detach %s during shutdown: operation lock is contended",
                    session.busid,
                )
                continue
            worker = usbip_worker.DetachWorker(
                session.busid,
                port=session.port,
                expected_vid=session.vid,
                expected_pid=session.pid,
                timeout=max(1, int(local_timeout)),
            )
            worker.finished.connect(
                lambda success, msg, b=session.busid: logger.info(
                    f"Shutdown detach {b}: {'OK' if success else 'FAIL: ' + msg}"
                )
            )
            worker.finished.connect(
                lambda *args, v=session.vid, p=session.pid, b=session.busid: operation_coordinator.release(v, p, b)
            )
            worker.finished.connect(worker.deleteLater)
            worker.destroyed.connect(lambda obj=None, w=worker: self._cleanup_worker(w))
            self._workers.append(worker)
            try:
                worker.start()
            except Exception:
                operation_coordinator.release(session.vid, session.pid, session.busid)
                self._cleanup_worker(worker)
                logger.exception("Failed to start shutdown detach for %s", session.busid)

    def _wait_for_workers(self, timeout_ms: int = 3500):
        workers = list(self._workers)
        for worker in workers:
            request_cancel = getattr(worker, "request_cancel", None)
            if callable(request_cancel):
                request_cancel()
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for worker in workers:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms <= 0:
                break
            try:
                worker.wait(remaining_ms)
            except RuntimeError:
                pass

    def _wait_for_transaction_workers(self, timeout_ms: int):
        """Cancel and wait for detach/rebind work within one shared deadline."""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000

        # Attach is safe to abort. Detach and reconnect/rebind are not.
        killable_owner_ids = usbip_worker.active_killable_thread_ids()
        if killable_owner_ids:
            usbip_wrapper.kill_all_subprocesses(killable_owner_ids)

        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        self._wait_for_workers(remaining_ms)

        if hasattr(self, "_scheduled_reconnect") and self._scheduled_reconnect:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            self._scheduled_reconnect.wait_for_workers(remaining_ms)

    def force_cleanup(self):
        logger.info("Force cleanup: cancelling workers and killing attach subprocesses only")
        for worker in list(self._workers):
            request_cancel = getattr(worker, "request_cancel", None)
            if callable(request_cancel):
                request_cancel()
        killable_owner_ids = usbip_worker.active_killable_thread_ids()
        if killable_owner_ids:
            usbip_wrapper.kill_all_subprocesses(killable_owner_ids)
        self._wait_for_transaction_workers(1500)

    def quit_app(self):
        if hasattr(self, "_scheduled_reconnect") and self._scheduled_reconnect:
            # Cancel now; the quit paths wait for all transaction workers with
            # one shared deadline after starting the shutdown detach workers.
            self._scheduled_reconnect.stop(wait_ms=0)
        if hasattr(self, "_poller") and self._poller:
            self._poller.devices_fetched.disconnect()
            self._poller.connection_changed.disconnect()
            self._poller.service_status_changed.disconnect()
            self._poller.stop()
            self._poller.wait(1000)
            self._poller = None
        if hasattr(self, "_pnp_recovery") and self._pnp_recovery:
            self._pnp_recovery.stop()

    def quit_app_with_detach(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Quitting app with bounded, coordinated detach")
        self.quit_app()
        self.detach_all_async(local_timeout=2.0, host_timeout=1.0)
        self._wait_for_transaction_workers(TRANSACTION_SHUTDOWN_WAIT_MS)

    def commit_data_request(self):
        """Fast Windows shutdown path with a strictly bounded detach budget."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Windows commitDataRequest: fast coordinated shutdown")
        self.quit_app()
        self.detach_all_async(local_timeout=0.35, host_timeout=0.35)
        self._wait_for_transaction_workers(TRANSACTION_SHUTDOWN_WAIT_MS)
