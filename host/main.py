import logging
import os
import sys

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
        t("install.downloading"), t("btn.cancel"), 0, 100, parent,
    )
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    progress.show()

    QApplication.processEvents()

    def on_progress(percent, msg=None):
        if percent >= 0:
            progress.setValue(percent)
            progress.setLabelText(t("install.downloading") + f" {percent}%")
        elif msg:
            progress.setLabelText(msg)
            progress.setValue(100)
        QApplication.processEvents()

    from shared.usbipd_installer import download_and_install
    success, message = download_and_install(progress_callback=on_progress)
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

    import subprocess
    try:
        subprocess.run(
            ["refreshenv"], shell=True, capture_output=True, timeout=5,
        )
    except Exception:
        pass

    from host.core import usbipd_wrapper
    return usbipd_wrapper.is_available()


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion("1.0.0")
    app.setStyle("Fusion")

    if not _ensure_usbipd():
        sys.exit(1)

    icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")
    connected_icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon_connected.ico")
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
    start_server(host="0.0.0.0", port=config.api_port)
    window.set_api_status(True, config.api_port)

    tray.show()
    window.show()

    logger.info("USBRelay Host started")

    ret = app.exec()

    window.quit_app()
    if hasattr(tray, "hide"):
        tray.hide()
    sys.exit(ret)


if __name__ == "__main__":
    main()
