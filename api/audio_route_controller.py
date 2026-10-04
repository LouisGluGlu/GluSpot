"""Serial Windows output switching and shared music-output orchestration.

Only the worker touches COM, mDNS, or processes. GUI readers receive immutable
snapshots. Selecting another output cancels the prior sender before switching.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import ipaddress
import math
import threading
import time

from api.airplay_discovery import AirPlayDiscovery
from api.airplay_process import AirPlayProcess
from api.airplay_group_process import AirPlayGroupProcess
from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from api.windows_audio_sessions import MusicAudioSource, resolve_music_audio_source


class AudioRouteController:
    POLL_SECONDS = 2.0
    CAPTURE_SIGNAL_MAX_AGE = 2.0
    CAPTURE_SILENCE_SECONDS = 3.0
    CAPTURE_SILENCE_MESSAGE = "已连接 HomePod，暂未检测到电脑声音"
    APP_ROUTE_RESUME_MESSAGE = "原输出设置已恢复，等待音乐继续播放"

    def __init__(self, backend=None, discovery=None, sender=None, *, latency_seconds=1.5,
                 start_worker=True, source_id="", auto_latency_enabled=True,
                 group_sender=None, source_resolver=None, app_router=None):
        if backend is None:
            from api.windows_audio_devices import WindowsAudioDevices
            backend = WindowsAudioDevices()
        self._backend = backend
        self._discovery = discovery or AirPlayDiscovery()
        self._sender = sender or AirPlayProcess(source_id=source_id)
        self._group_sender = group_sender or AirPlayGroupProcess(source_id=source_id)
        self._source_resolver = source_resolver or resolve_music_audio_source
        self._app_router = app_router
        self._app_route_guard = None
        self._resume_recovery_pending = False
        self._next_route_recovery_at = 0.0
        self._remaining_commands = ()
        self._last_sender = self._sender
        self._group_mode = False
        self._group_member_ids = ()
        self._group_source = None
        self._music_source_app_id = ""
        self._requested_music_source_app_id = ""
        self._next_source_resolve_at = 0.0
        self._cast_started_default_id = ""
        self._group_volume_signatures = {}
        self._group_known_connected_ids = ()
        self._group_streaming_seen = False
        # Constructor options remain migration aliases. Compensation is now
        # always automatic and starts from the existing protocol buffer.
        self._latency = 1.5
        self._auto_latency = True
        self._latency_updated_at = None
        self._lock = threading.Lock()
        self._snapshot = AudioRouteSnapshot(status_message="正在读取音频设备",
                                            cast_latency_auto=self._auto_latency)
        self._commands = deque(maxlen=16)
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._thread = None
        self._local_devices = ()
        self._manual_devices = {}
        self._baseline = False
        self._cast_id = ""
        self._cast_source_id = ""
        self._cast_source_original_mute = None
        self._receiver_volume_status_signature = None
        self._capture_authenticated_at = None
        self._capture_session_started_at = None
        self._capture_signal_received_at = None
        self._capture_signal_first_received_at = None
        self._capture_signal_nonzero = None
        self._capture_signal_age_ms = None
        self._generation = 0
        self._worker_started_at = time.monotonic()
        self._endpoint_error = False
        if start_worker:
            self._thread = threading.Thread(target=self._run, name="GluSpotAudioRouting", daemon=True)
            self._thread.start()

    @staticmethod
    def _normalize_latency(value):
        try:
            value = float(value)
            return max(0.0, min(5.0, value)) if math.isfinite(value) else 1.5
        except (ValueError, TypeError):
            return 1.5

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def diagnostics(self):
        sender = self._active_sender() if self._cast_id or self._group_mode else self._last_sender
        read = getattr(sender, "diagnostics", None)
        return read() if callable(read) else {}

    def _active_sender(self):
        return self._group_sender if self._group_mode else self._sender

    def _publish(self, **changes):
        with self._lock:
            candidate = replace(self._snapshot, **changes)
            if candidate != self._snapshot:
                self._snapshot = replace(candidate, revision=self._snapshot.revision + 1)

    def _announce(self, name, message):
        previous = self.snapshot()
        self._publish(event_serial=previous.event_serial + 1, event_name=name,
                      event_message=message)

    def _enqueue(self, operation, value=None):
        if self._closed.is_set():
            return False
        with self._lock:
            if operation in {"select", "stop"}:
                self._commands = deque((c for c in self._commands if c[0] not in {"select", "stop", "toggle"}), maxlen=16)
            elif operation == "music_source":
                self._commands = deque((c for c in self._commands if c[0] != "music_source"), maxlen=16)
            elif operation == "volume":
                self._commands = deque((c for c in self._commands
                                        if c[0] != "volume" or c[1][0] != value[0]), maxlen=16)
            elif operation != "toggle" and any(c[0] == operation and c[1] == value for c in self._commands):
                return True
            self._commands.append((operation, value))
        self._wake.set()
        return True

    def refresh(self):
        return self._enqueue("refresh")

    def select(self, device_id):
        if not any(d.id == device_id and d.available for d in self.snapshot().devices):
            return False
        return self._enqueue("select", device_id)

    def toggle(self, device_id):
        """Toggle one group member; Windows-only selection retains local behavior."""
        current = self.snapshot()
        if not any(d.id == device_id and (d.available or device_id in
                   current.member_ids + current.pending_ids) for d in current.devices):
            return False
        return self._enqueue("toggle", device_id)

    def set_music_source(self, source_app_id):
        """Accept the provider's exact selected GSMTC identity, never a display name."""
        if (not isinstance(source_app_id, str) or "\0" in source_app_id
                or len(source_app_id) > 32768):
            return False
        with self._lock:
            if source_app_id == self._requested_music_source_app_id:
                return not self._closed.is_set()
            self._requested_music_source_app_id = source_app_id
        return self._enqueue("music_source", source_app_id)

    def stop_cast(self):
        return self._enqueue("stop")

    def set_latency(self, value):
        """Deprecated manual delay control; automatic compensation owns it."""
        return False

    def set_auto_latency(self, enabled):
        return enabled is True and not self._closed.is_set()

    @staticmethod
    def _volume_scalar(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            value = float(value)
        except OverflowError:
            return None
        return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None

    def set_volume(self, device_id, volume):
        value = self._volume_scalar(volume)
        current = self.snapshot()
        device = next((d for d in current.devices if d.id == device_id), None)
        if value is None or device is None or device.volume is None:
            return False
        if device.transport == "airplay":
            selected = device_id in self._group_member_ids if self._group_mode else device_id == self._cast_id
            if not selected or current.state not in {"waiting_audio", "casting"}:
                return False
        elif device.transport != "windows" or not device.available:
            return False
        return self._enqueue("volume", (device_id, value, self._generation))

    def add_homepod(self, address, port=7000):
        try:
            ip = ipaddress.ip_address(str(address).strip())
            port = int(port)
            if ip.version != 4 or ip.is_unspecified or ip.is_multicast or not 1 <= port <= 65535:
                return False
        except (ValueError, TypeError):
            return False
        return self._enqueue("manual", (str(ip), port))

    def _refresh_devices(self):
        try:
            raw = self._backend.list_outputs()
            local = tuple(AudioRouteDevice(d.id, d.name, d.kind, "windows", d.is_bluetooth, d.is_default,
                                           volume=self._volume_scalar(getattr(d, "volume", None)),
                                           volume_muted=bool(getattr(d, "volume_muted", False)))
                          for d in raw)
        except Exception as exc:
            self._endpoint_error = True
            if self._cast_id or self._group_member_ids:
                self._end_cast()
            self._publish(active_id="", state="error", status_message=f"读取音频设备失败：{str(exc)[:180]}")
            return False
        if self._closed.is_set():
            return False
        if self._endpoint_error:
            self._endpoint_error = False
            self._publish(state="ready", status_message="音频设备已恢复")
        previous_ids = {d.id for d in self._local_devices}
        self._local_devices = local
        if self._baseline:
            for device in local:
                if device.id not in previous_ids:
                    self._announce(device.name, "蓝牙设备已接入" if device.is_bluetooth else "音频设备已接入")
        self._baseline = True
        default_id = next((d.id for d in local if d.is_default), "")
        local_ids = {d.id for d in local}
        selected_local = {identity for identity in self._group_member_ids
                          if any(d.id == identity and d.transport == "windows" for d in self.snapshot().devices)}
        if self._cast_source_id and self._cast_source_id not in {d.id for d in local}:
            self._end_cast()
            self._publish(state="error", status_message="投放的音频来源已断开，请重新选择输出设备")
        elif selected_local - local_ids:
            self._end_cast()
            self._publish(state="error", status_message="同步播放的电脑或蓝牙设备已断开，请重新选择")
        elif (self._cast_source_id and default_id and default_id !=
              (self._cast_started_default_id if self._group_mode else self._cast_source_id)):
            self._end_cast()
            self._publish(state="error", status_message="电脑输出已改变，HomePod 投放已停止；请重新选择 HomePod")
        current = self.snapshot()
        active = self._cast_id or default_id
        state = current.state
        if not self._cast_id and state not in {"error", "switching", "waiting_source"}:
            state = "ready" if local else "unavailable"
        self._publish(active_id=active, state=state)
        self._merge_devices()
        return True

    def _merge_devices(self):
        remote = {d.id: d for d in self._discovery.devices()}
        for identity, device in self._manual_devices.items():
            if not any(d.host == device.host for d in remote.values()):
                remote[identity] = device
        current = self.snapshot()
        # A receiver can disappear temporarily from mDNS while its session lives.
        retained = self._group_member_ids if self._group_mode else (self._cast_id,)
        for identity in retained:
            if not identity or identity in remote:
                continue
            active = next((d for d in current.devices if d.id == identity and d.transport == "airplay"), None)
            if active is not None:
                remote[active.id] = replace(active, available=False)
        if self._group_mode:
            self._merge_group_devices(remote, current)
            return
        confirmed = None
        if self._cast_id and current.state in {"waiting_audio", "casting"}:
            read = getattr(self._sender, "receiver_volume_state", None)
            evidence = read() if callable(read) else {}
            if (isinstance(evidence, dict) and evidence.get("known") is True
                    and evidence.get("source") in {"receiver_read", "receiver_info", "receiver_ack"}):
                confirmed = self._volume_scalar(evidence.get("volume"))
            if isinstance(evidence, dict):
                code, operation = evidence.get("last_code"), evidence.get("operation")
                signature = (self._generation, evidence.get("pending"), code, operation,
                             evidence.get("known"), confirmed)
                changed = signature != self._receiver_volume_status_signature
                self._receiver_volume_status_signature = signature
            else:
                changed, code, operation = False, None, None
            if changed and evidence.get("pending") is False:
                if isinstance(code, int) and not isinstance(code, bool) and (code >= 400 or code == 0):
                    if operation == "set":
                        reason = f"接收端返回 {code}" if code else "音量指令超时"
                        self._publish(status_message=f"HomePod 音量调整失败：{reason}")
                    elif operation == "read" and confirmed is None:
                        self._publish(status_message="暂时无法读取 HomePod 音量")
                elif code == 200 and operation == "set" and confirmed is not None:
                    self._publish(status_message="已调整 HomePod 音量")
        remote = {identity: replace(device,
                    volume=confirmed if identity == self._cast_id else None,
                    volume_muted=confirmed == 0.0 if identity == self._cast_id and confirmed is not None else False)
                  for identity, device in remote.items()}
        devices = self._local_devices + tuple(sorted(remote.values(), key=lambda d: (d.name.casefold(), d.id)))
        self._publish(devices=devices, discovery_state=self._discovery.state)

    def _merge_group_devices(self, remote, current):
        read = getattr(self._group_sender, "receiver_volume_state", None)
        read_states = getattr(self._group_sender, "receiver_states", None)
        states = read_states() if callable(read_states) else {}
        if not isinstance(states, dict):
            states = {}
        updated = {}
        for identity, device in remote.items():
            evidence = read(identity) if callable(read) and identity in self._group_member_ids else {}
            evidence = evidence if isinstance(evidence, dict) else {}
            confirmed = None
            evidence_source = evidence.get("source")
            if (current.state in {"waiting_audio", "casting"} and evidence.get("known") is True
                    and isinstance(evidence_source, str)
                    and evidence_source in {"receiver_read", "receiver_info", "receiver_ack"}):
                confirmed = self._volume_scalar(evidence.get("volume"))
            route_state = "pending" if identity in current.pending_ids else "active" if identity in current.member_ids else "available"
            member = states.get(identity, {})
            member = member if isinstance(member, dict) else {}
            message = ""
            if member.get("state") == "error":
                route_state, message = "error", "此设备的播放已中断"
            code, operation = evidence.get("last_code"), evidence.get("operation")
            signature = (self._generation, evidence.get("pending"), code, operation,
                         evidence.get("known"), confirmed)
            changed = signature != self._group_volume_signatures.get(identity)
            self._group_volume_signatures[identity] = signature
            if (identity in self._group_member_ids and changed and evidence.get("pending") is False
                    and isinstance(code, int) and not isinstance(code, bool)):
                if (code >= 400 or code == 0) and operation == "set":
                    reason = f"接收端返回 {code}" if code else "音量指令超时"
                    message = f"音量调整失败：{reason}"
                    self._publish(status_message=f"{device.name} {message}")
                elif code == 200 and operation == "set" and confirmed is not None:
                    self._publish(status_message=f"已调整 {device.name} 音量")
            updated[identity] = replace(device, volume=confirmed, volume_muted=confirmed == 0 if confirmed is not None else False,
                                        route_state=route_state, route_message=message)
        local = tuple(replace(device, route_state="pending" if device.id in current.pending_ids else
                              "active" if device.id in current.member_ids else "available", route_message="")
                      for device in self._local_devices)
        self._publish(devices=local + tuple(sorted(updated.values(), key=lambda d: (d.name.casefold(), d.id))),
                      discovery_state=self._discovery.state)

    def _set_volume(self, identity, value, generation):
        """Adjust the named device without routing changes or mute recovery."""
        current = self.snapshot()
        device = next((d for d in current.devices if d.id == identity), None)
        try:
            if device is None or device.volume is None:
                raise RuntimeError("设备音量暂时不可用")
            if device.transport == "windows":
                if not any(d.id == identity and d.available for d in self._local_devices):
                    raise RuntimeError("音频设备已断开")
                self._backend.set_endpoint_volume(identity, value)
                actual = self._volume_scalar(self._backend.get_endpoint_volume(identity))
                if actual is None or not math.isclose(actual, value, abs_tol=0.005):
                    raise RuntimeError("无法确认设备音量")
                muted = bool(self._backend.get_endpoint_mute(identity))
                self._local_devices = tuple(replace(d, volume=actual, volume_muted=muted)
                                            if d.id == identity else d for d in self._local_devices)
            elif (device.transport == "airplay" and (identity in self._group_member_ids if self._group_mode else identity == self._cast_id)
                    and generation == self._generation and current.state in {"waiting_audio", "casting"}):
                accepted = (self._group_sender.set_device_volume(identity, value) if self._group_mode
                            else self._sender.set_volume(value))
                if accepted is False:
                    raise RuntimeError("HomePod 音量指令未被接受")
                # Sending is not receiver confirmation. A later read/200 ACK
                # publishes the actual volume through the sender snapshot.
            else:
                raise RuntimeError("设备连接已改变，请重试")
            self._publish(status_message=f"正在调整 {device.name} 音量" if device.transport == "airplay"
                          else f"已调整 {device.name} 音量")
        except Exception as exc:
            # Volume errors leave a healthy cast and its owned mute intact.
            self._publish(status_message=f"调整音量失败：{str(exc)[:180]}")

    def _end_cast(self):
        self._generation += 1
        self._latency_updated_at = None
        self._receiver_volume_status_signature = None
        self._capture_authenticated_at = None
        self._capture_session_started_at = None
        self._capture_signal_received_at = None
        self._capture_signal_first_received_at = None
        self._capture_signal_nonzero = None
        self._capture_signal_age_ms = None
        self._group_volume_signatures = {}
        self._group_streaming_seen = False
        source_id, original_mute = self._cast_source_id, self._cast_source_original_mute
        app_guard, self._app_route_guard = self._app_route_guard, None
        sender = self._active_sender()
        self._last_sender = sender
        try:
            sender.stop()
        finally:
            self._cast_id = self._cast_source_id = ""
            self._cast_started_default_id = ""
            self._cast_source_original_mute = None
            self._group_mode = False
            self._group_member_ids = ()
            self._group_source = None
            self._group_known_connected_ids = ()
            self._next_source_resolve_at = 0.0
            default_id = next((d.id for d in self._local_devices if d.is_default), "")
            self._publish(active_id=default_id, pending_id="", cast_source_id="", cast_latency_seconds=0.0,
                          cast_latency_estimate_seconds=None, member_ids=(), pending_ids=(), group_id="",
                          capture_scope="", music_source_app_id=self._music_source_app_id)
            evidence = self.diagnostics().get("local_output", {})
            if evidence.get("endpoint_id") == source_id and isinstance(evidence.get("original_muted"), bool):
                original_mute = evidence["original_muted"]
            try:
                if (source_id and original_mute is False and
                        getattr(sender, "needs_local_output_restore", False)):
                    # Preserve a user's explicit unmute. Never touch volume or
                    # the new default; only restore this owned source endpoint.
                    if self._backend.get_endpoint_mute(source_id):
                        self._backend.set_endpoint_mute(source_id, original_mute)
            finally:
                # Stop/reap the delayed renderer and restore source mute before
                # returning the player's actual stream to its original output.
                if app_guard is not None:
                    try:
                        app_guard.restore()
                        if getattr(app_guard, "restoration_state", "") == "policy_restored_no_active_stream":
                            self._resume_recovery_pending = True
                            self._next_route_recovery_at = time.monotonic() + self.POLL_SECONDS
                    except Exception:
                        self._app_route_guard = app_guard  # Retain actionable recovery ownership.
                        raise

    def _select(self, identity):
        if self._closed.is_set() or not self._refresh_devices() or self._closed.is_set():
            return
        device = next((d for d in self.snapshot().devices if d.id == identity and d.available), None)
        if device is None:
            self._publish(state="error", pending_id="", status_message="设备已断开，请刷新后重试")
            return
        if not self._group_mode and device.id == self._cast_id and self.snapshot().state in {"connecting", "waiting_audio", "casting"}:
            return
        self._end_cast()
        self._publish(pending_id=device.id, state="switching" if device.transport == "windows" else "connecting",
                      status_message="正在切换输出" if device.transport == "windows" else "正在连接 HomePod")
        try:
            if device.transport == "windows":
                self._backend.set_default(device.id)
                if self._refresh_devices() and any(d.id == identity and d.is_default for d in self._local_devices):
                    # The user's confirmed new local choice supersedes only
                    # a pure paused observation. An unreturned route write is
                    # still recovery ownership and must never be discarded.
                    if self._pending_app_route_stage():
                        retire = getattr(self._app_router, "retire_resume_observation", None)
                        if not callable(retire):
                            raise RuntimeError("无法确认上次音乐输出恢复记录，请稍后重试")
                        retire()
                        if self._pending_app_route_stage():
                            raise RuntimeError("音乐软件的上次输出恢复仍未完成")
                        self._resume_recovery_pending = False
                    self._publish(pending_id="", state="ready", status_message=f"已选择 {device.name}")
                    self._announce(device.name, "已切换音频输出")
                else:
                    self._publish(pending_id="", state="error", status_message="无法确认实际音频输出，请刷新后重试")
            else:
                source = next((d for d in self._local_devices if d.is_default), None)
                if source is None:
                    raise RuntimeError("没有可用的电脑音频输出，无法获取播放器声音")
                if self._closed.is_set():
                    return
                self._cast_source_original_mute = self._backend.get_endpoint_mute(source.id)
                self._cast_source_id = source.id
                self._capture_session_started_at = time.monotonic()
                self._last_sender = self._sender
                self._sender.start(device, source.id)
                self._cast_id, self._cast_source_id = device.id, source.id
                self._publish(cast_source_id=source.id)
        except Exception as exc:
            self._end_cast()
            default_id = next((d.id for d in self._local_devices if d.is_default), "")
            self._publish(active_id=default_id, pending_id="", state="error", status_message=str(exc)[:240])

    def _toggle(self, identity):
        if self._closed.is_set() or not self._refresh_devices() or self._closed.is_set():
            return
        current = self.snapshot()
        devices = {device.id: device for device in current.devices}
        device = devices.get(identity)
        selected = list(self._group_member_ids)
        if not selected and self._cast_id:
            selected = [self._cast_id]
        if device is None or not device.available and identity not in selected:
            self._publish(status_message="设备已断开，请刷新后重试")
            return
        if device.transport == "windows" and not any(
                devices.get(member) is not None and devices[member].transport == "airplay" for member in selected):
            self._select(identity)
            return
        if identity in selected:
            selected.remove(identity)
        else:
            if device.transport == "windows":
                selected = [member for member in selected if devices.get(member) is not None
                            and devices[member].transport != "windows"]
            selected.append(identity)
        if len(selected) > AirPlayGroupProcess.MAX_SINKS:
            self._publish(status_message="同步播放最多支持 8 个输出设备")
            return
        receivers = [member for member in selected if devices.get(member) is not None and devices[member].transport == "airplay"]
        if not receivers:
            local = next((member for member in selected if devices.get(member) is not None
                          and devices[member].transport == "windows"), "")
            self._end_cast()
            if local:
                self._select(local)
            else:
                self._publish_stopped("同步播放已停止")
                self._refresh_devices()
            return
        self._begin_group(tuple(selected))

    def _change_music_source(self, source_app_id):
        if source_app_id == self._music_source_app_id:
            return
        selected = self._group_member_ids
        self._music_source_app_id = source_app_id
        self._publish(music_source_app_id=source_app_id)
        if self._group_mode and selected:
            self._begin_group(selected)

    def _begin_group(self, member_ids):
        # Stop/reap and restore the old session before starting its replacement.
        # The new helper owns one fixed source and the entire requested group.
        current = self.snapshot()
        connected_ids = current.member_ids if self._group_mode else (self._cast_id,) if current.state == "casting" else ()
        self._end_cast()
        self._group_mode = True
        self._group_member_ids = tuple(member_ids)
        self._group_known_connected_ids = tuple(identity for identity in member_ids if identity in connected_ids)
        self._last_sender = self._group_sender
        self._publish(state="waiting_source", pending_ids=self._group_member_ids,
                      pending_id=next((identity for identity in member_ids if any(
                          device.id == identity and device.transport == "airplay" for device in self.snapshot().devices)), ""),
                      member_ids=(), group_id=f"group:{self._generation}", capture_scope="music_process_tree",
                      music_source_app_id=self._music_source_app_id,
                      status_message="等待音乐软件播放，以确认同步播放来源")
        self._try_start_group()

    def _try_start_group(self):
        if not self._group_mode or self._cast_id or self._closed.is_set():
            return
        self._next_source_resolve_at = time.monotonic() + self.POLL_SECONDS
        try:
            # A previous parent/helper failure may have left the player on our
            # temporary endpoint even when this new group's source and sink
            # already differ. Restore every durable owned route first, then
            # resolve again: the pre-recovery session DTO is no longer valid.
            router = self._get_app_router()
            router.recover_pending()
            pending_stage = self._pending_app_route_stage()
            if pending_stage and pending_stage != "resume_verification_pending":
                raise RuntimeError("上次音乐输出的恢复仍未完成")
            self._resume_recovery_pending = pending_stage == "resume_verification_pending"
            self._next_route_recovery_at = time.monotonic() + self.POLL_SECONDS
            if self._route_setup_cancelled():
                self._cancel_group_setup()
                return
        except Exception as exc:
            if self._route_setup_cancelled():
                self._cancel_group_setup()
                return
            self._end_cast()
            self._publish(state="error", status_message=f"音乐软件的上次输出尚未恢复：{str(exc)[:160]}")
            return
        try:
            source = self._source_resolver(self._music_source_app_id) if self._music_source_app_id else None
        except Exception:
            source = None
        if not isinstance(source, MusicAudioSource) or source.source_app_id != self._music_source_app_id:
            message = (self.APP_ROUTE_RESUME_MESSAGE if self._resume_recovery_pending else
                       "等待音乐软件播放，以确认同步播放来源")
            self._publish(state="waiting_source", status_message=message)
            return
        endpoints = {session.endpoint_id for session in source.sessions}
        if len(endpoints) != 1:
            self._end_cast()
            self._publish(state="error", status_message="音乐软件正在使用多个输出，请先选择一个音乐来源输出")
            return
        source_endpoint = next(iter(endpoints))
        devices = {device.id: device for device in self.snapshot().devices}
        selected = tuple(replace(devices[identity], available=True) if identity in devices and
                         identity in self._group_known_connected_ids and devices[identity].transport == "airplay"
                         else devices.get(identity) for identity in self._group_member_ids)
        if (source_endpoint not in {device.id for device in self._local_devices}
                or any(device is None for device in selected)):
            self._end_cast()
            self._publish(state="error", status_message="音乐来源或所选设备已断开，请重新选择")
            return
        try:
            local_endpoint = next((device.id for device in selected if device.transport == "windows"), "")
            if local_endpoint == source_endpoint or not router.endpoint_is_unoccupied(source_endpoint, source):
                source = self._route_music_to_spare(source, local_endpoint or source_endpoint)
                source_endpoint = next(iter({session.endpoint_id for session in source.sessions}))
            if self._route_setup_cancelled():
                self._cancel_group_setup()
                return
            self._cast_source_original_mute = self._backend.get_endpoint_mute(source_endpoint)
            self._cast_source_id = source_endpoint
            self._cast_started_default_id = next((device.id for device in self._local_devices if device.is_default), "")
            self._capture_session_started_at = time.monotonic()
            self._group_source = source
            self._group_sender.start_group(selected, source, source_endpoint)
            self._cast_id = next(device.id for device in selected if device.transport == "airplay")
            self._publish(state="connecting", pending_id=self._cast_id, cast_source_id=source_endpoint,
                          status_message="正在连接同步播放设备")
        except Exception as exc:
            if self._route_setup_cancelled():
                self._cancel_group_setup()
                return
            self._end_cast()
            self._publish(state="error", status_message=str(exc)[:240])

    def _route_setup_cancelled(self):
        if self._closed.is_set():
            return True
        operations = {"select", "stop", "toggle", "music_source"}
        with self._lock:
            return any(command[0] in operations for command in self._commands) or any(
                command[0] in operations for command in self._remaining_commands)

    def _cancel_group_setup(self):
        desired = self._group_member_ids
        self._end_cast()
        if self._closed.is_set():
            return
        # A queued membership/source command still needs the requested group
        # during the migration gap. Preserve intent, then let that command
        # replace/cancel it on the worker; never restart in this same batch.
        self._group_mode, self._group_member_ids = True, desired
        self._last_sender = self._group_sender
        self._next_source_resolve_at = time.monotonic() + self.POLL_SECONDS
        self._publish(state="waiting_source", pending_ids=desired, member_ids=(),
                      group_id=f"group:{self._generation}", capture_scope="music_process_tree",
                      status_message="正在更新同步播放设备")

    def _route_music_to_spare(self, source, local_endpoint):
        router = self._get_app_router()
        original_endpoints = {session.endpoint_id for session in source.sessions}
        candidates = sorted((device for device in self._local_devices
                             if device.id != local_endpoint and device.id not in original_endpoints
                             and device.available and not device.is_bluetooth),
                            key=lambda device: (device.is_default, device.kind != "speaker", device.id))
        guard = None
        for candidate in candidates:
            if self._route_setup_cancelled():
                raise RuntimeError("同步播放设备设置已取消")
            try:
                guard = router.prepare(source, candidate.id, local_endpoint)
                break
            except (ValueError, RuntimeError, OSError):
                continue
        if guard is None:
            raise RuntimeError("没有可安全使用的空闲电脑音频输出，暂时无法同步播放此蓝牙设备")
        self._app_route_guard = guard
        self._publish(state="connecting", status_message="正在为音乐软件准备同步播放来源")
        guard.apply()
        migrated = guard.wait_migrated(timeout=5.0, cancelled=self._route_setup_cancelled)
        if (not isinstance(migrated, MusicAudioSource)
                or migrated.source_app_id != self._music_source_app_id
                or {session.endpoint_id for session in migrated.sessions} != {guard.spare_endpoint}):
            raise RuntimeError("无法确认音乐软件的同步播放来源")
        return migrated

    def _get_app_router(self):
        if self._app_router is None:
            from api.windows_app_audio_routes import WindowsAppAudioRoutes
            self._app_router = WindowsAppAudioRoutes(devices=self._backend, resolver=self._source_resolver)
        return self._app_router

    def _pending_app_route_stage(self):
        read = getattr(self._app_router, "pending_recovery", None)
        record = read() if callable(read) else None
        if record is None:
            return ""
        if not isinstance(record, dict) or not isinstance(record.get("stage"), str):
            raise RuntimeError("音乐输出恢复记录无法确认")
        return record["stage"]

    def _publish_stopped(self, message):
        try:
            stage = self._pending_app_route_stage()
        except Exception as exc:
            self._publish(state="error", status_message=f"{message}；音乐输出恢复记录无法确认：{str(exc)[:120]}")
            return
        self._resume_recovery_pending = stage == "resume_verification_pending"
        self._next_route_recovery_at = time.monotonic() + self.POLL_SECONDS
        if stage and not self._resume_recovery_pending:
            self._publish(state="error", status_message=f"{message}；音乐软件的原输出仍需恢复")
        else:
            self._publish(state="ready", status_message=f"{message}；{self.APP_ROUTE_RESUME_MESSAGE}"
                          if self._resume_recovery_pending else message)

    def _observe_pending_app_recovery(self):
        # A paused stop can prove policy restoration without proving a live
        # stream's endpoint. Keep that durable observation through resume;
        # never recover an active group's intentionally owned route here.
        if (not self._resume_recovery_pending or self._group_mode or self._cast_id
                or self._app_route_guard is not None or self._closed.is_set()
                or time.monotonic() < self._next_route_recovery_at):
            return
        self._next_route_recovery_at = time.monotonic() + self.POLL_SECONDS
        try:
            self._app_router.recover_pending()
            stage = self._pending_app_route_stage()
            if stage and stage != "resume_verification_pending":
                raise RuntimeError("音乐软件原输出的恢复仍未完成")
        except Exception as exc:
            current = self.snapshot()
            if current.state == "ready" or current.status_message.startswith("音乐软件原输出恢复待确认："):
                self._publish(state="error", status_message=f"音乐软件原输出恢复待确认：{str(exc)[:160]}")
            return
        self._resume_recovery_pending = bool(stage)
        current = self.snapshot()
        if not stage and (current.state == "ready" or current.status_message.startswith("音乐软件原输出恢复待确认：")):
            # A newer user output policy can legitimately end this observation
            # too. Clearing its record alone is not proof of the old endpoint.
            self._publish(state="ready", status_message="音乐软件输出恢复检查已结束")

    def _check_group_source(self):
        """A playing app's stream must remain on the explicitly suppressed output.

        Pauses have no active sessions and hold the established source. Positive
        evidence of a new process/tree or endpoint stops the group; rebuilding
        it requires an explicit player selection or device retry.
        """
        if not self._group_mode or not self._cast_id or self._group_source is None:
            return
        now = time.monotonic()
        if now < self._next_source_resolve_at:
            return
        self._next_source_resolve_at = now + self.POLL_SECONDS
        try:
            read_owned = getattr(self._group_sender, "owned_process_identity", None)
            owned_process = read_owned() if callable(read_owned) else None
            ownership = {"owned_process": owned_process} if owned_process is not None else {}
            if self._app_route_guard is not None:
                self._app_route_guard.check(**ownership)
            if not self._get_app_router().endpoint_is_unoccupied(self._cast_source_id, self._group_source, **ownership):
                raise RuntimeError("同步播放来源输出被另一应用占用")
            source = self._source_resolver(self._music_source_app_id)
        except Exception as exc:
            self._end_cast()
            self._publish(state="error", status_message=f"音乐软件的同步输出已改变：{str(exc)[:160]}")
            return
        if not isinstance(source, MusicAudioSource) or source.source_app_id != self._music_source_app_id:
            return
        same_process = ((source.process_id, source.process_created_at_100ns) ==
                        (self._group_source.process_id, self._group_source.process_created_at_100ns))
        same_endpoint = {session.endpoint_id for session in source.sessions} == {self._cast_source_id}
        if not same_process or not same_endpoint:
            self._end_cast()
            self._publish(state="error", status_message="音乐软件的进程或输出已改变，同步播放已停止；请重新选择设备")

    def _observe_capture_signal(self, event):
        """Report fresh capture evidence without changing routing or playback.

        A zero buffer can be a paused player or a song's quiet passage. This is
        a status message, never proof of a driver fault or receiver audibility.
        Older helpers have no signal evidence and retain their existing status.
        """
        current = self.snapshot()
        if current.state not in {"waiting_audio", "casting"} or self._capture_authenticated_at is None:
            return
        signal = event.get("capture_signal")
        if not isinstance(signal, dict):
            return
        raw_count, raw_age, peak = (signal.get(key) for key in ("nonzero_samples", "last_nonzero_age_ms", "peak"))
        meaningful = self._group_mode or "meaningful_samples" in signal or "last_meaningful_age_ms" in signal
        count, age = ((signal.get("meaningful_samples"), signal.get("last_meaningful_age_ms")) if meaningful
                      else (raw_count, raw_age))
        expected_modes = {("process_loopback", "music_process_tree")} if self._group_mode else {
            ("process_loopback", "all_other_processes"), ("endpoint_loopback", "selected_endpoint")}
        if (isinstance(count, bool) or not isinstance(count, int) or not 0 <= count < 2**64
                or (signal.get("mode"), signal.get("scope")) not in expected_modes
                or isinstance(peak, bool) or not isinstance(peak, (int, float))):
            return
        if meaningful:
            if (isinstance(raw_count, bool) or not isinstance(raw_count, int) or not count <= raw_count < 2**64
                    or raw_count == 0 and (raw_age is not None or peak != 0)
                    or raw_count > 0 and raw_age is None):
                return
            try:
                if raw_age is not None and (isinstance(raw_age, bool) or not isinstance(raw_age, (int, float))
                                           or not math.isfinite(raw_age) or raw_age < 0):
                    return
            except OverflowError:
                return
        try:
            if not math.isfinite(peak) or not 0 <= peak <= 1:
                return
            if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float))
                                    or not math.isfinite(age) or age < 0):
                return
        except OverflowError:
            return
        if (count == 0 and (age is not None or not meaningful and peak != 0)) or (count > 0 and age is None):
            return
        now = time.monotonic()
        received = event.get("_received_at")
        # The process reader timestamps every event before queueing.
        # Reject backlog and repeated/out-of-order events instead of making an
        # old sample fresh merely because the routing worker was polled again.
        if isinstance(received, bool) or not isinstance(received, (int, float)):
            return
        try:
            if (not math.isfinite(received) or not 0 <= now - received <= self.CAPTURE_SIGNAL_MAX_AGE
                    or self._capture_session_started_at is not None and received < self._capture_session_started_at
                    or self._capture_signal_received_at is not None and received <= self._capture_signal_received_at):
                return
        except OverflowError:
            return
        if self._capture_signal_nonzero is not None:
            if count < self._capture_signal_nonzero:
                return
            if (count == self._capture_signal_nonzero and age is not None
                    and self._capture_signal_age_ms is not None and age + 1 < self._capture_signal_age_ms):
                return
        self._capture_signal_received_at = received
        if self._capture_signal_first_received_at is None:
            self._capture_signal_first_received_at = received
        self._capture_signal_nonzero, self._capture_signal_age_ms = count, age
        silent = (age >= self.CAPTURE_SILENCE_SECONDS * 1000 if age is not None
                  else now - self._capture_signal_first_received_at >= self.CAPTURE_SILENCE_SECONDS)
        silence_message = "已连接播放设备，暂未检测到音乐软件声音" if self._group_mode else self.CAPTURE_SILENCE_MESSAGE
        if silent:
            self._publish(status_message=silence_message)
        elif current.status_message == silence_message:
            device = next((d for d in current.devices if d.id == self._cast_id), None)
            name = device.name if device is not None else "HomePod"
            self._publish(status_message=f"正在投放到 {name}" if current.state == "casting"
                          else "已连接 HomePod，等待电脑播放声音")

    def _poll_sender(self):
        if not self._cast_id:
            return
        session = (self._generation, self._cast_id)
        events = self._active_sender().poll_events()
        if self._closed.is_set() or session != (self._generation, self._cast_id):
            return
        # A terminal event in this batch wins over earlier stats/streaming.
        # Do not announce a healthy stream immediately before reporting loss.
        terminal = next((e for e in events if e.get("event") == "error"), None)
        if terminal is None:
            terminal = next((e for e in events if e.get("event") == "stopped"), None)
        if terminal is not None:
            message = str(terminal.get("message") or "HomePod 投放已中断")[:240]
            self._end_cast()
            self._refresh_devices()
            self._publish(state="error", status_message=message, pending_id="")
            return
        for event in events:
            if self._closed.is_set() or session != (self._generation, self._cast_id):
                break
            if event.get("event") == "waiting_audio":
                if self._capture_authenticated_at is None:
                    self._capture_authenticated_at = time.monotonic()
                self._publish(state="waiting_audio", status_message="已连接播放设备，等待音乐软件声音" if self._group_mode
                              else "已连接 HomePod，等待电脑播放声音")
            elif event.get("event") == "streaming":
                if self._group_mode:
                    self._group_streaming_seen = True
                    if self._capture_authenticated_at is None:
                        self._capture_authenticated_at = time.monotonic()
                    if self.snapshot().state == "connecting":
                        self._publish(state="waiting_audio", status_message="已连接播放设备，等待音乐软件声音")
                    self._observe_capture_signal(event)
                    self._confirm_group_streaming()
                    continue
                if self._capture_authenticated_at is None:
                    self._capture_authenticated_at = time.monotonic()
                device = next((d for d in self.snapshot().devices if d.id == self._cast_id), None)
                name = device.name if device is not None else "HomePod"
                changes = dict(active_id=self._cast_id, pending_id="", state="casting",
                               cast_latency_seconds=self._latency, status_message=f"正在投放到 {name}")
                if self._group_mode:
                    changes.update(member_ids=self._group_member_ids, pending_ids=(),
                                   status_message=f"正在同步播放到 {len(self._group_member_ids)} 个设备")
                self._publish(**changes)
                self._latency_updated_at = time.monotonic()
                self._announce(name, "HomePod 投放已连接")
            elif event.get("event") == "stats":
                self._observe_capture_signal(event)
                if self._group_mode and self._group_streaming_seen:
                    self._confirm_group_streaming()

    def _confirm_group_streaming(self):
        # Transport readiness and recent meaningful source PCM are separate
        # authorities. Either may arrive first; dither/stale stats are neither.
        if (not self._group_mode or not self._cast_id or not self._group_streaming_seen
                or self.snapshot().state == "casting" or self._capture_signal_nonzero is None
                or self._capture_signal_nonzero <= 0 or self._capture_signal_age_ms is None
                or self._capture_signal_age_ms > self.CAPTURE_SIGNAL_MAX_AGE * 1000
                or self._capture_signal_received_at is None
                or not 0 <= time.monotonic() - self._capture_signal_received_at <= self.CAPTURE_SIGNAL_MAX_AGE):
            return
        self._publish(active_id=self._cast_id, pending_id="", state="casting", member_ids=self._group_member_ids,
                      pending_ids=(), cast_latency_seconds=self._latency,
                      status_message=f"正在同步播放到 {len(self._group_member_ids)} 个设备")
        self._latency_updated_at = time.monotonic()
        self._announce("同步播放", "同步播放设备已连接")

    def _update_latency(self):
        """Track sender scheduling evidence without reconnecting or seeking.

        This estimate excludes receiver acoustics. A bounded slew keeps both
        row selection and the reading front continuous between 500 ms samples.
        Missing/stale evidence holds the last compensation instead of toggling
        back to a default each time capture is temporarily silent.
        """
        current = self.snapshot()
        if current.state != "casting" or not self._cast_id:
            return
        now = time.monotonic()
        elapsed = max(0.0, min(1.0, now - self._latency_updated_at)) if self._latency_updated_at is not None else 0.0
        self._latency_updated_at = now
        read = getattr(self._active_sender(), "latency_estimate", None)
        estimate = read() if callable(read) else None
        if estimate is None:
            if current.cast_latency_estimate_seconds is not None:
                self._publish(cast_latency_estimate_seconds=None)
            return
        try:
            target = float(estimate["seconds"])
        except (KeyError, TypeError, ValueError, OverflowError):
            self._publish(cast_latency_estimate_seconds=None)
            return
        if not math.isfinite(target) or not 0.0 <= target <= 5.0:
            self._publish(cast_latency_estimate_seconds=None)
            return
        changes = {"cast_latency_estimate_seconds": target}
        if self._auto_latency:
            difference = target - current.cast_latency_seconds
            step = max(-0.08 * elapsed, min(0.08 * elapsed, difference))
            changes["cast_latency_seconds"] = current.cast_latency_seconds + step
        self._publish(**changes)

    def _step(self):
        if self._closed.is_set():
            return
        with self._lock:
            commands = tuple(self._commands)
            self._commands.clear()
        for index, (operation, value) in enumerate(commands):
            self._remaining_commands = commands[index + 1:]
            if self._closed.is_set():
                return
            if operation == "refresh":
                self._discovery.start()
                self._refresh_devices()
            elif operation == "select":
                self._select(value)
            elif operation == "toggle":
                self._toggle(value)
            elif operation == "music_source":
                self._change_music_source(value)
            elif operation == "stop":
                self._end_cast()
                self._publish_stopped("HomePod 投放已停止")
                self._refresh_devices()
            elif operation == "volume":
                self._set_volume(*value)
            elif operation == "manual":
                host, port = value
                identity = "airplay:manual:" + host
                if len(self._manual_devices) >= 32 and identity not in self._manual_devices:
                    self._manual_devices.pop(next(iter(self._manual_devices)))
                self._manual_devices[identity] = AudioRouteDevice(identity, "HomePod · " + host,
                    "homepod", "airplay", host=host, port=port, device_id=host)
                # Manual targets describe an address, never a successful connection.
                self._merge_devices()
        self._remaining_commands = ()
        self._observe_pending_app_recovery()
        if (self._group_mode and not self._cast_id and self.snapshot().state == "waiting_source"
                and time.monotonic() >= self._next_source_resolve_at):
            self._try_start_group()
        self._check_group_source()
        self._poll_sender()
        self._update_latency()
        self._merge_devices()

    def _run(self):
        next_refresh = 0.0
        try:
            while not self._closed.is_set():
                self._wake.clear()
                try:
                    if time.monotonic() >= next_refresh:
                        self._refresh_devices()
                        next_refresh = time.monotonic() + self.POLL_SECONDS
                    self._step()
                except Exception as exc:
                    self._publish(state="error", pending_id="", status_message=f"音频输出异常：{str(exc)[:180]}")
                self._wake.wait(0.15)
        finally:
            self._dispose()

    def _dispose(self):
        app_close = getattr(self._app_router, "close", None)
        cleanups = [self._end_cast, self._discovery.close, self._backend.close]
        if callable(app_close):
            # _end_cast has already afforded the full restoration wait. Close
            # releases handles/lease and preserves the durable pending journal
            # after a zero-wait final check instead of repeating another 5 s.
            cleanups.insert(1, lambda: app_close(timeout=0.0))
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception:
                # Attempt every owned resource even after one cleanup fails.
                continue

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self._wake.set()
        if self._thread is not None:
            # Group graceful teardown: 8 * .8 + 1 s, forced reap 2 s, readers
            # 1 s, app stream restoration 5 s, plus bounded scheduling margin.
            group_cleanup = self._group_mode or self._app_route_guard is not None or self._last_sender is self._group_sender
            self._thread.join(timeout=17.5 if group_cleanup else 4.0)
        else:
            self._dispose()
