"""The release music page keeps its workers across library/preview lifetimes."""
from __future__ import annotations

from dataclasses import replace
import time
import unittest

from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QRect
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, IslandVisualData, TimedLyricLine, TrackData
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_library_window import MusicLibraryWindow
from local.music_surface_palette import artwork_surface_palette
from local.settings_window import MusicAmbiencePreview


class MusicSurfaceLifecycleT229Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        cover = QImage(320, 320, QImage.Format.Format_RGB32)
        with QPainter(cover) as painter:
            gradient = QLinearGradient(0, 0, 320, 320)
            gradient.setColorAt(0, QColor('#00d9cf'))
            gradient.setColorAt(.5, QColor('#2146bb'))
            gradient.setColorAt(1, QColor('#ed397c'))
            painter.fillRect(cover.rect(), gradient)
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        cover.save(buffer, 'PNG')
        rows = tuple(TimedLyricLine(index * 4., text) for index, text in enumerate((
            'Keep me warm through the night', 'Every album has its own colors',
            'Let the music move around us', 'And the song must carry on',
        )))
        self.track = TrackData(
            'Keep Me Warm', 'Chace', tuple(row.text for row in rows),
            ColorToken(56, 56, 56), ColorToken(72, 72, 72), (),
            album_art_bytes=bytes(encoded), source_app='NetEase',
            timed_lyrics=rows, lyrics_source='fixture', track_id='t229-capture',
            album_art_source='original', album_art_lookup_state='complete',
            duration_seconds=180., position_seconds=5., position_source='local-seek',
        )
        self.owner = IslandStackVisual(EmptyIslandDataProvider())
        self.owner.timer.stop()
        self.owner.music_intro_cleanup_enabled = False
        self.owner._music_page_geometry = QRect(0, 0, 960, 540)
        pending = replace(self.track, lyrics=('查找歌词中',), timed_lyrics=(),
                          lyrics_source='pending', album_art_bytes=b'',
                          album_art_lookup_state='pending')
        self.owner._apply_visual_data(IslandVisualData(pending, (), ()))
        # Exercise the actual release entry, including hiding the source island.
        self.owner._show_music_fullscreen(fullscreen=False)
        self.surface = self.owner.music_fullscreen_overlay
        self.surface.setGeometry(0, 0, 960, 540)
        self.store = self.owner._lyric_presentation_store
        self.palette_bridge = self.owner._solid_album_palette_bridge
        self.children = []
        self.addCleanup(self.dispose)

    def dispose(self):
        for child in self.children:
            child.close()
            child.deleteLater()
        self.owner._application_quitting = True
        self.surface.shutdown()
        self.owner.close()
        self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def wait_until(self, condition, timeout=4.):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.surface.grab()
            if condition():
                return
            QTest.qWait(20)
        self.assertTrue(condition(), 'release music surface did not settle')

    def assert_main_workers_unchanged(self):
        self.assertIs(self.owner._lyric_presentation_store, self.store)
        self.assertFalse(self.store.closed)
        self.assertIs(self.owner._solid_album_palette_bridge, self.palette_bridge)
        self.assertFalse(self.owner._solid_album_palette_closed)
        self.assertTrue(self.surface.isVisible())
        self.assertFalse(self.surface._lyric_resize_capturing)
        self.assertFalse(self.surface._lyric_resize_frozen)

    def new_library(self):
        library = MusicLibraryWindow()
        self.children.append(library)
        library.set_background_context({'v2_music_ambience_style': 'sea_level'}.get,
                                       self.owner._run_background)
        library.now_playing_island.set_context(
            {'v2_music_ambience_style': 'sea_level'}.get, self.owner)
        library.set_now_playing(self.track)
        self.assertIsNot(library.now_playing_island._lyric_presentation_store, self.store)
        self.assertIsNot(library.now_playing_island._solid_album_palette_bridge,
                         self.palette_bridge)
        self.assertIs(library.now_playing_island.data_provider, self.owner.data_provider)
        return library

    def finish_lookup_and_assert_result(self):
        self.owner._apply_visual_data(IslandVisualData(self.track, (), ()))
        expected_key = (self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
                        self.owner.secondary_color.rgba())
        self.wait_until(lambda: bool(self.surface._display_lyrics()[1])
                        and self.surface._art_palette_cache_key == expected_key)
        self.wait_until(lambda: not self.surface._lyric_loading_visible(time.monotonic()))
        self.assertEqual(self.surface._display_lyrics()[0], list(self.track.lyrics))
        self.assertFalse(self.surface._lyrics_lookup_pending())
        self.assertFalse(self.surface._lyric_loading_visible(time.monotonic()))
        expected = artwork_surface_palette(self.owner.album_art, self.owner.album_color,
                                           self.owner.secondary_color)
        actual = self.surface._art_palette()
        self.assertEqual(actual.primary.rgba(), expected.primary.rgba())
        self.assertEqual(actual.secondary.rgba(), expected.secondary.rgba())
        self.assertNotEqual(actual.primary.rgba(), self.owner.album_color.rgba())
        self.assert_main_workers_unchanged()

    def test_delayed_capture_result_survives_hidden_library_close_reopen_and_delete(self):
        self.wait_until(lambda: self.surface._lyric_loading_visible(time.monotonic()))
        library = self.new_library()
        self.assert_main_workers_unchanged()
        for action in ('hide', 'close'):
            library.show()
            self.app.processEvents()
            getattr(library, action)()
            self.app.processEvents()
            self.assertTrue(library.now_playing_island._lyric_presentation_store.closed)
            self.assert_main_workers_unchanged()
        library.show()
        self.app.processEvents()
        library.deleteLater()
        self.children.remove(library)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assert_main_workers_unchanged()
        self.finish_lookup_and_assert_result()

    def test_preview_destruction_and_unused_library_preserve_live_main_result(self):
        library = self.new_library()
        self.assertTrue(library.now_playing_island._lyric_presentation_store.closed)
        preview = MusicAmbiencePreview(island=self.owner)
        self.children.append(preview)
        preview.resize(320, 58)
        preview.set_preview_state('sea_level', album_color=self.owner.album_color,
                                  secondary_color=self.owner.secondary_color)
        preview.show()
        preview.grab()
        preview.hide()
        preview.close()
        preview.deleteLater()
        self.children.remove(preview)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assert_main_workers_unchanged()
        self.finish_lookup_and_assert_result()
        library.close()
        self.assert_main_workers_unchanged()
