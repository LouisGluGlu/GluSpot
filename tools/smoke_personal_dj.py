"""Real muted Qt personal-DJ playback with generated loopback HTTP audio.

The production default verified cache and MusicTasks workers are exercised.
No credentials, external services, captured audio, system volume or routing
APIs are used. Reports contain bounded counters and synthetic segment labels;
audio addresses and account material are never written to diagnostics.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
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
from api.online_music import LyricPayload, OnlineMusicService, StreamInfo
from api.owned_stream_cache import OwnedStreamCache
from local.music_tasks import MusicTasks
from local.owned_music_player import OwnedMusicPlayer


def wav_bytes(seconds):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(struct.pack("<hh", 100, -100) * round(44100 * seconds))
    return buffer.getvalue()


def run_case(output, scenario, timeout):
    output.mkdir(parents=True, exist_ok=True)
    voice_payload, song_payload = wav_bytes(.7), wav_bytes(1.3)
    started, gui_thread = time.monotonic(), threading.get_ident()
    lock = threading.RLock()
    counters, controls = Counter(), {}
    service_calls, requests = deque(maxlen=128), deque(maxlen=128)
    observations, events = deque(maxlen=1024), deque(maxlen=256)
    segments = []
    eof_count, timed_out = 0, False

    def elapsed():
        return round(time.monotonic() - started, 6)

    def note_call(operation, label=""):
        worker = threading.get_ident() != gui_thread
        with lock:
            counters[operation] += 1
            counters["gui_service_calls"] += int(not worker)
            service_calls.append({"operation": operation, "segment": label, "worker": worker})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            # All accepted addresses are constructed below, with no query,
            # redirects, credentials or outbound requests.
            label = self.path.lstrip("/")
            if label not in {"voice1", "voice2", "voice3", "voice4", "song101", "song102", "song103", "song104"}:
                self.send_error(404)
                return
            payload = voice_payload if label.startswith("voice") else song_payload
            low, high, partial = 0, len(payload) - 1, False
            header = self.headers.get("Range", "")
            if header:
                try:
                    first, last = header.removeprefix("bytes=").split("-", 1)
                    low = int(first)
                    high = min(high, int(last)) if last else high
                    partial = header.startswith("bytes=")
                except (ValueError, IndexError):
                    self.send_error(416)
                    return
            if not partial and header or not 0 <= low <= high < len(payload):
                self.send_error(416)
                return
            body = payload[low:high + 1]
            with lock:
                counters["http"] += 1
                counters["http_range"] += int(partial)
                counters["http_cookie"] += int(self.headers.get("Cookie") is not None)
                requests.append({"segment": label, "bytes": len(body), "partial": partial})
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", '"synthetic-' + label + '"')
            if partial:
                self.send_header("Content-Range", f"bytes {low}-{high}/{len(payload)}")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, name="GluSpotDJFixture", daemon=True)
    server_thread.start()
    address = f"http://127.0.0.1:{server.server_port}/"

    class FixtureService:
        def __init__(self):
            self.parser = object.__new__(OnlineMusicService)
            self.parser._clock = time.time

        def personal_dj(self, source_id="netease"):
            note_call("personal_dj")
            with lock:
                batch = min(counters["personal_dj"], 2)
            resources = []
            for index in ((1, 2) if batch == 1 else (3, 4)):
                song_id = str(100 + index)
                resources.extend((
                    {"type": "audio", "value": {"audioList": [{
                        "audioId": "voice" + str(index), "audioUrl": address + "voice" + str(index),
                        "duration": .7, "validTime": 60, "textType": "介绍",
                        "introductionRelatedSongIds": [int(song_id)],
                    }]}},
                    {"type": "song", "value": {"songId": song_id, "songData": {
                        "id": int(song_id), "name": "Synthetic song " + song_id,
                        "duration": 1300, "artists": [{"name": "Fixture"}], "album": {"name": "Generated PCM"},
                    }}},
                ))
            return self.parser._personal_dj_batch({"code": 200, "data": {"aiDjResources": resources}})

        def personal_fm(self, source_id="netease"):
            note_call("unexpected_personal_fm")
            raise AssertionError("DJ continuation requested the ordinary FM endpoint")

        def stream_url(self, source_id, song_id):
            valid = song_id in {"101", "102", "103", "104"}
            note_call("stream" if valid else "unexpected_voice_stream", "song" + song_id if valid else "voice")
            if not valid:
                raise AssertionError("DJ speech requested the ordinary song stream endpoint")
            return StreamInfo(source_id, song_id, address + "song" + song_id, True, quality="synthetic-wav")

        def lyrics(self, source_id, song_id):
            valid = song_id in {"101", "102", "103", "104"}
            note_call("lyrics" if valid else "unexpected_voice_lyrics", "song" + song_id if valid else "voice")
            if not valid:
                raise AssertionError("DJ speech requested ordinary song lyrics")
            return LyricPayload(source_id, song_id,
                raw_lrc="[00:00.00]Synthetic first line\n[00:00.70]Synthetic second line", source="synthetic-personal-dj")

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    audio = QAudioOutput()
    audio.setVolume(0.0)
    owned = OwnedMusicPlayer(FixtureService(), LocalLyricsStore(root=output / "lyrics"), audio=audio)
    player, loop, timer = owned.player, QEventLoop(), QTimer()
    timer.setInterval(20)
    seen_epochs = set()

    def snapshot(kind="sample", value=""):
        song = owned.queue[owned.index] if 0 <= owned.index < len(owned.queue) else None
        label = (song.song_id.removeprefix("dj:") if song.media_kind == "dj_voice" else "song" + song.song_id) if song else ""
        track = owned.track
        return {"at": elapsed(), "kind": kind, "value": value, "segment": label,
            "epoch": owned._epoch, "position_ms": player.position(), "duration_ms": player.duration(),
            "qt_state": player.playbackState().name, "owned_state": owned.status,
            "is_playing": bool(track and track.is_playing),
            "source_is_local_file": player.source().isLocalFile(),
            "song_lyrics_loaded": bool(track and track.lyrics == ("Synthetic first line", "Synthetic second line"))}

    def media_status(status):
        nonlocal eof_count
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            eof_count += 1
        events.append({"at": elapsed(), "kind": "media", "value": status.name})

    player.mediaStatusChanged.connect(media_status)
    player.errorOccurred.connect(lambda code, _text: events.append({"at": elapsed(), "kind": "error", "value": code.name}))
    owned.statusChanged.connect(lambda status: events.append({"at": elapsed(), "kind": "owned", "value": status}))

    def pause_round(label, sample):
        prefix = label + "_pause"
        if prefix + "_at" not in controls and sample["position_ms"] >= 100:
            controls[prefix + "_accepted"] = owned.send_playback_command("toggle_play_pause")
            controls[prefix + "_at"] = elapsed()
            controls[prefix + "_position"] = player.position()
            controls[prefix + "_epoch"] = owned._epoch
        elif prefix + "_at" in controls and prefix + "_resumed" not in controls:
            if elapsed() - controls[prefix + "_at"] >= .3:
                controls[prefix + "_stable"] = (player.playbackState() == QMediaPlayer.PlaybackState.PausedState
                    and owned._epoch == controls[prefix + "_epoch"]
                    and abs(player.position() - controls[prefix + "_position"]) <= 30)
                controls[prefix + "_resumed"] = owned.send_playback_command("toggle_play_pause")
        elif controls.get(prefix + "_resumed") and sample["position_ms"] >= controls[prefix + "_position"] + 100:
            controls[prefix + "_advanced"] = True

    def check():
        nonlocal timed_out
        sample = snapshot()
        observations.append(sample)
        if sample["qt_state"] == "PlayingState" and sample["is_playing"] and sample["position_ms"] >= 80:
            if sample["epoch"] not in seen_epochs:
                seen_epochs.add(sample["epoch"])
                segments.append(sample["segment"])
        if scenario == "pause":
            if sample["segment"] in {"voice1", "song101"}:
                pause_round(sample["segment"], sample)
            if sample["segment"] == "voice2" and sample["position_ms"] >= 100:
                loop.quit()
        elif scenario == "seek":
            if sample["segment"] == "song101" and sample["position_ms"] >= 100 and "seek_at" not in controls:
                epoch, track_id = owned._epoch, owned.track.track_id
                controls["seek_accepted"] = owned.seek_playback_position(.8, track_id)
                controls["seek_at"] = elapsed()
                controls["seek_epoch_unchanged"] = owned._epoch == epoch
                controls["seek_readback_near_target"] = abs(player.position() - 800) <= 250
            if "seek_at" in controls and sample["segment"] == "song101" and sample["position_ms"] >= 1000:
                controls["seek_really_advanced"] = True
            if sample["segment"] == "voice2" and sample["position_ms"] >= 100:
                loop.quit()
        elif scenario == "repeat":
            if sample["segment"] == "voice1" and sample["position_ms"] >= 100 and "repeat_selected" not in controls:
                controls["repeat_selected"] = owned.set_playback_mode("repeat_one")
            if segments[:3] == ["voice1", "song101", "song101"] and sample["position_ms"] >= 100 and "continuation_selected" not in controls:
                controls["continuation_selected"] = owned.set_playback_mode("sequential")
            if sample["segment"] == "voice2" and sample["position_ms"] >= 100:
                loop.quit()
        elif sample["segment"] == "song103" and sample["position_ms"] >= 300:
            loop.quit()
        if owned.status in {"playback_failed", "stream_unavailable", "catalog_unavailable"}:
            loop.quit()
        elif elapsed() >= timeout:
            timed_out = True
            loop.quit()

    timer.timeout.connect(check)
    report = None
    cache = owned.stream_cache
    try:
        accepted = owned.play_personal_dj("netease")
        timer.start()
        loop.exec()
        final = snapshot("final")
        with lock:
            counts, calls, http = dict(counters), list(service_calls), list(requests)
        established = [sample for sample in observations if sample["is_playing"]]
        errors = [event for event in events if event["kind"] == "error"]
        checks = {
            "dj_start_accepted": bool(accepted),
            "real_qmedia_player": type(player) is QMediaPlayer,
            "production_default_complete_audio_cache": type(cache) is OwnedStreamCache,
            "real_music_task_workers": all(type(worker) is MusicTasks for worker in
                (owned.tasks, owned.artwork_tasks, owned.prefetch_tasks, owned.download_tasks)),
            "service_calls_off_gui_thread": bool(calls) and counts.get("gui_service_calls", 0) == 0,
            "song_stream_endpoint_only_for_songs": counts.get("stream", 0) > 0 and counts.get("unexpected_voice_stream", 0) == 0,
            "song_lyric_endpoint_only_for_songs": counts.get("lyrics", 0) > 0 and counts.get("unexpected_voice_lyrics", 0) == 0,
            "dj_continuation_never_calls_ordinary_fm": counts.get("unexpected_personal_fm", 0) == 0,
            "decoder_reads_local_completed_files": bool(established) and all(sample["source_is_local_file"] for sample in established),
            "speech_duration_really_decoded": any(sample["segment"].startswith("voice") and sample["duration_ms"] == 700 for sample in established),
            "song_duration_really_decoded": any(sample["segment"].startswith("song") and sample["duration_ms"] == 1300 for sample in established),
            "real_song_lyrics_loaded": any(sample["song_lyrics_loaded"] for sample in established),
            "http_fixture_requested_without_cookie": bool(http) and counts.get("http_cookie", 0) == 0,
            "player_output_volume_zero": audio.volume() == 0.0,
            "real_end_of_media_observed": eof_count > 0,
            "no_qt_errors": not errors,
            "no_terminal_failure": owned.status not in {"playback_failed", "stream_unavailable", "catalog_unavailable"},
            "no_timeout": not timed_out,
        }
        if scenario == "sequence":
            checks.update({"two_dj_batches_requested": counts.get("personal_dj", 0) >= 2,
                "voice_song_order_preserved_through_natural_continuation": segments[:6] == ["voice1", "song101", "voice2", "song102", "voice3", "song103"],
                "natural_eof_advances_to_second_batch": eof_count >= 5,
                "second_batch_song_clock_advanced": final["segment"] == "song103" and final["position_ms"] >= 300})
        elif scenario == "pause":
            checks.update({"ordered_voice_song_after_pause": segments[:3] == ["voice1", "song101", "voice2"]})
            for label in ("voice1", "song101"):
                for suffix in ("accepted", "stable", "resumed", "advanced"):
                    name = label + "_pause_" + suffix
                    checks[name] = bool(controls.get(name))
        elif scenario == "seek":
            checks.update({"voice_song_order_after_seek": segments[:3] == ["voice1", "song101", "voice2"]})
            for name in ("seek_accepted", "seek_epoch_unchanged", "seek_readback_near_target", "seek_really_advanced"):
                checks[name] = bool(controls.get(name))
        else:
            checks.update({"repeat_selected_during_speech": bool(controls.get("repeat_selected")),
                "speech_not_repeated_song_repeated": segments[:4] == ["voice1", "song101", "song101", "voice2"],
                "return_to_continuation_accepted": bool(controls.get("continuation_selected")),
                "natural_song_eof_repeat_observed": eof_count >= 3})
        report = {"passed": all(checks.values()), "checks": checks, "scenario": scenario,
            "qt_version": qVersion(), "elapsed_seconds": elapsed(), "controls": controls,
            "fixture": {"voice_seconds": .7, "song_seconds": 1.3, "sample_rate": 44100, "channels": 2,
                "voice_bytes": len(voice_payload), "song_bytes": len(song_payload)},
            "counts": counts, "segments_played": segments[:32], "eof_count": eof_count,
            "service_calls": calls, "http_requests": http, "events": list(events), "observations": list(observations), "final": final,
            "scope": "Generated loopback audio; actual Qt and default verified cache; only own QAudioOutput is set to zero.",
            "limits": "No real accounts, DJ persona identity, audible continuity, physical outputs, acoustic sync or sustained internet validation."}
    finally:
        timer.stop()
        owned.close()
        app.processEvents()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    if report is not None:
        report["checks"]["cache_own_session_removed_after_close"] = not cache._session.exists()
        report["passed"] = all(report["checks"].values())
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/personal-dj-smoke"))
    parser.add_argument("--scenario", choices=("all", "sequence", "pause", "seek", "repeat"), default="all")
    parser.add_argument("--timeout", type=float, default=12)
    options = parser.parse_args()
    reports = []
    for scenario in (("sequence", "pause", "seek", "repeat") if options.scenario == "all" else (options.scenario,)):
        report = run_case(options.output.resolve() / scenario, scenario, max(8, min(options.timeout, 30)))
        reports.append(report)
        print(json.dumps({key: report[key] for key in ("passed", "scenario", "checks", "elapsed_seconds")}, ensure_ascii=False), flush=True)
    summary = {"passed": all(report["passed"] for report in reports),
        "check_count": sum(len(report["checks"]) for report in reports),
        "cases": [{key: report[key] for key in ("scenario", "passed", "elapsed_seconds")} for report in reports]}
    (options.output.resolve() / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
