from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow
from local.personal_dj_icon import DJ_RING_CYCLE_SECONDS, personal_dj_ring_poses
from tests.test_owned_playback_surface import _Provider


class PersonalDjSurfaceOutroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.media_track_id = "netease:actual-song"
        self.owner.album_art = QImage()
        self.owner.album_color = QColor("#9a426b")
        self.owner.secondary_color = QColor("#38233a")
        self.provider = _Provider()
        self.provider.fm = True
        self.enabled, self.speaking = True, False
        self.presentation = {}
        self.provider.get_owned_personal_dj_enabled = lambda: self.enabled if self.provider.fm else None
        self.provider.is_owned_personal_dj_speaking = lambda: self.speaking
        self.provider.get_personal_dj_presentation = lambda: self.presentation
        self.owner.data_provider = self.provider
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def sync(self, now):
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window._sync_owned_playback_mode()

    def start_speech(self):
        self.window._reset_control_idle(90)
        self.window._dj_activity_from = self.window._dj_activity_to = 0.0
        self.window._dj_activity_started_at = 99
        self.speaking = True
        self.presentation = {"state": "speaking", "next_title": "Real song being introduced",
            "token": 4, "completion_id": 0, "is_preview": False}
        self.sync(100)
        self.assertEqual(self.window._personal_dj_activity_at(100.4), 1)

    def complete_speech(self, *, preload="ready", now=101):
        self.speaking = False
        self.presentation = {"state": "idle", "next_title": "", "token": 5,
            "completion_id": 7, "completed_track_id": self.owner.media_track_id,
            "completed_at": now, "next_preload_state": preload, "is_preview": False}
        self.sync(now)

    def test_successful_speech_contracts_to_icon_then_keeps_two_complete_idle_cycles(self):
        self.start_speech()
        self.complete_speech()
        region = QRectF(self.window.rect())
        for now in (101.32, 103.82, 106.32):
            with self.subTest(now=now), patch("local.music_surface_window.time.monotonic", return_value=now):
                self.window._sync_window_control_rects()
                icon, caption = self.window._personal_dj_geometry(region, now)
                self.assertEqual(icon, caption)
                self.assertEqual(self.window._personal_dj_reveal_at(now), 1)
                self.assertEqual(self.window._control_reveal_at(now), 0)
                self.assertEqual(set(self.window._button_rects), {"personal_dj"})
        self.assertEqual(self.window.DJ_OUTRO_IDLE_SECONDS, 2 * DJ_RING_CYCLE_SECONDS)
        self.assertEqual(self.window._dj_ring_started_at, 101.32)
        self.assertEqual(personal_dj_ring_poses(0), personal_dj_ring_poses(self.window.DJ_OUTRO_IDLE_SECONDS))

    def test_outro_hides_with_existing_spring_after_both_cycles(self):
        self.start_speech()
        self.complete_speech()
        self.assertEqual(self.window._personal_dj_outro_reveal_at(106.32), 1)
        self.assertGreater(self.window._personal_dj_outro_reveal_at(106.5), 0)
        self.assertLess(self.window._personal_dj_outro_reveal_at(106.5), 1)
        with patch("local.music_surface_window.time.monotonic", return_value=106.85):
            self.window._sync_window_control_rects()
            self.assertEqual(self.window._personal_dj_reveal_at(106.85), 0)
            self.assertFalse(self.window._button_rects)

    def test_status_reports_actual_preload_and_never_restarts_outro_or_contraction(self):
        self.start_speech()
        self.complete_speech(preload="loading")
        self.assertEqual(self.window._personal_dj_outro_status_text(), "下一首歌播报加载中")
        for now, state, text in ((101.15, "ready", "下一首歌播报已准备好"),
                                 (101.25, "failed", "下一首歌播报加载失败"),
                                 (101.4, "idle", "等待下一首歌播报")):
            self.presentation.update(next_preload_state=state, token=now)
            self.sync(now)
            self.assertEqual(self.window._personal_dj_outro_status_text(), text)
            self.assertEqual(self.window._dj_outro_started_at, 101)
            self.assertEqual(self.window._dj_activity_started_at, 101)
            self.assertEqual(self.window._dj_ring_started_at, 101.32)
        self.assertEqual(self.window._personal_dj_activity_at(101.4), 0)

    def test_legacy_current_ready_is_not_claimed_as_next_preload_ready(self):
        self.start_speech()
        self.complete_speech(preload="loading")
        self.presentation["state"] = "ready"
        self.sync(102)
        self.assertEqual(self.window._personal_dj_outro_status_text(), "下一首歌播报加载中")

    def test_pause_multipart_failure_and_preview_do_not_create_an_outro(self):
        for state, preview in (("ready", False), ("loading", False), ("failed", False), ("idle", True)):
            with self.subTest(state=state, preview=preview):
                self.start_speech()
                self.speaking = False
                self.presentation.update(state=state, is_preview=preview)
                self.sync(101)
                self.assertIsNone(self.window._dj_outro_started_at)
                self.assertEqual(self.window._personal_dj_outro_reveal_at(101.4), 0)

    def test_disabled_source_navigation_or_new_speech_cancel_the_outro(self):
        for boundary in ("disabled", "system", "new_song", "speech", "provider_cleared"):
            with self.subTest(boundary=boundary):
                self.enabled, self.provider.fm, self.speaking = True, True, False
                self.owner.media_track_id = "netease:actual-song"
                self.window._dj_completed_token = 0
                self.start_speech()
                self.complete_speech()
                if boundary == "disabled":
                    self.enabled = False
                elif boundary == "system":
                    self.provider.fm = False
                elif boundary == "new_song":
                    self.owner.media_track_id = "netease:another-song"
                elif boundary == "speech":
                    self.speaking = True
                else:
                    self.presentation["completion_id"] = 0
                self.sync(102)
                self.assertIsNone(self.window._dj_outro_started_at)

    def test_hidden_page_does_not_restart_an_expired_completion(self):
        self.start_speech()
        self.complete_speech()
        self.window._dj_outro_started_at = None
        self.window._dj_completed_token = 0
        self.sync(120)
        self.assertIsNone(self.window._dj_outro_started_at)
        self.assertEqual(self.window._personal_dj_reveal_at(120), 0)

    def test_delayed_observation_keeps_provider_clock_instead_of_replaying_five_seconds(self):
        self.start_speech()
        self.complete_speech()
        self.window._dj_outro_started_at = None
        self.window._dj_completed_token = 0
        self.sync(105.9)
        self.assertEqual(self.window._dj_outro_started_at, 101)
        self.assertEqual(self.window._personal_dj_outro_reveal_at(106.85), 0)

    def test_completion_for_another_track_is_not_displayed_but_can_settle_after_metadata(self):
        self.start_speech()
        self.owner.media_track_id = "netease:old-song"
        self.speaking = False
        self.presentation.update(state="idle", completion_id=8, completed_at=101,
            completed_track_id="netease:next-song", next_preload_state="ready")
        self.sync(101)
        self.assertIsNone(self.window._dj_outro_started_at)
        self.owner.media_track_id = "netease:next-song"
        self.sync(101.1)
        self.assertEqual(self.window._dj_outro_started_at, 101)

    def test_status_and_icon_stay_bounded_and_render_in_window_and_fullscreen(self):
        self.start_speech()
        self.complete_speech()
        for size, fullscreen in (((640, 360), False), ((960, 540), False), ((1920, 1080), True)):
            with self.subTest(size=size, fullscreen=fullscreen), patch("local.music_surface_window.time.monotonic", return_value=102):
                self.window._fullscreen = fullscreen
                self.window.resize(*size)
                region = QRectF(self.window.rect())
                icon, caption = self.window._personal_dj_geometry(region, 102)
                status = self.window._personal_dj_outro_status_rect(region, 102)
                self.assertTrue(region.contains(icon.united(status)))
                self.assertLess(status.right(), icon.left())
                self.assertTrue(self.window._personal_dj_damage_rect(region).contains(status.toAlignedRect()))
                image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(0)
                painter = QPainter(image)
                self.window._paint_personal_dj_control(painter, region)
                painter.end()
                self.assertTrue(any(image.pixelColor(x, y).alpha() > 0
                    for y in range(round(status.top()), round(status.bottom()))
                    for x in range(round(status.left()), round(status.right()))))

    def test_real_current_introduction_waiting_reveals_only_dj_without_outro(self):
        self.window._reset_control_idle(90)
        for state in ("loading", "ready"):
            with self.subTest(state=state), patch("local.music_surface_window.time.monotonic", return_value=100):
                self.presentation = {"state": state, "next_title": "Current introduced song",
                    "introduction_pending": True, "is_preview": False, "token": 9}
                self.window._sync_owned_playback_mode()
                self.window._sync_window_control_rects()
                self.assertEqual(self.window._personal_dj_reveal_at(100), 1)
                self.assertEqual(self.window._control_reveal_at(100), 0)
                self.assertEqual(set(self.window._button_rects), {"personal_dj"})
                self.assertIsNone(self.window._dj_outro_started_at)
                self.assertEqual(self.window._personal_dj_pending_status_text(),
                    "本首歌播报已准备好" if state == "ready" else "本首歌播报加载中")

    def test_future_preload_preview_and_unknown_states_never_reveal_waiting(self):
        self.window._reset_control_idle(90)
        for snapshot in (
            {"state": "loading", "next_preload_state": "loading", "next_title": "Future song"},
            {"state": "ready", "next_preload_state": "ready", "next_title": "Future song"},
            {"state": "failed", "next_preload_state": "failed", "next_title": "Future song"},
            {"state": "loading", "is_preview": True, "introduction_pending": True},
            {"state": "failed", "is_preview": True, "introduction_failed": True},
        ):
            with self.subTest(snapshot=snapshot):
                self.presentation = snapshot
                self.sync(100)
                self.assertEqual(self.window._personal_dj_reveal_at(100), 0)
                self.assertIsNone(self.window._dj_outro_started_at)

    def test_current_intro_failure_reveal_obeys_provider_deadline_and_source_cancel(self):
        self.window._reset_control_idle(90)
        self.presentation = {"state": "failed", "token": 3,
            "introduction_failed": True, "completion_id": 0}
        self.sync(100)
        self.assertEqual(self.window._personal_dj_reveal_at(100), 1)
        self.assertEqual(self.window._personal_dj_pending_status_text(), "本首歌播报加载失败")
        self.presentation.update(introduction_failed=False, state="idle")
        self.sync(102.1)
        self.assertEqual(self.window._personal_dj_reveal_at(102.1), 0)
        self.assertIsNone(self.window._dj_outro_started_at)
        self.presentation.update(introduction_pending=True, state="loading")
        self.sync(103)
        self.assertEqual(self.window._personal_dj_reveal_at(103), 1)
        self.provider.fm = False
        self.sync(103.1)
        self.assertEqual(self.window._personal_dj_reveal_at(103.1), 0)

    def test_pending_state_text_repaints_without_changing_audio_or_readiness(self):
        self.window._reset_control_idle(90)
        self.presentation = {"state": "loading", "introduction_pending": True,
            "next_preload_state": "idle", "completion_id": 0}
        self.sync(100)
        self.assertEqual(self.window._dj_next_preload_state, "idle")
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            region = QRectF(self.window.rect())
            status = self.window._personal_dj_outro_status_rect(region, 100)
            self.assertTrue(self.window._personal_dj_damage_rect(region).contains(status.toAlignedRect()))
            image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            with QPainter(image) as painter:
                self.window._paint_personal_dj_control(painter, region)
            self.assertTrue(any(image.pixelColor(x, y).alpha() > 0
                for y in range(round(status.top()), round(status.bottom()))
                for x in range(round(status.left()), round(status.right()))))
        self.assertFalse(self.speaking)
        self.assertEqual(self.presentation["state"], "loading")


if __name__ == "__main__":
    unittest.main()
