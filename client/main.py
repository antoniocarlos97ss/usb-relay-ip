import faulthandler
import logging
import os
import sys
import traceback

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

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

    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion("1.0.0")
        app.setStyle("Fusion")
    except Exception as exc:
        _write_crash(f"QApplication init failed: {exc}")
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
        window.quit_app()
        tray.hide()
        app.quit()

    tray._quit_action.triggered.disconnect()
    tray._quit_action.triggered.connect(_quit)

    tray.show()
    window.show()

    logger.info("USBRelay Client started")

    ret = app.exec()
    logger.info(f"Event loop exited with code {ret}")
    sys.exit(ret)


if __name__ == "__main__":
    main()
