from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TrackData
from api.online_music import AccountSnapshot
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from tests.test_music_palette import _png


def _song(*, playing=True, art=None):
    return TrackData("当前歌曲", "当前歌手", (), ColorToken(193, 42, 31), ColorToken(40, 72, 160), (),
                     album_art_bytes=_png(QColor("#df302b")) if art is None else art,
                     is_playing=playing, track_id="owned:netease:current")


def _sample(image, point):
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(point.x()*ratio), round(point.y()*ratio)).name()


class MusicLibraryHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.now = 100.
        self.clock = patch("local.music_library_window.time.monotonic", side_effect=lambda: self.now)
        self.clock.start()
        self.window = MusicLibraryWindow()
        self.window.resize(760, 720)
        self.window.set_account(AccountSnapshot("netease", True, "header-account", "保留账号昵称"))
        self.window.set_background_context({"v2_music_flow_intensity": 0.}.get, lambda *args: None)
        avatar = QImage(32, 32, QImage.Format.Format_RGB32)
        avatar.fill(QColor("#287fe1"))
        self.window.set_artwork("account", avatar)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.clock.stop()

    def _select(self, song=None):
        self.window.set_now_playing(_song() if song is None else song)
        self.window._header_motion.setCurrentTime(self.window.HEADER_ANIMATION_MS)
        self.app.processEvents()

    def test_playing_header_keeps_avatar_and_nickname_separate_from_current_album(self):
        self._select()
        for width in (520, 760):
            self.window.resize(width, 500)
            avatar = self.window._account_avatar_rect
            album = self.window.now_playing_button.geometry()
            name = self.window.account_name_label.geometry()
            self.assertEqual(avatar.width(), 36)
            self.assertLess(album.right(), avatar.left())
            self.assertLess(avatar.right(), name.left())
            self.assertFalse(name.intersects(self.window.logout_button.geometry()))
            self.assertEqual(self.window.account_name_label.accessibleName(), "保留账号昵称")
        frame = self.window.grab().toImage()
        self.assertEqual(_sample(frame, avatar.center()), "#287fe1")
        self.assertEqual(_sample(frame, album.center()), "#df302b")

    def test_album_click_has_its_own_signal_and_hover_does_not_expand_header(self):
        self._select()
        playback = QSignalSpy(self.window.nowPlayingRequested)
        platform = QSignalSpy(self.window.sourceSwitchRequested)
        layout = (self.window.now_playing_button.geometry(), self.window._account_avatar_rect,
                  self.window.account_name_label.geometry())
        QTest.mouseMove(self.window.now_playing_button, QPoint(30, 30))
        self.app.processEvents()
        self.assertEqual(layout, (self.window.now_playing_button.geometry(), self.window._account_avatar_rect,
                                  self.window.account_name_label.geometry()))
        QTest.mouseClick(self.window.now_playing_button, Qt.MouseButton.LeftButton)
        self.assertEqual(playback.count(), 1)
        self.assertEqual(platform.count(), 0)
        self.assertEqual(self.window._drag_origin, None)
        self.window.source_button.click()
        self.assertEqual(platform.count(), 1)
        self.assertEqual(playback.count(), 1)

    def test_header_animation_reverses_from_current_pose_without_moving_the_catalog(self):
        song = _song()
        catalog = self.window.scroll.geometry()
        self.window.set_now_playing(song)
        self.window._header_motion.setCurrentTime(self.window.HEADER_ANIMATION_MS//2)
        fraction = self.window._header_fraction
        avatar = self.window._account_avatar_rect
        self.assertGreater(fraction, 0.)
        self.assertLess(fraction, 1.)
        self.window.set_now_playing(None)
        self.assertEqual(self.window._header_motion.startValue(), fraction)
        self.assertEqual(self.window._account_avatar_rect, avatar)
        self.assertEqual(self.window.scroll.geometry(), catalog)
        self.window._header_motion.setCurrentTime(self.window.HEADER_ANIMATION_MS)
        self.assertTrue(self.window.now_playing_button.isHidden())
        self.assertEqual(self.window._account_avatar_rect.width(), 64)
        self.assertEqual(self.window.account_name_label.x(), 104)
        for step in range(21):
            self.window._set_header_fraction(step/20)
            if not self.window.now_playing_button.isHidden():
                self.assertFalse(self.window._account_avatar_rect.intersects(
                    self.window.now_playing_button.geometry().toRectF()))

    def test_album_rotates_at_constant_speed_when_background_flow_is_disabled(self):
        self._select()
        self.assertFalse(self.window._background_timer.isActive())
        self.assertTrue(self.window._album_rotation_timer.isActive())
        for now, angle in ((101., 18.), (103.5, 63.), (120., 0.)):
            self.now = now
            self.window._advance_album_rotation()
            self.assertAlmostEqual(self.window._album_rotation, angle)

    def test_pause_hide_minimize_and_close_stop_rotation_without_elapsed_time_jump(self):
        song = _song()
        self._select(song)
        self.now = 101.
        self.window._advance_album_rotation()
        self.window.set_now_playing(replace(song, is_playing=False))
        self.assertFalse(self.window._album_rotation_timer.isActive())
        self.assertFalse(self.window.now_playing_button.isHidden())
        self.now = 500.
        self.window.set_now_playing(song)
        self.now = 501.
        self.window._advance_album_rotation()
        self.assertAlmostEqual(self.window._album_rotation, 36.)
        self.window.hide()
        self.assertFalse(self.window._album_rotation_timer.isActive())
        self.now = 900.
        self.window.show()
        self.now = 901.
        self.window._advance_album_rotation()
        self.assertAlmostEqual(self.window._album_rotation, 54.)
        self.window.showMinimized()
        self.app.processEvents()
        self.assertFalse(self.window._album_rotation_timer.isActive())
        self.now = 1000.
        self.window.showNormal()
        self.app.processEvents()
        self.now = 1001.
        self.window._advance_album_rotation()
        self.assertAlmostEqual(self.window._album_rotation, 72.)
        self.window.close()
        self.assertFalse(self.window._album_rotation_timer.isActive())

    def test_navigation_freezes_angle_and_captures_ui_without_current_cover(self):
        self._select()
        self.now = 102.
        rect, art, angle = self.window.freeze_navigation_cover()
        self.assertFalse(rect.isEmpty())
        self.assertEqual(art.pixelColor(10, 10).name(), "#df302b")
        self.assertAlmostEqual(angle, 36.)
        self.assertFalse(self.window._album_rotation_timer.isActive())
        center = self.window.mapFromGlobal(rect.center())
        full = self.window.grab().toImage()
        background = self.window.navigation_background_frame()
        self.assertEqual(_sample(full, center), "#df302b")
        self.assertNotEqual(_sample(background, center), "#df302b")
        ratio = background.devicePixelRatio()
        self.assertEqual(background.pixelColor(round(center.x()*ratio), round(center.y()*ratio)).alpha(), 0)
        self.assertEqual(_sample(background, self.window._account_avatar_rect.center()), "#287fe1")
        self.assertLessEqual(max(self.window.navigation_backdrop_frame().size().toTuple()), 256)
        self.now = 500.
        self.window.resume_navigation_cover()
        self.now = 501.
        self.window._advance_album_rotation()
        self.assertAlmostEqual(self.window._album_rotation, 54.)
        self.assertEqual(_sample(self.window.grab().toImage(), center), "#df302b")

    def test_navigation_capture_can_exclude_only_the_clicked_collection_cover(self):
        collection = LibraryCollection("playlist-one", "playlist", "真实歌单", "", "one")
        self.window.set_collections((collection,))
        self.window._expand_group("personal")
        self.window._section_motion.setCurrentTime(self.window._section_motion.duration())
        art = QImage(32, 32, QImage.Format.Format_RGB32)
        art.fill(QColor("#e4b129"))
        self.window.set_artwork(collection.key, art)
        cover = self.window._shelves[0].tiles[0][1]
        rect = cover.artwork_rect().toAlignedRect()
        rect.moveTopLeft(cover.mapToGlobal(rect.topLeft()))
        center = self.window.mapFromGlobal(rect.center())
        self.assertEqual(_sample(self.window.grab().toImage(), center), "#e4b129")
        frame = self.window.navigation_background_frame(rect)
        self.assertNotEqual(_sample(frame, center), "#e4b129")
        self.assertFalse(cover._navigation_capture_hidden)
        self.assertEqual(_sample(self.window.grab().toImage(), center), "#e4b129")

    def test_missing_album_has_a_current_album_placeholder_and_keeps_account_photo(self):
        self._select(_song(art=b""))
        _, image, _ = self.window.navigation_cover_state()
        self.assertFalse(image.isNull())
        self.assertEqual(self.window._now_playing_image.isNull(), True)
        self.assertEqual(_sample(self.window.grab().toImage(), self.window._account_avatar_rect.center()), "#287fe1")
        old_key = self.window._header_album_image.cacheKey()
        self.window.set_now_playing(replace(self.window._now_playing, position_seconds=10))
        self.assertEqual(self.window._header_album_image.cacheKey(), old_key)


if __name__ == "__main__":
    unittest.main()
