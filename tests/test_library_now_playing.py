"""The library embeds the real island renderer and keeps its live lifecycle."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest

from api.models import AudioFrame, ColorToken, TrackData
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.library_now_playing import LibraryNowPlayingIsland


class LibraryNowPlayingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.host.resize(420, 132)
        self.island = LibraryNowPlayingIsland(self.host)
        self.island.setFixedSize(420, 132)
        self.island.show()
        self.host.show()
        self.app.processEvents()
        self.addCleanup(self.dispose)
        self.track = TrackData('Track', 'Artist', ('First line', 'Second line'),
                               ColorToken(40, 90, 180), ColorToken(80, 40, 120), (),
                               track_id='first', duration_seconds=120)

    def dispose(self):
        self.island.close()
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def test_settings_modes_use_actual_expanded_renderer_without_changing_owner(self):
        settings = {}
        for mode in ('sea_level', 'solid_album', 'system'):
            settings['v2_music_ambience_style'] = mode
            self.island.set_context(settings.get)
            self.island.set_track(self.track)
            with patch.object(IslandStackVisual, '_paint_music', autospec=True,
                              side_effect=IslandStackVisual._paint_music) as paint:
                self.island.grab()
            self.assertEqual(self.island.music_ambience_style, mode)
            self.assertTrue(paint.call_args.kwargs['expanded'])
            self.assertEqual(len(self.island.music_button_rects), 3)
            self.assertTrue(all(self.island.rect().contains(r.toAlignedRect())
                                for r in self.island.music_button_rects.values()))

    def test_track_flip_is_independent_of_hidden_floating_island_and_survives_playback_polls(self):
        def artwork(color):
            image = QImage(64, 64, QImage.Format.Format_RGB32)
            image.fill(QColor(color))
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, 'PNG')
            return bytes(data)
        first = replace(self.track, album_art_bytes=artwork('red'))
        self.island.set_track(first)
        second = replace(first, title='New track', track_id='second', album_art_bytes=artwork('blue'))
        self.island.set_track(second)
        started = self.island._island_track_transition_started_at
        self.assertGreater(started, 0)
        self.assertFalse(self.island._island_track_previous_cover.isNull())
        self.island.set_track(replace(second, position_seconds=2))
        self.assertEqual(self.island._island_track_transition_started_at, started)
        self.island.grab()
        self.assertFalse(self.island.music_cover_rect.isEmpty())

    def test_existing_provider_drives_sea_and_hidden_child_stops_clock(self):
        provider = EmptyIslandDataProvider()
        provider.get_audio_frame = lambda: AudioFrame(.3, .4, .2, vocal=.8, peak=.9)
        source = type('Source', (), {'data_provider': provider})()
        self.island.set_context({'v2_music_ambience_style': 'sea_level'}.get, source)
        self.island.set_track(self.track)
        self.island._advance_embedded()
        self.assertEqual(self.island._sea_vocal_sample, .8)
        self.assertIs(self.island.data_provider, provider)
        self.assertTrue(self.island.timer.isActive())
        self.host.hide()
        self.assertFalse(self.island.timer.isActive())
        self.host.show()
        self.app.processEvents()
        self.assertTrue(self.island.timer.isActive())

    def test_lyricless_track_still_has_all_transport_controls(self):
        self.island.set_track(replace(self.track, lyrics=(), lyrics_source='instrumental'))
        self.island.grab()
        self.assertEqual(set(self.island.music_button_rects),
                         {'music:previous', 'music:toggle_play_pause', 'music:next'})
        self.assertFalse(self.island.music_cover_rect.isEmpty())

    def test_double_click_keeps_fixed_expanded_pose_and_never_enters_native_island_lifecycle(self):
        self.island.set_track(self.track)
        before = self.island.geometry()
        with patch.object(self.island, '_set_expanded') as collapse:
            QTest.mouseDClick(self.island, Qt.MouseButton.LeftButton)
        collapse.assert_not_called()
        self.assertTrue(self.island.expanded)
        self.assertEqual(self.island.geometry(), before)

    def test_library_hide_close_reopen_and_parent_deletion_release_owned_workers(self):
        from local.music_library_window import MusicLibraryWindow

        workers = set()
        for index in range(8):
            window = MusicLibraryWindow()
            island = window.now_playing_island
            self.assertTrue(island._lyric_presentation_store.closed)
            window.set_now_playing(replace(self.track, track_id=str(index)))
            for action in ('hide', 'close', 'delete'):
                window.show()
                self.app.processEvents()
                preparation = island._lyric_presentation_store
                self.assertFalse(preparation.closed)
                tasks = preparation.tasks
                workers.update(thread for thread in threading.enumerate()
                    if getattr(getattr(thread, '_target', None), '__self__', None) is tasks)
                self.assertEqual(island.track_title, self.track.title)
                island.grab()
                self.assertEqual(len(island.music_button_rects), 3)
                if action == 'delete':
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                else:
                    getattr(window, action)()
                    self.assertTrue(island._solid_album_palette_closed)
                    self.assertFalse(island.timer.isActive())
                self.assertTrue(preparation.closed)
                self.app.processEvents()
        self.assertGreater(len(workers), 0)
        for worker in workers:
            worker.join(timeout=1.)
            self.assertFalse(worker.is_alive(), worker.name)
