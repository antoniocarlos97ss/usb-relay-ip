from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from shared.i18n import t


class TrayIcon(QSystemTrayIcon):
    def __init__(self, icon_path: str, connected_icon_path: str, parent=None):
        super().__init__(parent)
        self._default_icon = QIcon(icon_path)
        self._connected_icon = QIcon(connected_icon_path) if connected_icon_path else self._default_icon
        self.setIcon(self._default_icon)
        self.setToolTip("USBRelay Host")

        self._menu = QMenu()
        self._setup_menu()

    def _setup_menu(self):
        self._title_action = QAction(t("tray.host_title"))
        self._title_action.setEnabled(False)
        self._menu.addAction(self._title_action)
        self._menu.addSeparator()

        self._status_action = QAction(t("tray.api_running"))
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._menu.addSeparator()

        self._open_action = QAction(t("tray.open"))
        self._open_action.triggered.connect(self._on_open)
        self._menu.addAction(self._open_action)

        self._menu.addSeparator()

        self._quit_action = QAction(t("tray.quit"))
        self._quit_action.triggered.connect(self._on_quit)
        self._menu.addAction(self._quit_action)

        self.setContextMenu(self._menu)

    def set_connected_state(self, connected: bool):
        if connected:
            self.setIcon(self._connected_icon)
            self._status_action.setText(t("tray.api_running"))
        else:
            self.setIcon(self._default_icon)
            self._status_action.setText(t("tray.api_stopped"))

    def show_notification(self, title: str, message: str):
        self.showMessage(title, message, QIcon(), 3000)

    def _on_open(self):
        if self.parent():
            self.parent().show()
            self.parent().raise_()
            self.parent().activateWindow()

    def _on_quit(self):
        from PyQt6.QtWidgets import QApplication
        if self.parent():
            self.parent().close()
        QApplication.quit()
