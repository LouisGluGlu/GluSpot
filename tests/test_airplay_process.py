from __future__ import annotations

import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from api.airplay_process import AirPlayProcess
from api.audio_routes import AudioRouteDevice


HOME = AudioRouteDevice("home", "起居室", transport="airplay", host="192.0.2.1", device_id="AABB")


class AirPlayProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.script = Path(self.temp.name) / "sender.py"
        self.script.write_text(
            "import sys,json\n"
            "print(json.dumps({'event':'waiting_audio','message':'ready'}),flush=True)\n"
            "for line in sys.stdin:\n"
            " if line.strip()=='stop': break\n"
            "print(json.dumps({'event':'stopped'}),flush=True)\n", encoding="utf-8")
        self.args = None
        def spawn(args, **kwargs):
            self.args = args
            return subprocess.Popen([sys.executable, "-u", str(self.script)], **kwargs)
        self.sender = AirPlayProcess(self.script, spawn, source_id="02:01:02:03:04:05")

    def tearDown(self):
        self.sender.stop()
        self.temp.cleanup()

    def _wait_events(self):
        results = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            results.extend(self.sender.poll_events())
            if results:
                return results
            time.sleep(0.01)
        self.fail("child did not produce a status event")

    def test_real_child_stop_reaps_process_and_waiting_does_not_timeout(self):
        self.sender.start(HOME, "endpoint")
        process = self.sender._process
        self.assertEqual(self._wait_events()[0]["event"], "waiting_audio")
        with patch("api.airplay_process.time.monotonic", return_value=self.sender._started_at + 60):
            self.assertFalse(self.sender.poll_events())
        self.sender.stop()
        self.assertEqual(process.poll(), 0)
        self.assertIsNone(self.sender._reader)

    def test_unicode_and_untrusted_names_are_passed_as_arguments(self):
        self.sender.start(HOME, 'endpoint $() " \n')
        self.assertEqual(self.args[self.args.index("--name") + 1], "起居室")
        self.assertEqual(self.args[self.args.index("--endpoint") + 1], 'endpoint $() " \n')
        self.assertEqual(self.args[self.args.index("--source-id") + 1], "02:01:02:03:04:05")

    def test_restart_fences_late_old_child_status(self):
        self.sender.start(HOME, "a")
        self._wait_events()
        old_events = self.sender._events
        self.sender.start(HOME, "b")
        old_events.put({"event": "error", "message": "old"})
        self.assertNotIn("old", [e.get("message") for e in self._wait_events()])

    def test_child_stall_is_bounded_and_killed(self):
        self.script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        self.sender.start(HOME, "endpoint")
        process = self.sender._process
        with patch("api.airplay_process.time.monotonic", return_value=self.sender._started_at + 60):
            events = self.sender.poll_events()
        self.assertEqual(events[0]["event"], "error")
        self.assertIsNotNone(process.poll())

    def test_silent_child_exit_reports_failure(self):
        self.script.write_text("raise SystemExit(3)\n", encoding="utf-8")
        self.sender.start(HOME, "endpoint")
        events = self._wait_events()
        self.assertEqual(events[0]["event"], "error")
        self.assertIn("3", events[0]["message"])

    def test_missing_helper_reports_actionable_failure(self):
        sender = AirPlayProcess(Path(self.temp.name) / "absent.exe")
        with self.assertRaisesRegex(RuntimeError, "投放组件未安装"):
            sender.start(HOME, "endpoint")

    def test_malformed_unknown_oversize_lines_do_not_enter_event_queue(self):
        lines = ["oops\n", "[]\n", '{"event":"unexpected"}\n', "x" * 9000 + "\n",
                 json.dumps({"event": "error", "message": "真正失败"}) + "\n"]
        from types import SimpleNamespace
        events = queue.Queue(maxsize=32)
        AirPlayProcess._read_events(SimpleNamespace(stdout=io.StringIO("".join(lines))), events)
        self.assertEqual(events.qsize(), 1)
        self.assertEqual(events.get()["message"], "真正失败")

    def test_flood_is_bounded_and_preserves_latest_terminal_event(self):
        lines = [json.dumps({"event": "stats", "frames": i}) + "\n" for i in range(300)]
        lines.append(json.dumps({"event": "error", "message": "last"}) + "\n")
        from types import SimpleNamespace
        events = queue.Queue(maxsize=32)
        AirPlayProcess._read_events(SimpleNamespace(stdout=io.StringIO("".join(lines))), events)
        self.assertEqual(events.qsize(), 32)
        values = list(events.queue)
        self.assertEqual(values[-1]["event"], "error")


if __name__ == "__main__":
    unittest.main()
