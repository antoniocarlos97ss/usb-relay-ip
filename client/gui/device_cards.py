import logging
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect, QSize
from PyQt6.QtWidgets import (
    QScrollArea, QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLayout, QLayoutItem, QSizePolicy
)
from shared.models import UsbDevice
from shared.i18n import t

logger = logging.getLogger(__name__)


class FlowLayout(QLayout):
    """
    A custom layout that arranges widgets horizontally and wraps them
    to the next line when width constraints are met, providing a responsive card grid.
    """
    def __init__(self, parent=None, margin=10, spacing=10):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(+left, +top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            if not widget or not widget.isVisible():
                continue
            
            space_x = self.spacing()
            space_y = self.spacing()
            if space_x == -1:
                space_x = widget.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal
                )
            if space_y == -1:
                space_y = widget.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Vertical
                )

            item_size = item.sizeHint()
            next_x = x + item_size.width() + space_x
            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + bottom


class DeviceCard(QFrame):
    attach_requested = pyqtSignal(str)
    detach_requested = pyqtSignal(str)
    permanent_toggle = pyqtSignal(str, bool)
    selected = pyqtSignal(str)

    def __init__(self, device: UsbDevice, is_selected=False, parent=None):
        super().__init__(parent)
        self.device = device
        self._is_selected = is_selected
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("DeviceCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        
        state_str = self.device.state.lower()
        self.setProperty("state", state_str)
        self.setProperty("selected", "true" if self._is_selected else "false")
        
        # Horizontal layout for the card
        card_layout = QHBoxLayout(self)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(12)
        
        # Left status indicator bar
        self.indicator_bar = QWidget()
        self.indicator_bar.setFixedWidth(4)
        self.indicator_bar.setObjectName("CardIndicator")
        card_layout.addWidget(self.indicator_bar)
        
        # Core Info Column
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        info_layout.setContentsMargins(0, 0, 0, 0)
        
        # Row 1: Title and Star
        title_layout = QHBoxLayout()
        title_layout.setSpacing(6)
        
        # Use description as title
        self.title_label = QLabel(self.device.description)
        self.title_label.setObjectName("CardTitle")
        self.title_label.setWordWrap(True)
        title_layout.addWidget(self.title_label, 1)
        
        # Permanent star toggle
        self.star_btn = QPushButton()
        self.star_btn.setObjectName("StarButton")
        self.star_btn.setFlat(True)
        self.star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star_btn.setText("★" if self.device.is_permanent else "☆")
        self.star_btn.setProperty("active", "true" if self.device.is_permanent else "false")
        self.star_btn.setToolTip(t("card.tooltip_permanent") if self.device.is_permanent else t("card.tooltip_not_permanent"))
        self.star_btn.clicked.connect(self._on_star_clicked)
        title_layout.addWidget(self.star_btn)
        
        info_layout.addLayout(title_layout)
        
        # Row 2: Badge elements (Bus ID, VID:PID, Status badge)
        badge_layout = QHBoxLayout()
        badge_layout.setSpacing(6)
        badge_layout.setContentsMargins(0, 2, 0, 0)
        
        bus_id_badge = QLabel(f"Bus {self.device.busid}")
        bus_id_badge.setObjectName("Badge")
        badge_layout.addWidget(bus_id_badge)
        
        vid_pid_badge = QLabel(f"{self.device.vid.upper()}:{self.device.pid.upper()}")
        vid_pid_badge.setObjectName("Badge")
        badge_layout.addWidget(vid_pid_badge)
        
        # Status Pill
        if state_str == "attached":
            status_text = t("state.attached")
        elif state_str in ("shared", "available"):
            status_text = t("state.available")
        else:
            status_text = t("state.offline")
            
        status_badge = QLabel(status_text)
        status_badge.setObjectName("StatusBadge")
        status_badge.setProperty("state", state_str)
        badge_layout.addWidget(status_badge)
        
        badge_layout.addStretch()
        info_layout.addLayout(badge_layout)
        
        card_layout.addLayout(info_layout, 1)
        
        # Actions Layout (Right side action button)
        self.action_btn = QPushButton()
        self.action_btn.setObjectName("CardActionButton")
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setMinimumWidth(110)
        
        if state_str == "attached":
            self.action_btn.setText(f"🔌 {t('ctx.detach')}")
            self.action_btn.setProperty("styleClass", "danger")
            self.action_btn.clicked.connect(lambda: self.detach_requested.emit(self.device.busid))
        elif state_str in ("shared", "available"):
            self.action_btn.setText(f"⚡ {t('ctx.attach')}")
            self.action_btn.setProperty("styleClass", "primary")
            self.action_btn.clicked.connect(lambda: self.attach_requested.emit(self.device.busid))
        else:
            self.action_btn.setText(t("state.offline"))
            self.action_btn.setEnabled(False)
            self.action_btn.setProperty("styleClass", "disabled")
            
        card_layout.addWidget(self.action_btn)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.device.busid)
        super().mousePressEvent(event)

    def _on_star_clicked(self):
        self.permanent_toggle.emit(self.device.busid, not self.device.is_permanent)

    def set_selected(self, selected: bool):
        self._is_selected = selected
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class ClientDeviceCards(QScrollArea):
    attach_requested = pyqtSignal(str)
    detach_requested = pyqtSignal(str)
    permanent_toggle = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: list[UsbDevice] = []
        self._selected_busid: str | None = None
        self._cards: dict[str, DeviceCard] = {}
        self._setup_ui()

    def _setup_ui(self):
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setObjectName("DeviceCardsScrollArea")
        
        self.container = QWidget()
        self.container.setObjectName("DeviceCardsContainer")
        
        self.flow_layout = FlowLayout(self.container, margin=8, spacing=12)
        self.setWidget(self.container)

    def update_devices(self, devices: list[UsbDevice]):
        self._devices = devices
        
        # Remove and delete all old card widgets
        while self.flow_layout.count() > 0:
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self._cards.clear()
        
        if not devices:
            no_devices_label = QLabel(t("card.no_devices"))
            no_devices_label.setObjectName("NoDevicesLabel")
            no_devices_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.flow_layout.addWidget(no_devices_label)
            return

        for device in devices:
            is_sel = (device.busid == self._selected_busid)
            card = DeviceCard(device, is_selected=is_sel)
            card.attach_requested.connect(self.attach_requested.emit)
            card.detach_requested.connect(self.detach_requested.emit)
            card.permanent_toggle.connect(self.permanent_toggle.emit)
            card.selected.connect(self._on_card_selected)
            
            self.flow_layout.addWidget(card)
            self._cards[device.busid] = card

    def _on_card_selected(self, busid: str):
        # Deselect previous card
        if self._selected_busid in self._cards:
            self._cards[self._selected_busid].set_selected(False)
            
        self._selected_busid = busid
        
        # Select new card
        if busid in self._cards:
            self._cards[busid].set_selected(True)

    def get_selected_busid(self) -> str | None:
        return self._selected_busid
