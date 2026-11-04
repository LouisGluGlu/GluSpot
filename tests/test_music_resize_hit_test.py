"""Frameless edges retain native cursors and the existing responsive resize path."""
from __future__ import annotations

import ctypes
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.music_navigation_native import _MSG
from local.music_resize_hit_test import (
    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT, HTCLIENT, HTLEFT, HTRIGHT, HTTOP,
    HTTOPLEFT, HTTOPRIGHT, NativeMusicResizeMixin, WM_ENTERSIZEMOVE,
    WM_EXITSIZEMOVE, WM_NCHITTEST, WM_SIZING, _RECT, constrain_native_rect,
    native_resize_hit, resize_cursor, resize_edges,
)


CASES = (
    (2, 250, {"left"}, HTLEFT, Qt.CursorShape.SizeHorCursor),
    (957, 250, {"right"}, HTRIGHT, Qt.CursorShape.SizeHorCursor),
    (480, 2, {"top"}, HTTOP, Qt.CursorShape.SizeVerCursor),
    (480, 537, {"bottom"}, HTBOTTOM, Qt.CursorShape.SizeVerCursor),
    (2, 2, {"left", "top"}, HTTOPLEFT, Qt.CursorShape.SizeFDiagCursor),
    (957, 2, {"right", "top"}, HTTOPRIGHT, Qt.CursorShape.SizeBDiagCursor),
    (2, 537, {"left", "bottom"}, HTBOTTOMLEFT, Qt.CursorShape.SizeBDiagCursor),
    (957, 537, {"right", "bottom"}, HTBOTTOMRIGHT, Qt.CursorShape.SizeFDiagCursor),
)


class ResizeMappingTests(unittest.TestCase):
    def test_eight_directions_match_qt_and_win32_at_both_dprs(self):
        for dpr in (1., 1.25, 2.):
            for x, y, expected, hit, cursor in CASES:
                with self.subTest(dpr=dpr, edges=expected):
                    edges = resize_edges(x, y, 960, 540, dpr=dpr)
                    native = resize_edges(x*dpr, y*dpr, 960*dpr, 540*dpr,
                                          dpr=dpr, physical=True)
                    self.assertEqual(edges, expected)
                    self.assertEqual(native, edges)
                    self.assertEqual(native_resize_hit(edges), hit)
                    self.assertEqual(resize_cursor(edges), cursor)

    def test_center_and_outside_points_are_not_resize_targets(self):
        for point in ((8, 8), (400, 250), (-1, 250), (960, 250), (400, 540)):
            self.assertEqual(resize_edges(*point, 960, 540), set())
        self.assertEqual(native_resize_hit(set()), 0)

    def test_band_is_seven_logical_pixels_at_both_dprs(self):
        for dpr in (1., 2.):
            self.assertEqual(resize_edges(6.9, 200, 960, 540, dpr=dpr), {"left"})
            self.assertEqual(resize_edges(7., 200, 960, 540, dpr=dpr), set())
            self.assertEqual(resize_edges(953., 200, 960, 540, dpr=dpr), {"right"})

    def test_native_minimum_and_maximum_aspect_preserve_opposite_anchor(self):
        rect = _RECT(1200, 900, 1360, 940)
        constrain_native_rect(rect, {"left", "top"}, (330, 400), (1920, 1080), aspect_ratio=16/9)
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (1030, 540, 1360, 940))
        rect = _RECT(400, 300, 2000, 700)
        constrain_native_rect(rect, {"right", "bottom"}, (330, 400), (1920, 1080), aspect_ratio=16/9)
        self.assertEqual((rect.left, rect.top), (400, 300))
        self.assertLessEqual((rect.right-rect.left)/(rect.bottom-rect.top), 16/9)
        self.assertEqual(rect.right-rect.left, 1600)


class _NativeBase:
    def nativeEvent(self, event_type, message):
        return False, 0


class _NativePage(NativeMusicResizeMixin, _NativeBase):
    def __init__(self, dpr):
        self.dpr = dpr
        self.holds = []
        self._content_layout_user_resizing = False

    def isFullScreen(self): return False
    def isMaximized(self): return False
    def devicePixelRatioF(self): return self.dpr
    def _native_window_rect(self, hwnd): return _RECT(-1920, -1080, -1920+960*self.dpr, -1080+540*self.dpr)
    def _set_resize_chrome_hold(self, held): self.holds.append(held)
    def minimumWidth(self): return 330
    def minimumHeight(self): return 400
    def _available_geometry(self): return QRect(0, 0, 1920, 1080)


