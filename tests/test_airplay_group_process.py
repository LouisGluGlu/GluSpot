from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from api.airplay_group_process import AirPlayGroupProcess
from api.audio_routes import AudioRouteDevice
from api.windows_audio_sessions import AudioSessionEvidence, MusicAudioSource


HOME = AudioRouteDevice("home", "起居室", transport="airplay", host="192.0.2.1", device_id="AABB")
BEDROOM = AudioRouteDevice("bedroom", "卧室", transport="airplay", host="192.0.2.2", device_id="CCDD")
M6 = AudioRouteDevice("m6", "M6", is_bluetooth=True)
SPEAKERS = AudioRouteDevice("speakers", "本地音响")
SESSION = AudioSessionEvidence("source", 124, "music-session", "music-instance")
SOURCE = MusicAudioSource("cloudmusic.exe", 123, 134353908200167471, r"C:\Music\cloudmusic.exe",
                          (124,), (SESSION.session_identifier,), (SESSION,), 100.0)


class AirPlayGroupProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.script = Path(self.temp.name) / "group.py"
        self.script.write_text(
            "import sys,json\n"
            "print(json.dumps({'event':'waiting_audio'}),flush=True)\n"
            "print(json.dumps({'event':'stats','members':[{'receiver_id':'home','state':'waiting_audio',"
            "'receiver_volume':{'volume':0.4,'known':True,'source':'receiver_read','operation':'read','last_code':200}},"
            "{'receiver_id':'bedroom','state':'waiting_audio','receiver_volume':{'volume':0.7,'known':True,"
            "'source':'receiver_info','operation':'info','last_code':200}}],"
            "'local_renders':[dict(endpoint_id=sys.argv[i+1],ready=True,armed=True,playing=True,meaningful_samples=1) "
            "for i,arg in enumerate(sys.argv) if arg=='--local-endpoint'],"
            "'timing':{'valid':True,'method':'protocol_plus_input_queue',"
            "'estimated_delay_ms':1520,'protocol_buffer_ms':1500,'input_queue_ms':20}}),flush=True)\n"
            "for line in sys.stdin:\n"
            " if line.strip()=='stop': break\n"
            " command=json.loads(line)\n"
            " if command.get('command')=='set_volume':\n"
            "  receiver=('home','bedroom')[command['receiver']]\n"
            "  print(json.dumps({'event':'receiver_volume','members':[{'receiver_id':receiver,"
            "'receiver_volume':{'volume':command['volume'],'known':True,'source':'receiver_ack',"
            "'operation':'set','pending':False,'last_code':200}}]}),flush=True)\n"
            "print(json.dumps({'event':'stopped'}),flush=True)\n", encoding="utf-8")
        self.args, self.kwargs = None, None

        def spawn(args, **kwargs):
            self.args, self.kwargs = args, kwargs
            return subprocess.Popen([sys.executable, "-u", str(self.script), *args[1:]], **kwargs)

        self.sender = AirPlayGroupProcess(self.script, spawn, source_id="02:01:02:03:04:05")

    def tearDown(self):
        self.sender.stop()
        self.temp.cleanup()

    def _wait(self, predicate):
        deadline = time.monotonic() + 3
        results = []
        while time.monotonic() < deadline:
            results.extend(self.sender.poll_events())
            if predicate(results):
                return results
            time.sleep(.01)
        self.fail("group child did not publish the expected state")

    def _connected(self, devices=(HOME, BEDROOM)):
        self.sender.start_group(devices, SOURCE, "source")
        self._wait(lambda events: any(event["event"] == "stats" for event in events))

    @staticmethod
    def _local_stats(renders):
        return {"event": "stats", "local_renders": renders,
                "timing": {"valid": True, "method": "protocol_plus_input_queue",
                           "estimated_delay_ms": 1520, "protocol_buffer_ms": 1500, "input_queue_ms": 20}}

    @staticmethod
    def _render(endpoint, **changes):
        return dict(endpoint_id=endpoint, **dict({"ready": True, "armed": True, "playing": True,
                                                 "meaningful_samples": 1}, **changes))

    def test_one_child_owns_music_capture_and_all_named_receiver_arguments(self):
        self._connected((HOME, BEDROOM, M6))
        self.assertIn("--group", self.args)
        self.assertEqual(self.args[self.args.index("--music-pid") + 1], "123")
        self.assertEqual(self.args[self.args.index("--music-created-at") + 1], "134353908200167471")
        self.assertEqual(self.args[self.args.index("--source-endpoint") + 1], "source")
        self.assertEqual(self.args[self.args.index("--local-endpoint") + 1], "m6")
        self.assertEqual(self.args.count("--receiver-id"), 2)
        self.assertEqual([self.args[index + 1] for index, arg in enumerate(self.args) if arg == "--receiver-id"],
                         ["home", "bedroom"])
        self.assertNotIn("--exclusive-output", self.args)
        self.assertNotIn("--endpoint", self.args)
        self.assertNotIn("shell", self.kwargs)

    def test_two_local_outputs_and_two_homepods_share_one_music_capture_child(self):
        self._connected((HOME, SPEAKERS, BEDROOM, M6))
        self.assertEqual([self.args[index + 1] for index, arg in enumerate(self.args)
                          if arg == "--local-endpoint"], ["speakers", "m6"])
        self.assertEqual(self.args.count("--music-pid"), 1)
        self.assertEqual(self.args.count("--source-endpoint"), 1)
        self.assertEqual(self.args.count("--receiver-id"), 2)
        self.assertNotIn("--endpoint", self.args)
        self.assertNotIn("--exclusive-output", self.args)
        diagnostic = self.sender.diagnostics()
        self.assertEqual(diagnostic["local_endpoints"], ("speakers", "m6"))
        self.assertEqual(diagnostic["local_endpoint"], "")
        self.assertEqual([render["endpoint_id"] for render in diagnostic["local_renders"]],
                         ["speakers", "m6"])
        self.assertAlmostEqual(self.sender.latency_estimate()["seconds"], 1.52)

    def test_two_windows_outputs_spawn_one_existing_helper_without_receiver_or_system_capture(self):
        self._connected((M6, SPEAKERS))
        self.assertEqual(self.args.count("--music-pid"), 1)
        self.assertEqual(self.args.count("--source-endpoint"), 1)
        self.assertEqual([self.args[index + 1] for index, arg in enumerate(self.args)
                          if arg == "--local-endpoint"], ["m6", "speakers"])
        self.assertNotIn("--receiver-id", self.args)
        self.assertNotIn("--host", self.args)
        self.assertNotIn("--exclusive-output", self.args)
        self.assertNotIn("--endpoint", self.args)
        self.assertEqual(self.sender.receiver_states(), {})
        self.assertEqual(self.sender.diagnostics()["local_endpoints"], ("m6", "speakers"))
        # The legacy fixture reports network timing. It cannot validate a
        # Windows-only timeline even when both named local rows are healthy.
        self.assertIsNone(self.sender.latency_estimate())


    def test_single_local_diagnostic_alias_does_not_choose_first_of_multiple_outputs(self):
        self._connected((HOME, M6))
        self.assertEqual(self.sender.diagnostics()["local_endpoint"], "m6")
        self.assertEqual(self.sender.diagnostics()["local_endpoints"], ("m6",))
        self.sender.start_group((HOME,), SOURCE, "source")
        self._wait(lambda events: any(event["event"] == "stats" for event in events))
        self.assertEqual(self.sender.diagnostics()["local_endpoint"], "")
        self.assertEqual(self.sender.diagnostics()["local_endpoints"], ())
        self.assertEqual(self.sender.diagnostics()["local_renders"], ())

    def test_group_spawn_binds_and_stop_releases_exact_capture_child_identity(self):
        from api.windows_audio_sessions import ProcessIdentity
        adapter = Mock()
        adapter.process_identity.side_effect = lambda pid: ProcessIdentity(pid, 100, str(self.script.resolve()))
        with patch("api.airplay_process.sys.platform", "win32"), patch(
                "api.airplay_process._WindowsAudioSessionAdapter", return_value=adapter):
            self._connected()
        identity = self.sender.owned_process_identity()
        self.assertEqual(identity.process_id, self.sender._process.pid)
        self.assertEqual(identity.process_path, str(self.script.resolve()))
        self.sender.stop()
        self.assertIsNone(self.sender.owned_process_identity())

    def test_individual_receiver_volumes_are_confirmed_independently(self):
        self._connected()
        self.assertEqual(self.sender.receiver_volume_state("home")["volume"], .4)
        self.assertEqual(self.sender.receiver_volume_state("bedroom")["volume"], .7)
        self.assertIsNone(self.sender.receiver_volume_state("unknown")["volume"])
        self.assertTrue(self.sender.set_device_volume("bedroom", .23))
        self._wait(lambda events: any(event["event"] == "receiver_volume" for event in events))
        self.assertEqual(self.sender.receiver_volume_state("bedroom")["volume"], .23)
        self.assertEqual(self.sender.receiver_volume_state("home")["volume"], .4)

    def test_command_is_canonical_and_index_tracks_explicit_receiver_order(self):
        self._connected((BEDROOM, HOME))
        writer = Mock()
        original = self.sender._process.stdin
        self.sender._process.stdin = writer
        try:
            self.assertTrue(self.sender.set_device_volume("home", .51))
            writer.write.assert_called_once_with('{"command":"set_volume","receiver":1,"volume":0.51}\n')
            writer.flush.assert_called_once()
        finally:
            self.sender._process.stdin = original

    def test_invalid_or_unnamed_volume_never_retargets_a_receiver(self):
        self._connected()
        self.assertFalse(self.sender.set_volume(.2))
        for value in (None, True, -1, 1.01, float("inf"), float("nan"), 10**10000, "0.4"):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(self.sender.set_device_volume("home", value))
        self.assertFalse(self.sender.set_device_volume("m6", .2))

    def test_source_identity_and_session_evidence_are_required_before_any_spawn(self):
        for source in (None, replace(SOURCE, process_id=0), replace(SOURCE, process_id=True),
                       replace(SOURCE, process_created_at_100ns=0),
                       replace(SOURCE, process_created_at_100ns=True),
                       replace(SOURCE, process_created_at_100ns=2**64), replace(SOURCE, sessions=()),
                       replace(SOURCE, session_process_ids=()),
                       replace(SOURCE, session_process_ids=(True,)),
                       replace(SOURCE, sessions=(replace(SESSION, active=False),)),
                       replace(SOURCE, sessions=(replace(SESSION, system_sounds=True),)),
                       replace(SOURCE, sessions=(replace(SESSION, single_process=False),)),
                       replace(SOURCE, sessions=(replace(SESSION, endpoint_id="another"),))):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    self.sender.start_group((HOME,), source, "source")
                self.assertIsNone(self.sender._process)
                self.assertIsNone(self.args)

    def test_invalid_selection_and_host_never_spawns_fallback(self):
        choices = ((), (M6,), (HOME, HOME), (HOME, replace(M6, id="source")),
                   (HOME, SPEAKERS, replace(M6, id="SOURCE")),
                   (HOME, M6, replace(M6, id="M6")), (replace(HOME, available=False),),
                   (replace(HOME, host="0.0.0.0"),), (replace(HOME, host="239.0.0.1"),),
                   (replace(HOME, host="::1"),), (replace(HOME, port=True),),
                   (replace(HOME, port=0),), (replace(HOME, id="bad\0"),),
                   (replace(HOME, name="bad\nname"),), (HOME, replace(BEDROOM, host=HOME.host)),
                   (replace(HOME, transport="unknown"),))
        for devices in choices:
            with self.subTest(devices=devices):
                with self.assertRaises(ValueError):
                    self.sender.start_group(devices, SOURCE, "source")
                self.assertIsNone(self.args)

    def test_total_sink_limit_counts_bluetooth_and_preserves_member_ids(self):
        receivers = tuple(replace(HOME, id=f"receiver-{i}", host=f"192.0.2.{i + 1}") for i in range(8))
        with self.assertRaises(ValueError):
            self.sender.start_group(receivers + (M6,), SOURCE, "source")
        self.sender.start_group(receivers, SOURCE, "source")
        self.assertEqual(self.args.count("--receiver-id"), 8)

    def test_total_sink_limit_accepts_one_airplay_and_seven_distinct_windows_outputs(self):
        locals_ = tuple(replace(M6, id=f"windows-{index}") for index in range(7))
        self.sender.start_group((HOME,) + locals_, SOURCE, "source")
        self._wait(lambda events: any(event["event"] == "stats" for event in events))
        process = self.sender._process
        self.assertEqual(self.args.count("--local-endpoint"), 7)
        self.assertEqual(self.args.count("--receiver-id"), 1)
        self.assertEqual(len(self.sender.diagnostics()["local_renders"]), 7)
        with self.assertRaises(ValueError):
            self.sender.start_group((HOME,) + locals_ + (SPEAKERS,), SOURCE, "source")
        self.assertIs(self.sender._process, process)
        self.assertIsNone(process.poll())

    def test_endpoint_collision_fence_preserves_opaque_original_arguments(self):
        endpoint = "{0.0.0.00000000}.{ABCDEF01-2345-6789-ABCD-001122334455}"
        source = replace(SOURCE, sessions=(replace(SESSION, endpoint_id="SOURCE"),))
        self.sender.start_group((HOME, replace(M6, id=endpoint)), source, "source")
        self.assertEqual(self.args[self.args.index("--local-endpoint") + 1], endpoint)
        with self.assertRaises(ValueError):
            self.sender.start_group((HOME, replace(M6, id=endpoint), replace(SPEAKERS, id=endpoint.lower())),
                                    SOURCE, "source")

    def test_validation_failure_does_not_stop_current_group(self):
        self._connected()
        process, args = self.sender._process, self.args
        with self.assertRaises(ValueError):
            self.sender.start_group((HOME,), replace(SOURCE, process_id=0), "source")
        self.assertIs(self.sender._process, process)
        self.assertEqual(self.args, args)
        self.assertIsNone(process.poll())

    def test_receiver_name_and_id_are_individual_arguments_without_shell_execution(self):
        device = replace(HOME, name='起居室 $() "', id='receiver $() "')
        self.sender.start_group((device,), SOURCE, "source")
        self.assertEqual(self.args[self.args.index("--name") + 1], device.name)
        self.assertEqual(self.args[self.args.index("--receiver-id") + 1], device.id)
        self.assertNotIn("shell", self.kwargs)

    def test_restart_fences_old_member_status_ack_and_event_queue(self):
        self._connected()
        old_state, old_queue = self.sender._mute_state, self.sender._events
        self.sender.start_group((BEDROOM, HOME), SOURCE, "source")
        self._wait(lambda events: any(event["event"] == "stats" for event in events))
        old_queue.put({"event": "error", "message": "old group"})
        self.sender._remember_native_event(old_state, {"event": "receiver_volume", "members": [{
            "receiver_id": "home", "receiver_volume": {"known": True, "source": "receiver_ack", "volume": .99}}]})
        self.assertEqual(self.sender.receiver_volume_state("home")["volume"], .4)
        self.assertNotIn("old group", [event.get("message") for event in self.sender.poll_events()])

    def test_unexpected_or_duplicate_members_cannot_replace_receiver_evidence(self):
        self._connected()
        for members in ([{"receiver_id": "intruder"}], [{"receiver_id": "home"}, {"receiver_id": "home"}],
                        [None], [{"receiver_id": []}], [{"receiver_id": "home"}] * 9):
            with self.subTest(members=members):
                self.sender._remember_native_event(self.sender._mute_state, {"event": "receiver_volume", "members": members})
                self.assertEqual(self.sender.receiver_volume_state("home")["volume"], .4)

    def test_malformed_volume_and_member_state_are_not_confirmation(self):
        self._connected()
        for volume in (True, "0.2", float("inf"), -1, 2, 10**10000):
            with self.subTest(volume=type(volume).__name__):
                self.sender._remember_native_event(self.sender._mute_state, {"event": "stats", "members": [{
                    "receiver_id": "home", "state": [], "receiver_volume": {
                        "known": True, "source": "receiver_read", "volume": volume, "operation": []}}]})
                self.assertFalse(self.sender.receiver_volume_state("home")["known"])
                self.assertEqual(self.sender.receiver_states()["home"]["state"], "unknown")
        self.sender._remember_native_event(self.sender._mute_state, {"event": "stats", "members": [{
            "receiver_id": "home", "receiver_volume": {"known": True, "source": [], "volume": .3}}]})
        self.assertFalse(self.sender.receiver_volume_state("home")["known"])

    def test_missing_helper_and_failed_createprocess_have_no_prior_ownership(self):
        absent = AirPlayGroupProcess(Path(self.temp.name) / "absent.exe")
        with self.assertRaisesRegex(RuntimeError, "投放组件未安装"):
            absent.start_group((HOME,), SOURCE, "source")
        self._connected()
        self.sender.needs_local_output_restore = True
        self.sender._popen = Mock(side_effect=OSError("CreateProcess failed"))
        with self.assertRaisesRegex(OSError, "CreateProcess failed"):
            self.sender.start_group((HOME,), SOURCE, "source")
        self.assertFalse(self.sender.needs_local_output_restore)
        self.assertEqual(self.sender.diagnostics()["local_output"], {})
        self.assertEqual(self.sender.receiver_volume_state("home")["known"], False)

    def test_partial_duplicate_unknown_or_malformed_local_samples_invalidate_group_timing(self):
        self._connected((HOME, SPEAKERS, M6))
        healthy = [self._render("speakers"), self._render("m6")]
        choices = [None, [], healthy[:1], healthy + [self._render("unknown")],
                   [self._render("speakers"), self._render("speakers")],
                   [self._render("speakers"), self._render("unknown")],
                   [self._render("speakers"), self._render([])],
                   [self._render("speakers"), None], healthy * 5]
        for field, value in (("playing", 1), ("ready", "yes"), ("armed", None),
                             ("meaningful_samples", True), ("meaningful_samples", -1),
                             ("meaningful_samples", 2**64), ("meaningful_samples", "1"),
                             ("clock_qpc_age_ms", float("inf")), ("phase_error_ms", float("nan")),
                             ("resample_ratio", 10**10000), ("clock_position", 1.5)):
            choices.append([self._render("speakers"), self._render("m6", **{field: value})])
        for renders in choices:
            with self.subTest(renders=type(renders).__name__):
                self.sender._remember_native_event(self.sender._mute_state, self._local_stats(healthy))
                self.assertIsNotNone(self.sender.latency_estimate())
                self.sender._remember_native_event(self.sender._mute_state, self._local_stats(renders))
                self.assertIsNone(self.sender.latency_estimate())
                self.assertEqual(self.sender.diagnostics()["local_renders"], ())
                self.assertEqual(self.sender.diagnostics()["stats"]["local_renders"], [])

    def test_each_named_local_must_render_meaningful_pcm_before_timing_is_valid(self):
        self._connected((HOME, SPEAKERS, M6))
        for field, value in (("ready", False), ("armed", False), ("playing", False), ("meaningful_samples", 0)):
            with self.subTest(field=field):
                renders = [self._render("speakers"), self._render("m6", **{field: value})]
                self.sender._remember_native_event(self.sender._mute_state, self._local_stats(renders))
                self.assertIsNone(self.sender.latency_estimate())
                self.assertEqual(len(self.sender.diagnostics()["local_renders"]), 2)
        self.sender._remember_native_event(self.sender._mute_state, self._local_stats([
            self._render("speakers"), self._render("m6")]))
        self.assertIsNotNone(self.sender.latency_estimate())

    def test_legacy_unnamed_renderer_is_authority_only_for_one_selected_local(self):
        self._connected((HOME, M6))
        stats = self._local_stats([])
        stats.pop("local_renders")
        stats["local_render"] = {"ready": True, "armed": True, "playing": True, "meaningful_samples": 3}
        self.sender._remember_native_event(self.sender._mute_state, stats)
        self.assertIsNotNone(self.sender.latency_estimate())
        self.assertEqual(self.sender.diagnostics()["local_renders"][0]["endpoint_id"], "m6")
        self._connected((HOME, SPEAKERS, M6))
        self.sender._remember_native_event(self.sender._mute_state, stats)
        self.assertIsNone(self.sender.latency_estimate())
        self.assertEqual(self.sender.diagnostics()["local_renders"], ())
        self.assertNotIn("local_render", self.sender.diagnostics()["stats"])

    def test_renderer_stats_restart_fence_and_stop_cannot_reauthorize_old_timing(self):
        self._connected((HOME, M6))
        old_state = self.sender._mute_state
        old_sample = self._local_stats([self._render("m6", meaningful_samples=999)])
        self._connected((HOME, SPEAKERS, M6))
        self.sender._remember_native_event(old_state, old_sample)
        retained = self.sender.diagnostics()["local_renders"]
        self.assertEqual([render["endpoint_id"] for render in retained], ["speakers", "m6"])
        self.assertEqual(retained[1]["meaningful_samples"], 1)
        self.sender.stop()
        self.sender._remember_native_event(self.sender._mute_state, self._local_stats([
            self._render("speakers"), self._render("m6")]))
        self.assertIsNone(self.sender.latency_estimate())

    def test_named_renderer_diagnostics_are_copies_bounded_to_selected_endpoints(self):
        self._connected((HOME, SPEAKERS, M6))
        renders = [self._render("m6", source_frame=10, arbitrary_data=[1]),
                   self._render("speakers", source_frame=20)]
        self.sender._remember_native_event(self.sender._mute_state, self._local_stats(renders))
        renders[0]["source_frame"] = 100
        evidence = self.sender.diagnostics()["local_renders"]
        self.assertEqual([render["endpoint_id"] for render in evidence], ["speakers", "m6"])
        self.assertEqual(evidence[1]["source_frame"], 10)
        self.assertNotIn("arbitrary_data", evidence[1])
        evidence[1]["source_frame"] = 200
        self.assertEqual(self.sender.diagnostics()["local_renders"][1]["source_frame"], 10)

    def test_missing_helper_for_new_valid_attempt_cannot_inherit_stopped_mute_authority(self):
        self.sender._mute_state = {"phase": "muted", "endpoint_id": "old-source",
                                   "original_muted": False, "applied": True}
        self.sender.needs_local_output_restore = True
        self.sender.executable = Path(self.temp.name) / "removed-helper.exe"
        with self.assertRaisesRegex(RuntimeError, "投放组件未安装"):
            self.sender.start_group((HOME,), SOURCE, "source")
        self.assertFalse(self.sender.needs_local_output_restore)
        self.assertEqual(self.sender.diagnostics()["local_output"], {})
        self.assertIsNone(self.sender._process)
        self.assertIsNone(self.args)

    def test_real_child_stop_reaps_owned_process_and_makes_all_volume_unavailable(self):
        self._connected()
        process = self.sender._process
        self.sender.stop()
        self.assertEqual(process.poll(), 0)
        self.assertIsNone(self.sender._reader)
        self.assertIsNone(self.sender._stderr_reader)
        self.assertFalse(self.sender.receiver_volume_state("home")["known"])
        self.assertFalse(self.sender.set_device_volume("home", .5))

    def test_member_and_diagnostic_returns_do_not_mutate_internal_evidence(self):
        self._connected()
        event = {"event": "stats", "members": [{"receiver_id": "home", "protocol": {"counts": [1]},
                "receiver_volume": {"known": True, "source": "receiver_read", "volume": .4}}]}
        self.sender._remember_native_event(self.sender._mute_state, event)
        event["members"][0]["receiver_volume"]["volume"] = .99
        self.assertEqual(self.sender.receiver_volume_state("home")["volume"], .4)
        state = self.sender.receiver_states()
        state["home"]["protocol"]["counts"].append(2)
        self.assertEqual(self.sender.receiver_states()["home"]["protocol"]["counts"], [1])
        evidence = self.sender.diagnostics()
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["music_source"]["scope"], "music_process_tree")

    def test_large_group_stats_are_accepted_with_receiver_receipt_time(self):
        stats = {"event": "stats", "_received_at": -1, "members": [
            {"receiver_id": f"r-{i}", "protocol": {"message": "x" * 1500}} for i in range(8)]}
        text = json.dumps(stats) + "\n"
        self.assertGreater(len(text), 8192)
        events = queue.Queue(maxsize=32)
        with patch("api.airplay_group_process.time.monotonic", return_value=100):
            self.sender._read_events(SimpleNamespace(stdout=io.StringIO(text)), events)
        event = events.get_nowait()
        self.assertEqual(event["_received_at"], 100)
        self.assertEqual(len(event["members"]), 8)

    def test_oversize_lines_are_drained_and_unknown_events_rejected(self):
        oversize = "x" * 65537 + '{"event":"streaming"}\n'
        text = oversize + '{"event":"intruder"}\n[]\ninvalid\n{"event":"waiting_audio"}\n'
        events = queue.Queue(maxsize=32)
        self.sender._read_events(SimpleNamespace(stdout=io.StringIO(text)), events)
        self.assertEqual(events.get_nowait()["event"], "waiting_audio")
        self.assertTrue(events.empty())

    def test_terminal_survives_later_stat_flood_in_bounded_queue(self):
        text = '{"event":"error","message":"real failure"}\n' + '{"event":"stats"}\n' * 100
        text += '{"event":"mute_state","phase":"restored"}\n{"event":"stopped"}\n'
        events, remembered = queue.Queue(maxsize=32), []
        self.sender._read_events(SimpleNamespace(stdout=io.StringIO(text)), events, remembered.append)
        self.assertEqual(events.get_nowait()["message"], "real failure")
        self.assertTrue(events.empty())
        self.assertEqual(remembered[-2]["phase"], "restored")

    def test_connect_timeout_stops_and_reaps_a_stalled_group(self):
        self.script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        self.sender.start_group((HOME,), SOURCE, "source")
        process = self.sender._process
        with patch("api.airplay_process.time.monotonic", return_value=self.sender._started_at + 60):
            events = self.sender.poll_events()
        self.assertEqual(events[0]["event"], "error")
        self.assertIsNotNone(process.poll())

    def test_graceful_stop_wait_scales_with_bounded_member_teardown(self):
        process = Mock(returncode=0)
        process.poll.return_value = None
        self.sender._process = process
        self.sender._receiver_ids = tuple(f"receiver-{index}" for index in range(8))
        self.sender.stop()
        process.wait.assert_called_once_with(timeout=7.4)
        process.kill.assert_not_called()
        self.assertIsNone(self.sender._process)

    def test_group_stop_timeout_still_kills_reaps_and_closes_all_pipes(self):
        process = Mock(returncode=1)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("group", 7.4), None]
        self.sender._process = process
        self.sender._receiver_ids = tuple(f"receiver-{index}" for index in range(8))
        self.sender._mute_state = {"phase": "muted", "original_muted": False, "applied": True}
        self.sender.stop()
        self.assertEqual([call.kwargs["timeout"] for call in process.wait.call_args_list], [7.4, 2.0])
        process.kill.assert_called_once()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close.assert_called_once()
        self.assertTrue(self.sender.needs_local_output_restore)


if __name__ == "__main__":
    unittest.main()
