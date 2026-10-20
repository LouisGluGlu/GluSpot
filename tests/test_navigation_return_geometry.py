"""Controller-driven returns keep one global page rectangle during handoff.

The older overlay tests called request_start directly, so they did not expose
native Show/Move events feeding back into the controller's shared placement.
This fixture uses actual Qt pages and the player back action, with the existing
offline service/task fakes; no account, network, settings or audio is involved.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import delete, isValid

from api.online_music import OnlineSong
from local.island_stack_visual import IslandStackVisual
from local.music_library_controller import MusicLibraryController
from local.music_library_window import MusicLibraryWindow
from local.music_surface_window import MusicSurfaceWindow
from local.playback_entry_window import PlaybackEntryWindow
from tests.test_music_centered_layout import _Owner
from tests.test_music_library_controller import Player, Service, track
from tests.test_playback_entry_controller import FakeSettings, FakeTasks


class _MappingLibrary(MusicLibraryWindow):
    """Model a platform mapping move synchronously inside QWidget.show()."""
    mapping_move = None

    def show(self):
        super().show()
        if self.mapping_move is not None:
            target, self.mapping_move = self.mapping_move, None
            self.move(target)


class NavigationReturnGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.owner = _Owner()
        self.owner.settings = self.settings
        self.owner._music_surface_active = True
        self.owner._music_library_active = False
        self.owner._tray_hidden = False
        self.owner._application_quitting = False
        self.owner.data_provider = SimpleNamespace(_owned_active=lambda: True)
        self.owner._navigation_surface_blocks_island = lambda: self.owner._music_library_active
        # Reuse the actual hide/ownership implementation, rather than the old
        # fake Island method which left a real playback page visible behind it.
        self.owner._restore_island_from_music_surface = MethodType(
            IslandStackVisual._restore_island_from_music_surface, self.owner)
        self.surface = MusicSurfaceWindow(self.owner)
        self.owner.music_fullscreen_overlay = self.surface
        self.window = _MappingLibrary()
        self.entry_window = PlaybackEntryWindow()
        # Deterministic event-injection cases isolate Qt/controller ownership
        # from unrelated window-manager placement. The dedicated native case
        # below retains actual exposure and observes the complete event loop.
        native = self._testMethodName == "test_native_event_loop_return_keeps_destination_and_source_ownership"
        for page in (self.owner, self.surface, self.window, self.entry_window):
            page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, not native)
        self.entry = SimpleNamespace(window=self.entry_window, _navigation=None)
        self.service, self.player = Service(), Player()
        self.player.track = track(OnlineSong("netease", "return-fixture", "Return fixture"))
        self.controller = MusicLibraryController(self.settings, self.service,
            self.player, self.owner, self.entry, window=self.window,
            tasks=FakeTasks(), art_tasks=FakeTasks())
        self.controller._source = "netease"
        self.window.set_account(self.service.accounts["netease"])
        self.owner._music_library_back_callback = self.controller.return_from_music_surface
        self.nav = self.controller._navigation
        self.nav.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, not native)
        # Keep every native client inside this screen at both DPR 1 and 2.
        available = self.app.primaryScreen().availableGeometry()
        self.expected = QRect(available.x() + 76, available.y() + 64,
            min(720, available.width() - 140), min(500, available.height() - 120))
        self.window.setGeometry(self.expected.translated(35, 28))
        self.surface._normal_geometry = QRect(self.expected)
        self.surface._fullscreen = False
        self.surface.setGeometry(self.expected)
        self.surface.show()
        self.app.processEvents()
        # Platform initial exposure may move new windows; establish the user's
        # final playback rectangle after that separate initial mapping.
        self.surface.setGeometry(self.expected)
        self.surface._normal_geometry = QRect(self.expected)
        self.app.processEvents()

    def tearDown(self):
        self.controller.close()
        self.surface.shutdown()
        for widget in (self.window, self.entry_window, self.nav, self.surface, self.owner):
            if isValid(widget):
                widget.close()
                widget.deleteLater()
        self.app.processEvents()

    @staticmethod
    def global_client_rect(widget):
        return QRect(widget.mapToGlobal(QPoint()), widget.size())

    def assert_page_geometry(self, label):
        self.assertEqual(self.global_client_rect(self.window), self.expected, label)
        self.assertEqual(self.controller._library_geometry, self.expected, label)
        self.assertEqual(self.owner._music_page_geometry, self.expected, label)
        self.assertEqual(self.surface._normal_geometry, self.expected, label)

    def start_return(self):
        self.assertTrue(self.surface.isVisible())
        self.assertTrue(self.surface.return_to_library())
        self.assertFalse(self.surface.isVisible(), "the old native playback page must be hidden")
        self.assertFalse(self.owner.isVisible(), "the island must not be a second page underneath")
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.nav.isVisible())
        self.assertIs(self.nav._pending_start[2], self.window)
        self.assertEqual(self.window.windowOpacity(), 0.)
        self.assertFalse(self.window.isEnabled())
        self.assert_native_guard_owners(self.window)

    def assert_native_guard_owners(self, *pages):
        guard = self.nav._native_geometry_guard
        if sys.platform != "win32":
            self.assertFalse(self.nav._native_geometry_hwnds)
            self.assertFalse(guard._owners)
            self.assertFalse(guard._installed)
            return
        self.assertEqual(set(self.nav._native_geometry_hwnds), set(pages))
        self.assertEqual(set(guard._owners), set(self.nav._native_geometry_hwnds.values()))
        self.assertEqual(guard._installed, bool(pages))
        for page in pages:
            self.assertEqual(self.nav._native_geometry_hwnds[page], int(page.windowHandle().winId()))

    def prepare_and_finish(self, *, wait_settle=True):
        # A real hold-layer paint is the precondition for deferred target
        # capture; do not bypass request_start with the synchronous API.
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        self.assertTrue(self.nav._origin_presented)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assertIs(self.nav._destination, self.window)
        self.assert_native_guard_owners(self.window)
        self.assertEqual(self.nav._target_frame.geometry, self.expected)
        self.assert_page_geometry("prepared target")
        for fraction in (0., .2, .5, .8, 1.):
            self.nav._advance(fraction)
            self.assert_page_geometry(f"animation fraction {fraction}")
            self.assertFalse(self.surface.isVisible())
            self.assertTrue(self.window.isVisible())
        self.nav._finished()
        self.assertFalse(self.nav.isVisible())
        self.assertIs(self.nav._settling_destination, self.window)
        self.assertEqual(set(self.nav._native_geometry_handles), {self.window})
        self.assert_native_guard_owners(self.window)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isEnabled())
        self.assertEqual(self.window.windowOpacity(), 1.)
        self.assertFalse(self.surface.isVisible())
        self.assert_page_geometry("finished")
        if wait_settle:
            QTest.qWait(230)
            self.assert_settling_released()

    def assert_settling_released(self):
        self.assertIsNone(self.nav._settling_destination)
        self.assertFalse(self.nav._placement_settle_timer.isActive())
        self.assertFalse(self.nav._native_geometry_handles)
        self.assertFalse(self.nav.destination_geometry(self.window).isValid())
        self.assert_native_guard_owners()

    def test_real_back_action_returns_to_player_rectangle_and_hides_source(self):
        self.start_return()
        self.assert_page_geometry("return action")
        self.prepare_and_finish()

    def test_native_show_mapping_move_cannot_replace_player_rectangle(self):
        self.window.mapping_move = self.expected.topLeft() + QPoint(8, 17)
        self.start_return()
        self.assert_page_geometry("synchronous native mapping move")
        self.prepare_and_finish()
        QTest.qWait(230)  # Existing 0/75/200 ms native repair callbacks.
        self.assert_page_geometry("all placement callbacks finished")

    def test_first_show_restores_destination_before_it_can_publish_stale_geometry(self):
        # A visible stale library rectangle must never become the capture
        # authority when the playback page was subsequently moved/resized.
        stale = self.expected.translated(35, 28)
        self.assertEqual(self.window.geometry(), stale)
        self.start_return()
        self.assert_page_geometry("first show of stale hidden library")
        self.prepare_and_finish()

    def test_pending_target_move_cannot_change_the_captured_return_rectangle(self):
        self.start_return()
        self.window.move(self.expected.topLeft() + QPoint(21, 14))
        self.assert_page_geometry("native move while target capture is pending")
        self.prepare_and_finish()

    def test_native_global_drift_is_repaired_even_when_qt_geometry_is_unchanged(self):
        self.start_return()
        self.assertEqual(self.window.geometry(), self.expected)
        handle = self.window.windowHandle()
        self.assertIsNotNone(handle)
        actual = self.expected.topLeft() + QPoint(41, 29)
        native_position = [QPoint(actual)]
        calls = []
        original_set_position = handle.setPosition

        def set_position(position):
            calls.append(QPoint(position))
            native_position[0] = QPoint(position)
            original_set_position(position)

        # QWidget's cached geometry can already equal the target while the
        # platform HWND was mapped elsewhere. Observe real QWindow calls and
        # model that independent native position, rather than a Qt Move event.
        with patch.object(self.window, "mapToGlobal",
                side_effect=lambda point: native_position[0] + point), \
                patch.object(handle, "setPosition", side_effect=set_position):
            self.assertNotEqual(self.global_client_rect(self.window), self.expected)
            self.nav._pin_destination_geometry(self.window)
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(calls[:2], [actual, self.expected.topLeft()])
            self.assertEqual(self.global_client_rect(self.window), self.expected)
        self.assert_page_geometry("native coordinate repair")
        self.prepare_and_finish()

    def test_fullscreen_source_returns_to_normal_player_geometry(self):
        fullscreen = self.app.primaryScreen().geometry()
        self.surface._fullscreen = True
        self.surface.setGeometry(fullscreen)
        self.surface._normal_geometry = QRect(self.expected)
        self.start_return()
        self.assertEqual(self.nav._origin_frame.geometry, fullscreen)
        self.assertEqual(self.settings.get("v2_library_last_playback_surface"), "fullscreen")
        self.assert_page_geometry("normal page rectangle from fullscreen source")
        self.prepare_and_finish()

    def assert_display_change_releases_geometry(self, event_type, *, prepared):
        self.start_return()
        if prepared:
            image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            self.nav.render(image)
            self.nav._prepare_pending()
            self.nav._animation.stop()
        self.assertTrue(self.nav.destination_geometry(self.window).isValid())
        self.app.sendEvent(self.window, QEvent(event_type))
        self.assertFalse(self.nav.isVisible())
        self.assertIsNone(self.nav._pending_start)
        self.assertIsNone(self.nav._destination)
        self.assertFalse(self.nav._native_geometry_handles)
        self.assert_native_guard_owners()
        self.assertFalse(self.nav.destination_geometry(self.window).isValid())
        self.assertEqual(self.window.windowOpacity(), 1.)
        self.assertTrue(self.window.isEnabled())
        self.assertFalse(self.surface.isVisible())
        # The new work-area/window-state placement becomes authoritative;
        # stale native-map settle callbacks must not pin the old screen rect.
        self.expected = self.expected.translated(13, 11)
        self.window.setGeometry(self.expected)
        QTest.qWait(230)
        self.assert_page_geometry("display change owns its new rectangle")

    def test_screen_change_releases_pending_destination_geometry(self):
        self.assert_display_change_releases_geometry(QEvent.Type.ScreenChangeInternal, prepared=False)

    def test_dpr_change_releases_prepared_destination_geometry(self):
        self.assert_display_change_releases_geometry(QEvent.Type.DevicePixelRatioChange, prepared=True)

    def assert_cancelled_handoff_restores_live_destination(self):
        self.assertFalse(self.nav.isVisible())
        self.assertEqual(self.nav._animation.state(), QAbstractAnimation.State.Stopped)
        self.assertIsNone(self.nav._pending_start)
        self.assertIsNone(self.nav._destination)
        self.assertIsNone(self.nav._origin_frame)
        self.assertIsNone(self.nav._target_frame)
        self.assertFalse(self.nav._native_geometry_handles)
        self.assert_native_guard_owners()
        self.assertFalse(self.nav.destination_geometry(self.window).isValid())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isEnabled())
        self.assertEqual(self.window.windowOpacity(), 1.)
        self.assertFalse(self.surface.isVisible())
        self.assertFalse(self.owner.isVisible())

    def inject_screen_change_on_transparent_destination(self):
        original = self.window.setWindowOpacity
        triggered = []

        def set_opacity(value):
            original(value)
            if value == 0. and not triggered:
                triggered.append(True)
                self.app.sendEvent(self.window, QEvent(QEvent.Type.ScreenChangeInternal))

        return patch.object(self.window, "setWindowOpacity", side_effect=set_opacity), triggered

    def test_screen_change_inside_request_alpha_mapping_cannot_show_orphan_hold_layer(self):
        mapping, triggered = self.inject_screen_change_on_transparent_destination()
        with mapping:
            self.assertTrue(self.surface.return_to_library())
        self.assertEqual(triggered, [True])
        self.assert_cancelled_handoff_restores_live_destination()
        self.app.processEvents()
        self.assert_cancelled_handoff_restores_live_destination()

    def test_screen_change_inside_prepared_alpha_mapping_cannot_restart_cancelled_animation(self):
        self.start_return()
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        self.assertTrue(self.nav._origin_presented)
        mapping, triggered = self.inject_screen_change_on_transparent_destination()
        with mapping:
            self.nav._prepare_pending()
        self.assertEqual(triggered, [True])
        self.assert_cancelled_handoff_restores_live_destination()
        self.app.processEvents()
        self.assert_cancelled_handoff_restores_live_destination()

    def inject_screen_change_inside_native_observer_pin(self):
        original_observe = self.nav._observe_native_geometry
        original_pin = self.nav._pin_destination_geometry
        triggered = []

        def observe(destination):
            def pin(window):
                original_pin(window)
                if window is destination and not triggered:
                    triggered.append(True)
                    self.app.sendEvent(destination, QEvent(QEvent.Type.ScreenChangeInternal))
            with patch.object(self.nav, "_pin_destination_geometry", side_effect=pin):
                original_observe(destination)

        return patch.object(self.nav, "_observe_native_geometry", side_effect=observe), triggered

    def test_screen_change_inside_request_observer_pin_cannot_disable_cancelled_destination(self):
        observing, triggered = self.inject_screen_change_inside_native_observer_pin()
        with observing:
            self.assertTrue(self.surface.return_to_library())
        self.assertEqual(triggered, [True])
        self.assert_cancelled_handoff_restores_live_destination()
        self.app.processEvents()
        self.assert_cancelled_handoff_restores_live_destination()

    def test_screen_change_inside_prepared_observer_pin_cannot_disable_cancelled_destination(self):
        self.start_return()
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        observing, triggered = self.inject_screen_change_inside_native_observer_pin()
        with observing:
            self.nav._prepare_pending()
        self.assertEqual(triggered, [True])
        self.assert_cancelled_handoff_restores_live_destination()
        self.app.processEvents()
        self.assert_cancelled_handoff_restores_live_destination()

    def test_reverse_releases_old_native_handle_and_close_releases_current(self):
        self.start_return()
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assertEqual(set(self.nav._native_geometry_handles), {self.window})
        self.assert_native_guard_owners(self.window)
        self.nav._advance(.4)
        frame = self.nav.origin_frame(self.window, separate_cover=True)
        self.assertFalse(self.nav._native_geometry_handles)
        self.assert_native_guard_owners()
        self.surface.show()
        self.surface.setGeometry(self.expected)
        self.assertTrue(self.nav.request_start(frame, self.surface))
        self.assertEqual(set(self.nav._native_geometry_handles), {self.surface})
        self.assert_native_guard_owners(self.surface)
        # Signals from the formerly owned library handle must no longer call
        # the observer, even while another destination is being prepared.
        with patch.object(self.nav, "_pin_destination_geometry") as pin:
            self.window.windowHandle().setPosition(self.expected.topLeft() + QPoint(7, 9))
            self.assertFalse(any(call.args[0] is self.window for call in pin.call_args_list))
        self.nav.close()
        self.assertFalse(self.nav._native_geometry_handles)
        self.assert_native_guard_owners()
        self.assertIsNone(self.nav._pending_start)
        self.assertIsNone(self.nav._destination)
        self.assertEqual(self.surface.windowOpacity(), 1.)
        self.assertTrue(self.surface.isEnabled())

    def test_user_move_after_return_owns_position_over_delayed_mapping_repairs(self):
        self.start_return()
        self.prepare_and_finish(wait_settle=False)
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, pos=QPoint(210, 140))
        self.assert_settling_released()
        moved = self.expected.translated(19, 23)
        self.window.setGeometry(moved)
        self.expected = moved
        QTest.qWait(230)
        self.assert_page_geometry("user's new placement")

    def test_late_transparent_mapping_move_is_repaired_before_live_page_reveal(self):
        self.start_return()
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assertEqual(self.nav._target_frame.geometry, self.expected)
        QTest.qWait(230)
        # Windows can deliver another native mapping relocation after all
        # three legacy 0/75/200 ms repair callbacks, while opacity remains 0.
        self.assertEqual(self.window.windowOpacity(), 0.)
        self.window.move(self.expected.topLeft() + QPoint(47, 31))
        self.nav._advance(1.)
        self.nav._finished()
        self.app.processEvents()
        self.assert_page_geometry("actual destination at final opacity handoff")
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.windowOpacity(), 1.)
        self.assertFalse(self.surface.isVisible())
        self.assertFalse(self.nav.isVisible())
        self.assertIs(self.nav._settling_destination, self.window)
        self.assertEqual(set(self.nav._native_geometry_handles), {self.window})
        QTest.qWait(230)
        self.assert_settling_released()

    def test_queued_native_mapping_after_opacity_restore_cannot_publish_wrong_live_rectangle(self):
        self.start_return()
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        original_set_opacity = self.window.setWindowOpacity
        mapped = []

        def set_opacity(value):
            original_set_opacity(value)
            if value == 1. and not mapped:
                mapped.append(True)
                # Model a Windows alpha remapping event delivered after the
                # setter and overlay completion return, without user input.
                QTimer.singleShot(1, lambda: self.window.move(self.expected.topLeft() + QPoint(41, 29)))

        with patch.object(self.window, "setWindowOpacity", side_effect=set_opacity):
            self.nav._advance(1.)
            self.nav._finished()
        self.assertEqual(mapped, [True])
        self.assertEqual(self.window.windowOpacity(), 1.)
        self.assertFalse(self.nav.isVisible())
        self.assert_page_geometry("synchronous alpha handoff")
        QTest.qWait(25)
        self.assert_page_geometry("queued native remapping after live page reveal")
        self.assertFalse(self.surface.isVisible())
        self.assertTrue(self.window.isEnabled())
        self.assertIs(self.nav._settling_destination, self.window)
        QTest.qWait(230)
        self.assert_settling_released()

    def test_settling_display_change_releases_old_screen_geometry(self):
        self.start_return()
        self.prepare_and_finish(wait_settle=False)
        self.app.sendEvent(self.window, QEvent(QEvent.Type.ScreenChangeInternal))
        self.assert_settling_released()
        self.expected = self.expected.translated(17, 12)
        self.window.setGeometry(self.expected)
        QTest.qWait(230)
        self.assert_page_geometry("new screen placement after settling release")

    def test_settling_hide_releases_native_observer_and_timer(self):
        self.start_return()
        self.prepare_and_finish(wait_settle=False)
        self.window.hide()
        self.assert_settling_released()
        self.assertFalse(self.nav.isVisible())
        self.assertTrue(self.window.isEnabled())
        self.assertEqual(self.window.windowOpacity(), 1.)

    def test_settling_timeout_repairs_native_drift_before_releasing_cached_qt_rectangle(self):
        self.start_return()
        self.prepare_and_finish(wait_settle=False)
        self.assertEqual(self.window.geometry(), self.expected)
        handle = self.window.windowHandle()
        actual = self.expected.topLeft() + QPoint(41, 29)
        native_position = [QPoint(actual)]
        calls = []
        original_set_position = handle.setPosition

        def set_position(position):
            calls.append(QPoint(position))
            native_position[0] = QPoint(position)
            original_set_position(position)

        with patch.object(self.window, "mapToGlobal",
                side_effect=lambda point: native_position[0] + point), \
                patch.object(handle, "setPosition", side_effect=set_position):
            # Model native movement before its queued Qt Move signal: the
            # timer must verify actual coordinates while it still owns them.
            self.assertNotEqual(self.global_client_rect(self.window), self.expected)
            self.nav._finish_settling_destination()
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(calls[:2], [actual, self.expected.topLeft()])
            self.assertEqual(self.global_client_rect(self.window), self.expected)
        self.assert_settling_released()
        self.assert_page_geometry("settle deadline repairs native position before dropping its lease")

    def test_settling_destination_destruction_releases_observer_and_timer(self):
        self.start_return()
        self.prepare_and_finish(wait_settle=False)
        delete(self.window)
        # The library controller's normal close cleanup still needs a live
        # closable fixture page; its task queues are otherwise unchanged.
        self.window = self.controller.window = _MappingLibrary()
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.assert_settling_released()
        self.assertFalse(self.nav.isVisible())
        self.assertIsNone(self.nav._origin_frame)
        self.assertIsNone(self.nav._target_frame)

    def test_replacement_request_releases_old_settling_destination(self):
        self.start_return()
        self.prepare_and_finish(wait_settle=False)
        frame = self.nav.origin_frame(self.window, separate_cover=True)
        self.surface.show()
        self.surface.setGeometry(self.expected)
        self.assertTrue(self.nav.request_start(frame, self.surface))
        self.assertIsNone(self.nav._settling_destination)
        self.assertFalse(self.nav._placement_settle_timer.isActive())
        self.assertEqual(set(self.nav._native_geometry_handles), {self.surface})
        self.assert_native_guard_owners(self.surface)
        self.assertFalse(self.nav.destination_geometry(self.window).isValid())
        self.nav.cancel()
        self.assertFalse(self.nav._native_geometry_handles)
        self.assert_native_guard_owners()

    def test_screen_change_inside_live_alpha_restore_keeps_new_screen_placement_and_input(self):
        self.start_return()
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        original = self.window.setWindowOpacity
        relocated = self.expected.translated(24, 18)
        triggered = []

        def set_opacity(value):
            original(value)
            if value == 1. and not triggered:
                triggered.append(True)
                self.app.sendEvent(self.window, QEvent(QEvent.Type.ScreenChangeInternal))
                self.window.setGeometry(relocated)

        with patch.object(self.window, "setWindowOpacity", side_effect=set_opacity):
            self.nav._advance(1.)
            self.nav._finished()
        self.assertEqual(triggered, [True])
        self.expected = relocated
        self.assert_cancelled_handoff_restores_live_destination()
        self.assert_settling_released()
        self.assert_page_geometry("screen change inside native alpha restore")
        QTest.qWait(230)
        self.assert_page_geometry("old restore stack and timers cannot repin prior screen")

    def test_replacement_inside_live_alpha_restore_survives_old_cancel_stack(self):
        self.start_return()
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        original = self.window.setWindowOpacity
        triggered = []
        accepted = []

        def set_opacity(value):
            original(value)
            if value == 1. and not triggered:
                triggered.append(True)
                frame = self.nav.origin_frame(self.window, separate_cover=True)
                accepted.append(self.nav.request_start(frame, self.window))

        with patch.object(self.window, "setWindowOpacity", side_effect=set_opacity):
            self.nav._advance(1.)
            self.nav._finished()
        self.assertEqual(triggered, [True])
        self.assertEqual(accepted, [True])
        self.assertTrue(self.nav.isVisible())
        self.assertIsNotNone(self.nav._origin_frame)
        self.assertIs(self.nav._pending_start[2], self.window)
        self.assertEqual(set(self.nav._native_geometry_handles), {self.window})
        self.assert_native_guard_owners(self.window)
        self.assertEqual(self.window.windowOpacity(), 0.)
        self.assertFalse(self.window.isEnabled())
        self.assertFalse(self.surface.isVisible())
        self.nav.render(image)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assertIs(self.nav._destination, self.window)
        self.assertIsNotNone(self.nav._target_frame)
        self.assertTrue(self.nav._target_frame.geometry.isValid())
        self.nav._advance(.5)
        self.assertTrue(self.nav.isVisible())
        self.assertEqual(self.window.windowOpacity(), 0.)
        self.nav._advance(1.)
        self.nav._finished()
        self.assertIsNone(self.nav._pending_start)
        self.assertIsNone(self.nav._origin_frame)
        self.assertIsNone(self.nav._target_frame)
        self.assertFalse(self.nav.isVisible())
        self.assertEqual(self.window.windowOpacity(), 1.)
        self.assertTrue(self.window.isEnabled())
        self.assert_page_geometry("replacement completes with live input and original opacity")
        QTest.qWait(230)
        self.assert_settling_released()

    def test_native_event_loop_return_keeps_destination_and_source_ownership(self):
        source_cover = QRectF(self.surface.navigation_cover_state()[0])
        source_before = {
            "qt": QRect(self.surface.geometry()),
            "global": self.global_client_rect(self.surface),
            "normal": QRect(self.surface._normal_geometry),
            "cover": source_cover,
        }
        observations = []
        prepared_events = []
        source_captures = []
        started = time.monotonic()
        original_start = self.nav._start_prepared
        original_source_cover = self.surface.navigation_cover_state

        def source_cover_trace():
            state = original_source_cover()
            source_captures.append({
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "qt": QRect(self.surface.geometry()),
                "global": self.global_client_rect(self.surface),
                "cover": QRectF(state[0]),
            })
            return state

        def frame_trace(frame):
            if frame is None:
                return None
            return {"geometry": QRect(frame.geometry), "visible_geometry": QRectF(frame.visible_geometry),
                "cover_rect": QRectF(frame.cover_rect), "cover_angle": frame.cover_angle,
                "velocity": frame.velocity}

        def prepared_trace(frame, destination, target, **kwargs):
            before = {
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "origin": frame_trace(frame), "target": frame_trace(target),
                "destination_qt": QRect(destination.geometry()),
                "destination_global": self.global_client_rect(destination),
                "source_qt": QRect(self.surface.geometry()),
                "source_global": self.global_client_rect(self.surface),
            }
            result = original_start(frame, destination, target, **kwargs)
            prepared_events.append({"before": before, "after": {
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "started": result,
                "origin": frame_trace(self.nav._origin_frame),
                "target": frame_trace(self.nav._target_frame),
                "overlay_qt": QRect(self.nav.geometry()),
                "overlay_global": self.global_client_rect(self.nav),
                "window_pose": QRectF(self.nav._window_rect_at()) if self.nav._origin_frame else QRectF(),
                "cover_pose": QRectF(self.nav._cover_rect_at()) if self.nav._origin_frame else QRectF(),
                "destination_qt": QRect(destination.geometry()),
                "destination_global": self.global_client_rect(destination),
            }})
            return result

        sampler = QTimer()
        sampler.setInterval(10)
        sampler.timeout.connect(lambda: observations.append((
            self.global_client_rect(self.window), self.surface.isVisible(),
            self.window.isVisible(), self.owner.isVisible(), {
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "qt_geometry": QRect(self.window.geometry()),
                "destination_opacity": self.window.windowOpacity(),
                "overlay_visible": self.nav.isVisible(),
                "fraction": self.nav._fraction,
                "pending": self.nav._pending_start is not None,
                "active_destination": self.nav._destination is self.window,
                "overlay_geometry": QRect(self.nav.geometry()),
                "overlay_actual_global": self.global_client_rect(self.nav),
                "window_pose": QRectF(self.nav._window_rect_at()) if self.nav._origin_frame else QRectF(),
                "cover_pose": QRectF(self.nav._cover_rect_at()) if self.nav._origin_frame else QRectF(),
                "placement_epoch": self.controller._library_placement_epoch,
                "shared": QRect(self.owner._music_page_geometry),
                "library_geometry": QRect(self.controller._library_geometry),
                "normal_geometry": QRect(self.surface._normal_geometry),
                # Derive this from the local button and the leased page
                # rectangle, independently of a transparent HWND relocation.
                "local_target_cover": QRectF(self.window.now_playing_button.geometry().adjusted(1, 1, -1, -1)
                    .translated(self.expected.topLeft())),
                "origin_geometry": QRect(self.nav._origin_frame.geometry)
                    if self.nav._origin_frame is not None else QRect(),
                "target_geometry": QRect(self.nav._target_frame.geometry)
                    if self.nav._target_frame is not None else QRect(),
                "origin_cover_rect": QRectF(self.nav._origin_frame.cover_rect)
                    if self.nav._origin_frame is not None else QRectF(),
                "target_cover_rect": QRectF(self.nav._target_frame.cover_rect)
                    if self.nav._target_frame is not None else QRectF(),
            })))
        with patch.object(self.nav, "_start_prepared", side_effect=prepared_trace), \
                patch.object(self.surface, "navigation_cover_state", side_effect=source_cover_trace):
            self.start_return()
            sampler.start()
            try:
                # Let real expose, deferred target capture, animation, native move
                # events and final opacity restoration run without manual advance.
                QTest.qWait(850)
            finally:
                sampler.stop()
        self.assertGreater(len(observations), 5)
        # Qt's initial native mapping may settle after the fixture's final
        # setGeometry. The origin is the page actually visible at the click,
        # while the normal shared rectangle remains the return destination.
        source_geometry = source_before["global"]
        self.assertEqual(source_before["qt"], source_geometry)
        self.assertEqual(len(source_captures), 1)
        self.assertEqual(source_captures[0]["qt"], source_geometry)
        self.assertEqual(source_captures[0]["global"], source_geometry)
        # Record the unmodified live getter at capture time: its breathing
        # scale may cross an aligned-pixel boundary since source_before.
        source_cover = source_captures[0]["cover"]
        transparent_drifts = sum(rect != self.expected and state["destination_opacity"] == 0.
            for rect, _source, _destination, _island, state in observations)
        trace_path = os.environ.get("GLUSPOT_NAVIGATION_RETURN_TRACE", "")
        if trace_path:
            path = Path(trace_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "expected": self.expected, "dpr": self.window.devicePixelRatioF(),
                "source_before": source_before, "source_captures": source_captures,
                "prepared_events": prepared_events,
                "transparent_destination_native_drift_samples": transparent_drifts,
                "samples": observations, "final_global": self.global_client_rect(self.window),
                "final_qt": self.window.geometry(), "final_opacity": self.window.windowOpacity(),
                "final_overlay_visible": self.nav.isVisible(),
            }, default=lambda value: [value.x(), value.y(), value.width(), value.height()]
                if isinstance(value, (QRect, QRectF)) else repr(value), indent=2), encoding="utf-8")
        self.assertEqual(len(prepared_events), 1, "the deferred target must actually reach prepared start")
        self.assertTrue(prepared_events[0]["after"]["started"], "the prepared animation must start")
        self.assertTrue(any(0. < state["fraction"] < 1. and state["overlay_visible"]
            and state["active_destination"] for *_pages, state in observations),
            "the native event loop must observe an active intermediate animation frame")
        for index, (rect, source_visible, destination_visible, island_visible, state) in enumerate(observations):
            label = f"native checkpoint {index}: {state}"
            self.assertEqual(state["qt_geometry"], self.expected, label)
            self.assertEqual(state["shared"], self.expected, label)
            self.assertEqual(state["library_geometry"], self.expected, label)
            self.assertEqual(state["normal_geometry"], self.expected, label)
            if state["destination_opacity"] > 0.:
                self.assertEqual(rect, self.expected, label)
            else:
                # Native alpha mapping may briefly relocate the fully
                # transparent real destination. Its HWND is not the visible
                # animation: assert every visible frozen layer independently,
                # and retain the hidden drift count in the diagnostic trace.
                self.assertTrue(state["overlay_visible"], label)
                self.assertEqual(state["overlay_actual_global"], state["overlay_geometry"], label)
                self.assertEqual(state["origin_geometry"], source_geometry, label)
                target_geometry = self.expected if state["active_destination"] else source_geometry
                self.assertEqual(state["target_geometry"], target_geometry, label)
                self.assertEqual(state["overlay_geometry"], source_geometry.united(target_geometry), label)
                self.assertEqual(state["origin_cover_rect"], source_cover, label)
                target_cover = state["local_target_cover"] if state["active_destination"] else source_cover
                self.assertEqual(state["target_cover_rect"], target_cover, label)
                fraction = state["fraction"]
                smooth = fraction * fraction * (3. - 2. * fraction)
                progress = smooth + .045 * math.sin(smooth * math.pi * 2.) * math.sin(smooth * math.pi)
                expected_window = QRectF(*(a + (b - a) * progress for a, b in zip(
                    (source_geometry.x(), source_geometry.y(), source_geometry.width(), source_geometry.height()),
                    (target_geometry.x(), target_geometry.y(), target_geometry.width(), target_geometry.height()))))
                self.assertEqual(state["window_pose"], expected_window, label)
                expected_cover = QRectF(*(a + (b - a) * progress for a, b in zip(
                    (source_cover.x(), source_cover.y(), source_cover.width(), source_cover.height()),
                    (target_cover.x(), target_cover.y(), target_cover.width(), target_cover.height()))))
                self.assertEqual(state["cover_pose"], expected_cover, label)
            self.assertFalse(source_visible, f"old playback page at checkpoint {index}")
            self.assertTrue(destination_visible, f"missing destination at checkpoint {index}")
            self.assertFalse(island_visible, f"duplicate island at checkpoint {index}")
        self.assertFalse(self.nav.isVisible())
        self.assertTrue(self.window.isEnabled())
        self.assertEqual(self.window.windowOpacity(), 1.)
        self.assert_page_geometry("real event-loop completion")


if __name__ == "__main__":
    unittest.main()
