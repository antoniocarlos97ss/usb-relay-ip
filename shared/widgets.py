"""Reusable branded widgets for USB Relay IP — OhMyTech corporate identity.

Provides:
- BrandedHeader: dark header bar with logo monogram, title, subtitle, instance badge
- StatusBadge: compact status indicator pill (colored dot + text)
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from shared.theme import COLORS


class BrandedHeader(QWidget):
    """Dark header banner with app logo monogram, title, subtitle, and instance badge.

    Usage:
        header = BrandedHeader(
            title="USB Relay IP",
            subtitle="Host — Compartilhamento de dispositivos USB via rede",
            instance="HOST",
        )
        main_layout.addWidget(header)
    """

    def __init__(self, title: str, subtitle: str = "", instance: str = "", parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setObjectName("BrandedHeader")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        # Logo monogram — styled circle with "UR"
        logo = QLabel("UR")
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(f"""
            QLabel {{
                background-color: {COLORS['accent']};
                color: #ffffff;
                border-radius: 17px;
                font-weight: 700;
                font-size: 13px;
            }}
        """)
        layout.addWidget(logo)

        # Title + subtitle column
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color: #ffffff; font-size: 14px; font-weight: 600; background: transparent;"
        )
        title_col.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.7); font-size: 11px; background: transparent;"
            )
            title_col.addWidget(sub_lbl)

        layout.addLayout(title_col)
        layout.addStretch()

        # Instance badge (HOST / CLIENT)
        if instance:
            badge = QLabel(instance)
            badge.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba(88, 101, 242, 0.15);
                    color: {COLORS['accent']};
                    border: 1px solid {COLORS['accent']};
                    border-radius: 10px;
                    padding: 2px 10px;
                    font-size: 10px;
                    font-weight: 600;
                }}
            """)
            layout.addWidget(badge)


class StatusBadge(QLabel):
    """Compact status indicator pill — colored dot + text.

    Usage:
        badge = StatusBadge()
        badge.set_state("Online", "ok")       # green dot
        badge.set_state("Offline", "error")    # red dot
        badge.set_state("Iniciando", "info")   # accent dot
    """

    _DOT = {
        "ok": "●",
        "warning": "●",
        "error": "✗",
        "info": "●",
        "idle": "●",
    }

    _COLOR_MAP = {
        "ok": COLORS["success"],
        "warning": COLORS["warning"],
        "error": COLORS["danger"],
        "info": COLORS["accent"],
        "idle": COLORS["text_muted"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBadge")

    def set_state(self, text: str, state: str = "idle"):
        """Update the badge text and color.

        Args:
            text: Display text (e.g. "Online", "Offline", "Service Down")
            state: One of "ok", "warning", "error", "info", "idle"
        """
        color = self._COLOR_MAP.get(state, COLORS["text_muted"])
        dot = self._DOT.get(state, "●")
        self.setText(f"{dot}  {text}")
        self.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 11px; font-weight: 500;"
        )
