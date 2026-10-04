"""Own one native capture/timeline shared by a bounded set of audio sinks.

The single-receiver helper remains independently supported. Group startup
requires a verified music process and creation token; failure never widens the
capture scope to the selected endpoint or all computer playback.
"""
from __future__ import annotations

from collections import deque
import copy
import ipaddress
import json
import math
import os
import queue
import re
import subprocess
import threading
import time

from api.airplay_process import AirPlayProcess
from api.audio_routes import AudioRouteDevice
from api.windows_audio_sessions import AudioSessionEvidence, MusicAudioSource


class AirPlayGroupProcess(AirPlayProcess):
    MAX_SINKS = 8
    MAX_EVENT_CHARS = 65536
    _EVENTS = frozenset({"connecting", "waiting_audio", "streaming", "error", "stopped",
                         "stats", "mute_state", "receiver_volume"})
    _ENDPOINT_CASE_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    _LOCAL_BOOL_FIELDS = frozenset({"ready", "armed", "playing", "endpoint_clock_valid",
                                    "acoustic_delay_known", "clock_qpc_age_known"})
    _LOCAL_COUNT_FIELDS = frozenset({"written_frames", "nonzero_samples", "meaningful_samples",
                                     "underrun_frames", "source_frame", "device_clock_frames",
                                     "presentation_latency_frames", "clock_position", "clock_frequency",
                                     "clock_qpc_100ns", "padding_frames", "write_estimate_written_frames"})
    _LOCAL_FLOAT_FIELDS = frozenset({"clock_qpc_age_ms", "write_queue_delay_ms",
                                     "write_presentation_offset_ms", "write_presentation_epoch_offset_ms",
                                     "stream_latency_ms", "buffer_duration_ms", "phase_error_ms", "resample_ratio"})

    def __init__(self, executable=None, popen=None, *, source_id=""):
        super().__init__(executable, popen, source_id=source_id, exclusive_output=False)
        self._receiver_ids = ()
        self._member_evidence = {}
        self._group_source = None
        self._group_source_endpoint = ""
        self._local_endpoints = ()
        self._local_evidence = {}

    @classmethod
    def _endpoint_key(cls, endpoint_id):
        # Match the native group's collision fence without parsing or changing
        # the opaque IMMDevice::GetId string sent to GetDevice.
        return endpoint_id.translate(cls._ENDPOINT_CASE_FOLD)

    @staticmethod
    def _text(value, maximum):
        if not isinstance(value, str) or not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            return False
        try:
            return len(value.encode("utf-8")) <= maximum
        except UnicodeEncodeError:
            return False

    @classmethod
    def _validate_group(cls, devices, source, source_endpoint):
        if (not isinstance(source, MusicAudioSource)
                or isinstance(source.process_id, bool) or not isinstance(source.process_id, int)
                or not 0 < source.process_id <= 0xffffffff
                or isinstance(source.process_created_at_100ns, bool)
                or not isinstance(source.process_created_at_100ns, int)
                or not 0 < source.process_created_at_100ns < 2**64
                or source.process_id == os.getpid()
                or not cls._text(source.source_app_id, 32768)
                or not cls._text(source_endpoint, 2048)):
            raise ValueError("无法确认音乐软件进程，请保持播放器播放后重试")
        if (not source.session_process_ids or any(
                isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 0xffffffff
                or pid == os.getpid() for pid in source.session_process_ids)
                or not source.sessions or any(
                not isinstance(session, AudioSessionEvidence) or session.active is not True
                or session.system_sounds is not False or session.single_process is not True
                or not isinstance(session.endpoint_id, str)
                or cls._endpoint_key(session.endpoint_id) != cls._endpoint_key(source_endpoint)
                or not cls._text(session.session_identifier, 32768)
                or not cls._text(session.session_instance_id, 32768)
                or session.process_id not in source.session_process_ids
                for session in source.sessions)):
            raise ValueError("音乐软件的输出已改变，请刷新后重新选择投放设备")
        if not isinstance(devices, (tuple, list)) or not 1 <= len(devices) <= cls.MAX_SINKS:
            raise ValueError("请为同步播放选择 1 至 8 个输出设备")
        seen, addresses, endpoints, receivers, locals_ = set(), set(), set(), [], []
        for device in devices:
            if (not isinstance(device, AudioRouteDevice) or not cls._text(device.id, 2048)
                    or device.id in seen or device.available is not True):
                raise ValueError("所选投放设备重复或已断开")
            seen.add(device.id)
            if device.transport == "airplay":
                try:
                    address = ipaddress.ip_address(device.host)
                except ValueError as exc:
                    raise ValueError("HomePod 的局域网地址无效") from exc
                if (address.version != 4 or address.is_unspecified or address.is_multicast
                        or isinstance(device.port, bool) or not isinstance(device.port, int)
                        or not 1 <= device.port <= 65535
                        or not cls._text(device.device_id or device.host, 1024)
                        or not cls._text(device.name, 1024)):
                    raise ValueError("HomePod 的连接信息无效")
                target = (str(address), device.port)
                if target in addresses:
                    raise ValueError("所选 HomePod 地址重复，请刷新设备列表")
                addresses.add(target)
                receivers.append(device)
            elif device.transport == "windows":
                endpoint = cls._endpoint_key(device.id)
                if endpoint in endpoints:
                    raise ValueError("所选电脑或蓝牙输出重复，请刷新设备列表")
                if endpoint == cls._endpoint_key(source_endpoint):
                    raise ValueError("音乐来源与同步输出相同，请先选择另一个电脑输出")
                endpoints.add(endpoint)
                locals_.append(device.id)
            else:
                raise ValueError("所选设备不支持同步播放")
        if not receivers:
            raise ValueError("同步播放需要至少一台 AirPlay 设备")
        return tuple(receivers), tuple(locals_)

    def start_group(self, devices, source: MusicAudioSource, source_endpoint: str):
        """Start one helper; receiver order also defines command receiver indices."""
        receivers, local_endpoints = self._validate_group(devices, source, source_endpoint)
        if self._source_id and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", self._source_id):
            raise ValueError("HomePod 发送端身份无效")
        args = [str(self.executable), "--group", "--music-pid", str(source.process_id),
                "--music-created-at", str(source.process_created_at_100ns),
                "--source-endpoint", source_endpoint]
        if self._source_id:
            args += ["--source-id", self._source_id]
        for local_endpoint in local_endpoints:
            args += ["--local-endpoint", local_endpoint]
        for receiver in receivers:
            args += ["--receiver-id", receiver.id, "--host", receiver.host,
                     "--port", str(receiver.port), "--device-id", receiver.device_id or receiver.host,
                     "--name", receiver.name]
        self.stop()
        self.needs_local_output_restore = False
        self._started_at = self._stopped_at = 0.0
        with self._diagnostic_lock:
            self._last_stats = {}
            self._last_stats_at = 0.0
            self._last_terminal = {}
            tail = self._stderr_tail = deque(maxlen=24)
            mute_state = self._mute_state = {}
            self._receiver_volume = {}
            self._receiver_connected = False
            self._member_evidence = {}
            self._receiver_ids = tuple(device.id for device in receivers)
            self._group_source = source
            self._group_source_endpoint = source_endpoint
            self._local_endpoints = local_endpoints
            self._local_evidence = {}
        if not self.executable.is_file():
            # A valid new attempt cannot inherit a stopped predecessor's mute
            # authority just because its executable disappeared before spawn.
            raise RuntimeError("HomePod 投放组件未安装，请更新完整的 GluSpot 安装包")
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = self._popen(args, **kwargs)
        self._bind_owned_process_identity()
        self._started_at = time.monotonic()
        self._streaming = self._terminal_seen = False
        events = self._events = queue.Queue(maxsize=32)
        self._reader = threading.Thread(target=self._read_events, args=(
            self._process, events, lambda event: self._remember_native_event(mute_state, event)),
                                       name="GluSpotAirPlayGroupStatus", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, args=(self._process, tail),
                                              name="GluSpotAirPlayGroupDiagnostics", daemon=True)
        self._stderr_reader.start()

    def _validated_local_renders(self, event):
        """Validate one complete named renderer sample for this capture session."""
        renders = event.get("local_renders")
        if "local_renders" not in event:
            # Older helpers identify their sole renderer implicitly. An
            # unnamed object cannot confirm any member of a plural group.
            legacy = event.get("local_render")
            if len(self._local_endpoints) == 1 and isinstance(legacy, dict):
                endpoint = self._local_endpoints[0]
                if "endpoint_id" not in legacy or legacy["endpoint_id"] == endpoint:
                    renders = [dict(legacy, endpoint_id=endpoint)]
            elif not self._local_endpoints and legacy is None:
                renders = []
        if (not isinstance(renders, list) or len(renders) != len(self._local_endpoints)
                or len(renders) > self.MAX_SINKS):
            return None
        updates = {}
        selected = set(self._local_endpoints)
        fields = self._LOCAL_BOOL_FIELDS | self._LOCAL_COUNT_FIELDS | self._LOCAL_FLOAT_FIELDS
        for render in renders:
            if not isinstance(render, dict):
                return None
            endpoint = render.get("endpoint_id")
            if not isinstance(endpoint, str) or endpoint not in selected or endpoint in updates:
                return None
            if any(not isinstance(render.get(key), bool) for key in ("ready", "armed", "playing")):
                return None
            if "meaningful_samples" not in render:
                return None
            for key in fields & render.keys():
                value = render[key]
                if key in self._LOCAL_BOOL_FIELDS:
                    valid = isinstance(value, bool)
                elif key in self._LOCAL_COUNT_FIELDS:
                    valid = isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**64
                else:
                    valid = isinstance(value, (int, float)) and not isinstance(value, bool)
                    if valid:
                        try:
                            valid = math.isfinite(float(value))
                        except OverflowError:
                            valid = False
                if not valid:
                    return None
            updates[endpoint] = {key: render[key] for key in fields & render.keys()}
            updates[endpoint]["endpoint_id"] = endpoint
        return updates

    def _remember_native_event(self, state, event):
        remembered = event
        locals_ = None
        if event.get("event") == "stats":
            with self._diagnostic_lock:
                if state is self._mute_state:
                    locals_ = self._validated_local_renders(event)
                    remembered = dict(event)
                    remembered["local_renders"] = list((locals_ or {}).values())
                    remembered.pop("local_render", None)
                    if locals_ is not None and len(self._local_endpoints) == 1:
                        remembered["local_render"] = dict(locals_[self._local_endpoints[0]])
                    if locals_ is None or any(not all(render[key] is True for key in (
                            "ready", "armed", "playing")) or render["meaningful_samples"] == 0
                            for render in locals_.values()):
                        timing = event.get("timing")
                        remembered["timing"] = dict(timing, valid=False) if isinstance(timing, dict) else {"valid": False}
        super()._remember_native_event(state, remembered)
        with self._diagnostic_lock:
            if state is not self._mute_state:
                return
            if event.get("event") == "stats":
                # A missing or malformed member clears the whole local sample,
                # so one healthy endpoint cannot stand in for another output.
                self._local_evidence = locals_ or {}
            members = event.get("members")
            if not isinstance(members, list) or len(members) > self.MAX_SINKS:
                return
            updates = {}
            for member in members:
                if not isinstance(member, dict):
                    return
                receiver_id = member.get("receiver_id")
                if receiver_id not in self._receiver_ids or receiver_id in updates:
                    return
                # Retain only the current session's named receivers. No extra
                # network device can become a volume-command destination.
                updates[receiver_id] = copy.deepcopy(member)
            self._member_evidence.update(updates)

    def _volume_state_unlocked(self, receiver_id):
        member = self._member_evidence.get(receiver_id, {})
        state = member.get("receiver_volume", {})
        if not isinstance(state, dict):
            state = {}
        connected = (receiver_id in self._receiver_ids and self._process is not None
                     and self._receiver_connected and not self._last_terminal)
        volume = state.get("volume")
        evidence_source = state.get("source")
        known = (connected and state.get("known") is True and isinstance(evidence_source, str)
                 and evidence_source in {"receiver_read", "receiver_info", "receiver_ack"}
                 and not isinstance(volume, bool) and isinstance(volume, (int, float)))
        if known:
            try:
                volume = float(volume)
                known = math.isfinite(volume) and 0 <= volume <= 1
            except OverflowError:
                known = False
        code, operation = state.get("last_code", 0), state.get("operation", "unknown")
        return {"volume": volume if known else None, "known": bool(known),
                "source": state.get("source") if known else "unknown",
                "operation": operation if isinstance(operation, str) and operation in {"read", "set", "info"} else "unknown",
                "pending": bool(connected and state.get("pending") is True),
                "last_code": code if isinstance(code, int) and not isinstance(code, bool) else 0}

    def receiver_volume_state(self, receiver_id):
        with self._diagnostic_lock:
            return self._volume_state_unlocked(receiver_id)

    def receiver_states(self):
        """Bounded member evidence; a requested volume is never confirmation."""
        with self._diagnostic_lock:
            result = {}
            for receiver_id in self._receiver_ids:
                member = self._member_evidence.get(receiver_id, {})
                protocol = member.get("protocol", {})
                state = member.get("state")
                result[receiver_id] = {
                    "state": state if isinstance(state, str) and state in {
                        "connecting", "waiting_audio", "streaming", "error", "stopped"} else "unknown",
                    "receiver_volume": self._volume_state_unlocked(receiver_id),
                    "protocol": copy.deepcopy(protocol) if isinstance(protocol, dict) else {},
                }
            return result

    def set_device_volume(self, receiver_id, volume):
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            return False
        try:
            volume = float(volume)
        except OverflowError:
            return False
        if not math.isfinite(volume) or not 0 <= volume <= 1:
            return False
        with self._command_lock:
            with self._diagnostic_lock:
                process = self._process
                if (receiver_id not in self._receiver_ids or process is None
                        or not self._receiver_connected or self._last_terminal):
                    return False
                index = self._receiver_ids.index(receiver_id)
            try:
                if process.poll() is not None:
                    return False
                process.stdin.write(json.dumps({"command": "set_volume", "receiver": index,
                                                "volume": volume}, allow_nan=False,
                                               separators=(",", ":")) + "\n")
                process.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def set_volume(self, volume):
        # A legacy unnamed command must not accidentally adjust the first
        # receiver after the group membership or order changes.
        return False

    def _stop_timeout(self):
        # Native teardown closes each member in turn: bounded TCP send/FIN
        # waits are 500 + 300 ms per receiver. Reuse the base's final cleanup
        # after allowing the complete group its bounded graceful-stop window.
        return max(self.STOP_TIMEOUT, min(self.MAX_SINKS, len(self._receiver_ids)) * .8 + 1.0)

    def diagnostics(self):
        result = copy.deepcopy(super().diagnostics())
        with self._diagnostic_lock:
            source = self._group_source
            result.update({"schema_version": 1, "receiver_ids": self._receiver_ids,
                           "local_endpoints": self._local_endpoints,
                           "local_endpoint": self._local_endpoints[0] if len(self._local_endpoints) == 1 else "",
                           "local_renders": tuple(copy.deepcopy(self._local_evidence[endpoint])
                               for endpoint in self._local_endpoints if endpoint in self._local_evidence),
                           "music_source": {
                               "source_app_id": source.source_app_id,
                               "process_id": source.process_id,
                               "process_created_at_100ns": source.process_created_at_100ns,
                               "source_endpoint": self._group_source_endpoint,
                               "scope": "music_process_tree",
                           } if source is not None else {}})
        return result

    @staticmethod
    def _read_events(process, events, on_event=None):
        # Eight per-receiver diagnostic objects exceed the old single-session
        # 8 Ki-character line limit. A fixed ceiling bounds memory and work.
        terminal = ""
        try:
            while True:
                line = process.stdout.readline(AirPlayGroupProcess.MAX_EVENT_CHARS + 1)
                if not line:
                    break
                if len(line) > AirPlayGroupProcess.MAX_EVENT_CHARS or not line.endswith("\n"):
                    while line and not line.endswith("\n"):
                        line = process.stdout.readline(AirPlayGroupProcess.MAX_EVENT_CHARS + 1)
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("event") not in AirPlayGroupProcess._EVENTS:
                        continue
                    event["_received_at"] = time.monotonic()
                    if on_event is not None:
                        on_event(event)
                    event_name = event["event"]
                    if terminal:
                        # Healthy samples after a terminal cannot push that
                        # terminal out of the bounded queue. Final mute
                        # restoration is still recorded by the callback.
                        if event_name != "error":
                            continue
                    if event_name in {"error", "stopped"}:
                        terminal = event_name
                    if events.full():
                        try:
                            events.get_nowait()
                        except queue.Empty:
                            pass
                    events.put_nowait(event)
                except (ValueError, queue.Full):
                    continue
        except (OSError, ValueError):
            pass
