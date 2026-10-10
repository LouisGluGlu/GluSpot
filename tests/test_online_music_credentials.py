from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.online_music_credentials import (
    CredentialProtectionError, ProtectedCredentialStore, WindowsDPAPI,
)


class FixtureProtector:
    """Authenticated reversible test fixture; never used as a runtime cipher."""
    key = hashlib.sha256(b"synthetic-unit-test-protector").digest()

    def __init__(self):
        self.protect_calls = 0
        self.unprotect_calls = 0

    def protect(self, data):
        self.protect_calls += 1
        encoded = bytes(value ^ self.key[index % len(self.key)] for index, value in enumerate(data))
        return hmac.digest(self.key, encoded, "sha256") + encoded

    def unprotect(self, data):
        self.unprotect_calls += 1
        tag, encoded = data[:32], data[32:]
        if not hmac.compare_digest(tag, hmac.digest(self.key, encoded, "sha256")):
            raise CredentialProtectionError("Synthetic protected data was tampered with")
        return bytes(value ^ self.key[index % len(self.key)] for index, value in enumerate(encoded))


class ProtectedCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "accounts"
        self.protector = FixtureProtector()
        self.store = ProtectedCredentialStore(self.root, self.protector)
        self.credential = {
            "cookie": "synthetic-unit-secret-cookie-never-a-real-account",
            "token": "synthetic-unit-secret-token-never-a-real-account",
            "sessions": [{"device": {"identity": "fixture-device"}, "expiresAt": 1900000000000}],
        }

    def write_envelope(self, source, envelope):
        self.root.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(envelope).encode("utf-8")
        (self.root / f"{source}.dpapi").write_bytes(
            self.store._magic + self.protector.protect(plaintext))

    def symlink_or_skip(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Creating temporary symlink is unavailable: {type(error).__name__}")

    def test_saved_file_is_protected_and_restart_restores_complete_envelope(self):
        self.store.save("qq", self.credential)
        path = self.root / "qq.dpapi"
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(self.store._magic))
        self.assertNotIn(self.credential["cookie"].encode(), raw)
        self.assertNotIn(self.credential["token"].encode(), raw)
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), ["qq.dpapi"])
        restarted = ProtectedCredentialStore(self.root, FixtureProtector())
        restored = restarted.load("qq")
        self.assertEqual(restored, self.credential)
        restored["sessions"][0]["device"]["identity"] = "changed-only-in-memory"
        self.assertEqual(restarted.load("qq"), self.credential)

    def test_missing_account_returns_none_without_creating_storage(self):
        self.assertIsNone(self.store.load("netease"))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.protector.unprotect_calls, 0)

    def test_unknown_or_traversal_source_cannot_write_read_or_delete(self):
        for source in ("../outside", "qq/../../outside", "QQ", "unregistered"):
            for operation in (
                lambda: self.store.save(source, self.credential),
                lambda: self.store.load(source),
                lambda: self.store.delete(source),
            ):
                with self.subTest(source=source), self.assertRaises(CredentialProtectionError):
                    operation()
        self.assertFalse(self.root.exists())

    def test_moved_source_file_is_rejected_after_decryption(self):
        self.store.save("netease", self.credential)
        (self.root / "qq.dpapi").write_bytes((self.root / "netease.dpapi").read_bytes())
        with self.assertRaises(CredentialProtectionError):
            self.store.load("qq")
        self.assertEqual(self.store.load("netease"), self.credential)

    def test_tampered_ciphertext_and_plaintext_file_fail_closed(self):
        self.store.save("qq", self.credential)
        path = self.root / "qq.dpapi"
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
        with self.assertRaises(CredentialProtectionError):
            self.store.load("qq")
        path.write_bytes(json.dumps(self.credential).encode())
        with self.assertRaises(CredentialProtectionError):
            self.store.load("qq")

    def test_invalid_envelope_schema_is_rejected(self):
        for envelope in (
            [], {"version": 1, "source_id": "qq", "credential": []},
            {"version": 2, "source_id": "qq", "credential": {}},
            {"version": 1, "source_id": "qq"},
        ):
            with self.subTest(envelope=envelope):
                self.write_envelope("qq", envelope)
                with self.assertRaises(CredentialProtectionError):
                    self.store.load("qq")

    def test_oversize_file_is_rejected_before_unprotect(self):
        self.root.mkdir()
        (self.root / "qq.dpapi").write_bytes(self.store._magic + b"x" * self.store._max_bytes)
        with self.assertRaises(CredentialProtectionError):
            self.store.load("qq")
        self.assertEqual(self.protector.unprotect_calls, 0)

    def test_oversize_credential_preserves_previous_account(self):
        self.store.save("qq", self.credential)
        calls = self.protector.protect_calls
        with self.assertRaises(CredentialProtectionError):
            self.store.save("qq", {"token": "x" * self.store._max_bytes})
        self.assertEqual(self.protector.protect_calls, calls)
        self.assertEqual(self.store.load("qq"), self.credential)

    def test_account_symlink_cannot_read_replace_or_delete_external_file(self):
        self.root.mkdir()
        outside = self.base / "outside.dpapi"
        outside.write_bytes(b"unrelated-temporary-file")
        self.symlink_or_skip(self.root / "qq.dpapi", outside)
        for operation in (
            lambda: self.store.load("qq"),
            lambda: self.store.save("qq", self.credential),
            lambda: self.store.delete("qq"),
        ):
            with self.assertRaises(CredentialProtectionError):
                operation()
        self.assertEqual(outside.read_bytes(), b"unrelated-temporary-file")
        self.assertTrue((self.root / "qq.dpapi").is_symlink())

    def test_redirected_root_cannot_create_or_remove_external_account(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.symlink_or_skip(self.root, outside, directory=True)
        for operation in (
            lambda: self.store.load("qq"),
            lambda: self.store.save("qq", self.credential),
            lambda: self.store.delete("qq"),
        ):
            with self.assertRaises(CredentialProtectionError):
                operation()
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_ancestor_is_rejected_before_creating_child_directory(self):
        outside = self.base / "outside"
        outside.mkdir()
        redirect = self.base / "redirect"
        self.symlink_or_skip(redirect, outside, directory=True)
        store = ProtectedCredentialStore(redirect / "new-account-directory", self.protector)
        with self.assertRaises(CredentialProtectionError):
            store.save("qq", self.credential)
        self.assertFalse((outside / "new-account-directory").exists())

    def test_failed_atomic_replace_retains_old_account_and_removes_temporary_file(self):
        self.store.save("qq", self.credential)
        old_raw = (self.root / "qq.dpapi").read_bytes()
        with patch("api.online_music_credentials.os.replace", side_effect=OSError("fixture replace failed")):
            with self.assertRaises(OSError):
                self.store.save("qq", {"token": "new-synthetic-value"})
        self.assertEqual((self.root / "qq.dpapi").read_bytes(), old_raw)
        self.assertEqual(self.store.load("qq"), self.credential)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_failed_fsync_retains_old_account_and_removes_temporary_file(self):
        self.store.save("qq", self.credential)
        with patch("api.online_music_credentials.os.fsync", side_effect=OSError("fixture fsync failed")):
            with self.assertRaises(OSError):
                self.store.save("qq", {"token": "new-synthetic-value"})
        self.assertEqual(self.store.load("qq"), self.credential)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_protection_failure_has_no_plaintext_fallback(self):
        with patch.object(self.protector, "protect", side_effect=CredentialProtectionError("fixture unavailable")):
            with self.assertRaises(CredentialProtectionError):
                self.store.save("qq", self.credential)
        self.assertFalse((self.root / "qq.dpapi").exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_delete_is_scoped_and_idempotent(self):
        self.store.save("qq", self.credential)
        self.store.save("netease", {"cookie": "second-synthetic-secret"})
        unrelated = self.root / "unrelated.txt"
        unrelated.write_text("keep", encoding="utf-8")
        self.store.delete("qq")
        self.store.delete("qq")
        self.assertIsNone(self.store.load("qq"))
        self.assertEqual(self.store.load("netease"), {"cookie": "second-synthetic-secret"})
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")


@unittest.skipUnless(os.name == "nt", "Actual Windows DPAPI requires Windows")
class WindowsDPAPITests(unittest.TestCase):
    def test_synthetic_secret_protect_unprotect_and_tamper_rejection(self):
        protector = WindowsDPAPI()
        secret = b"synthetic-dpapi-unit-secret-not-a-user-account"
        ciphertext = protector.protect(secret)
        self.assertNotIn(secret, ciphertext)
        self.assertEqual(protector.unprotect(ciphertext), secret)
        damaged = bytearray(ciphertext)
        damaged[-1] ^= 1
        with self.assertRaises(CredentialProtectionError):
            protector.unprotect(bytes(damaged))

    def test_actual_dpapi_store_restart_roundtrip_uses_only_temporary_root(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = {"cookie": "synthetic-dpapi-temporary-account", "userid": "fixture-123"}
            store = ProtectedCredentialStore(Path(directory) / "accounts", WindowsDPAPI())
            store.save("netease", credential)
            raw = (store.root / "netease.dpapi").read_bytes()
            self.assertNotIn(credential["cookie"].encode(), raw)
            restored = ProtectedCredentialStore(store.root, WindowsDPAPI()).load("netease")
            self.assertEqual(restored, credential)


if __name__ == "__main__":
    unittest.main()
