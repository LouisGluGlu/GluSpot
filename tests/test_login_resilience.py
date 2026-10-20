from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from api.online_music import OnlineMusicService, OnlineMusicError
from api.online_music_credentials import ProtectedCredentialStore
from api.online_music_diagnostics import login_trace, login_phase, login_request_cancelled, record_login_phase
from api.online_music_resume import KEY
from tests.test_online_music import Backend, FixtureProtector, COOKIE


class LoginResilienceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ProtectedCredentialStore(self.folder.name, FixtureProtector())
        self.backend = Backend()
        self.now = 1000.
        self.service = self.make_service()

    def make_service(self):
        service = OnlineMusicService(self.backend, self.store, clock=lambda: self.now)
        self.addCleanup(service.close)
        return service

    def login(self):
        snap = self.service.begin_login("netease")
        self.backend.state = "confirmed"
        return self.service.poll_login(snap.session_id)

    def test_cold_cached_profile_is_immediate_but_never_verified(self):
        self.login()
        restored = self.make_service()
        calls = len(self.backend.calls)
        cached = restored.restore_account("netease")
        self.assertEqual(cached.state, "cached")
        self.assertFalse(cached.logged_in)
        self.assertEqual(cached.nickname, "用户")
        self.assertEqual(len(self.backend.calls), calls)
        self.assertTrue(restored.account_status("netease", refresh=True).logged_in)

    def test_catalog_snapshot_is_encrypted_bound_and_not_live_authority(self):
        self.login()
        self.backend.catalog["playlists"] = {"playlist": [{"id": 1, "name": "favorite", "picUrl": "https://example.org/art"}]}
        self.service.playlists("netease")
        restored = self.make_service()
        self.assertFalse(restored.restore_account("netease").logged_in)
        self.assertEqual(restored.cached_catalog("netease").items[0].title, "favorite")
        self.assertNotIn(b"favorite", (Path(self.folder.name) / "netease.dpapi").read_bytes())
        self.assertFalse(restored.cached_catalog("qq").items)
        restored.logout("netease")
        self.assertFalse(restored.cached_catalog("netease").items)

    def test_transient_validation_keeps_snapshot_and_protected_session(self):
        self.login()
        restored = self.make_service()
        restored.restore_account("netease")
        self.backend.error = True
        status = restored.account_status("netease", refresh=True)
        self.assertFalse(status.logged_in)
        self.assertEqual(status.user_id, "42")
        self.assertEqual(status.state, "error")
        self.assertTrue(restored.has_login_record("netease"))
        self.backend.error = False
        self.assertTrue(restored.account_status("netease", refresh=True).logged_in)

    def test_explicit_invalidity_deletes_only_that_source_record(self):
        self.login()
        self.store.save("qq", {"token": "synthetic", "sessions": [{}]})
        self.backend.verified = False
        self.assertFalse(self.service.account_status("netease", refresh=True).logged_in)
        self.assertFalse(self.service.has_login_record("netease"))
        self.assertTrue(self.service.has_login_record("qq"))

    def test_account_mismatch_does_not_adopt_old_catalog(self):
        self.login()
        original = self.backend.request
        def changed(source, op, params, credential=None):
            response = original(source, op, params, credential)
            if op == "account_status":
                response["data"]["user_id"] = "other-account"
            return response
        self.backend.request = changed
        result = self.service.account_status("netease", refresh=True)
        self.assertEqual(result.state, "expired")
        self.assertFalse(self.service.has_login_record("netease"))

    def test_simultaneous_validation_is_coalesced_without_extra_request(self):
        self.login()
        started, release = threading.Event(), threading.Event()
        original = self.backend.request
        count = []
        def blocked(source, op, params, credential=None):
            if op == "account_status":
                count.append(1)
                started.set()
                release.wait(2)
            return original(source, op, params, credential)
        self.backend.request = blocked
        results = []
        a = threading.Thread(target=lambda: results.append(self.service.account_status("netease", refresh=True)))
        b = threading.Thread(target=lambda: results.append(self.service.account_status("netease", refresh=True)))
        a.start()
        self.assertTrue(started.wait(1))
        b.start()
        time.sleep(.03)
        release.set()
        a.join(2); b.join(2)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(len(count), 1)
        self.assertTrue(all(item.logged_in for item in results))

    def test_temporary_poll_failure_retains_qr_and_recovers(self):
        snap = self.service.begin_login("netease")
        self.backend.error = True
        temporary = self.service.poll_login(snap.session_id)
        self.assertEqual(temporary.state, "waiting")
        self.assertEqual(temporary.qr_image_bytes, snap.qr_image_bytes)
        self.assertEqual(temporary.failure_stage, "poll_login")
        self.assertEqual(temporary.failure_category, "network")
        calls = len(self.backend.calls)
        self.service.poll_login(snap.session_id)
        self.assertEqual(len(self.backend.calls), calls)
        self.now += 1
        self.backend.error = False
        self.backend.state = "scanned"
        self.assertEqual(self.service.poll_login(snap.session_id).state, "scanned")

    def test_confirmed_account_verification_retries_that_stage_only(self):
        snap = self.service.begin_login("netease")
        self.backend.state = "confirmed"
        original = self.backend.request
        failure = [True]
        def request(source, op, params, credential=None):
            if op == "account_status" and failure[0]:
                raise OnlineMusicError("network", source)
            return original(source, op, params, credential)
        self.backend.request = request
        retry = self.service.poll_login(snap.session_id)
        self.assertEqual(retry.state, "scanned")
        self.assertEqual(retry.failure_stage, "account_status")
        self.assertFalse(self.service.has_login_record("netease"))
        self.now += 1
        failure[0] = False
        self.assertEqual(self.service.poll_login(snap.session_id).state, "confirmed")
        self.assertEqual(sum(op == "poll_login" for _, op, _ in self.backend.calls), 1)

    def test_bounded_poll_retries_end_and_expiry_never_reuses_qr(self):
        snap = self.service.begin_login("netease")
        self.backend.error = True
        for _ in range(3):
            self.assertEqual(self.service.poll_login(snap.session_id).state, "waiting")
            self.now += 5
        self.assertEqual(self.service.poll_login(snap.session_id).state, "error")
        self.assertFalse(self.service.poll_login(snap.session_id).qr_image_bytes)

    def test_local_record_read_late_after_logout_cannot_resurrect(self):
        self.login()
        restored = self.make_service()
        original = self.store.load
        entered, release = threading.Event(), threading.Event()
        def load(source):
            value = original(source)
            entered.set(); release.wait(2)
            return value
        result = []
        with patch.object(self.store, "load", side_effect=load):
            thread = threading.Thread(target=lambda: result.append(restored.restore_account("netease")))
            thread.start(); self.assertTrue(entered.wait(1))
            restored.logout("netease")
            release.set(); thread.join(2)
        self.assertFalse(result[0].logged_in)
        self.assertEqual(result[0].state, "signed_out")
        self.assertFalse(restored.has_login_record("netease"))

    def test_qq_begin_passes_device_only_and_preserves_session_authority(self):
        saved = {"token": "private", "sessions": [{"private": "session"}], "device_context": {"synthetic": 1}}
        self.store.save("qq", saved)
        original = self.backend.request
        seen = []
        def request(source, op, params, credential=None):
            if op == "begin_login":
                seen.append(credential)
            return original(source, op, params, credential)
        self.backend.request = request
        self.service.begin_login("qq")
        self.assertEqual(seen, [{"device_context": {"synthetic": 1}}])
        self.assertEqual(self.store.load("qq")["token"], "private")

    def test_expired_snapshot_is_not_displayed_or_rebound(self):
        self.login()
        self.now += 31 * 86400
        restored = self.make_service()
        self.assertEqual(restored.restore_account("netease").state, "signed_out")

    def test_retired_error_cancel_cannot_cancel_new_same_platform_attempt(self):
        first = self.service.begin_login("netease")
        self.backend.state = "error"
        self.assertEqual(self.service.poll_login(first.session_id).state, "error")
        second = self.service.begin_login("netease")
        generation = self.service._generations["netease"]
        self.service.cancel_login(first.session_id)
        self.assertEqual(self.service._generations["netease"], generation)
        self.backend.state = "confirmed"
        self.assertEqual(self.service.poll_login(second.session_id).state, "confirmed")

    def test_cancel_signal_finishes_before_replacement_registration(self):
        for operation in ("invalidate_login", "invalidate_source_login", "cancel_account_validation"):
            with self.subTest(operation=operation):
                first = self.service.begin_login("netease")
                cancel_entered, release, starting, started = (threading.Event() for _ in range(4))
                errors = []

                def cancel(source):
                    cancel_entered.set()
                    if not release.wait(2):
                        errors.append("cancel barrier timed out")

                def replace_login():
                    starting.set()
                    self.service.begin_login("netease")
                    started.set()

                self.backend.cancel_login_request = cancel
                argument = first.session_id if operation == "invalidate_login" else "netease"
                old = threading.Thread(target=lambda: getattr(self.service, operation)(argument))
                new = threading.Thread(target=replace_login)
                old.start()
                self.assertTrue(cancel_entered.wait(1))
                new.start()
                self.assertTrue(starting.wait(1))
                try:
                    self.assertFalse(started.wait(.03))
                finally:
                    release.set()
                    old.join(2); new.join(2)
                self.assertFalse(old.is_alive() or new.is_alive())
                self.assertTrue(started.is_set())
                self.assertEqual(errors, [])

    def test_terminal_sdk_failure_keeps_safe_stage_without_retrying_dead_qr(self):
        snap = self.service.begin_login("qq")
        with patch.object(self.backend, "request", return_value={"data": {
                "state": "error", "failure_category": "network", "failure_stage": "poll_login",
                "message": "private upstream response"}}):
            result = self.service.poll_login(snap.session_id)
        self.assertEqual(result.state, "error")
        self.assertEqual(result.failure_category, "network")
        self.assertEqual(result.failure_stage, "poll_login")
        self.assertFalse(result.qr_image_bytes)
        self.assertNotIn("private", result.message)
        self.assertIn("刷新", result.message)

    def test_confirmed_malformed_identity_retries_verification_without_authorizing(self):
        self.backend.state = "confirmed"
        original = self.backend.request
        for identity in (None, True, False, 42.0, [], {}, "", " "):
            with self.subTest(identity=identity):
                snap = self.service.begin_login("netease")
                def malformed(source, operation, params, credential=None):
                    response = original(source, operation, params, credential)
                    if operation == "account_status":
                        response["data"]["user_id"] = identity
                    return response
                with patch.object(self.backend, "request", side_effect=malformed):
                    result = self.service.poll_login(snap.session_id)
                self.assertEqual(result.state, "scanned")
                self.assertEqual(result.failure_stage, "account_status")
                self.assertEqual(result.failure_category, "invalid_response")
                self.assertFalse(self.service.account_status("netease").logged_in)
                self.assertFalse(self.service.has_login_record("netease"))

    def test_poll_and_confirmation_keep_their_original_request_ownership(self):
        original = self.backend.request
        for blocked_operation in ("poll_login", "account_status"):
            with self.subTest(operation=blocked_operation):
                self.backend.state = "confirmed" if blocked_operation == "account_status" else "waiting"
                snapshot = self.service.begin_login("netease")
                entered, release = threading.Event(), threading.Event()
                observed, results = [], []
                def blocked(source, operation, params, credential=None):
                    if operation == blocked_operation and threading.current_thread().name == "retired-poll":
                        entered.set()
                        if not release.wait(2):raise AssertionError("poll fixture timed out")
                        observed.append(login_request_cancelled())
                        if login_request_cancelled():raise OnlineMusicError("cancelled", source)
                    return original(source, operation, params, credential)
                with patch.object(self.backend, "request", side_effect=blocked):
                    worker = threading.Thread(target=lambda: results.append(self.service.poll_login(snapshot.session_id)), name="retired-poll")
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(1))
                        self.service.invalidate_source_login("netease")
                        replacement = self.service.begin_login("netease")
                    finally:
                        release.set(); worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(observed, [True])
                self.assertEqual(results[0].state, "cancelled")
                self.assertEqual(self.service.poll_login(replacement.session_id).session_id, replacement.session_id)
                # A confirmed replacement belongs to the new generation only.
                if blocked_operation == "poll_login":
                    self.assertFalse(self.service.has_login_record("netease"))

    def test_cancel_validation_owns_only_its_epoch_and_preserves_cached_account(self):
        self.login()
        restored = self.make_service()
        self.assertEqual(restored.restore_account("netease").state, "cached")
        entered, release = threading.Event(), threading.Event()
        observed, results = [], []
        original = self.backend.request
        def blocked(source, operation, params, credential=None):
            if operation == "account_status" and threading.current_thread().name == "retired-validation":
                entered.set()
                if not release.wait(2):raise AssertionError("validation fixture timed out")
                observed.append(login_request_cancelled())
                if login_request_cancelled():raise OnlineMusicError("cancelled", source)
            return original(source, operation, params, credential)
        with patch.object(self.backend, "request", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(restored.account_status("netease", refresh=True)), name="retired-validation")
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                generation = restored._generations["netease"]
                restored.cancel_account_validation("netease")
                self.assertEqual(restored._generations["netease"], generation)
            finally:
                release.set(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(observed, [True])
        self.assertEqual(results[0].state, "cached")
        self.assertFalse(results[0].logged_in)
        self.assertTrue(restored.has_login_record("netease"))
        self.assertTrue(restored.account_status("netease", refresh=True).logged_in)

    def test_terminal_confirmation_failure_retains_stage_and_explicit_retry(self):
        snap = self.service.begin_login("netease")
        self.backend.state = "confirmed"
        original = self.backend.request
        fail = [True]
        def request(source, op, params, credential=None):
            if op == "account_status" and fail[0]:
                raise OnlineMusicError("timeout", source)
            return original(source, op, params, credential)
        self.backend.request = request
        for _ in range(3):
            self.service.poll_login(snap.session_id)
            self.now += 5
        result = self.service.poll_login(snap.session_id)
        self.assertEqual(result.state, "error")
        self.assertEqual(result.failure_stage, "account_status")
        self.assertEqual(result.failure_category, "timeout")
        fail[0] = False
        self.assertEqual(self.service.retry_login_verification(snap.session_id).state, "confirmed")
        self.assertEqual(sum(op == "poll_login" for _, op, _ in self.backend.calls), 1)

    def test_lost_runtime_session_is_not_reported_as_expired_or_recoverable_qr(self):
        snap = self.service.begin_login("netease")
        self.backend.request = lambda *args, **kwargs: (_ for _ in ()).throw(
            OnlineMusicError("timeout", "netease", session_lost=True))
        result = self.service.poll_login(snap.session_id)
        self.assertEqual(result.state, "error")
        self.assertEqual(result.failure_category, "runtime")
        self.assertEqual(result.failure_stage, "poll_login")
        self.assertFalse(result.qr_image_bytes)
        self.assertIn("重启", result.message)

    def test_diagnostic_allowlist_never_logs_arbitrary_values(self):
        with self.assertLogs("GluSpot.login", level="INFO") as logs:
            record_login_phase("netease", "begin", "task_queue", .001, 2, "secret cookie")
            record_login_phase("token=secret", "begin", "task_queue", .001)
            with login_trace("qq", "account", 3):
                with login_phase("saved_account_validation"):
                    pass
        output = "\n".join(logs.output)
        self.assertNotIn("secret", output)
        self.assertNotIn("cookie", output)
        self.assertIn("error=request_failed", output)
        self.assertIn("stage=saved_account_validation", output)


if __name__ == "__main__":
    unittest.main()
