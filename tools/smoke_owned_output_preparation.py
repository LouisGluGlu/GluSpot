"""Actual muted Qt decode with simulated receiver readiness, never a device route.

Only the fixture's QAudioOutput is volume-zero/muted. Receiver/backend objects
are in-memory; no real receiver, endpoint setting or system default is touched.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QEventLoop, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice
from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, StreamInfo
from api.owned_audio_outputs import OwnedAudioOutputController
from api.windows_audio_devices import AudioOutputDevice
from local.owned_audio_bridge import OwnedAudioBridge
from local.owned_music_player import OwnedMusicPlayer


class FixtureTasks(QObject):
    completed = Signal(object, object, object)
    def submit(self, *_): return True
    def close(self): pass


class FixtureBackend:
    def list_outputs(self):
        return (AudioOutputDevice("fixture-local", "Muted fixture", "speaker", is_default=True),)
    def close(self): pass


class FixtureDiscovery:
    state = "fixture"
    def devices(self):
        return (AudioRouteDevice("fixture-airplay", "In-memory receiver", "homepod", "airplay",
            host="127.0.0.1", device_id="fixture"),)
    def close(self): pass


class FixtureSender:
    def __init__(self): self.events, self.packets, self.starts = [], [], []
    def start_pcm(self, devices, epoch): self.starts.append(epoch)
    def stop(self): pass
    def pcm_error(self): return ""
    def poll_events(self):
        result, self.events = self.events, []
        return result
    def submit_pcm(self, data, epoch):
        self.packets.append((epoch, len(data)))
        return True
    def latency_estimate(self): return None
    def receiver_volume_state(self, *_): return {"known": False}


def spin(seconds, condition=None):
    loop, timer = QEventLoop(), QTimer()
    start = time.monotonic()
    timer.setInterval(5)
    timer.timeout.connect(lambda: loop.quit() if time.monotonic() - start >= seconds
        or condition is not None and condition() else None)
    timer.start()
    loop.exec()
    timer.stop()


def run():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    checks, first_buffers, errors = {}, {}, []
    with tempfile.TemporaryDirectory(prefix="GluSpot-output-prepare-fixture-") as root:
        root = Path(root)
        path = root / "fixture.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(44100)
            wav.writeframes(b"".join(struct.pack("<hh", value, value)
                for frame in range(44100 * 6)
                for value in (round(1800 * math.sin(frame * math.tau * 440 / 44100)),)))
        audio, dj_audio = QAudioOutput(), QAudioOutput()
        audio.setVolume(0); dj_audio.setVolume(0)
        owned = OwnedMusicPlayer(SimpleNamespace(), LocalLyricsStore(root / "lyrics"),
            audio=audio, dj_audio=dj_audio, tasks=FixtureTasks(), artwork_tasks=FixtureTasks(),
            prefetch_tasks=FixtureTasks(), stream_cache=None)
        bridge, sender = OwnedAudioBridge(owned), FixtureSender()
        controller = OwnedAudioOutputController(bridge, backend=FixtureBackend(),
            discovery=FixtureDiscovery(), sender=sender, start_worker=False)
        owned.player.errorOccurred.connect(lambda code, *_: errors.append(code.name))
        def observed(buffer):
            if (buffer.isValid() and buffer.frameCount() and not bridge._restarting
                    and controller.accepts_pcm(bridge._epoch)):
                first_buffers.setdefault(bridge._epoch, buffer.startTime())
        bridge.output.audioBufferReceived.connect(observed)
        source = QUrl.fromLocalFile(str(path))
        def load():
            song = owned.queue[owned.index]
            owned._start_stream_audio(StreamInfo(song.source_id, song.song_id, "https://fixture.invalid/local", True), source)
        def ready():
            sender.events = [{"event": "waiting_audio"}]
            controller._step()
        try:
            controller._inventory()
            owned.play_queue((OnlineSong("netease", "fixture-one", "One", duration_seconds=6),
                OnlineSong("netease", "fixture-two", "Two", duration_seconds=6)))
            controller._choose(("fixture-local", "fixture-airplay"))
            load()
            spin(2.0)
            checks["two_second_authentication_preserves_intro"] = owned.player.position() <= 20 and not sender.packets
            checks["authentication_holds_private_mute"] = audio.isMuted() and owned._output_preparing
            ready()
            spin(3, lambda: owned.player.position() >= 500)
            checks["ready_resumes_from_original_first_pcm"] = first_buffers.get(1) == 0
            checks["ready_advances_decoder_while_private_output_stays_muted"] = owned.player.position() >= 500 and audio.isMuted()

            owned.seek_playback_position(2.0)
            controller._step()
            held_position = owned.player.position()
            spin(2.0)
            checks["seek_authentication_holds_target_position"] = abs(owned.player.position() - held_position) <= 20
            ready()
            spin(3, lambda: controller._session_epoch in first_buffers)
            seek_epoch = controller._session_epoch
            checks["seek_first_pcm_preserves_requested_position"] = abs(first_buffers.get(seek_epoch, -10000000) - 2000000) <= 25000

            owned.send_playback_command("next")
            controller._step()
            load()
            spin(2.0)
            track_epoch = controller._session_epoch
            checks["track_restart_preserves_new_intro"] = owned.player.position() <= 20 and track_epoch not in first_buffers
            owned.send_playback_command("toggle_play_pause")
            ready()
            spin(.4)
            checks["user_pause_cancels_ready_autoplay"] = owned.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
            owned.send_playback_command("toggle_play_pause")
            spin(3, lambda: track_epoch in first_buffers)
            checks["explicit_resume_keeps_new_intro"] = first_buffers.get(track_epoch) == 0

            controller.media_changed(bridge.media_token(), force=True)
            controller._step()
            before_stop = owned.player.position()
            controller.stop_cast(); controller._step()
            spin(.3)
            checks["stop_restores_private_mute_and_prior_play_intent"] = (not audio.isMuted()
                and not owned._output_preparing and owned.player.position() > before_stop)
            checks["fixture_outputs_remain_volume_zero"] = audio.volume() == 0 and dj_audio.volume() == 0
            checks["no_decoder_errors"] = not errors
        finally:
            controller.close(); bridge.close(); owned.close()
            app.processEvents()
    return {"passed": all(checks.values()), "checks": checks,
        "first_pcm_microseconds": first_buffers, "native_receiver_contacts": 0,
        "system_endpoint_mutations": 0, "packet_count": len(sender.packets)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-output-preparation-2026-10-17.json"))
    arguments = parser.parse_args()
    result = run()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
