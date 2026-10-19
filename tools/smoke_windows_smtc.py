"""Muted offline Qt playback controlled through this process's Windows SMTC.

Uses an exact unique AppUserModelID, generated WAVs and no account/network or
system-wide volume changes. Windows commands target only this test's session.
Run serially with other native Qt tests/previews.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from dataclasses import replace
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PySide6.QtWidgets import QApplication
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from local.windows_smtc import WindowsSmtcBridge, _NativeSmtcSession, _SmtcWorker


async def run(output, timeout):
    app_id = f"GluSpot.SMTC.Smoke.{os.getpid()}"
    if int(ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)) != 0:
        raise RuntimeError("Cannot isolate the smoke test's Windows session identity")
    qt_app = QApplication.instance() or QApplication([])
    gui_thread = threading.get_ident()
    checks, calls, native_observations = [], [], []
    active = True

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        if not value:
            raise AssertionError(name)

    async def until(predicate, label):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qt_app.processEvents()
            result = predicate()
            if result:
                return result
            await asyncio.sleep(.02)
        raise TimeoutError(label)

    class ObservedSession(_NativeSmtcSession):
        async def publish(self, snapshot):
            await super().publish(snapshot)
            native_observations.append((self.player.source is None, self.player.is_muted,
                not self.player.command_manager.is_enabled))

    def worker(callback):
        return _SmtcWorker(callback, session_factory=ObservedSession)

    owned = bridge = server = None
    report = {"passed": False, "checks": checks, "credentials_used": False,
        "external_network_used": False, "loopback_http_used": True, "system_volume_modified": False}
    try:
        with tempfile.TemporaryDirectory(prefix="GluSpotSMTC-") as temporary:
            root = Path(temporary)
            audio = root / "generated.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(22050)
                wav.writeframes(b"\0\0\0\0" * (22050 * 40))
            payload = audio.read_bytes()
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args): pass
                def do_GET(self):
                    if self.path != "/audio.wav":
                        self.send_error(404)
                        return
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
                    if header and not partial or not 0 <= low <= high < len(payload):
                        self.send_error(416)
                        return
                    self.send_response(206 if partial else 200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(high - low + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    if partial:
                        self.send_header("Content-Range", f"bytes {low}-{high}/{len(payload)}")
                    self.end_headers()
                    try:
                        self.wfile.write(payload[low:high + 1])
                    except (BrokenPipeError, ConnectionResetError):
                        pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, name="GluSpotSMTCFixture", daemon=True).start()
            audio_url = f"http://127.0.0.1:{server.server_port}/audio.wav"
            service = SimpleNamespace(stream_url=lambda source, song: StreamInfo(source, song,
                audio_url, True), lyrics=lambda source, song: LyricPayload(source, song))
            owned = OwnedMusicPlayer(service, LocalLyricsStore(root=root / "lyrics"), stream_cache=None)
            owned.audio.setMuted(True)
            owned.dj_audio.setMuted(True)
            send_command, seek = owned.send_playback_command, owned.seek_playback_position
            def recorded_command(command):
                calls.append((command, threading.get_ident() == gui_thread))
                return send_command(command)
            def recorded_seek(value, track_id):
                calls.append(("seek", threading.get_ident() == gui_thread))
                return seek(value, track_id)
            owned.send_playback_command = recorded_command
            owned.seek_playback_position = recorded_seek
            bridge = WindowsSmtcBridge(owned, lambda: active, worker_factory=worker)
            songs = tuple(OnlineSong("local", str(i), f"SMTC Offline Song {i}",
                artist="GluSpot Test", album="Offline Album", duration_seconds=40.0) for i in (1, 2))
            owned.play_queue(songs)
            await until(lambda: owned.track is not None and owned.track.is_playing
                and owned.track.position_seconds > .1, "actual muted Qt playback")
            manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            def own_session():
                return next((session for session in manager.get_sessions()
                    if session.source_app_user_model_id == app_id), None)
            session = await until(own_session, "this process's SMTC registration")
            await until(lambda: session.get_playback_info().playback_status.name == "PLAYING", "playing publication")
            properties = await session.try_get_media_properties_async()
            check("title_artist_album_readback", (properties.title, properties.artist, properties.album_title)
                == (songs[0].title, songs[0].artist, songs[0].album))
            check("timeline_readback", session.get_timeline_properties().end_time.total_seconds() == 40.0)
            art = io.BytesIO()
            Image.new("RGB", (32, 32), (60, 100, 150)).save(art, format="PNG")
            owned.track = replace(owned.track, album_art_bytes=art.getvalue())
            owned.changed.emit()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                qt_app.processEvents()
                properties = await session.try_get_media_properties_async()
                if properties.thumbnail is not None:
                    break
                await asyncio.sleep(.03)
            check("thumbnail_readback", properties.thumbnail is not None)
            check("pause_ack", await session.try_pause_async())
            await until(lambda: not owned.track.is_playing and owned.player.playbackState().name == "PausedState",
                "system Pause reached owned Qt decoder")
            await until(lambda: session.get_playback_info().playback_status.name == "PAUSED", "paused publication")
            check("pause_state_readback", True)
            check("play_ack", await session.try_play_async())
            await until(lambda: owned.track.is_playing, "system Play reached owned Qt decoder")
            check("next_ack", await session.try_skip_next_async())
            await until(lambda: owned.track.track_id == "owned:local:2" and owned.track.is_playing,
                "system Next used existing queue navigation")
            check("next_owned_queue", owned.index == 1)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                qt_app.processEvents()
                properties = await session.try_get_media_properties_async()
                if properties.title == songs[1].title:
                    break
                await asyncio.sleep(.03)
            check("next_title_readback", properties.title == songs[1].title)
            check("previous_ack", await session.try_skip_previous_async())
            await until(lambda: owned.track.track_id == "owned:local:1" and owned.track.is_playing,
                "system Previous used existing queue navigation")
            check("previous_owned_queue", owned.index == 0)
            # Wait for that generation's publication before targeting seek.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                qt_app.processEvents()
                properties = await session.try_get_media_properties_async()
                if properties.title == songs[0].title:
                    break
                await asyncio.sleep(.03)
            check("seek_ack", await session.try_change_playback_position_async(70_000_000))
            await until(lambda: 6.8 <= owned.track.position_seconds <= 8.5, "system seek reached Qt decoder")
            check("seek_actual_clock", True)
            check("all_native_commands_on_gui_thread", calls and all(gui for _command, gui in calls))
            check("separate_smtc_session_never_decodes_or_outputs_audio",
                native_observations and all(all(row) for row in native_observations))
            check("both_qt_outputs_remain_process_muted", owned.audio.isMuted() and owned.dj_audio.isMuted())
            active = False
            bridge.refresh()
            await until(lambda: own_session() is None, "disabled owned source retires its Windows session")
            check("disabled_source_unregisters", True)
            check("native_worker_error_empty", not bridge._worker.last_error)
            bridge.close()
            owned.close()
            qt_app.processEvents()
            bridge = owned = None
        report.update(passed=True, commands=[command for command, _gui in calls])
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        if bridge is not None:
            bridge.close()
        if owned is not None:
            owned.close()
        if server is not None:
            server.shutdown()
            server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/windows-smtc-smoke/result.json"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows is required")
    report = asyncio.run(run(args.output, args.timeout))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