class _ManualNativePage(_NativePage):
    def _native_system_resize_enabled(self): return False
    def setCursor(self, cursor): self.cursor = cursor


class NativeMessageTests(unittest.TestCase):
    def send(self, page, native):
        with patch("local.music_resize_hit_test.sys.platform", "win32"):
            return page.nativeEvent(b"windows_generic_MSG", ctypes.addressof(native))

    def test_hit_test_sign_extends_coordinates_on_left_top_monitors(self):
        for dpr in (1, 2):
            page = _NativePage(dpr)
            for x, y, _edges, expected, _cursor in CASES:
                px, py = -1920+x*dpr, -1080+y*dpr
                packed = (px & 0xffff) | ((py & 0xffff) << 16)
                native = _MSG(0x1234, WM_NCHITTEST, 0, packed, 0, (0, 0), 0)
                self.assertEqual(self.send(page, native), (True, expected))

    def test_native_size_loop_holds_existing_resize_and_releases_it(self):
        page = _NativePage(2)
        page._native_pending_resize_edges = {"right"}
        native = _MSG(0x1234, WM_ENTERSIZEMOVE, 0, 0, 0, (0, 0), 0)
        self.assertEqual(self.send(page, native), (False, 0))
        self.assertTrue(page._content_layout_user_resizing)
        rect = _RECT(100, 200, 200, 300)
        native.message, native.wParam, native.lParam = WM_SIZING, 8, ctypes.addressof(rect)
        self.assertEqual(self.send(page, native), (True, 1))
        self.assertEqual((rect.right-rect.left, rect.bottom-rect.top), (660, 800))
        native.message, native.lParam = WM_EXITSIZEMOVE, 0
        self.assertEqual(self.send(page, native), (False, 0))
        self.assertFalse(page._content_layout_user_resizing)
        self.assertEqual(page.holds, [True, False])

    def test_player_fallback_keeps_native_cursor_but_never_starts_the_clamping_loop(self):
        for dpr in (1, 2):
            page = _ManualNativePage(dpr)
            for x, y, _edges, _hit, cursor in CASES:
                px, py = -1920+x*dpr, -1080+y*dpr
                packed = (px & 0xffff) | ((py & 0xffff) << 16)
                native = _MSG(0x1234, WM_NCHITTEST, 0, packed, 0, (0, 0), 0)
                self.assertEqual(self.send(page, native), (True, HTCLIENT))
                self.assertEqual(page.cursor, cursor)
            native.message, native.lParam = WM_ENTERSIZEMOVE, 0
            self.assertEqual(self.send(page, native), (False, 0))
            raw = _RECT(100, 200, 265, 600)  # Raw half-minimum width stays untouched.
            native.message, native.wParam, native.lParam = WM_SIZING, 2, ctypes.addressof(raw)
            self.assertEqual(self.send(page, native), (False, 0))
            self.assertEqual((raw.right-raw.left, raw.bottom-raw.top), (165, 400))
            self.assertEqual(page.holds, [])


class ActualMusicEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_full_height_lyrics_cannot_take_the_wide_edge_cursor(self):
        from local.music_surface_window import MusicSurfaceWindow
        from tests.test_music_centered_layout import _Owner
        owner = _Owner()
        window = MusicSurfaceWindow(owner)
        try:
            window.resize(960, 540)
            window._lyric_hit_rects = {0: QRectF(window.rect())}
            for x, y, _edges, _hit, cursor in CASES:
                event = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
                                    Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                    Qt.KeyboardModifier.NoModifier)
                window.mouseMoveEvent(event)
                self.assertEqual(window.cursor().shape(), cursor)
        finally:
            window.shutdown()
            owner.close()

    def test_windows_client_edge_preserves_feather_and_exact_half_minimum_free_handoff(self):
        from unittest.mock import Mock
        from local.music_surface_window import MusicSurfaceWindow
        from tests.test_music_centered_layout import _Owner
        owner = _Owner()
        snapshot = QImage(330, 400, QImage.Format.Format_ARGB32_Premultiplied)
        snapshot.fill(0xff24466a)
        preview = QImage(160, 50, QImage.Format.Format_ARGB32_Premultiplied)
        preview.fill(0xff070707)
        owner._preview_free_island_from_music_surface = Mock(
            return_value=(QRect(485, 475, 160, 50), preview))
        owner._commit_free_island_from_music_surface = Mock()
        owner._send_playback_command_async = Mock()
        window = MusicSurfaceWindow(owner)
        try:
            window.setGeometry(400, 300, 330, 400)
            window._available_geometry = lambda: QRect(0, 0, 3840, 2160)
            # A real page is laid out before an edge can be pressed.
            window._width_breakpoint_narrow()
            dpr = window.devicePixelRatioF()
            raw = _RECT(100, 200, round(100+330*dpr), round(200+400*dpr))
            px, py = raw.right-round(2*dpr), raw.top+round(200*dpr)
            packed = (px & 0xffff) | ((py & 0xffff) << 16)
            native = _MSG(0x1234, WM_NCHITTEST, 0, packed, 0, (0, 0), 0)
            with patch("local.music_resize_hit_test.sys.platform", "win32"), \
                    patch.object(window, "_native_window_rect", return_value=raw):
                self.assertEqual(window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(native)),
                                 (True, HTCLIENT))
            self.assertEqual(window.cursor().shape(), Qt.CursorShape.SizeHorCursor)
            point = QPointF(328, 200)
            origin = QPointF(window.mapToGlobal(point.toPoint()))
            press = QMouseEvent(QEvent.Type.MouseButtonPress, point, origin,
                                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            with patch.object(window, "_capture_transition_snapshot", return_value=snapshot):
                self.app.sendEvent(window, press)
                for inward in (100., 165.):
                    delta = QPointF(-inward, 0.)
                    move = QMouseEvent(QEvent.Type.MouseMove, point+delta, origin+delta,
                                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier)
                    self.app.sendEvent(window, move)
                    self.assertEqual((window.width(), window.height()), (330, 400))
                    self.assertGreater(window._compact_fraction, 0.)
                    if inward < 165.:
                        self.assertFalse(window._compact_committing)
                        self.assertEqual(window._compact_pressure_edges, {"right"})
                        self.assertGreater(window._compact_pressure[0], 0.)
            self.assertEqual(window._compact_fraction, 1.)
            self.assertTrue(window._compact_committing)
            window._compact_animation.stop()
            window._advance_window_compact(1.)
            window._finish_window_compact()
            owner._commit_free_island_from_music_surface.assert_called_once()
            owner._send_playback_command_async.assert_not_called()
        finally:
            window.shutdown()
            owner.close()

    def test_responsive_player_exposes_all_edges_in_wide_and_narrow(self):
        from _demos.narrow3.narrow_window import NarrowStudyWindow
        from local.island_stack_visual import IslandStackVisual
        owner = IslandStackVisual(data_provider=EmptyIslandDataProvider())
        owner.timer.stop()
        window = NarrowStudyWindow(owner, "B", "left", live=True)
        try:
            for width, height in ((960, 540), (433, 550), (391, 508)):
                window.resize(width, height)
                window._width_chrome_animation.stop()
                window._finish_width_chrome()
                # The wide endpoint still enters the narrow input override
                # while its width handoff is active.
                for handoff in ((False, True) if width == 960 else (False,)):
                    window._width_chrome_active = handoff
                    for x, y, expected, _hit, cursor in CASES:
                        point = QPointF(x*width/960, y*height/540)
                        event = QMouseEvent(QEvent.Type.MouseMove, point, point,
                                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                            Qt.KeyboardModifier.NoModifier)
                        window.mouseMoveEvent(event)
                        self.assertEqual(window._edge_set(point), expected)
                        self.assertEqual(window.cursor().shape(), cursor)
                window._width_chrome_active = False
        finally:
            window.shutdown()
            window.close()
            owner._application_quitting = True
            owner.close()

    def test_library_and_login_share_all_cursor_directions(self):
        from local.music_library_window import MusicLibraryWindow
        from local.playback_entry_window import PlaybackEntryWindow
        library, login = MusicLibraryWindow(), PlaybackEntryWindow()
        try:
            login.page = "sources"
            for page in (library, login):
                for width, height in ((960, 540), (433, 550), (391, 508)):
                    page.resize(width, height)
                    for x, y, _edges, _hit, cursor in CASES:
                        point = QPointF(x*width/960, y*height/540)
                        event = QMouseEvent(QEvent.Type.MouseMove, point, point,
                                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                            Qt.KeyboardModifier.NoModifier)
                        page.mouseMoveEvent(event)
                        self.assertEqual(page.cursor().shape(), cursor)
        finally:
            library.close()
            login.close()


if __name__ == "__main__":
    unittest.main()
