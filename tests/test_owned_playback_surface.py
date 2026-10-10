from __future__ import annotations

import hashlib
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteSnapshot
from local.branding import ROOT_DIR
from local.music_source_icons import music_source_icon_image
from local.music_source_icons import paint_music_source_icon
from local.music_surface_window import MusicSurfaceWindow


class _Provider:
    def __init__(self):
        self.mode = "sequential"
        self.owned = True
        self.calls = []
        self.accept = True

    def _owned_active(self):
        return self.owned

    def get_owned_playback_mode(self):
        return self.mode if self.owned else None

    def set_owned_playback_mode(self, mode):
        self.calls.append(mode)
        if self.accept and self.owned:
            self.mode = mode
            return True
        return False

    def get_audio_route_snapshot(self):
        return AudioRouteSnapshot(capture_scope="owned-player")


class OwnedPlaybackSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.provider = _Provider()
        self.owner.data_provider = self.provider
        self.owner._music_library_back_callback = Mock()
        self.owner.album_color, self.owner.secondary_color = QColor("#2870a0"), QColor("#202d48")
        self.owner.album_art = QImage()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window.show()
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window._sync_audio_output_status(settle=True)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _event(self, point):
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, position=lambda: point,
            globalPosition=lambda: QPointF(self.window.mapToGlobal(point.toPoint())), accept=lambda: None)

    def test_click_cycles_all_three_provider_modes_without_transport_commands(self):
        for mode in ("shuffle", "repeat_one", "sequential"):
            self.window._reset_control_idle()
            self.window._sync_window_control_rects()
            event = self._event(self.window._button_rects["playback_mode"].center())
            self.window.mousePressEvent(event)
            self.window.mouseReleaseEvent(event)
            self.assertEqual(self.provider.mode, mode)
            self.assertEqual(self.window._owned_playback_mode, mode)
            self.assertIn("点击切换", self.window.toolTip())
        self.assertEqual(self.provider.calls, ["shuffle", "repeat_one", "sequential"])
        self.owner._music_library_back_callback.assert_not_called()
        self.assertIn("歌单循环", self.window.toolTip())

    def test_mode_sits_between_device_and_translation_at_both_sizes_and_presentations(self):
        for size in ((960, 540), (640, 360)):
            self.window.resize(*size)
            for fullscreen in (False, True):
                self.window._fullscreen = fullscreen
                self.window._reset_control_idle()
                self.window._sync_window_control_rects()
                audio, playback, translation = [self.window._button_rects[key]
                    for key in ("audio_output", "playback_mode", "translation")]
                self.assertLess(audio.right(), playback.left())
                self.assertLess(playback.right(), translation.left())
                self.assertEqual(audio.size(), playback.size())
                self.assertEqual(playback.size(), translation.size())
                destination = self.window._button_rects["mode" if fullscreen else "return"]
                self.assertEqual(destination.size(), translation.size())
                self.assertAlmostEqual(destination.left() - translation.right(),
                    12 * self.window._surface_scale())
                for key in ("audio_output", "playback_mode", "translation", "library"):
                    self.assertTrue(QRectF(self.window.rect()).contains(self.window._button_rects[key]))

    def test_mode_uses_shared_idle_hiding_and_shared_control_layer(self):
        self.window._reset_control_idle(now=100.0)
        with patch("local.music_surface_window.time.monotonic", return_value=110.0):
            self.window._sync_window_control_rects()
        self.assertEqual(set(self.window._button_rects), {"library"})
        self.window._reset_control_idle()
        seen = []
        original = self.window._paint_control_layer
        def observe(painter, bounds, reveal, shift, cache_key, draw, **kwargs):
            seen.append(cache_key)
            return original(painter, bounds, reveal, shift, cache_key, draw, **kwargs)
        with patch.object(self.window, "_paint_control_layer", side_effect=observe):
            image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            self.window.render(image)
        self.assertTrue(any(key[0] == "window" and "sequential" in key for key in seen))

    def test_paint_reads_cached_mode_and_uses_supplied_library_svg(self):
        self.window._reset_control_idle()
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(self.provider, "get_owned_playback_mode", side_effect=AssertionError("paint provider read")):
            with patch("local.music_surface_window.paint_music_source_icon", wraps=__import__(
                    "local.music_source_icons", fromlist=["paint_music_source_icon"]).paint_music_source_icon) as paint:
                self.window.render(image)
        keys = [call.args[2] for call in paint.call_args_list]
        self.assertIn("sequence", keys)
        self.assertIn("library", keys)
        self.assertEqual(self.provider.calls, [])

    def test_system_or_unsupported_mode_has_no_button_and_no_setter_action(self):
        for mode in (None, "unsupported"):
            self.provider.mode = mode
            self.window._sync_owned_playback_mode()
            self.assertNotIn("playback_mode", self.window._button_rects)
            self.assertFalse(self.window._cycle_owned_playback_mode())
        self.provider.mode, self.provider.owned = "sequential", False
        self.window._sync_owned_playback_mode()
        self.assertNotIn("playback_mode", self.window._button_rects)
        self.assertFalse(self.window._cycle_owned_playback_mode())
        self.assertEqual(self.provider.calls, [])

    def test_source_change_between_press_and_release_cannot_set_system_mode(self):
        self.window._reset_control_idle()
        self.window._sync_window_control_rects()
        event = self._event(self.window._button_rects["playback_mode"].center())
        self.window.mousePressEvent(event)
        self.provider.owned = False
        self.window.mouseReleaseEvent(event)
        self.assertEqual(self.provider.calls, [])
        self.assertIsNone(self.window._owned_playback_mode)

    def test_failed_setter_keeps_confirmed_mode_and_external_change_updates_cache(self):
        self.provider.accept = False
        self.assertFalse(self.window._cycle_owned_playback_mode())
        self.assertEqual(self.window._owned_playback_mode, "sequential")
        self.provider.mode = "repeat_one"
        self.window._sync_audio_output_status()
        self.assertEqual(self.window._owned_playback_mode, "repeat_one")
        self.assertIn("单曲循环", self.window._playback_mode_tooltip())

    def _mode_control_render(self, mode, backdrop):
        self.provider.mode = mode
        self.window._sync_owned_playback_mode()
        self.window._reset_control_idle(now=100.0)
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(self.window, "_background_sample_colors", return_value=(backdrop,)):
            with QPainter(image) as painter, patch("local.music_surface_window.time.monotonic", return_value=100.0):
                with patch("local.music_surface_window.paint_music_source_icon", wraps=paint_music_source_icon) as paint:
                    self.window._paint_window_controls(painter, QRectF(self.window.rect()))
        asset = "sequence" if mode == "sequential" else mode
        icon_call = next(call for call in paint.call_args_list if call.args[2] == asset)
        circle = self.window._button_rects["playback_mode"]
        face = image.pixelColor(round(circle.center().x()), round(circle.top() + circle.height() * .12))
        return image, QColor(icon_call.kwargs["tint"]), face

    def test_shuffle_and_repeat_use_pale_enabled_face_and_readable_live_album_ink(self):
        for backdrop in (QColor("#102a40"), QColor("#e0edfc"), QColor("#20ed40")):
            with self.subTest(backdrop=backdrop.name()):
                _, default_ink, default_face = self._mode_control_render("sequential", backdrop)
                self.assertEqual(default_ink.name(), "#ffffff")
                self.assertLess(default_face.lightness(), 90)
                for mode in ("shuffle", "repeat_one"):
                    image, ink, face = self._mode_control_render(mode, backdrop)
                    self.assertNotEqual(ink.name(), default_ink.name())
                    self.assertGreater(face.lightness(), 230)
                    composite = self.window._composited_color(ink, face)
                    self.assertGreaterEqual(self.window._contrast_ratio(composite, face), 4.5)
                    circle = self.window._button_rects["playback_mode"].toAlignedRect()
                    self.assertTrue(any(image.pixelColor(x, y).red() < 180
                        for y in range(circle.top() + 4, circle.bottom() - 4)
                        for x in range(circle.left() + 4, circle.right() - 4)))

    def test_mode_tint_and_fading_control_cache_follow_album_background_changes(self):
        seen = []
        original = self.window._paint_control_layer
        def observe(painter, bounds, reveal, shift, cache_key, draw, **kwargs):
            if cache_key[0] == "window":
                seen.append(cache_key)
            return original(painter, bounds, reveal, shift, cache_key, draw, **kwargs)
        with patch.object(self.window, "_paint_control_layer", side_effect=observe):
            _, blue, _ = self._mode_control_render("shuffle", QColor("#184878"))
            _, purple, _ = self._mode_control_render("shuffle", QColor("#582868"))
        self.assertNotEqual(blue.rgba(), purple.rgba())
        self.assertNotEqual(seen[0], seen[1])
        self.assertIn(blue.rgba(), seen[0])
        self.assertIn(purple.rgba(), seen[1])


class OwnedPlaybackAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_supplied_control_bytes_local_geometry_and_render_tint(self):
        hashes = {
            "library": "1f30a8b8d6598219b2db0a2e496f493ff1b4cbc063fc60c70ebdc017737e184c",
            "shuffle": "a2784902611c85b1b3d433297ae270ba5f34cd34248e70b5bc47cb7de185eaa0",
            "sequence": "11c76facd25e99de9703b085fbcf31ace5547767bf55a745e0c2de37896210ce",
            "repeat_one": "45485b17ef9b28b87a34401eacd12db798dc6d7ced84bb6fd6d85f96fe0789da",
        }
        for key, digest in hashes.items():
            with self.subTest(key=key):
                data = (ROOT_DIR / "assets" / "music_controls" / f"{key}.svg").read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
                root = ET.fromstring(data)
                self.assertEqual({element.tag.rsplit("}", 1)[-1] for element in root.iter()}, {"svg", "path"})
                self.assertNotRegex(data, rb"<!ENTITY|<!DOCTYPE[^>]*\[|\bon\w+\s*=|url\(|<script|\bhref=")
                image = music_source_icon_image(key, QSize(100, 80), tint=QColor("#abcdef"))
                visible = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())
                    if image.pixelColor(x, y).alpha() == 255]
                self.assertGreater(len(visible), 100)
                self.assertEqual({pixel.name() for pixel in visible}, {"#abcdef"})


if __name__ == "__main__":
    unittest.main()
