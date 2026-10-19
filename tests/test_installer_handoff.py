from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect
from local.installer_handoff import (
    InstallerHandoffEvents, handoff_event_name, logical_handoff_rect,
    parse_handoff_arguments, parse_handoff_request, start_synchronized_handoff,
)


class InstallerHandoffTests(unittest.TestCase):
    def screen(self, name, rect, dpr):
        return SimpleNamespace(name=lambda: name, geometry=lambda: QRect(*rect), devicePixelRatio=lambda: dpr)

    def test_negative_origin_and_mixed_scale_keep_native_monitor_origin(self):
        left = self.screen("left", (-1920, 0, 1920, 1080), 1.)
        right = self.screen("right", (0, 0, 1920, 1080), 2.)
        self.assertEqual(logical_handoff_rect((-1200, 0, 370, 70), [left, right]), QRect(-1200, 0, 370, 70))
        self.assertEqual(logical_handoff_rect((1550, 0, 740, 140), [left, right]), QRect(775, 0, 370, 70))

    def test_invalid_offscreen_and_ambiguous_mirrors_do_not_pick_random_screen(self):
        screen = self.screen("a", (0, 0, 1920, 1080), 1.)
        mirror = self.screen("b", (0, 0, 1920, 1080), 1.)
        self.assertIsNone(logical_handoff_rect((600, 400, 370, 70), [screen]))
        self.assertIsNone(logical_handoff_rect((600, 0, 370, 70), [screen, mirror]))
        self.assertIsNone(logical_handoff_rect((600, 0, 370, 70), []))

    def test_cli_rejects_incomplete_or_extreme_bounds_before_start(self):
        self.assertEqual(parse_handoff_arguments(["--installer-handoff", "-1200", "0", "370", "70"]), (-1200, 0, 370, 70))
        for args in (["--installer-handoff"], ["--installer-handoff", "a", "0", "370", "70"],
                     ["--installer-handoff", "0", "0", "0", "70"],
                     ["--installer-handoff", "0", "0", "370", "999999"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                parse_handoff_arguments(args)

    def test_cli_preserves_normal_dispatch_and_passes_only_checked_bounds(self):
        from local.main import main
        app_main = Mock(return_value=19)
        with patch.dict("sys.modules", {"local.app": SimpleNamespace(main=app_main)}):
            self.assertEqual(main(["--installer-handoff", "600", "0", "370", "70"]), 19)
        app_main.assert_called_once_with(installer_handoff=(600, 0, 370, 70))
        app_main.reset_mock()
        with patch.dict("sys.modules", {"local.app": SimpleNamespace(main=app_main)}):
            self.assertEqual(main(["--installer-handoff", "600"]), 2)
        app_main.assert_not_called()

    def test_optional_sync_token_is_checked_without_changing_legacy_bounds(self):
        args = ["--installer-handoff", "600", "0", "370", "70"]
        self.assertEqual(parse_handoff_request(args), ((600, 0, 370, 70), None))
        self.assertEqual(parse_handoff_request(args + ["--installer-sync", "123-456"]),
                         ((600, 0, 370, 70), "123-456"))
        for tail in (["--installer-sync"], ["--installer-sync", "../arbitrary"],
                     ["--installer-sync", "123-456.extra"], ["--installer-sync", "-1-456"],
                     ["--installer-sync", "12345678901-1"], ["--unexpected", "123-456"]):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                parse_handoff_request(args + tail)
        app_main = Mock(return_value=7)
        from local.main import main
        with patch.dict("sys.modules", {"local.app": SimpleNamespace(main=app_main)}):
            self.assertEqual(main(args + ["--installer-sync", "123-456"]), 7)
        app_main.assert_called_once_with(installer_handoff=(600, 0, 370, 70), installer_sync="123-456")


class HandoffGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QWidget
        self.surface = QWidget()
        self.surface.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.events = SimpleNamespace(signal=Mock(return_value=True), revealed=Mock(return_value=False), close=Mock())
        self.start = Mock()
        self.gate = start_synchronized_handoff(self.app, self.start, (self.surface,), self.events)

    def tearDown(self):
        self.gate.close()
        self.surface.close()
        self.gate.deleteLater()
        self.surface.deleteLater()
        self.app.processEvents()

    def test_start_waits_for_native_arrival_and_paint_ack_is_deferred(self):
        from PySide6.QtCore import QEvent
        self.events.signal.assert_called_once_with("ready")
        self.gate.check()
        self.start.assert_not_called()
        self.events.revealed.return_value = True
        self.gate.check()
        self.gate.check()
        self.start.assert_called_once_with()
        self.surface.show()
        self.gate.eventFilter(self.surface, QEvent(QEvent.Type.Paint))
        self.events.signal.assert_called_once_with("ready")
        self.app.processEvents()
        self.assertEqual([call.args[0] for call in self.events.signal.call_args_list], ["ready", "painted"])
        self.events.close.assert_called_once_with()

    def test_hidden_surface_does_not_acknowledge_a_frame(self):
        from PySide6.QtCore import QEvent
        self.events.revealed.return_value = True
        self.gate.check()
        self.gate.eventFilter(self.surface, QEvent(QEvent.Type.Paint))
        self.app.processEvents()
        self.events.signal.assert_called_once_with("ready")

    def test_captured_entry_hidden_before_callback_waits_for_destination_paint(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtWidgets import QWidget
        destination = QWidget()
        destination.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.gate.close()
        self.events.close.reset_mock()
        self.events.signal.reset_mock()
        self.gate = start_synchronized_handoff(self.app, self.start, (self.surface, destination), self.events)
        try:
            self.events.revealed.return_value = True
            self.gate.check()
            self.surface.show()
            self.gate.eventFilter(self.surface, QEvent(QEvent.Type.Paint))
            self.surface.hide()
            self.app.processEvents()
            self.events.signal.assert_called_once_with("ready")
            destination.show()
            self.gate.eventFilter(destination, QEvent(QEvent.Type.Paint))
            self.app.processEvents()
            self.assertEqual([call.args[0] for call in self.events.signal.call_args_list], ["ready", "painted"])
        finally:
            self.gate.close()
            destination.close()
            destination.deleteLater()

    def test_abandoned_installer_expires_without_hiding_or_double_starting_app(self):
        self.gate.expire()
        self.gate.expire()
        self.start.assert_called_once_with()
        self.events.close.assert_called_once_with()
        self.assertIsNone(self.gate.events)
        self.assertFalse(self.gate.poll.isActive())
        self.assertFalse(self.gate.deadline.isActive())

    def test_quit_before_reveal_releases_handles_without_starting_app(self):
        self.gate.close()
        self.gate.close()
        self.start.assert_not_called()
        self.events.close.assert_called_once_with()


class NativeHandoffEventTests(unittest.TestCase):
    def test_existing_native_events_deliver_ready_reveal_and_first_frame(self):
        import ctypes
        import os
        import sys
        import time
        if sys.platform != "win32":
            self.skipTest("Win32 event acceptance requires Windows")
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        token = str(os.getpid()) + "-" + str(time.monotonic_ns() % 10000000000)
        handles = {}
        connection = None
        try:
            # An app cannot invent a handshake and block normal startup.
            self.assertIsNone(InstallerHandoffEvents.connect(token))
            for role in ("ready", "reveal", "painted"):
                handles[role] = kernel.CreateEventW(None, True, False, handoff_event_name(token, role))
                self.assertTrue(handles[role])
            connection = InstallerHandoffEvents.connect(token)
            self.assertIsNotNone(connection)
            self.assertFalse(connection.revealed())
            self.assertTrue(connection.signal("ready"))
            self.assertEqual(kernel.WaitForSingleObject(handles["ready"], 0), 0)
            kernel.SetEvent(handles["reveal"])
            self.assertTrue(connection.revealed())
            self.assertTrue(connection.signal("painted"))
            self.assertEqual(kernel.WaitForSingleObject(handles["painted"], 0), 0)
        finally:
            if connection is not None:
                connection.close()
                connection.close()
            for handle in handles.values():
                kernel.CloseHandle(handle)
