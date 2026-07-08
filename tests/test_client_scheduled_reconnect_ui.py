import os
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _FakeSignal:
    def __init__(self, *args, **kwargs):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class _FakeQt:
    class ContextMenuPolicy:
        CustomContextMenu = 1

    class AlignmentFlag:
        AlignLeft = 1
        AlignVCenter = 2


class _FakeHeader:
    class ResizeMode:
        ResizeToContents = 1
        Stretch = 2

    def setStretchLastSection(self, *_args, **_kwargs):
        pass

    def setSectionResizeMode(self, *_args, **_kwargs):
        pass


class _FakeQColor:
    def __init__(self, value):
        self.value = value


class _FakeQTableWidgetItem:
    def __init__(self, text):
        self.text = text
        self.alignment = None
        self.foreground = None

    def setTextAlignment(self, alignment):
        self.alignment = alignment

    def setForeground(self, color):
        self.foreground = color


class _FakeQWidget:
    def __init__(self, parent=None):
        self.parent = parent


class _FakeQTableWidget(_FakeQWidget):
    SelectionBehavior = types.SimpleNamespace(SelectRows=1)
    SelectionMode = types.SimpleNamespace(SingleSelection=1)
    EditTrigger = types.SimpleNamespace(NoEditTriggers=1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row_count = 0
        self._current_row = -1
        self._header = _FakeHeader()
        self.customContextMenuRequested = _FakeSignal()

    def setColumnCount(self, *_args, **_kwargs):
        pass

    def setHorizontalHeaderLabels(self, *_args, **_kwargs):
        pass

    def setSelectionBehavior(self, *_args, **_kwargs):
        pass

    def setSelectionMode(self, *_args, **_kwargs):
        pass

    def setEditTriggers(self, *_args, **_kwargs):
        pass

    def setAlternatingRowColors(self, *_args, **_kwargs):
        pass

    def setShowGrid(self, *_args, **_kwargs):
        pass

    def setSortingEnabled(self, *_args, **_kwargs):
        pass

    def setContextMenuPolicy(self, *_args, **_kwargs):
        pass

    def horizontalHeader(self):
        return self._header

    def setRowCount(self, value):
        self._row_count = value

    def setItem(self, *_args, **_kwargs):
        pass

    def rowAt(self, *_args, **_kwargs):
        return self._current_row

    def viewport(self):
        return types.SimpleNamespace(mapToGlobal=lambda pos: pos)

    def currentRow(self):
        return self._current_row

    def deleteLater(self):
        pass


class _FakeQMenu:
    last_instance = None

    def __init__(self, parent=None):
        self.parent = parent
        self.entries = []
        self.executed = False
        _FakeQMenu.last_instance = self

    def addAction(self, text):
        action = _FakeAction(text)
        self.entries.append(action)
        return action

    def addSeparator(self):
        self.entries.append(None)

    def exec(self, *_args, **_kwargs):
        self.executed = True


class _FakeAction:
    def __init__(self, text):
        self.text = text
        self.triggered = _FakeSignal()


_fake_qt = types.ModuleType("PyQt6")
_fake_qtcore = types.ModuleType("PyQt6.QtCore")
_fake_qtgui = types.ModuleType("PyQt6.QtGui")
_fake_qtwidgets = types.ModuleType("PyQt6.QtWidgets")
_fake_qtcore.Qt = _FakeQt
_fake_qtcore.pyqtSignal = lambda *args, **kwargs: _FakeSignal()
_fake_qtgui.QColor = _FakeQColor
_fake_qtwidgets.QHeaderView = _FakeHeader
_fake_qtwidgets.QMenu = _FakeQMenu
_fake_qtwidgets.QTableWidget = _FakeQTableWidget
_fake_qtwidgets.QTableWidgetItem = _FakeQTableWidgetItem
sys.modules.setdefault("PyQt6", _fake_qt)
sys.modules["PyQt6.QtCore"] = _fake_qtcore
sys.modules["PyQt6.QtGui"] = _fake_qtgui
sys.modules["PyQt6.QtWidgets"] = _fake_qtwidgets

from client.gui.device_table import ClientDeviceTable
from shared.i18n import get_language, set_language, t
from shared.models import UsbDevice


class TestClientScheduledReconnectMenu(unittest.TestCase):
    def setUp(self):
        self._table = ClientDeviceTable()

    def tearDown(self):
        self._table.deleteLater()

    def _show_menu(self, device, scheduled=None):
        self._table._devices = [device]
        self._table.set_scheduled_reconnect_devices(scheduled or set())
        viewport = types.SimpleNamespace(mapToGlobal=lambda pos: pos)
        pos = types.SimpleNamespace(y=lambda: 0)

        with patch("client.gui.device_table.QMenu", _FakeQMenu), patch.object(
            self._table, "rowAt", return_value=0
        ), patch.object(
            self._table, "viewport", return_value=viewport
        ):
            self._table._show_context_menu(pos)

        return _FakeQMenu.last_instance

    def test_menu_offers_enable_when_scheduled_reconnect_is_disabled(self):
        device = UsbDevice(
            busid="1-1",
            vid="046d",
            pid="c31c",
            description="Keyboard",
            state="Shared",
            is_permanent=False,
        )

        menu = self._show_menu(device)

        texts = [entry.text for entry in menu.entries if entry is not None]
        self.assertIn(t("ctx.scheduled_reconnect_enable"), texts)
        self.assertNotIn(t("ctx.scheduled_reconnect_update"), texts)
        self.assertNotIn(t("ctx.scheduled_reconnect_disable"), texts)

    def test_menu_uses_cached_schedule_state_without_loading_config(self):
        device = UsbDevice(
            busid="1-1",
            vid="046d",
            pid="c31c",
            description="Keyboard",
            state="Shared",
            is_permanent=True,
        )

        with patch("client.core.config_manager.load_config", side_effect=AssertionError("unexpected load_config")):
            menu = self._show_menu(device, {("046d", "c31c")})

        texts = [entry.text for entry in menu.entries if entry is not None]
        self.assertIn(t("ctx.scheduled_reconnect_update"), texts)
        self.assertIn(t("ctx.scheduled_reconnect_disable"), texts)

    def test_menu_offers_update_and_disable_when_scheduled_reconnect_is_enabled(self):
        device = UsbDevice(
            busid="1-1",
            vid="046d",
            pid="c31c",
            description="Keyboard",
            state="Shared",
            is_permanent=True,
        )

        menu = self._show_menu(device, {("046d", "c31c")})

        texts = [entry.text for entry in menu.entries if entry is not None]
        self.assertIn(t("ctx.scheduled_reconnect_update"), texts)
        self.assertIn(t("ctx.scheduled_reconnect_disable"), texts)
        self.assertNotIn(t("ctx.scheduled_reconnect_enable"), texts)


class TestClientScheduledReconnectStrings(unittest.TestCase):
    def setUp(self):
        self._previous_lang = get_language()

    def tearDown(self):
        set_language(self._previous_lang)

    def test_new_strings_exist_in_both_languages(self):
        set_language("en")
        self.assertEqual(t("ctx.scheduled_reconnect_enable"), "Enable Scheduled Reconnect")
        self.assertEqual(t("dialog.scheduled_reconnect_title"), "Scheduled Reconnect")
        self.assertEqual(
            t("notify.scheduled_reconnect_disabled", busid="1-1"),
            "Scheduled reconnect disabled for 1-1.",
        )

        set_language("pt")
        self.assertEqual(t("ctx.scheduled_reconnect_enable"), "Ativar Reconexão Programada")
        self.assertEqual(t("dialog.scheduled_reconnect_title"), "Reconexão Programada")
        self.assertEqual(
            t("notify.scheduled_reconnect_disabled", busid="1-1"),
            "Reconexão programada desativada para 1-1.",
        )
