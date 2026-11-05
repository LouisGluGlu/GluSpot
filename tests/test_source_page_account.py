"""Source-page lamps, account actions, and the avatar/icon color field."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from api.online_music import AccountSnapshot
from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow
import time

from local.source_page_backdrop import (
    FIELD_BLEND_SECONDS, FIELD_PERIODS, blend_color, crossfade_color_fields,
    extract_dominant_color, icon_dominant_color, render_source_color_field,
)


def _solid(width, height, color) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color)
    return image


def _relative_luminance(color: QColor) -> float:
    channels = []
    for value in color.getRgbF()[:3]:
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


class SourceColorFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_quantizer_keeps_the_chromatic_mass_and_drops_gray(self):
        image = _solid(64, 64, QColor(128, 128, 128))
        painter = QPainter(image)
        painter.fillRect(32, 0, 32, 64, QColor(220, 40, 36))
        painter.end()
        color = extract_dominant_color(image)
        self.assertGreater(color.red(), color.green() + 80)
        self.assertGreater(color.red(), color.blue() + 80)
        self.assertEqual(extract_dominant_color(_solid(8, 8, QColor(250, 250, 250))), QColor(180, 140, 120))

    def test_platform_icons_keep_their_own_non_gray_pigment(self):
        netease = icon_dominant_color("netease")
        kugou = icon_dominant_color("kugou")
        self.assertGreater(netease.red(), netease.blue())
        self.assertGreater(kugou.blue(), kugou.red())

    def test_periods_and_blend_match_the_confirmed_timing(self):
        self.assertTrue(all(14.0 <= period <= 19.0 for period in FIELD_PERIODS))
        self.assertEqual(FIELD_BLEND_SECONDS, 0.6)
        start, end = QColor(20, 30, 40), QColor(200, 10, 10)
        self.assertEqual(blend_color(start, end, 0.0), start)
        self.assertEqual(blend_color(start, end, 1.0), end)
        midpoint = blend_color(start, end, 0.5)
        self.assertEqual(midpoint.red(), 110)

    def test_missing_avatar_uses_only_the_icon_and_motion_stays_bounded(self):
        icon = QColor(40, 90, 220)
        avatar = QColor(220, 48, 36)
        mixed = render_source_color_field(QRect(0, 0, 960, 540).size(), avatar, icon, 0.0, "frosted")
        icon_only = render_source_color_field(QRect(0, 0, 960, 540).size(), None, icon, 0.0, "frosted")
        moved = render_source_color_field(QRect(0, 0, 960, 540).size(), None, icon, 8.0, "frosted")
        self.assertLessEqual(max(mixed.width(), mixed.height()), 256)
        avatar_spot = mixed.pixelColor(round(mixed.width() * 0.52), round(mixed.height() * 0.42))
        same_spot = icon_only.pixelColor(round(icon_only.width() * 0.52), round(icon_only.height() * 0.42))
        self.assertGreater(avatar_spot.red() - avatar_spot.blue(), same_spot.red() - same_spot.blue())
        self.assertNotEqual(icon_only, moved)
        for variant in ("classic", "flow", "frosted", "noir"):
            frame = render_source_color_field(QRect(0, 0, 640, 360).size(), avatar, icon, 1.0, variant)
            peak = max(_relative_luminance(frame.pixelColor(x, y))
                       for y in range(0, frame.height(), 7) for x in range(0, frame.width(), 7))
            self.assertLess(peak, 0.30)
        self.assertNotEqual(
            render_source_color_field(QRect(0, 0, 640, 360).size(), None, icon, 0.0, "flow"),
            render_source_color_field(QRect(0, 0, 640, 360).size(), None, icon, 0.0, "frosted"))
        self.assertNotEqual(
            render_source_color_field(QRect(0, 0, 640, 360).size(), None, icon, 0.0, "frosted"),
            render_source_color_field(QRect(0, 0, 640, 360).size(), None, icon, 0.0, "noir"))


class SourceAccountPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080),
                                 geometry=lambda: QRect(0, 0, 1920, 1080))
        self.screen_patch = patch.object(QGuiApplication, "screenAt", return_value=screen)
        self.screen_patch.start()
        self.addCleanup(self.screen_patch.stop)
        with patch.dict(os.environ, {"GLUSPOT_LOGIN_VARIANT": "sidebar"}):
            self.window = PlaybackEntryWindow()
        self.window.show_sources()
        self.window.set_sources(tuple(SimpleNamespace(source_id=source.source_id, available=True)
                                      for source in ENTRY_SOURCES))
        self.window.select_source("netease")
        self.window._expansion.stop()
        self.window._expanded = 1.0
        self.window.resize(960, 540)
        self.window._layout_children()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_qr_group_aligns_with_the_code_and_uses_the_flat_glass_button(self):
        from local.playback_entry_window import SourceFlatButton
        self.window.select_source("kugou")
        self.window._expansion.stop()
        self.window._expanded = 1.0
        self.window.set_login_snapshot(SimpleNamespace(
            source_id="kugou", state="waiting", message="等待扫码"))
        for width, height, stacked in ((1000, 700, False), (440, 480, True)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.window._layout_children()
                pane = self.window._source_pane_rect()
                qr, detail = self.window._login_content_rects()
                title, helper, message, action = self.window._login_detail_geometry()
                button = self.window.refresh_button
                self.assertIsInstance(button, SourceFlatButton)
                self.assertTrue(button.primary)
                self.assertEqual(button.height(), 38)
                self.assertEqual(button.text(), "刷新二维码")
                self.assertTrue(pane.contains(qr))
                self.assertTrue(pane.contains(action))
                self.assertFalse(message.intersects(action))
                if stacked:
                    self.assertGreater(title.top(), qr.bottom())
                    self.assertAlmostEqual(qr.center().x(), pane.center().x(), delta=1)
                    self.assertAlmostEqual(button.geometry().center().x(), pane.center().x(), delta=2)
                else:
                    self.assertAlmostEqual(title.top(), qr.top(), delta=1)
                    self.assertAlmostEqual(action.bottom(), qr.bottom(), delta=1)
                    self.assertAlmostEqual(action.left(), title.left(), delta=1)
                    self.assertAlmostEqual(button.geometry().bottom(), round(qr.bottom()), delta=1)
                    self.assertAlmostEqual(button.geometry().left(), round(title.left()), delta=1)
                    group_mid = (qr.left() + detail.right()) / 2
                    self.assertAlmostEqual(group_mid, pane.center().x(), delta=1)
                    self.assertAlmostEqual((qr.top() + qr.bottom()) / 2, pane.center().y(), delta=1)

    def test_status_lamp_sits_on_the_icon_corner_in_both_rails(self):
        for width in (960, 440):
            with self.subTest(width=width):
                self.window.resize(width, 540)
                self.window._layout_children()
                button = self.window.source_buttons["netease"]
                icon = button._icon_rect()
                dot = button._status_dot_rect()
                self.assertEqual(dot.width(), 10)
                self.assertEqual(dot.height(), 10)
                self.assertGreaterEqual(dot.center().x() - icon.right(), 2)
                self.assertLessEqual(dot.center().x() - icon.right(), 3)
                self.assertGreaterEqual(dot.center().y() - icon.bottom(), 2)
                self.assertLessEqual(dot.center().y() - icon.bottom(), 3)
                self.assertLessEqual(button.status_rect().width(), 16)
                self.assertTrue(button.status_rect().contains(dot.toAlignedRect()))
                button.set_connection_status("connected")
                self.window.show()
                self.app.processEvents()
                image = button.grab().toImage()
                center = dot.center().toPoint()
                fill = image.pixelColor(center)
                self.assertGreater(fill.green(), fill.red())
                ring = image.pixelColor(center.x() + 6, center.y())
                self.assertLess(ring.green(), 80)
                self.assertLess(ring.red(), 80)
                self.assertTrue(self.window.source_buttons["spotify"].status_rect().isEmpty())

    def test_row_logout_is_gone_and_detail_logout_clears_only_that_platform(self):
        self.window.set_account(AccountSnapshot("netease", True, "n", "LouisHerm", state="verified"))
        self.window._set_source_status("qq", "connected")
        self.window.show()
        self.app.processEvents()
        self.assertFalse(any(button.text() == "退出" and button.isVisible()
                             for button in self.window.findChildren(QPushButton)))
        self.assertTrue(self.window.account_logout_button.isVisible())
        self.assertEqual(self.window.enter_button.text(), "使用此平台")
        self.assertEqual(self.window.account_logout_button.text(), "退出登录")
        self.assertEqual(self.window.enter_button.height(), 38)
        self.assertEqual(self.window.account_logout_button.height(), 38)
        self.assertLess(self.window.enter_button.geometry().right(), self.window.account_logout_button.geometry().left())
        pane = self.window._source_pane_rect()
        for button in (self.window.enter_button, self.window.account_logout_button):
            self.assertTrue(pane.contains(QRectF(button.geometry())))
        host = self.window.enter_button.parentWidget()
        plate = QImage(host.size(), QImage.Format.Format_ARGB32_Premultiplied)
        plate.fill(QColor(16, 16, 18))
        painter = QPainter(plate)
        self.window.enter_button.render(painter, self.window.enter_button.pos())
        self.window.account_logout_button.render(painter, self.window.account_logout_button.pos())
        painter.end()
        def face(button):
            # Sample the flat fill above the label. Glyph counters would show the page behind them.
            return plate.pixelColor(button.x() + button.width() // 2, button.y() + 8)
        primary, base = face(self.window.enter_button), face(self.window.account_logout_button)
        for color in (primary, base):
            self.assertLess(max(color.red(), color.green(), color.blue()) - min(color.red(), color.green(), color.blue()), 18)
        self.assertGreater(primary.red() + primary.green() + primary.blue(), base.red() + base.green() + base.blue())
        spy = QSignalSpy(self.window.sourceLogoutRequested)
        self.window.account_logout_button.click()
        dialog = self.window._logout_confirmation
        self.assertIn("网易云音乐", dialog.text())
        dialog.button(QMessageBox.StandardButton.Cancel).click()
        self.assertEqual(spy.count(), 0)
        self.window.account_logout_button.click()
        self.window._logout_confirmation.button(QMessageBox.StandardButton.Ok).click()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), ["netease"])
        self.assertEqual(self.window._source_status["qq"], "connected")

    def test_qr_page_keeps_the_refresh_label_for_every_login_state(self):
        for state in ("waiting", "scanned", "expired", "error", "cancelled"):
            with self.subTest(state=state):
                self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state=state, message="状态"))
                self.assertEqual(self.window.refresh_button.text(), "刷新二维码")
                self.assertNotIn(self.window.refresh_button.text(), ("登录", "重新登录"))
        self.assertFalse(self.window.refresh_button.isHidden())
        self.assertTrue(self.window.enter_button.isHidden())
        self.assertTrue(self.window.account_logout_button.isHidden())
        self.assertEqual(self.window.refresh_button.height(), 38)

    def test_platform_blend_crossfades_cached_plates_at_60fps(self):
        """Offscreen stand-in for the stutter: time the old plate rebuild against the cached crossfade.

        ``QT_QPA_PLATFORM=offscreen`` has no vsync. A 600 ms blend at 60 fps is
        36 presented frames, so the gate is those 36 frames finishing inside
        600 ms, including the window paint.
        """
        size = self.window.size()
        start = render_source_color_field(size, QColor(210, 96, 64), QColor(220, 40, 36), 0.0)
        end = render_source_color_field(size, None, QColor(40, 90, 220), 0.0)
        frames = 36
        began = time.perf_counter()
        for index in range(frames):
            render_source_color_field(size, QColor(180, 70, 50), QColor(80, 90, 200), index / frames)
        rebuild = time.perf_counter() - began
        began = time.perf_counter()
        faded = start
        for index in range(frames):
            faded = crossfade_color_fields(start, end, index / (frames - 1))
        fade = time.perf_counter() - began
        self.assertLess(fade, 0.6)
        self.assertLess(fade * 4, rebuild)
        self.assertGreater(faded.pixelColor(faded.width() // 2, faded.height() // 2).blue(),
                           start.pixelColor(start.width() // 2, start.height() // 2).blue())
        self.window.show()
        self.app.processEvents()
        self.window.select_source("kugou")
        self.window._expansion.stop()
        self.window._set_expanded(1.0)
        self.window._field_blend_at = time.monotonic()
        self.window._sync_field_timer()
        self.assertEqual(self.window._field_timer.interval(), 16)
        with patch("local.playback_entry_window.render_source_color_field") as rebuilds:
            began = time.perf_counter()
            for _index in range(frames):
                # Hold the blend open so the sample counts only crossfades.
                self.window._field_blend_at = time.monotonic()
                self.window._advance_platform_field()
                self.app.processEvents()
            painted = time.perf_counter() - began
        rebuilds.assert_not_called()
        self.assertLess(painted, 0.6)
        self.assertLessEqual(self.window._background_frame.width(), 256)

    def test_selected_platform_field_is_stable_across_layout_and_bounded(self):
        self.window.set_account(AccountSnapshot("netease", True, "n", "LouisHerm", state="verified"))
        image = QImage(48, 48, QImage.Format.Format_RGB32)
        image.fill(QColor(210, 96, 64))
        self.window.set_account_artwork(image)
        self.assertLessEqual(self.window._background_frame.width(), 256)
        self.assertLessEqual(self.window._background_frame.height(), 256)
        key = self.window._background_frame.cacheKey()
        self.window._layout_children()
        self.assertEqual(self.window._background_frame.cacheKey(), key)
        self.assertFalse(self.window._frame_timer.isActive())
        before = self.window._background_frame.cacheKey()
        self.window.select_source("kugou")
        self.window._expansion.stop()
        self.window._set_expanded(1.0)
        self.assertNotEqual(self.window._background_frame.cacheKey(), before)
