from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot
from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow
from local.liquid_glass import GlassPushButton
from tests.test_playback_entry_window import _image_bytes


class LoginLayoutVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080),
                                 geometry=lambda: QRect(0, 0, 1920, 1080))
        self.screen_patch = patch.object(QGuiApplication, "screenAt", return_value=screen)
        self.screen_patch.start()
        self.addCleanup(self.screen_patch.stop)
        with patch.dict(os.environ, {"GLUSPOT_LOGIN_VARIANT": "classic"}):
            self.window = PlaybackEntryWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def prepare(self, variant, width=960, height=540):
        self.window.set_login_variant(variant)
        self.window.show_sources()
        # Forged availability must still not enable unsupported adapters.
        self.window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=True) for s in ENTRY_SOURCES))
        self.window.select_source("netease")
        self.window._expansion.stop()
        self.window._expanded = 1.
        self.window.resize(width, height)

    def test_env_selection_default_and_invalid_fallback(self):
        for value, expected in (("", "sidebar"), ("unknown", "sidebar"), ("sidebar", "sidebar"),
                                ("focus", "focus"), ("hero", "hero"), ("classic", "classic")):
            with self.subTest(value=value), patch.dict(os.environ, {"GLUSPOT_LOGIN_VARIANT": value}):
                window = PlaybackEntryWindow()
                try:
                    self.assertEqual(window.login_variant, expected)
                    self.assertEqual(window.page, "modes")
                    self.assertEqual(window.size(), self.window.size())
                finally:
                    window.close()
                    window.deleteLater()
        with patch.dict(os.environ):
            os.environ.pop("GLUSPOT_LOGIN_VARIANT", None)
            window = PlaybackEntryWindow()
            try:
                self.assertEqual(window.login_variant, "sidebar")
            finally:
                window.close()
                window.deleteLater()

    def test_switch_preserves_live_qr_widgets_and_signals(self):
        self.prepare("sidebar")
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes()))
        key = self.window._qr_image.cacheKey()
        button = self.window.source_buttons["netease"]
        selected = QSignalSpy(self.window.sourceSelected)
        refresh = QSignalSpy(self.window.refreshRequested)
        accepted = QSignalSpy(self.window.loginAccepted)
        for variant in ("focus", "hero", "classic", "sidebar"):
            self.window.set_login_variant(variant)
            self.assertEqual(self.window.selected_source, "netease")
            self.assertEqual(self.window.login_state, "waiting")
            self.assertEqual(self.window._qr_image.cacheKey(), key)
            self.assertIs(self.window.source_buttons["netease"], button)
            button.click()
            for source in ENTRY_SOURCES:
                if not source.login_supported:
                    self.assertFalse(self.window.source_buttons[source.source_id].isEnabled())
                    self.window.select_source(source.source_id)
                    self.assertEqual(self.window.selected_source, "netease")
        self.assertEqual((selected.count(), refresh.count(), accepted.count()), (0, 0, 0))
        with self.assertRaises(ValueError):
            self.window.set_login_variant("not-a-layout")
        self.assertEqual(self.window.login_variant, "sidebar")

    def test_switch_from_classic_picker_exposes_usable_detail_and_keeps_shared_geometry(self):
        self.window.show_sources()
        self.assertEqual(self.window.height(), 264)
        self.window.set_login_variant("hero")
        self.assertGreaterEqual(self.window.height(), 400)
        self.assertGreater(self.window._source_pane_rect().height(), 180)
        self.window.set_login_variant("classic")
        self.window._shared_music_geometry = QRect(50, 60, 433, 400)
        self.window.setGeometry(self.window._shared_music_geometry)
        self.window.set_login_variant("hero")
        self.assertEqual(self.window.geometry(), self.window._shared_music_geometry)
        for variant in ("sidebar", "focus", "hero"):
            self.window.set_login_variant(variant)
            pane = self.window._source_pane_rect()
            qr, _detail = self.window._login_content_rects()
            for rect in (qr, *self.window._login_detail_geometry()):
                self.assertTrue(pane.contains(rect), (variant, pane, rect))

    def test_requested_sizes_keep_login_states_and_account_actions_contained(self):
        for variant in ("sidebar", "focus", "hero"):
            sizes = ((960, 540), (433, 550), (391, 508)) if variant == "sidebar" else ((960, 540), (433, 550))
            for width, height in sizes:
                with self.subTest(variant=variant, size=(width, height)):
                    self.prepare(variant, width, height)
                    pane = self.window._source_pane_rect()
                    for state in ("waiting", "error"):
                        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state=state,
                            message="等待扫码" if state == "waiting" else "连接暂不可用，请重试",
                            qr_image_bytes=_image_bytes() if state == "waiting" else b""))
                        qr, _detail = self.window._login_content_rects()
                        title, helper, message, action = self.window._login_detail_geometry()
                        for rect in (qr, title, helper, message, action):
                            self.assertTrue(pane.contains(rect), (variant, state, rect, pane))
                        self.assertFalse(qr.intersects(title))
                        self.assertFalse(helper.intersects(message))
                        self.assertFalse(message.intersects(action))
                        self.assertFalse(qr.intersects(action))
                        self.assertEqual(self.window._qr_image.isNull(), state == "error")
                        self.assertTrue(self.window.refresh_button.isEnabled())
                        for button in self.window.source_buttons.values():
                            if not button.isHidden():
                                self.assertTrue(self.window.rect().contains(button.geometry()))
                                self.assertGreaterEqual(button.height(), 44)
                                self.assertFalse(QRectF(button.geometry()).intersects(pane))
                    account = AccountSnapshot("netease", True, "u1", "LouisHerm", "", "verified", "")
                    self.window.set_account(account)
                    avatar, name, action = self.window._account_content_rects()
                    for rect in (avatar, name, action):
                        self.assertTrue(pane.contains(rect), (variant, rect, pane))
                    self.assertFalse(avatar.intersects(action))
                    self.assertFalse(name.intersects(action))
                    accepted = QSignalSpy(self.window.loginAccepted)
                    self.window.enter_button.click()
                    self.assertEqual(accepted.count(), 1)
                    self.assertEqual(accepted.at(0), ["netease"])
                    refreshed = QSignalSpy(self.window.refreshRequested)
                    self.window.select_source("qq")
                    self.window._expansion.stop()
                    self.assertIsNone(self.window._account)
                    self.window.set_login_snapshot(SimpleNamespace(source_id="qq", state="error"))
                    self.window.refresh_button.click()
                    self.assertEqual(refreshed.count(), 1)

    def test_account_switch_keeps_artwork_and_hero_backdrop_is_static(self):
        self.prepare("sidebar", 433, 550)
        account = AccountSnapshot("netease", True, "u1", "LouisHerm", state="verified")
        self.window.set_account(account)
        image = QImage(80, 80, QImage.Format.Format_RGB32)
        image.fill(QColor("#a6c5c6"))
        self.window.set_account_artwork(image)
        key = self.window._account_image.cacheKey()
        for variant in ("hero", "focus", "classic", "sidebar", "hero"):
            self.window.set_login_variant(variant)
            self.assertIs(self.window._account, account)
            self.assertEqual(self.window._account_image.cacheKey(), key)
            self.assertEqual(self.window.enter_button.text(), "使用此平台")
        old_backdrop = self.window._background_frame.cacheKey()
        image.fill(QColor("#c5a6aa"))
        self.window.set_account_artwork(image)
        self.assertNotEqual(self.window._background_frame.cacheKey(), old_backdrop)
        backdrop = self.window._background_frame.cacheKey()
        self.window._layout_children()
        self.assertEqual(self.window._background_frame.cacheKey(), backdrop)
        self.assertLessEqual(self.window._background_frame.width(), 256)
        self.assertFalse(self.window._frame_timer.isActive())

    def test_sidebar_keeps_all_platforms_in_vertical_rail_and_detail_on_right(self):
        for width, height in ((960, 540), (433, 550), (391, 508), (330, 400), (560, 360)):
            with self.subTest(size=(width, height)):
                self.prepare("sidebar", width, height)
                self.window._layout_children()
                pane = self.window._source_pane_rect()
                buttons = tuple(self.window.source_buttons.values())
                for index, button in enumerate(buttons):
                    self.assertFalse(button.isHidden())
                    self.assertTrue(self.window.rect().contains(button.geometry()))
                    self.assertGreaterEqual(button.width(), 44)
                    self.assertGreaterEqual(button.height(), 44)
                    self.assertLess(button.geometry().right(), pane.left())
                    self.assertEqual(button.login_style, "sidebar" if width >= 720 else "sidebar-icons")
                    self.assertIn(button.source.name, button.toolTip())
                    self.assertIn(button.connection_status, button.accessibleName())
                    for other in buttons[index + 1:]:
                        self.assertGreater(other.y(), button.y())
                        self.assertFalse(button.geometry().intersects(other.geometry()))
                    self.assertEqual(button.status_rect().isEmpty(), not button.source.login_supported)

    def test_source_status_remembers_other_accounts_without_replacing_selected_details(self):
        self.prepare("sidebar")
        self.window.set_account(AccountSnapshot("netease", True, "n", "NetEase", state="verified"))
        self.window.select_source("qq")
        self.assertEqual(self.window._source_status["netease"], "connected")
        self.assertEqual(self.window._source_status["qq"], "signed_out")
        self.window.set_account(AccountSnapshot("kugou", True, "k", "Kugou"))
        self.assertEqual(self.window._source_status["kugou"], "connected")
        self.assertIsNone(self.window._account)
        self.assertEqual(self.window.selected_source, "qq")
        for state in ("error", "expired"):
            self.window.set_account(AccountSnapshot("netease", True, "n", state=state))
            self.assertEqual(self.window._source_status["netease"], "error")
        self.window.set_account(AccountSnapshot("netease"))
        self.assertEqual(self.window._source_status["netease"], "signed_out")
        self.window.set_login_snapshot(SimpleNamespace(source_id="qq", state="confirmed"))
        self.assertEqual(self.window._source_status["qq"], "signed_out")
        for state in ("expired", "error"):
            self.window.set_login_snapshot(SimpleNamespace(source_id="qq", state=state))
            self.assertEqual(self.window._source_status["qq"], "error")
        self.window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=True) for s in ENTRY_SOURCES))
        self.assertEqual(self.window._source_status["qq"], "error")
        self.assertIn("异常", self.window.source_buttons["qq"].accessibleName())
        for source in ENTRY_SOURCES:
            if not source.login_supported:
                self.window.set_account(AccountSnapshot(source.source_id, True, "forged"))
                self.assertEqual(self.window._source_status[source.source_id], "upcoming")

    def test_pulse_invalidates_only_connected_dots_without_rebuilding_backdrop(self):
        self.prepare("sidebar")
        self.window.set_account(AccountSnapshot("netease", True, "n", state="verified"))
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window._status_timer.isActive())
        self.assertFalse(self.window._frame_timer.isActive())
        backdrop = self.window._background_frame.cacheKey()
        button = self.window.source_buttons["netease"]
        with patch.object(button, "update") as dirty, patch.object(self.window, "update") as window_dirty, \
                patch.object(self.window, "_source_backdrop") as rebuild, \
                patch.object(self.window.source_buttons["qq"], "update") as signed_out_dirty:
            for offset, opacity in ((0., .55), (1., 1.), (2., .55)):
                self.window._advance_status_pulse(self.window._status_pulse_started_at + offset)
                self.assertAlmostEqual(button.status_opacity, opacity)
                self.assertEqual(dirty.call_args.args, (button.status_rect(),))
            self.assertEqual(dirty.call_count, 3)
            window_dirty.assert_not_called()
            rebuild.assert_not_called()
            signed_out_dirty.assert_not_called()
        self.assertEqual(self.window._background_frame.cacheKey(), backdrop)
        self.assertLessEqual(button.status_rect().width(), 16)
        self.assertLessEqual(button.status_rect().height(), 16)

    def test_pulse_stops_without_visible_green_dots_or_when_leaving_sources(self):
        self.prepare("sidebar")
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window._status_timer.isActive())
        self.window.set_account(AccountSnapshot("netease", True, "n"))
        self.assertTrue(self.window._status_timer.isActive())
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="error"))
        self.assertFalse(self.window._status_timer.isActive())
        self.window.set_account(AccountSnapshot("netease", True, "n"))
        for variant in ("classic", "focus", "hero"):
            self.window.set_login_variant(variant)
            self.assertFalse(self.window._status_timer.isActive())
        self.window.set_login_variant("sidebar")
        self.assertTrue(self.window._status_timer.isActive())
        self.window.source_buttons["netease"].hide()
        self.window._advance_status_pulse()
        self.assertFalse(self.window._status_timer.isActive())
        self.window._layout_children()
        self.assertTrue(self.window._status_timer.isActive())
        self.window.hide()
        self.assertFalse(self.window._status_timer.isActive())
        self.window.show()
        self.assertTrue(self.window._status_timer.isActive())
        self.window.show_modes()
        self.assertFalse(self.window._status_timer.isActive())

    def test_status_colors_render_and_only_connected_dot_changes_with_phase(self):
        self.prepare("sidebar")
        self.window.show()
        self.app.processEvents()
        button = self.window.source_buttons["netease"]
        self.window.set_account(AccountSnapshot("netease", True, "n"))
        self.window._status_timer.stop()
        self.window._advance_status_pulse(self.window._status_pulse_started_at + 1.)
        center = button._status_dot_rect().center().toPoint()
        green = button.grab().toImage().pixelColor(center)
        self.assertGreater(green.green(), green.red() * 1.5)
        bright = button.grab().toImage()
        self.window._advance_status_pulse(self.window._status_pulse_started_at)
        dim = button.grab().toImage()
        self.assertLess(dim.pixelColor(center).green(), green.green())
        changed = [(x, y) for y in range(dim.height()) for x in range(dim.width())
                   if dim.pixel(x, y) != bright.pixel(x, y)]
        self.assertTrue(changed)
        self.assertTrue(all(button.status_rect().contains(x, y) for x, y in changed))
        for snapshot in (AccountSnapshot("netease"), AccountSnapshot("netease", state="expired")):
            self.window.set_account(snapshot)
            first = button.grab().toImage()
            self.window._advance_status_pulse(self.window._status_pulse_started_at)
            self.assertEqual(first, button.grab().toImage())
        error = button.grab().toImage().pixelColor(center)
        self.assertGreater(error.red(), error.green())

    def test_account_and_retry_actions_keep_shared_glass_capsule_material(self):
        self.prepare("sidebar")
        styles = [button.styleSheet() for button in (self.window.enter_button, self.window.refresh_button)]
        self.window.set_account(AccountSnapshot("netease", True, "n"))
        self.window.select_source("qq")
        self.window.set_login_snapshot(SimpleNamespace(source_id="qq", state="error"))
        for button, style in zip((self.window.enter_button, self.window.refresh_button), styles):
            self.assertIsInstance(button, GlassPushButton)
            self.assertTrue(button.property("glassRefractive"))
            self.assertTrue(button.property("glassLight"))
            self.assertEqual(button.property("glassRadius"), 22.)
            self.assertEqual(button.styleSheet(), style)
            self.assertNotIn("#df2a16", style)


if __name__ == "__main__":
    unittest.main()
