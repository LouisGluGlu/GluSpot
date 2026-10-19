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
import sys
import threading
import time
from types import SimpleNamespace
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer, qVersion
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication, QWidget

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineMusicService, OnlineSong, StreamInfo
from api.owned_stream_cache import OwnedStreamCache
from local.music_tasks import MusicTasks
from local.music_surface_window import MusicSurfaceWindow
from local.owned_audio_bridge import OwnedAudioBridge
from local.owned_music_player import OwnedMusicPlayer


def wav_bytes(seconds, frequency=1000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        # Equal-phase stereo survives the production analyzer's mono average.
        # This is generated PCM; only this process's Qt output is silenced.
        mono = .28 * np.sin(2 * np.pi * frequency * np.arange(round(44100 * seconds)) / 44100)
        stereo = np.repeat((mono * 32767).astype("<i2")[:, None], 2, axis=1)
        output.writeframes(stereo.tobytes())
    return buffer.getvalue()


def run_case(output, scenario, timeout):
    output.mkdir(parents=True, exist_ok=True)
    voice_seconds, song_seconds = .7, 2.4
    voice_payload, song_payload = wav_bytes(voice_seconds), wav_bytes(song_seconds, 330)
    started, gui_thread = time.monotonic(), threading.get_ident()
    lock = threading.RLock()
    counters, controls = Counter(), {}
    service_calls, requests = deque(maxlen=128), deque(maxlen=128)
    observations, events = deque(maxlen=1024), deque(maxlen=256)
    segments, music_order, speech_order = [], [], []
    eof_counts, timed_out = Counter(), False
    continuation_requested, continuation_release = threading.Event(), threading.Event()
    retired_sources = []

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
            if scenario == "voice_failure" and label == "voice2":
                with lock:
                    counters["rejected_voice_downloads"] += 1
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
            if scenario == "manual_next_tail":
                if batch == 1:
                    indices = (1,)
                else:
                    continuation_requested.set()
                    if not continuation_release.wait(timeout):
                        raise AssertionError("Fixture continuation was not released")
                    indices = (2, 3)
            elif scenario == "multi_intro":
                indices = (1, 3) if batch == 1 else (4,)
            else:
                indices = (2, 3) if scenario == "fm_toggle" else ((1, 2) if batch == 1 else (3, 4))
            for index in indices:
                song_id = str(100 + index)
                voices = (1, 2) if scenario == "multi_intro" and index == 1 else (index,)
                audio_list = [{"audioId": "voice" + str(voice), "audioUrl": address + "voice" + str(voice),
                    "duration": .7, "validTime": 60, "textType": "介绍",
                    "introductionRelatedSongIds": [int(song_id)]} for voice in voices]
                resources.extend((
                    {"type": "audio", "value": {"audioList": audio_list}},
                    {"type": "song", "value": {"songId": song_id, "songData": {
                        "id": int(song_id), "name": "Synthetic song " + song_id,
                        "duration": round(song_seconds * 1000), "artists": [{"name": "Fixture"}], "album": {"name": "Generated PCM"},
                    }}},
                ))
            return self.parser._personal_dj_batch({"code": 200, "data": {"aiDjResources": resources}})

        def personal_fm(self, source_id="netease"):
            if scenario == "fm_toggle":
                note_call("personal_fm")
                with lock:
                    identity = "101" if counters["personal_fm"] == 1 else "104"
                return (OnlineSong("netease", identity, "Synthetic song " + identity,
                    "Fixture", duration_seconds=song_seconds),)
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
    audio, dj_audio = QAudioOutput(), QAudioOutput()
    music_gain, speech_gain = .65, .9
    audio.setVolume(music_gain)
    dj_audio.setVolume(speech_gain)
    # Muting both process-local outputs preserves measurable gain ratios.
    # No endpoint, system volume or external routing state is changed.
    audio.setMuted(True)
    dj_audio.setMuted(True)
    owned = OwnedMusicPlayer(FixtureService(), LocalLyricsStore(root=output / "lyrics"),
        audio=audio, dj_audio=dj_audio)
    bridge = OwnedAudioBridge(owned)
    view_owner = QWidget()
    view_owner.data_provider = SimpleNamespace(
        get_owned_playback_mode=lambda: owned.playback_mode,
        set_owned_playback_mode=owned.set_playback_mode,
        get_owned_playback_modes=owned.available_playback_modes,
        is_owned_personal_fm=owned.is_personal_fm,
        get_owned_personal_dj_enabled=lambda: owned.personal_dj_enabled,
        set_owned_personal_dj_enabled=owned.set_personal_dj_enabled,
        is_owned_personal_dj_speaking=owned.is_personal_dj_speaking,
        get_owned_personal_dj_audio_frame=bridge.get_personal_dj_audio_frame,
        get_personal_dj_presentation=owned.get_personal_dj_presentation,
    )
    surface = MusicSurfaceWindow(view_owner)
    surface._timer.stop()
    surface._audio_output_status_timer.stop()
    if bridge.output is not None:
        bridge.output.audioBufferReceived.connect(lambda buffer: counters.update(
            {"decoded_valid_pcm_buffers": int(buffer.isValid() and buffer.frameCount() > 0)}))
    if bridge.dj_output is not None:
        bridge.dj_output.audioBufferReceived.connect(lambda buffer: counters.update(
            {"decoded_valid_speech_pcm_buffers": int(buffer.isValid() and buffer.frameCount() > 0)}))
    player, speech_player, loop, timer = owned.player, owned.dj_player, QEventLoop(), QTimer()
    timer.setInterval(20)
    seen_epochs, seen_music, seen_speech = set(), set(), set()

    def snapshot(kind="sample", value=""):
        song = owned.queue[owned.index] if 0 <= owned.index < len(owned.queue) else None
        music_label = "song" + song.song_id if song and song.media_kind != "dj_voice" else ""
        speaking = owned.is_personal_dj_speaking()
        speech_token = owned.personal_dj_audio_token()
        voice_label = speech_token[2].removeprefix("dj:") if speaking else ""
        # This is the visible presentation phase, not sequential audio: music
        # and speech actually run concurrently during a genuine introduction.
        label = voice_label or music_label
        track = owned.track
        surface._sync_owned_playback_mode()
        surface._update_personal_dj_control(time.monotonic())
        frame = bridge.get_personal_dj_audio_frame()
        position = speech_player.position() / 1000.0
        with bridge._dj_lock:
            aligned = frame is not None and any(start <= position < end and measured is frame
                for start, end, measured in bridge._dj_frames)
        return {"at": elapsed(), "kind": kind, "value": value, "segment": label,
            "epoch": owned._epoch, "position_ms": speech_player.position() if speaking else player.position(),
            "duration_ms": speech_player.duration() if speaking else player.duration(),
            "qt_state": player.playbackState().name, "owned_state": owned.status,
            "music_segment": music_label, "speech_segment": voice_label, "speaking": speaking,
            "music_position_ms": player.position(), "music_duration_ms": player.duration(),
            "speech_position_ms": speech_player.position(), "speech_duration_ms": speech_player.duration(),
            "speech_qt_state": speech_player.playbackState().name, "speech_token": speech_token,
            "music_gain": audio.volume(), "speech_gain": dj_audio.volume(),
            "music_gain_factor": owned.personal_dj_music_gain(),
            "music_output_muted": audio.isMuted(), "speech_output_muted": dj_audio.isMuted(),
            "is_playing": bool(track and track.is_playing),
            "visual_track_id": track.track_id if track else "",
            "visual_title": track.title if track else "",
            "visual_position": track.position_seconds if track else None,
            "visual_duration": track.duration_seconds if track else None,
            "dj_presentation": owned.get_personal_dj_presentation(),
            "dj_bands": [frame.low, frame.mid, frame.high] if frame is not None else None,
            "dj_frame_matches_current_presentation": aligned,
            "dj_ui_consumed_same_frame": frame is not None and surface._dj_audio_frame == frame,
            "source_is_local_file": player.source().isLocalFile(),
            "speech_source_is_local_file": speech_player.source().isLocalFile(),
            "music_source_empty": player.source().isEmpty(), "speech_source_empty": speech_player.source().isEmpty(),
            "song_lyrics_loaded": bool(track and track.lyrics == ("Synthetic first line", "Synthetic second line"))}

    def media_status(decoder, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            eof_counts[decoder] += 1
        events.append({"at": elapsed(), "kind": "media", "decoder": decoder, "value": status.name})

    for decoder, qt_player in (("music", player), ("speech", speech_player)):
        qt_player.mediaStatusChanged.connect(lambda status, name=decoder: media_status(name, status))
        qt_player.errorOccurred.connect(lambda code, _text, name=decoder: events.append(
            {"at": elapsed(), "kind": "error", "decoder": name, "value": code.name}))

        def state_changed(state, name=decoder, source_player=qt_player):
            if state == QMediaPlayer.PlaybackState.PlayingState and retired_sources:
                old = retired_sources[0][0 if name == "music" else 1]
                if not old.isEmpty() and source_player.source() == old:
                    controls["retired_source_revived"] = True
            events.append({"at": elapsed(), "kind": "state", "decoder": name, "value": state.name})

        qt_player.playbackStateChanged.connect(state_changed)
    owned.statusChanged.connect(lambda status: events.append({"at": elapsed(), "kind": "owned", "value": status}))

    def pause_round(label, sample):
        prefix = label + "_pause"
        active = speech_player if label.startswith("voice") else player
        if prefix + "_at" not in controls and active.position() >= 100:
            controls[prefix + "_accepted"] = owned.send_playback_command("toggle_play_pause")
            if label.startswith("voice"):
                controls[prefix + "_dj_frame_cleared_when_paused"] = bridge.get_personal_dj_audio_frame() is None
            controls[prefix + "_at"] = elapsed()
            controls[prefix + "_position"] = active.position()
            controls[prefix + "_music_position"] = player.position()
            controls[prefix + "_epoch"] = owned._epoch
        elif prefix + "_at" in controls and prefix + "_resumed" not in controls:
            if elapsed() - controls[prefix + "_at"] >= .3:
                controls[prefix + "_stable"] = (active.playbackState() == QMediaPlayer.PlaybackState.PausedState
                    and player.playbackState() == QMediaPlayer.PlaybackState.PausedState
                    and owned._epoch == controls[prefix + "_epoch"]
                    and abs(active.position() - controls[prefix + "_position"]) <= 30
                    and abs(player.position() - controls[prefix + "_music_position"]) <= 30)
                controls[prefix + "_resumed"] = owned.send_playback_command("toggle_play_pause")
        elif controls.get(prefix + "_resumed") and active.position() >= controls[prefix + "_position"] + 100:
            controls[prefix + "_advanced"] = True

    def check():
        nonlocal timed_out
        sample = snapshot()
        observations.append(sample)
        if sample["qt_state"] == "PlayingState" and sample["position_ms"] >= 80:
            phase = (sample["epoch"], sample["segment"], tuple(sample["speech_token"]) if sample["speaking"] else ())
            if phase not in seen_epochs:
                seen_epochs.add(phase)
                segments.append(sample["segment"])
        music_key = (sample["epoch"], sample["music_segment"])
        if sample["qt_state"] == "PlayingState" and sample["music_position_ms"] >= 80 and music_key not in seen_music:
            seen_music.add(music_key)
            music_order.append(sample["music_segment"])
        speech_key = tuple(sample["speech_token"])
        if sample["speaking"] and sample["speech_position_ms"] >= 80 and speech_key not in seen_speech:
            seen_speech.add(speech_key)
            speech_order.append(sample["speech_segment"])
        if scenario == "manual_next_tail":
            if (sample["speech_segment"] == "voice1" and sample["speech_position_ms"] >= 150
                    and continuation_requested.is_set() and "manual_next_accepted" not in controls):
                retired_sources.append((player.source(), speech_player.source()))
                controls["manual_next_accepted"] = owned.send_playback_command("next")
                controls["tail_skip_at"] = elapsed()
                after = snapshot("immediate_tail_next")
                observations.append(after)
                controls["tail_both_decoders_retired_before_catalog_release"] = (
                    after["qt_state"] == after["speech_qt_state"] == "StoppedState"
                    and after["music_source_empty"] and after["speech_source_empty"]
                    and not after["speaking"] and after["dj_bands"] is None
                    and not continuation_release.is_set())
                controls["tail_wait_never_revived_old_sources"] = True
            elif "tail_skip_at" in controls and not continuation_release.is_set():
                controls["tail_wait_samples"] = controls.get("tail_wait_samples", 0) + 1
                controls["tail_wait_never_revived_old_sources"] &= (
                    sample["qt_state"] == sample["speech_qt_state"] == "StoppedState"
                    and sample["music_source_empty"] and sample["speech_source_empty"]
                    and not sample["speaking"] and sample["dj_bands"] is None)
                if elapsed() - controls["tail_skip_at"] >= .5:
                    controls["tail_catalog_released_at"] = elapsed()
                    continuation_release.set()
            if (continuation_release.is_set() and sample["segment"] == "song102"
                    and sample["music_position_ms"] >= 900 and not sample["speaking"]):
                loop.quit()
        elif scenario in {"manual_next", "manual_previous"}:
            if sample["segment"] == "song101" and sample["position_ms"] >= 1800 and "manual_next_accepted" not in controls:
                controls["manual_previous_visual"] = sample["visual_track_id"]
                controls["manual_previous_position"] = sample["visual_position"]
                controls["manual_next_accepted"] = owned.send_playback_command("next")
            if sample["segment"] == "song102" and sample["position_ms"] >= 150:
                if scenario == "manual_next":
                    loop.quit()
                elif "manual_previous_accepted" not in controls:
                    controls["manual_previous_accepted"] = owned.send_playback_command("previous")
            if scenario == "manual_previous" and controls.get("manual_previous_accepted") and len(segments) >= 6 and sample["segment"] == "song101" and sample["position_ms"] >= 150:
                loop.quit()
        elif scenario == "voice_failure":
            if sample["segment"] == "song102" and sample["position_ms"] >= 150:
                loop.quit()
        elif scenario == "fm_toggle":
            if sample["segment"] == "song101" and sample["position_ms"] >= 100 and "enabled" not in controls:
                epoch, source, position = owned._epoch, player.source(), player.position()
                controls["enabled"] = owned.set_personal_dj_enabled(True)
                controls["current_song_unchanged"] = (owned._epoch == epoch and player.source() == source
                    and player.position() == position and player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
            if sample["segment"] == "voice2" and sample["position_ms"] >= 100 and "disabled" not in controls:
                controls["voice_pause_accepted"] = owned.send_playback_command("toggle_play_pause")
                controls["paused_voice_has_no_frequency_frame"] = bridge.get_personal_dj_audio_frame() is None
                controls["disabled"] = owned.set_personal_dj_enabled(False)
                controls["disabled_at"] = elapsed()
            if sample["segment"] == "song102" and "disabled_at" in controls and "song_resumed" not in controls:
                if elapsed() - controls["disabled_at"] >= .2 and player.isSeekable():
                    controls["disabled_voice_song_remains_paused"] = (
                        player.playbackState() == QMediaPlayer.PlaybackState.PausedState and not owned.track.is_playing)
                    controls["song_resumed"] = owned.send_playback_command("toggle_play_pause")
            if sample["segment"] == "song103" and sample["position_ms"] >= 100:
                loop.quit()
        elif scenario == "pause":
            if "voice1_pause_at" in controls and not controls.get("voice1_pause_advanced"):
                pause_round("voice1", sample)
            elif sample["segment"] in {"voice1", "song101"}:
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
        accepted = owned.play_personal_fm("netease") if scenario == "fm_toggle" else owned.play_personal_dj("netease")
        timer.start()
        loop.exec()
        final = snapshot("final")
        with lock:
            counts, calls, http = dict(counters), list(service_calls), list(requests)
        established = [sample for sample in observations if sample["qt_state"] == "PlayingState"]
        speech_samples = [sample for sample in established if sample["speaking"]
            and sample["speech_position_ms"] >= 80]
        measured = [sample for sample in observations if sample["dj_bands"] is not None
            and max(sample["dj_bands"]) > .02]
        errors = [event for event in events if event["kind"] == "error"]
        checks = {
            "dj_start_accepted": bool(accepted),
            "two_real_independent_qmedia_players": type(player) is type(speech_player) is QMediaPlayer and player is not speech_player,
            "both_real_qt_audio_buffer_outputs_connected": type(bridge.output) is type(bridge.dj_output) is QAudioBufferOutput,
            "actual_decoded_pcm_received_without_casting": counters.get("decoded_valid_pcm_buffers", 0) > 0 and not bridge._direct,
            "actual_speech_pcm_received_without_casting": counters.get("decoded_valid_speech_pcm_buffers", 0) > 0 and not bridge._direct,
            "speech_frequency_nonzero_from_actual_decoder": bool(measured),
            "decoded_sine_selects_actual_mid_band": bool(measured) and all(
                sample["dj_bands"][1] > max(sample["dj_bands"][0], sample["dj_bands"][2]) for sample in measured),
            "frequency_frames_match_actual_presentation_clock": bool(measured) and all(
                sample["dj_frame_matches_current_presentation"] for sample in measured),
            "music_surface_consumes_actual_pcm_frame": bool(measured) and all(
                sample["dj_ui_consumed_same_frame"] for sample in measured),
            "ordinary_music_has_no_dj_frequency_frame": all(
                sample["dj_bands"] is None for sample in observations if not sample["speaking"]),
            "production_default_complete_audio_cache": type(cache) is OwnedStreamCache,
            "real_music_task_workers": all(type(worker) is MusicTasks for worker in
                (owned.tasks, owned.artwork_tasks, owned.prefetch_tasks, owned.download_tasks)),
            "service_calls_off_gui_thread": bool(calls) and counts.get("gui_service_calls", 0) == 0,
            "song_stream_endpoint_only_for_songs": counts.get("stream", 0) > 0 and counts.get("unexpected_voice_stream", 0) == 0,
            "song_lyric_endpoint_only_for_songs": counts.get("lyrics", 0) > 0 and counts.get("unexpected_voice_lyrics", 0) == 0,
            "dj_continuation_never_calls_ordinary_fm": counts.get("unexpected_personal_fm", 0) == 0,
            "decoder_reads_local_completed_files": bool(established) and all(sample["source_is_local_file"] for sample in established),
            "speech_decoder_reads_local_completed_files": bool(speech_samples) and all(sample["speech_source_is_local_file"] for sample in speech_samples),
            "speech_duration_really_decoded": any(sample["speech_duration_ms"] == round(voice_seconds * 1000) for sample in speech_samples),
            "song_duration_really_decoded": any(sample["music_duration_ms"] == round(song_seconds * 1000) for sample in established),
            "real_song_lyrics_loaded": any(sample["song_lyrics_loaded"] for sample in established),
            "http_fixture_requested_without_cookie": bool(http) and counts.get("http_cookie", 0) == 0,
            "both_process_local_outputs_muted": all(sample["music_output_muted"] and sample["speech_output_muted"] for sample in observations),
            "real_end_of_media_observed": sum(eof_counts.values()) > 0,
            "no_qt_errors": not errors,
            "no_terminal_failure": owned.status not in {"playback_failed", "stream_unavailable", "catalog_unavailable"},
            "no_timeout": not timed_out,
            "speech_never_replaces_song_visual_with_voice_metadata": all(
                not sample["visual_track_id"].startswith("owned:netease:dj:") and "私人DJ" not in sample["visual_title"]
                for sample in observations),
            "music_and_speech_really_play_together": bool(speech_samples) and all(
                sample["is_playing"] and sample["speech_qt_state"] == "PlayingState" for sample in speech_samples),
            "speech_only_pauses_cover_pose": bool(speech_samples) and all(
                sample["dj_presentation"]["cover_paused"] for sample in speech_samples),
            "music_is_twenty_percent_under_normal_speech_gain": bool(speech_samples) and all(
                abs(sample["music_gain_factor"] - .2) < .001
                and abs(sample["music_gain"] - music_gain * .2) < .001
                and abs(sample["speech_gain"] - speech_gain) < .001 for sample in speech_samples),
            "speech_title_matches_introduced_song": all(
                sample["dj_presentation"]["next_title"] == "Synthetic song " + str(
                    101 if scenario == "multi_intro" and sample["segment"] == "voice2"
                    else 100 + int(sample["segment"].removeprefix("voice")))
                for sample in speech_samples),
            "voice_decoder_duration_does_not_replace_song_duration": all(
                sample["visual_duration"] == song_seconds for sample in speech_samples),
            "song_visual_clock_follows_music_decoder_during_speech": bool(speech_samples) and all(
                abs(sample["visual_position"] * 1000 - sample["music_position_ms"]) <= 100 for sample in speech_samples),
            "song_music_clock_really_advances_during_speech": any(
                sample["music_position_ms"] >= 100 for sample in speech_samples),
            "speech_displays_introduced_song_visual": all(
                sample["visual_track_id"] == "owned:netease:" + str(
                    101 if scenario == "multi_intro" and sample["segment"] == "voice2"
                    else 100 + int(sample["segment"].removeprefix("voice")))
                and sample["visual_title"] == sample["dj_presentation"]["next_title"]
                for sample in speech_samples),
            "introduced_song_lyrics_available_during_speech": any(
                sample["song_lyrics_loaded"] for sample in speech_samples),
        }
        if scenario == "multi_intro":
            checks.update({"multiple_intro_segments_preserve_target_order": segments[:5] == ["voice1", "voice2", "song101", "voice3", "song103"],
                "prefetch_never_replaces_current_speech_token": all(
                    len({sample["dj_presentation"]["token"] for sample in speech_samples
                        if (sample["epoch"], sample["speech_segment"]) == identity}) == 1
                    for identity in {(sample["epoch"], sample["speech_segment"]) for sample in speech_samples})})
        elif scenario == "manual_next_tail":
            checks.update({
                "manual_next_accepted": bool(controls.get("manual_next_accepted")),
                "actual_catalog_worker_waited_for_release": continuation_requested.is_set()
                    and bool(controls.get("tail_catalog_released_at")),
                "tail_both_decoders_retired_before_catalog_release": bool(controls.get("tail_both_decoders_retired_before_catalog_release")),
                "tail_no_old_sources_revive_during_half_second_wait": bool(
                    controls.get("tail_wait_never_revived_old_sources") and controls.get("tail_wait_samples", 0) >= 10
                    and controls.get("tail_catalog_released_at", 0) - controls.get("tail_skip_at", 0) >= .5),
                "retired_source_never_reenters_playing_state": not controls.get("retired_source_revived", False),
                "next_catalog_plays_matching_song_and_intro": music_order[:2] == ["song101", "song102"]
                    and speech_order[:2] == ["voice1", "voice2"] and any(
                        sample["music_segment"] == "song102" and sample["speech_segment"] == "voice2"
                        and sample["dj_bands"] is not None for sample in speech_samples),
            })
        elif scenario in {"manual_next", "manual_previous"}:
            checks.update({
                "manual_next_accepted": bool(controls.get("manual_next_accepted")),
                "manual_next_plays_matching_intro_with_target_song": music_order[:2] == ["song101", "song102"]
                    and speech_order[:2] == ["voice1", "voice2"],
                "manual_next_keeps_cover_pose_paused_while_both_decoders_play": any(
                    sample["speech_segment"] == "voice2" for sample in speech_samples)
                    and all(sample["visual_track_id"] == "owned:netease:102" and sample["is_playing"]
                        and sample["dj_presentation"]["cover_paused"]
                        for sample in speech_samples if sample["speech_segment"] == "voice2"),
                "completed_intro_restores_original_gain_over_one_second": any(
                    sample["music_segment"] == "song101" and not sample["speaking"]
                    and sample["music_position_ms"] >= 1700 and abs(sample["music_gain"] - music_gain) < .001
                    for sample in established) and len({round(sample["music_gain_factor"], 2)
                        for sample in established if sample["music_segment"] == "song101"
                        and not sample["speaking"] and .2 < sample["music_gain_factor"] < 1}) >= 5,
            })
            if scenario == "manual_previous":
                checks.update({"manual_previous_accepted": bool(controls.get("manual_previous_accepted")),
                    "manual_previous_replays_matching_real_intro": segments[:6] == ["voice1", "song101", "voice2", "song102", "voice1", "song101"]})
        elif scenario == "voice_failure":
            checks.update({"actual_voice_http_rejection_observed": counts.get("rejected_voice_downloads", 0) > 0,
                "failed_voice_does_not_block_associated_song": segments[:3] == ["voice1", "song101", "song102"],
                "failed_voice_never_plays": "voice2" not in segments,
                "failed_voice_presentation_observed": any(sample["dj_presentation"]["state"] == "failed" for sample in observations)})
        elif scenario == "fm_toggle":
            checks.update({"starts_in_ordinary_fm": counts.get("personal_fm", 0) > 0,
                "same_fm_context_has_real_dj_batch": counts.get("personal_dj", 0) > 0 and owned.is_personal_fm(),
                "enable_preserves_current_decoded_song": bool(controls.get("enabled") and controls.get("current_song_unchanged")),
                "disable_paused_voice_preserves_pause": bool(controls.get("disabled") and controls.get("disabled_voice_song_remains_paused")),
                "pause_clears_actual_speech_frequency_frame": bool(controls.get("paused_voice_has_no_frequency_frame")),
                "introduced_song_resumed_after_disable": bool(controls.get("song_resumed")),
                "disabled_dj_cannot_play_later_introduction": "voice3" not in segments and not owned.personal_dj_enabled,
                "real_music_voice_music_order": segments[:4] == ["song101", "voice2", "song102", "song103"]})
        elif scenario == "sequence":
            checks.update({"two_dj_batches_requested": counts.get("personal_dj", 0) >= 2,
                "voice_song_order_preserved_through_natural_continuation": segments[:6] == ["voice1", "song101", "voice2", "song102", "voice3", "song103"],
                "natural_eof_advances_to_second_batch": eof_counts["music"] >= 2 and eof_counts["speech"] >= 3,
                "second_batch_song_clock_advanced": final["segment"] == "song103" and final["position_ms"] >= 300})
        elif scenario == "pause":
            checks.update({"ordered_voice_song_after_pause": segments[:3] == ["voice1", "song101", "voice2"]})
            checks["pause_clears_actual_speech_frequency_frame"] = bool(controls.get("voice1_pause_dj_frame_cleared_when_paused"))
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
                "natural_song_eof_repeat_observed": eof_counts["music"] >= 2 and eof_counts["speech"] >= 1})
        report = {"passed": all(checks.values()), "checks": checks, "scenario": scenario,
            "qt_version": qVersion(), "elapsed_seconds": elapsed(), "controls": controls,
            "fixture": {"voice_seconds": voice_seconds, "song_seconds": song_seconds, "sample_rate": 44100, "channels": 2,
                "voice_bytes": len(voice_payload), "song_bytes": len(song_payload)},
            "counts": counts, "presentation_phases": segments[:32], "music_played": music_order[:32],
            "speech_played": speech_order[:32], "eof_count": dict(eof_counts),
            "service_calls": calls, "http_requests": http, "events": list(events), "observations": list(observations), "final": final,
            "scope": "Generated loopback audio; actual independent Qt music/speech decoders and default verified cache; both process-local outputs muted.",
            "limits": "No real accounts, DJ persona identity, audible continuity, physical outputs, acoustic sync or sustained internet validation."}
    finally:
        timer.stop()
        continuation_release.set()
        surface.shutdown()
        view_owner.close()
        bridge.close()
        owned.close()
        app.processEvents()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    if report is not None:
        report["checks"]["cache_own_session_removed_after_close"] = not cache._session.exists()
        report["checks"]["closed_bridge_has_no_frequency_frame_or_retained_timeline"] = (
            bridge.get_personal_dj_audio_frame() is None and not bridge._dj_frames)
        report["passed"] = all(report["checks"].values())
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/personal-dj-smoke"))
    scenarios = ("sequence", "pause", "seek", "repeat", "fm_toggle", "manual_next", "manual_previous", "manual_next_tail", "voice_failure", "multi_intro")
    parser.add_argument("--scenario", choices=("all", *scenarios), default="all")
    parser.add_argument("--timeout", type=float, default=12)
    options = parser.parse_args()
    reports = []
    for scenario in (scenarios if options.scenario == "all" else (options.scenario,)):
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
