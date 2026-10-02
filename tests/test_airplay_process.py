from __future__ import annotations

import io
import json
from collections import deque
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

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
        self.assertIsNone(self.sender._stderr_reader)

    def test_transport_evidence_is_bounded_and_retained_after_child_exit(self):
        self.script.write_text(
            "import sys,json\n"
            "for i in range(70): print('transport '+str(i),file=sys.stderr,flush=True)\n"
            "print(json.dumps({'event':'stats','event_replies':3}),flush=True)\n"
            "print(json.dumps({'event':'error','message':'event channel closed'}),flush=True)\n",
            encoding="utf-8")
        self.sender.start(HOME, "endpoint")
        events = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            events.extend(self.sender.poll_events())
            if any(event["event"] == "error" for event in events):
                break
            time.sleep(0.01)
        self.sender.stop()
        evidence = self.sender.diagnostics()
        self.assertEqual(evidence["stats"]["event_replies"], 3)
        self.assertEqual(evidence["terminal"]["message"], "event channel closed")
        self.assertEqual(len(evidence["transport_messages"]), 24)
        self.assertEqual(evidence["transport_messages"][-1], "transport 69")
        self.assertGreater(evidence["session_seconds"], 0)

    def test_stderr_flood_drops_oversize_lines_and_old_session_cannot_replace_new_tail(self):
        from types import SimpleNamespace
        old_tail = deque(maxlen=24)
        self.sender._stderr_tail.append("current session")
        lines = ["x" * 9000 + "\n"] + ["entry " + str(i) + "\n" for i in range(100)]
        self.sender._read_stderr(SimpleNamespace(stderr=io.StringIO("".join(lines))), old_tail)
        self.assertEqual(len(old_tail), 24)
        self.assertEqual(old_tail[-1], "entry 99")
        self.assertEqual(self.sender.diagnostics()["transport_messages"], ("current session",))

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

    def test_failed_restart_does_not_reuse_previous_session_mute_ownership(self):
        self.sender.needs_local_output_restore = True
        self.sender._mute_state = {"endpoint_id": "endpoint", "original_muted": False, "phase": "muted"}
        self.sender._last_stats = {"event": "stats", "event_acks": 3}
        self.sender._popen = lambda *_a, **_kw: (_ for _ in ()).throw(OSError("CreateProcess failed"))
        with self.assertRaisesRegex(OSError, "CreateProcess failed"):
            self.sender.start(HOME, "endpoint")
        self.assertFalse(self.sender.needs_local_output_restore)
        self.assertEqual(self.sender.diagnostics()["local_output"], {})
        self.assertEqual(self.sender.diagnostics()["stats"], {})

    def test_mute_restore_evidence_is_recorded_even_without_ui_polling(self):
        self.script.write_text(
            "import sys,json\n"
            "for phase in ('prepared','muted','restored'):\n"
            " print(json.dumps({'event':'mute_state','phase':phase,'endpoint_id':'endpoint','original_muted':False}),flush=True)\n",
            encoding="utf-8")
        self.sender.start(HOME, "endpoint")
        self.sender._process.wait(timeout=3)
        self.sender.stop()
        self.assertEqual(self.sender.diagnostics()["local_output"]["phase"], "restored")
        self.assertFalse(self.sender.needs_local_output_restore)

    def test_stop_retains_final_stats_and_error_without_polling_the_final_queue(self):
        self.script.write_text(
            "import sys,json\n"
            "for line in sys.stdin:\n"
            " if line.strip()=='stop': break\n"
            "for event in ({'event':'stats','final_frames':999},{'event':'error','message':'transport lost'},{'event':'stopped'}):\n"
            " print(json.dumps(event),flush=True)\n",
            encoding="utf-8")
        self.sender.start(HOME, "endpoint")
        self.sender.stop()
        self.assertEqual(self.sender.diagnostics()["stats"]["final_frames"], 999)
        self.assertEqual(self.sender.diagnostics()["terminal"]["message"], "transport lost")

    def test_failed_pipe_cleanup_still_closes_other_pipes_and_requests_mute_recovery(self):
        process = Mock(returncode=1)
        process.poll.return_value = 1
        process.stdout.close.side_effect = OSError("stdout cleanup failed")
        self.sender._process = process
        reader, stderr_reader = Mock(), Mock()
        self.sender._reader, self.sender._stderr_reader = reader, stderr_reader
        self.sender._mute_state = {"phase": "muted", "original_muted": False, "applied": True}
        self.sender.stop()
        process.stdin.close.assert_called_once()
        process.stderr.close.assert_called_once()
        reader.join.assert_called_once()
        stderr_reader.join.assert_called_once()
        self.assertTrue(self.sender.needs_local_output_restore)

    def test_failed_kill_wait_keeps_cleanup_and_mute_recovery_enabled(self):
        process = Mock(returncode=None)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("sender", 1)
        self.sender._process = process
        self.sender._mute_state = {"phase": "muted", "original_muted": False, "applied": True}
        with self.assertRaises(subprocess.TimeoutExpired):
            self.sender.stop()
        process.kill.assert_called_once()
        process.stderr.close.assert_called_once()
        self.assertTrue(self.sender.needs_local_output_restore)

    def test_prepared_session_has_no_authority_to_restore_a_users_later_mute(self):
        process = Mock(returncode=1)
        process.poll.return_value = 1
        self.sender._process = process
        self.sender._mute_state = {"phase": "prepared", "original_muted": False, "applied": False}
        self.sender.stop()
        self.assertFalse(self.sender.needs_local_output_restore)

    def test_real_child_hard_exit_retains_confirmed_mute_ownership_for_parent_recovery(self):
        self.script.write_text(
            "import sys,json,time\n"
            "print(json.dumps({'event':'mute_state','phase':'muted','endpoint_id':'endpoint','original_muted':False,'applied':True}),flush=True)\n"
            "time.sleep(60)\n", encoding="utf-8")
        self.sender.start(HOME, "endpoint")
        self.assertEqual(self._wait_events()[0]["event"], "mute_state")
        process = self.sender._process
        process.kill()
        process.wait(timeout=3)
        self.sender.stop()
        self.assertTrue(self.sender.needs_local_output_restore)
        self.assertEqual(self.sender.diagnostics()["local_output"]["endpoint_id"], "endpoint")
        self.assertIsNone(self.sender._reader)
        self.assertIsNone(self.sender._stderr_reader)

    def test_malformed_unknown_oversize_lines_do_not_enter_event_queue(self):
        lines = ["oops\n", "[]\n", '{"event":"unexpected"}\n', "x" * 9000 + "\n",
                 json.dumps({"event": "error", "message": "真正失败"}) + "\n"]
        from types import SimpleNamespace
        events = queue.Queue(maxsize=32)
        AirPlayProcess._read_events(SimpleNamespace(stdout=io.StringIO("".join(lines))), events)
        self.assertEqual(events.qsize(), 1)
        self.assertEqual(events.get()["message"], "真正失败")

    def test_reader_owns_receipt_timestamp_and_queue_and_diagnostics_share_the_actual_receipt(self):
        from types import SimpleNamespace
        for index, forged in enumerate((10**12, -1, True, None, "future", {"time": 99}, float("nan"))):
            with self.subTest(forged=forged):
                event = {"event": "stats", "_received_at": forged, "capture_signal": {
                    "nonzero_samples": 20, "last_nonzero_age_ms": 0,
                    "peak": .1, "mode": "endpoint_loopback", "scope": "selected_endpoint",
                }}
                process = SimpleNamespace(stdout=io.StringIO(json.dumps(event) + "\n"))
                received = 123.5 + index
                # Diagnostic retention happens after parsing; it must preserve
                # the reader's receipt, rather than restamping the queued event.
                with patch("api.airplay_process.time.monotonic", side_effect=(received, received + .25)):
                    self.sender._read_events(process, self.sender._events,
                        lambda value: self.sender._remember_native_event(self.sender._mute_state, value))
                queued, = self.sender.poll_events()
                evidence = self.sender.diagnostics()["stats"]
                self.assertEqual(queued["_received_at"], received)
                self.assertEqual(evidence["_received_at"], received)
                self.assertEqual(evidence["capture_signal"], event["capture_signal"])
                self.assertEqual(self.sender._last_stats_at, received + .25)
                # UI queue consumers cannot rewrite the retained receipt.
                queued["_received_at"] = -100
                self.assertEqual(self.sender.diagnostics()["stats"]["_received_at"], received)

    def test_flood_is_bounded_and_preserves_latest_terminal_event(self):
        lines = [json.dumps({"event": "stats", "frames": i}) + "\n" for i in range(300)]
        lines.append(json.dumps({"event": "error", "message": "last"}) + "\n")
        from types import SimpleNamespace
        events = queue.Queue(maxsize=32)
        AirPlayProcess._read_events(SimpleNamespace(stdout=io.StringIO("".join(lines))), events)
        self.assertEqual(events.qsize(), 32)
        values = list(events.queue)
        self.assertEqual(values[-1]["event"], "error")

    def _publish_timing(self, **updates):
        timing = {"method": "protocol_plus_input_queue", "valid": True,
                  "estimated_delay_ms": 1520.0, "protocol_buffer_ms": 1500.0,
                  "input_queue_ms": 20.0}
        timing.update(updates)
        self.sender._remember_native_event(self.sender._mute_state,
            {"event": "stats", "timing": timing,
             "protocol": {"receiver_latency_reported": True, "receiver_latency_frames": 88200}})

    def test_fresh_sender_estimate_excludes_receiver_and_returns_an_isolated_snapshot(self):
        self.sender.start(HOME, "endpoint")
        self._publish_timing()
        estimate = self.sender.latency_estimate()
        self.assertAlmostEqual(estimate["seconds"], 1.52)
        self.assertEqual(estimate["source"], "protocol_plus_input_queue")
        self.assertAlmostEqual(estimate["protocol_buffer_seconds"], 1.5)
        self.assertAlmostEqual(estimate["input_queue_seconds"], 0.02)
        estimate["seconds"] = 9
        self.assertAlmostEqual(self.sender.latency_estimate()["seconds"], 1.52)
        with patch("api.airplay_process.time.monotonic", return_value=self.sender._last_stats_at + 2.01):
            self.assertIsNone(self.sender.latency_estimate())

    def test_invalid_and_malformed_estimates_never_adjust_lyrics(self):
        self.sender.start(HOME, "endpoint")
        for updates in ({"valid": False}, {"valid": 1}, {"method": "measured_acoustic"},
                        {"estimated_delay_ms": -1}, {"estimated_delay_ms": True},
                        {"estimated_delay_ms": float("nan")}, {"input_queue_ms": float("inf")},
                        {"estimated_delay_ms": "1520"}, {"estimated_delay_ms": 3000},
                        {"input_queue_ms": None}, {"estimated_delay_ms": 10 ** 400}):
            with self.subTest(updates=updates):
                self._publish_timing(**updates)
                self.assertIsNone(self.sender.latency_estimate())
        self._publish_timing()
        self.assertIsNotNone(self.sender.latency_estimate())
        # Old helpers provide useful diagnostics without claiming a delay.
        self.sender._remember_native_event(self.sender._mute_state, {"event": "stats"})
        self.assertIsNone(self.sender.latency_estimate())

    def test_stop_failure_and_restart_invalidate_timing_and_fence_old_reader(self):
        self.sender.start(HOME, "endpoint")
        self._publish_timing()
        old_state = self.sender._mute_state
        old_event = dict(self.sender._last_stats)
        self.sender._remember_native_event(old_state, {"event": "error", "message": "lost"})
        self.assertIsNone(self.sender.latency_estimate())
        self.sender.stop()
        self.assertIsNone(self.sender.latency_estimate())
        self.sender.start(HOME, "another")
        self.sender._remember_native_event(old_state, old_event)
        self.assertIsNone(self.sender.latency_estimate())
        self._publish_timing(estimated_delay_ms=1510.0, input_queue_ms=10.0)
        self.assertAlmostEqual(self.sender.latency_estimate()["seconds"], 1.51)
        self.sender.stop()
        self.assertIsNone(self.sender.latency_estimate())

    def _publish_volume(self, **updates):
        state = {"volume": 0.3, "known": True, "source": "receiver_read",
                 "pending": False, "last_code": 200}
        state.update(updates)
        self.sender._remember_native_event(self.sender._mute_state,
            {"event": "receiver_volume", "receiver_volume": state})

    def test_receiver_volume_is_unknown_until_connected_actual_read_and_send_does_not_confirm(self):
        self.assertIsNone(self.sender.receiver_volume())
        self.assertFalse(self.sender.set_volume(0.5))
        self.sender.start(HOME, "endpoint")
        self._wait_events()
        self.assertIsNone(self.sender.receiver_volume())
        self._publish_volume()
        self.assertAlmostEqual(self.sender.receiver_volume(), 0.3)
        self.assertTrue(self.sender.set_volume(0.7))
        self.assertAlmostEqual(self.sender.receiver_volume(), 0.3)
        self._publish_volume(volume=0.7, source="receiver_ack", pending=False)
        self.assertAlmostEqual(self.sender.receiver_volume(), 0.7)
        view = self.sender.receiver_volume_state()
        view["volume"] = 1
        self.assertAlmostEqual(self.sender.receiver_volume(), 0.7)
        self.sender._remember_native_event(self.sender._mute_state, {"event": "error", "message": "lost"})
        self.assertFalse(self.sender.set_volume(0.8))
        self.assertIsNone(self.sender.receiver_volume())

    def test_receiver_volume_rejects_malformed_state_and_slider_commands(self):
        self.sender.start(HOME, "endpoint")
        self._wait_events()
        for value in (-0.01, 1.01, True, "0.5", None, float("nan"), float("inf"), 10 ** 400):
            with self.subTest(value=value):
                self.assertFalse(self.sender.set_volume(value))
                self._publish_volume(volume=value)
                self.assertIsNone(self.sender.receiver_volume())
        self._publish_volume(known=False, volume=0.5)
        self.assertIsNone(self.sender.receiver_volume())
        self._publish_volume(source="sent", volume=0.5)
        self.assertIsNone(self.sender.receiver_volume())
        self._publish_volume(volume=0, source="receiver_ack")
        self.assertEqual(self.sender.receiver_volume(), 0)
        self._publish_volume(known=False, volume=None, source="unknown", last_code=501)
        self.assertEqual(self.sender.receiver_volume_state()["last_code"], 501)
        self.assertIsNone(self.sender.receiver_volume())

    def test_receiver_volume_resets_on_stop_and_fences_previous_helper_events(self):
        self.sender.start(HOME, "endpoint")
        self._wait_events()
        self._publish_volume(volume=0.9)
        old_state = self.sender._mute_state
        self.sender.stop()
        self.assertIsNone(self.sender.receiver_volume())
        self.assertFalse(self.sender.set_volume(1))
        self.sender.start(HOME, "another")
        self._wait_events()
        self.sender._remember_native_event(old_state,
            {"event": "receiver_volume", "receiver_volume": {"volume": 0.9, "known": True, "source": "receiver_ack"}})
        self.assertIsNone(self.sender.receiver_volume())
        self.sender._remember_native_event(self.sender._mute_state,
            {"event": "stats", "receiver_volume": {"volume": 0.2, "known": True,
             "source": "receiver_info", "pending": False, "last_code": 0}})
        self.assertAlmostEqual(self.sender.receiver_volume(), 0.2)

    def test_real_child_receives_normalized_json_and_publishes_receiver_ack(self):
        self.script.write_text(
            "import sys,json\n"
            "print(json.dumps({'event':'waiting_audio'}),flush=True)\n"
            "for line in sys.stdin:\n"
            " if line.strip()=='stop': break\n"
            " command=json.loads(line)\n"
            " if command['command']=='set_volume':\n"
            "  print(json.dumps({'event':'receiver_volume','receiver_volume':{'volume':command['volume'],'known':True,'source':'receiver_ack','pending':False,'last_code':200}}),flush=True)\n",
            encoding="utf-8")
        self.sender.start(HOME, "endpoint")
        self._wait_events()
        self.assertTrue(self.sender.set_volume(0.65))
        event = self._wait_events()[-1]
        self.assertEqual(event["event"], "receiver_volume")
        self.assertEqual(event["receiver_volume"]["volume"], 0.65)
        self.assertEqual(self.sender.receiver_volume_state()["source"], "receiver_ack")


if __name__ == "__main__":
    unittest.main()
