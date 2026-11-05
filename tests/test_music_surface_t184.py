"""Cover recovery and wide chrome regressions from the t184 PC frame."""
import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication, QToolTip

from api.lyrics import LocalLyricsStore, LyricsMatch
from local.music_surface_window import MusicSurfaceWindow
from tests import test_narrow_chrome as chrome
from tests import test_owned_artwork_retry as owned
from tests import test_artwork_sources as artwork
from tests.test_music_surface_transition import _TransitionOwner


class RecoveredCoverLookupTests(unittest.TestCase):
    def test_explicit_retry_reopens_only_a_miss_and_keeps_pending_and_results(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            key = store._cache_key("Song", "Artist", "", 0.)
            other = store._cache_key("Other", "Artist", "", 0.)
            store._store_miss(key, retry_seconds=float("inf"))
            store._store_miss(other, retry_seconds=float("inf"))
            with patch("api.lyrics.threading.Thread") as thread:
                self.assertFalse(store.resolve_in_background("Song", "Artist"))
                self.assertTrue(store.resolve_in_background("Song", "Artist", retry_missed=True))
                self.assertFalse(store.resolve_in_background("Song", "Artist", retry_missed=True))
                thread.return_value.start.assert_called_once()
            self.assertIn(other, store._missed_at)
            store._pending_keys.discard(key)
            store._store_cache(key, LyricsMatch((), ("Known lyrics",), "manual"))
            self.assertFalse(store.resolve_in_background("Song", "Artist", retry_missed=True))

    def test_system_and_capture_cover_recovery_retries_once_without_erasing_cache(self):
        helper = artwork.ArtworkVisualPriorityTests()
        provider = helper.provider(b"")
        provider.lyrics_store.resolve_in_background = Mock(side_effect=lambda *a, **kw: kw.get("retry_missed", False))
        provider._last_data = asyncio.run(provider._read_visual_data())
        self.assertEqual(provider._last_data.track.lyrics, ("暂无歌词",))
        original = helper.png(1200, 0xFFB03437)
        provider._current_online_album_art_bytes = lambda _key: original
        recovered = asyncio.run(provider._read_visual_data())
        self.assertEqual(recovered.track.lyrics, ("查找歌词中",))
        retry = [call for call in provider.lyrics_store.resolve_in_background.call_args_list if call.kwargs]
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0].kwargs, {"retry_missed": True})
        provider._last_data = recovered
        asyncio.run(provider._read_visual_data())
        self.assertEqual(len([c for c in provider.lyrics_store.resolve_in_background.call_args_list if c.kwargs]), 1)

    def test_original_relabel_or_existing_lyrics_does_not_retry(self):
        helper = artwork.ArtworkVisualPriorityTests()
        for known in (False, True):
            with self.subTest(known=known):
                art = helper.png(1200, 0xFFB03437)
                provider = helper.provider(art)
                data = asyncio.run(provider._read_visual_data())
                provider._last_data = replace(data, track=replace(data.track,
                    lyrics=("Known",) if known else ("暂无歌词",),
                    album_art_source="thumbnail"))
                provider.lyrics_store.resolve_in_background = Mock(return_value=False)
                provider._current_online_album_art_bytes = lambda _key: art
                asyncio.run(provider._read_visual_data())
                self.assertFalse(any(c.kwargs for c in provider.lyrics_store.resolve_in_background.call_args_list))


