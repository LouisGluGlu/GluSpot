"""Real Qt same-song recovery using generated audio and loopback HTTP.

MP3 encoding requires ffmpeg on PATH or --ffmpeg. All audio outputs have volume
zero. No account, external request, audio capture or global routing is used.
"""
from __future__ import annotations

import argparse
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
from local.owned_music_player import OwnedMusicPlayer


def run_case(output, scenario, codec, ffmpeg, timeout):
    output.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(struct.pack("<hh", 100, -100) * (44100 * 4))
    fixture = output / "fixture.wav"
    fixture.write_bytes(buffer.getvalue())
    if codec == "mp3":
        mp3 = output / "fixture.mp3"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(fixture),
            "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
        payload, bytes_per_second = mp3.read_bytes(), 16000
    else:
        payload, bytes_per_second = fixture.read_bytes(), 176400
    started = time.monotonic()
    calls, requests, events, samples, controls = [], [], [], [], {}

    def elapsed():
        return round(time.monotonic() - started, 6)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            kind = self.path.split("/", 2)[1]
            low, high, partial = 0, len(payload) - 1, False
            header = self.headers.get("Range", "")
            if header.startswith("bytes="):
                first, last = header[6:].split("-", 1)
                low, high, partial = int(first or 0), min(high, int(last)) if last else high, True
            if not 0 <= low <= high < len(payload):
                self.send_error(416)
                return
            body = payload[low:high + 1]
            advertised = len(body)
            if kind == "truncated":
                body = body[:-2 * bytes_per_second]
            requests.append({"at": elapsed(), "kind": kind, "low": low, "high": high,
                "advertised_bytes": advertised, "sent_bytes": len(body)})
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/mpeg" if codec == "mp3" else "audio/wav")
            self.send_header("Content-Length", str(advertised))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {low}-{high}/{len(payload)}")
            self.end_headers()
            try:
                if kind == "stalled":
                    cut = min(len(body), 2 * bytes_per_second + 44)
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    time.sleep(3)
                    self.wfile.write(body[cut:])
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    class FixtureService:
        def stream_url(self, source_id, song_id):
            ordinal = 1 + len(calls)
            kind = "stalled" if scenario == "stall" and ordinal == 1 else \
                "truncated" if ordinal == 1 or scenario == "permanent" else "healthy"
            calls.append({"at": elapsed(), "song_id": song_id, "kind": kind})
            return StreamInfo(source_id, song_id, f"http://127.0.0.1:{server.server_port}/{kind}/audio", True)

        def lyrics(self, source_id, song_id):
            return LyricPayload(source_id, song_id, raw_lrc="[00:00.00]First\n[00:03.00]Last", source="fixture")

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    audio = QAudioOutput()
    audio.setVolume(0)
    owned = OwnedMusicPlayer(FixtureService(), LocalLyricsStore(root=output / "lyrics"), audio=audio, stream_cache=None)
    player, loop, timer = owned.player, QEventLoop(), QTimer()
    timer.setInterval(25)
    timed_out = False

    def snapshot(kind="sample", value=""):
        track, recovery = owned.track, owned._stream_recovery
        return {"at": elapsed(), "kind": kind, "value": value, "epoch": owned._epoch,
            "index": owned.index, "position": player.position(), "duration": player.duration(),
            "media": player.mediaStatus().name, "buffer": player.bufferProgress(),
            "qt_state": player.playbackState().name, "status": owned.status,
            "phase": recovery.phase if recovery else "", "lyrics": list(track.lyrics) if track else [],
            "is_playing": bool(track and track.is_playing)}

    player.errorOccurred.connect(lambda code, _text: events.append(snapshot("error", code.name)))
    player.mediaStatusChanged.connect(lambda status: events.append(snapshot("media", status.name)))
    owned.statusChanged.connect(lambda status: events.append(snapshot("owned", status)))

    def check():
        nonlocal timed_out
        samples.append(snapshot())
        recovery = owned._stream_recovery
        if recovery and "recovery_at" not in controls:
            controls.update(recovery_at=elapsed(), target=recovery.position_ms,
                retained_lyrics=owned.track.lyrics == ("First", "Last"),
                retained_clock=owned.track.position_observed_seconds == recovery.position_ms / 1000)
        if scenario == "pause" and recovery and "pause_at" not in controls:
            controls["pause_at"] = elapsed()
            controls["pause_accepted"] = owned.send_playback_command("toggle_play_pause")
        if scenario == "pause" and "pause_at" in controls and recovery is None and "paused_ready_at" not in controls:
            controls["paused_ready_at"] = elapsed()
        if scenario == "pause" and "paused_ready_at" in controls and "resume_at" not in controls:
            if elapsed() - controls["paused_ready_at"] >= 1.2:
                controls["pause_no_late_play"] = player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
                controls["resume_at"] = elapsed()
                controls["resume_accepted"] = owned.send_playback_command("toggle_play_pause")
        if scenario == "seek" and recovery and recovery.phase == "progress" and "seek_at" not in controls:
            controls["seek_at"] = elapsed()
            controls["seek_accepted"] = owned.seek_playback_position(0.5, owned.track.track_id)
            controls["seek_cleared_old_target"] = owned._stream_recovery is None
        if scenario == "cancel" and recovery and "next_at" not in controls:
            controls["next_at"] = elapsed()
            controls["next_accepted"] = owned.send_playback_command("next")
        if owned.status == "playback_failed":
            loop.quit()
        elif scenario == "stall" and owned._epoch >= 2:
            loop.quit()
        elif scenario == "cancel" and owned.index == 1 and owned.status == "playing" and player.position() >= 250:
            loop.quit()
        elif scenario == "seek" and "seek_at" in controls and player.position() >= 1000:
            loop.quit()
        elif (scenario in {"recovered", "pause"} and owned._epoch == 2 and owned.status == "playing"
                and player.position() > controls.get("target", 100000) + 250
                and (scenario != "pause" or "resume_at" in controls)):
            loop.quit()
        elif elapsed() >= timeout:
            timed_out = True
            loop.quit()

    timer.timeout.connect(check)
    try:
        owned.play_queue((OnlineSong("netease", "0", "Generated fixture", duration_seconds=120),
            OnlineSong("netease", "1", "Next generated fixture", duration_seconds=120)))
        timer.start()
        loop.exec()
        final = snapshot("final")
        first = [sample for sample in samples if sample["epoch"] == 1]
        recovery_samples = [sample for sample in samples if sample["status"] == "recovering_stream"]
        checks = {"real_qt_player": type(player) is QMediaPlayer,
            "muted_player_output": audio.volume() == 0, "no_timeout": not timed_out,
            "real_decoded_clock_advanced": max((sample["position"] for sample in first), default=0) >= 1000,
            "decoded_duration_4000": any(sample["duration"] == 4000 for sample in first),
            "established_lyrics": any(sample["lyrics"] == ["First", "Last"] for sample in first)}
        if scenario == "stall":
            checks.update(no_transport_errors=not any(event["kind"] == "error" for event in events),
                no_automatic_recovery=not recovery_samples,
                real_eof=any(event["kind"] == "media" and event["value"] == "EndOfMedia" for event in events),
                ordinary_eof_advances=owned.index == 1)
        else:
            checks.update(real_transport_error=any(event["kind"] == "error" for event in events),
                recovery_retains_lyrics=controls.get("retained_lyrics", False),
                recovery_retains_actual_clock=controls.get("retained_clock", False),
                recovery_display_frozen=bool(recovery_samples) and all(not sample["is_playing"] for sample in recovery_samples),
                no_extra_reauthorizations=(len(calls) in {2, 3} if scenario == "cancel" else len(calls) == 2)
                    and len([call for call in calls if call["song_id"] == "0"]) <= 2)
            if scenario == "permanent":
                checks.update(second_failure_is_terminal=owned.status == "playback_failed",
                    no_song_skip=owned.index == 0, terminal_lyrics_retained=owned.track.lyrics == ("First", "Last"))
            elif scenario == "cancel":
                checks.update(next_cancels_recovery=controls.get("next_accepted", False),
                    only_requested_next=owned.index == 1 and calls[-1]["song_id"] == "1",
                    new_song_real_clock=owned.status == "playing" and player.position() >= 250)
            elif scenario == "seek":
                checks.update(seek_accepted=controls.get("seek_accepted", False),
                    old_automatic_seek_cancelled=controls.get("seek_cleared_old_target", False),
                    user_target_advances=1000 <= player.position() < controls.get("target", 0),
                    no_song_skip=owned.index == 0)
            else:
                checks.update(same_song_reauthorized=all(call["song_id"] == "0" for call in calls),
                    recovered_real_clock=owned.status == "playing" and player.position() > controls.get("target", 100000) + 250,
                    lyrics_retained=owned.track.lyrics == ("First", "Last"), no_song_skip=owned.index == 0)
                if scenario == "pause":
                    checks.update({name: bool(controls.get(name)) for name in
                        ("pause_accepted", "pause_no_late_play", "resume_accepted")})
        report = {"passed": all(checks.values()), "checks": checks, "scenario": scenario, "codec": codec,
            "qt_version": qVersion(), "elapsed_seconds": elapsed(), "controls": controls,
            "events": list(events), "samples": list(samples), "requests": list(requests), "calls": list(calls), "final": final,
            "scope": "Generated audio; loopback HTTP; own output volume zero; no accounts or device/global writes.",
            "limits": "Verifies Qt decoding, position and control fences; excludes online account/network and physical group listening."}
    finally:
        timer.stop()
        owned.close()
        app.processEvents()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-stream-recovery"))
    parser.add_argument("--scenario", choices=("all", "recovered", "permanent", "pause", "seek", "cancel", "stall"), default="all")
    parser.add_argument("--codec", choices=("wav", "mp3"), default="mp3")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--timeout", type=float, default=15)
    options = parser.parse_args()
    if options.codec == "mp3" and not options.ffmpeg:
        parser.error("MP3 fixture encoding requires ffmpeg; use --ffmpeg or --codec wav")
    scenarios = ("recovered", "permanent", "pause", "seek", "cancel", "stall") if options.scenario == "all" else (options.scenario,)
    reports = []
    for scenario in scenarios:
        report = run_case(options.output.resolve() / scenario, scenario, options.codec, options.ffmpeg, max(12, options.timeout))
        reports.append(report)
        print(json.dumps({key: report[key] for key in ("passed", "checks", "scenario", "codec", "elapsed_seconds")}), flush=True)
    summary = {"passed": all(report["passed"] for report in reports),
        "check_count": sum(len(report["checks"]) for report in reports),
        "cases": [{key: report[key] for key in ("scenario", "codec", "passed", "elapsed_seconds")} for report in reports]}
    (options.output.resolve() / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
