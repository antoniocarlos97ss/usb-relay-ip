import atexit
import faulthandler
import logging
import os
import sys
import threading
import time
import traceback

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QSystemTrayIcon

from shared.constants import APP_NAME
from shared.i18n import t

_CRASH_LOG = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "USBRelay", "usbrelay_client_crash.log",
)


def _write_crash(msg: str):
    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
            f.flush()
    except Exception:
        pass


def setup_logging():
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    log_dir = os.path.join(appdata, "USBRelay")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "usbrelay_client.log")

    crash_fh = open(_CRASH_LOG, "a", encoding="utf-8")
    faulthandler.enable(crash_fh)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _ensure_usbip(parent=None) -> bool:
    from client.core import usbip_wrapper

    if usbip_wrapper.is_available():
        return True

    reply = QMessageBox.question(
        parent,
        t("install.title"),
        t("install.client_text"),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )

    if reply != QMessageBox.StandardButton.Yes:
        return False

    progress = QProgressDialog(
        t("install.installing"), t("btn.cancel"), 0, 0, parent,
    )
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.show()

    QApplication.processEvents()

    def on_progress(percent, msg=None):
        if msg:
            progress.setLabelText(msg)
        QApplication.processEvents()

    from shared.usbipd_installer import install_for_client
    success, message = install_for_client(progress_callback=on_progress)
    progress.close()

    if not success:
        QMessageBox.warning(
            parent,
            t("install.error_title"),
            f"{t('install.error_text')}\n\n{message}",
        )
        return False

    QMessageBox.information(
        parent,
        t("install.success_title"),
        t("install.success_text"),
    )

    from client.core import usbip_wrapper
    return usbip_wrapper.is_available()


def _emergency_cleanup():
    """Last-resort cleanup for interruptible attach children only.

    Detach and host-cycle workers are transactional and must be allowed to
    finish identity validation or compensatory rebind during shutdown.
    """
    try:
        from client.core import usbip_worker, usbip_wrapper

        owner_ids = usbip_worker.active_killable_thread_ids()
        if owner_ids:
            usbip_wrapper.kill_all_subprocesses(owner_ids)
    except Exception:
        pass


atexit.register(_emergency_cleanup)


def _monitor_headless(
    api_client,
    initial_devices,
    stop_event: threading.Event | None = None,
    refresh_seconds: int = 10,
    config_signature=None,
):
    from client.core import config_manager
    from client.core.pnp_recovery import PnpRecoveryCore

    logger = logging.getLogger(__name__)
    shutdown = stop_event or threading.Event()
    # Session 0 has no QCoreApplication and may not have a desktop.  Keep the
    # lifecycle here entirely in plain Python; the GUI owns the QThread wrapper
    # separately.
    monitor = PnpRecoveryCore(api_client, poll_seconds=10)
    initial_monitored = initial_devices
    if config_signature is not None:
        try:
            initial_monitored = _filter_permanent_devices(
                initial_devices,
                config_manager.load_config(),
            )
        except Exception:
            logger.exception("Headless monitor could not load initial configuration; retrying")
    try:
        monitor.update_devices(initial_monitored)
        monitor.start()
        monitor.poll_cycle()
    except Exception:
        logger.exception("Headless recovery monitor start/poll failed; continuing")
    logger.info("Headless PnP recovery monitor started")
    try:
        while not shutdown.wait(max(1, refresh_seconds)):
            try:
                current_config = None
                if config_signature is not None:
                    current_config = config_manager.load_config()
                    if _headless_config_signature(current_config) != config_signature:
                        logger.info("Headless configuration changed; restarting attach evaluation")
                        return True
                    api_client.update_config(
                        current_config.host_ip,
                        current_config.host_port,
                        current_config.api_key,
                    )
                    monitor.update_host_config(
                        current_config.host_ip,
                        current_config.host_port,
                        current_config.api_key,
                    )
                devices = api_client.get_devices(timeout=3.0)
                if api_client.is_connected():
                    monitored = (
                        _filter_permanent_devices(devices, current_config)
                        if config_signature is not None
                        else devices
                    )
                    monitor.update_devices(monitored)
            except Exception:
                logger.exception("Headless monitor refresh failed; retrying next cycle")
            try:
                monitor.poll_cycle()
            except Exception:
                logger.exception("Headless recovery cycle failed; retrying next cycle")
    except KeyboardInterrupt:
        logger.info("Headless monitor interrupted")
    finally:
        monitor.stop()
    return False


