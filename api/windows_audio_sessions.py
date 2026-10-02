"""Read-only evidence joining a selected GSMTC app to its render sessions.

GSMTC exposes an application identity, not a PID. Audio sessions supply the
actual playback PID; process image/AUMID and creation times identify a unique
matching process tree. No foreground-window, display-name or brand guessing is
used. This module opens no audio stream and changes no endpoint/session state.

COM slots and IIDs are checked against Windows SDK audiopolicy.h; process
metadata uses QueryFullProcessImageNameW, GetProcessTimes,
GetApplicationUserModelId and Toolhelp32's PROCESSENTRY32W.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import ntpath
import os
import sys
import time

from api.windows_audio_devices import (
    _ComScope, _DWORD, _GUID, _IID_ENUMERATOR, _MM_ENUMERATOR,
    _WindowsCoreAudioAdapter, _call, _check,
)


@dataclass(frozen=True)
class AudioSessionEvidence:
    endpoint_id: str
    process_id: int
    session_identifier: str
    session_instance_id: str
    active: bool = True
    system_sounds: bool = False
    single_process: bool = True


@dataclass(frozen=True)
class ProcessIdentity:
    process_id: int
    process_created_at_100ns: int
    process_path: str
    application_user_model_id: str = ""


@dataclass(frozen=True)
class MusicAudioSource:
    source_app_id: str
    process_id: int
    process_created_at_100ns: int
    process_path: str
    session_process_ids: tuple[int, ...]
    session_identifiers: tuple[str, ...]
    sessions: tuple[AudioSessionEvidence, ...] = ()
    observed_at: float = 0.0


def _valid_pid(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 0xffffffff


def _valid_identity(value, process_id):
    return (isinstance(value, ProcessIdentity) and value.process_id == process_id
            and _valid_pid(process_id)
            and isinstance(value.process_created_at_100ns, int)
            and not isinstance(value.process_created_at_100ns, bool)
            and 0 < value.process_created_at_100ns < 2**64
            and isinstance(value.process_path, str) and "\0" not in value.process_path
            and ntpath.isabs(value.process_path))


def _source_matcher(source_app_id):
    if not isinstance(source_app_id, str) or not source_app_id or "\0" in source_app_id:
        return None
    if source_app_id != source_app_id.strip() or len(source_app_id) > 32768:
        return None
    # Windows paths/basenames are case insensitive; packaged AUMIDs are joined
    # by their exact published identity rather than inferred from package names.
    if "!" in source_app_id:
        return lambda identity: identity.application_user_model_id == source_app_id
    if ntpath.isabs(source_app_id) and ntpath.basename(source_app_id).casefold().endswith(".exe"):
        wanted = ntpath.normpath(source_app_id).casefold()
        return lambda identity: ntpath.normpath(identity.process_path).casefold() == wanted
    if (ntpath.basename(source_app_id) == source_app_id
            and source_app_id.casefold().endswith(".exe") and len(source_app_id) > 4):
        wanted = source_app_id.casefold()
        return lambda identity: ntpath.basename(identity.process_path).casefold() == wanted
    return None


def list_music_audio_sessions(*, adapter=None) -> tuple[AudioSessionEvidence, ...]:
    """Return active non-system render-session evidence, without capturing PCM.

    ``adapter`` supplies list_sessions(), process_identity(pid) and
    process_parents() for focused tests. The native adapter is created only on
    Windows and COM objects never escape its calling thread.
    """
    if adapter is None:
        if sys.platform != "win32":
            return ()
        adapter = _WindowsAudioSessionAdapter()
    result, seen = [], set()
    for session in adapter.list_sessions():
        if (not isinstance(session, AudioSessionEvidence) or session.active is not True
                or session.system_sounds is not False or session.single_process is not True
                or not _valid_pid(session.process_id) or session.process_id == os.getpid()
                or not isinstance(session.endpoint_id, str) or not session.endpoint_id
                or not isinstance(session.session_identifier, str) or not session.session_identifier
                or not isinstance(session.session_instance_id, str) or not session.session_instance_id
                or any("\0" in value for value in (session.endpoint_id,
                           session.session_identifier, session.session_instance_id))):
            continue
        key = (session.endpoint_id, session.session_instance_id)
        if key not in seen:
            result.append(session)
            seen.add(key)
    return tuple(result)


def resolve_music_audio_source(source_app_id: str, *, adapter=None) -> MusicAudioSource | None:
    """Resolve one uniquely evidenced playback tree, or fail closed.

    A child audio process can map to a matching parent app. PID reuse is checked
    through parent/child creation ordering and a final root read. Consumers must
    also validate the returned creation token when opening/holding the native
    capture process handle; this snapshot does not reserve a PID.
    """
    matches = _source_matcher(source_app_id)
    if matches is None:
        return None
    try:
        if adapter is None:
            if sys.platform != "win32":
                return None
            adapter = _WindowsAudioSessionAdapter()
        sessions = list_music_audio_sessions(adapter=adapter)
        if not sessions:
            return None
        parents = adapter.process_parents()
        if not isinstance(parents, dict):
            return None
        identities = {}

        def identity_for(process_id):
            if process_id not in identities:
                value = adapter.process_identity(process_id)
                identities[process_id] = value if _valid_identity(value, process_id) else None
            return identities[process_id]

        grouped = {}
        for session in sessions:
            process_id, seen = session.process_id, set()
            current, chosen = identity_for(process_id), None
            if current is None:
                continue
            for _depth in range(64):
                if current.process_id in seen or current.process_id == os.getpid():
                    # Never capture our own tree even when a caller's app ID
                    # matches an executable used by GluSpot.
                    chosen = None
                    break
                seen.add(current.process_id)
                if matches(current):
                    chosen = current
                parent_id = parents.get(current.process_id)
                if not _valid_pid(parent_id) or parent_id == current.process_id:
                    break
                parent = identity_for(parent_id)
                if parent is None or parent.process_created_at_100ns > current.process_created_at_100ns:
                    # Toolhelp's numeric parent may already belong to a new
                    # process. It cannot authorize expanding the capture tree.
                    break
                current = parent
            else:
                chosen = None  # An unbounded/cyclic ancestry is not authority.
            if chosen is not None:
                key = (chosen.process_id, chosen.process_created_at_100ns)
                grouped.setdefault(key, (chosen, []))[1].append(session)
        if len(grouped) != 1:
            return None
        root, selected_sessions = next(iter(grouped.values()))
        final = adapter.process_identity(root.process_id)
        if (not _valid_identity(final, root.process_id) or final != root
                or not matches(final)):
            return None
        return MusicAudioSource(
            source_app_id, root.process_id, root.process_created_at_100ns,
            root.process_path, tuple(sorted({s.process_id for s in selected_sessions})),
            tuple(s.session_identifier for s in selected_sessions),
            tuple(selected_sessions), time.monotonic(),
        )
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        # Missing/protected/changed source evidence must not widen capture to
        # all computer sound or borrow the currently focused application's PID.
        return None


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", _DWORD), ("high", _DWORD)]

    def value(self):
        return (int(self.high) << 32) | int(self.low)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", _DWORD), ("cntUsage", _DWORD), ("th32ProcessID", _DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", _DWORD),
                ("cntThreads", _DWORD), ("th32ParentProcessID", _DWORD),
                ("pcPriClassBase", ctypes.c_int32), ("dwFlags", _DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


class _WindowsAudioSessionAdapter:
    _IID_MANAGER = "77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f"
    _IID_CONTROL2 = "bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"

    def __init__(self):
        self._core = _WindowsCoreAudioAdapter()
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.OpenProcess.argtypes = [_DWORD, ctypes.c_int32, _DWORD]
        self._kernel.OpenProcess.restype = ctypes.c_void_p
        self._kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel.CloseHandle.restype = ctypes.c_int32
        self._kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, _DWORD,
                                                         ctypes.c_wchar_p, ctypes.POINTER(_DWORD)]
        self._kernel.QueryFullProcessImageNameW.restype = ctypes.c_int32
        self._kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(_FILETIME)] * 4
        self._kernel.GetProcessTimes.restype = ctypes.c_int32
        self._kernel.CreateToolhelp32Snapshot.argtypes = [_DWORD, _DWORD]
        self._kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        for name in ("Process32FirstW", "Process32NextW"):
            method = getattr(self._kernel, name)
            method.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
            method.restype = ctypes.c_int32
        self._get_app_id = getattr(self._kernel, "GetApplicationUserModelId", None)
        if self._get_app_id is not None:
            self._get_app_id.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DWORD), ctypes.c_wchar_p]
            self._get_app_id.restype = ctypes.c_int32

    def _session_text(self, session, slot):
        text = ctypes.c_void_p()
        _check(_call(session, slot, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(text)),
               "IAudioSessionControl2.GetSessionIdentifier")
        try:
            return ctypes.wstring_at(text) if text else ""
        finally:
            self._core._ole32.CoTaskMemFree(text)

    def list_sessions(self):
        result = []
        with _ComScope(self._core._ole32) as scope:
            enumerator = scope.create(_MM_ENUMERATOR, _IID_ENUMERATOR)
            collection = ctypes.c_void_p()
            _check(_call(enumerator, 3, (ctypes.c_int, _DWORD, ctypes.POINTER(ctypes.c_void_p)),
                         0, 1, ctypes.byref(collection)), "IMMDeviceEnumerator.EnumAudioEndpoints")
            scope.own(collection)
            count = _DWORD()
            _check(_call(collection, 3, (ctypes.POINTER(_DWORD),), ctypes.byref(count)),
                   "IMMDeviceCollection.GetCount")
            if count.value > 128:
                raise RuntimeError("Audio endpoint snapshot exceeds its evidence limit")
            for index in range(count.value):
                # A session or output can disappear during a snapshot. Keep
                # verified peers while skipping only the vanished endpoint.
                try:
                    device = ctypes.c_void_p()
                    _check(_call(collection, 4, (_DWORD, ctypes.POINTER(ctypes.c_void_p)),
                                 index, ctypes.byref(device)), "IMMDeviceCollection.Item")
                    scope.own(device)
                    endpoint_id = self._core._device_id(device)
                    manager = ctypes.c_void_p()
                    _check(_call(device, 3, (ctypes.POINTER(_GUID), _DWORD, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p)),
                                 ctypes.byref(_GUID.from_string(self._IID_MANAGER)), 23, None,
                                 ctypes.byref(manager)), "IMMDevice.Activate(IAudioSessionManager2)")
                    scope.own(manager)
                    sessions = ctypes.c_void_p()
                    _check(_call(manager, 5, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(sessions)),
                           "IAudioSessionManager2.GetSessionEnumerator")
                    scope.own(sessions)
                    session_count = ctypes.c_int()
                    _check(_call(sessions, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(session_count)),
                           "IAudioSessionEnumerator.GetCount")
                    if not 0 <= session_count.value <= 1024:
                        raise RuntimeError("Audio session snapshot exceeds its evidence limit")
                    for position in range(session_count.value):
                        try:
                            control = ctypes.c_void_p()
                            _check(_call(sessions, 4, (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                                         position, ctypes.byref(control)), "IAudioSessionEnumerator.GetSession")
                            scope.own(control)
                            state = ctypes.c_int()
                            _check(_call(control, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(state)),
                                   "IAudioSessionControl.GetState")
                            if state.value != 1:  # AudioSessionStateActive
                                continue
                            control2 = ctypes.c_void_p()
                            _check(_call(control, 0, (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
                                         ctypes.byref(_GUID.from_string(self._IID_CONTROL2)),
                                         ctypes.byref(control2)), "IAudioSessionControl.QueryInterface")
                            scope.own(control2)
                            # S_OK identifies system sounds; S_FALSE is an
                            # ordinary session. Any other result is uncertain.
                            if _call(control2, 15, ()) != 1:
                                continue
                            process_id = _DWORD()
                            if _call(control2, 14, (ctypes.POINTER(_DWORD),), ctypes.byref(process_id)) != 0:
                                # AUDCLNT_S_NO_SINGLE_PROCESS is successful but
                                # supplies only a creator PID for a shared
                                # cross-process session: it is not a source.
                                continue
                            result.append(AudioSessionEvidence(
                                endpoint_id, int(process_id.value), self._session_text(control2, 12),
                                self._session_text(control2, 13),
                            ))
                        except (OSError, ValueError):
                            continue
                except (OSError, ValueError):
                    continue
        return tuple(result)

    def process_identity(self, process_id):
        if not _valid_pid(process_id):
            return None
        handle = self._kernel.OpenProcess(0x1000, False, process_id)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = _DWORD(len(path))
            if not self._kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                return None
            created, exited, kernel_time, user_time = (_FILETIME() for _ in range(4))
            if not self._kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                               ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            if exited.value():
                return None
            app_id = ""
            if self._get_app_id is not None:
                app_length = _DWORD()
                if self._get_app_id(handle, ctypes.byref(app_length), None) == 122 and 0 < app_length.value <= 32768:
                    app_buffer = ctypes.create_unicode_buffer(app_length.value)
                    if self._get_app_id(handle, ctypes.byref(app_length), app_buffer) == 0:
                        app_id = app_buffer.value
            return ProcessIdentity(process_id, created.value(), path.value, app_id)
        finally:
            self._kernel.CloseHandle(handle)

    def process_parents(self):
        snapshot = self._kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if snapshot == ctypes.c_void_p(-1).value or snapshot is None:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        try:
            entry, parents = _PROCESSENTRY32W(), {}
            entry.dwSize = ctypes.sizeof(entry)
            available = self._kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while available and len(parents) < 65536:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                available = self._kernel.Process32NextW(snapshot, ctypes.byref(entry))
            if available:
                raise RuntimeError("Process snapshot exceeds its evidence limit")
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise OSError(ctypes.get_last_error(), "Process enumeration failed")
            return parents
        finally:
            self._kernel.CloseHandle(snapshot)
