import faulthandler
import logging
import os
import sys
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


def run_headless():
    logger = logging.getLogger(__name__)
    from client.api.host_client import HostApiClient
    from client.core import config_manager, usbip_wrapper

    config = config_manager.load_config()
    if not config.host_ip or not config.permanent_devices:
        logger.info("No host IP or permanent devices configured, nothing to do in headless mode")
        return

    api_client = HostApiClient(
        host_ip=config.host_ip,
        host_port=config.host_port,
        api_key=config.api_key,
    )

    max_attempts = 20
    for attempt in range(max_attempts):
        if attempt > 0:
            time.sleep(10)

        devices = api_client.get_devices()
        if not api_client.is_connected():
            logger.warning(f"Host not reachable (attempt {attempt+1}/{max_attempts})")
            continue

        all_done = True
        for perm_device in config.permanent_devices:
            matched = None
            for d in devices:
                if d.vid == perm_device.vid and d.pid == perm_device.pid:
                    matched = d
                    break

            if matched and matched.state == "Shared":
                logger.info(f"Attaching {matched.busid}")
                result = usbip_wrapper.attach_device(config.host_ip, matched.busid)
                if result.success:
                    logger.info(f"Attached {matched.busid}")
                else:
                    logger.error(f"Attach failed {matched.busid}: {result.message}")
                    logger.info(f"Trying unbind+rebind on host for {matched.busid}")
                    api_client.unbind_device(matched.busid)
                    time.sleep(2)
                    api_client.bind_device(matched.busid)
                    time.sleep(2)
                    result2 = usbip_wrapper.attach_device(config.host_ip, matched.busid)
                    if result2.success:
                        logger.info(f"Attached {matched.busid} after unbind+rebind")
                    else:
                        logger.error(f"Attach still failed after unbind+rebind: {result2.message}")
                        all_done = False
            elif not (matched and matched.state == "Attached"):
                all_done = False

        if all_done:
            logger.info("All permanent devices attached, exiting")
            return

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
        app.commitDataRequest.connect(lambda request, manager: app.quit())
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