class OwnedRecoveredCoverTests(unittest.TestCase):
    setUpClass = owned.OwnedArtworkRetryTests.__dict__["setUpClass"]
    setUp = owned.OwnedArtworkRetryTests.setUp
    tearDown = owned.OwnedArtworkRetryTests.tearDown

    def test_recovered_owned_cover_uses_existing_exact_id_task_once_and_no_loading_marker(self):
        player = self.owned
        player.play_queue((self.song,))
        player._stream_epoch = player._epoch
        player._lyrics_unavailable()
        key = player._art_key(self.song)
        player._art_cache[key] = (owned.picture(640), *self.colors)
        player._art_sizes[key] = 640
        before = len(player.tasks.jobs)
        player._apply_cached_artwork()
        request = player.tasks.jobs[-1][0]
        self.assertEqual(request[:3], ("lyrics", player._epoch, player._lyrics_generation))
        self.assertGreater(request[3], 0)  # The retry fence identifies this exact attempt.
        self.assertEqual(player._lyrics_request_pending[0], request)
        self.assertEqual(len(player.tasks.jobs), before + 1)
        self.assertEqual(player.track.lyrics, ("暂无歌词",))
        player._apply_cached_artwork()
        self.assertEqual(len(player.tasks.jobs), before + 1)
        action = player.tasks.jobs[-1][1]
        player._epoch += 1
        with patch.object(player, "_fetch_lyrics") as fetch:
            self.assertIsNone(action())
            fetch.assert_not_called()

    def test_recovered_cover_does_not_duplicate_pending_enrichment(self):
        player = self.owned
        player.play_queue((self.song,))
        player._stream_epoch = player._epoch
        player._lyrics_unavailable()
        player._lyrics_enrichment_pending = ("pending",)
        key = player._art_key(self.song)
        player._art_cache[key] = (owned.picture(640), *self.colors)
        player._art_sizes[key] = 640
        before = len(player.tasks.jobs)
        player._apply_cached_artwork()
        self.assertEqual(len(player.tasks.jobs), before)

    def test_full_foreground_queue_retains_one_retry_until_capacity_returns(self):
        player = self.owned
        player.play_queue((self.song,))
        player._stream_epoch = player._epoch
        player._lyrics_unavailable()
        key = player._art_key(self.song)
        player._art_cache[key] = (owned.picture(640), *self.colors)
        player._art_sizes[key] = 640
        player.tasks.accepted = False
        player._apply_cached_artwork()
        self.assertEqual(player._cover_lyrics_retry, (player._epoch, self.song))
        before = len(player.tasks.jobs)
        player.tasks.accepted = True
        player.tasks.completed.emit(("obsolete", player._epoch - 1), None, None)
        self.assertIsNone(player._cover_lyrics_retry)
        self.assertEqual(len(player.tasks.jobs), before + 1)
        self.assertEqual(player.tasks.jobs[-1][0][0], "lyrics")
        player.tasks.completed.emit(("obsolete", player._epoch - 1), None, None)
        self.assertEqual(len(player.tasks.jobs), before + 1)


class WideChromeRecoveryTests(unittest.TestCase):
    setUpClass = chrome.NarrowChromeTests.__dict__["setUpClass"]
    setUp = chrome.NarrowChromeTests.setUp
    tearDown = chrome.NarrowChromeTests.tearDown

    def test_new_wide_background_releases_gray_chrome_before_control_paint(self):
        window = self.window
        window.resize(960, 540)
        window._sync_narrow_mode()
        self.assertFalse(window.narrow)
        gray = QImage(960, 540, QImage.Format.Format_RGB32)
        gray.fill(QColor("#303030"))
        window._chrome_backdrop = gray
        window._chrome_backdrop_key = ("old",)
        window._background_cache_key = ("old",)
        red = gray.copy()
        red.fill(QColor("#b03437"))
        def publish(*_args, **_kwargs):
            window._background_cache = red
            window._background_cache_key = ("red",)
        with patch.object(MusicSurfaceWindow, "_paint_background", side_effect=publish):
            with QPainter(gray) as painter:
                window._paint_background(painter, QRectF(window.rect()), QColor("red"), QColor("red"), 100.)
        self.assertIsNone(window._chrome_backdrop)
        for role, target in (("audio-output", QRectF(730, 16, 36, 36)),
                             ("collection", QRectF(850, 490, 90, 36))):
            face = QImage(960, 540, QImage.Format.Format_ARGB32_Premultiplied)
            face.fill(Qt.GlobalColor.transparent)
            with QPainter(face) as painter:
                window._paint_surface_glass(painter, target, role=role)
            color = face.pixelColor(target.center().toPoint())
            self.assertGreater(color.red(), color.green() + 30, (role, color.getRgb()))

    def test_wide_ten_second_check_recovers_drift_without_narrow_veil_work(self):
        window = self.window
        window.resize(960, 540)
        window._sync_narrow_mode()
        window._sync_artwork_palette(100.)
        window._chrome_backdrop_key = (960, 540, 1., "lyrics", "track", -1)
        window._chrome_backdrop = QImage(10, 10, QImage.Format.Format_RGB32)
        with patch.object(window.background_veil, "prepare") as prepare:
            window._sync_artwork_palette(109.)
            self.assertIsNotNone(window._chrome_backdrop)
            window._sync_artwork_palette(110.)
            self.assertIsNone(window._chrome_backdrop)
            prepare.assert_not_called()

    def test_same_song_explicit_retry_restores_lyrics_but_terminal_marker_stays_cover_only(self):
        window, owner = self.window, self.owner
        owner.track_title = "Recovered song"
        owner.media_track_id = "recovered-song"
        saved = {}
        owner.settings_setter = lambda key, value: saved.__setitem__(key, value)
        for width in (360, 960):
            for layout in ("lyrics", "cover_lyrics"):
                with self.subTest(width=width, layout=layout):
                    window.resize(width, 640)
                    window._sync_narrow_mode()
                    window.set_content_layout(layout, animate=False)
                    baseline = dict(saved)
                    owner.lyrics, owner.timed_lyrics = ["暂无歌词"], []
                    with patch.object(window, "_display_lyrics", return_value=(["暂无歌词"], [])):
                        window._update_lyric_loading_state(100.)
                        window._sync_no_lyrics_content_layout(100.)
                    self.assertEqual(window._effective_content_layout(), "cover")
                    # A quiet retry retaining the provider's confirmed miss
                    # keeps the terminal presentation until data changes.
                    with patch.object(window, "_display_lyrics", return_value=(["暂无歌词"], [])):
                        window._update_lyric_loading_state(100.05)
                        window._sync_no_lyrics_content_layout(100.05)
                        self.assertEqual(window._effective_content_layout(), "cover")
                        self.assertFalse(window._lyric_loading_visible(100.05))
                    window._cover_layout_to = 0.0  # The cover travel has not settled yet.
                    owner.lyrics = ["查找歌词中"]
                    with patch.object(window, "_display_lyrics", return_value=(["查找歌词中"], [])):
                        for now in (100.1, 101.5, 104.):
                            window._update_lyric_loading_state(now)
                            window._sync_no_lyrics_content_layout(now)
                            self.assertEqual(window._lyric_content_state(now), "loading")
                            self.assertEqual(window._effective_content_layout(), "lyrics")
                            self.assertFalse(window._no_lyrics_layout_active)
                            self.assertEqual(window._lyric_loading_visible(now), now >= 101.1)
                            # Pending cover-lyrics reopens an external column;
                            # it never places its skeleton over the artwork.
                            self.assertEqual(window._content_layout, layout)
                            self.assertEqual(saved, baseline)
                    owner.lyrics, owner.timed_lyrics = ["Found"], [(0., "Found")]
                    with patch.object(window, "_display_lyrics", return_value=(["Found"], [(0., "Found")])):
                        window._update_lyric_loading_state(105.)
                        window._sync_no_lyrics_content_layout(105.)
                    self.assertEqual(window._effective_content_layout(), "lyrics")
                    self.assertEqual(window._content_layout, layout)
                    self.assertEqual(saved, baseline)


class MetadataAndTooltipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        QToolTip.hideText()
        self.window.shutdown()
        self.window.close()
        self.owner.close()

    def test_long_title_rests_with_a_right_fade_then_scrolls(self):
        text = "Human Sacrifice——Daniel Caesar"
        font = self.window._surface_font(28, QFont.Weight.Bold)
        slot = QRectF(10, 10, 120, 40)
        def render(now):
            image = QImage(160, 60, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                painter.setPen(QColor("white"))
                painter.setFont(font)
                self.window._paint_scrolling_metadata(painter, slot, text, font, now, "song", field="title")
            return image
        with patch.object(self.window, "_control_reveal_at", return_value=1.):
            initial = render(100.)
            left = max(initial.pixelColor(x, 30).alpha() for x in range(12, 24))
            self.assertGreater(left, 80)
            self.assertLess(initial.pixelColor(129, 30).alpha(), 40)
            from local.metadata_marquee import START_HOLD_FRACTION, TRAVEL_FRACTION, cycle_seconds
            duration = cycle_seconds(
                self.window._metadata_text_widths["title"], self.window._metadata_speed_scale("title"),
            )
            moving = render(100. + START_HOLD_FRACTION * duration + TRAVEL_FRACTION * duration * 0.4)
            self.assertNotEqual(initial, moving)
            self.assertEqual(self.window._metadata_marquee_states["title"][2], 100.)

    def test_read_and_unread_lyric_rasters_have_no_chromatic_word_ink(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        text = "New words every night"
        layout, height = self.window._wrapped_lyric_layout(text, font, 600.)
        self.owner.album_color = QColor("#b03437")
        for progress in (0., .37, 1.):
            image = QImage(640, 100, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window._paint_wrapped_lifted_lyric(painter, QRectF(20, 20, 600, height),
                    text, font, layout, progress)
            rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
            pixels = np.frombuffer(rgba.constBits(), np.uint8).reshape(100, 640, 4)
            self.assertTrue(np.any(pixels[:, :, 3]))
            self.assertTrue(np.all(pixels[:, :, 0] == pixels[:, :, 1]))
            self.assertTrue(np.all(pixels[:, :, 1] == pixels[:, :, 2]))

    def test_actual_music_tooltip_renders_dark_with_existing_text_contract(self):
        self.window.setToolTip("返回岛模式")
        QToolTip.showText(QPoint(100, 100), self.window.toolTip(), self.window)
        self.app.processEvents()
        tip = next(widget for widget in self.app.topLevelWidgets() if widget.objectName() == "qtooltip_label")
        image = tip.grab().toImage()
        self.assertEqual(image.pixelColor(3, 3).name(), "#24262b")
        self.assertEqual(QToolTip.text(), "返回岛模式")


if __name__ == "__main__":
    unittest.main()
