"""Owned temporary music-app output overrides, separate from global defaults.

The internal AudioPolicyConfig ABI is checked against EarTrumpet's MIT variants:
https://github.com/File-New-Project/EarTrumpet/tree/master/EarTrumpet/Interop
21H2/downlevel use IInspectable plus nineteen prefix methods: Set slot 25,
Get slot 26. A persisted-policy readback is not proof of live stream migration.
No virtual device, audio capture, mute or volume operation lives here.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import json
import math
import ntpath
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

from api.windows_audio_devices import _ComScope, _DWORD, _GUID, _HRESULT, _call, _check, WindowsAudioDevices
from api.windows_audio_sessions import (
    MusicAudioSource, ProcessIdentity, _WindowsAudioSessionAdapter,
    _valid_identity, resolve_music_audio_source,
)

_PREFIX = r"\\?\SWD#MMDEVAPI#"
_RENDER_SUFFIX = "#{e6327cad-dcec-4949-ae8a-991e976a79d2}"


def _route_text(value):
    if not isinstance(value, str) or "\0" in value or len(value) > 32768:
        raise ValueError("Windows app route is unknown or invalid")
    return value


def _same_application(first, second):
    return (isinstance(second, ProcessIdentity)
            and ntpath.normcase(ntpath.normpath(first.process_path)) == ntpath.normcase(ntpath.normpath(second.process_path))
            and first.application_user_model_id == second.application_user_model_id)


@dataclass
class _OwnedRole:
    identity: ProcessIdentity
    role: int
    original: str
    owned_route: str
    intent: bool = False
    restored: bool = False
    external_change: bool = False
    original_effective_endpoint: str = ""
    restoring_route: str = ""


class AppAudioRouteMigrationTimeout(RuntimeError):
    """A confirmed policy write without confirmed live playback migration."""
    def __init__(self, evidence):
        super().__init__("Music playback did not migrate to the temporary output")
        self.evidence = dict(evidence)


class WindowsAppAudioRoutes:
    """Explicit route transaction factory; construction has no Windows effects.

    A native adapter supplies hold_process(identity), process_identity(pid),
    get_route(pid,role), set_route(pid,role,raw), active_owners(), session_owners()
    and close_handle. The full session inventory distinguishes inactive
    retained streams from absent or still-running music.
    The resolver and devices reuse the existing source/output boundaries.
    """
    def __init__(self, *, devices=None, adapter=None, resolver=None, journal_path=None):
        self._devices = devices or WindowsAudioDevices()
        self._adapter = adapter
        self._resolver = resolver or resolve_music_audio_source
        if journal_path is None:
            from local.branding import local_app_data_dir
            journal_path = Path(local_app_data_dir()) / "audio-app-route-recovery.json"
        self._journal = Path(journal_path)
        self._lock = threading.RLock()
        self._active = None
        self._lease = None

    def _claim_journal(self):
        if self._lease is not None:
            return
        path = self._journal.with_suffix(self._journal.suffix + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lease = stream
        except OSError as error:
            stream.close()
            raise RuntimeError("Another process owns the app audio route journal") from error

    def _release_journal(self):
        if self._lease is not None:
            self._lease.close()  # OS releases the byte/file lock, including after a crash.
            self._lease = None

    def close(self, *, timeout=5.0):
        """Restore on shutdown; preserve unresolved durable recovery evidence."""
        with self._lock:
            try:
                if self._active is not None:
                    self._active.restore(timeout=timeout)
            finally:
                if self._active is not None:
                    self._active._close_handles()
                    self._active._closed = True
                    self._active = None
                self._release_journal()

    def prepare(self, source: MusicAudioSource, spare_endpoint: str, local_endpoint: str):
        """Read all evidence before a caller explicitly invokes guard.apply()."""
        with self._lock:
            if self._active is not None:
                raise RuntimeError("An app audio route still requires restoration")
            if self._journal.exists():
                try:
                    pending = self.pending_recovery()
                except (ValueError, TypeError, OSError):
                    raise RuntimeError("An app audio route still requires restoration") from None
                if pending.get("stage") != "resume_verification_pending":
                    raise RuntimeError("An app audio route still requires restoration")
            if not isinstance(source, MusicAudioSource) or not source.sessions:
                raise ValueError("A verified music source with current sessions is required")
            WindowsAudioDevices._validate_endpoint_id(spare_endpoint)
            WindowsAudioDevices._validate_endpoint_id(local_endpoint)
            if spare_endpoint == local_endpoint:
                raise ValueError("Capture source and delayed local output must differ")
            active = {output.id for output in self._devices.list_outputs()}
            if spare_endpoint not in active or local_endpoint not in active:
                raise ValueError("Both explicit render endpoints must be active")
            fresh = self._resolver(source.source_app_id)
            if not self._same_source(source, fresh):
                raise RuntimeError("Music source identity changed before app routing")
            if len({fresh.process_id, *fresh.session_process_ids}) > 64:
                raise ValueError("Music routing supports at most 64 verified processes")
            if self._adapter is None:
                if sys.platform != "win32":
                    raise RuntimeError("Application output routing requires Windows")
                self._adapter = _WindowsAppRouteAdapter()
            self._claim_journal()
            try:
                if self._journal.exists():
                    if self.pending_recovery().get("stage") != "resume_verification_pending":
                        raise RuntimeError("An app audio route still requires restoration")
                    self.recover_pending()
                    if self._journal.exists():
                        # All original policies and inactive ownership were
                        # just checked under this lease. A new explicit group
                        # request supersedes the remaining observation only.
                        self._journal.unlink()
                    self._claim_journal()
                    fresh = self._resolver(source.source_app_id)
                    if not self._same_source(source, fresh) or not fresh.sessions:
                        raise RuntimeError("Music source changed while restoring prior output routing")
                guard = AppAudioRouteGuard(self, fresh, spare_endpoint, local_endpoint)
            except Exception:
                self._release_journal()
                raise
            self._active = guard
            return guard

    def endpoint_is_unoccupied(self, endpoint, source, *, owned_process=None):
        """Read full active ownership, including system/ambiguous sessions."""
        WindowsAudioDevices._validate_endpoint_id(endpoint)
        if not isinstance(source, MusicAudioSource):
            raise ValueError("Verified music process evidence is required")
        with self._lock:
            if self._adapter is None:
                if sys.platform != "win32":
                    return False
                self._adapter = _WindowsAppRouteAdapter()
            selected = {source.process_id, *source.session_process_ids}
            return self._endpoint_owners_allowed(endpoint, selected, owned_process=owned_process)

    def _endpoint_owners_allowed(self, endpoint, selected, *, owned_process=None):
        """Accept music owners plus a separately reverified owned capture child.

        Process loopback can register the native helper as an active session on
        the source render endpoint. It does not make that endpoint shared with
        another application. Only a caller's exact live child identity grants
        this exception; ambiguous and system sessions remain unsafe to mute.
        """
        for endpoint_id, pid, single, system in self._adapter.active_owners():
            if endpoint_id != endpoint:
                continue
            if single is not True or system is not False:
                return False
            if pid in selected:
                continue
            if (not _valid_identity(owned_process, pid)
                    or pid != owned_process.process_id
                    or self._adapter.process_identity(pid) != owned_process):
                return False
        return True

    def _default_endpoint(self):
        selected = {output.id for output in self._devices.list_outputs() if getattr(output, "is_default", False)}
        if len(selected) != 1:
            raise RuntimeError("The current Windows default output is not uniquely verified")
        return next(iter(selected))

    def retire_resume_observation(self):
        """An explicit output selection supersedes only returned-policy observation.

        This never clears a record with outstanding writes and never changes
        an application policy. New user policies are retained verbatim.
        """
        with self._lock:
            if self._active is not None:
                raise RuntimeError("An app audio route still requires restoration")
            self._claim_journal()
            try:
                value = self.pending_recovery()
                if value is None:
                    return False
                if value.get("stage") != "resume_verification_pending":
                    raise RuntimeError("An app audio route still requires restoration")
                if self._adapter is None:
                    if sys.platform != "win32":
                        raise RuntimeError("Application output observation requires Windows")
                    self._adapter = _WindowsAppRouteAdapter()
                for role in value["roles"]:
                    original = ProcessIdentity(**role["identity"])
                    # A returned-policy record owns no writes on a dead/reused
                    # PID. Never use its numeric PID to query a replacement.
                    if self._adapter.process_identity(original.process_id) == original:
                        _route_text(self._adapter.get_route(original.process_id, role["role"]))
                self._journal.unlink()
                return True
            finally:
                self._release_journal()

    @staticmethod
    def _same_source(first, second):
        return (isinstance(second, MusicAudioSource)
                and first.process_id == second.process_id
                and first.process_created_at_100ns == second.process_created_at_100ns
                and ntpath.normcase(first.process_path) == ntpath.normcase(second.process_path))

    def pending_recovery(self):
        """Return a bounded recovery record without invoking a native setter."""
        with self._lock:
            if not self._journal.exists():
                return None
            if self._journal.stat().st_size > 1024 * 1024:
                raise ValueError("App route recovery record exceeds its limit")
            value = json.loads(self._journal.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or value.get("schema") != 1
                    or not isinstance(value.get("source"), dict)
                    or not isinstance(value.get("roles"), list) or not 0 < len(value["roles"]) <= 128):
                raise ValueError("App route recovery record is invalid")
            WindowsAudioDevices._validate_endpoint_id(value.get("spare_endpoint"))
            WindowsAudioDevices._validate_endpoint_id(value.get("local_endpoint"))
            if value.get("original_default_endpoint"):
                WindowsAudioDevices._validate_endpoint_id(value["original_default_endpoint"])
            keys, identities = set(), {}
            for role in value["roles"]:
                if (not isinstance(role, dict) or type(role.get("role")) is not int or role.get("role") not in (0, 1)
                        or not isinstance(role.get("identity"), dict)):
                    raise ValueError("App route recovery identity is invalid")
                identity = ProcessIdentity(**role["identity"])
                if not _valid_identity(identity, identity.process_id):
                    raise ValueError("App route recovery process identity is invalid")
                key = (identity.process_id, role["role"])
                if key in keys or (identity.process_id in identities and identities[identity.process_id] != identity):
                    raise ValueError("App route recovery roles have conflicting identities")
                keys.add(key)
                identities[identity.process_id] = identity
                _route_text(role.get("original"))
                if _route_text(role.get("owned_route")) != _PREFIX + value["spare_endpoint"] + _RENDER_SUFFIX:
                    raise ValueError("App route recovery does not match its owned endpoint")
                if any(type(role.get(flag)) is not bool for flag in ("intent", "restored", "external_change")):
                    raise ValueError("App route recovery ownership flags are invalid")
                if role.get("original_effective_endpoint"):
                    WindowsAudioDevices._validate_endpoint_id(role["original_effective_endpoint"])
                restoring = _route_text(role.get("restoring_route", ""))
                expected_restore = role["original"] or _PREFIX + role.get("original_effective_endpoint", "") + _RENDER_SUFFIX
                if restoring and restoring != expected_restore:
                    raise ValueError("App route recovery has an unrelated restoring policy")
            if value.get("stage") == "resume_verification_pending" and not all(
                    role["restored"] or role["external_change"] for role in value["roles"]):
                raise ValueError("Deferred playback verification still contains owned route writes")
            return value

    def recover_pending(self):
        """Restore only policies evidenced on one exact live application tree.

        A dead PID is never assumed resolvable by the internal Windows API.
        When the app is restarted, exact full path/AUMID and a fresh policy Get
        must independently prove that the owned policy applies to the new PID.
        Until then the durable journal remains actionable and blocks new writes.
        """
        with self._lock:
            prior_lease = self._lease
            try:
                return self._recover_pending_locked()
            finally:
                # A pure observation or failed read-only recovery must never
                # acquire a lease for longer than this call. An existing live
                # transaction/prepare retains its prior lease on failure.
                if self._active is None and prior_lease is None:
                    self._release_journal()

    def _recover_pending_locked(self):
        with self._lock:
            if self._active is not None:
                if not self._journal.exists():
                    # A read-only prepared guard still owns the OS lease. A
                    # recovery query must not release another live operation.
                    return False
                if all(self._adapter.process_identity(role.identity.process_id) == role.identity
                       for role in self._active._roles):
                    self._active.restore()
                    return True
                # The prior process died/restarted. Keep its durable journal
                # and lease, then rebuild ownership only from fresh evidence.
                self._active._close_handles()
                self._active._closed = True
                self._active = None
            had_lease = self._lease is not None
            self._claim_journal()
            value = self.pending_recovery()
            if value is None:
                self._release_journal()
                return False
            saved_source = value["source"]
            app_id = saved_source.get("source_app_id")
            if not isinstance(app_id, str) or not app_id or "\0" in app_id:
                raise ValueError("App route recovery source identity is invalid")
            fresh = self._resolver(app_id)
            deferred = value.get("stage") == "resume_verification_pending"
            paused_saved_source = fresh is None and deferred
            if fresh is None and deferred:
                # Paused sources are deliberately absent from the active-only
                # resolver. Reconstruct only their previously held identities;
                # a checked inactive session must independently prove no stream
                # is running before the observation can remain nonblocking.
                try:
                    fresh = MusicAudioSource(app_id, saved_source["process_id"],
                        saved_source["process_created_at_100ns"], saved_source["process_path"],
                        tuple(sorted({role["identity"]["process_id"] for role in value["roles"]})), (), ())
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("Deferred app route source identity is invalid") from error
            if not isinstance(fresh, MusicAudioSource):
                raise RuntimeError("Restart the same music application to restore its pending output route")
            if ntpath.normcase(fresh.process_path) != ntpath.normcase(str(saved_source.get("process_path", ""))):
                raise RuntimeError("Pending app route belongs to a different application image")
            if self._adapter is None:
                if sys.platform != "win32":
                    raise RuntimeError("Application route recovery requires Windows")
                self._adapter = _WindowsAppRouteAdapter()
            identities = [self._adapter.process_identity(pid)
                          for pid in {fresh.process_id, *fresh.session_process_ids}]
            guard = AppAudioRouteGuard.__new__(AppAudioRouteGuard)
            guard._owner, guard.source = self, fresh
            guard.spare_endpoint, guard.local_endpoint = value["spare_endpoint"], value["local_endpoint"]
            guard.original_default_endpoint = value.get("original_default_endpoint", "")
            guard._handles, guard._roles = {}, []
            guard._applied, guard._migrated, guard._closed = True, False, False
            guard._restoration_state = "not_restored"
            mapped_roles = set()
            try:
                for saved in value["roles"]:
                    original_identity = ProcessIdentity(**saved["identity"])
                    candidates = [identity for identity in identities
                                  if identity is not None and _same_application(original_identity, identity)]
                    exact = [identity for identity in candidates if identity == original_identity]
                    if exact:
                        candidates = exact
                    if len(candidates) != 1:
                        raise RuntimeError("Pending route owner is not one uniquely verified live audio process")
                    identity = candidates[0]
                    if paused_saved_source and identity != original_identity:
                        raise RuntimeError("Paused music process identity changed before output observation")
                    if deferred and identity != original_identity:
                        # Returned policies leave only an old-stream
                        # observation, not permission to route a replacement
                        # process. Preserve its current output without a Set.
                        guard._finish_restoration("external_output_preserved")
                        return True
                    mapped_key = (identity.process_id, saved["role"])
                    if mapped_key in mapped_roles:
                        raise RuntimeError("Restarted application merges distinct pending audio route owners")
                    mapped_roles.add(mapped_key)
                    current = _route_text(self._adapter.get_route(identity.process_id, saved["role"]))
                    # A new PID must actually expose the prior owned policy.
                    # Identical image names alone never authorize a Set.
                    if not deferred and identity != original_identity and current not in (
                            saved["owned_route"], saved["original"], saved.get("restoring_route", "")):
                        raise RuntimeError("Restarted application does not expose the pending owned audio policy")
                    if identity.process_id not in guard._handles:
                        guard._handles[identity.process_id] = self._adapter.hold_process(identity)
                    guard._roles.append(_OwnedRole(identity, saved["role"], saved["original"], saved["owned_route"],
                        saved["intent"], saved["restored"], saved["external_change"],
                        saved.get("original_effective_endpoint", ""), saved.get("restoring_route", "")))
                if deferred:
                    externally_changed = False
                    for role in guard._roles:
                        guard._fence(role)
                        current = _route_text(self._adapter.get_route(role.identity.process_id, role.role))
                        if current != role.original:
                            externally_changed = True
                        # The old temporary write was already returned. Any
                        # newer policy belongs to the user, even if it happens
                        # to name the former spare output.
                        role.restored = False
                    if externally_changed:
                        guard._finish_restoration("external_output_preserved")
                        return True
                    if (all(not role.original for role in guard._roles)
                            and guard.original_default_endpoint
                            and self._default_endpoint() != guard.original_default_endpoint):
                        # All per-app policies already follow Windows. A new
                        # global default takes precedence over our old stream
                        # observation; rebinding the old endpoint would undo it.
                        guard._finish_restoration("external_output_preserved")
                        return True
                    expected = {role.original_effective_endpoint for role in guard._roles}
                    if len(expected) != 1 or not next(iter(expected)):
                        raise RuntimeError("Deferred playback has no unique original output")
                    observation = guard._observe_effective(next(iter(expected)))
                    if observation == "policy_restored_no_active_stream":
                        guard._close_handles()
                        guard._closed = True
                        if not had_lease:
                            self._release_journal()
                        return False
                    if observation == "active_output_verified":
                        guard._finish_restoration(observation)
                        return True
                    if not fresh.sessions:
                        raise RuntimeError("Deferred music playback has no verified inactive or active stream")
                    if (not set(fresh.session_process_ids).issubset(guard._handles)
                            or any(session.process_id not in guard._handles for session in fresh.sessions)
                            or len({session.endpoint_id for session in fresh.sessions}) != 1):
                        raise RuntimeError("Deferred playback has changed audio owners or ambiguous outputs")
                    if all(not role.original for role in guard._roles) and not guard.original_default_endpoint:
                        raise RuntimeError("Deferred playback has no original Windows default output evidence")
                    # A resumed stream remains elsewhere despite unchanged
                    # original policies: repair it under fresh exact ownership.
                guard._save("recovering")
                self._active = guard
                guard.restore()
                return True
            except Exception:
                if self._active is not guard:
                    guard._close_handles()
                    self._release_journal()
                raise

    def restore_source_playback(self, original_source, *, timeout=5.0):
        """Repair a proven prior default-following stream after policy clearing.

        Windows may keep an already-open stream on the last explicit output
        after its policy is cleared. This explicit recovery uses the caller's
        original source/session proof, then restores the empty policy again.
        It refuses nonempty current overrides rather than overwriting a user.
        """
        with self._lock:
            if self._active is not None or self._journal.exists():
                raise RuntimeError("Restore pending app routing ownership first")
            fresh = self._resolver(original_source.source_app_id)
            if not self._same_source(original_source, fresh):
                raise RuntimeError("Original music process cannot be verified for playback restoration")
            if len({fresh.process_id, *fresh.session_process_ids}) > 64:
                raise ValueError("Music routing supports at most 64 verified processes")
            endpoints = {session.endpoint_id for session in original_source.sessions}
            if len(endpoints) != 1:
                raise RuntimeError("Original effective music output is not uniquely evidenced")
            endpoint = next(iter(endpoints))
            if endpoint not in {output.id for output in self._devices.list_outputs()}:
                raise RuntimeError("Original effective music output is no longer active")
            if self._adapter is None:
                self._adapter = _WindowsAppRouteAdapter()
            original_default_endpoint = self._default_endpoint()
            self._claim_journal()
            guard = AppAudioRouteGuard.__new__(AppAudioRouteGuard)
            guard._owner, guard.source = self, fresh
            guard.spare_endpoint, guard.local_endpoint = endpoint, endpoint
            guard.original_default_endpoint = original_default_endpoint
            guard._handles, guard._roles = {}, []
            guard._applied, guard._migrated, guard._closed = True, False, False
            guard._restoration_state = "not_restored"
            try:
                for pid in {fresh.process_id, *fresh.session_process_ids}:
                    identity = self._adapter.process_identity(pid)
                    if not _valid_identity(identity, pid):
                        raise RuntimeError("Playback restoration process cannot be verified")
                    guard._handles[pid] = self._adapter.hold_process(identity)
                    for role in (0, 1):
                        if self._adapter.get_route(pid, role) != "":
                            raise RuntimeError("Application output changed after the original routing test")
                        guard._roles.append(_OwnedRole(identity, role, "", _PREFIX + endpoint + _RENDER_SUFFIX,
                                                       True, False, False, endpoint))
                guard._save("effective_restore_required")
                self._active = guard
                guard.restore(timeout=timeout)
            except Exception:
                if self._active is not guard:
                    guard._close_handles()
                    self._release_journal()
                raise


class AppAudioRouteGuard:
    def __init__(self, owner, source, spare, local):
        self._owner, self.source = owner, source
        self.spare_endpoint, self.local_endpoint = spare, local
        self.original_default_endpoint = owner._default_endpoint()
        self._handles, self._roles = {}, []
        self._applied = self._migrated = self._closed = False
        self._restoration_state = "not_restored"
        adapter = owner._adapter
        route = _route_text(_PREFIX + spare + _RENDER_SUFFIX)
        try:
            pids = {source.process_id, *source.session_process_ids}
            if len(pids) > 64:
                raise ValueError("Music routing supports at most 64 verified processes")
            observed_endpoints = {session.endpoint_id for session in source.sessions}
            if len(observed_endpoints) != 1:
                raise RuntimeError("Original effective music output is not uniquely evidenced")
            original_endpoint = next(iter(observed_endpoints))
            for pid in sorted(pids):
                identity = adapter.process_identity(pid)
                if not _valid_identity(identity, pid):
                    raise RuntimeError("Music audio owner cannot be verified")
                if pid == source.process_id and (identity.process_created_at_100ns != source.process_created_at_100ns
                                                 or ntpath.normcase(identity.process_path) != ntpath.normcase(source.process_path)):
                    raise RuntimeError("Music process creation identity changed")
                self._handles[pid] = adapter.hold_process(identity)
                for role in (0, 1):
                    self._roles.append(_OwnedRole(identity, role, _route_text(adapter.get_route(pid, role)), route,
                                                   original_effective_endpoint=original_endpoint))
            self._verify_spare()
        except Exception:
            self._close_handles()
            raise

    @property
    def migrated(self):
        return self._migrated and not self._closed

    @property
    def restoration_state(self):
        """Separate policy cleanup from effective running-stream verification."""
        return self._restoration_state

    def _verify_spare(self, *, owned_process=None):
        selected = {entry.identity.process_id for entry in self._roles}
        if not self._owner._endpoint_owners_allowed(self.spare_endpoint, selected,
                                                    owned_process=owned_process):
            raise RuntimeError("Spare audio endpoint is occupied by another application")

    def _fence(self, entry):
        current = self._owner._adapter.process_identity(entry.identity.process_id)
        if current != entry.identity:
            raise RuntimeError("Music process exited or its identity changed; app route recovery remains pending")

    def _save(self, stage):
        path = self._owner._journal
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {"schema": 1, "stage": stage, "source": asdict(self.source),
                 "spare_endpoint": self.spare_endpoint, "local_endpoint": self.local_endpoint,
                 "original_default_endpoint": self.original_default_endpoint,
                 "roles": [asdict(role) for role in self._roles]}
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        # Reserve space for every later restoration-intent entry before any
        # native Set. Otherwise a near-limit initial record could become too
        # large to save the recovery information after Windows was changed.
        future = dict(value, stage="effective_recovery_required", roles=[dict(role,
            intent=False, restored=False, external_change=False,
            restoring_route=role["original"] or _PREFIX + role["original_effective_endpoint"] + _RENDER_SUFFIX)
            for role in value["roles"]])
        if max(len(encoded), len(json.dumps(future, ensure_ascii=False).encode("utf-8"))) > 1024 * 1024:
            raise ValueError("App route recovery record exceeds its limit")
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def apply(self):
        with self._owner._lock:
            if self._closed:
                raise RuntimeError("Application output route is closed")
            if self._applied:
                return
            self._verify_spare()
            # Failures while reading/preparing must produce zero route writes.
            for role in self._roles:
                self._fence(role)
                if _route_text(self._owner._adapter.get_route(role.identity.process_id, role.role)) != role.original:
                    raise RuntimeError("Application output changed before routing")
            self._save("prepared")
            try:
                for role in self._roles:
                    self._fence(role)
                    if role.original == role.owned_route:
                        role.restored = True
                        continue
                    role.intent = True
                    self._save("write_intent")
                    self._owner._adapter.set_route(role.identity.process_id, role.role, role.owned_route)
                    # The persisted pre-write intent is sufficient to recover
                    # a successful Set followed by a readback/parent failure.
                    if _route_text(self._owner._adapter.get_route(role.identity.process_id, role.role)) != role.owned_route:
                        raise RuntimeError("Windows did not confirm the temporary application output")
                    self._save("owned")
                self._applied = True
            except Exception:
                try:
                    self.restore()
                except Exception:
                    pass  # Durable pending journal carries the original values.
                raise

    def wait_migrated(self, *, timeout=5.0, poll_seconds=.1, cancelled=None):
        if not self._applied or self._closed:
            raise RuntimeError("Temporary application route has not been applied")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout)
                or not 0 <= timeout <= 30 or isinstance(poll_seconds, bool)
                or not isinstance(poll_seconds, (int, float)) or not math.isfinite(poll_seconds)
                or not 0 < poll_seconds <= 1 or (cancelled is not None and not callable(cancelled))):
            raise ValueError("A bounded migration wait is required")
        deadline = time.monotonic() + timeout
        while True:
            if cancelled is not None and cancelled():
                raise RuntimeError("Music output migration was cancelled")
            self.check()
            fresh = self._owner._resolver(self.source.source_app_id)
            # During a legitimate endpoint rebind GSMTC/audio sessions may
            # briefly have no active stream. The retained live process fence
            # still proves identity; silence alone is not a replacement app.
            if fresh is not None and not self._owner._same_source(self.source, fresh):
                raise RuntimeError("Music source changed during output migration")
            self._verify_spare()
            if (fresh is not None and fresh.sessions and set(fresh.session_process_ids).issubset(self._handles)
                    and all(session.endpoint_id == self.spare_endpoint for session in fresh.sessions)):
                with self._owner._lock:
                    if self._closed:
                        raise RuntimeError("Application output route closed during migration")
                    self._migrated = True
                    self._save("migrated")
                return fresh
            if time.monotonic() >= deadline:
                raise AppAudioRouteMigrationTimeout(self._migration_evidence(fresh))
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))

    def _migration_evidence(self, fresh):
        """Bounded scalar failure evidence; policy readback is never readiness."""
        endpoints = tuple(sorted({session.endpoint_id for session in fresh.sessions})) if fresh is not None else ()
        pids = tuple(sorted(set(fresh.session_process_ids))) if fresh is not None else ()
        original = tuple(sorted({session.endpoint_id for session in self.source.sessions}))
        unowned = tuple(pid for pid in pids if pid not in self._handles)
        if fresh is None or not fresh.sessions:
            reason = "no_active_source"
        elif unowned:
            reason = "unowned_audio_process"
        elif endpoints == original:
            reason = "playback_stayed_on_source"
        else:
            reason = "playback_on_other_output"
        return {"reason": reason, "target_endpoint": self.spare_endpoint,
                "observed_at": time.monotonic(), "source_process_id": self.source.process_id,
                "source_created_at_100ns": self.source.process_created_at_100ns,
                "original_endpoints": original, "observed_endpoints": endpoints[:128],
                "observed_process_ids": pids[:64], "unowned_process_ids": unowned[:64],
                "active_session_count": len(fresh.sessions) if fresh is not None else 0,
                "policy_confirmed": True, "playback_verified": False}

    def _observe_effective(self, endpoint):
        for role in self._roles:
            self._fence(role)
        fresh = self._owner._resolver(self.source.source_app_id)
        if fresh is not None and not self._owner._same_source(self.source, fresh):
            raise RuntimeError("Music source changed during playback restoration")
        if fresh is not None:
            if (fresh.sessions and set(fresh.session_process_ids).issubset(self._handles)
                    and all(session.endpoint_id == endpoint for session in fresh.sessions)):
                return "active_output_verified"
            return "unverified"
        # AudioSessionStateInactive retains at least one initialized stream,
        # but none is running. Expired contains no streams. Resolver(None)
        # alone cannot distinguish either from ambiguity or a rebind gap.
        selected = set(self._handles)
        observed_idle = False
        for endpoint_id, pid, single, system, state in self._owner._adapter.session_owners():
            if type(state) is not int or state not in (0, 1, 2):
                raise RuntimeError("Music playback session state is unknown")
            if state == 1 and (pid in selected or (not single and not system)):
                return "unverified"
            if pid in selected and not system and state in (0, 2):
                observed_idle = True
        return "policy_restored_no_active_stream" if observed_idle else "unverified"

    def _wait_effective(self, endpoint, timeout):
        deadline = time.monotonic() + timeout
        while True:
            observation = self._observe_effective(endpoint)
            if observation != "unverified":
                return observation
            if time.monotonic() >= deadline:
                raise RuntimeError("Original effective music playback output has not been restored")
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def _finish_restoration(self, state, *, deferred=False):
        self._restoration_state = state
        if deferred:
            self._save("resume_verification_pending")
        elif self._owner._journal.exists():
            self._owner._journal.unlink()
        self._close_handles()
        self._closed, self._migrated = True, False
        if self._owner._active is self:
            self._owner._active = None
        self._owner._release_journal()

    def restore(self, *, timeout=5.0):
        with self._owner._lock:
            if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                    or not math.isfinite(timeout) or not 0 <= timeout <= 30):
                raise ValueError("A bounded effective-playback restoration wait is required")
            if self._closed:
                return
            deadline = time.monotonic() + timeout
            failures = []
            restore_endpoint = ""
            restored_roles = []
            observation = "policy_restored"
            # An empty policy alone did not relocate the user's real stream in
            # a physical test. Explicitly rebind default-following owned roles
            # to their prior effective endpoint before clearing the policy.
            for role in reversed(self._roles):
                if not role.intent or role.restored or role.external_change:
                    continue
                try:
                    self._fence(role)
                    current = _route_text(self._owner._adapter.get_route(role.identity.process_id, role.role))
                    expected = role.original or _PREFIX + role.original_effective_endpoint + _RENDER_SUFFIX
                    if not role.original_effective_endpoint:
                        raise RuntimeError("Original effective music output is missing from recovery evidence")
                    if current not in (role.owned_route, role.original, role.restoring_route):
                        role.external_change = True
                        continue
                    role.restoring_route = expected
                    self._save("effective_restore_intent")
                    if current != expected:
                        self._owner._adapter.set_route(role.identity.process_id, role.role, expected)
                    if self._owner._adapter.get_route(role.identity.process_id, role.role) != expected:
                        raise RuntimeError("Windows did not confirm the original effective music output policy")
                    restore_endpoint = role.original_effective_endpoint
                except (OSError, ValueError, RuntimeError) as error:
                    failures.append(str(error))
            if restore_endpoint and not failures and not any(role.external_change for role in self._roles):
                try:
                    observation = self._wait_effective(restore_endpoint, max(0, deadline - time.monotonic()))
                except (OSError, ValueError, RuntimeError) as error:
                    failures.append(str(error))
            if failures:
                self._save("effective_recovery_required")
                raise RuntimeError("; ".join(dict.fromkeys(failures)))
            for role in reversed(self._roles):
                if not role.intent or role.restored or role.external_change:
                    continue
                try:
                    self._fence(role)
                    current = _route_text(self._owner._adapter.get_route(role.identity.process_id, role.role))
                    if current == role.original:
                        restored_roles.append(role)
                    elif current not in (role.owned_route, role.restoring_route):
                        role.external_change = True  # Retain the user's newer choice.
                    else:
                        self._owner._adapter.set_route(role.identity.process_id, role.role, role.original)
                        if self._owner._adapter.get_route(role.identity.process_id, role.role) != role.original:
                            raise RuntimeError("Windows did not confirm restored application output")
                        restored_roles.append(role)
                except (OSError, ValueError, RuntimeError) as error:
                    failures.append(str(error))
            if failures:
                self._save("recovery_required")
                raise RuntimeError("; ".join(dict.fromkeys(failures)))
            # Policy clearing can rebind a live stream again. Preserve intent
            # until the actual effective output is also proven after Clear.
            if restore_endpoint and not any(role.external_change for role in self._roles):
                try:
                    observation = self._wait_effective(restore_endpoint, max(0, deadline - time.monotonic()))
                except (OSError, ValueError, RuntimeError):
                    self._save("effective_recovery_required")
                    raise
            for role in restored_roles:
                role.restored = True
            if any(role.external_change for role in self._roles):
                observation = "external_output_preserved"
            self._finish_restoration(observation,
                deferred=observation == "policy_restored_no_active_stream")

    def check(self, *, owned_process=None):
        """Validate the active transaction without changing routing or audio."""
        with self._owner._lock:
            if self._closed or not self._applied:
                raise RuntimeError("Temporary application audio route is not active")
            for role in self._roles:
                self._fence(role)
                if self._owner._adapter.get_route(role.identity.process_id, role.role) != role.owned_route:
                    raise RuntimeError("Application output changed during migration")
            self._verify_spare(owned_process=owned_process)

    def _close_handles(self):
        for handle in self._handles.values():
            self._owner._adapter.close_handle(handle)
        self._handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.restore()


class _WindowsAppRouteAdapter:
    def __init__(self):
        self._sessions = _WindowsAudioSessionAdapter()
        self._combase = ctypes.WinDLL("combase", use_last_error=True)
        self._combase.RoInitialize.argtypes = [_DWORD]
        self._combase.RoInitialize.restype = _HRESULT
        self._combase.RoUninitialize.argtypes = []
        self._combase.WindowsCreateString.argtypes = [ctypes.c_wchar_p, _DWORD, ctypes.POINTER(ctypes.c_void_p)]
        self._combase.WindowsCreateString.restype = _HRESULT
        self._combase.WindowsDeleteString.argtypes = [ctypes.c_void_p]
        self._combase.WindowsGetStringRawBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DWORD)]
        self._combase.WindowsGetStringRawBuffer.restype = ctypes.c_wchar_p
        self._combase.RoGetActivationFactory.argtypes = [ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self._combase.RoGetActivationFactory.restype = _HRESULT

    def process_identity(self, pid):
        return self._sessions.process_identity(pid)

    def hold_process(self, identity):
        kernel = self._sessions._kernel
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, identity.process_id)
        if not handle:
            raise OSError("Cannot hold the exact music process")
        if self.process_identity(identity.process_id) != identity:
            kernel.CloseHandle(handle)
            raise RuntimeError("Music process creation changed while acquiring route ownership")
        return handle

    def close_handle(self, handle):
        self._sessions._kernel.CloseHandle(handle)

    def _policy_call(self, pid, role, value=None, *, write=False):
        if role not in (0, 1):
            raise ValueError("Only console and multimedia music roles are supported")
        library, factory, strings = self._combase, ctypes.c_void_p(), []
        hr = library.RoInitialize(1)
        _check(hr, "App routing RoInitialize")
        try:
            def hstring(text):
                raw = ctypes.c_void_p()
                _check(library.WindowsCreateString(text, len(text), ctypes.byref(raw)), "App routing HSTRING")
                strings.append(raw)
                return raw
            runtime = hstring("Windows.Media.Internal.AudioPolicyConfig")
            for iid in ("ab3d4648-e242-459f-b02f-541c70306324", "2a59116d-6c4f-45e0-a74f-707e3fef9258"):
                hr = library.RoGetActivationFactory(runtime, ctypes.byref(_GUID.from_string(iid)), ctypes.byref(factory))
                if hr >= 0:
                    break
            _check(hr, "Windows app routing activation")
            if write:
                raw = hstring(_route_text(value)) if value else ctypes.c_void_p()
                _check(_call(factory, 25, (_DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_void_p),
                             pid, 0, role, raw), "SetPersistedDefaultAudioEndpoint")
                return None
            raw = ctypes.c_void_p()
            _check(_call(factory, 26, (_DWORD, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                         pid, 0, role, ctypes.byref(raw)), "GetPersistedDefaultAudioEndpoint")
            strings.append(raw)
            length = _DWORD()
            text = library.WindowsGetStringRawBuffer(raw, ctypes.byref(length))
            return _route_text(text or "")
        finally:
            if factory:
                _call(factory, 2, (), result_type=_DWORD)
            for raw in reversed(strings):
                if raw:
                    library.WindowsDeleteString(raw)
            library.RoUninitialize()

    def get_route(self, pid, role):
        return self._policy_call(pid, role)

    def set_route(self, pid, role, value):
        self._policy_call(pid, role, value, write=True)

    def active_owners(self):
        return tuple((endpoint, pid, single, system)
                     for endpoint, pid, single, system, state in self.session_owners(include_inactive=False))

    def session_owners(self, *, include_inactive=True):
        # Keep system/ambiguous sessions in this inventory: they are excluded
        # from capture resolution but still make an endpoint unsafe to mute.
        core, result = self._sessions._core, []
        with _ComScope(core._ole32) as scope:
            enumerator = scope.create("bcde0395-e52f-467c-8e3d-c4579291692e", "a95664d2-9614-4f35-a746-de8db63617e6")
            devices = ctypes.c_void_p()
            _check(_call(enumerator, 3, (ctypes.c_int, _DWORD, ctypes.POINTER(ctypes.c_void_p)),
                         0, 1, ctypes.byref(devices)), "App route render endpoint inventory")
            scope.own(devices)
            count = _DWORD()
            _check(_call(devices, 3, (ctypes.POINTER(_DWORD),), ctypes.byref(count)), "App route endpoint count")
            if count.value > 128:
                raise RuntimeError("App route endpoint inventory exceeds limit")
            for index in range(count.value):
                device, manager, sessions = (ctypes.c_void_p() for _ in range(3))
                _check(_call(devices, 4, (_DWORD, ctypes.POINTER(ctypes.c_void_p)), index, ctypes.byref(device)), "App route endpoint")
                scope.own(device)
                endpoint = core._device_id(device)
                _check(_call(device, 3, (ctypes.POINTER(_GUID), _DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                             ctypes.byref(_GUID.from_string(self._sessions._IID_MANAGER)), 23, None,
                             ctypes.byref(manager)), "App route session manager")
                scope.own(manager)
                _check(_call(manager, 5, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(sessions)), "App route sessions")
                scope.own(sessions)
                session_count = ctypes.c_int()
                _check(_call(sessions, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(session_count)), "App route session count")
                if not 0 <= session_count.value <= 1024:
                    raise RuntimeError("App route session inventory exceeds limit")
                for slot in range(session_count.value):
                    control, extended = ctypes.c_void_p(), ctypes.c_void_p()
                    _check(_call(sessions, 4, (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)), slot, ctypes.byref(control)), "App route session")
                    scope.own(control)
                    state = ctypes.c_int()
                    _check(_call(control, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(state)), "App route session state")
                    if state.value not in (0, 1, 2):
                        raise RuntimeError("App route session state is unknown")
                    if not include_inactive and state.value != 1:
                        # Endpoint occupancy must not depend on a stale,
                        # disconnected inactive session's process query.
                        continue
                    _check(_call(control, 0, (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
                                 ctypes.byref(_GUID.from_string(self._sessions._IID_CONTROL2)), ctypes.byref(extended)), "App route extended session")
                    scope.own(extended)
                    pid = _DWORD()
                    hr = _call(extended, 14, (ctypes.POINTER(_DWORD),), ctypes.byref(pid))
                    _check(hr, "App route session process")
                    system = _call(extended, 15, ())
                    if system not in (0, 1):
                        _check(system, "App route system sounds state")
                        raise RuntimeError("App route system sounds state is unknown")
                    result.append((endpoint, pid.value, hr == 0, system == 0, state.value))
        return tuple(result)
