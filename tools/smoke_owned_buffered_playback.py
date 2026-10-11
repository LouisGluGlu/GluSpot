"""Verified complete MP3 transfer before muted Qt playback, using loopback only.

Exercises truncation/resume, offline playback/seek, permanent failure and next
prefetch. Generated audio and aggregate diagnostics stay under .runtime. No
account, external service, capture, system volume or global routing is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer, qVersion
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineSong, StreamInfo
from api.owned_stream_cache import OwnedStreamCache
from local.owned_music_player import OwnedMusicPlayer


def make_fixture(output, ffmpeg):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(struct.pack("<hh", 100, -100) * 441000)
    source, encoded = output / "fixture.wav", output / "fixture.mp3"
    source.write_bytes(buffer.getvalue())
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-c:a", "libmp3lame", "-b:a", "128k", str(encoded)], check=True)
    return encoded.read_bytes()


def run_case(output, scenario, payload, timeout):
    output.mkdir(parents=True, exist_ok=True)
    started, gui_thread = time.monotonic(), threading.get_ident()
    calls, requests, events, controls = [], [], [], {}
    counts, lock = Counter(), threading.RLock()
    offline, shutting_down = threading.Event(), threading.Event()
    eof_count = 0

    def elapsed():
        return round(time.monotonic() - started, 6)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            label = self.path.lstrip("/")
            if label not in {"song-a", "song-b"}:
                self.send_error(404)
                return
            low, high = 0, len(payload) - 1
            header = self.headers.get("Range", "")
            partial = header.startswith("bytes=")
            if partial:
                low = int(header[6:].split("-", 1)[0])
            if not 0 <= low <= high:
                self.send_error(416)
                return
            body, advertised = payload[low:], len(payload) - low
            with lock:
                counts[label] += 1
                ordinal = counts[label]
            if scenario == "permanent" or (scenario == "resumed" and ordinal == 1):
                body = body[:len(body) // 2]
            with lock:
                requests.append({"at": elapsed(), "song": label, "ordinal": ordinal, "range_start": low,
                    "partial": partial, "advertised": advertised, "sent": len(body), "after_shutdown": shutting_down.is_set()})
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(advertised))
            self.send_header("ETag", '"generated-mp3-v1"')
            if partial:
                self.send_header("Content-Range", f"bytes {low}-{high}/{len(payload)}")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def stop_server():
        if not shutting_down.is_set():
            shutting_down.set()
            server.shutdown()
            server.server_close()
            offline.set()

    class Service:
        def stream_url(self, source, song):
            calls.append({"operation": "stream", "song": song, "worker": threading.get_ident() != gui_thread})
            return StreamInfo(source, song, f"http://127.0.0.1:{server.server_port}/song-{song}", True)

        def lyrics(self, source, song):
            calls.append({"operation": "lyrics", "song": song, "worker": threading.get_ident() != gui_thread})
            return LyricPayload(source, song, raw_lrc="[00:00.00]Generated\n[00:08.00]Offline seek", source="fixture")

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    audio = QAudioOutput()
    audio.setVolume(0)
    cache = OwnedStreamCache(output / "audio-cache")
    owned = OwnedMusicPlayer(Service(), LocalLyricsStore(output / "lyrics"), audio=audio, stream_cache=cache)
    player, loop, timer = owned.player, QEventLoop(), QTimer()
    timer.setInterval(20)
    timed_out = False

    def record(kind, value):
        nonlocal eof_count
        if kind == "media" and value == "EndOfMedia":
            eof_count += 1
        events.append({"at": elapsed(), "kind": kind, "value": value, "position": player.position(),
            "local_source": player.source().isLocalFile(), "epoch": owned._epoch})

    player.playbackStateChanged.connect(lambda state: record("state", state.name))
    player.mediaStatusChanged.connect(lambda state: record("media", state.name))
    player.errorOccurred.connect(lambda code, _text: record("error", code.name))
    owned.statusChanged.connect(lambda value: record("owned", value))

    def check():
        nonlocal timed_out
        if owned._prepared_audio is not None and "prepared_exact" not in controls:
            prepared = owned._prepared_audio
            controls.update(prepared_exact=prepared.path.read_bytes() == payload,
                preparation_requests=prepared.request_count, preparation_resumed=prepared.resumed,
                prepared_bytes=prepared.byte_count)
        if scenario == "permanent":
            if owned.status == "stream_unavailable":
                loop.quit()
                return
        elif scenario == "prefetch":
            ready = owned._prefetched_audio
            if ready is not None and "next_at" not in controls and player.position() >= 300:
                controls.update(next_at=elapsed(), next_exact=ready.path.read_bytes() == payload,
                    ready_lease=ready.lease, ready_path=str(ready.path), before_next_requests=len(requests))
                controls["next_accepted"] = owned.send_playback_command("next")
            if "next_at" in controls and owned.index == 1 and player.position() >= 300:
                controls.update(next_consumed=owned._prepared_audio is not None
                    and owned._prepared_audio.lease == controls["ready_lease"], after_next_requests=len(requests))
                loop.quit()
                return
        elif player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and player.position() >= 300:
            if "shutdown_at" not in controls:
                controls["shutdown_at"] = elapsed()
                threading.Thread(target=stop_server, daemon=True).start()
            if offline.is_set() and "seek_at" not in controls:
                controls.update(seek_at=elapsed(), seek_accepted=owned.seek_playback_position(8, owned.track.track_id))
            if "seek_at" in controls and player.position() >= 8250:
                controls["offline_clock_advanced"] = True
            if eof_count:
                loop.quit()
                return
        if owned.status == "playback_failed":
            loop.quit()
        elif elapsed() >= timeout:
            timed_out = True
            loop.quit()

    timer.timeout.connect(check)
    try:
        songs = tuple(OnlineSong("netease", name, "Generated MP3", duration_seconds=10) for name in
            (("a", "b") if scenario == "prefetch" else ("a",)))
        accepted = owned.play_queue(songs)
        timer.start()
        loop.exec()
        checks = {
            "queue_accepted": accepted, "actual_qt_player": type(player) is QMediaPlayer,
            "all_service_calls_off_gui": bool(calls) and all(call["worker"] for call in calls),
            "owned_output_zero": audio.volume() == 0,
            "no_remote_qt_source": all(event["local_source"] for event in events
                if event["kind"] == "state" and event["value"] == "PlayingState"),
            "no_qt_errors": not any(event["kind"] == "error" for event in events), "no_timeout": not timed_out,
        }
        if scenario == "permanent":
            checks.update(rejected_before_play=not any(event["value"] == "PlayingState" for event in events),
                incomplete_reason=owned.failure_reason == "incomplete", no_false_eof=eof_count == 0,
                no_song_skip=owned.index == 0, bounded_three_requests=len(requests) == 3,
                no_partial_decoder_file=owned._prepared_audio is None and player.source().isEmpty())
        elif scenario == "prefetch":
            checks.update(exact_current=controls.get("prepared_exact", False), exact_next=controls.get("next_exact", False),
                next_accepted=controls.get("next_accepted", False), next_lease_consumed=controls.get("next_consumed", False),
                one_transfer_per_song=counts == {"song-a": 1, "song-b": 1},
                no_transfer_on_next=controls.get("before_next_requests") == controls.get("after_next_requests"),
                no_second_song_reauthorization=sum(call["operation"] == "stream" and call["song"] == "b" for call in calls) == 1)
        else:
            checks.update(exact_complete_file=controls.get("prepared_exact", False), server_closed=offline.is_set(),
                offline_seek_accepted=controls.get("seek_accepted", False), offline_clock_advanced=controls.get("offline_clock_advanced", False),
                actual_eof=eof_count > 0, no_http_after_shutdown=not any(request["after_shutdown"] for request in requests),
                expected_transfer_count=controls.get("preparation_requests") == (2 if scenario == "resumed" else 1))
            if scenario == "resumed":
                checks.update(resume_reported=controls.get("preparation_resumed", False),
                    positive_range_start=any(request["partial"] and request["range_start"] > 0 for request in requests))
        # Cache paths and random leases are internal controls, not report data.
        controls.pop("ready_path", None)
        controls.pop("ready_lease", None)
        report = {"passed": all(checks.values()), "scenario": scenario, "checks": checks,
            "elapsed_seconds": elapsed(), "qt_version": qVersion(), "payload_bytes": len(payload),
            "requests": requests, "service_calls": calls, "events": events, "controls": controls,
            "scope": "Generated muted MP3, loopback HTTP, actual Qt and bounded complete-file cache only.",
            "limits": "No real account, audible output, physical group, online latency or long-session evidence."}
    finally:
        timer.stop()
        owned.close()
        app.processEvents()
        stop_server()
        offline.wait(3)
        server_thread.join(timeout=2)
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-buffered-playback"))
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--timeout", type=float, default=12)
    options = parser.parse_args()
    if not options.ffmpeg:
        parser.error("ffmpeg is required to encode generated MP3 fixtures")
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = make_fixture(output, options.ffmpeg)
    cases = [run_case(output / scenario, scenario, payload, options.timeout)
        for scenario in ("healthy", "resumed", "permanent", "prefetch")]
    report = {"passed": all(case["passed"] for case in cases), "cases": cases,
        "check_count": sum(len(case["checks"]) for case in cases)}
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "check_count": report["check_count"],
        "cases": [{"scenario": case["scenario"], "passed": case["passed"], "elapsed_seconds": case["elapsed_seconds"],
            "failed_checks": [name for name, passed in case["checks"].items() if not passed]} for case in cases]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
