"""Real Qt decode/clock smoke via a loopback HTTP WAV and MusicTasks workers.

No account, external service, captured audio or global device-volume API is
used. Only this player's QAudioOutput volume is set to zero before playback.
Generated WAV, diagnostics and lyric-cache fixtures stay under .runtime.
"""
from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path
import struct
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer, qVersion
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineSong, StreamInfo
from local.music_tasks import MusicTasks
from local.owned_music_player import OwnedMusicPlayer


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        # Nonzero deterministic PCM exercises decoding. The QAudioOutput is
        # silent independently of system endpoint mute and volume settings.
        samples = bytearray()
        for index in range(2 * 44100):
            value = round(1800 * math.sin(index * math.tau * 440 / 44100))
            samples.extend(struct.pack("<hh", value, value))
        output.writeframes(samples)
    return buffer.getvalue()


def run(output: Path, timeout: float) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    payload = _wav_bytes()
    (output / "fixture.wav").write_bytes(payload)
    started = time.monotonic()
    main_thread = threading.get_ident()
    requests = []
    service_calls = []
    states, statuses, positions, errors, owned_statuses = [], [], [], [], []

    def elapsed():
        return round(time.monotonic() - started, 6)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != "/fixture.wav":
                self.send_error(404)
                return
            low, high = 0, len(payload) - 1
            header = self.headers.get("Range", "")
            partial = False
            if header.startswith("bytes="):
                parts = header[6:].split("-", 1)
                try:
                    low = int(parts[0]) if parts[0] else 0
                    high = min(high, int(parts[1])) if parts[1] else high
                    partial = True
                except (ValueError, IndexError):
                    self.send_error(416)
                    return
            if not 0 <= low <= high < len(payload):
                self.send_error(416)
                return
            body = payload[low:high + 1]
            requests.append({"at": elapsed(), "bytes": len(body), "partial": partial})
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            if partial:
                self.send_header("Content-Range", f"bytes {low}-{high}/{len(payload)}")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, name="GluSpotLoopbackFixture", daemon=True)
    server_thread.start()
    loopback_url = f"http://127.0.0.1:{server.server_port}/fixture.wav"

    class FixtureService:
        def stream_url(self, source_id, song_id):
            service_calls.append({"operation": "stream", "worker_thread": threading.get_ident() != main_thread})
            return StreamInfo(source_id, song_id, loopback_url, True, quality="fixture-pcm")

        def lyrics(self, source_id, song_id):
            service_calls.append({"operation": "lyrics", "worker_thread": threading.get_ident() != main_thread})
            return LyricPayload(source_id, song_id, raw_lrc="[00:00.00]Loopback fixture\n[00:01.00]Real Qt clock", source="local-http-fixture")

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    audio = QAudioOutput()
    audio.setVolume(0.0)
    tasks = MusicTasks()
    owned = OwnedMusicPlayer(FixtureService(), LocalLyricsStore(root=output / "lyrics"), audio=audio, tasks=tasks)
    player = owned.player
    player.playbackStateChanged.connect(lambda state: states.append({"at": elapsed(), "state": state.name}))
    player.mediaStatusChanged.connect(lambda status: statuses.append({"at": elapsed(), "status": status.name}))
    player.positionChanged.connect(lambda milliseconds: positions.append({"at": elapsed(), "milliseconds": milliseconds}))
    player.errorOccurred.connect(lambda code, _text: errors.append({"at": elapsed(), "code": code.name}))
    owned.statusChanged.connect(lambda status: owned_statuses.append({"at": elapsed(), "status": status}))
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(20)
    timed_out = False

    def check():
        nonlocal timed_out
        if owned.status in {"ended", "playback_failed", "stream_unavailable"}:
            loop.quit()
        elif time.monotonic() - started > timeout:
            timed_out = True
            loop.quit()

    timer.timeout.connect(check)
    accepted = False
    try:
        accepted = owned.play_queue((OnlineSong("netease", "loopback-fixture", "Qt decode clock fixture", duration_seconds=2.0),))
        timer.start()
        loop.exec()
        last_track = owned.track
        observed_positions = [sample["milliseconds"] for sample in positions]
        progressed = len(set(observed_positions)) >= 3 and max(observed_positions, default=0) >= 1500
        checks = {
            "queue_accepted": bool(accepted),
            "real_qmedia_player": type(player) is QMediaPlayer,
            "real_music_tasks": type(tasks) is MusicTasks,
            "service_calls_off_gui_thread": bool(service_calls) and all(call["worker_thread"] for call in service_calls),
            "http_fixture_requested": bool(requests),
            "qt_playing_state_observed": any(sample["state"] == "PlayingState" for sample in states),
            "qt_positions_advanced": progressed,
            "qt_positions_monotonic": bool(observed_positions) and all(a <= b for a, b in zip(observed_positions, observed_positions[1:])),
            "qt_end_of_media_observed": any(sample["status"] == "EndOfMedia" for sample in statuses),
            "owned_end_observed": owned.status == "ended",
            "provider_clock_advanced": last_track is not None and last_track.position_seconds >= 1.5,
            "player_output_volume_zero": audio.volume() == 0.0,
            "no_qt_errors": not errors,
            "no_timeout": not timed_out,
        }
        report = {
            "passed": all(checks.values()), "checks": checks, "qt_version": qVersion(),
            "elapsed_seconds": elapsed(), "fixture": {"duration_seconds": 2, "sample_rate": 44100, "channels": 2, "pcm_bits": 16, "bytes": len(payload)},
            "scope": "Loopback HTTP and generated PCM only; no accounts, external requests, capture, global mute or global volume writes.",
            "limits": "Real Qt decoding, worker handoff, playback clock and EOF only; not audible playback, online authentication, AirPlay, acoustic synchronization or long-session validation.",
            "requests": list(requests), "service_calls": list(service_calls), "playback_states": list(states),
            "media_statuses": list(statuses), "positions": list(positions), "owned_statuses": list(owned_statuses), "errors": list(errors),
            "final_track": None if last_track is None else {"position_seconds": last_track.position_seconds, "duration_seconds": last_track.duration_seconds, "position_source": last_track.position_source, "is_playing": last_track.is_playing},
        }
    finally:
        timer.stop()
        owned.close()
        app.processEvents()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-playback-smoke"))
    parser.add_argument("--timeout", type=float, default=12.0)
    options = parser.parse_args()
    report = run(options.output.resolve(), max(3.0, options.timeout))
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "elapsed_seconds": report["elapsed_seconds"], "result": str((options.output / "result.json").resolve())}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
