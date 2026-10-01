"""Serial Windows output switching and single-receiver AirPlay orchestration.

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
from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot


class AudioRouteController:
    POLL_SECONDS = 2.0

    def __init__(self, backend=None, discovery=None, sender=None, *, latency_seconds=1.5,
                 start_worker=True, source_id=""):
        if backend is None:
            from api.windows_audio_devices import WindowsAudioDevices
            backend = WindowsAudioDevices()
        self._backend = backend
        self._discovery = discovery or AirPlayDiscovery()
        self._sender = sender or AirPlayProcess(source_id=source_id)
        self._latency = self._normalize_latency(latency_seconds)
        self._lock = threading.Lock()
        self._snapshot = AudioRouteSnapshot(status_message="正在读取音频设备")
        self._commands = deque(maxlen=16)
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._thread = None
        self._local_devices = ()
        self._manual_devices = {}
        self._baseline = False
        self._cast_id = ""
        self._cast_source_id = ""
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
                self._commands = deque((c for c in self._commands if c[0] not in {"select", "stop"}), maxlen=16)
            elif any(c[0] == operation and c[1] == value for c in self._commands):
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

    def stop_cast(self):
        return self._enqueue("stop")

    def set_latency(self, value):
        return self._enqueue("latency", self._normalize_latency(value))

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
            local = tuple(AudioRouteDevice(d.id, d.name, d.kind, "windows", d.is_bluetooth, d.is_default)
                          for d in raw)
        except Exception as exc:
            self._endpoint_error = True
            if self._cast_id:
                self._end_cast()
            self._publish(state="error", status_message=f"读取音频设备失败：{str(exc)[:180]}")
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
        if self._cast_source_id and self._cast_source_id not in {d.id for d in local}:
            self._end_cast()
            self._publish(state="error", status_message="投放的音频来源已断开，请重新选择输出设备")
        elif self._cast_source_id and default_id and default_id != self._cast_source_id:
            self._end_cast()
            self._publish(state="error", status_message="电脑输出已改变，HomePod 投放已停止；请重新选择 HomePod")
        current = self.snapshot()
        active = self._cast_id or default_id
        state = current.state
        if not self._cast_id and state not in {"error", "switching"}:
            state = "ready" if local else "unavailable"
        self._publish(active_id=active, state=state)
        self._merge_devices()
        return True

    def _merge_devices(self):
        remote = {d.id: d for d in self._discovery.devices()}
        for identity, device in self._manual_devices.items():
            if not any(d.host == device.host for d in remote.values()):
                remote[identity] = device
        devices = self._local_devices + tuple(sorted(remote.values(), key=lambda d: (d.name.casefold(), d.id)))
        current = self.snapshot()
        # A receiver can disappear temporarily from mDNS while its session lives.
        if self._cast_id and self._cast_id not in {d.id for d in devices}:
            active = next((d for d in current.devices if d.id == self._cast_id), None)
            if active is not None:
                devices += (replace(active, available=False),)
        self._publish(devices=devices, discovery_state=self._discovery.state)

    def _end_cast(self):
        self._generation += 1
        try:
            self._sender.stop()
        finally:
            self._cast_id = self._cast_source_id = ""
            self._publish(pending_id="", cast_source_id="", cast_latency_seconds=0.0)

    def _select(self, identity):
        if self._closed.is_set() or not self._refresh_devices() or self._closed.is_set():
            return
        device = next((d for d in self.snapshot().devices if d.id == identity and d.available), None)
        if device is None:
            self._publish(state="error", pending_id="", status_message="设备已断开，请刷新后重试")
            return
        if device.id == self._cast_id and self.snapshot().state in {"connecting", "waiting_audio", "casting"}:
            return
        self._end_cast()
        self._publish(pending_id=device.id, state="switching" if device.transport == "windows" else "connecting",
                      status_message="正在切换输出" if device.transport == "windows" else "正在连接 HomePod")
        try:
            if device.transport == "windows":
                self._backend.set_default(device.id)
                self._publish(pending_id="", state="ready", status_message=f"已选择 {device.name}")
                if self._refresh_devices() and any(d.id == identity and d.is_default for d in self._local_devices):
                    self._announce(device.name, "已切换音频输出")
                else:
                    self._publish(state="error", status_message="无法确认实际音频输出，请刷新后重试")
            else:
                source = next((d for d in self._local_devices if d.is_default), None)
                if source is None:
                    raise RuntimeError("没有可用的电脑音频输出，无法获取播放器声音")
                if self._closed.is_set():
                    return
                self._sender.start(device, source.id)
                self._cast_id, self._cast_source_id = device.id, source.id
                self._publish(cast_source_id=source.id)
        except Exception as exc:
            self._end_cast()
            default_id = next((d.id for d in self._local_devices if d.is_default), "")
            self._publish(active_id=default_id, pending_id="", state="error", status_message=str(exc)[:240])

    def _poll_sender(self):
        if not self._cast_id:
            return
        for event in self._sender.poll_events():
            if event.get("event") == "waiting_audio":
                self._publish(state="waiting_audio", status_message="已连接 HomePod，等待电脑播放声音")
            elif event.get("event") == "streaming":
                device = next((d for d in self.snapshot().devices if d.id == self._cast_id), None)
                name = device.name if device is not None else "HomePod"
                self._publish(active_id=self._cast_id, pending_id="", state="casting",
                              cast_latency_seconds=self._latency, status_message=f"正在投放到 {name}")
                self._announce(name, "HomePod 投放已连接")
            elif event.get("event") in {"error", "stopped"}:
                message = str(event.get("message") or "HomePod 投放已中断")[:240]
                self._end_cast()
                self._refresh_devices()
                self._publish(state="error", status_message=message, pending_id="")
                break

    def _step(self):
        if self._closed.is_set():
            return
        with self._lock:
            commands = tuple(self._commands)
            self._commands.clear()
        for operation, value in commands:
            if self._closed.is_set():
                return
            if operation == "refresh":
                self._discovery.start()
                self._refresh_devices()
            elif operation == "select":
                self._select(value)
            elif operation == "stop":
                self._end_cast()
                self._publish(state="ready", status_message="HomePod 投放已停止")
                self._refresh_devices()
            elif operation == "latency":
                self._latency = value
                self._publish(cast_latency_seconds=value if self.snapshot().state == "casting" else 0.0)
            elif operation == "manual":
                host, port = value
                identity = "airplay:manual:" + host
                if len(self._manual_devices) >= 32 and identity not in self._manual_devices:
                    self._manual_devices.pop(next(iter(self._manual_devices)))
                self._manual_devices[identity] = AudioRouteDevice(identity, "HomePod · " + host,
                    "homepod", "airplay", host=host, port=port, device_id=host)
                # Manual targets describe an address, never a successful connection.
                self._merge_devices()
        self._poll_sender()
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
        for cleanup in (self._sender.stop, self._discovery.close, self._backend.close):
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
            self._thread.join(timeout=4.0)
        else:
            self._dispose()
