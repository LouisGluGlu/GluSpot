"""Owned-player routes reuse device evidence and the native shared PCM clock."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import ipaddress
import math
import threading
import time

from api.airplay_discovery import AirPlayDiscovery
from api.audio_route_controller import AudioRouteController
from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from api.audio_output_volume import AudioOutputVolumeHistory
from api.owned_pcm_process import OwnedPcmProcess


class OwnedAudioOutputController:
    POLL_SECONDS = 2.0

    def __init__(self, bridge, *, backend=None, discovery=None, sender=None, source_id="", start_worker=True):
        if backend is None:
            from api.windows_audio_devices import WindowsAudioDevices
            backend = WindowsAudioDevices()
        self.bridge = bridge
        bridge.attach(self)
        self._backend = backend
        self._discovery = discovery or AirPlayDiscovery()
        self._sender = sender or OwnedPcmProcess(source_id=source_id)
        self._lock = threading.RLock()
        self._snapshot = AudioRouteSnapshot(capture_scope="owned-player", status_message="正在读取播放设备")
        self._commands = deque(maxlen=16)
        self._wake, self._closed = threading.Event(), threading.Event()
        self._members = ()
        self._confirmed_receivers = {}
        self._volume_history = AudioOutputVolumeHistory()
        self._requested_members = None
        self._queued_members = None
        self._membership_baseline = 0
        self._handoff_pending = False
        self._manual = {}
        self._session_epoch = 0
        self._feeding_epoch = 0
        self._running = False
        self._streaming = False
        self._signal_fresh = False
        self._signal_deadline = 0.0
        self._single_id = ""
        self._token = bridge.media_token()
        self._last_inventory = 0.0
        self._thread = None
        if start_worker:
            self._thread = threading.Thread(target=self._run, name="GluSpotOwnedOutputs", daemon=True)
            self._thread.start()

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def _publish(self, **changes):
        with self._lock:
            result = replace(self._snapshot, **changes)
            if result != self._snapshot:
                self._snapshot = replace(result, revision=self._snapshot.revision + 1)

    def _enqueue(self, operation, value=None):
        if self._closed.is_set():
            return False
        with self._lock:
            if operation in {"select", "stop"}:
                self._commands = deque((job for job in self._commands if job[0] not in {"select", "toggle", "stop"}), maxlen=16)
            elif operation in {"media", "failure"}:
                self._commands = deque((job for job in self._commands if job[0] != operation), maxlen=16)
            elif operation == "volume":
                self._commands = deque((job for job in self._commands if job[0] != operation or job[1][0] != value[0]), maxlen=16)
            elif operation != "toggle" and (operation, value) in self._commands:
                return True
            self._commands.append((operation, value))
        self._wake.set()
        return True

    def refresh(self):
        return self._enqueue("refresh")

    def select(self, identity):
        if not any(d.id == identity and d.available for d in self.snapshot().devices):
            return False
        return self._enqueue("select", identity)

    def toggle(self, identity):
        if not any(d.id == identity and (d.available or identity in self._members) for d in self.snapshot().devices):
            return False
        return self._enqueue("toggle", identity)

    def set_volume(self, identity, value):
        volume = AudioRouteController._volume_scalar(value)
        device = next((d for d in self.snapshot().devices if d.id == identity), None)
        if (volume is None or device is None or device.volume is None
                or not device.available and device.transport != "airplay"):
            return False
        if device.transport == "airplay" and (identity not in self._members or not self._running):
            return False
        fence = self._volume_fence()
        self._volume_history.request(identity, volume, fence)
        return self._enqueue("volume", (identity, volume, fence, False))

    def _volume_fence(self):
        return (self._session_epoch, self.bridge.media_token())

    def _is_selected(self, identity):
        return identity in self._members if self._members else identity == self._single_id

    def toggle_mute(self, identity):
        device = next((d for d in self.snapshot().devices if d.id == identity), None)
        if (device is None or not self._is_selected(identity)
                or device.transport == "windows" and not device.available
                or device.transport == "airplay" and not self._running):
            return False
        fence = self._volume_fence()
        volume = self._volume_history.toggle(device, fence)
        return self._enqueue("volume", (identity, volume, fence, True))

    def add_homepod(self, address, port=7000):
        try:
            ip, port = ipaddress.ip_address(str(address).strip()), int(port)
            if ip.version != 4 or ip.is_unspecified or ip.is_multicast or not 1 <= port <= 65535:
                return False
        except (ValueError, TypeError):
            return False
        return self._enqueue("manual", (str(ip), port))

    def stop_cast(self):
        return self._enqueue("stop")

    def media_changed(self, token, *, force=False):
        return self._enqueue("media", (token, bool(force)))

    def pcm_failed(self, reason, epoch):
        with self._lock:
            if not self._feeding_epoch or epoch != self._feeding_epoch:
                return False
        return self._enqueue("failure", (reason, epoch, self.bridge.media_token()))

    def submit_pcm(self, content, epoch):
        with self._lock:
            if not self._feeding_epoch or epoch != self._feeding_epoch or self._closed.is_set():
                return False
        return self._sender.submit_pcm(content, epoch)

    def accepts_pcm(self, epoch):
        with self._lock:
            return bool(self._feeding_epoch and epoch == self._feeding_epoch and not self._closed.is_set())

    def _inventory(self):
        try:
            raw = self._backend.list_outputs()
            local = tuple(AudioRouteDevice(d.id, d.name, d.kind, "windows", d.is_bluetooth, d.is_default,
                volume=AudioRouteController._volume_scalar(getattr(d, "volume", None)),
                volume_muted=bool(getattr(d, "volume_muted", False)), **AudioRouteController._battery_fields(d)) for d in raw)
        except Exception:
            self._stop_group()
            self._publish(state="error", status_message="无法读取播放设备，请刷新重试")
            return False
        remote = {d.id: d for d in self._discovery.devices()}
        for key, device in self._manual.items():
            if not any(d.host == device.host for d in remote.values()):
                remote[key] = device
        previous = self.snapshot()
        if self._running:
            # mDNS disappearance is not transport failure. The authenticated
            # helper remains the authority for a connected receiver.
            for device in previous.devices:
                if device.transport == "airplay" and device.id in self._members and device.id not in remote:
                    remote[device.id] = replace(device, available=False)
        devices = local + tuple(sorted(remote.values(), key=lambda d: (d.name.casefold(), d.id)))
        ids = {d.id for d in devices if d.available}
        selected_local = {d.id for d in previous.devices if d.transport == "windows" and d.id in self._members}
        if self._running and selected_local - {d.id for d in local}:
            self._stop_group()
            self._publish(state="error", status_message="播放设备已断开，请重新选择")
        if not self._single_id:
            current = self.bridge.current_device()
            self._single_id = current if current in ids else next((d.id for d in local if d.is_default), "")
        elif self._single_id not in ids:
            current = self.bridge.current_device()
            self._single_id = current if current in ids else ""
        snapshot = self.snapshot()
        previous_ids = {d.id for d in snapshot.devices}
        selected = snapshot.member_ids
        pending = snapshot.pending_ids
        decorated = []
        for device in devices:
            if device.transport == "airplay" and self._running and device.id in self._members:
                evidence = self._sender.receiver_volume_state(device.id)
                volume = (AudioRouteController._volume_scalar(evidence.get("volume"))
                    if evidence.get("known") is True and evidence.get("source") in {"receiver_read", "receiver_info", "receiver_ack"} else None)
                device = replace(device, volume=volume, volume_muted=volume == 0 if volume is not None else False)
            decorated.append(replace(device, route_state="active" if device.id in selected else "pending" if device.id in pending else "available"))
            self._volume_history.observe(device, self._volume_fence())
        changes = dict(devices=tuple(decorated), discovery_state=self._discovery.state)
        if not self._running and not self._members:
            changes["active_id"] = self._single_id
            if snapshot.state == "idle":
                changes.update(state="ready", status_message="选择程序内音乐的播放设备")
        self._publish(**changes)
        if previous_ids:
            added = next((d for d in devices if d.id not in previous_ids), None)
            if added is not None:
                self._notice(added.name, "播放设备已接入", added.id)
        self._last_inventory = time.monotonic()
        return True

    def _notice(self, name, message, identity=""):
        current = self.snapshot()
        self._publish(event_serial=current.event_serial + 1, event_name=name, event_message=message, event_device_id=identity)

    def _stop_group(self, *, preserve_selection=False):
        epoch = self._session_epoch
        with self._lock:
            self._feeding_epoch = 0
        if self._running:
            self._sender.stop()
        self._running, self._streaming, self._signal_fresh = False, False, False
        self._signal_deadline = 0.0
        self._requested_members = self._queued_members = None
        self._membership_baseline = 0
        self._handoff_pending = False
        self._volume_history.reset_intents()
        if not preserve_selection:
            self._members = ()
            self._confirmed_receivers.clear()
        self.bridge.set_direct(False, epoch)
        self._publish(member_ids=(), pending_ids=(), pending_id="", group_id="", cast_latency_seconds=0,
            cast_latency_estimate_seconds=None, active_id=self._single_id)

    def _choose(self, identities, *, restart=False):
        identities = tuple(identities)
        if len(set(identities)) != len(identities) or len(identities) > 8:
            self._publish(status_message="同步播放最多支持 8 个不同输出设备")
            return
        devices = {d.id: d for d in self.snapshot().devices if d.available}
        # Keep the exact address of a receiver whose transport was confirmed
        # in this selected group. Repeated seek/track restarts may precede the
        # next confirmation while mDNS remains absent. Selection removal,
        # explicit stop and terminal failures all revoke this evidence.
        for key, device in self._confirmed_receivers.items():
            if key not in devices:
                devices[key] = replace(device, available=True)
        if any(key not in devices for key in identities):
            if restart:
                self._stop_group()
            self._publish(**({} if self._running else {"state": "error"}),
                status_message="播放设备已断开，请刷新重试")
            return
        selected = tuple(devices[key] for key in identities)
        # A capable owned helper retains authenticated transports and their
        # source epoch. Requests arriving during preparation replace only the
        # next intent, never tear down the still-confirmed group.
        hot = (not restart and identities and self._running and self._streaming
            and not self._handoff_pending and bool(self.snapshot().member_ids)
            and self.snapshot().state in {"casting", "reconfiguring"}
            and self._feeding_epoch == self._session_epoch and self._token == self.bridge.media_token()
            and callable(getattr(self._sender, "supports_pcm_reconfigure", None))
            and self._sender.supports_pcm_reconfigure(self._session_epoch))
        if hot:
            if self._requested_members is not None:
                self._queued_members = identities
                return
            if identities == self._members:
                return
            evidence = self._sender.membership_state()
            baseline = evidence.get("revision", 0)
            if self._sender.reconfigure_pcm(selected, self._session_epoch):
                self._requested_members = identities
                self._membership_baseline = baseline
                pending = tuple(key for key in identities if key not in self._members)
                self._publish(state="reconfiguring", member_ids=self._members,
                    pending_ids=pending, pending_id=pending[0] if pending else "",
                    status_message="正在更新同步设备，当前设备继续播放")
            else:
                self._publish(status_message="当前设备继续播放；无法加入，请稍后重试（连接期间最多 8 个设备）")
            return
        self._stop_group(preserve_selection=True)
        self._members = identities
        self._confirmed_receivers = {key: device for key, device in self._confirmed_receivers.items() if key in identities}
        if not identities:
            self._publish(state="ready", status_message="程序内同步播放已停止")
            return
        if len(selected) == 1 and selected[0].transport == "windows":
            if self.bridge.select_device(selected[0].id):
                self._single_id = selected[0].id
                self._members = ()
                self._publish(active_id=selected[0].id, state="ready", status_message=f"已选择 {selected[0].name}")
                self._notice(selected[0].name, "已切换音频输出", selected[0].id)
            else:
                self._members = ()
                self._publish(state="error", status_message="无法切换程序内播放设备，请刷新重试")
            return
        if not self.bridge.supported:
            self._members = ()
            self._publish(state="error", status_message="当前 Qt 播放组件不支持解码音频输出")
            return
        token = self.bridge.media_token()
        if not token[0]:
            self._publish(state="waiting_source", pending_ids=self._members, status_message="选择歌曲后开始同步播放")
            return
        self._session_epoch += 1
        self._token = token
        try:
            self._sender.start_pcm(selected, self._session_epoch)
            self._running = True
            self._publish(state="connecting", active_id="", pending_id=selected[0].id, pending_ids=self._members,
                group_id=f"owned:{self._session_epoch}", music_source_app_id="GluSpot", status_message="正在连接程序内播放设备")
        except Exception:
            self._sender.stop()
            self._members = ()
            self._confirmed_receivers.clear()
            self._publish(state="error", status_message="无法启动同步输出，请确认完整播放组件已安装")

    def _toggle(self, identity):
        intended = self._queued_members if self._queued_members is not None else self._requested_members
        selected = list(self._members if intended is None else intended)
        if not selected and self._single_id:
            selected = [self._single_id]
        if identity in selected:
            selected.remove(identity)
        else:
            selected.append(identity)
        if len(selected) > 8:
            self._publish(status_message="同步播放最多支持 8 个输出设备")
            return
        self._choose(tuple(selected))

    def _membership_changed(self):
        if self._requested_members is None:
            return
        evidence = self._sender.membership_state()
        if (evidence.get("pcm_epoch") != self._session_epoch
                or evidence.get("revision", 0) <= self._membership_baseline
                or evidence.get("state") not in {"applied", "rejected"}):
            return
        requested, queued = self._requested_members, self._queued_members
        applied = evidence["state"] == "applied"
        expected = requested if applied else self._members
        if set(evidence.get("confirmed_ids", ())) != set(expected):
            return
        if applied:
            self._members = requested
            devices = {d.id: d for d in self.snapshot().devices}
            self._confirmed_receivers = {key: replace(devices[key], available=True) for key in requested
                if key in devices and devices[key].transport == "airplay"}
        self._requested_members = self._queued_members = None
        self._publish(state="casting", member_ids=self._members, active_id=self._members[0],
            pending_id="", pending_ids=(), status_message=(f"正在同步播放到 {len(self._members)} 个设备"
                if applied else "新设备连接失败，当前设备继续播放"))
        if applied:
            self._notice("同步播放", "同步设备已更新")
        self._inventory()
        if queued is not None and queued != self._members:
            self._choose(queued)

    def _poll(self):
        if not self._running:
            return
        if self._sender.pcm_error():
            self._stop_group()
            self._members = ()
            self._publish(state="error", status_message="同步音频传输中断，已恢复程序内输出")
            return
        events = self._sender.poll_events()
        if any(event.get("event") in {"error", "stopped"} for event in events):
            self._stop_group()
            self._members = ()
            self._publish(state="error", status_message="同步播放已中断，已恢复程序内输出")
            return
        for event in events:
            kind = event.get("event")
            if kind == "waiting_audio" and not self._feeding_epoch:
                with self._lock:
                    self._feeding_epoch = self._session_epoch
                capability = getattr(self._sender, "supports_owned_presentation", None)
                self._handoff_pending = bool(callable(capability) and capability(self._session_epoch))
                enabled = (self.bridge.set_direct(True, self._session_epoch, self._token, muted=False)
                    if self._handoff_pending else self.bridge.set_direct(True, self._session_epoch, self._token))
                if enabled:
                    self._publish(state="waiting_audio", status_message="播放设备已连接，等待程序内音乐")
                else:
                    self._stop_group()
                    self._publish(state="error", status_message="歌曲已改变，请重新选择同步设备")
                    return
            elif kind == "streaming":
                self._streaming = True
            elif kind == "stats":
                signal = event.get("capture_signal", {})
                group = event.get("group", {})
                count, age = signal.get("meaningful_samples"), signal.get("last_meaningful_age_ms")
                received = event.get("_received_at")
                self._signal_fresh = (signal.get("mode") == "direct_pcm" and signal.get("scope") == "owned-player"
                    and group.get("pcm_epoch") == self._session_epoch
                    and isinstance(count, int) and not isinstance(count, bool) and count > 0
                    and isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age <= 2000
                    and isinstance(received, (int, float)) and not isinstance(received, bool)
                    and 0 <= time.monotonic() - received and time.monotonic() - received + age / 1000 <= 2)
                self._signal_deadline = received + 2 - age / 1000 if self._signal_fresh else 0.0
            if (self._streaming and self._signal_fresh and time.monotonic() <= self._signal_deadline
                    and self.snapshot().state in {"connecting", "waiting_audio"}):
                if self._handoff_pending:
                    if not self.bridge.commit_direct_mute(self._session_epoch, self._token):
                        self._stop_group()
                        self._publish(state="error", status_message="歌曲已改变，已恢复程序内输出")
                        return
                    self._handoff_pending = False
                devices = {d.id: d for d in self.snapshot().devices}
                self._confirmed_receivers = {key: replace(devices[key], available=True) for key in self._members
                    if key in devices and devices[key].transport == "airplay"}
                self._publish(state="casting", active_id=self._members[0], pending_id="", pending_ids=(), member_ids=self._members,
                    cast_latency_seconds=1.75,
                    status_message=f"正在同步播放到 {len(self._members)} 个设备")
                self._notice("同步播放", "同步播放设备已连接")
        self._membership_changed()
        estimate = self._sender.latency_estimate()
        if self.snapshot().state in {"casting", "reconfiguring"} and estimate is not None:
            self._publish(cast_latency_seconds=estimate["seconds"], cast_latency_estimate_seconds=estimate["seconds"])

    def _step(self):
        with self._lock:
            commands, self._commands = tuple(self._commands), deque(maxlen=16)
        for operation, value in commands:
            if self._closed.is_set():
                return
            if operation == "refresh":
                self._discovery.start()
                self._inventory()
            elif operation == "select":
                self._choose((value,))
            elif operation == "toggle":
                self._toggle(value)
            elif operation == "stop":
                self._choose(())
            elif operation == "media":
                token, force = value
                if force or token != self._token:
                    self._token = token
                    if self._members:
                        intended = self._queued_members if self._queued_members is not None else self._requested_members
                        self._choose(self._members if intended is None else intended, restart=True)
            elif operation == "failure":
                _, epoch, token = value
                if (not self._running or epoch != self._session_epoch or token != self._token
                        or token != self.bridge.media_token()):
                    continue
                self._stop_group()
                self._members = ()
                self._publish(state="error", status_message="同步音频中断，已恢复程序内输出")
            elif operation == "manual":
                host, port = value
                key = "airplay:manual:" + host
                self._manual[key] = AudioRouteDevice(key, "HomePod " + host, "homepod", "airplay", host=host, port=port)
                self._inventory()
            elif operation == "volume":
                identity, volume, fence, selected_only = value
                if fence != self._volume_fence() or selected_only and not self._is_selected(identity):
                    continue
                device = next((d for d in self.snapshot().devices if d.id == identity), None)
                succeeded = False
                if device and device.transport == "windows":
                    try:
                        if device.available:
                            result = self._backend.set_endpoint_volume(identity, volume)
                            succeeded = result is not False
                            if succeeded and volume > 0 and self._is_selected(identity) and device.volume_muted:
                                actual = AudioRouteController._volume_scalar(result)
                                if actual is None:
                                    getter = getattr(self._backend, "get_endpoint_volume", None)
                                    actual = AudioRouteController._volume_scalar(getter(identity) if callable(getter) else
                                        next((d.volume for d in self._backend.list_outputs() if d.id == identity), None))
                                # Confirm the requested scalar before releasing a
                                # hardware mute, including with injected backends.
                                succeeded = actual is not None and math.isclose(actual, volume, abs_tol=.005)
                                if succeeded:
                                    succeeded = self._backend.set_endpoint_mute(identity, False) is not False
                    except Exception:
                        succeeded = False
                elif device and device.transport == "airplay" and self._running and identity in self._members:
                    succeeded = self._sender.set_device_volume(identity, volume) is not False
                self._inventory()
                if not succeeded:
                    self._volume_history.discard(identity, fence, value=volume)
                    self._publish(status_message="设备音量调整失败，请重试")
        self._poll()
        if time.monotonic() - self._last_inventory >= self.POLL_SECONDS:
            self._inventory()

    def _run(self):
        try:
            while not self._closed.is_set():
                try:
                    self._step()
                except Exception:
                    self._stop_group()
                    self._publish(state="error", status_message="播放设备服务暂不可用，请刷新重试")
                self._wake.wait(.025 if self._running else .25)
                self._wake.clear()
        finally:
            self._stop_group()
            self._discovery.close()
            self._backend.close()

    def diagnostics(self):
        result = self._sender.diagnostics()
        result["owned_output_members"] = self._members
        return result

    def close(self):
        self._closed.set()
        with self._lock:
            self._feeding_epoch = 0
        # The application closes this controller on Qt's thread. Restore its
        # private mute now, before aboutToQuit stops servicing queued requests;
        # native teardown remains on the routing worker.
        self.bridge.set_direct(False, self._session_epoch)
        self._wake.set()
        if self._thread is None:
            self._stop_group()
            self._discovery.close()
            self._backend.close()
