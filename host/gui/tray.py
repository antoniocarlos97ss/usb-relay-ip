from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from shared.i18n import t


def _make_icon(color: str) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.GlobalColor.white)
    painter.drawEllipse(2, 2, 28, 28)
    painter.setBrush(Qt.GlobalColor.gray if color == "gray" else Qt.GlobalColor.darkGreen)
    painter.drawEllipse(5, 5, 22, 22)
    painter.end()
    return QIcon(pixmap)


def _load_icon(path: str, fallback_color: str) -> QIcon:
    if not path:
        return _make_icon(fallback_color)
    try:
        icon = QIcon(path)
        if icon.availableSizes():
            return icon
    except Exception:
        pass
    return _make_icon(fallback_color)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, icon_path: str, connected_icon_path: str, parent=None):
        super().__init__(parent)
        self._default_icon = _load_icon(icon_path, "gray") if icon_path else _make_icon("gray")
        self._connected_icon = _load_icon(connected_icon_path, "green") if connected_icon_path else _make_icon("green")

        self.setIcon(self._default_icon)
        self.setToolTip("USB Relay IP Host")

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
