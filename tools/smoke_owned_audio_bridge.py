"""Muted local-WAV decode through the actual Qt PCM output bridge."""
from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import time
import struct
import wave
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QEventLoop, QTimer, QUrl, Signal, qVersion
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

from local.owned_audio_bridge import OwnedAudioBridge


def run(timeout=10, source_rate=48000, source_channels=1):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    class Owner(QObject):
        changed = Signal()
        def __init__(self):
            super().__init__()
            self.track, self._epoch = SimpleNamespace(track_id="silent-pcm-fixture"), 1
            self.audio, self.player = QAudioOutput(self), QMediaPlayer(self)
            self.audio.setVolume(0)
            self.player.setAudioOutput(self.audio)
    owner = Owner()
    bridge = OwnedAudioBridge(owner)
    packets, errors, failures = [], [], []
    controller = SimpleNamespace(accepts_pcm=lambda epoch: epoch == 1,
        submit_pcm=lambda content, epoch: packets.append((len(content), epoch, any(content))) or True,
        pcm_failed=lambda reason, epoch: failures.append((reason, epoch)), media_changed=lambda *_args, **_kwargs: None)
    bridge.attach(controller)
    owner.player.errorOccurred.connect(lambda code, _raw: errors.append(code.name))
    started, max_position = time.monotonic(), 0
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(20)
    def check():
        nonlocal max_position
        max_position = max(max_position, owner.player.position())
        if (owner.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia or errors or failures
                or time.monotonic() - started > timeout):
            loop.quit()
    timer.timeout.connect(check)
    with tempfile.TemporaryDirectory(prefix="GluSpot-owned-pcm-smoke-") as root:
        path = Path(root) / "fixture.wav"
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(source_channels)
            wav.setsampwidth(2)
            wav.setframerate(source_rate)
            content = bytearray()
            for frame in range(source_rate * 2):
                value = round(1800 * math.sin(frame * math.tau * 440 / source_rate))
                content.extend(struct.pack("<h", value) * source_channels)
            wav.writeframes(content)
        path.write_bytes(buffer.getvalue())
        owner.player.setSource(QUrl.fromLocalFile(str(path)))
        bridge.set_direct(True, 1)
        timer.start()
        owner.player.play()
        loop.exec()
        timer.stop()
        checks = {"qt_buffer_output_available": bridge.supported, "actual_pcm_buffers_received": bool(packets),
            "nonzero_decoded_pcm": any(nonzero for _, _, nonzero in packets),
            "bounded_aligned_packets": bool(packets) and all(0 < size <= 65536 and size % 4 == 0 for size, _, _ in packets),
            "current_session_epoch_only": bool(packets) and all(epoch == 1 for _, epoch, _ in packets),
            "qt_position_advanced": max_position >= 1200, "own_output_volume_zero": owner.audio.volume() == 0,
            "own_output_muted_during_forwarding": owner.audio.isMuted(), "no_qt_error": not errors, "no_bridge_failure": not failures}
        bridge.close()
        checks["own_mute_restored"] = not owner.audio.isMuted()
        owner.player.stop()
        owner.player.setSource(QUrl())
        app.processEvents()
    return {"passed": all(checks.values()), "checks": checks, "qt_version": qVersion(),
        "source_rate": source_rate, "source_channels": source_channels,
        "packet_count": len(packets), "decoded_pcm_bytes": sum(size for size, _, _ in packets),
        "max_position_ms": max_position, "elapsed_seconds": round(time.monotonic() - started, 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-audio-bridge-smoke.json"))
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--source-rate", type=int, choices=(44100, 48000), default=48000)
    parser.add_argument("--source-channels", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    result = run(args.timeout, args.source_rate, args.source_channels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
