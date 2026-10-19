"""Authenticated local PCM transport into the existing coordinated sender."""
from __future__ import annotations

from collections import deque
import ctypes
import ipaddress
import json
import math
import os
import queue
import re
import secrets
import struct
import subprocess
import threading
import time

from api.airplay_group_process import AirPlayGroupProcess
from api.audio_routes import AudioRouteDevice
from api.windows_audio_sessions import _WindowsAudioSessionAdapter, _valid_identity


class _WindowsPcmPipe:
    """A random local pipe is additionally bound to the child process handle."""
    def __init__(self, name, process, identity_reader, stopped):
        from ctypes import wintypes
        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]
        self._overlapped_type = Overlapped
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        self._kernel.CreateFileW.restype = ctypes.c_void_p
        self._kernel.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._kernel.GetNamedPipeServerProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG)]
        self._kernel.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
        self._kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateEventW.restype = wintypes.HANDLE
        self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self._closed = threading.Event()
        self._stopped = stopped
        self._handle = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and process.poll() is None and not stopped.is_set():
            handle = self._kernel.CreateFileW("\\\\.\\pipe\\" + name, 0x40000000, 0, None, 3, 0x40000000, None)
            if handle != ctypes.c_void_p(-1).value:
                server = wintypes.ULONG()
                proof = identity_reader()
                if (self._kernel.GetNamedPipeServerProcessId(handle, ctypes.byref(server))
                        and server.value == process.pid and proof is not None and proof.process_id == process.pid
                        and process.poll() is None and not stopped.is_set()):
                    self._handle = handle
                    return
                self._kernel.CloseHandle(handle)
                raise RuntimeError("pipe_identity")
            stopped.wait(.025)
        raise RuntimeError("pipe_timeout")

    def write(self, data):
        from ctypes import wintypes
        offset = 0
        while offset < len(data):
            handle = self._handle
            if handle is None or self._closed.is_set() or self._stopped.is_set():
                raise OSError("pipe_closed")
            raw = ctypes.create_string_buffer(data[offset:])
            written = wintypes.DWORD()
            event = self._kernel.CreateEventW(None, True, False, None)
            if not event:
                raise OSError("pipe_event")
            operation = self._overlapped_type()
            operation.hEvent = event
            try:
                if not self._kernel.WriteFile(handle, raw, len(data) - offset, None, ctypes.byref(operation)):
                    if ctypes.get_last_error() != 997:  # ERROR_IO_PENDING
                        raise OSError("pipe_write")
                    # The buffer and OVERLAPPED remain owned until completion,
                    # including cancelled writes. No handle closes/reuses while
                    # another thread still references its pending operation.
                    while True:
                        waited = self._kernel.WaitForSingleObject(event, 50)
                        if waited == 0:
                            break
                        if waited != 258:
                            # Even an unexpected wait failure must complete
                            # cancellation before releasing OVERLAPPED/buffer.
                            self._kernel.CancelIoEx(handle, ctypes.byref(operation))
                            self._kernel.GetOverlappedResult(handle, ctypes.byref(operation), ctypes.byref(written), True)
                            raise OSError("pipe_wait")
                        if self._closed.is_set() or self._stopped.is_set():
                            self._kernel.CancelIoEx(handle, ctypes.byref(operation))
                if not self._kernel.GetOverlappedResult(handle, ctypes.byref(operation), ctypes.byref(written), False) or not written.value:
                    raise OSError("pipe_write")
                offset += written.value
            finally:
                self._kernel.CloseHandle(event)

    def close(self):
        self._closed.set()
        handle = self._handle
        if handle is not None:
            self._kernel.CancelIoEx(handle, None)

    def dispose(self):
        # Called by the writer only after all OVERLAPPED operations completed.
        handle, self._handle = self._handle, None
        if handle is not None:
            self._kernel.CloseHandle(handle)


