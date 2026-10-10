from __future__ import annotations

import ctypes
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.windows_app_audio_routes import (WindowsAppAudioRoutes, _WindowsAppRouteAdapter,
    AppAudioRouteMigrationTimeout, _PREFIX, _RENDER_SUFFIX)
from api.windows_audio_sessions import MusicAudioSource, AudioSessionEvidence, ProcessIdentity


def source(endpoint="bt", pid=30, created=100):
    session = AudioSessionEvidence(endpoint, pid, f"session-{pid}", f"instance-{pid}")
    return MusicAudioSource("cloudmusic.exe", pid, created, r"C:\Music\cloudmusic.exe", (pid,), (session.session_identifier,), (session,), 1)


class Adapter:
    def __init__(self):
        self.identities = {30: ProcessIdentity(30, 100, r"C:\Music\cloudmusic.exe")}
        self.routes = {(30, 0): "", (30, 1): "old-role-one"}
        self.writes, self.closed = [], []
        self.owners = (("bt", 30, True, False),)
        self.states = None
        self.fail_role = None
        self.confirm = True
        self.journal = None
        self.on_set = None

    def process_identity(self, pid):
        return self.identities.get(pid)

    def hold_process(self, identity):
        return (identity.process_id, identity.process_created_at_100ns)

    def close_handle(self, handle):
        self.closed.append(handle)

    def get_route(self, pid, role):
        return self.routes[(pid, role)]

    def set_route(self, pid, role, value):
        if self.journal is not None:
            saved = json.loads(self.journal.read_text(encoding="utf-8"))
            self.assert_intent = any(entry["intent"] and entry["role"] == role for entry in saved["roles"])
            if not self.assert_intent:
                raise AssertionError("Native Set happened before durable intent")
        self.writes.append((pid, role, value))
        if role == self.fail_role:
            raise OSError("Set failed")
        if self.confirm:
            self.routes[(pid, role)] = value
            if self.on_set is not None:
                self.on_set(pid, role, value)

    def active_owners(self):
        return self.owners

    def session_owners(self):
        return self.states if self.states is not None else tuple((*owner, 1) for owner in self.owners)


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Path(self.directory.name) / "recovery.json"
        self.adapter = Adapter()
        self.adapter.journal = self.journal
        self.current = source()
        def restore_effective(pid, role, value):
            if value == _PREFIX + "bt" + _RENDER_SUFFIX and self.current is not None and self.current.process_id == 30:
                self.current = source("bt")
        self.adapter.on_set = restore_effective
        self.default_endpoint = "bt"
        self.devices = SimpleNamespace(list_outputs=lambda: (SimpleNamespace(id="bt", is_default=self.default_endpoint == "bt"),
                                                             SimpleNamespace(id="spare", is_default=self.default_endpoint == "spare")))
        self.backend = WindowsAppAudioRoutes(devices=self.devices, adapter=self.adapter,
                                            resolver=lambda app: self.current, journal_path=self.journal)
        self.addCleanup(self.close_backend, self.backend)

    @staticmethod
    def close_backend(backend):
        try:
            backend.close(timeout=0)
        except RuntimeError:
            pass

    def prepare(self):
        return self.backend.prepare(self.current, "spare", "bt")

    def test_prepare_reads_only_and_retains_each_original_role(self):
        guard = self.prepare()
        self.assertEqual(self.adapter.writes, [])
        self.assertFalse(self.journal.exists())
        self.assertFalse(guard.migrated)
        guard.restore()
        self.assertEqual(self.adapter.closed, [(30, 100)])

    def test_read_only_guard_release_does_not_depend_on_disconnected_device_inventory(self):
        guard = self.prepare()
        self.devices.list_outputs = lambda: (_ for _ in ()).throw(OSError("output inventory unavailable"))
        guard.restore(timeout=0)
        self.assertEqual(self.adapter.writes, [])
        self.assertEqual(self.adapter.closed, [(30, 100)])
        self.assertIsNone(self.backend._active)
        self.assertIsNone(self.backend._lease)

    def test_inventory_failure_returns_owned_policy_and_keeps_effective_proof_pending(self):
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        original_inventory = self.devices.list_outputs
        self.devices.list_outputs = lambda: (_ for _ in ()).throw(OSError("device query failed"))
        before = len(self.adapter.writes)
        with self.assertRaisesRegex(RuntimeError, "inventory unavailable.*device query failed"):
            guard.restore(timeout=0)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): "old-role-one"})
        self.assertEqual(self.adapter.writes[before:], [(30, 1, "old-role-one"), (30, 0, "")])
        pending = self.backend.pending_recovery()
        self.assertEqual(pending["stage"], "effective_recovery_required")
        self.assertTrue(all(not role["restored"] for role in pending["roles"]))
        self.assertTrue(all(not role["recovery_reason"] for role in pending["roles"]))
        self.assertFalse(guard._closed)
        self.devices.list_outputs = original_inventory
        guard.restore(timeout=0)
        self.assertEqual(guard.restoration_state, "active_output_verified")
        self.assertFalse(self.journal.exists())

    def test_inventory_failure_does_not_overwrite_external_policy_or_reused_pid(self):
        for pid_reused in (False, True):
            with self.subTest(pid_reused=pid_reused):
                guard = self.prepare()
                guard.apply()
                newer = _PREFIX + "user-choice" + _RENDER_SUFFIX
                self.adapter.routes[(30, 1)] = newer
                original_inventory = self.devices.list_outputs
                self.devices.list_outputs = lambda: (_ for _ in ()).throw(OSError("device query failed"))
                before = len(self.adapter.writes)
                if pid_reused:
                    self.adapter.identities[30] = ProcessIdentity(30, 200, r"C:\Music\cloudmusic.exe")
                with self.assertRaisesRegex(RuntimeError, "inventory unavailable"):
                    guard.restore(timeout=0)
                self.assertEqual(self.adapter.routes[(30, 1)], newer)
                if pid_reused:
                    self.assertEqual(self.adapter.writes[before:], [])
                else:
                    self.assertEqual(self.adapter.writes[before:], [(30, 0, "")])
                self.assertEqual(self.backend.pending_recovery()["stage"], "effective_recovery_required")
                self.devices.list_outputs = original_inventory
                self.adapter.identities[30] = ProcessIdentity(30, 100, r"C:\Music\cloudmusic.exe")
                guard.restore(timeout=0)

    def test_policy_write_requires_live_session_migration_before_group_ready(self):
        guard = self.prepare()
        guard.apply()
        self.assertFalse(guard.migrated)
        with self.assertRaisesRegex(RuntimeError, "did not migrate"):
            guard.wait_migrated(timeout=0)
        self.current = source("spare")
        self.adapter.owners = (("spare", 30, True, False),)
        observed = guard.wait_migrated(timeout=0)
        self.assertEqual(observed.sessions[0].endpoint_id, "spare")
        self.assertTrue(guard.migrated)
        guard.restore()
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): "old-role-one"})
        self.assertEqual(self.adapter.writes[2:], [
            (30, 1, "old-role-one"), (30, 0, _PREFIX + "bt" + _RENDER_SUFFIX), (30, 0, "")])
        self.assertFalse(self.journal.exists())

    def test_migration_timeout_distinguishes_live_old_output_from_missing_or_new_owner(self):
        guard = self.prepare()
        guard.apply()
        cases = ((source("bt"), "playback_stayed_on_source"),
                 (None, "no_active_source"),
                 (source("other"), "playback_on_other_output"),
                 (replace(source("spare"), session_process_ids=(31,),
                          sessions=(AudioSessionEvidence("spare", 31, "s31", "i31"),)), "unowned_audio_process"))
        for fresh, reason in cases:
            with self.subTest(reason=reason):
                self.current = fresh
                with self.assertRaises(AppAudioRouteMigrationTimeout) as caught:
                    guard.wait_migrated(timeout=0)
                evidence = caught.exception.evidence
                self.assertEqual(evidence["reason"], reason)
                self.assertEqual(evidence["target_endpoint"], "spare")
                self.assertTrue(evidence["policy_confirmed"])
                self.assertFalse(evidence["playback_verified"])
                self.assertFalse(guard.migrated)
        self.current = source("bt")
        guard.restore()

    def test_same_source_sink_or_missing_spare_is_rejected_before_any_write(self):
        with self.assertRaisesRegex(ValueError, "must differ"):
            self.backend.prepare(self.current, "bt", "bt")
        with self.assertRaisesRegex(ValueError, "active"):
            self.backend.prepare(self.current, "absent", "bt")
        self.assertEqual(self.adapter.writes, [])

    def test_unknown_original_route_is_not_treated_as_default(self):
        self.adapter.routes[(30, 1)] = None
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.prepare()
        self.assertEqual(self.adapter.writes, [])
        self.assertEqual(self.adapter.closed, [(30, 100)])

    def test_other_app_system_and_multiprocess_spare_occupancy_fail_closed(self):
        for pid, single, system in ((40, True, False), (0, True, True), (30, False, False)):
            with self.subTest(pid=pid, single=single, system=system):
                self.adapter.owners = (("spare", pid, single, system),)
                with self.assertRaisesRegex(RuntimeError, "occupied"):
                    self.prepare()
        self.assertEqual(self.adapter.writes, [])

    def test_source_reuse_before_prepare_is_rejected(self):
        original = self.current
        self.current = source(created=200)
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.backend.prepare(original, "spare", "bt")
        self.assertEqual(self.adapter.writes, [])

    def test_creation_fence_before_apply_produces_no_mutation(self):
        guard = self.prepare()
        self.adapter.identities[30] = replace(self.adapter.identities[30], process_created_at_100ns=200)
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            guard.apply()
        self.assertEqual(self.adapter.writes, [])
        guard.restore()

    def test_external_route_change_before_apply_is_not_overwritten(self):
        guard = self.prepare()
        self.adapter.routes[(30, 1)] = "user-headset"
        with self.assertRaisesRegex(RuntimeError, "changed before"):
            guard.apply()
        self.assertEqual(self.adapter.writes, [])
        guard.restore()

    def test_second_role_failure_restores_first_and_clears_journal(self):
        guard = self.prepare()
        self.adapter.fail_role = 1
        with self.assertRaisesRegex(OSError, "Set failed"):
            guard.apply()
        self.assertEqual(self.adapter.routes[(30, 0)], "")
        self.assertFalse(self.journal.exists())

    def test_user_newer_route_is_preserved_during_restore(self):
        guard = self.prepare()
        guard.apply()
        self.adapter.routes[(30, 1)] = "user-headset"
        guard.restore()
        self.assertEqual(self.adapter.routes[(30, 1)], "user-headset")
        self.assertEqual(self.adapter.routes[(30, 0)], "")
        self.assertNotIn((30, 1, "old-role-one"), self.adapter.writes)

    def test_source_exit_keeps_durable_recovery_instead_of_forgetting_override(self):
        guard = self.prepare()
        guard.apply()
        self.adapter.identities.pop(30)
        with self.assertRaisesRegex(RuntimeError, "recovery remains pending"):
            guard.restore()
        saved = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(saved["stage"], "effective_recovery_required")
        self.assertEqual(saved["roles"][0]["original"], "")
        self.assertEqual(saved["roles"][1]["original"], "old-role-one")
        self.assertEqual(len(self.adapter.writes), 2)
        self.assertEqual(self.adapter.closed, [])

    def test_existing_recovery_record_blocks_new_persistent_route(self):
        self.journal.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "requires restoration"):
            self.prepare()
        self.assertEqual(self.adapter.writes, [])

    def test_restart_recovery_requires_exact_app_image_and_policy_readback(self):
        guard = self.prepare()
        guard.apply()
        self.adapter.identities.pop(30)
        with self.assertRaises(RuntimeError):
            guard.restore()
        restarted = source("spare", pid=40, created=200)
        self.current = restarted
        self.adapter.identities[40] = ProcessIdentity(40, 200, r"C:\Music\cloudmusic.exe")
        self.adapter.routes[(40, 0)] = self.adapter.routes[(30, 0)]
        self.adapter.routes[(40, 1)] = self.adapter.routes[(30, 1)]
        self.adapter.on_set = lambda pid, role, value: setattr(self, "current", source("bt", pid=40, created=200)) if value == _PREFIX + "bt" + _RENDER_SUFFIX else None
        self.close_backend(self.backend)
        reopened = WindowsAppAudioRoutes(devices=self.devices, adapter=self.adapter,
                                         resolver=lambda app: self.current, journal_path=self.journal)
        self.addCleanup(self.close_backend, reopened)
        self.assertTrue(reopened.recover_pending())
        self.assertEqual(self.adapter.routes[(40, 0)], "")
        self.assertEqual(self.adapter.routes[(40, 1)], "old-role-one")
        self.assertFalse(self.journal.exists())

    def test_restart_without_live_source_keeps_pending_journal_and_does_not_set(self):
        guard = self.prepare()
        guard.apply()
        self.current = None
        self.close_backend(self.backend)
        writes_before_recover = list(self.adapter.writes)
        reopened = WindowsAppAudioRoutes(devices=self.devices, adapter=self.adapter,
                                         resolver=lambda app: None, journal_path=self.journal)
        self.addCleanup(self.close_backend, reopened)
        with self.assertRaisesRegex(RuntimeError, "Restart the same"):
            reopened.recover_pending()
        self.assertEqual(self.adapter.writes, writes_before_recover)
        self.assertTrue(self.journal.exists())

    def test_restart_with_changed_policy_does_not_assume_persistence_key(self):
        guard = self.prepare()
        guard.apply()
        self.current = source("spare", pid=40, created=200)
        self.adapter.identities[40] = ProcessIdentity(40, 200, r"C:\Music\cloudmusic.exe")
        self.adapter.routes[(40, 0)], self.adapter.routes[(40, 1)] = "user-route", "user-route"
        self.close_backend(self.backend)
        writes_before_recover = list(self.adapter.writes)
        reopened = WindowsAppAudioRoutes(devices=self.devices, adapter=self.adapter,
                                         resolver=lambda app: self.current, journal_path=self.journal)
        self.addCleanup(self.close_backend, reopened)
        with self.assertRaisesRegex(RuntimeError, "does not expose"):
            reopened.recover_pending()
        self.assertEqual(self.adapter.writes, writes_before_recover)
        self.assertTrue(self.journal.exists())

    def test_invalid_recovery_record_cannot_invoke_windows_set(self):
        self.journal.write_text('{"schema":1,"source":{},"roles":[{}]}', encoding="utf-8")
        with self.assertRaises((ValueError, TypeError)):
            self.backend.recover_pending()
        self.assertEqual(self.adapter.writes, [])

    def test_failed_journal_write_prevents_native_set(self):
        guard = self.prepare()
        with patch.object(guard, "_save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                guard.apply()
        self.assertEqual(self.adapter.writes, [])
        guard.restore()

    def test_policy_readback_failure_rolls_back_successful_write(self):
        guard = self.prepare()
        original_get = self.adapter.get_route
        def read(pid, role):
            if self.adapter.writes and len(self.adapter.writes) == 1:
                return "unexpected-policy"
            return original_get(pid, role)
        with patch.object(self.adapter, "get_route", side_effect=read):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                guard.apply()
        # Uncertain readback is treated as a conflicting choice, never blindly
        # restored. This is distinct from a failed setter with original readback.
        self.assertEqual(len(self.adapter.writes), 1)

    def test_new_renderer_process_not_in_owned_tree_requires_new_transaction(self):
        guard = self.prepare()
        guard.apply()
        self.current = replace(source("spare"), session_process_ids=(31,),
                               sessions=(AudioSessionEvidence("spare", 31, "s31", "i31"),))
        with self.assertRaisesRegex(RuntimeError, "did not migrate"):
            guard.wait_migrated(timeout=0)
        guard.restore()

    def test_temporary_no_active_stream_during_rebind_waits_for_proven_migration(self):
        guard = self.prepare()
        guard.apply()
        migrated = source("spare")
        with patch.object(self.backend, "_resolver", side_effect=[None, migrated]), patch(
                "api.windows_app_audio_routes.time.sleep") as sleep:
            observed = guard.wait_migrated(timeout=1)
        self.assertEqual(observed, migrated)
        sleep.assert_called_once()
        guard.restore()

    def test_paused_no_stream_remains_bounded_and_does_not_widen_route(self):
        guard = self.prepare()
        guard.apply()
        self.current = None
        with self.assertRaisesRegex(RuntimeError, "did not migrate"):
            guard.wait_migrated(timeout=0)
        self.assertFalse(guard.migrated)
        self.assertEqual(len(self.adapter.writes), 2)
        self.current = source("bt")
        guard.restore()

    def test_positive_different_music_identity_during_rebind_fails_immediately(self):
        guard = self.prepare()
        guard.apply()
        self.current = source("spare", pid=40, created=200)
        with self.assertRaisesRegex(RuntimeError, "source changed"):
            guard.wait_migrated(timeout=1)
        self.current = source("bt")
        guard.restore()

    def test_empty_policy_restore_rebinds_live_stream_before_clearing(self):
        self.adapter.routes[(30, 1)] = ""
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        guard.restore()
        expected = _PREFIX + "bt" + _RENDER_SUFFIX
        self.assertIn((30, 0, expected), self.adapter.writes)
        self.assertIn((30, 1, expected), self.adapter.writes)
        self.assertEqual(self.current.sessions[0].endpoint_id, "bt")
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})

    def test_mixed_original_roles_restore_binding_role_before_effective_wait(self):
        original_multimedia = _PREFIX + "bt" + _RENDER_SUFFIX
        self.adapter.routes[(30, 1)] = original_multimedia
        # A realistic media stream responds only to multimedia-role Set;
        # clearing its policy does not necessarily move the existing stream.
        def media_rebind(pid, role, value):
            if role == 1 and value == original_multimedia:
                self.current = source("bt")
        self.adapter.on_set = media_rebind
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        guard.restore(timeout=0)
        self.assertEqual(self.adapter.writes[2:], [
            (30, 1, original_multimedia), (30, 0, original_multimedia), (30, 0, "")])
        self.assertEqual(self.current.sessions[0].endpoint_id, "bt")
        self.assertFalse(self.journal.exists())

    def test_policy_clear_rebind_requires_fresh_effective_output_proof(self):
        self.adapter.routes[(30, 1)] = ""
        original_callback = self.adapter.on_set
        def clear_rebind(pid, role, value):
            original_callback(pid, role, value)
            if value == "":
                self.current = source("spare")
        self.adapter.on_set = clear_rebind
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        with self.assertRaisesRegex(RuntimeError, "has not been restored"):
            guard.restore(timeout=0)
        self.assertFalse(guard._closed)
        self.assertTrue(self.journal.exists())
        self.assertFalse(any(role.restored for role in guard._roles))
        self.adapter.on_set = original_callback
        guard.restore(timeout=0)
        self.assertFalse(self.journal.exists())

    def pause_source(self, *, single=False):
        self.current = None
        self.adapter.owners = ()
        self.adapter.states = (("spare", 30, single, False, 0),)
        self.adapter.on_set = None

    def paused_restore(self):
        guard = self.prepare()
        guard.apply()
        self.pause_source()
        guard.restore(timeout=0)
        return guard

    def test_pause_stop_restores_original_policies_and_releases_ownership(self):
        guard = self.paused_restore()
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): "old-role-one"})
        self.assertEqual(guard.restoration_state, "policy_restored_no_active_stream")
        self.assertTrue(guard._closed)
        self.assertEqual(self.adapter.closed, [(30, 100)])
        self.assertIsNone(self.backend._active)
        self.assertIsNone(self.backend._lease)
        saved = self.backend.pending_recovery()
        self.assertEqual(saved["stage"], "resume_verification_pending")
        self.assertTrue(all(role["restored"] for role in saved["roles"]))

    def test_paused_deferred_recovery_is_read_only_releases_lease_and_does_not_block(self):
        self.paused_restore()
        before = list(self.adapter.writes)
        self.assertFalse(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertIsNone(self.backend._active)
        self.assertIsNone(self.backend._lease)
        self.assertEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")

    def test_paused_close_leaves_only_observation_not_owned_route_changes(self):
        guard = self.prepare()
        guard.apply()
        self.pause_source()
        self.backend.close(timeout=0)
        self.assertTrue(guard._closed)
        self.assertIsNone(self.backend._lease)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): "old-role-one"})
        self.assertEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")

    def test_resume_at_original_endpoint_clears_observation_without_writes(self):
        self.paused_restore()
        self.current = source("bt")
        self.adapter.states = (("bt", 30, True, False, 1),)
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertFalse(self.journal.exists())
        self.assertIsNone(self.backend._lease)

    def test_resume_still_on_spare_repairs_actual_stream_with_original_policy_preserved(self):
        self.paused_restore()
        self.current = source("spare")
        self.adapter.states = (("spare", 30, True, False, 1),)
        self.adapter.on_set = lambda pid, role, value: setattr(self, "current", source("bt")) if value == _PREFIX + "bt" + _RENDER_SUFFIX else None
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.current.sessions[0].endpoint_id, "bt")
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): "old-role-one"})
        self.assertFalse(self.journal.exists())

    def test_resume_external_new_policy_is_preserved_even_when_same_as_former_spare(self):
        self.paused_restore()
        owned = _PREFIX + "spare" + _RENDER_SUFFIX
        self.adapter.routes[(30, 1)] = owned
        self.current = source("spare")
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertEqual(self.adapter.routes[(30, 1)], owned)
        self.assertFalse(self.journal.exists())

    def test_none_source_requires_real_idle_session_not_absence_or_active_ambiguity(self):
        for states in ((), (("spare", 30, True, False, 1),),
                       (("spare", 30, False, False, 0), ("bt", 99, False, False, 1))):
            with self.subTest(states=states):
                guard = self.prepare()
                guard.apply()
                self.pause_source()
                self.adapter.states = states
                with self.assertRaisesRegex(RuntimeError, "has not been restored"):
                    guard.restore(timeout=0)
                self.assertFalse(guard._closed)
                self.assertNotEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")
                self.adapter.states = (("spare", 30, False, False, 0),)
                guard.restore(timeout=0)
                self.current = source("bt")
                self.backend.recover_pending()

    def test_unknown_deferred_state_cannot_write_or_clear_observation(self):
        self.paused_restore()
        self.adapter.states = ()
        before = list(self.adapter.writes)
        with self.assertRaisesRegex(RuntimeError, "no verified inactive or active stream"):
            self.backend.recover_pending()
        self.assertEqual(self.adapter.writes, before)
        self.assertEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")
        self.assertIsNone(self.backend._lease)

    def test_deferred_new_audio_child_or_mixed_outputs_cannot_authorize_repair_writes(self):
        self.paused_restore()
        for fresh in (
                replace(source("spare"), session_process_ids=(31,), sessions=(AudioSessionEvidence("spare", 31, "s31", "i31"),)),
                replace(source("spare"), sessions=(*source("spare").sessions, AudioSessionEvidence("bt", 30, "s30-other", "i30-other")))):
            with self.subTest(source=fresh):
                self.current = fresh
                self.adapter.identities[31] = ProcessIdentity(31, 101, r"C:\Music\renderer.exe")
                before = list(self.adapter.writes)
                with self.assertRaisesRegex(RuntimeError, "changed audio owners or ambiguous"):
                    self.backend.recover_pending()
                self.assertEqual(self.adapter.writes, before)
                self.assertEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")
                self.assertIsNone(self.backend._lease)

    def test_deferred_paused_pid_reuse_retires_only_old_returned_policy_observation(self):
        self.paused_restore()
        self.adapter.identities[30] = ProcessIdentity(30, 200, r"C:\Music\cloudmusic.exe")
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertIsNone(self.backend.pending_recovery())
        self.assertIsNone(self.backend._lease)

    def test_returned_policy_observation_after_music_process_exit_never_blocks_another_output(self):
        self.paused_restore()
        self.adapter.identities.pop(30)
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertIsNone(self.backend.pending_recovery())
        self.assertIsNone(self.backend._lease)

    def test_active_restarted_process_does_not_inherit_old_observation_write_authority(self):
        self.adapter.routes[(30, 1)] = ""
        # The old initialized stream was off the global default before casting.
        # On restart a fresh stream normally follows the unchanged default.
        self.default_endpoint = "spare"
        self.paused_restore()
        self.current = source("spare", pid=40, created=200)
        self.adapter.identities[40] = ProcessIdentity(40, 200, r"C:\Music\cloudmusic.exe")
        self.adapter.routes.update({(40, 0): "", (40, 1): ""})
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertEqual(self.current.sessions[0].endpoint_id, "spare")
        self.assertEqual(self.adapter.routes[(40, 0)], "")
        self.assertEqual(self.adapter.routes[(40, 1)], "")
        self.assertFalse(self.journal.exists())
        self.assertIsNone(self.backend._lease)

    def test_source_exit_during_pause_keeps_pending_owned_changes(self):
        guard = self.prepare()
        guard.apply()
        self.pause_source()
        self.adapter.identities.pop(30)
        with self.assertRaisesRegex(RuntimeError, "recovery remains pending"):
            guard.restore(timeout=0)
        self.assertFalse(guard._closed)
        self.assertNotEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")

    def test_paused_new_group_can_supersede_observation_for_a_different_verified_source(self):
        self.paused_restore()
        new_source = replace(source("bt", pid=40, created=200), source_app_id="player.exe", process_path=r"C:\Music\player.exe")
        self.adapter.identities[40] = ProcessIdentity(40, 200, r"C:\Music\player.exe")
        self.adapter.routes.update({(40, 0): "", (40, 1): ""})
        self.backend._resolver = lambda app: new_source if app == "player.exe" else None
        new_guard = self.backend.prepare(new_source, "spare", "bt")
        self.assertFalse(self.journal.exists())
        self.assertEqual(new_guard.source, new_source)
        self.assertFalse(new_guard._closed)
        new_guard.restore(timeout=0)

    def test_prepare_refreshes_effective_source_after_consuming_resume_observation(self):
        self.paused_restore()
        self.current = source("spare")
        prior_source = self.current
        self.adapter.on_set = lambda pid, role, value: setattr(self, "current", source("bt")) if value == _PREFIX + "bt" + _RENDER_SUFFIX else None
        guard = self.backend.prepare(prior_source, "spare", "bt")
        self.assertTrue(all(role.original_effective_endpoint == "bt" for role in guard._roles))
        guard.restore()

    def test_forged_deferred_stage_cannot_hide_unrestored_owned_policy(self):
        guard = self.prepare()
        guard.apply()
        original = json.loads(self.journal.read_text(encoding="utf-8"))
        edited = dict(original, stage="resume_verification_pending")
        self.journal.write_text(json.dumps(edited), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "still contains owned route writes"):
            self.backend.pending_recovery()
        self.journal.write_text(json.dumps(original), encoding="utf-8")
        guard.restore()

    def test_external_global_default_change_retires_default_following_observation_without_writes(self):
        self.adapter.routes[(30, 1)] = ""
        self.paused_restore()
        self.default_endpoint = "spare"
        self.current = source("spare")
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        self.assertFalse(self.journal.exists())
        self.assertIsNone(self.backend._lease)

    def test_global_default_change_does_not_erase_explicit_per_app_policy_restoration(self):
        self.paused_restore()
        self.default_endpoint = "spare"
        before = list(self.adapter.writes)
        self.assertFalse(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")

    def test_old_default_following_observation_without_baseline_cannot_guess_repair(self):
        self.adapter.routes[(30, 1)] = ""
        self.paused_restore()
        saved = json.loads(self.journal.read_text(encoding="utf-8"))
        saved.pop("original_default_endpoint")
        self.journal.write_text(json.dumps(saved), encoding="utf-8")
        self.current = source("spare")
        before = list(self.adapter.writes)
        with self.assertRaisesRegex(RuntimeError, "no original Windows default"):
            self.backend.recover_pending()
        self.assertEqual(self.adapter.writes, before)
        self.assertTrue(self.journal.exists())
        self.assertIsNone(self.backend._lease)

    def test_explicit_new_output_retires_only_returned_policy_observation(self):
        self.paused_restore()
        self.adapter.routes[(30, 1)] = "user-output"
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.retire_resume_observation())
        self.assertEqual(self.adapter.writes, before)
        self.assertEqual(self.adapter.routes[(30, 1)], "user-output")
        self.assertFalse(self.journal.exists())
        self.assertIsNone(self.backend._lease)
        self.assertFalse(self.backend.retire_resume_observation())

    def test_explicit_output_cannot_retire_live_guard_or_unreturned_write_journal(self):
        guard = self.prepare()
        guard.apply()
        with self.assertRaisesRegex(RuntimeError, "still requires restoration"):
            self.backend.retire_resume_observation()
        self.backend.close(timeout=0)
        # Make a standalone nondeferred recovery record without an active guard.
        self.current = source()
        guard = self.prepare()
        guard.apply()
        self.adapter.identities.pop(30)
        self.close_backend(self.backend)
        before = list(self.adapter.writes)
        with self.assertRaisesRegex(RuntimeError, "still requires restoration"):
            self.backend.retire_resume_observation()
        self.assertEqual(self.adapter.writes, before)
        self.assertTrue(self.journal.exists())
        self.assertIsNone(self.backend._lease)

    def test_failed_live_restore_keeps_explicit_intent_journal_without_claiming_success(self):
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        self.adapter.on_set = None
        with self.assertRaisesRegex(RuntimeError, "has not been restored"):
            guard.restore(timeout=0)
        saved = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(saved["stage"], "effective_recovery_required")
        self.assertTrue(any(role["restoring_route"] for role in saved["roles"]))
        self.assertFalse(guard._closed)
        # A failed playback proof retains recovery intent, not a persisted pin
        # that would make future Windows output selections ineffective.
        self.assertEqual(self.adapter.routes[(30, 0)], "")

    def test_disconnect_returns_only_owned_policies_to_current_default(self):
        self.adapter.routes[(30, 1)] = _PREFIX + "bt" + _RENDER_SUFFIX
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        self.devices.list_outputs = lambda: (SimpleNamespace(id="spare", is_default=True),)
        self.adapter.on_set = lambda _pid, _role, _value: setattr(self, "current", source("spare"))
        before = len(self.adapter.writes)
        guard.restore(timeout=0)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        self.assertTrue(all(value != _PREFIX + "bt" + _RENDER_SUFFIX
                            for _pid, _role, value in self.adapter.writes[before:]))
        self.assertEqual(guard.restoration_state, "active_output_verified")
        self.assertFalse(self.journal.exists())

    def test_default_change_returns_default_following_roles_without_forcing_old_output(self):
        self.adapter.routes[(30, 1)] = ""
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        self.default_endpoint = "spare"
        self.adapter.on_set = lambda _pid, _role, _value: setattr(self, "current", source("spare"))
        before = len(self.adapter.writes)
        guard.restore(timeout=0)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        self.assertTrue(all(value != _PREFIX + "bt" + _RENDER_SUFFIX
                            for _pid, _role, value in self.adapter.writes[before:]))
        self.assertFalse(self.journal.exists())

    def test_disconnect_during_recovery_without_active_stream_clears_owned_pins_and_defers_proof(self):
        self.adapter.routes[(30, 1)] = ""
        guard = self.prepare()
        guard.apply()
        saved = self.journal.read_text(encoding="utf-8")
        self.close_backend(self.backend)
        self.journal.write_text(saved, encoding="utf-8")
        self.current = None
        self.adapter.owners, self.adapter.states = (), ()
        self.adapter.routes = {(30, 0): guard._roles[0].owned_route, (30, 1): guard._roles[1].owned_route}
        self.devices.list_outputs = lambda: (SimpleNamespace(id="spare", is_default=True),)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        pending = self.backend.pending_recovery()
        self.assertEqual(pending["stage"], "resume_verification_pending")
        self.assertTrue(all(role["restored"] for role in pending["roles"]))
        self.assertTrue(all(role["recovery_endpoint"] == "spare" for role in pending["roles"]))
        self.current = source("spare")
        self.adapter.states = (("spare", 30, True, False, 1),)
        before = list(self.adapter.writes)
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertFalse(self.journal.exists())

    def test_disconnect_keeps_newer_external_application_policy_untouched(self):
        self.adapter.routes[(30, 1)] = ""
        guard = self.prepare()
        guard.apply()
        newer = _PREFIX + "user-choice" + _RENDER_SUFFIX
        self.adapter.routes[(30, 0)] = newer
        self.devices.list_outputs = lambda: (SimpleNamespace(id="spare", is_default=True),)
        self.current = source("spare")
        guard.restore(timeout=0)
        self.assertEqual(self.adapter.routes[(30, 0)], newer)
        self.assertEqual(self.adapter.routes[(30, 1)], "")
        self.assertEqual(guard.restoration_state, "external_output_preserved")

    def test_failed_disconnect_playback_proof_still_clears_temporary_policies(self):
        self.adapter.routes[(30, 1)] = ""
        guard = self.prepare()
        guard.apply()
        self.current = source("wrong")
        self.adapter.on_set = None
        self.devices.list_outputs = lambda: (SimpleNamespace(id="spare", is_default=True),)
        with self.assertRaisesRegex(RuntimeError, "has not been restored"):
            guard.restore(timeout=0)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        self.assertEqual(self.backend.pending_recovery()["stage"], "effective_recovery_required")
        self.assertFalse(guard._closed)
        self.current = source("spare")
        guard.restore(timeout=0)
        self.assertFalse(self.journal.exists())

    def test_no_outputs_clears_owned_disconnected_pins_then_verifies_new_default_on_reconnect(self):
        self.adapter.routes[(30, 1)] = _PREFIX + "bt" + _RENDER_SUFFIX
        guard = self.prepare()
        guard.apply()
        self.current = None
        self.adapter.owners, self.adapter.states = (), ()
        self.devices.list_outputs = lambda: ()
        guard.restore(timeout=0)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        self.assertEqual(guard.restoration_state, "policy_restored_no_active_stream")
        self.assertEqual(self.backend.pending_recovery()["stage"], "resume_verification_pending")
        self.devices.list_outputs = lambda: (SimpleNamespace(id="spare", is_default=True),)
        self.current = source("spare")
        self.adapter.states = (("spare", 30, True, False, 1),)
        self.adapter.on_set = None
        self.assertTrue(self.backend.recover_pending())
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})

    def test_disconnect_recovery_record_cannot_replace_unrelated_saved_application_policy(self):
        self.adapter.routes[(30, 1)] = ""
        guard = self.prepare()
        guard.apply()
        self.devices.list_outputs = lambda: (SimpleNamespace(id="spare", is_default=True),)
        self.current = source("wrong")
        self.adapter.on_set = None
        with self.assertRaises(RuntimeError):
            guard.restore(timeout=0)
        saved = self.journal.read_text(encoding="utf-8")
        value = json.loads(saved)
        value["roles"][0]["original"] = "unrelated-user-pin"
        self.journal.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exceeds its original ownership"):
            self.backend.pending_recovery()
        self.journal.write_text(saved, encoding="utf-8")
        self.current = source("spare")
        guard.restore(timeout=0)

    def test_explicit_default_follow_commit_clears_only_proven_music_roles(self):
        self.adapter.routes[(30, 0)] = _PREFIX + "bt" + _RENDER_SUFFIX
        self.adapter.routes[(30, 1)] = _PREFIX + "bt" + _RENDER_SUFFIX
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        guard.wait_migrated(timeout=0)
        self.default_endpoint = "spare"
        guard.follow_system_default(timeout=0)
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        self.assertEqual(self.current.sessions[0].endpoint_id, "spare")
        self.assertEqual(guard.restoration_state, "active_output_verified")
        self.assertFalse(self.journal.exists())
        self.assertIsNone(self.backend._active)

    def test_explicit_default_repair_accepts_shared_target_without_touching_other_application(self):
        self.default_endpoint = "spare"
        self.adapter.identities[99] = ProcessIdentity(99, 200, r"C:\Meeting\meeting.exe")
        self.adapter.routes.update({(99, 0): "meeting-role-zero", (99, 1): "meeting-role-one"})
        self.adapter.owners = (("bt", 30, True, False), ("spare", 99, True, False))
        guard = self.backend.prepare_default_follow(self.current)
        self.assertEqual(self.adapter.writes, [])
        guard.apply()
        self.current = source("spare")
        guard.wait_migrated(timeout=0)
        guard.follow_system_default(timeout=0)
        self.assertEqual(self.adapter.routes[(30, 0)], "")
        self.assertEqual(self.adapter.routes[(30, 1)], "")
        self.assertEqual(self.adapter.routes[(99, 0)], "meeting-role-zero")
        self.assertEqual(self.adapter.routes[(99, 1)], "meeting-role-one")
        self.assertTrue(all(pid == 30 for pid, _role, _value in self.adapter.writes))
        self.assertFalse(self.journal.exists())

    def test_ordinary_group_prepare_still_rejects_shared_spare_even_when_default(self):
        self.default_endpoint = "spare"
        self.adapter.owners = (("bt", 30, True, False), ("spare", 99, True, False))
        with self.assertRaisesRegex(RuntimeError, "occupied"):
            self.prepare()
        self.assertEqual(self.adapter.writes, [])
        self.assertFalse(self.journal.exists())
        self.assertIsNone(self.backend._active)

    def test_shared_default_repair_refuses_changed_default_before_apply_without_policy_writes(self):
        self.default_endpoint = "spare"
        self.adapter.owners = (("bt", 30, True, False), ("spare", 99, True, False))
        guard = self.backend.prepare_default_follow(self.current)
        self.default_endpoint = "bt"
        with self.assertRaisesRegex(RuntimeError, "default changed"):
            guard.apply()
        self.assertEqual(self.adapter.writes, [])
        guard.restore(timeout=0)

    def test_shared_default_repair_checks_default_between_owned_role_writes(self):
        self.default_endpoint = "spare"
        self.adapter.routes[(30, 0)] = _PREFIX + "bt" + _RENDER_SUFFIX
        self.adapter.routes[(30, 1)] = _PREFIX + "bt" + _RENDER_SUFFIX
        guard = self.backend.prepare_default_follow(self.current)
        original_callback = self.adapter.on_set
        def switch_default(pid, role, value):
            original_callback(pid, role, value)
            if value == _PREFIX + "spare" + _RENDER_SUFFIX:
                self.default_endpoint = "bt"
        self.adapter.on_set = switch_default
        with self.assertRaisesRegex(RuntimeError, "default changed"):
            guard.apply()
        self.assertEqual(self.adapter.writes.count((30, 0, _PREFIX + "spare" + _RENDER_SUFFIX)), 1)
        self.assertNotIn((30, 1, _PREFIX + "spare" + _RENDER_SUFFIX), self.adapter.writes)
        self.assertEqual(self.adapter.routes[(30, 0)], _PREFIX + "bt" + _RENDER_SUFFIX)
        self.assertEqual(self.adapter.routes[(30, 1)], _PREFIX + "bt" + _RENDER_SUFFIX)
        self.assertFalse(self.journal.exists())

    def test_shared_default_repair_refuses_changed_default_during_migration(self):
        self.default_endpoint = "spare"
        guard = self.backend.prepare_default_follow(self.current)
        guard.apply()
        self.current = source("spare")
        self.default_endpoint = "bt"
        before = list(self.adapter.writes)
        with self.assertRaisesRegex(RuntimeError, "default changed"):
            guard.wait_migrated(timeout=0)
        self.assertEqual(self.adapter.writes, before)
        guard.restore(timeout=0)

    def test_default_repair_requires_no_spare_when_music_already_plays_on_only_output(self):
        self.adapter.routes[(30, 1)] = ""
        self.devices.list_outputs = lambda: (SimpleNamespace(id="bt", is_default=True),)
        self.adapter.owners = (("bt", 30, True, False), ("bt", 99, True, False))
        guard = self.backend.prepare_default_follow(self.current)
        self.assertEqual(guard.spare_endpoint, guard.local_endpoint)
        guard.apply()
        guard.wait_migrated(timeout=0)
        guard.follow_system_default(timeout=0)
        self.assertEqual(self.adapter.routes[(30, 0)], "")
        self.assertEqual(self.adapter.routes[(30, 1)], "")
        self.assertFalse(self.journal.exists())

    def test_default_follow_requires_migration_and_exact_current_default(self):
        guard = self.prepare()
        guard.apply()
        before = list(self.adapter.writes)
        with self.assertRaisesRegex(RuntimeError, "Verified migration"):
            guard.follow_system_default(timeout=0)
        self.current = source("spare")
        guard.wait_migrated(timeout=0)
        with self.assertRaisesRegex(RuntimeError, "Verified migration"):
            guard.follow_system_default(timeout=0)
        self.assertEqual(self.adapter.writes, before)
        guard.restore(timeout=0)

    def test_failed_default_follow_commit_rolls_back_owned_empty_values_and_proves_previous_output(self):
        self.adapter.routes[(30, 0)] = _PREFIX + "bt" + _RENDER_SUFFIX
        self.adapter.routes[(30, 1)] = _PREFIX + "bt" + _RENDER_SUFFIX
        self.default_endpoint = "spare"
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        guard.wait_migrated(timeout=0)
        original_callback = self.adapter.on_set
        def clear_rebind(pid, role, value):
            original_callback(pid, role, value)
            if value == "":
                self.current = source("wrong")
        self.adapter.on_set = clear_rebind
        with self.assertRaisesRegex(RuntimeError, "has not been restored"):
            guard.follow_system_default(timeout=0)
        self.assertEqual(self.adapter.routes, {(30, 0): _PREFIX + "bt" + _RENDER_SUFFIX,
                                              (30, 1): _PREFIX + "bt" + _RENDER_SUFFIX})
        self.assertEqual(self.current.sessions[0].endpoint_id, "bt")
        self.assertFalse(self.journal.exists())

    def test_default_follow_never_overwrites_newer_user_policy_during_commit(self):
        self.default_endpoint = "spare"
        self.adapter.routes[(30, 1)] = ""
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        guard.wait_migrated(timeout=0)
        newer = _PREFIX + "user-choice" + _RENDER_SUFFIX
        original_callback = self.adapter.on_set
        def external_change(pid, role, value):
            original_callback(pid, role, value)
            if role == 0 and value == "":
                self.adapter.routes[(30, 1)] = newer
        self.adapter.on_set = external_change
        with self.assertRaisesRegex(RuntimeError, "changed during"):
            guard.follow_system_default(timeout=0)
        self.assertEqual(self.adapter.routes[(30, 1)], newer)
        self.assertEqual(guard.restoration_state, "external_output_preserved")

    def test_default_follow_paused_commit_keeps_observation_for_committed_default_policy(self):
        self.adapter.routes[(30, 0)] = _PREFIX + "bt" + _RENDER_SUFFIX
        self.adapter.routes[(30, 1)] = _PREFIX + "bt" + _RENDER_SUFFIX
        self.default_endpoint = "spare"
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        guard.wait_migrated(timeout=0)
        self.pause_source()
        guard.follow_system_default(timeout=0)
        self.assertEqual(guard.restoration_state, "policy_restored_no_active_stream")
        pending = self.backend.pending_recovery()
        self.assertEqual(pending["stage"], "resume_verification_pending")
        self.assertTrue(all(role["default_clear_intent"] and role["recovery_policy"] == ""
                            for role in pending["roles"]))
        before = list(self.adapter.writes)
        self.assertFalse(self.backend.recover_pending())
        self.current = source("spare")
        self.assertTrue(self.backend.recover_pending())
        self.assertEqual(self.adapter.writes, before)
        self.assertFalse(self.journal.exists())

    def test_explicit_prior_source_playback_recovery_preserves_empty_policy(self):
        self.adapter.routes[(30, 1)] = ""
        original = source("bt")
        self.current = source("spare")
        self.backend.restore_source_playback(original, timeout=0)
        self.assertEqual(self.current.sessions[0].endpoint_id, "bt")
        self.assertEqual(self.adapter.routes, {(30, 0): "", (30, 1): ""})
        self.assertFalse(self.journal.exists())

    def test_playback_recovery_default_inventory_failure_cannot_acquire_journal_lease(self):
        original = source("bt")
        self.current = source("spare")
        self.default_endpoint = "unknown"
        with self.assertRaisesRegex(RuntimeError, "default output is not uniquely"):
            self.backend.restore_source_playback(original, timeout=0)
        self.assertEqual(self.adapter.writes, [])
        self.assertIsNone(self.backend._lease)
        self.assertFalse(self.journal.exists())

    def test_roles_are_separate_and_communications_never_written(self):
        guard = self.prepare()
        guard.apply()
        self.assertEqual([role for pid, role, route in self.adapter.writes], [0, 1])
        self.assertTrue(all(route == _PREFIX + "spare" + _RENDER_SUFFIX for pid, role, route in self.adapter.writes))
        guard.restore()

    def test_two_backend_instances_cannot_prepare_the_same_journal(self):
        guard = self.prepare()
        second = WindowsAppAudioRoutes(devices=self.devices, adapter=self.adapter,
                                        resolver=lambda app: self.current, journal_path=self.journal)
        self.addCleanup(self.close_backend, second)
        with self.assertRaisesRegex(RuntimeError, "Another process owns"):
            second.prepare(self.current, "spare", "bt")
        guard.restore()
        second.prepare(self.current, "spare", "bt").restore()

    def test_recovery_query_preserves_read_only_prepared_guard_lease(self):
        guard = self.prepare()
        self.assertFalse(self.backend.recover_pending())
        second = WindowsAppAudioRoutes(devices=self.devices, adapter=self.adapter,
                                        resolver=lambda app: self.current, journal_path=self.journal)
        self.addCleanup(self.close_backend, second)
        with self.assertRaisesRegex(RuntimeError, "Another process owns"):
            second.prepare(self.current, "spare", "bt")
        self.assertEqual(self.adapter.writes, [])
        guard.restore()

    def test_same_backend_recovery_rechecks_restarted_process_policy(self):
        guard = self.prepare()
        guard.apply()
        self.adapter.identities.pop(30)
        self.current = source("spare", pid=40, created=200)
        self.adapter.identities[40] = ProcessIdentity(40, 200, r"C:\Music\cloudmusic.exe")
        self.adapter.routes[(40, 0)] = self.adapter.routes[(30, 0)]
        self.adapter.routes[(40, 1)] = self.adapter.routes[(30, 1)]
        self.adapter.on_set = lambda pid, role, value: setattr(self, "current", source("bt", pid=40, created=200)) if value == _PREFIX + "bt" + _RENDER_SUFFIX else None
        self.assertTrue(self.backend.recover_pending())
        self.assertTrue(guard._closed)
        self.assertEqual(self.adapter.routes[(40, 0)], "")
        self.assertEqual(self.adapter.routes[(40, 1)], "old-role-one")
        self.assertFalse(self.journal.exists())

    def test_recovery_rejects_collapsing_two_old_owners_into_one_restarted_pid(self):
        self.adapter.identities[31] = ProcessIdentity(31, 101, r"C:\Music\cloudmusic.exe")
        self.adapter.routes.update({(31, 0): "", (31, 1): "renderer-role"})
        self.current = replace(self.current, session_process_ids=(30, 31), sessions=(
            *self.current.sessions, AudioSessionEvidence("bt", 31, "s31", "i31")))
        guard = self.prepare()
        guard.apply()
        self.adapter.identities.pop(30)
        self.adapter.identities.pop(31)
        self.current = source("spare", pid=40, created=200)
        self.adapter.identities[40] = ProcessIdentity(40, 200, r"C:\Music\cloudmusic.exe")
        owned = _PREFIX + "spare" + _RENDER_SUFFIX
        self.adapter.routes.update({(40, 0): owned, (40, 1): owned})
        prior_writes = list(self.adapter.writes)
        with self.assertRaisesRegex(RuntimeError, "merges distinct"):
            self.backend.recover_pending()
        self.assertEqual(self.adapter.writes, prior_writes)
        self.assertTrue(self.journal.exists())

    def test_restore_two_effective_checks_share_one_timeout_budget(self):
        guard = self.prepare()
        guard.apply()
        with patch("api.windows_app_audio_routes.time.monotonic", side_effect=[10, 11, 14]), patch.object(
                guard, "_wait_effective") as wait:
            guard.restore(timeout=5)
        self.assertEqual([args.args for args in wait.call_args_list], [("bt", 4), ("bt", 1)])

    def test_process_limit_is_checked_before_native_handles_or_routes(self):
        self.current = replace(self.current, session_process_ids=tuple(range(30, 95)))
        with patch.object(self.adapter, "hold_process", wraps=self.adapter.hold_process) as hold:
            with self.assertRaisesRegex(ValueError, "at most 64"):
                self.prepare()
        hold.assert_not_called()
        self.assertEqual(self.adapter.writes, [])

    def test_oversized_source_record_prevents_any_native_set(self):
        self.current = replace(self.current, session_identifiers=("歌词" * 180000,))
        guard = self.prepare()
        with self.assertRaisesRegex(ValueError, "exceeds its limit"):
            guard.apply()
        self.assertEqual(self.adapter.writes, [])
        self.assertFalse(self.journal.exists())
        guard.restore()

    def test_journal_size_reserves_future_restoring_policies_before_first_set(self):
        self.current = replace(self.current, session_identifiers=("x" * 950000,))
        self.adapter.routes.update({(30, 0): "a" * 32700, (30, 1): "b" * 32700})
        guard = self.prepare()
        with self.assertRaisesRegex(ValueError, "exceeds its limit"):
            guard.apply()
        self.assertEqual(self.adapter.writes, [])
        self.assertFalse(self.journal.exists())
        guard.restore()

    def test_cancelled_wait_never_marks_route_migrated_and_can_restore(self):
        guard = self.prepare()
        guard.apply()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            guard.wait_migrated(cancelled=lambda: True)
        self.assertFalse(guard.migrated)
        guard.restore()

    def test_health_check_detects_spare_occupation_and_policy_conflict(self):
        guard = self.prepare()
        guard.apply()
        guard.check()
        self.adapter.owners = (("spare", 40, True, False),)
        with self.assertRaisesRegex(RuntimeError, "occupied"):
            guard.check()
        self.adapter.owners = ()
        self.adapter.routes[(30, 1)] = "new-user-route"
        with self.assertRaisesRegex(RuntimeError, "output changed"):
            guard.check()
        guard.restore()

    def test_full_endpoint_inventory_includes_ambiguous_and_system_occupants(self):
        self.assertTrue(self.backend.endpoint_is_unoccupied("spare", self.current))
        self.adapter.owners = (("spare", 0, False, True),)
        self.assertFalse(self.backend.endpoint_is_unoccupied("spare", self.current))

    def test_exact_owned_capture_session_is_allowed_only_after_reverification(self):
        helper = ProcessIdentity(50, 200, r"C:\GluSpot\GluSpotAirPlay.exe")
        self.adapter.identities[50] = helper
        self.adapter.owners = (("bt", 30, True, False), ("bt", 50, True, False))
        self.assertFalse(self.backend.endpoint_is_unoccupied("bt", self.current))
        self.assertTrue(self.backend.endpoint_is_unoccupied("bt", self.current, owned_process=helper))
        for current in (replace(helper, process_created_at_100ns=201),
                        replace(helper, process_path=r"C:\Other\GluSpotAirPlay.exe"), None):
            with self.subTest(current=current):
                self.adapter.identities[50] = current
                self.assertFalse(self.backend.endpoint_is_unoccupied("bt", self.current, owned_process=helper))

    def test_owned_capture_identity_never_allows_system_or_ambiguous_owners(self):
        helper = ProcessIdentity(50, 200, r"C:\GluSpot\GluSpotAirPlay.exe")
        self.adapter.identities[50] = helper
        for single, system in ((False, False), (True, True), (False, True)):
            with self.subTest(single=single, system=system):
                self.adapter.owners = (("bt", 50, single, system),)
                self.assertFalse(self.backend.endpoint_is_unoccupied("bt", self.current, owned_process=helper))
        self.adapter.owners = (("bt", 51, True, False),)
        self.adapter.identities[51] = replace(helper, process_id=51)
        self.assertFalse(self.backend.endpoint_is_unoccupied("bt", self.current, owned_process=helper))

    def test_spare_guard_accepts_exact_current_capture_child_but_rejects_stale_authority(self):
        guard = self.prepare()
        guard.apply()
        self.current = source("spare")
        self.adapter.owners = (("spare", 30, True, False),)
        guard.wait_migrated(timeout=0)
        helper = ProcessIdentity(50, 200, r"C:\GluSpot\GluSpotAirPlay.exe")
        self.adapter.identities[50] = helper
        self.adapter.owners += (("spare", 50, True, False),)
        with self.assertRaisesRegex(RuntimeError, "occupied"):
            guard.check()
        guard.check(owned_process=helper)
        self.adapter.identities[50] = replace(helper, process_created_at_100ns=201)
        with self.assertRaisesRegex(RuntimeError, "occupied"):
            guard.check(owned_process=helper)
        self.adapter.owners = (("spare", 30, True, False),)
        guard.restore()

    def test_recovery_rejects_duplicate_roles_bool_role_and_unrelated_restore_route(self):
        guard = self.prepare()
        guard.apply()
        original = json.loads(self.journal.read_text(encoding="utf-8"))
        for edit in (lambda data: data["roles"].append(dict(data["roles"][0])),
                     lambda data: data["roles"][0].update(role=True),
                     lambda data: data["roles"][0].update(restoring_route="user-new-route")):
            with self.subTest(edit=edit):
                data = json.loads(json.dumps(original))
                edit(data)
                self.journal.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.backend.pending_recovery()
        self.journal.write_text(json.dumps(original), encoding="utf-8")
        guard.restore()


