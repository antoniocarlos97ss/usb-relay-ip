"""Central design system for USB Relay IP — dark professional corporate theme.

Unifies the 4 divergent palettes into one coherent system.
Extends the existing client/gui/style.qss for full Host + Client coverage.
"""

from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

# ── Color Tokens ──
COLORS = {
    # Neutrals
    "bg_base": "#1a1a20",
    "bg_surface": "#1e1e24",
    "bg_elevated": "#252630",
    "bg_input": "#141418",
    "bg_hover": "#2a2b36",
    "bg_selected": "#2e303f",
    "border_subtle": "#2d2d39",
    "border_hover": "#374151",
    "border_focus": "#4752c4",
    "text_primary": "#f3f4f6",
    "text_secondary": "#9ca3af",
    "text_muted": "#6b7280",
    # Accent (blurple — maintained from existing design)
    "accent": "#5865f2",
    "accent_hover": "#4752c4",
    "accent_pressed": "#3c44a3",
    # Semantic
    "success": "#10b981",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    "info": "#5865f2",
    # Brand
    "brand_deep": "#2d6cdf",
    "brand_gold": "#f59e0b",
}

# Semantic QColor shortcuts — lazy init (QColor requires QApplication)
_qcolor_cache = None

def get_qcolors():
    """Return dict of named QColor objects. Call after QApplication is created."""
    global _qcolor_cache
    if _qcolor_cache is None:
        _qcolor_cache = {
            "success":  QColor(COLORS["success"]),
            "warning":  QColor(COLORS["warning"]),
            "danger":   QColor(COLORS["danger"]),
            "info":     QColor(COLORS["info"]),
            "gold":     QColor(COLORS["brand_gold"]),
            "accent":   QColor(COLORS["accent"]),
            "muted":    QColor(COLORS["text_muted"]),
            "secondary": QColor(COLORS["text_secondary"]),
        }
    return _qcolor_cache


class _QCOLORProxy:
    """Lazy proxy — defers QColor construction until first access."""
    def __getitem__(self, key):
        return get_qcolors()[key]
    def __contains__(self, key):
        return key in get_qcolors()

QCOLOR = _QCOLORProxy()

# Log viewer color palette (unified — replaces Tokyo Night ad-hoc values)
LOG_COLORS = {
    "timestamp": "#565f89",
    "logger": "#7aa2f7",
    "message": "#c0caf5",
    "DEBUG": "#565f89",
    "INFO": "#10b981",
    "WARNING": "#e0af68",
    "ERROR": "#f7768e",
    "CRITICAL": "#bb9af3",
}

QSS_PATH = (Path(__file__).parent / "theme.qss").resolve()


def apply_theme(app: QApplication):
    """Apply the global dark corporate theme to the QApplication.

    Sets Fusion style + dark QPalette + global QSS from shared/theme.qss.
    """
    app.setStyle("Fusion")

    # Dark QPalette base — covers widgets QSS can't reach (native dialogs etc.)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg_base"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text_primary"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["bg_input"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["bg_surface"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text_primary"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["bg_elevated"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text_primary"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["bg_elevated"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text_primary"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["text_muted"]))
    app.setPalette(palette)

    # Load and apply global QSS with token substitution
    import logging
    _logger = logging.getLogger(__name__)
    if QSS_PATH.exists():
        qss = QSS_PATH.read_text(encoding="utf-8")
        for key, value in COLORS.items():
            qss = qss.replace("{{" + key + "}}", value)
        app.setStyleSheet(qss)
    else:
        _logger.warning(f"theme.qss not found at {QSS_PATH} — UI will use default Fusion styling")
