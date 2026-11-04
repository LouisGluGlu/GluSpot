"""Artwork publication owns cover/chrome timing, even without playback ticks."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.models import ColorToken, IslandVisualData, TimedLyricLine, TrackData
from api.online_music import OnlineSong, StreamInfo
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider
from local.island_stack_visual import IslandStackVisual
from local.music_surface_palette import artwork_surface_palette
from local.music_surface_window import MusicSurfaceWindow
from local.owned_music_player import OwnedMusicPlayer
from _demos.narrow3.narrow_window import NarrowStudyWindow
from tests.test_owned_buffered_playback import FixtureCache
from tests.test_owned_playback import Player, Tasks


def picture(tone):
    image = QImage(72, 72, QImage.Format.Format_RGB32)
    image.fill(QColor(tone))
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(encoded)


def track(identity, tone="#245ba8", *, source="original"):
    return TrackData(identity, "Offline artist", ("A held lyric line",),
        ColorToken(*QColor(tone).getRgb()[:3]), ColorToken(20, 40, 60), (),
        track_id=identity, album_art_bytes=picture(tone), album_art_source=source,
        album_art_lookup_state="pending" if source == "thumbnail" else "original",
        is_playing=False, position_seconds=12., duration_seconds=120.,
        timed_lyrics=(TimedLyricLine(0., "A held lyric line"),))


class ArtworkControlOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.now = 100.
        clock = patch("local.music_surface_window.time.monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.provider = EmptyIslandDataProvider()
        self.data = IslandVisualData(track("first", source="thumbnail"), (), ())
        self.provider.get_visual_data = lambda: self.data
        self.owner = IslandStackVisual(self.provider)
        self.owner.timer.stop()
        self.jobs = []
        self.owner._run_background = lambda name, action: self.jobs.append((name, action))
        artwork_surface_palette(self.owner.album_art, self.owner.album_color, self.owner.secondary_color)
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.resize(360, 640)
        self.window.narrow = True
        self.window._finish_width_chrome()
        self.window.visibility.opacity.start = self.window.visibility.opacity.target = 1.
        self.window.visibility.opacity.duration = 0.
        self.window._sync_artwork_palette(self.now)
        self.window.refresh_controls(self.now)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def publish(self, incoming):
        self.data = IslandVisualData(incoming, (), ())
        self.owner._apply_visual_data(self.data)

    def assert_published_chrome(self):
        self.assertEqual(self.window._track_visual[3].cacheKey(), self.owner.album_art.cacheKey())
        self.assertEqual(self.window._controls_palette_key,
            (self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
             self.owner.secondary_color.rgba(), self.window._art_palette_cache_key))
        self.assertFalse(self.window._timer.isActive())
        self.assertFalse(self.window.study_timer.isActive())

    def test_paused_track_publication_refreshes_chrome_before_next_clock(self):
        previous_bands = self.window._controls_bands
        self.now = 102.
        self.publish(track("second", "#328742"))
        self.assertEqual(self.owner.current_visual_data.track.track_id, "second")
        self.assertFalse(self.owner.media_is_playing)
        self.assertEqual(self.owner.media_position_seconds, 12.)
        self.assert_published_chrome()
        self.assertIsNot(self.window._controls_bands, previous_bands)
        self.assertEqual(self.window._track_cover_started_at, self.now)
        self.assertEqual(self.window._track_cover_progress(self.now), 0.)
        self.assertEqual(self.window._track_cover_progress(self.now + .640), 1.)

    def test_visible_cover_and_control_samples_share_the_publication_frame(self):
        renderer = self.window._clear_control_renderer
        samples = []
        paint = renderer.paint

        def observe(painter, target, **kwargs):
            samples.append((kwargs["scene"].cacheKey(),
                renderer.live_tint_scene.cacheKey(),
                self.window._background_cache.cacheKey(),
                self.window._track_cover_progress(self.now),
                self.window._cover_upgrade_started_at,
                self.window._chrome_backdrop_key[5]))
            return paint(painter, target, **kwargs)

        self.window._pointer_hover = True
        with patch.object(self.window, "isVisible", return_value=True), \
                patch.object(renderer, "paint", side_effect=observe):
            self.window._refresh_chrome_backdrop(self.now)
            self.window.refresh_controls(self.now)
            samples.clear()
            self.now = 102.
            self.publish(track("second", "#328742"))
            self.assertTrue(samples, "publication must prepare the first control frame before another clock")
            self.assertEqual(samples[-1][:3], (self.window._chrome_backdrop.cacheKey(),
                self.window._chrome_backdrop.cacheKey(), self.window._background_cache.cacheKey()))
            self.assertEqual(samples[-1][2], samples[-1][5], "the composite must contain this background frame")
            self.assertEqual(samples[-1][3], 0., "controls begin on the flip's publication frame")
            published = self.window._track_cover_started_at
            old_bands = self.window._controls_bands
            for fraction in (.25, .5, .75, 1.):
                self.now = published + .640 * fraction
                samples.clear()
                self.window.tick()
                self.window.grab()
                self.assertTrue(samples)
                self.assertTrue(all(scene == tint and background == raw
                    for scene, tint, background, _cover, _upgrade, raw in samples))
                self.assertAlmostEqual(samples[-1][3], fraction)
            self.assertIsNot(self.window._controls_bands, old_bands)
            self.now = 104.
            samples.clear()
            self.publish(track("second", "#a86620"))
            self.assertTrue(samples, "same-track upgrade must publish controls in its first frame")
            self.assertEqual(samples[-1][4], self.now)
            self.assertEqual(samples[-1][0], samples[-1][1])
            self.assertEqual(samples[-1][2], samples[-1][5])

    def test_same_track_original_starts_cover_upgrade_on_the_publication(self):
        previous = self.owner.album_art.cacheKey()
        self.now = 102.
        self.publish(track("first", "#328742"))
        self.assert_published_chrome()
        self.assertEqual(self.window._cover_upgrade_previous.cacheKey(), previous)
        self.assertEqual(self.window._cover_upgrade_started_at, self.now)
        self.assertEqual(self.window._track_cover_started_at, 0.)

    def test_palette_worker_delivery_refreshes_chrome_without_play_or_progress(self):
        self.now = 102.
        self.publish(track("second", "#328742"))
        key = self.window._art_palette_worker_key
        token = self.window._art_palette_active_token
        old_controls = self.window._controls_bands
        result = artwork_surface_palette(self.owner.album_art, self.owner.album_color, self.owner.secondary_color)
        self.now += .05
        self.window._on_artwork_palette_ready(token, key, result)
        self.assert_published_chrome()
        self.assertEqual(self.window._controls_palette_key[-1], key)
        self.assertIsNot(self.window._controls_bands, old_controls)
        self.assertEqual(self.window._track_cover_started_at, 102., "palette enrichment cannot restart the flip")
        self.assertFalse(self.owner.media_is_playing)

    def test_deferred_extraction_uses_current_provider_tones_at_cover_publication(self):
        old_primary = self.window._art_palette().primary
        self.now = 102.
        self.publish(track("second", "#328742"))
        self.assertIsNotNone(self.window._art_palette_active_token)
        expected = artwork_surface_palette(QImage(), self.owner.album_color, self.owner.secondary_color)
        self.assertEqual(self.window._art_palette().primary, expected.primary)
        self.assertNotEqual(self.window._art_palette().primary, old_primary)
        self.assertEqual(self.window._track_visual[4], expected.primary)
        self.assert_published_chrome()

    def test_old_palette_delivery_cannot_revert_newer_artwork_or_restart_its_flip(self):
        self.now = 102.
        self.publish(track("second", "#328742"))
        key, token = self.window._art_palette_worker_key, self.window._art_palette_active_token
        result = artwork_surface_palette(self.owner.album_art, self.owner.album_color, self.owner.secondary_color)
        self.now = 102.1
        self.publish(track("third", "#a86620"))
        latest = self.window._art_palette().primary
        self.window._on_artwork_palette_ready(token, key, result)
        self.assertEqual(self.window._art_palette().primary, latest)
        self.assertEqual(self.window._track_visual[0], "third")
        self.assertEqual(self.window._track_cover_started_at, 102.1)
        self.assert_published_chrome()

    def test_provider_color_only_publication_invalidates_the_current_chrome(self):
        self.now = 102.
        image = self.owner.album_art.cacheKey()
        self.publish(replace(self.data.track, album_color=ColorToken(80, 170, 90)))
        self.assertEqual(self.owner.album_art.cacheKey(), image)
        self.assert_published_chrome()

    def test_hidden_shared_surface_observes_original_at_publication(self):
        surface = MusicSurfaceWindow(self.owner)
        self.addCleanup(surface.deleteLater)
        self.addCleanup(surface.shutdown)
        surface._on_owner_visual_data_changed()
        palette = surface._art_palette()
        surface._track_transition(self.now, palette.primary, palette.secondary)
        self.now = 102.
        self.publish(track("first", "#328742"))
        self.assertFalse(surface.isVisible())
        self.assertEqual(surface._track_visual[3].cacheKey(), self.owner.album_art.cacheKey())
        self.assertEqual(surface._cover_upgrade_started_at, self.now)

    def test_prefetched_audio_and_later_original_use_the_same_publication_order(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        tasks, downloads, artwork = Tasks(), Tasks(), Tasks()
        cache = FixtureCache(root)
        service = SimpleNamespace(stream_url=Mock(side_effect=lambda source, song:
            StreamInfo(source, song, "https://fixture.invalid/audio", True)), lyrics=lambda *_args: None)
        owned = OwnedMusicPlayer(service, LocalLyricsStore(root / "lyrics"),
            player=Player(), audio=object(), dj_player=Player(), dj_audio=object(),
            tasks=tasks, artwork_tasks=artwork, prefetch_tasks=Tasks(),
            stream_cache=cache, download_tasks=downloads)
        self.addCleanup(owned.close)
        system = EmptyIslandDataProvider()
        system.suspend_live_audio = Mock()
        system.stop_audio_cast = Mock()
        source = PlaybackSourceProvider(system, owned,
            lambda key, default=None: True if key == "v2_owned_playback_enabled" else default)
        self.addCleanup(source._owned_audio_bridge.close)
        song = OnlineSong("netease", "prefetched", "Prefetched song",
            artwork_url="https://fixture.invalid/art", duration_seconds=120.)
        self.assertTrue(owned.play_queue((replace(song, song_id="first"), song)))
        key = owned._art_key(song)
        colors = ColorToken(40, 80, 160), ColorToken(20, 40, 60)
        owned._art_cache[key] = (picture("#245ba8"), *colors)
        owned._art_sizes[key] = 320
        owned._art_cache_targets[key] = owned.ARTWORK_THUMBNAIL_SIDE
        owned._art_targets[key] = owned.ARTWORK_THUMBNAIL_SIDE
        owned._prefetched_audio = cache.prepare(service.stream_url(song.source_id, song.song_id))
        previous_streams = sum(token[0] == "stream" for token, _ in tasks.jobs)
        self.now = 102.
        self.assertTrue(owned.select_queue_index(1, owned._queue_view_serial))
        self.assertIsNotNone(owned._prepared_audio)
        self.assertEqual(sum(token[0] == "stream" for token, _ in tasks.jobs), previous_streams)
        self.owner._apply_visual_data(source.get_visual_data())
        self.assert_published_chrome()
        self.assertEqual(self.window._track_cover_started_at, self.now)
        self.now = 103.
        token = next(token for token, _ in reversed(artwork.jobs) if token[2] == key)
        artwork.completed.emit(token, (picture("#328742"), ColorToken(50, 135, 66), colors[1], 1280), None)
        self.owner._apply_visual_data(source.get_visual_data())
        self.assert_published_chrome()
        self.assertEqual(self.window._cover_upgrade_started_at, self.now)


if __name__ == "__main__":
    unittest.main()
