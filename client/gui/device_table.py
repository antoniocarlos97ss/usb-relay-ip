from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QMenu, QTableWidget, QTableWidgetItem

from shared.i18n import t
from shared.models import UsbDevice


class ClientDeviceTable(QTableWidget):
    attach_requested = pyqtSignal(str)
    detach_requested = pyqtSignal(str)
    permanent_toggle = pyqtSignal(str, bool)

    COL_BUSID = 0
    COL_VIDPID = 1
    COL_DESCRIPTION = 2
    COL_STATUS = 3
    COL_PERMANENT = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: list[UsbDevice] = []
        self._setup_ui()

    def _setup_ui(self):
        headers = [
            t("table.bus_id"), t("table.vid_pid"),
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
        header.setSectionResizeMode(self.COL_VIDPID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_DESCRIPTION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_PERMANENT, QHeaderView.ResizeMode.ResizeToContents)

    def update_devices(self, devices: list[UsbDevice]):
        self._devices = devices
        self.setRowCount(len(devices))

        for row, device in enumerate(devices):
            self.setItem(row, self.COL_BUSID, self._make_item(device.busid))
            self.setItem(row, self.COL_VIDPID, self._make_item(f"{device.vid.upper()}:{device.pid.upper()}"))
            self.setItem(row, self.COL_DESCRIPTION, self._make_item(device.description))

            if device.state == "Attached":
                status_text = t("state.attached")
                color = QColor("#22aa22")
            elif device.state == "Shared":
                status_text = t("state.available")
                color = QColor("#ddaa00")
            else:
                status_text = t("state.offline")
                color = QColor("#cc2222")

            status_item = self._make_item(status_text)
            status_item.setForeground(color)
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

        if device.state in ("Shared", "Available"):
            attach_action = menu.addAction(t("ctx.attach"))
            attach_action.triggered.connect(lambda: self.attach_requested.emit(device.busid))
        elif device.state == "Attached":
            detach_action = menu.addAction(t("ctx.detach"))
            detach_action.triggered.connect(lambda: self.detach_requested.emit(device.busid))

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