class NativeAppRouteCallTests(unittest.TestCase):
    def test_checked_slots_flow_role_hstring_and_cleanup(self):
        created, deleted, calls = {}, [], []
        next_pointer = iter(range(100, 110))
        class Library:
            def RoInitialize(self, mode):
                self.mode = mode
                return 0
            def RoUninitialize(self):
                self.uninitialized = True
            def WindowsCreateString(self, text, length, out):
                pointer = next(next_pointer)
                created[pointer] = text
                self.assert_length = length == len(text)
                out._obj.value = pointer
                return 0
            def WindowsDeleteString(self, pointer):
                deleted.append(pointer.value)
            def RoGetActivationFactory(self, runtime, iid, out):
                out._obj.value = 90
                return 0
            def WindowsGetStringRawBuffer(self, raw, length):
                length._obj.value = 0
                return ""
        library = Library()
        native = _WindowsAppRouteAdapter.__new__(_WindowsAppRouteAdapter)
        native._combase = library
        def call(pointer, slot, argtypes, *args, **kwargs):
            calls.append((slot, args))
            if slot == 26:
                args[-1]._obj.value = 150
            elif slot == 25:
                self.assertEqual(args[:3], (30, 0, 1))
                self.assertEqual(created[args[-1].value], "owned-device-route")
            elif slot != 2:
                self.fail(f"Unchecked native slot {slot}")
            return 0
        with patch("api.windows_app_audio_routes._call", side_effect=call):
            self.assertEqual(native.get_route(30, 0), "")
            native.set_route(30, 1, "owned-device-route")
        self.assertEqual([slot for slot, _ in calls], [26, 2, 25, 2])
        self.assertIn(150, deleted)
        self.assertTrue(all(pointer in deleted for pointer in created))
        self.assertEqual(library.mode, 1)
        self.assertTrue(library.uninitialized)


if __name__ == "__main__":
    unittest.main()
