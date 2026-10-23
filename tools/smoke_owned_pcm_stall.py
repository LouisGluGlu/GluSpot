"""Muted Qt decode -> real PCM pipe -> localhost AP2, with a GUI stall.

Uses synthetic audio and the existing authenticated receiver fixture. No
physical receiver, account, system default or endpoint volume is changed.
"""
from __future__ import annotations

import argparse
import faulthandler
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QObject, QEventLoop, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice
from api.owned_pcm_process import OwnedPcmProcess
from local.owned_audio_bridge import OwnedAudioBridge


def run(seconds=120, stall_at=10, stall_ms=2600, executable=None):
    root = Path(__file__).resolve().parents[1]
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    class Owner(QObject):
        changed = Signal()

        def __init__(self):
            super().__init__()
            self.track = SimpleNamespace(track_id="synthetic-stall-fixture")
            self._epoch = 1
            self.audio, self.player = QAudioOutput(self), QMediaPlayer(self)
            self.audio.setVolume(0)
            self.player.setAudioOutput(self.audio)

    peer = subprocess.Popen([str(root / "build/native-airplay/gluspot_owned_membership_receiver_fixture.exe")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
    owner, sender = Owner(), OwnedPcmProcess(executable)
    bridge = OwnedAudioBridge(owner)
    failures, samples = [], []
    active = stalled = False
    start = None
    high_water = 0
    bridge.attach(SimpleNamespace(
        accepts_pcm=lambda epoch: active and epoch == 1,
        submit_pcm=sender.submit_pcm,
        pcm_failed=lambda reason, epoch: failures.append([reason, epoch]),
        media_changed=lambda *_args, **_kwargs: failures.append(["unexpected_epoch_change", 1])))
    owner.player.errorOccurred.connect(lambda code, _text: failures.append([code.name, 1]))
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(10)
    began = time.monotonic()

    def check():
        nonlocal active, stalled, start, high_water
        for event in sender.poll_events():
            if event["event"] == "waiting_audio" and not active:
                active = True
                bridge.set_direct(True, 1)
                start = time.monotonic()
                owner.player.play()
            elif event["event"] == "error":
                failures.append([event.get("message", "native_error"), 1])
            elif event["event"] == "stats":
                samples.append(event)
        high_water = max(high_water, sender._pcm_bytes)
        if start is not None and not stalled and time.monotonic() - start >= stall_at:
            stalled = True
            # Model the observed 2.59-second native Finder/UI stall. Sleep
            # releases the GIL; the native sender and pipe workers stay live.
            time.sleep(stall_ms / 1000)
        if (failures or sender.pcm_error() or time.monotonic() - began > seconds + 10
                or start is not None and time.monotonic() - start >= seconds):
            loop.quit()

    timer.timeout.connect(check)
    try:
        port = json.loads(peer.stdout.readline())["ports"][0]
        device = AudioRouteDevice("airplay:stall-fixture", "Local AP2 fixture", transport="airplay",
            host="127.0.0.1", port=port, device_id="stall-fixture")
        with tempfile.TemporaryDirectory(prefix="GluSpot-stall-") as folder:
            path = Path(folder) / "synthetic.wav"
            values = (2000 * np.sin(np.arange(44100) * np.pi * 880 / 44100)).astype("<i2")
            second = np.repeat(values[:, None], 2, axis=1).tobytes()
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(2)
                audio.setsampwidth(2)
                audio.setframerate(44100)
                for _ in range(int(seconds) + 10):
                    audio.writeframesraw(second)
            owner.player.setSource(QUrl.fromLocalFile(str(path)))
            sender.start_pcm((device,), 1)
            process = sender._process
            timer.start()
            loop.exec()
            timer.stop()
            diagnostics = sender.diagnostics()
            before_stop = process.poll()
            bridge.close()
            owner.player.stop()
            owner.player.setSource(QUrl())
            app.processEvents()
        peer.stdin.write("stats\n")
        peer.stdin.flush()
        received = json.loads(peer.stdout.readline())["receivers"][0]
        protocol = samples[-1].get("protocol", {}) if samples else {}
        elapsed = time.monotonic() - start if start is not None else 0
        checks = {
            "completed_duration": elapsed >= seconds,
            "gui_stall_injected": stalled,
            "same_live_helper": before_stop is None,
            "no_pcm_failure": not failures and not diagnostics["pcm_error"],
            "receiver_decrypts_audio": received["packets"] > 100 and not received["decrypt_failures"],
            "receiver_packet_continuity": not received["continuity_failures"],
            "feedback_kept_alive": protocol.get("feedback_replies", 0) >= int(seconds / 2) - 2,
            "no_capture_drops": samples and samples[-1].get("dropped_samples") == 0,
        }
        return {"passed": all(checks.values()), "checks": checks, "failures": failures,
            "pcm_error": diagnostics["pcm_error"], "terminal": diagnostics["terminal"],
            "elapsed_seconds": round(elapsed, 3), "stall_ms": stall_ms,
            "queue_high_water_bytes": high_water, "pcm_queue": diagnostics["pcm_queue"],
            "last_stats": samples[-1] if samples else {}}
    finally:
        timer.stop()
        sender.stop()
        bridge.close()
        owner.player.stop()
        if peer.poll() is None:
            peer.stdin.write("stop\n")
            peer.stdin.flush()
            try:
                peer.wait(timeout=3)
            except subprocess.TimeoutExpired:
                peer.kill()
                peer.wait(timeout=2)
        for stream in (peer.stdin, peer.stdout):
            stream.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--stall-at", type=float, default=10)
    parser.add_argument("--stall-ms", type=int, default=2600)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path, default=Path("logs/owned-pcm-stall.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    trace = open(args.output.with_suffix(".stack.log"), "w", encoding="utf-8")
    faulthandler.dump_traceback_later(args.seconds + 15, file=trace)
    try:
        report = run(args.seconds, args.stall_at, args.stall_ms, args.executable)
    finally:
        faulthandler.cancel_dump_traceback_later()
        trace.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "last_stats"}))
    raise SystemExit(0 if report["passed"] else 1)
