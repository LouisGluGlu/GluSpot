from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.cantonese import cantonese_phonetics, has_cantonese_evidence
from api.lyrics import LocalLyricsStore
from api.models import TimedLyricLine, TimedLyricUnit
from api.providers import WindowsMediaProvider
from local.island_stack_visual import IslandStackVisual
from local.lyric_unit_timing import duet_estimated_read_start
from local.music_lyric_presentation import present_lyric_text, split_display_layers
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


class LyricRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_timing_enrichment_cannot_reset_a_filtered_clock_spike(self):
        owner = _LayerOwner()
        owner.media_position_source = "windows-media"
        owner.media_position_sampled_at = 100.0
        owner.media_position_seconds = 10.0
        owner._estimated_media_position = lambda now: owner.media_position_seconds
        surface = MusicSurfaceWindow(owner)
        self.addCleanup(owner.close)
        self.addCleanup(surface.shutdown)
        rows = [(float(index * 4), str(index)) for index in range(40)]
        surface._lyric_frame(rows, 100.0)
        owner.media_position_seconds = 90.0
        owner.media_position_sampled_at = 100.1
        surface._lyric_frame(rows, 100.1)
        enriched = [(start, text + "\nTranslation") for start, text in rows]
        for now in (100.2, 100.7, 101.2):
            frame = surface._lyric_frame(enriched, now)
            self.assertLessEqual(frame.active_index, 3)
            self.assertAlmostEqual(surface._lyric_motion.last_position, 10.0 + now - 100.0)

    def test_quick_adjacent_originals_keep_independent_timestamps_and_units(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        a = (TimedLyricUnit(10.0, 10.06, "A"),)
        b = (TimedLyricUnit(10.06, 11.0, "B"),)
        source = [TimedLyricLine(10.0, "A", a, "unit"), TimedLyricLine(10.06, "B", b, "unit")]
        rows, units, kinds = visual._coalesced_timed_lyric_data(source)
        self.assertEqual(rows, [(10.0, "A"), (10.06, "B")])
        self.assertEqual(units, [a, b])
        self.assertEqual(kinds, ["unit", "unit"])
        self.assertEqual(MusicSurfaceWindow._coalesced_timed_lyrics(rows), rows)

    def test_same_time_duet_keeps_two_real_clocks_through_owner_handoff(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.artist = "薛之谦/刘惜君"
        a = (TimedLyricUnit(10.0, 11.0, "薛："), TimedLyricUnit(11.0, 12.0, "甲"))
        b = (TimedLyricUnit(10.0, 10.2, "刘："), TimedLyricUnit(10.2, 13.0, "乙"))
        rows, units, kinds = visual._coalesced_timed_lyric_data([
            TimedLyricLine(10.0, "薛：甲", a, "unit"), TimedLyricLine(10.0, "刘：乙", b, "unit"),
        ])
        self.assertEqual(rows, [(10.0, "薛：甲"), (10.0, "刘：乙")])
        self.assertEqual(units, [a, b])
        self.assertEqual(kinds, ["unit", "unit"])

    def test_untimed_duet_voice_does_not_start_during_timed_singer_label(self):
        # Local screenshot fixture: row time 188.34, sung body at 191.62.
        units = (TimedLyricUnit(188.34, 189.98, "薛 "),
                 TimedLyricUnit(189.98, 191.62, ": "),
                 TimedLyricUnit(191.62, 191.83, "我"),
                 TimedLyricUnit(191.83, 192.55, "记"))
        onset = duet_estimated_read_start(188.34, "薛 : 我记", units, (4, 5, 6), peer_source_start=188.34)
        self.assertAlmostEqual(onset, 191.62)
        self.assertEqual(duet_estimated_read_start(192.0, "薛 : 我记", units, (4, 5, 6), peer_source_start=188.34), 192.0)
        self.assertEqual(duet_estimated_read_start(188.34, "薛 : 我记", (), (4, 5, 6), peer_source_start=188.34), 188.34)

    def test_translation_button_spacing_matches_modes_and_shared_toggle(self):
        owner = _LayerOwner()
        owner.set_enable_dual_line_lyrics = lambda enabled: setattr(owner, "enable_dual_line_lyrics", enabled)
        surface = MusicSurfaceWindow(owner)
        surface.resize(1190, 666)
        self.addCleanup(owner.close)
        self.addCleanup(surface.shutdown)
        for strategy in ("gold", "low_perception"):
            owner.music_surface_reading_strategy = strategy
            for fullscreen in (True, False):
                surface._fullscreen = fullscreen
                surface._sync_window_control_rects()
                mode = surface._button_rects["mode" if fullscreen else "return"]
                translation = surface._button_rects["translation"]
                self.assertEqual(translation.size(), mode.size())
                self.assertEqual(translation.top(), mode.top())
                anchor = surface._obo_badge_rect(mode, 1.0) if strategy == "gold" else mode
                self.assertAlmostEqual(anchor.left() - translation.right(), 12.0)
        self.assertTrue(owner.enable_dual_line_lyrics)
        surface._toggle_translation()
        self.assertFalse(owner.enable_dual_line_lyrics)
        surface._toggle_translation()
        self.assertTrue(owner.enable_dual_line_lyrics)

    def test_enabled_translation_text_is_a_transparent_hole_in_pale_button(self):
        owner = _LayerOwner()
        surface = MusicSurfaceWindow(owner)
        surface.resize(1190, 666)
        self.addCleanup(owner.close)
        self.addCleanup(surface.shutdown)
        image = QImage(1190, 666, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        surface._paint_window_controls(painter, QRectF(0, 0, 1190, 666))
        painter.end()
        button = surface._button_rects["translation"]
        inner = button.adjusted(13, 9, -13, -9).toRect()
        pixels = [image.pixelColor(x, y) for x in range(inner.left(), inner.right())
                  for y in range(inner.top(), inner.bottom())]
        self.assertGreater(sum(pixel.alpha() == 0 for pixel in pixels), 10)
        self.assertGreater(sum(pixel.alpha() > 180 and pixel.red() > 245 for pixel in pixels), 10)


class ArtworkRecoveryTests(unittest.TestCase):
    def test_invalid_retained_cover_is_not_selected_over_thumbnail_fallback(self):
        self.assertEqual(WindowsMediaProvider._preferred_album_art_bytes(b"broken retained cover", b""), b"")

    def test_hung_thumbnail_attempt_times_out_and_allows_same_track_retry(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.THUMBNAIL_READ_TIMEOUT_SECONDS = 0.08
        provider._last_refresh_at = 0.0
        async def scenario():
            blocker = asyncio.Event()
            async def stuck(_properties, _key):
                await blocker.wait()
                return b""
            provider._read_thumbnail = stuck
            self.assertEqual(await provider._thumbnail_for_track(None, "song"), b"")
            task = provider._pending_thumbnail_tasks["song"]
            with self.assertRaises(asyncio.TimeoutError):
                await task
            await asyncio.sleep(0)
            self.assertNotIn("song", provider._pending_thumbnail_tasks)
            provider._read_thumbnail = lambda *_args: asyncio.sleep(0, result=b"recovered")
            self.assertEqual(await provider._thumbnail_for_track(None, "song"), b"recovered")
        asyncio.run(scenario())

    def test_invalid_original_bytes_and_memory_cache_do_not_block_download_retry(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        url = "https://example.test/cover.jpg"
        provider._queue_art_cache = {url: b"corrupt"}
        provider._queue_art_disk_path = lambda _url: None
        scheduled = []
        provider._schedule_queue_album_art_prefetch = lambda value: scheduled.append(value) or True
        self.assertEqual(provider._queue_album_art_bytes(SimpleNamespace(
            album_art_bytes=b"broken jpeg", image_bytes=b"", album_art_url=url)), b"")
        self.assertNotIn(url, provider._queue_art_cache)
        self.assertEqual(scheduled, [url])


class CantoneseSecondaryTests(unittest.TestCase):
    def _store(self, priority="phonetic_first"):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        settings = {"v2_enable_dual_line_lyrics": True, "v2_cantonese_secondary_priority": priority}
        return LocalLyricsStore(Path(root.name), settings_getter=lambda key, default=None: settings.get(key, default))

    def test_real_converter_preserves_parenthetical_structure(self):
        self.assertEqual(cantonese_phonetics("你(我)"), "nei5 (ngo5)")

    def test_explicit_language_tag_generates_phonetics_without_retiming(self):
        store = self._store()
        match = store._lyrics_match_from_text("[language:yue]\n[00:10.00]<00:10.00>你<00:11.00>好<00:12.00>", "netease")
        self.assertIn("粤语音译：nei5 hou2", match.timed_lyrics[0].text)
        self.assertEqual(match.timed_lyrics[0].time_seconds, 10.0)
        self.assertEqual([(unit.start_seconds, unit.end_seconds) for unit in match.timed_lyrics[0].units], [(10.0, 11.0), (11.0, 12.0)])
        displayed = present_lyric_text(match.timed_lyrics[0].text, show_translation=True)
        self.assertEqual(displayed, "你好\nnei5 hou2")
        self.assertEqual(present_lyric_text(match.timed_lyrics[0].text, show_translation=False), "你好")

    def test_priority_selects_existing_secondary_and_falls_back(self):
        lyric = "[language:yue]\n[00:01.00]你好\n[00:01.00]翻译：问候你\n[00:01.00]粤拼：nei5 hou2"
        # Sources use a combined logical sentence before source persistence.
        original = "[language:yue]\n[00:01.00]你好"
        companions = ("[00:01.00]翻译：问候你", "[00:01.00]粤拼：nei5 hou2")
        for priority, expected in (("phonetic_first", "粤语音译：nei5 hou2"), ("translation_first", "粤语译文：问候你")):
            store = self._store(priority)
            match = store._lyrics_match_from_text((original, *companions), "netease")
            self.assertEqual(match.timed_lyrics[0].text, "你好\n" + expected)
        store = self._store("translation_first")
        match = store._lyrics_match_from_text("[language:yue]\n[00:01.00]你好", "netease")
        self.assertIn("粤语音译：nei5 hou2", match.timed_lyrics[0].text)

    def test_provider_jyutping_is_preferred_and_uses_original_word_clock(self):
        store = self._store()
        payload = {"language": "yue", "yrc": {"lyric": "[1000,2000](1000,1000,0)你(2000,1000,0)好"},
                   "lrc": {"lyric": "[00:01.50]你好"},
                   "romalrc": {"lyric": "[00:01.50]nei5 hou2"},
                   "tlyric": {"lyric": "[00:01.50]问候你"}}
        match = store._lyrics_match_from_text(store._extract_netease_lyric_text(payload, prefer_secondary=True), "netease")
        row = match.timed_lyrics[0]
        self.assertEqual(row.time_seconds, 1.0)
        self.assertEqual(row.text, "你好\n粤语音译：nei5 hou2")
        self.assertEqual(row.timing_kind, "unit")
        self.assertEqual([unit.start_seconds for unit in row.units], [1.0, 2.0])

    def test_ordinary_mandarin_is_not_assumed_cantonese(self):
        self.assertFalse(has_cantonese_evidence("我等你的雨季 如此安静"))
        store = self._store()
        match = store._lyrics_match_from_text("[00:01.00]我等你的雨季 如此安静", "netease")
        self.assertEqual(match.timed_lyrics[0].text, "我等你的雨季 如此安静")

    def test_native_han_translation_and_aside_phonetics_share_popout_layer(self):
        store = self._store("translation_first")
        match = store._lyrics_match_from_text(("[language:yue]\n[00:01.00]你(我)", "[00:01.00]翻译：您(本人)"), "netease")
        layer = split_display_layers(match.timed_lyrics[0].text)
        self.assertEqual(layer.translation_text, "您")
        self.assertEqual(layer.parenthetical_translation_text, "本人")
        store = self._store()
        match = store._lyrics_match_from_text("[language:yue]\n[00:01.00]你(我)", "netease")
        layer = split_display_layers(match.timed_lyrics[0].text)
        self.assertEqual(layer.translation_text, "nei5")
        self.assertEqual(layer.parenthetical_translation_text, "ngo5")

    def test_priority_participates_in_cache_key(self):
        first = self._store("phonetic_first")
        second = self._store("translation_first")
        self.assertNotEqual(first._cache_key("Song", "Artist", "", 0), second._cache_key("Song", "Artist", "", 0))

    def test_persisted_candidates_can_change_priority_without_network_or_unit_loss(self):
        store = self._store()
        payload = {"language": "yue", "yrc": {"lyric": "[1000,2000](1000,1000,0)你(2000,1000,0)好"},
                   "ytlrc": {"lyric": "[00:01.00]问候你"},
                   "yromalrc": {"lyric": "[00:01.00]nei5 hou2"}}
        match = store._lyrics_match_from_text(store._extract_netease_lyric_text(payload, prefer_secondary=True), "netease")
        rendered = store._render_lrc("Song", "Artist", "", match)
        self.assertIn("粤语译文：问候你", rendered)
        self.assertIn("粤语音译：nei5 hou2", rendered)
        path = store.root / "cache.lrc"
        path.write_text(rendered, encoding="utf-8")
        settings = {"v2_enable_dual_line_lyrics": True, "v2_cantonese_secondary_priority": "translation_first"}
        store.settings_getter = lambda key, default=None: settings.get(key, default)
        translation = store._read_lrc(path)[0]
        self.assertEqual(translation.text, "你好\n粤语译文：问候你")
        self.assertEqual([unit.start_seconds for unit in translation.units], [1.0, 2.0])
        settings["v2_enable_dual_line_lyrics"] = False
        off = store._read_lrc(path)[0]
        self.assertEqual(present_lyric_text(off.text, show_translation=False), "你好")

    def test_priority_setting_tile_saves_shared_preference_and_refreshes_provider(self):
        from local.settings_window import SettingsWindow
        from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager
        app = QApplication.instance() or QApplication([])
        island, settings = FakeIslandWidget(), FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            combo = window.cantonese_secondary_priority
            self.assertEqual(combo.currentData(), "phonetic_first")
            combo.setCurrentIndex(combo.findData("translation_first"))
            self.assertEqual(settings.get("v2_cantonese_secondary_priority"), "translation_first")
            self.assertTrue(island.media_refresh_calls)
            settings.set("v2_cantonese_secondary_priority", "phonetic_first")
            window.sync_from_widget()
            self.assertEqual(combo.currentData(), "phonetic_first")
        finally:
            window.close()
            island.close()


if __name__ == "__main__":
    unittest.main()
