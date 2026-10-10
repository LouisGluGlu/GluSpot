"""Qt seek/track epochs -> authenticated PCM -> existing silent Windows output.

Runs only when a Windows output is already muted or has zero endpoint volume.
It changes no endpoint volume/mute, system default, account or receiver state.
Only the smoke player's private QAudioOutput is muted while forwarding PCM.
The normal controller switches a single Windows output directly in Qt. This
probe exercises its separately tested bridge/transport restart boundary using
one native renderer, without inventing a second device or contacting a receiver.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QEventLoop, QTimer, QUrl, Signal, qInstallMessageHandler
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication
from api.audio_routes import AudioRouteDevice
from api.owned_pcm_process import OwnedPcmProcess
from api.windows_audio_devices import WindowsAudioDevices
from local.owned_audio_bridge import OwnedAudioBridge


def run(timeout=35, executable=None):
    # Decoder diagnostics could include a source URL in a future fixture;
    # discard them rather than treating arbitrary strings as report fields.
    null = open(os.devnull, "w")
    os.dup2(null.fileno(), 2)
    qInstallMessageHandler(lambda *_: None)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    backend = WindowsAudioDevices()
    outputs = backend.list_outputs()
    before = {d.id: (backend.get_endpoint_mute(d.id), backend.get_endpoint_volume(d.id)) for d in outputs}
    default_before = tuple(d.id for d in outputs if d.is_default)
    silent = next((d for d in outputs if before[d.id][0] is True or before[d.id][1] == 0), None)
    if silent is None:
        backend.close()
        return {"passed": False, "reason": "no_existing_silent_endpoint", "endpoint_state_changed": False}
    class Owner(QObject):
        changed = Signal()
        def __init__(self):
            super().__init__()
            self.track, self._epoch = SimpleNamespace(track_id="local-native-pcm-fixture"), 1
            self.audio, self.player = QAudioOutput(self), QMediaPlayer(self)
            self.audio.setVolume(0)
            self.player.setAudioOutput(self.audio)
    owner, sender = Owner(), OwnedPcmProcess(executable)
    bridge = OwnedAudioBridge(owner)
    active, epoch, restart_pending = False, 0, False
    packets, failures, qt_errors, events = [], [], [], []
    stopped_processes, stopped_writers, rejected_old_pcm, separate_queues = [], [], [], []
    proven_epochs, streaming_epochs = set(), set()
    transitions, pending_since, paused_restart = set(), None, False
    def forward(content, incoming_epoch):
        packets.append((len(content), incoming_epoch))
        return sender.submit_pcm(content, incoming_epoch)
    def changed(_token, **_kwargs):
        nonlocal restart_pending
        restart_pending = True
    controller = SimpleNamespace(accepts_pcm=lambda incoming: active and incoming == epoch, submit_pcm=forward,
        pcm_failed=lambda reason, incoming: failures.append((reason, incoming)), media_changed=changed)
    bridge.attach(controller)
    owner.player.errorOccurred.connect(lambda code, _raw: qt_errors.append(code.name))
    started = time.monotonic()
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(20)
    device = AudioRouteDevice(silent.id, silent.name, silent.kind)
    error = ""
    def restart():
        nonlocal active, epoch, restart_pending, pending_since
        previous = epoch
        process, writer, pending = sender._process, sender._pcm_thread, sender._pcm_queue
        active = False
        if previous:
            bridge.set_direct(False, previous)
        sender.stop()
        if previous:
            stopped_processes.append(process is not None and process.poll() is not None)
            stopped_writers.append(writer is not None and not writer.is_alive())
            rejected_old_pcm.append(not sender.submit_pcm(b"\x10\0" * 2, previous))
        epoch += 1
        sender.start_pcm((device,), epoch)
        if previous:
            separate_queues.append(sender._pcm_queue is not pending)
            rejected_old_pcm.append(not sender.submit_pcm(b"\x10\0" * 2, previous))
        restart_pending, pending_since = False, time.monotonic()
    def check():
        nonlocal active, paused_restart, pending_since, error
        if restart_pending:
            try:
                restart()
            except Exception:
                error = "restart"
                loop.quit()
                return
        current = sender.poll_events()
        events.extend((event.get("event"), epoch) for event in current)
        for event in current:
            if event.get("event") == "waiting_audio" and not active:
                active = True
                if not bridge.set_direct(True, epoch):
                    error = "bridge_start"
                if epoch == 1:
                    owner.player.play()
                if epoch == 4:
                    paused_restart = owner.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
                    pending_since = time.monotonic()
            if event.get("event") == "streaming":
                streaming_epochs.add(epoch)
            if event.get("event") == "stats":
                signal, group = event.get("capture_signal", {}), event.get("group", {})
                locals_ = event.get("local_renders", [])
                if (signal.get("mode") == "direct_pcm" and signal.get("scope") == "owned-player"
                    and signal.get("meaningful_samples", 0) > 0 and group.get("pcm_epoch") == epoch
                    and len(locals_) == 1 and locals_[0].get("endpoint_id") == silent.id
                    and all(locals_[0].get(key) is True for key in ("ready", "armed", "playing"))
                    and locals_[0].get("meaningful_samples", 0) > 0):
                    proven_epochs.add(epoch)
        if epoch == 4 and active and paused_restart and pending_since is not None and time.monotonic() - pending_since >= .25:
            owner.player.play()
            pending_since = None
        if epoch in proven_epochs and epoch in streaming_epochs and epoch not in transitions:
            transitions.add(epoch)
            if epoch == 1:
                owner.player.setPosition(500)
            elif epoch == 2:
                owner.player.pause()
                owner.track, owner._epoch = SimpleNamespace(track_id="second-local-native-pcm-fixture"), 2
                owner.changed.emit()
                owner.player.setSource(QUrl.fromLocalFile(str(second_path)))
                owner.player.play()
            elif epoch == 3:
                owner.player.pause()
                owner.player.setPosition(12000)
            elif epoch == 4:
                loop.quit()
        if (qt_errors or failures or sender.pcm_error() or error
                or any(kind == "error" for kind, _ in events) or time.monotonic() - started >= timeout):
            loop.quit()
    timer.timeout.connect(check)
    with tempfile.TemporaryDirectory(prefix="GluSpot-native-pcm-smoke-") as root:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            data = bytearray()
            for frame in range(48000 * 18):
                data.extend(struct.pack("<h", round(1800 * math.sin(frame * math.tau * 440 / 48000))))
            wav.writeframes(data)
        path = Path(root) / "fixture.wav"
        path.write_bytes(buffer.getvalue())
        second_path = Path(root) / "second-fixture.wav"
        second_path.write_bytes(buffer.getvalue())
        owner.player.setSource(QUrl.fromLocalFile(str(path)))
        try:
            restart()
            timer.start()
            loop.exec()
        except Exception:
            error = "backend"
        finally:
            timer.stop()
            active = False
            process, writer = sender._process, sender._pcm_thread
            sender.stop()
            stopped_processes.append(process is not None and process.poll() is not None)
            stopped_writers.append(writer is not None and not writer.is_alive())
            bridge.close()
            owner.player.stop()
            owner.player.setSource(QUrl())
            app.processEvents()
    after = {d.id: (backend.get_endpoint_mute(d.id), backend.get_endpoint_volume(d.id)) for d in outputs}
    default_after = tuple(d.id for d in backend.list_outputs() if d.is_default)
    backend.close()
    checks = {"existing_silent_endpoint_only": True, "system_default_unchanged": default_before == default_after,
        "endpoint_mute_volume_unchanged": before == after, "qt_pcm_received": bool(packets),
        "native_actual_meaningful_render_all_epochs": proven_epochs == {1, 2, 3, 4},
        "native_streaming_all_epochs": streaming_epochs == {1, 2, 3, 4},
        "seek_track_paused_seek_restarted": transitions == {1, 2, 3, 4},
        "paused_seek_replaced_native_before_resume": paused_restart,
        "old_helper_processes_terminated": len(stopped_processes) == 4 and all(stopped_processes),
        "old_pipe_writers_cancelled": len(stopped_writers) == 4 and all(stopped_writers),
        "old_pcm_rejected_before_and_after_restart": len(rejected_old_pcm) == 6 and all(rejected_old_pcm),
        "new_epoch_uses_fresh_pcm_queue": len(separate_queues) == 3 and all(separate_queues),
        "pcm_from_all_four_epochs": {incoming for _, incoming in packets} == {1, 2, 3, 4},
        "own_output_mute_restored": not owner.audio.isMuted(), "no_qt_error": not qt_errors,
        "no_bridge_error": not failures,
        "no_native_error": not any(kind == "error" for kind, _ in events) and not sender.pcm_error() and not error}
    return {"passed": all(checks.values()), "checks": checks, "packet_count": len(packets),
        "decoded_pcm_bytes": sum(size for size, _ in packets), "native_epochs": sorted(proven_epochs),
        "elapsed_seconds": round(time.monotonic() - started, 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-native-pcm-smoke.json"))
    parser.add_argument("--timeout", type=float, default=35)
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    result = run(args.timeout, args.executable)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
