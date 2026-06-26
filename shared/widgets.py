"""Reusable branded widgets for USB Relay IP — OhMyTech corporate identity.

Provides:
- BrandedHeader: dark header bar with real app logo, title, subtitle, instance badge
- StatusBadge: compact status indicator pill (colored dot + text)
"""

from pathlib import Path
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from shared.theme import COLORS


def _resolve_brand_logo_path() -> str | None:
    """Locate the custom installer/app logo bundled with the app or available in repo."""
    candidates = []

    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass:
            candidates.append(meipass / "assets" / "icon.ico")
            candidates.append(meipass / "assets" / "icon_connected.ico")

    repo_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            repo_root / "host" / "assets" / "icon.ico",
            repo_root / "client" / "assets" / "icon.ico",
            repo_root / "host" / "assets" / "icon_connected.ico",
            repo_root / "client" / "assets" / "icon_connected.ico",
            repo_root / "assets" / "icon.ico",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


class BrandedHeader(QWidget):
    """Dark header banner with logo, title, subtitle, and instance badge."""

    def __init__(self, title: str, subtitle: str = "", instance: str = "", parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setObjectName("BrandedHeader")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self._logo = QLabel()
        logo_size = 34
        self._logo.setFixedSize(logo_size, logo_size)
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setStyleSheet(
            f"""
            QLabel {{
                background-color: {COLORS['bg_elevated']};
                color: {COLORS['accent']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 12px;
            }}
            """
        )

        logo_path = _resolve_brand_logo_path()
        if logo_path:
            pixmap = QIcon(logo_path).pixmap(logo_size, logo_size)
            if not pixmap.isNull():
                self._logo.setPixmap(pixmap)
                self._logo.setStyleSheet("background: transparent; border: none;")
            else:
                self._logo.setText("UR")
                self._logo.setStyleSheet(
                    f"""
                    QLabel {{
                        background-color: {COLORS['accent']};
                        color: #ffffff;
                        border-radius: {logo_size // 2}px;
                        font-weight: 700;
                        font-size: 13px;
                    }}
                    """
                )
        else:
            self._logo.setText("UR")
            self._logo.setStyleSheet(
                f"""
                QLabel {{
                    background-color: {COLORS['accent']};
                    color: #ffffff;
                    border-radius: {logo_size // 2}px;
                    font-weight: 700;
                    font-size: 13px;
                }}
                """
            )

        layout.addWidget(self._logo)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            "color: #ffffff; font-size: 14px; font-weight: 600; background: transparent;"
        )
        title_col.addWidget(self._title_lbl)

        self._subtitle_lbl = QLabel(subtitle) if subtitle else None
        if self._subtitle_lbl:
            self._subtitle_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.7); font-size: 11px; background: transparent;"
            )
            title_col.addWidget(self._subtitle_lbl)

        layout.addLayout(title_col)
        layout.addStretch()

        if instance:
            badge = QLabel(instance)
            badge.setStyleSheet(
                f"""
                QLabel {{
                    background-color: rgba(88, 101, 242, 0.15);
                    color: {COLORS['accent']};
                    border: 1px solid {COLORS['accent']};
                    border-radius: 10px;
                    padding: 2px 10px;
                    font-size: 10px;
                    font-weight: 600;
                }}
                """
            )
            layout.addWidget(badge)


_STATUS_ICON = {
    "ok": "●",
    "warning": "●",
    "error": "●",
    "info": "●",
    "idle": "●",
}

_STATUS_COLOR = {
    "ok": COLORS["success"],
    "warning": COLORS["warning"],
    "error": COLORS["danger"],
    "info": COLORS["accent"],
    "idle": COLORS["text_muted"],
}


class StatusBadge(QLabel):
    """Compact status indicator pill — colored dot + text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBadge")
        self.set_state("", "idle")

    def set_state(self, text: str, state: str = "idle"):
        color = _STATUS_COLOR.get(state, COLORS["text_muted"])
        dot = _STATUS_ICON.get(state, "●")
        self.setText(f"{dot}  {text}")
        self.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 11px; font-weight: 500;"
        )
