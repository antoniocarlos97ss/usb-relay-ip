import atexit
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from shared.constants import APP_NAME
from shared.i18n import t


def setup_logging():
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    log_dir = os.path.join(appdata, "USBRelay")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "usbrelay_host.log")

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


def _emergency_cleanup():
    """Last-resort: kill all usbipd.exe processes on abnormal exit."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "usbipd.exe"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


atexit.register(_emergency_cleanup)


def _ensure_usbipd(parent=None) -> bool:
    from host.core import usbipd_wrapper

    if usbipd_wrapper.is_available():
        return True

    reply = QMessageBox.question(
        parent,
        t("install.title"),
        t("install.text"),
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

    from shared.usbipd_installer import install_bundled
    success, message = install_bundled(progress_callback=on_progress)
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

    from host.core import usbipd_wrapper
    return usbipd_wrapper.is_available()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_headless_host():
    """Boot-time headless mode: auto-bind permanent devices + start API server.

    Runs as SYSTEM in Session 0 (no desktop). Must not touch Qt at all.
    Keeps the process alive so the daemon API server thread keeps running.
    """
    logger = logging.getLogger(__name__)
    from host.core import config_manager, usbipd_wrapper
    from host.api.server import start_server

    config = config_manager.load_config()

    # Start FastAPI server first so Client VMs can connect while binding
    start_server(host="0.0.0.0", port=config.api_port)
    logger.info(f"[headless] API server started on port {config.api_port}")

    # Auto-bind permanent devices (retry loop while usbipd settles after boot)
    if config.permanent_devices:
        max_attempts = 20
        for attempt in range(max_attempts):
            if attempt > 0:
                time.sleep(5)
            try:
                devices = usbipd_wrapper.list_devices()
                pending = []
                for perm in config.permanent_devices:
                    matched = next(
                        (d for d in devices
                         if d.vid.lower() == perm.vid.lower()
                         and d.pid.lower() == perm.pid.lower()),
                        None,
                    )
                    if matched:
                        if matched.state not in ("Shared", "Attached"):
                            result = usbipd_wrapper.bind_device(matched.busid)
                            if result.success:
                                logger.info(f"[headless] Auto-bound {matched.busid}")
                            else:
                                logger.warning(f"[headless] Bind failed {matched.busid}: {result.stderr}")
                                pending.append(perm)
                        # else already shared/attached — OK
                    else:
                        pending.append(perm)

                if not pending:
                    logger.info("[headless] All permanent devices bound")
                    break
            except Exception as exc:
                logger.warning(f"[headless] Auto-bind attempt {attempt + 1} failed: {exc}")
    else:
        logger.info("[headless] No permanent devices configured")

    # Keep the process alive — API server thread is a daemon thread
    logger.info("[headless] Entering wait loop (API server running)")
    threading.Event().wait()


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    def _excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            logger.critical(f"Unhandled exception:\n{tb}")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    # --headless: boot task running as SYSTEM in Session 0 (no desktop).
    # Must bypass QApplication entirely.
    if "--headless" in sys.argv:
        logger.info("Headless mode: boot-time auto-bind + API server")
        run_headless_host()
        return

    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion("1.0.0")
        app.setStyle("Fusion")
    except Exception as exc:
        logger.critical(f"QApplication init failed: {exc}")
        return

    if not _ensure_usbipd():
        sys.exit(1)

    if getattr(sys, "frozen", False):
        assets_dir = os.path.join(sys._MEIPASS, "assets")
    else:
        assets_dir = os.path.join(os.path.dirname(__file__), "assets")

    icon_path = os.path.join(assets_dir, "icon.ico")
    connected_icon_path = os.path.join(assets_dir, "icon_connected.ico")
    if not os.path.exists(icon_path):
        icon_path = ""
        connected_icon_path = ""

    from host.gui.tray import TrayIcon
    tray = TrayIcon(icon_path, connected_icon_path)

    from host.gui.main_window import HostMainWindow
    window = HostMainWindow(tray)
    tray.setParent(window)

    from host.api.server import start_server
    from host.core import config_manager as host_config

    config = host_config.load_config()

    # If the headless boot task is already running the API server, skip
    # starting a second instance (would fail to bind the port anyway).
    if _port_in_use(config.api_port):
        logger.info(
            f"API port {config.api_port} already in use "
            "(headless boot task running) — skipping server start"
        )
    else:
        start_server(host="0.0.0.0", port=config.api_port)

    window.set_api_status(True, config.api_port)

    def _quit():
        logger.info("Quitting Host application")
        window.quit_app()
        tray.hide()
        app.quit()

    tray._quit_action.triggered.disconnect()
    tray._quit_action.triggered.connect(_quit)

    # commitDataRequest fires on Windows shutdown/restart/logoff.
    # Signal emits ONE argument (QSessionManager).
    app.commitDataRequest.connect(lambda _manager: _quit())

    # aboutToQuit fires after app.quit() is accepted by the event loop.
    # Last chance to forcibly kill any usbipd subprocesses.
    app.aboutToQuit.connect(window.force_cleanup)

    tray.show()
    if "--minimized" in sys.argv:
        window.hide()
    else:
        window.show()

    logger.info("USBRelay Host started")

    ret = app.exec()
    logger.info(f"Event loop exited with code {ret}")
    sys.exit(ret)


if __name__ == "__main__":
    main()
