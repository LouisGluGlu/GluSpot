"""Startup timings are INFO lines and do not run ahead of the measured work."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from local.startup_timing import _log, startup_phase


ROOT = Path(__file__).resolve().parents[1]


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class StartupTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_phase_line_is_info_and_follows_the_measured_work(self):
        events = []

        levels = []

        class _Order(logging.Handler):
            def emit(self, record):
                events.append(record.getMessage())
                levels.append(record.levelno)

        handler = _Order()
        _log.addHandler(handler)
        self.addCleanup(_log.removeHandler, handler)
        with startup_phase("lyric_fonts"):
            events.append("work")
        self.assertEqual(events[0], "work")
        self.assertRegex(events[1], r"^phase=lyric_fonts ms=\d+$")
        self.assertEqual(levels, [logging.INFO])

    def test_failed_phase_still_reports_and_reraises(self):
        handler = _Collect()
        _log.addHandler(handler)
        self.addCleanup(_log.removeHandler, handler)
        with self.assertRaises(RuntimeError):
            with startup_phase("windows_media_provider"):
                raise RuntimeError("stop")
        self.assertRegex(handler.messages[-1], r"^phase=windows_media_provider ms=\d+$")

    def test_import_ui_line_is_emitted_when_the_app_module_loads(self):
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        completed = subprocess.run(
            [sys.executable, "-c", "from local.main import _load_app; _load_app()"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertRegex(completed.stderr, r"GluSpot\.startup phase=import_ui ms=\d+")

    def test_bootstrap_logs_startup_phases_in_existing_order(self):
        from local.app import bootstrap
        handler = _Collect()
        _log.addHandler(handler)
        self.addCleanup(_log.removeHandler, handler)
        with tempfile.TemporaryDirectory() as root, \
             patch("local.app.WindowsMediaProvider", _FixtureSystemProvider), \
             patch("local.app.make_online_music_service", return_value=Mock()), \
             patch("local.app.LocalIslandApp._apply_startup_setting"), \
             patch("local.app.LocalIslandApp._setup_mydockfinder_listener"):
            runtime = bootstrap(view="narrow", capture="attach_only",
                                settings_path=Path(root) / "settings.json")
            try:
                phases = [message.split()[0].split("=", 1)[1] for message in handler.messages]
                expected = [
                    "qapplication", "settings", "lyric_fonts", "windows_media_provider",
                    "online_music", "owned_player", "windows_smtc", "audio_outputs",
                    "island_window", "saved_settings", "settings_window", "playback_entry",
                    "music_library", "song_collection", "mydockfinder", "narrow_view",
                ]
                self.assertEqual([phase for phase in phases if phase in expected], expected)
                for message in handler.messages:
                    self.assertRegex(message, r"^phase=[a-z_]+ ms=\d+$")
            finally:
                runtime.window._application_quitting = True
                runtime.system_media_controls.close()
                runtime.data_provider.close_audio_outputs()
                runtime.view_window.shutdown()
                runtime.view_window.close()
                runtime.window.close()
                runtime.settings.save_now()
                runtime.watchdog.timer.stop()
                runtime.gc_scheduler.timer.stop()
                runtime.song_collection.close()
                runtime.playback_entry.close()
                runtime.music_library.close()
                runtime.online_music.close()


class _FixtureSystemProvider:
    def __init__(self, *, settings_getter, attach_only=False):
        from api.providers import EmptyIslandDataProvider
        self._fallback = EmptyIslandDataProvider()
        self.settings_getter = settings_getter
        self.attach_only = attach_only
        self.lyrics_store = Mock()
        self.settings_setter = None

    def start_audio_outputs(self):
        return None

    def close_audio_outputs(self):
        pass

    def suspend_live_audio(self):
        pass

    def get_visual_data(self):
        return self._fallback.get_visual_data()

    def __getattr__(self, name):
        return getattr(self._fallback, name)


if __name__ == "__main__":
    unittest.main()
