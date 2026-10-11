"""Real Qt HTTP-tail recovery and midstream-failure regression.

Uses generated PCM, loopback HTTP and muted player output. No account,
external service, audio capture, global volume or system routing is used.
Diagnostics and fixtures remain beneath the selected .runtime directory.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import struct
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


def wav_bytes(seconds):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(struct.pack("<hh", 100, -100) * round(44100 * seconds))
    return buffer.getvalue()


def run_case(output, scenario, mode, timeout):
    output.mkdir(parents=True, exist_ok=True)
    healthy, long_audio = wav_bytes(2), wav_bytes(4)
    (output / "fixture.wav").write_bytes(long_audio)
    started, owner_thread = time.monotonic(), threading.get_ident()
    calls, requests, events, samples = [], [], [], []
    controls = {}

    def elapsed():
        return round(time.monotonic() - started, 6)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path not in {"/healthy.wav", "/tail.wav", "/midstream.wav"}:
                self.send_error(404)
                return
            payload = healthy if self.path == "/healthy.wav" else long_audio
            low, high, partial = 0, len(payload) - 1, False
            header = self.headers.get("Range", "")
            if header.startswith("bytes="):
                try:
                    first, last = header[6:].split("-", 1)
                    low = int(first) if first else 0
                    high = min(high, int(last)) if last else high
                    partial = True
                except (ValueError, IndexError):
                    self.send_error(416)
                    return
            if not 0 <= low <= high < len(payload):
                self.send_error(416)
                return
            advertised = payload[low:high + 1]
            missing = 17640 if self.path == "/tail.wav" else 352800 if self.path == "/midstream.wav" else 0
            body = advertised[:-missing] if missing else advertised
            requests.append({"at": elapsed(), "kind": self.path[1:-4],
                "advertised_bytes": len(advertised), "sent_bytes": len(body), "partial": partial})
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(advertised)))
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

    class FixtureService:
        def stream_url(self, source_id, song_id):
            ordinal = 1 + sum(call["operation"] == "stream" for call in calls)
            kind = "midstream" if scenario in {"midstream", "retry"} and ordinal <= 2 else \
                "healthy" if scenario == "healthy" or ordinal > 1 else \
                "midstream" if scenario in {"midstream", "retry"} else "tail"
            calls.append({"operation": "stream", "song_id": song_id, "at": elapsed(),
                "worker": threading.get_ident() != owner_thread})
            url = f"http://127.0.0.1:{server.server_port}/{kind}.wav"
            return StreamInfo(source_id, song_id, url, True)

        def lyrics(self, source_id, song_id):
            calls.append({"operation": "lyrics", "song_id": song_id, "at": elapsed(),
                "worker": threading.get_ident() != owner_thread})
            return LyricPayload(source_id, song_id,
                raw_lrc="[00:00.00]First fixture line\n[00:01.00]Last fixture line", source="local-fixture")

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    audio = QAudioOutput()
    audio.setVolume(0.0)
    owned = OwnedMusicPlayer(FixtureService(), LocalLyricsStore(root=output / "lyrics"), audio=audio)
    owned._random.shuffle = lambda songs: songs.reverse()
    owned.set_playback_mode(mode)
    player = owned.player
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(25)
    timed_out = False
    first_epoch = owned._epoch + 1

    def snapshot(kind="sample", value=""):
        track = owned.track
        return {"at": elapsed(), "kind": kind, "value": value, "epoch": owned._epoch,
            "position": player.position(), "duration": player.duration(),
            "qt_state": player.playbackState().name, "status": owned.status,
            "lyrics": list(track.lyrics) if track else [],
            "is_playing": bool(track and track.is_playing)}

    player.errorOccurred.connect(lambda code, _text: events.append(snapshot("error", code.name)))
    player.mediaStatusChanged.connect(lambda status: events.append(snapshot("media", status.name)))
    player.playbackStateChanged.connect(lambda state: events.append(snapshot("state", state.name)))
    player.positionChanged.connect(lambda position: events.append(snapshot("position", position)))
    owned.statusChanged.connect(lambda status: events.append(snapshot("owned", status)))

    def check():
        nonlocal timed_out
        samples.append(snapshot())
        if scenario == "pause" and "pause_at" not in controls and player.position() >= 350:
            controls["pause_at"] = elapsed()
            controls["pause_accepted"] = owned.send_playback_command("toggle_play_pause")
        if scenario == "pause" and "pause_at" in controls and "resume_at" not in controls:
            if elapsed() - controls["pause_at"] >= 1.4:
                controls["paused_unchanged"] = (owned._epoch == first_epoch
                    and player.playbackState() == QMediaPlayer.PlaybackState.PausedState
                    and owned.track.lyrics == ("First fixture line", "Last fixture line")
                    and owned.status == "playing")
                controls["resume_at"] = elapsed()
                controls["resume_accepted"] = owned.send_playback_command("toggle_play_pause")
        if scenario == "seek" and "seek_at" not in controls and player.position() >= 500:
            controls["seek_at"] = elapsed()
            controls["seek_accepted"] = owned.seek_playback_position(1.0)
            controls["seek_cleared_fault"] = owned._stream_fault is None
            controls["seek_kept_epoch"] = owned._epoch == first_epoch
            controls["seek_kept_lyrics"] = owned.track.lyrics == ("First fixture line", "Last fixture line")
        if scenario == "retry" and owned.status == "playback_failed" and "retry_at" not in controls:
            controls["retry_at"] = elapsed()
            controls["failed_qt_player_paused"] = player.playbackState() == QMediaPlayer.PlaybackState.PausedState
            controls["failure_kept_lyrics"] = owned.track.lyrics == ("First fixture line", "Last fixture line")
            controls["automatic_recovery_bounded_to_one"] = sum(call["operation"] == "stream" for call in calls) == 2
            controls["retry_accepted"] = owned.send_playback_command("toggle_play_pause")
            controls["retry_new_epoch"] = owned._epoch == first_epoch + 2
            controls["retry_same_song"] = owned.track.track_id == "owned:netease:0"
        stream_calls = sum(call["operation"] == "stream" for call in calls)
        expected_epoch = first_epoch + (2 if scenario == "retry" else 1)
        if (owned._epoch >= expected_epoch and stream_calls >= (3 if scenario == "retry" else 2) and owned.status == "playing"
                and player.position() >= 250):
            loop.quit()
        elif owned.status in {"playback_failed", "stream_unavailable"}:
            loop.quit()
        elif elapsed() >= timeout:
            timed_out = True
            loop.quit()

    timer.timeout.connect(check)
    try:
        accepted = owned.play_queue(tuple(OnlineSong("netease", str(index), f"Fixture {index}",
            duration_seconds=120) for index in range(3)))
        timer.start()
        loop.exec()
        first_samples = [sample for sample in samples if sample["epoch"] == first_epoch]
        errors = [event for event in events if event["kind"] == "error"]
        first_errors = [event for event in errors if event["epoch"] == first_epoch]
        first_eof = [event for event in events if event["epoch"] == first_epoch
            and event["kind"] == "media" and event["value"] == "EndOfMedia"]
        terminal = owned.status == "playback_failed"
        expected_id = "0" if mode == "repeat_one" or scenario == "retry" else "2" if mode == "shuffle" else "1"
        stream_calls = [call for call in calls if call["operation"] == "stream"]
        established = [sample for sample in first_samples
            if sample["lyrics"] == ["First fixture line", "Last fixture line"]]
        checks = {
            "queue_accepted": bool(accepted),
            "real_qmedia_player": type(player) is QMediaPlayer,
            "workers_off_gui_thread": bool(calls) and all(call["worker"] for call in calls),
            "http_fixture_requested": bool(requests),
            "decoded_duration_overrides_catalog": any(sample["duration"] in {2000, 4000} for sample in first_samples),
            "qt_clock_advanced": max((sample["position"] for sample in first_samples), default=0) >= 1000,
            "established_lyrics_observed": bool(established),
            "player_output_volume_zero": audio.volume() == 0.0,
            "no_timeout": not timed_out,
        }
        if scenario == "healthy":
            checks.update({"no_transport_errors": not errors,
                "real_end_of_media": any(event["kind"] == "media" and event["value"] == "EndOfMedia" for event in events)})
        else:
            checks.update({"real_transport_fault": any(event["value"] in {"ResourceError", "NetworkError"} for event in first_errors),
                "fault_keeps_playback_until_clock_drains": bool(first_errors) and all(event["status"] != "playback_failed" for event in first_errors),
                "fault_does_not_clear_lyrics": bool(established) and all(sample["lyrics"] == ["First fixture line", "Last fixture line"]
                    for sample in first_samples if sample["at"] >= established[0]["at"]),
                "missing_end_of_media_reproduced": not first_eof})
        if scenario == "midstream":
            checks.update({"midstream_failure_is_bounded": terminal and elapsed() < timeout,
                "midstream_does_not_skip_song": len(stream_calls) == 2 and owned.index == 0
                    and all(call["song_id"] == "0" for call in stream_calls),
                "automatic_recovery_bounded_to_one": owned._epoch == first_epoch + 1,
                "terminal_qt_player_paused": player.playbackState() == QMediaPlayer.PlaybackState.PausedState,
                "terminal_lyrics_retained": owned.track.lyrics == ("First fixture line", "Last fixture line"),
                "terminal_visual_paused": not owned.track.is_playing})
        else:
            checks.update({"existing_playback_mode_preserved": len(stream_calls) == (3 if scenario == "retry" else 2)
                    and stream_calls[-1]["song_id"] == expected_id,
                "next_stream_really_progresses": owned.status == "playing" and player.position() >= 250,
                "one_transition_only": owned._epoch == first_epoch + (2 if scenario == "retry" else 1)})
            if scenario != "retry":
                checks["no_terminal_failure"] = not any(event["status"] == "playback_failed" for event in events)
        if scenario == "pause":
            checks.update({"pause_accepted": controls.get("pause_accepted", False),
                "pause_does_not_trigger_eof": controls.get("paused_unchanged", False),
                "resume_accepted": controls.get("resume_accepted", False)})
        if scenario == "seek":
            checks.update({name: bool(controls.get(name)) for name in
                ("seek_accepted", "seek_cleared_fault", "seek_kept_epoch", "seek_kept_lyrics")})
        if scenario == "retry":
            checks.update({name: bool(controls.get(name)) for name in
                ("failed_qt_player_paused", "failure_kept_lyrics", "automatic_recovery_bounded_to_one",
                 "retry_accepted", "retry_new_epoch", "retry_same_song")})
        report = {"passed": all(checks.values()), "checks": checks, "scenario": scenario, "mode": mode,
            "qt_version": qVersion(), "elapsed_seconds": elapsed(), "controls": controls,
            "scope": "Generated PCM and loopback HTTP; player output volume zero; no accounts, capture or global device writes.",
            "limits": "Reproduces Qt FFmpeg transport faults locally; does not prove every online end-of-song failure or physical audibility.",
            "requests": list(requests), "service_calls": list(calls), "events": list(events), "samples": list(samples),
            "final": snapshot("final")}
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
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-stream-fault-smoke"))
    parser.add_argument("--scenario", choices=("all", "healthy", "tail", "midstream", "pause", "seek", "retry"), default="all")
    parser.add_argument("--timeout", type=float, default=12)
    options = parser.parse_args()
    cases = [(scenario, mode) for scenario in ("healthy", "tail")
        for mode in ("sequential", "shuffle", "repeat_one")]
    cases += [(scenario, "sequential") for scenario in ("midstream", "pause", "seek", "retry")]
    if options.scenario != "all":
        cases = [case for case in cases if case[0] == options.scenario]
    reports = []
    for scenario, mode in cases:
        report = run_case(options.output.resolve() / f"{scenario}-{mode}", scenario, mode, max(8, options.timeout))
        reports.append(report)
        print(json.dumps({key: report[key] for key in ("scenario", "mode", "passed", "checks", "elapsed_seconds")},
            ensure_ascii=False), flush=True)
    summary = {"passed": all(report["passed"] for report in reports),
        "check_count": sum(len(report["checks"]) for report in reports),
        "cases": [{key: report[key] for key in ("scenario", "mode", "passed", "elapsed_seconds")} for report in reports]}
    (options.output.resolve() / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
