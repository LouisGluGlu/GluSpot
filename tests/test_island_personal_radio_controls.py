"""Personal-radio island controls use the existing presentation and transport paths."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.providers import EMPTY_VISUAL_DATA, EmptyIslandDataProvider, PlaybackSourceProvider
from local.island_stack_visual import IslandKind, IslandStackVisual
from tests import test_personal_dj_completion as decoder_fixtures
from tests.test_personal_dj_playback import segments
from tests.test_personal_dj_prefetch import parts


class _RadioProvider(EmptyIslandDataProvider):
    def __init__(self):
        track = replace(EMPTY_VISUAL_DATA.track, title="A real radio song", artist="Radio artist",
                        track_id="owned:netease:radio", source_app="网易云音乐", is_playing=True,
                        position_source="owned", duration_seconds=180., lyrics=("Radio lyric",))
        super().__init__(replace(EMPTY_VISUAL_DATA, track=track))
        self.row = 0
        self.fm = True
        self.speaking = True
        self.commands = []

    def is_owned_personal_fm(self):
        return self.fm

    def get_owned_playback_queue(self):
        return 1, self.row, (object(), object())

    def get_personal_dj_presentation(self):
        return {"speaking": self.speaking, "next_title": self.visual_data.track.title, "token": 1}

    def send_playback_command(self, command):
        self.commands.append(command)
        if command == "toggle_play_pause":
            self.speaking = not self.speaking
        return True


class IslandPersonalRadioControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.provider = _RadioProvider()
        self.visual = IslandStackVisual(self.provider)
        self.visual.timer.stop()
        self.visual._run_background = lambda _name, action: action()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        self.visual.music_auto_detect = False
        self.visual.music_playing = True
        self.visual.track_title = "A real radio song"
        self.visual.artist = "Radio artist"
        self.visual.media_track_id = "owned:netease:radio"
        self.visual.media_source_app = "网易云音乐"
        self.visual.media_is_playing = True
        self.visual.media_duration_seconds = 180.
        self.visual.media_position_seconds = self.visual.lyric_last_sync_position = 30.
        self.visual._estimated_media_position = lambda _now: 30.
        self.visual.lyrics = ["Radio lyric", "Following radio lyric"]
        self.visual.timed_lyrics = [(0., self.visual.lyrics[0]), (40., self.visual.lyrics[1])]
        self.visual.timed_lyric_units = [(), ()]
        self.visual.timed_lyric_timing_kinds = ["line", "line"]
        self.visual.lyric_index = self.visual.prev_lyric_index = 0
        self.visual.lyric_changed_at = 0.
        self.visual.album_art = QImage(80, 80, QImage.Format.Format_RGB32)
        self.visual.album_art.fill(QColor("#087d86"))
        self.visual.album_color = QColor("#087d86")
        self.visual.secondary_color = QColor("#184f72")
        self.visual.music_expanded_layout_style = "standard"
        self.visual.music_surface_transition_started_at = 0.
        self.visual._sync_personal_dj_island(100.)

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def paint(self, *, expanded=True, style="sea_level", painter_method=None):
        self.visual.expanded = expanded
        self.visual.expansion = 1. if expanded else 0.
        self.visual.set_music_ambience_style(style, notify=False)
        rect = QRectF(8., 8., 520., 136. if expanded else 112.)
        image = QImage(540, 160, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.island_stack_visual.system_dark_mode", return_value=False), \
                patch("local.island_stack_visual.time.monotonic", return_value=100.):
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                if painter_method is None:
                    self.visual._paint_music(painter, rect, expanded)
                else:
                    painter_method(painter, rect)
        return image, rect

    def test_collapsed_radio_rings_expose_existing_fullscreen_hover_and_activation(self):
        for style in ("sea_level", "solid_album", "system"):
            with self.subTest(style=style):
                stale_cover = QRectF(450., 130., 20., 20.)
                self.visual.music_cover_rect = stale_cover
                self.paint(expanded=False, style=style)
                cover = QRectF(self.visual.music_cover_rect)
                self.assertFalse(cover.isEmpty())
                self.assertNotEqual(cover, stale_cover)
                self.assertEqual(self.visual.music_button_rects, {})
                self.assertEqual(self.visual._hit_test(cover.center()), "music:open_surface")
                self.assertNotEqual(self.visual._hit_test(stale_cover.center()), "music:open_surface")
                self.visual.pointer_pos = cover.center()
                for frame in range(40):
                    self.visual._advance_music_cover_hover(100. + frame / 60.)
                with patch("local.island_stack_visual.paint_presentation_glyph") as glyph:
                    self.paint(expanded=False, style=style)
                self.assertEqual(glyph.call_count, 1)
                self.assertEqual(glyph.call_args.args[2], "fullscreen")
                with patch.object(self.visual, "_show_music_fullscreen") as open_surface:
                    self.visual._activate_key(self.visual._hit_test(cover.center()))
                open_surface.assert_called_once_with()

    def test_speaking_can_expand_and_uses_the_expanded_shell_in_all_backgrounds(self):
        self.visual._activate_key("music")
        self.assertTrue(self.visual.expanded)
        for style in ("sea_level", "solid_album", "system"):
            with self.subTest(style=style), patch.object(self.visual, "_paint_audio_device_background",
                    wraps=self.visual._paint_audio_device_background) as background:
                _image, rect = self.paint(style=style)
                self.assertTrue(background.call_args.kwargs["expanded"])
                self.assertTrue(self.visual.expanded)
                self.assertGreater(self.visual._base_main_size_for_kind(IslandKind.MUSIC, rect.width())[1],
                                   self.visual._u(112.))
                self.assertEqual(set(self.visual.music_button_rects),
                                 {"music:toggle_play_pause", "music:next"})
                cover = self.visual.music_cover_rect
                inset = cover.left() - rect.left()
                self.assertAlmostEqual(cover.top() - rect.top(), inset)
                self.assertAlmostEqual(rect.bottom() - cover.bottom(), inset)
                self.assertAlmostEqual(cover.height(), rect.height() - 2.0 * inset)
                for button in self.visual.music_button_rects.values():
                    self.assertTrue(rect.contains(button))
                    self.assertFalse(self.visual.music_cover_rect.intersects(button))

    def test_radio_reuses_normal_track_transport_geometry_fill_and_radius(self):
        self.provider.row = 1
        with patch.object(self.visual, "_paint_music_control_button") as button:
            self.paint()
            radio = [(QRectF(call.args[1]), call.args[2:], call.kwargs)
                     for call in button.call_args_list]
            button.reset_mock()
            self.provider.speaking = False
            self.visual._sync_personal_dj_island(100.)
            self.paint()
            ordinary = [(QRectF(call.args[1]), call.args[2:], call.kwargs)
                        for call in button.call_args_list]
        self.assertEqual(radio, ordinary)
        self.assertEqual([record[1][0] for record in radio],
                         ["music:previous", "music:toggle_play_pause", "music:next"])

    def test_first_fm_song_omits_previous_and_history_restores_it(self):
        for speaking in (True, False):
            for row in (0, 1):
                with self.subTest(speaking=speaking, row=row):
                    self.provider.speaking = speaking
                    self.provider.row = row
                    self.visual._sync_personal_dj_island(100.)
                    self.paint()
                    self.assertEqual("music:previous" in self.visual.music_button_rects, row > 0)
                    self.assertIn("music:toggle_play_pause", self.visual.music_button_rects)
                    self.assertIn("music:next", self.visual.music_button_rects)

    def test_unknown_fm_history_does_not_expose_a_dead_previous_button(self):
        for queue in (lambda: (None, -1, ()), lambda: (None, "0", ()),
                      Mock(side_effect=RuntimeError("unavailable"))):
            with self.subTest(queue=queue), patch.object(self.provider, "get_owned_playback_queue", queue):
                self.paint()
                self.assertNotIn("music:previous", self.visual.music_button_rects)

    def test_other_normal_island_transport_presentations_also_omit_unavailable_previous(self):
        self.provider.speaking = False
        self.visual._sync_personal_dj_island(100.)
        painters = (self.visual._paint_music_expanded_dual_panel,
                    self.visual._paint_album_island_expanded,
                    self.visual._paint_taskbar_music_hover_overlay)
        with patch.object(self.visual, "_taskbar_music_hover_overlay_active", return_value=True):
            for painter_method in painters:
                for row in (0, 1):
                    with self.subTest(painter=painter_method.__name__, row=row):
                        self.provider.row = row
                        self.paint(painter_method=painter_method)
                        self.assertEqual("music:previous" in self.visual.music_button_rects, row > 0)
                        self.assertIn("music:toggle_play_pause", self.visual.music_button_rects)
                        self.assertIn("music:next", self.visual.music_button_rects)

    def test_visible_controls_dispatch_existing_provider_commands(self):
        self.provider.row = 1
        self.paint()
        keys = list(self.visual.music_button_rects)
        for key in keys:
            center = self.visual.music_button_rects[key].center()
            self.assertEqual(self.visual._hit_test(center), key)
            self.visual._activate_key(self.visual._hit_test(center))
        self.assertEqual(self.provider.commands, ["previous", "toggle_play_pause", "next"])

    def test_normal_track_on_other_platform_keeps_existing_transport(self):
        self.provider.fm = self.provider.speaking = False
        self.visual._sync_personal_dj_island(100.)
        for source in ("QQ音乐", "酷狗音乐"):
            with self.subTest(source=source):
                self.visual.media_source_app = source
                self.paint()
                self.assertEqual(set(self.visual.music_button_rects),
                                 {"music:previous", "music:toggle_play_pause", "music:next"})

    def decoder_fixture(self, batch=None):
        fixture = decoder_fixtures.PersonalDjCompletionTests("test_pause_resume_and_intermediate_part_loading_are_not_completion")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.start(batch)
        system = SimpleNamespace(suspend_live_audio=lambda: None, stop_audio_cast=lambda: None)
        self.provider = PlaybackSourceProvider(system, fixture.owned,
                                              lambda _key, default=None: default)
        self.addCleanup(self.provider._owned_audio_bridge.close)
        self.visual.data_provider = self.provider
        self.visual.media_track_id = fixture.owned.track.track_id
        self.visual.track_title = fixture.owned.track.title
        self.visual._sync_personal_dj_island(100.)
        return fixture

    def test_real_decoder_pause_restores_normal_expanded_resume_then_next_skips_intro(self):
        fixture = self.decoder_fixture()
        self.assertTrue(self.provider.is_owned_personal_dj_speaking())
        self.paint()
        self.assertNotIn("music:previous", self.visual.music_button_rects)
        self.visual._activate_key("music:toggle_play_pause")
        self.assertEqual(fixture.music.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(fixture.speech.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.visual._sync_personal_dj_island(100.1)
        self.assertFalse(self.visual._personal_dj_island_presenting())
        self.paint()
        self.assertTrue(self.visual.expanded)
        self.assertIn("music:toggle_play_pause", self.visual.music_button_rects)
        self.visual._activate_key("music:toggle_play_pause")
        self.assertEqual(fixture.music.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(fixture.speech.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.visual._sync_personal_dj_island(100.2)
        self.assertTrue(self.visual._personal_dj_island_presenting())
        self.paint()
        self.visual._activate_key("music:next")
        self.assertEqual(fixture.owned.track.track_id, "owned:netease:song-2")
        self.assertEqual(self.provider.get_owned_playback_queue()[1], 1)
        self.paint()
        self.assertIn("music:previous", self.visual.music_button_rects)

    def test_multipart_voice_queue_exposes_previous_only_for_a_prior_song(self):
        fixture = self.decoder_fixture(parts(1, 2) + segments(2) + segments(3) + segments(4))
        self.assertEqual(self.provider.get_owned_playback_queue()[1], 0)
        self.assertFalse(self.provider.send_playback_command("previous"))
        self.paint()
        self.assertNotIn("music:previous", self.visual.music_button_rects)
        self.assertTrue(self.provider.send_playback_command("next"))
        self.assertEqual(self.provider.get_owned_playback_queue()[1], 1)
        self.paint()
        self.assertIn("music:previous", self.visual.music_button_rects)
        self.visual._activate_key("music:previous")
        self.assertEqual(fixture.owned.track.track_id, "owned:netease:song-1")
        self.assertEqual(self.provider.get_owned_playback_queue()[1], 0)


if __name__ == "__main__":
    unittest.main()
