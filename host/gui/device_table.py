from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QMenu, QTableWidget, QTableWidgetItem

from shared.i18n import t
from shared.models import UsbDevice


class DeviceTable(QTableWidget):
    share_requested = pyqtSignal(str)
    unshare_requested = pyqtSignal(str)
    permanent_toggle = pyqtSignal(str, bool)

    COL_BUSID = 0
    COL_VID = 1
    COL_PID = 2
    COL_DESCRIPTION = 3
    COL_STATUS = 4
    COL_PERMANENT = 5

    def __init__(self, is_host: bool = True, parent=None):
        super().__init__(parent)
        self._is_host = is_host
        self._devices: list[UsbDevice] = []
        self._setup_ui()

    def _setup_ui(self):
        headers = [
            t("table.bus_id"), t("table.vid"), t("table.pid"),
            t("table.description"), t("table.status"), t("table.permanent"),
        ]
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        self.setSortingEnabled(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(self.COL_BUSID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_VID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_PID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_DESCRIPTION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_PERMANENT, QHeaderView.ResizeMode.ResizeToContents)

    def update_devices(self, devices: list[UsbDevice]):
        self._devices = devices
        self.setRowCount(len(devices))

        state_labels = {
            "Not shared": t("state.not_shared"),
            "Shared": t("state.shared"),
            "Attached": t("state.attached"),
        }

        for row, device in enumerate(devices):
            self.setItem(row, self.COL_BUSID, self._make_item(device.busid))
            self.setItem(row, self.COL_VID, self._make_item(device.vid.upper()))
            self.setItem(row, self.COL_PID, self._make_item(device.pid.upper()))
            self.setItem(row, self.COL_DESCRIPTION, self._make_item(device.description))

            status_text = state_labels.get(device.state, device.state)
            status_item = self._make_item(status_text)
            if device.state == "Shared":
                status_item.setForeground(QColor("#22aa22"))
            elif device.state == "Attached":
                status_item.setForeground(QColor("#2266cc"))
            else:
                status_item.setForeground(QColor("#cc2222"))
            self.setItem(row, self.COL_STATUS, status_item)

            perm_item = self._make_item("\u2605" if device.is_permanent else "")
            if device.is_permanent:
                perm_item.setForeground(QColor("#ddaa00"))
            self.setItem(row, self.COL_PERMANENT, perm_item)

    def _make_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _show_context_menu(self, pos):
        row = self.rowAt(pos.y())
        if row < 0 or row >= len(self._devices):
            return

        device = self._devices[row]
        menu = QMenu(self)

        if self._is_host:
            if device.state == "Not shared":
                share_action = menu.addAction(t("ctx.share"))
                share_action.triggered.connect(lambda: self.share_requested.emit(device.busid))
            else:
                unshare_action = menu.addAction(t("ctx.unshare"))
                unshare_action.triggered.connect(lambda: self.unshare_requested.emit(device.busid))

            menu.addSeparator()

            if device.is_permanent:
                perm_action = menu.addAction(t("ctx.remove_always_share"))
                perm_action.triggered.connect(lambda: self.permanent_toggle.emit(device.busid, False))
            else:
                perm_action = menu.addAction(t("ctx.always_share"))
                perm_action.triggered.connect(lambda: self.permanent_toggle.emit(device.busid, True))
        else:
            if device.state in ("Shared", "Available"):
                attach_action = menu.addAction(t("ctx.attach"))
                attach_action.triggered.connect(lambda: self.share_requested.emit(device.busid))
            elif device.state == "Attached":
                detach_action = menu.addAction(t("ctx.detach"))
                detach_action.triggered.connect(lambda: self.unshare_requested.emit(device.busid))

            menu.addSeparator()

            if device.is_permanent:
                perm_action = menu.addAction(t("ctx.remove_always_attach"))
                perm_action.triggered.connect(lambda: self.permanent_toggle.emit(device.busid, False))
            else:
                perm_action = menu.addAction(t("ctx.always_attach"))
                perm_action.triggered.connect(lambda: self.permanent_toggle.emit(device.busid, True))

        menu.addSeparator()
        copy_action = menu.addAction(t("ctx.copy_busid"))
        copy_action.triggered.connect(lambda: self._copy_busid(device.busid))

        menu.exec(self.viewport().mapToGlobal(pos))

    def _copy_busid(self, busid: str):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(busid)

    def get_selected_busid(self) -> str | None:
        row = self.currentRow()
        if 0 <= row < len(self._devices):
            return self._devices[row].busid
        return None
