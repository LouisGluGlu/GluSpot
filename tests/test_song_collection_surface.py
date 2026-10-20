from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QApplication

from api.online_music import OnlinePlaylist
from local.music_surface_window import MusicSurfaceWindow
from local.song_collection_controller import SongCollectionState
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class Collection(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.state = SongCollectionState("owned:netease:1", "netease", "u", True, False)
        self.authority = object()
        self.toggle_favorite = Mock()
        self.load_playlists = Mock()
        self.add_to_playlist = Mock()
        self.refresh = Mock()

    def context(self, track_id):
        return self.state if self.state.track_id == track_id else SongCollectionState()

    def capture_authority(self, track_id):
        return self.authority if self.context(track_id).track_id else None

    def authority_matches(self, track_id, authority):
        return authority is not None and authority is self.authority and bool(self.context(track_id).track_id)


class SongCollectionSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.media_track_id = "owned:netease:1"
        self.owner.data_provider = _Provider()
        self.owner.data_provider._owned_active = lambda: True
        self.collection = Collection()
        self.owner._song_collection_controller = self.collection
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window.show()
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window._reset_control_idle()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self):
        self.window._reset_control_idle()
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def event(self, point):
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, position=lambda: point,
            globalPosition=lambda: QPointF(self.window.mapToGlobal(point.toPoint())), accept=lambda: None)

    def activate(self, key):
        track_id = self.owner.media_track_id
        return self.window._activate_song_collection(key, track_id,
                    self.window._capture_song_collection_authority(track_id))

    def test_shared_capsule_stays_in_bounds_and_does_not_cover_dj_or_transport(self):
        for size in ((200, 400), (303, 400), (640, 400), (960, 540), (1920, 1080)):
            for mode in ("cover", "cover_lyrics", "playlist", "lyrics"):
                self.window.resize(*size)
                self.window.set_content_layout(mode, animate=False)
                self.render()
                region = QRectF(self.window.rect())
                face = self.window._song_collection_geometry(region)
                self.assertTrue(region.contains(face), (size, face))
                self.assertFalse(face.intersects(self.window._media_control_panel_rect), (size, mode, face))
                icon, caption = self.window._personal_dj_geometry(region, 100., expansion=0.)
                self.assertFalse(face.intersects(icon))

    def test_unrelated_dirty_region_skips_footer_material_without_losing_footer_hits(self):
        with patch("local.music_surface_window.time.monotonic", return_value=100.):
            self.window._reset_control_idle(100.)
            output = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            self.window.render(output)
            unrelated = QRect(5, 5, 24, 24)
            expected = output.copy(unrelated)
            with patch.object(self.window, "_paint_song_collection_control", wraps=self.window._paint_song_collection_control) as draw:
                self.window.render(output, unrelated.topLeft(), QRegion(unrelated))
            draw.assert_not_called()
            self.assertEqual(output.copy(unrelated), expected)
            self.assertIn("favorite", self.window._button_rects)
            face = self.window._song_collection_geometry(QRectF(self.window.rect()))
            with patch.object(self.window, "_paint_song_collection_control", wraps=self.window._paint_song_collection_control) as draw:
                damage = QRegion(face.adjusted(-4, -4, 4, 4).toAlignedRect())
                self.window.render(output, damage.boundingRect().topLeft(), damage)
            draw.assert_called_once()

    def test_narrow_source_endpoint_fits_complete_metadata_and_transport_above_footer(self):
        for size in ((200, 400), (303, 400)):
            self.window.resize(*size)
            self.window.set_content_layout("cover", animate=False)
            region = QRectF(self.window.rect())
            scale = self.window._surface_scale(region)
            content, _cover, _column = self.window._content_column_geometry(region)
            for reveal in (0., .2, .5, 1.):
                with self.subTest(size=size, reveal=reveal), patch.object(self.window, "_control_reveal_at", return_value=reveal):
                    source, _centered = self.window._cover_layout_endpoints(100.)
                    _title_font, title, _artist_font, artist = self.window._metadata_layout(source, content, scale, reveal)
                    transport_top = artist.bottom() + 12. * scale
                    transport_bottom = transport_top + self.window.TRANSPORT_PANEL_HEIGHT * scale
                    self.assertLess(source.bottom(), title.top())
                    self.assertLess(artist.bottom(), transport_top)
                    self.assertLess(transport_bottom, self.window._song_collection_geometry(region).top())

    def test_centering_handoff_does_not_move_transport_through_collection_footer(self):
        self.window.resize(303, 400)
        self.window.set_content_layout("cover", animate=False)
        region = QRectF(self.window.rect())
        for fraction in (0., .2, .5, .8, 1.):
            with self.subTest(fraction=fraction), \
                    patch.object(self.window, "_cover_layout_fraction_at", return_value=fraction), \
                    patch.object(self.window, "_control_reveal_at", return_value=1.):
                self.window._cover_layout_from = 0.
                self.window._cover_layout_to = 1.
                self.render()
                face = self.window._song_collection_geometry(region)
                self.assertFalse(face.intersects(self.window._media_control_panel_rect))
                self.assertLess(self.window._media_control_panel_rect.bottom(), face.top())

    def test_held_heart_gesture_cannot_favorite_the_next_song(self):
        self.render()
        point = self.window._button_rects["favorite"].center()
        self.window.mousePressEvent(self.event(point))
        self.owner.media_track_id = "owned:netease:2"
        self.collection.state = replace(self.collection.state, track_id=self.owner.media_track_id)
        self.window.mouseReleaseEvent(self.event(point))
        self.collection.toggle_favorite.assert_not_called()

    def test_heart_click_changes_only_current_song_and_unknown_refreshes(self):
        self.activate("favorite")
        self.collection.toggle_favorite.assert_called_once_with("owned:netease:1",
                                                               expected_authority=self.collection.authority)
        self.collection.state = replace(self.collection.state, liked=None)
        self.activate("favorite")
        self.collection.refresh.assert_called_once_with()

    def test_click_only_panel_reuses_real_playlists_and_closes_on_track_change(self):
        self.collection.state = replace(self.collection.state, playlists=(OnlinePlaylist("netease", "9", "自建歌单"),), playlists_loaded=True)
        self.activate("playlist_add")
        panel = self.window._song_playlist_panel
        self.assertTrue(panel.isVisible())
        self.assertTrue(QRectF(self.window.rect()).contains(QRectF(panel.geometry())))
        from local.liquid_glass import GlassPushButton
        button = next(button for button in panel.scroll.widget().findChildren(GlassPushButton) if button.text() == "自建歌单")
        button.click()
        self.collection.add_to_playlist.assert_called_once_with("9", "owned:netease:1",
                                                               expected_authority=self.collection.authority)
        self.owner.media_track_id = "owned:netease:2"
        self.collection.changed.emit()
        self.assertFalse(panel.isVisible())

    def test_held_heart_press_rejects_account_change_and_same_user_relogin(self):
        self.render()
        point = self.window._button_rects["favorite"].center()
        for user_id in ("other", "other"):
            with self.subTest(user_id=user_id):
                self.window.mousePressEvent(self.event(point))
                self.collection.authority = object()
                self.collection.state = replace(self.collection.state, user_id=user_id)
                self.collection.changed.emit()
                self.window.mouseReleaseEvent(self.event(point))
                self.collection.toggle_favorite.assert_not_called()

    def test_panel_opened_during_initial_status_load_fetches_lists_once_when_ready(self):
        self.collection.state = replace(self.collection.state, available=False, busy=True,
                                        liked=None, message="正在读取收藏状态…")
        self.activate("playlist_add")
        panel = self.window._song_playlist_panel
        self.assertTrue(panel.isVisible())
        self.collection.load_playlists.assert_not_called()
        self.collection.state = replace(self.collection.state, available=True, busy=False, liked=False)
        self.collection.changed.emit()
        self.collection.changed.emit()
        panel.refresh()
        self.collection.load_playlists.assert_called_once_with("owned:netease:1", more=False,
                                                               expected_authority=self.collection.authority)
        self.collection.state = replace(self.collection.state, message="请求超时，请刷新状态后重试")
        self.collection.changed.emit()
        self.assertEqual(self.collection.load_playlists.call_count, 1, "no automatic retry")
        panel.more.click()
        self.assertEqual(self.collection.load_playlists.call_count, 2, "explicit retry remains available")

    def test_old_panel_buttons_cannot_rebind_to_new_account_without_changed_signal(self):
        self.collection.state = replace(self.collection.state,
            playlists=(OnlinePlaylist("netease", "9", "自建歌单"),), playlists_loaded=True)
        self.activate("playlist_add")
        panel = self.window._song_playlist_panel
        from local.liquid_glass import GlassPushButton
        button = next(button for button in panel.scroll.widget().findChildren(GlassPushButton)
                      if button.text() == "自建歌单")
        self.collection.authority = object()
        button.click()
        panel.more.click()
        self.collection.add_to_playlist.assert_not_called()
        self.collection.load_playlists.assert_not_called()
        self.collection.changed.emit()
        self.assertFalse(panel.isVisible())

    def test_old_panel_row_cannot_add_after_visual_song_change_before_notification(self):
        self.collection.state = replace(self.collection.state,
            playlists=(OnlinePlaylist("netease", "9", "自建歌单"),), playlists_loaded=True)
        self.activate("playlist_add")
        panel = self.window._song_playlist_panel
        from local.liquid_glass import GlassPushButton
        button = next(button for button in panel.scroll.widget().findChildren(GlassPushButton)
                      if button.text() == "自建歌单")
        self.owner.media_track_id = "owned:netease:2"
        button.click()
        self.collection.add_to_playlist.assert_not_called()

    def test_system_media_does_not_inherit_retained_owned_collection(self):
        self.owner.data_provider._owned_active = lambda: False
        self.render()
        self.assertNotIn("favorite", self.window._button_rects)
        self.assertFalse(self.window._song_collection_geometry(QRectF(self.window.rect())).isValid())

    def test_chooser_keeps_real_cover_rows_stable_during_busy_and_uses_scene_ink(self):
        from local.music_song_collection import CollectionGlassButton
        cover = QImage(32, 32, QImage.Format.Format_ARGB32)
        cover.fill(QColor("blue"))
        self.collection.playlist_artwork = Mock(return_value=cover)
        self.collection.request_playlist_artwork = Mock()
        self.collection.state = replace(self.collection.state, playlists=tuple(
            OnlinePlaylist("netease", str(i), f"歌单 {i}", f"https://example.test/{i}.png", 20 + i)
            for i in range(10)), playlists_loaded=True)
        with patch.object(self.window, "_scene_control_ink", return_value=QColor("#121212")):
            self.activate("playlist_add")
            panel = self.window._song_playlist_panel
            panel.layout().activate()
            panel._visible_artwork()
            first = panel._row_buttons["0"]
            self.assertIsInstance(first, CollectionGlassButton)
            self.assertGreaterEqual(first.height(), 76)
            self.assertEqual(first.artwork.pixelColor(0, 0), QColor("blue"))
            self.assertGreater(panel.scroll.verticalScrollBar().maximum(), 0)
            self.assertIn("#121212", panel.title.styleSheet())
            self.collection.state = replace(self.collection.state, busy=True)
            self.collection.changed.emit()
            self.assertIs(panel._row_buttons["0"], first)
            self.assertFalse(first.isEnabled())
        self.collection.request_playlist_artwork.assert_not_called()
        with patch.object(self.window, "_scene_control_ink", return_value=QColor("#ffffff")):
            self.render()
            self.assertIn("#ffffff", panel.title.styleSheet())
            self.assertIn("#ffffff", panel.message.styleSheet())
        panel.hide()
        self.assertFalse(panel._pulse.isActive())

    def test_chooser_and_dual_button_share_existing_elastic_click_curve(self):
        self.collection.state = replace(self.collection.state,
            playlists=(OnlinePlaylist("netease", "9", "自建歌单"),), playlists_loaded=True)
        self.activate("playlist_add")
        row = self.window._song_playlist_panel._row_buttons["9"]
        row.click()
        row._click.setCurrentTime(120)
        self.assertGreater(row._click_scale(), 1.1)
        row._click.setCurrentTime(300)
        self.assertLess(row._click_scale(), 1.)
        self.assertIn("playlist_add", self.window._song_collection_clicks)

    def test_compact_shared_footer_never_overlaps_dj_during_speech_and_status(self):
        provider = self.owner.data_provider
        provider.fm = True
        provider.get_owned_personal_dj_enabled = lambda: True
        provider.is_owned_personal_dj_speaking = lambda: self.window._owned_dj_speaking
        provider.get_personal_dj_presentation = lambda: {}
        for size in ((200, 400), (303, 400), (960, 540)):
            self.window.resize(*size)
            rect = QRectF(self.window.rect())
            self.window._owned_personal_dj_enabled = True
            self.window._owned_dj_speaking = False
            self.window._dj_activity_from = self.window._dj_activity_to = 0.
            self.window._dj_introduction_pending = False
            self.window._dj_introduction_failed = False
            self.window._footer_dj_lane_width(rect, 100.)
            for status in ("speaking", "pending", "idle"):
                self.window._owned_dj_speaking = status == "speaking"
                current = self.window._personal_dj_activity_at(101.)
                self.window._dj_activity_from = current
                self.window._dj_activity_to = float(status == "speaking")
                self.window._dj_activity_started_at = 101.
                self.window._dj_introduction_pending = status == "pending"
                for now in (101., 101.08, 101.16, 101.32, 102.):
                    with self.subTest(size=size, status=status, now=now):
                        face = self.window._song_collection_geometry(rect, now, shifted=False)
                        icon, caption = self.window._personal_dj_geometry(rect, now)
                        self.assertTrue(rect.contains(face))
                        self.assertFalse(face.intersects(caption))
                        self.assertFalse(face.intersects(icon))
                        self.assertAlmostEqual(face.bottom(), caption.bottom(), delta=.5)
                        if status == "pending":
                            prompt = self.window._personal_dj_outro_status_rect(rect, now)
                            self.assertFalse(face.intersects(prompt))

    def test_login_navigation_uses_supplied_glyph_and_never_sends_playback(self):
        self.owner._music_login_required = lambda: True
        self.owner._music_login_callback = Mock()
        self.owner._music_library_back_callback = Mock()
        self.render()
        self.assertTrue(self.window._library_back_available())
        with patch("local.music_surface_window.paint_music_source_icon") as paint:
            self.window._paint_library_back
            self.window._control_layer_cache.clear()
            self.render()
        self.assertTrue(any(call.args[2] == "login" for call in paint.call_args_list))
        self.assertTrue(self.window.return_to_library())
        self.owner._music_login_callback.assert_called_once_with()
        self.owner._music_library_back_callback.assert_not_called()
