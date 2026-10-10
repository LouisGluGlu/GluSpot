from __future__ import annotations

from dataclasses import replace
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.windows_audio_sessions import (
    AudioSessionEvidence, MusicAudioSource, ProcessIdentity,
    _WindowsAudioSessionAdapter, list_music_audio_sessions, resolve_music_audio_source,
)


def _process(pid, created, path=r"C:\Music\cloudmusic.exe", app_id=""):
    return ProcessIdentity(pid, created, path, app_id)


def _session(pid, instance=None, endpoint="speakers", **changes):
    instance = instance or f"instance-{pid}"
    return AudioSessionEvidence(endpoint, pid, f"session-{pid}", instance, **changes)


class _Adapter:
    def __init__(self, sessions=(), processes=None, parents=None):
        self.sessions = sessions
        self.processes = processes or {}
        self.parents = parents or {}
        self.identity_reads = []
        self.list_reads = 0

    def list_sessions(self):
        self.list_reads += 1
        return self.sessions

    def process_identity(self, pid):
        self.identity_reads.append(pid)
        value = self.processes.get(pid)
        if isinstance(value, list):
            return value.pop(0) if len(value) > 1 else value[0]
        return value

    def process_parents(self):
        return self.parents


class WindowsAudioSessionTests(unittest.TestCase):
    def setUp(self):
        self.own_pid = patch("api.windows_audio_sessions.os.getpid", return_value=900)
        self.own_pid.start()
        self.addCleanup(self.own_pid.stop)

    def test_active_background_audio_process_resolves_without_foreground_evidence(self):
        adapter = _Adapter((_session(30),), {30: _process(30, 1000)})
        with patch("api.windows_audio_sessions.time.monotonic", return_value=123.5):
            source = resolve_music_audio_source("cloudmusic.exe", adapter=adapter)
        self.assertIsInstance(source, MusicAudioSource)
        self.assertEqual((source.process_id, source.process_created_at_100ns), (30, 1000))
        self.assertEqual(source.session_process_ids, (30,))
        self.assertEqual(source.observed_at, 123.5)
        self.assertEqual(source.sessions[0].session_instance_id, "instance-30")

    def test_matching_audio_children_share_highest_verified_parent_root(self):
        adapter = _Adapter((_session(31), _session(32, endpoint="bluetooth")),
                           {30: _process(30, 1000), 31: _process(31, 1100),
                            32: _process(32, 1200)}, {31: 30, 32: 30})
        source = resolve_music_audio_source("CloudMusic.EXE", adapter=adapter)
        self.assertEqual(source.process_id, 30)
        self.assertEqual(source.session_process_ids, (31, 32))
        self.assertEqual(tuple(s.endpoint_id for s in source.sessions), ("speakers", "bluetooth"))

    def test_differently_named_audio_child_can_belong_to_exact_music_app_parent(self):
        adapter = _Adapter((_session(32),),
                           {30: _process(30, 1000), 32: _process(32, 1200, r"C:\Music\audio-helper.exe")},
                           {32: 30})
        self.assertEqual(resolve_music_audio_source("cloudmusic.exe", adapter=adapter).process_id, 30)

    def test_simultaneous_independent_matching_app_roots_are_ambiguous(self):
        adapter = _Adapter((_session(30), _session(50)),
                           {30: _process(30, 1000), 50: _process(50, 1200)})
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_unrelated_active_sound_cannot_join_selected_source(self):
        adapter = _Adapter((_session(30), _session(40)),
                           {30: _process(30, 1000), 40: _process(40, 1100, r"C:\Browser\chrome.exe")})
        source = resolve_music_audio_source("cloudmusic.exe", adapter=adapter)
        self.assertEqual(source.session_process_ids, (30,))
        self.assertEqual(source.sessions, (_session(30),))

    def test_source_display_name_or_substring_never_authorizes_capture(self):
        adapter = _Adapter((_session(30),), {30: _process(30, 1000)})
        for value in ("cloudmusic", "网易云音乐", "music.exe", " cloudmusic.exe", "cloudmusic.exe ",
                      "folder/cloudmusic.exe", "cloudmusic.exe\0", "", None, True):
            with self.subTest(value=value):
                self.assertIsNone(resolve_music_audio_source(value, adapter=adapter))

    def test_complete_path_matches_exactly_with_windows_case_and_separator_rules(self):
        adapter = _Adapter((_session(30), _session(40)),
                           {30: _process(30, 1000), 40: _process(40, 1200, r"D:\Other\cloudmusic.exe")})
        self.assertEqual(resolve_music_audio_source("c:/MUSIC/cloudmusic.exe", adapter=adapter).process_id, 30)
        self.assertIsNone(resolve_music_audio_source(r"C:\Different\cloudmusic.exe", adapter=adapter))
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_packaged_source_requires_exact_process_application_user_model_id(self):
        app_id = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
        adapter = _Adapter((_session(30),),
                           {30: _process(30, 1000, r"C:\WindowsApps\Spotify.exe", app_id)})
        self.assertEqual(resolve_music_audio_source(app_id, adapter=adapter).process_id, 30)
        self.assertIsNone(resolve_music_audio_source(app_id.lower(), adapter=adapter))
        self.assertIsNone(resolve_music_audio_source("OtherPackage!Spotify", adapter=adapter))

    def test_newer_reused_parent_pid_cannot_expand_child_tree(self):
        adapter = _Adapter((_session(32),),
                           {30: _process(30, 1300), 32: _process(32, 1200, r"C:\Music\audio-helper.exe")},
                           {32: 30})
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_reused_parent_does_not_discard_independent_matching_audio_child(self):
        adapter = _Adapter((_session(32),), {30: _process(30, 1300), 32: _process(32, 1200)}, {32: 30})
        source = resolve_music_audio_source("cloudmusic.exe", adapter=adapter)
        self.assertEqual((source.process_id, source.process_created_at_100ns), (32, 1200))

    def test_root_pid_reuse_between_initial_and_final_read_invalidates_source(self):
        adapter = _Adapter((_session(30),), {30: [_process(30, 1000), _process(30, 2000)]})
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_root_exit_or_image_change_before_final_read_invalidates_source(self):
        for final in (None, _process(30, 1000, r"C:\Browser\chrome.exe")):
            with self.subTest(final=final):
                adapter = _Adapter((_session(30),), {30: [_process(30, 1000), final]})
                self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_inactive_system_cross_process_and_own_sessions_are_excluded(self):
        adapter = _Adapter((_session(30, active=False), _session(31, system_sounds=True),
                            _session(32, single_process=False), _session(900), _session(33)),
                           {33: _process(33, 1000)})
        self.assertEqual(list_music_audio_sessions(adapter=adapter), (_session(33),))

    def test_own_descendant_cannot_become_capture_source(self):
        adapter = _Adapter((_session(31),),
                           {31: _process(31, 1200), 900: _process(900, 1000)}, {31: 900})
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_duplicate_session_instance_is_retained_once_per_endpoint(self):
        first = _session(30)
        second_endpoint = replace(first, endpoint_id="bluetooth")
        adapter = _Adapter((first, first, second_endpoint), {30: _process(30, 1000)})
        self.assertEqual(list_music_audio_sessions(adapter=adapter), (first, second_endpoint))

    def test_malformed_sessions_supply_no_source_authority(self):
        good = _session(30)
        bad = (None, {}, replace(good, process_id=True), replace(good, process_id=0),
               replace(good, process_id=2**32), replace(good, endpoint_id=""),
               replace(good, session_instance_id=""), replace(good, session_identifier="bad\0"))
        adapter = _Adapter(bad, {30: _process(30, 1000)})
        self.assertEqual(list_music_audio_sessions(adapter=adapter), ())
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_malformed_process_identity_supplies_no_source_authority(self):
        good = _process(30, 1000)
        for value in (None, {}, replace(good, process_id=31), replace(good, process_created_at_100ns=0),
                      replace(good, process_created_at_100ns=True), replace(good, process_created_at_100ns=2**64),
                      replace(good, process_path="cloudmusic.exe"), replace(good, process_path="C:\\bad\0.exe")):
            with self.subTest(value=value):
                adapter = _Adapter((_session(30),), {30: value})
                self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_cyclic_ancestry_cannot_authorize_tree_capture(self):
        adapter = _Adapter((_session(30),), {30: _process(30, 1000), 31: _process(31, 1000)}, {30: 31, 31: 30})
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_deep_ancestry_is_bounded_and_fails_closed(self):
        processes = {pid: _process(pid, 1000) for pid in range(1, 67)}
        parents = {pid: pid + 1 for pid in range(1, 66)}
        adapter = _Adapter((_session(1),), processes, parents)
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))
        self.assertLessEqual(len(adapter.identity_reads), 65)

    def test_unreadable_audio_owner_and_source_backend_errors_do_not_fallback(self):
        adapter = _Adapter((_session(30),), {})
        self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))
        with patch.object(adapter, "list_sessions", side_effect=OSError("access denied")):
            self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))
        with patch.object(adapter, "process_parents", return_value=[]):
            self.assertIsNone(resolve_music_audio_source("cloudmusic.exe", adapter=adapter))

    def test_unsupported_platform_does_not_construct_windows_backend(self):
        with patch("api.windows_audio_sessions.sys.platform", "linux"), patch(
                "api.windows_audio_sessions._WindowsAudioSessionAdapter") as native:
            self.assertEqual(list_music_audio_sessions(), ())
            self.assertIsNone(resolve_music_audio_source("cloudmusic.exe"))
            native.assert_not_called()

    def test_native_enumeration_uses_checked_read_slots_and_frees_identifiers(self):
        # One real source, plus inactive/system/multiprocess sessions. The
        # virtual COM fixture checks actual native adapter slots and out types,
        # independently of the high-level resolver's DTO-only seam.
        fixture = (
            {"state": 1, "system": 1, "pid_code": 0, "pid": 30},
            {"state": 0, "system": 1, "pid_code": 0, "pid": 31},
            {"state": 1, "system": 0, "pid_code": 0, "pid": 32},
            {"state": 1, "system": 1, "pid_code": 0x0889000d, "pid": 33},
        )
        buffers, freed, calls = [], [], []

        class Scope:
            def __init__(scope, ole):
                pass

            def __enter__(scope):
                return scope

            def __exit__(scope, *args):
                pass

            def create(scope, clsid, iid):
                return ctypes.c_void_p(10)

            def own(scope, pointer):
                return pointer

        def read(pointer, slot, argtypes, *args, **kwargs):
            value = pointer.value
            calls.append((value, slot))
            if (value, slot) == (10, 3):
                args[-1]._obj.value = 20
            elif (value, slot) == (20, 3):
                args[-1]._obj.value = 1
            elif (value, slot) == (20, 4):
                args[-1]._obj.value = 30
            elif (value, slot) == (30, 3):
                args[-1]._obj.value = 40
            elif (value, slot) == (40, 5):
                args[-1]._obj.value = 50
            elif (value, slot) == (50, 3):
                args[-1]._obj.value = len(fixture)
            elif (value, slot) == (50, 4):
                args[-1]._obj.value = 100 + args[0]
            elif 100 <= value < 104 and slot == 3:
                args[-1]._obj.value = fixture[value - 100]["state"]
            elif 100 <= value < 104 and slot == 0:
                args[-1]._obj.value = value + 100
            elif 200 <= value < 204 and slot == 15:
                return fixture[value - 200]["system"]
            elif 200 <= value < 204 and slot == 14:
                args[-1]._obj.value = fixture[value - 200]["pid"]
                return fixture[value - 200]["pid_code"]
            elif value == 200 and slot in (12, 13):
                buffer = ctypes.create_unicode_buffer("id" if slot == 12 else "instance")
                buffers.append(buffer)
                args[-1]._obj.value = ctypes.addressof(buffer)
            else:
                self.fail(f"Unexpected COM call or mutation: {(value, slot)}")
            return 0

        adapter = _WindowsAudioSessionAdapter.__new__(_WindowsAudioSessionAdapter)
        adapter._core = SimpleNamespace(
            _ole32=SimpleNamespace(CoTaskMemFree=lambda pointer: freed.append(pointer.value)),
            _device_id=lambda device: "speakers",
        )
        with patch("api.windows_audio_sessions._ComScope", Scope), patch(
                "api.windows_audio_sessions._call", side_effect=read):
            sessions = adapter.list_sessions()
        self.assertEqual(sessions, (AudioSessionEvidence("speakers", 30, "id", "instance"),))
        self.assertEqual(freed, [ctypes.addressof(buffer) for buffer in buffers])
        self.assertNotIn((101, 0), calls)  # Inactive sessions are not guessed.
        self.assertNotIn((202, 14), calls)  # System sound PID is never borrowed.
        self.assertNotIn((203, 12), calls)  # Shared session is not a sole app.


if __name__ == "__main__":
    unittest.main()