class OwnedPcmProcess(AirPlayGroupProcess):
    """The process-mode PID/session guards remain entirely outside this path."""
    MAX_PACKET_FRAMES = 16384
    MAX_QUEUED_BYTES = 352800  # At most two seconds of stereo 44.1 kHz PCM.
    HEADER = struct.Struct("<4sQQI")

    def __init__(self, *args, identity_reader=None, pipe_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._parent_identity_reader = identity_reader or (lambda: _WindowsAudioSessionAdapter().process_identity(os.getpid()))
        self._pipe_factory = pipe_factory or _WindowsPcmPipe
        self._pcm_epoch = 0
        self._pcm_queue = queue.Queue(maxsize=64)
        self._pcm_lock = threading.Lock()
        self._pcm_bytes = 0
        self._pcm_error = ""
        self._pcm_stop = threading.Event()
        self._pcm_thread = None
        self._pcm_pipe = None
        self._pcm_pipe_epoch = 0
        self._pcm_membership_supported = False
        self._pcm_presentation_supported = False
        self._membership_revision = 0
        self._membership_pending_revision = 0
        self._pcm_devices = ()
        self._membership_requested = ()
        self._membership_evidence = {}

    @classmethod
    def _validate_pcm_devices(cls, devices):
        if not isinstance(devices, (tuple, list)) or not 1 <= len(devices) <= cls.MAX_SINKS:
            raise ValueError("invalid_pcm_members")
        receivers, endpoints, identities, targets = [], [], set(), set()
        for device in devices:
            if (not isinstance(device, AudioRouteDevice) or not cls._text(device.id, 2048)
                    or device.id in identities or device.available is not True):
                raise ValueError("invalid_pcm_member")
            identities.add(device.id)
            if device.transport == "windows":
                folded = cls._endpoint_key(device.id)
                if folded in targets:
                    raise ValueError("duplicate_pcm_endpoint")
                targets.add(folded)
                endpoints.append(device.id)
            elif device.transport == "airplay":
                try:
                    address = ipaddress.ip_address(device.host)
                except ValueError:
                    raise ValueError("invalid_pcm_receiver") from None
                if (address.version != 4 or address.is_unspecified or address.is_multicast
                        or isinstance(device.port, bool) or not isinstance(device.port, int)
                        or not 1 <= device.port <= 65535 or not cls._text(device.name, 1024)
                        or not cls._text(device.device_id or device.host, 1024)):
                    raise ValueError("invalid_pcm_receiver")
                target = (str(address), device.port)
                if target in targets:
                    raise ValueError("duplicate_pcm_receiver")
                targets.add(target)
                receivers.append(device)
            else:
                raise ValueError("invalid_pcm_transport")
        return tuple(receivers), tuple(endpoints)

    def start_pcm(self, devices, epoch):
        if isinstance(epoch, bool) or not isinstance(epoch, int) or not 0 < epoch < 2**64:
            raise ValueError("invalid_pcm_epoch")
        receivers, endpoints = self._validate_pcm_devices(devices)
        identity = self._parent_identity_reader()
        if not _valid_identity(identity, os.getpid()):
            raise RuntimeError("pcm_parent_identity")
        if self._source_id and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", self._source_id):
            raise ValueError("invalid_pcm_sender_identity")
        self.stop()
        if not self.executable.is_file():
            raise RuntimeError("pcm_helper_missing")
        name = "GluSpotPCM-" + secrets.token_hex(16)
        args = [str(self.executable), "--group", "--pcm-pipe", name, "--pcm-parent-pid", str(identity.process_id),
            "--pcm-parent-created-at", str(identity.process_created_at_100ns), "--pcm-epoch", str(epoch)]
        if self._source_id:
            args += ["--source-id", self._source_id]
        for endpoint in endpoints:
            args += ["--local-endpoint", endpoint]
        for device in receivers:
            args += ["--receiver-id", device.id, "--host", device.host, "--port", str(device.port),
                "--device-id", device.device_id or device.host, "--name", device.name]
        with self._diagnostic_lock:
            self._last_stats, self._last_terminal, self._mute_state = {}, {}, {}
            self._last_stats_at = 0.0
            self._stderr_tail = deque(maxlen=24)
            self._receiver_connected = False
            self._receiver_ids = tuple(device.id for device in receivers)
            self._local_endpoints = tuple(endpoints)
            self._member_evidence, self._local_evidence = {}, {}
            self._group_source = None
            self._group_source_endpoint = ""
            self._pcm_membership_supported = False
            self._pcm_presentation_supported = False
            self._membership_revision = self._membership_pending_revision = 0
            self._pcm_devices = tuple(devices)
            self._membership_requested = ()
            self._membership_evidence = {}
        self.needs_local_output_restore = False
        self._pcm_epoch = epoch
        self._pcm_stop = threading.Event()
        self._pcm_queue = queue.Queue(maxsize=64)
        self._pcm_bytes, self._pcm_error = 0, ""
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = self._popen(args, **kwargs)
        self._bind_owned_process_identity()
        self._started_at, self._stopped_at = time.monotonic(), 0.0
        self._streaming = self._terminal_seen = False
        self._events = queue.Queue(maxsize=32)
        state = self._mute_state
        self._reader = threading.Thread(target=self._read_events, args=(self._process, self._events,
            lambda event: self._remember_native_event(state, event)), daemon=True, name="GluSpotOwnedOutputStatus")
        self._stderr_reader = threading.Thread(target=self._read_stderr, args=(self._process, self._stderr_tail), daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        self._pcm_thread = threading.Thread(target=self._write_pcm, args=(name, self._process, epoch,
            self._pcm_queue, self._pcm_stop), daemon=True, name="GluSpotOwnedPCM")
        self._pcm_thread.start()

    def supports_pcm_reconfigure(self, epoch):
        with self._diagnostic_lock:
            return bool(type(epoch) is int and self._pcm_membership_supported and epoch == self._pcm_epoch
                and self._process is not None and not self._pcm_stop.is_set() and not self._last_terminal)

    def supports_owned_presentation(self, epoch):
        with self._diagnostic_lock:
            return bool(type(epoch) is int and self._pcm_presentation_supported and epoch == self._pcm_epoch
                and self._process is not None and not self._pcm_stop.is_set() and not self._last_terminal)

    @staticmethod
    def _device_target(device):
        return (device.transport, device.id) if device.transport == "windows" else (
            device.transport, device.host, device.port, device.device_id or device.host)

    def reconfigure_pcm(self, devices, epoch):
        """Queue an authenticated helper's bounded same-timeline change.

        False explicitly means unsupported, busy, stale or invalid; it never
        stops/restarts the current pipe or claims a receiver has connected.
        Completion is the revision-fenced membership event from the helper.
        """
        if isinstance(epoch, bool) or not isinstance(epoch, int) or not 0 < epoch < 2**64:
            return False
        try:
            receivers, endpoints = self._validate_pcm_devices(devices)
        except (TypeError, ValueError):
            return False
        with self._command_lock:
            with self._diagnostic_lock:
                if (not self._pcm_membership_supported or epoch != self._pcm_epoch
                        or self._process is None or self._pcm_stop.is_set() or self._last_terminal
                        or self._membership_pending_revision or self._membership_revision >= 2**64 - 1):
                    return False
                previous = {d.id: d for d in self._pcm_devices}
                desired = {d.id: d for d in devices}
                if len(previous.keys() | desired.keys()) > self.MAX_SINKS or any(
                        self._device_target(previous[key]) != self._device_target(desired[key])
                        for key in previous.keys() & desired.keys()):
                    return False
                # The native stage also bounds live transports. Duplicate
                # physical targets cannot hide behind a new public identity.
                union = dict(previous); union.update(desired)
                try:
                    self._validate_pcm_devices(tuple(union.values()))
                except ValueError:
                    return False
                arguments = []
                for endpoint in endpoints:
                    arguments += ["--local-endpoint", endpoint]
                for device in receivers:
                    arguments += ["--receiver-id", device.id, "--host", device.host, "--port", str(device.port),
                        "--device-id", device.device_id or device.host, "--name", device.name]
                revision = self._membership_revision + 1
                command = json.dumps({"command": "reconfigure_pcm", "epoch": epoch, "revision": revision,
                    "outputs_hex": b"\0".join(part.encode("utf-8") for part in arguments).hex()},
                    separators=(",", ":")) + "\n"
                if len(command) > 131072:
                    return False
                process = self._process
                self._membership_revision = self._membership_pending_revision = revision
                self._membership_requested = tuple(devices)
                # This matches the helper's retained-then-added transport order,
                # including the receiver index used by explicit volume commands.
                self._receiver_ids += tuple(d.id for d in receivers if d.id not in previous)
                self._local_endpoints += tuple(e for e in endpoints if e not in previous)
            try:
                if process.poll() is not None:
                    raise OSError("pcm_helper_ended")
                process.stdin.write(command); process.stdin.flush()
                return True
            except (OSError, ValueError):
                with self._diagnostic_lock:
                    self._membership_pending_revision = 0
                    self._membership_requested = ()
                    self._receiver_ids = tuple(d.id for d in self._pcm_devices if d.transport == "airplay")
                    self._local_endpoints = tuple(d.id for d in self._pcm_devices if d.transport == "windows")
                return False

    def membership_state(self):
        with self._diagnostic_lock:
            return dict(self._membership_evidence)

    def set_device_volume(self, receiver_id, volume):
        if not self.supports_pcm_reconfigure(self._pcm_epoch):
            return super().set_device_volume(receiver_id, volume)
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or not 0 <= volume <= 1:
            return False
        with self._command_lock:
            with self._diagnostic_lock:
                if (receiver_id not in {d.id for d in self._pcm_devices if d.transport == "airplay"}
                        or self._process is None or self._last_terminal or self._pcm_stop.is_set()):
                    return False
                process = self._process
                command = json.dumps({"command": "set_owned_volume", "epoch": self._pcm_epoch,
                    "receiver_hex": receiver_id.encode("utf-8").hex(), "volume": volume}, separators=(",", ":")) + "\n"
            try:
                if process.poll() is not None:
                    return False
                process.stdin.write(command); process.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def submit_pcm(self, content, epoch):
        if (isinstance(epoch, bool) or not isinstance(epoch, int) or epoch != self._pcm_epoch
                or self._process is None or self._pcm_stop.is_set()
                or not isinstance(content, bytes) or not content or len(content) % 4
                or len(content) > self.MAX_PACKET_FRAMES * 4):
            return False
        with self._pcm_lock:
            # Stop can win after the first fast fence while this submitter is
            # waiting for the lock. Never append audio back into a retired queue.
            if self._pcm_stop.is_set() or epoch != self._pcm_epoch or self._process is None:
                return False
            if self._pcm_error or self._pcm_bytes + len(content) > self.MAX_QUEUED_BYTES:
                self._pcm_error = "backpressure"
                return False
            try:
                self._pcm_queue.put_nowait(content)
            except queue.Full:
                self._pcm_error = "backpressure"
                return False
            self._pcm_bytes += len(content)
        return True

    def _write_pcm(self, name, process, epoch, pending, stopped):
        pipe = None
        try:
            pipe = self._pipe_factory(name, process, self.owned_process_identity, stopped)
            with self._pcm_lock:
                if stopped.is_set() or epoch != self._pcm_epoch or pending is not self._pcm_queue:
                    return
                self._pcm_pipe, self._pcm_pipe_epoch = pipe, epoch
            sequence = 0
            while not stopped.is_set():
                try:
                    content = pending.get(timeout=.1)
                except queue.Empty:
                    continue
                if stopped.is_set() or epoch != self._pcm_epoch:
                    break
                with self._pcm_lock:
                    if epoch == self._pcm_epoch:
                        self._pcm_bytes = max(0, self._pcm_bytes - len(content))
                if stopped.is_set() or epoch != self._pcm_epoch:
                    break
                sequence += 1
                pipe.write(self.HEADER.pack(b"GPCM", epoch, sequence, len(content) // 4) + content)
        except Exception:
            if not stopped.is_set() and epoch == self._pcm_epoch:
                with self._pcm_lock:
                    self._pcm_error = "pipe_failed"
        finally:
            try:
                if pipe is not None:
                    try:
                        pipe.close()
                    finally:
                        dispose = getattr(pipe, "dispose", None)
                        if callable(dispose):
                            dispose()
            finally:
                with self._pcm_lock:
                    if self._pcm_pipe is pipe and self._pcm_pipe_epoch == epoch:
                        self._pcm_pipe, self._pcm_pipe_epoch = None, 0

    def pcm_error(self):
        with self._pcm_lock:
            return self._pcm_error

    def _remember_native_event(self, state, event):
        if event.get("event") == "receiver_play_pause":
            with self._diagnostic_lock:
                # Only the current authenticated direct-PCM child's confirmed
                # members may control the owned source. Never widen this to
                # system-capture events or an uncommitted joining receiver.
                return (state is self._mute_state and not self._pcm_stop.is_set()
                    and not self._last_terminal and self._receiver_connected
                    and type(event.get("pcm_epoch")) is int and event["pcm_epoch"] == self._pcm_epoch
                    and type(event.get("revision")) is int and not self._membership_pending_revision
                    and event["revision"] == self._membership_evidence.get("revision", 0)
                    and isinstance(event.get("receiver_id"), str) and event["receiver_id"] in self._receiver_ids
                    and isinstance(event.get("cseq"), str)
                    and re.fullmatch(r"0|[1-9][0-9]{0,15}", event["cseq"]) is not None
                    and type(event.get("age_ms")) is int and 0 <= event["age_ms"] < 2000)
        if event.get("event") in {"capabilities", "membership"}:
            with self._diagnostic_lock:
                if (state is not self._mute_state or type(event.get("pcm_epoch")) is not int
                        or event["pcm_epoch"] != self._pcm_epoch or self._pcm_stop.is_set()):
                    return False
                if event["event"] == "capabilities":
                    self._pcm_membership_supported = type(event.get("reconfigure_pcm")) is int and event["reconfigure_pcm"] == 1
                    self._pcm_presentation_supported = type(event.get("owned_presentation")) is int and event["owned_presentation"] == 1
                else:
                    if (not self._membership_pending_revision or type(event.get("revision")) is not int
                            or event["revision"] != self._membership_pending_revision):
                        return False
                    phase = event.get("state")
                    confirmed, pending = event.get("confirmed_ids"), event.get("pending_ids")
                    union = {d.id: d for d in (*self._pcm_devices, *self._membership_requested)}
                    if (phase not in {"pending", "applied", "rejected"} or
                            not all(isinstance(ids, list) and len(ids) <= self.MAX_SINKS and
                                all(isinstance(key, str) and key in union for key in ids) and len(set(ids)) == len(ids)
                                for ids in (confirmed, pending)) or set(confirmed) & set(pending)):
                        return False
                    expected = {d.id for d in (self._membership_requested if phase == "applied" else self._pcm_devices)}
                    if set(confirmed) != expected or phase != "pending" and pending:
                        return False
                    if phase == "pending" and set(pending) != {d.id for d in self._membership_requested} - expected:
                        return False
                    self._membership_evidence = {"pcm_epoch": self._pcm_epoch, "revision": event["revision"],
                        "state": phase, "confirmed_ids": tuple(confirmed), "pending_ids": tuple(pending)}
                    if phase != "pending":
                        self._pcm_devices = tuple(union[key] for key in confirmed)
                        self._receiver_ids = tuple(key for key in confirmed if union[key].transport == "airplay")
                        self._local_endpoints = tuple(key for key in confirmed if union[key].transport == "windows")
                        self._member_evidence = {key: value for key, value in self._member_evidence.items() if key in confirmed}
                        self._local_evidence = {key: value for key, value in self._local_evidence.items() if key in confirmed}
                        self._membership_pending_revision = 0
                        self._membership_requested = ()
            return True
        if event.get("event") == "stats":
            group = event.get("group")
            signal = event.get("capture_signal")
            if (not isinstance(group, dict) or group.get("pcm_epoch") != self._pcm_epoch
                    or not isinstance(signal, dict) or signal.get("mode") != "direct_pcm"
                    or signal.get("scope") != "owned-player"):
                return
        super()._remember_native_event(state, event)

    def stop(self):
        self._pcm_stop.set()
        with self._pcm_lock:
            pipe = self._pcm_pipe if self._pcm_pipe_epoch == self._pcm_epoch else None
            # Stopping the consumer does not drain Queue automatically. Drop
            # decoded audio now, including authentication/cancel paths where
            # the pipe worker never began reading it.
            while True:
                try:
                    self._pcm_queue.get_nowait()
                except queue.Empty:
                    break
            self._pcm_bytes = 0
        try:
            try:
                if pipe is not None:
                    pipe.close()
            finally:
                super().stop()
        finally:
            thread, self._pcm_thread = self._pcm_thread, None
            if thread is not None and thread is not threading.current_thread():
                thread.join(.5)
            self._pcm_epoch = 0
            with self._pcm_lock:
                if self._pcm_pipe is pipe:
                    self._pcm_pipe, self._pcm_pipe_epoch = None, 0
            with self._diagnostic_lock:
                self._pcm_membership_supported = False
                self._pcm_presentation_supported = False
                self._membership_pending_revision = 0
                self._membership_requested = ()
                self._pcm_devices = ()
                self._membership_evidence = {}
                self._receiver_ids = self._local_endpoints = ()
                self._member_evidence = self._local_evidence = {}
                self._receiver_connected = False

    def diagnostics(self):
        result = super().diagnostics()
        result.update(pcm_epoch=self._pcm_epoch, pcm_error=self.pcm_error(), capture_scope="owned-player")
        return result
