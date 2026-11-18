import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.models import IslandVisualData
from api.online_music import OnlineSong
from api.providers import EmptyIslandDataProvider


class NarrowPlaylistColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from local.island_stack_visual import IslandStackVisual
        from _demos.narrow3.narrow_window import NarrowStudyWindow
        from _demos.narrow3.layout import NarrowLayout
        from tests.test_narrow_v4 import lyric_track
        self.layout_type = NarrowLayout
        provider = EmptyIslandDataProvider(IslandVisualData(lyric_track(is_playing=True), (), ()))
        songs = tuple(OnlineSong("netease", str(i), f"Song {i}", artist="Artist") for i in range(83))
        provider.get_owned_playback_queue = lambda: (37, 40, songs)
        provider.prefetch_owned_queue_artwork = Mock()
        self.owner = IslandStackVisual(data_provider=provider)
        self.owner.timer.stop()
        self.owner.settings_setter = lambda key, value: None
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.window.hints = False
        self.owner._apply_visual_data(provider.get_visual_data())
        self.owner.album_art = QImage(64, 64, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(30, 90, 100))
        self.window.resize(320, 540)
        self.window.set_content_layout("playlist", animate=False)

    def tearDown(self):
        self.window.shutdown()
        self.owner._application_quitting = True
        self.owner.close()

    def test_list_stays_above_the_transport_while_this_window_shows_it(self):
        window = self.window
        # The big window's own control timer has idled out; the narrow
        # window still shows its seek bar and keys.
        window._layout_g_control_reveal = lambda: 0.0
        window.visibility.progress = 1.0
        column = window._content_column_geometry()[2]
        track = window.narrow_progress_track(self.layout_type(window.width(), window.height()))
        self.assertLess(column.bottom(), track.top() - 8.0)
        window.visibility.progress = 0.0
        hidden = window._content_column_geometry()[2]
        self.assertGreater(hidden.bottom(), window.height() - 20.0)

    def test_queue_glass_samples_the_whole_scene_not_the_button_capture(self):
        from unittest.mock import patch
        window = self.window
        window.visibility.progress = 1.0
        scenes = []
        original = window._panel_glass_scene

        def scene():
            image = original()
            scenes.append(image)
            return image

        image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(window, "_panel_glass_scene", side_effect=scene):
            window.render(image)
        self.assertTrue(scenes)
        chrome = getattr(window, "_chrome_backdrop", None)
        self.assertTrue(all(chrome is None or s is not chrome for s in scenes))


if __name__ == "__main__":
    unittest.main()