def _headless_config_signature(config):
    permanent = tuple(sorted(
        (device.vid.lower(), device.pid.lower())
        for device in config.permanent_devices
    ))
    return config.host_ip, config.host_port, config.api_key, permanent


def _filter_permanent_devices(devices, config):
    permanent = {
        (device.vid.lower(), device.pid.lower())
        for device in config.permanent_devices
    }
    return [
        device for device in devices
        if (device.vid.lower(), device.pid.lower()) in permanent
    ]


def _run_headless_attach_cycle(api_client, config, shutdown, logger):
    """Run one attach/recovery pass.

    Return ``True`` when configuration changed and the caller should restart
    evaluation, ``None`` after entering the monitor, and ``False`` when the
    caller should retry the attach pass.  Exceptions intentionally escape this
    helper so the outer headless loop can log them and continue.
    """
    from client.core import operation_coordinator, usbip_wrapper
    from client.core.pnp_recovery import VALIDATE_SECONDS, _wait_pnp_healthy
    from client.core.scheduled_reconnect import _run_reconnect_cycle

    if not config.host_ip or not config.permanent_devices:
        logger.info("No host IP or permanent devices configured; headless mode is waiting")
        return False
    api_client.update_config(config.host_ip, config.host_port, config.api_key)

    devices = api_client.get_devices()
    if not api_client.is_connected():
        logger.warning("Host not reachable during headless attach cycle")
        return False

    all_done = True
    for perm_device in config.permanent_devices:
        key = perm_device.vid.lower(), perm_device.pid.lower()
        matches = [
            device for device in devices
            if (device.vid.lower(), device.pid.lower()) == key
        ]
        if len(matches) != 1:
            logger.error(
                "Headless attach requires one unambiguous host device for %s:%s; found %s",
                perm_device.vid,
                perm_device.pid,
                len(matches),
            )
            all_done = False
            continue

        matched = matches[0]
        if matched.state == "Attached":
            local_query = usbip_wrapper.query_attached_devices()
            local_matches = [
                item for item in local_query.devices
                if item.busid == matched.busid
                and (item.vid.lower(), item.pid.lower()) == key
            ] if local_query.success else []
            healthy = (
                local_query.success
                and len(local_matches) == 1
                and _wait_pnp_healthy(
                    matched,
                    time.monotonic() + VALIDATE_SECONDS,
                )
            )
            if healthy:
                continue
            logger.warning(
                "Host reports %s Attached but local session/PnP is not healthy; recovering",
                matched.busid,
            )
            # A host-side Attached row without a live local session is not
            # ownership evidence.  Keep the default fail-closed behavior so a
            # scheduled/headless client cannot reset another machine's session.
            recovered, message = _run_reconnect_cycle(
                api_client,
                matched,
                record_completion=False,
            )
            if recovered:
                logger.info("Recovered stale attached session %s", matched.busid)
            else:
                logger.error("Verified recovery failed for %s: %s", matched.busid, message)
                all_done = False
            continue
        if matched.state != "Shared":
            all_done = False
            continue

        logger.info("Attaching %s", matched.busid)
        if not operation_coordinator.try_acquire(matched.vid, matched.pid, matched.busid):
            logger.warning("Another operation is running for %s", matched.busid)
            all_done = False
            continue
        result = None
        healthy = False
        try:
            try:
                result = usbip_wrapper.attach_device(
                    config.host_ip,
                    matched.busid,
                    vid=matched.vid,
                    pid=matched.pid,
                )
                healthy = result.success and _wait_pnp_healthy(
                    matched,
                    time.monotonic() + VALIDATE_SECONDS,
                )
            except Exception:
                logger.exception("Initial attach crashed for %s", matched.busid)
        finally:
            operation_coordinator.release(matched.vid, matched.pid, matched.busid)
        if healthy:
            logger.info("Attached and validated %s", matched.busid)
            continue

        detail = result.message if result is not None else "unexpected attach exception"
        logger.error("Initial attach/validation failed for %s: %s", matched.busid, detail)
        logger.info("Running verified host/client recovery cycle for %s", matched.busid)
        recovered, message = _run_reconnect_cycle(
            api_client,
            matched,
            record_completion=False,
        )
        if recovered:
            logger.info("Recovered %s through verified host/client cycle", matched.busid)
        else:
            logger.error("Verified recovery failed for %s: %s", matched.busid, message)
            all_done = False

    if not all_done:
        return False
    logger.info("All permanent devices attached; continuing in recovery monitor mode")
    reconfigure = _monitor_headless(
        api_client,
        devices,
        stop_event=shutdown,
        config_signature=_headless_config_signature(config),
    )
    return True if reconfigure is True else None


