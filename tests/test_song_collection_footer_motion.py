"""Collection follows the ordinary footer while Private DJ can stay visible."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider
from tests.test_song_collection_surface import Collection


class SongCollectionFooterMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.media_track_id = "owned:netease:1"
        self.provider = _Provider()
        self.provider.fm = True
        self.speaking = False
        self.presentation = {}
        self.provider.get_owned_personal_dj_enabled = lambda: True
        self.provider.is_owned_personal_dj_speaking = lambda: self.speaking
        self.provider.get_personal_dj_presentation = lambda: self.presentation
        self.owner.data_provider = self.provider
        self.owner._song_collection_controller = Collection()
        with patch("local.music_surface_window.time.monotonic", return_value=100.):
            self.window = MusicSurfaceWindow(self.owner)
            self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            self.window.resize(960, 540)
            self.window.show()
            self.window._timer.stop()
            self.window._audio_output_status_timer.stop()
            self.window._sync_owned_playback_mode()
            self.window._reset_control_idle(100.)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def sync(self, now):
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window._sync_owned_playback_mode()
            self.window._sync_window_control_rects()

    def start_speech(self, now):
        self.speaking = True
        self.presentation = {"state": "speaking", "next_title": "A real introduction",
                             "token": 4, "is_preview": False}
        self.sync(now)

    def test_speech_retargets_collection_from_its_visible_position(self):
        region = QRectF(self.window.rect())
        before = self.window._song_collection_geometry(region, 101., shifted=False)
        self.start_speech(101.)
        start = self.window._song_collection_geometry(region, 101., shifted=False)
        middle = self.window._song_collection_geometry(region, 101.12, shifted=False)
        end = self.window._song_collection_geometry(region, 102., shifted=False)
        self.assertAlmostEqual(start.left(), before.left(), delta=.5)
        self.assertLess(end.left(), before.left())
        self.assertLess(middle.left(), before.left())
        self.assertGreater(middle.left(), end.left())
        self.assertAlmostEqual(start.center().y(), end.center().y(), delta=.5)

    def test_independent_dj_status_does_not_keep_collection_hit_targets_alive(self):
        for state in ("loading", "speaking", "outro"):
            with self.subTest(state=state):
                self.speaking = state == "speaking"
                self.presentation = {"state": "speaking" if self.speaking else "loading",
                    "introduction_pending": state == "loading", "next_title": "An introduction",
                    "token": state, "is_preview": False}
                self.sync(102.)
                if state == "outro":
                    self.window._dj_outro_started_at = 102.
                    self.window._dj_outro_track_id = self.owner.media_track_id
                with patch.object(self.window, "_control_reveal_at", return_value=0.), \
                        patch("local.music_surface_window.time.monotonic", return_value=102.5):
                    self.window._sync_window_control_rects()
                    self.assertGreater(self.window._personal_dj_reveal_at(102.5), .75)
                    self.assertIn("personal_dj", self.window._button_rects)
                    self.assertNotIn("favorite", self.window._button_rects)
                    self.assertNotIn("playlist_add", self.window._button_rects)
                    face = self.window._song_collection_geometry(QRectF(self.window.rect()), 102.5)
                    self.assertGreaterEqual(face.top(), self.window.height())

    def test_reveal_and_hide_share_pose_with_hit_rects(self):
        self.start_speech(101.)
        for reveal in (.0, .25, .5, .8, 1.):
            with self.subTest(reveal=reveal), \
                    patch.object(self.window, "_control_reveal_at", return_value=reveal), \
                    patch("local.music_surface_window.time.monotonic", return_value=102.):
                region = QRectF(self.window.rect())
                face = self.window._song_collection_geometry(region, 102.)
                self.window._sync_window_control_rects()
                if reveal >= .75:
                    union = self.window._button_rects["favorite"].united(self.window._button_rects["playlist_add"])
                    self.assertAlmostEqual(union.center().x(), face.center().x(), delta=1.)
                    self.assertAlmostEqual(union.center().y(), face.center().y(), delta=1.)
                else:
                    self.assertNotIn("favorite", self.window._button_rects)
                if reveal == 0.:
                    self.assertFalse(region.intersects(face))
                elif reveal == 1.:
                    self.assertTrue(region.contains(face))

    def test_collection_damage_clears_previous_position_during_dj_expansion(self):
        region = QRectF(self.window.rect())
        self.window.update = Mock()
        before = self.window._song_collection_geometry(region, 101.)
        self.window._update_song_collection_animation(101.)
        self.start_speech(101.)
        self.window._update_song_collection_animation(101.12)
        self.window._update_song_collection_animation(102.)
        damages = [call.args[0] for call in self.window.update.call_args_list if call.args]
        self.assertTrue(any(QRectF(damage.boundingRect() if hasattr(damage, "boundingRect") else damage)
                            .contains(before) for damage in damages))
        after = self.window._song_collection_geometry(region, 102.)
        self.assertTrue(any(QRectF(damage.boundingRect() if hasattr(damage, "boundingRect") else damage)
                            .contains(after) for damage in damages))


if __name__ == "__main__":
    unittest.main()
