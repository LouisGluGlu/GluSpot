from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TrackData
from api.online_music import AccountSnapshot
from local.music_library_window import MusicLibraryWindow
from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_surface_palette import artwork_surface_palette, relative_luminance
from tests.test_music_palette import _png


def _track(name="first", *, primary=(215, 52, 39), secondary=(40, 72, 160), art=b"", playing=True):
    return TrackData(name, "artist", (), ColorToken(*primary), ColorToken(*secondary), (),
                     album_art_bytes=art, is_playing=playing, track_id=f"owned:netease:{name}")


def _pixels(image):
    return bytes(image.constBits())


class MusicLibraryBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MusicLibraryWindow()
        self.window.resize(760, 720)
        self.window.set_account(AccountSnapshot("netease", True, "background-account", "背景账号"))
        self.settings = {"v2_music_flow_intensity": .74, "v2_animation_fps": 60}
        self.work = []
        self.window.set_background_context(self.settings.get, lambda name, prepare: self.work.append(prepare))
        with patch("local.music_library_window.time.monotonic", return_value=100.):
            self.window.show()
            self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _select(self, track, now=100.):
        with patch("local.music_library_window.time.monotonic", return_value=now):
            self.window.set_now_playing(track)

    def _advance(self, start=100., frames=45):
        for frame in range(1, frames + 1):
            with patch("local.music_library_window.time.monotonic", return_value=start + frame / 30.):
                self.window._advance_background()

    def test_playing_library_reuses_bounded_song_field_and_preserves_readability(self):
        initial = self.window._background_frame.copy()
        self._select(_track())
        renderer = self.window._background_renderer
        self.assertTrue(self.window._background_timer.isActive())
        self.assertGreaterEqual(self.window._background_timer.interval(), 34)
        self._advance()
        self.assertIs(self.window._background_renderer, renderer)
        self.assertNotEqual(_pixels(initial), _pixels(self.window._background_frame))
        self.assertLessEqual(max(self.window._background_frame.width(), self.window._background_frame.height()), 256)
        # The same dark reading wash remains above the renderer's bounded
        # pigments, so header, song labels and glass controls retain contrast.
        image = self.window._background_frame
        brightest = max(relative_luminance(image.pixelColor(x, y))
            for y in range(0, image.height(), 8) for x in range(0, image.width(), 8))
        self.assertGreaterEqual((relative_luminance(QColor("#bcc3cf")) + .05) / (brightest + .05), 4.5)

    def test_song_change_retargets_from_displayed_palette_without_a_first_frame_flash(self):
        self._select(_track())
        self._advance(frames=45)
        previous = self.window._background_frame.copy()
        self._select(_track("second", primary=(30, 185, 76), secondary=(151, 30, 181)), now=101.5)
        renderer = self.window._background_renderer
        self.assertEqual(_pixels(self.window._background_frame), _pixels(previous))
        self._advance(start=101.5, frames=18)
        self.assertNotEqual(renderer._painted_colors, renderer._target_colors)
        middle = self.window._background_frame.copy()
        self._advance(start=102.1, frames=30)
        self.assertEqual(renderer._painted_colors, renderer._target_colors)
        self.assertNotEqual(_pixels(middle), _pixels(previous))
        self.assertNotEqual(_pixels(middle), _pixels(self.window._background_frame))

    def test_pause_hide_close_and_resume_do_not_accumulate_hidden_wall_time(self):
        song = _track()
        self._select(song)
        self._advance(frames=9)
        before = self.window._background_seconds
        self._select(replace(song, is_playing=False), now=100.3)
        self.assertFalse(self.window._background_timer.isActive())
        self._advance(start=500., frames=2)
        self.assertEqual(self.window._background_seconds, before)
        self._select(song, now=600.)
        self._advance(start=600., frames=1)
        self.assertLessEqual(self.window._background_seconds - before, 1. / 30. + 1e-8)
        self.window.hide()
        before = self.window._background_seconds
        self._advance(start=900., frames=2)
        self.assertFalse(self.window._background_timer.isActive())
        self.assertEqual(self.window._background_seconds, before)
        with patch("local.music_library_window.time.monotonic", return_value=1000.):
            self.window.show()
        self._advance(start=1000., frames=1)
        self.assertLessEqual(self.window._background_seconds - before, 1. / 30. + 1e-8)
        self.window.close()
        self.assertFalse(self.window._background_timer.isActive())

    def test_cover_preparation_is_coalesced_and_position_changes_never_extract(self):
        song = _track(art=_png(QColor("#c84335")))
        with patch("local.music_library_window.artwork_surface_palette", wraps=artwork_surface_palette) as extract:
            self._select(song)
            self.assertEqual(len(self.work), 1)
            for position in range(1, 8):
                self._select(replace(song, position_seconds=position))
            self.assertEqual(len(self.work), 1)
            self.assertTrue(all(call.args[0].isNull() for call in extract.call_args_list))
            self.work.pop(0)()
            self.assertEqual(sum(not call.args[0].isNull() for call in extract.call_args_list), 1)
            calls = extract.call_count
            self._select(replace(song, position_seconds=10))
            self._advance(frames=3)
            self.assertEqual(extract.call_count, calls)

    def test_old_cover_worker_cannot_overwrite_new_song_and_hidden_cover_waits(self):
        self._select(_track(art=_png(QColor("#d04433"))))
        old_key = self.window._background_palette_current_key
        self._select(_track("second", art=_png(QColor("#2d78be"))))
        current_key = self.window._background_palette_current_key
        self.assertNotEqual(old_key, current_key)
        self.assertEqual(len(self.work), 1)
        self.work.pop(0)()
        self.assertNotEqual(self.window._background_palette_key, old_key)
        self.assertEqual(len(self.work), 1)
        self.work.pop(0)()
        self.assertEqual(self.window._background_palette_key, current_key)
        self.window.hide()
        self._select(_track("third", art=_png(QColor("#57bc43"))))
        self.assertEqual(self.work, [])
        self.window.show()
        self.assertEqual(len(self.work), 1)

    def test_settings_zero_flow_stops_ticks_and_no_track_restores_static_source_scene(self):
        self._select(_track())
        self._advance(frames=9)
        self.settings["v2_music_flow_intensity"] = 0.
        self.window.on_background_setting_changed("v2_music_flow_intensity", 0.)
        self.assertFalse(self.window._background_timer.isActive())
        before = self.window._background_seconds
        self._advance(start=500., frames=2)
        self.assertEqual(self.window._background_seconds, before)
        self.settings.update(v2_music_flow_intensity=.5, v2_animation_fps=20)
        self.window.on_background_setting_changed("v2_animation_fps", 20)
        self.assertEqual(self.window._background_timer.interval(), 50)
        self._select(None)
        self.assertFalse(self.window._background_timer.isActive())
        source = self.window.source_button.source
        expected = AlbumFlowBackgroundRenderer().render(self.window.size(), (
            QColor("#303a43"), QColor(source.color), QColor("#d1d7dd")), 0., intensity=0.)
        # The source raster stays cached across layout/position-free paints.
        key = self.window._background_frame.cacheKey()
        self.window._update_background()
        self.assertEqual(self.window._background_frame.cacheKey(), key)
        self.assertEqual(self.window._background_frame.size(), expected.size())

    def test_paused_new_song_uses_its_own_static_colors_without_starting_a_timer(self):
        self._select(_track())
        self._advance(frames=9)
        previous = self.window._background_frame.copy()
        self._select(_track("paused", primary=(37, 162, 82), secondary=(151, 49, 187), playing=False))
        self.assertFalse(self.window._background_timer.isActive())
        renderer = self.window._background_renderer
        self.assertEqual(renderer._painted_colors, renderer._target_colors)
        self.assertNotEqual(_pixels(previous), _pixels(self.window._background_frame))


if __name__ == "__main__":
    unittest.main()