def run_headless(
    max_attempts: int | None = None,
    retry_seconds: float = 10,
    stop_event: threading.Event | None = None,
):
    logger = logging.getLogger(__name__)
    from client.api.host_client import HostApiClient
    from client.core import config_manager, operation_coordinator, usbip_wrapper
    from client.core.pnp_recovery import _wait_pnp_healthy
    from client.core.scheduled_reconnect import _run_reconnect_cycle

    try:
        config = config_manager.load_config()
    except Exception:
        logger.exception("Headless mode could not load configuration; retrying")
        config = None
    api_client = HostApiClient()
    shutdown = stop_event or threading.Event()
    attempt = 0
    while not shutdown.is_set() and (max_attempts is None or attempt < max(1, max_attempts)):
        if attempt > 0 and retry_seconds > 0:
            if shutdown.wait(retry_seconds):
                return
        attempt += 1
        try:
            config = config_manager.load_config()
            cycle_result = _run_headless_attach_cycle(api_client, config, shutdown, logger)
            if cycle_result is True:
                continue
            if cycle_result is None:
                return
        except Exception:
            logger.exception("Headless attach/recovery cycle failed; retrying")

    if max_attempts is not None and not shutdown.is_set():
        logger.warning("Timeout waiting for permanent devices")


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    def _excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write_crash(f"=== UNHANDLED EXCEPTION ===\n{tb}")
        try:
            logger.critical(f"Unhandled exception:\n{tb}")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    # --headless: boot task running as SYSTEM in Session 0 (no desktop).
    # Must bypass QApplication entirely — Qt cannot initialise without a desktop.
    if "--headless" in sys.argv:
        logger.info("Headless mode: boot-time pre-login attach loop")
        run_headless()
        return

    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion("1.0.0")
        app.setStyle("Fusion")
    except Exception as exc:
        _write_crash(f"QApplication init failed: {exc}")
        return

    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.info("No system tray available — running in headless mode")
        run_headless()
        return

    if not _ensure_usbip():
        sys.exit(1)

    from client.core import config_manager as client_config

    config = client_config.load_config()
    if not config.host_ip:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(t("setup.no_ip_title"))
        msg.setText(t("setup.no_ip_text"))
        msg.setInformativeText(t("setup.no_ip_info"))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    if getattr(sys, "frozen", False):
        assets_dir = os.path.join(sys._MEIPASS, "assets")
    else:
        assets_dir = os.path.join(os.path.dirname(__file__), "assets")

    icon_path = os.path.join(assets_dir, "icon.ico")
    connected_icon_path = os.path.join(assets_dir, "icon_connected.ico")
    if not os.path.exists(icon_path):
        icon_path = ""
        connected_icon_path = ""

    from client.gui.tray import ClientTrayIcon
    from client.gui.main_window import ClientMainWindow

    tray = ClientTrayIcon(icon_path, connected_icon_path)
    window = ClientMainWindow(tray)
    tray.setParent(window)

    def _quit():
        logger.info("Quitting application")
        window.quit_app_with_detach()
        tray.hide()
        app.quit()

    tray._quit_action.triggered.disconnect()
    tray._quit_action.triggered.connect(_quit)

    # commitDataRequest fires on Windows shutdown/restart/logoff.
    # Use the same bounded transaction budget as explicit quit so an in-flight
    # post-unbind compensation is not abandoned during session shutdown.
    def _commit_data_request(_manager):
        logger.info("Windows shutdown/logoff requested")
        window.commit_data_request()
        tray.hide()
        app.quit()

    app.commitDataRequest.connect(_commit_data_request)

    # aboutToQuit fires after app.quit() has been accepted by the event loop.
    # This is our last chance to forcibly kill any remaining subprocesses that
    # would hold VHCI handles open and block Windows shutdown.
    app.aboutToQuit.connect(window.force_cleanup)

    tray.show()
    if "--minimized" in sys.argv:
        window.hide()
    else:
        window.show()

    logger.info("USBRelay Client started")

    ret = app.exec()
    logger.info(f"Event loop exited with code {ret}")
    sys.exit(ret)


if __name__ == "__main__":
    main()
