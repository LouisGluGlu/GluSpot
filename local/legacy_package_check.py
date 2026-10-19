"""Isolated acceptance diagnostic for the Windows 7 application package.

Run before app.main, its single-instance lock and settings/account startup.
Only the requested report survives. Generated WAVs/caches live in a temporary
directory, windows stay hidden, and decoding uses private mute plus volume zero.
This verifies the running package on its actual host; it is not Win7 hardware
certification and does not exercise online accounts or physical receivers.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import platform
import struct
import sys
import tempfile
import threading
import time
import wave

from local.branding import APP_NAME, APP_VERSION


@contextmanager
def _attribute(owner, name, value):
    previous = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        setattr(owner, name, previous)


def _brief_error(exc):
    return "%s: %s" % (type(exc).__name__, str(exc)[:1200])


def _pump_until(app, condition, label, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise RuntimeError(label)


def _pump_for(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)


def _media_check(app, directory, require):
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaPlayer

    player = QMediaPlayer()
    audio = QAudioOutput()
    audio.setVolume(0)
    audio.setMuted(True)
    player.setAudioOutput(audio)
    format_ = QAudioFormat()
    format_.setSampleRate(44100)
    format_.setChannelCount(2)
    format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    output = QAudioBufferOutput(format_)
    buffers = []
    errors = []
    player.errorOccurred.connect(lambda code, detail: errors.append((int(code), str(detail)[:500])))
    attach_probe = getattr(player, "setAudioBufferOutput", None)

    def received(buffer):
        if buffer.isValid() and buffer.frameCount():
            buffers.append({"frames": buffer.frameCount(), "bytes": len(buffer.constData()),
                "rate": buffer.format().sampleRate(), "channels": buffer.format().channelCount(),
                "sample_format": int(buffer.format().sampleFormat()), "start": buffer.startTime()})

    try:
        require("native_audio_probe_available", callable(attach_probe))
        attach_probe(output)
        output.audioBufferReceived.connect(received)
        paths = []
        for index, seconds in enumerate((1.8, .65)):
            path = directory / ("local-decode-%d.wav" % index)
            with wave.open(str(path), "wb") as wav:
                wav.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x01\x00\xff\xff" * round(44100 * seconds))
            paths.append(path)
        player.setSource(QUrl.fromLocalFile(str(paths[0])))
        require("native_source_url", Path(player.source().toLocalFile()) == paths[0])
        player.play()
        _pump_until(app, lambda: player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and player.duration() > 0 and player.position() >= 120, "First local source did not start")
        require("native_source_duration", 1700 <= player.duration() <= 1900)
        player.pause()
        _pump_until(app, lambda: player.playbackState() == QMediaPlayer.PlaybackState.PausedState,
            "Private decoder did not acknowledge pause")
        paused = player.position()
        _pump_for(app, .15)
        require("native_pause_holds_clock", abs(player.position() - paused) <= 60)
        player.setPosition(700)
        _pump_until(app, lambda: 650 <= player.position() <= 900, "Private decoder seek failed")
        require("native_source_seekable", player.isSeekable())
        player.play()
        _pump_until(app, lambda: player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia,
            "First local source did not reach end of media")
        require("native_source_eof", player.position() >= 1700)
        player.stop()
        player.setSource(QUrl())
        player.setSource(QUrl.fromLocalFile(str(paths[1])))
        require("native_next_source", Path(player.source().toLocalFile()) == paths[1])
        player.play()
        _pump_until(app, lambda: player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and 550 <= player.duration() <= 750, "Next local source did not start")
        _pump_until(app, lambda: player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia,
            "Next local source did not reach end of media")
        require("native_next_eof", player.position() >= 550)
        require("native_decode_without_errors", not errors)
        require("native_decoded_pcm_received", bool(buffers))
        require("native_decoded_pcm_format", all(row["rate"] == 44100 and row["channels"] == 2
            and row["sample_format"] == 2 and row["bytes"] == row["frames"] * 4 for row in buffers))
        require("private_decode_stayed_silent", player.isMuted() and player.volume() == 0)
        return {"sources": 2, "buffers": len(buffers), "formats": sorted({
            (row["rate"], row["channels"], row["sample_format"]) for row in buffers}),
            "media_errors": errors, "private_mute": True, "private_volume": 0,
            "selected_windows_endpoint": False, "receiver_contacted": False}
    finally:
        player.stop()
        player.setSource(QUrl())
        if callable(attach_probe):
            attach_probe(None)
        player.setAudioOutput(None)
        player.deleteLater()
        output.deleteLater()
        audio.deleteLater()
        _pump_for(app, .05)


def _policy_check(directory, require):
    from PySide6.QtCore import QObject, Signal
    from api import providers
    from api.lyrics import LocalLyricsStore
    from local import windows_smtc
    from local.runtime_platform import normalize_playback_selection, owned_playback_only

    require("runtime_owned_playback_only", owned_playback_only())
    require("stale_system_only_selection_normalized", normalize_playback_selection(False, True) == (True, False))
    forbidden = []

    def forbidden_loop(*args, **kwargs):
        forbidden.append("WindowsMediaProvider._start_loop")
        raise RuntimeError("Diagnostic prevented an unsupported system-media loop")

    def forbidden_worker(*args, **kwargs):
        forbidden.append("WindowsSmtcBridge._SmtcWorker")
        raise RuntimeError("Diagnostic prevented an unsupported SMTC worker")

    def isolated_store(*args, **kwargs):
        kwargs["root"] = directory / "lyrics"
        kwargs["generated_root"] = directory / "generated-lyrics"
        return LocalLyricsStore(*args, **kwargs)

    class _DiagnosticOwnedSignals(QObject):
        changed = Signal()
        statusChanged = Signal(str)
        track = None

    with _attribute(providers, "LocalLyricsStore", isolated_store), \
            _attribute(providers.WindowsMediaProvider, "_start_loop", forbidden_loop), \
            _attribute(windows_smtc, "_SmtcWorker", forbidden_worker):
        provider = providers.WindowsMediaProvider(settings_getter=lambda _key, default=None: default)
        require("system_provider_no_loop", provider._loop is None and provider._thread is None)
        require("system_provider_reports_unsupported", provider._provider_status == "unsupported_os")
        require("system_sdk_not_loaded", not providers._ensure_winsdk_loaded())
        # Exercise the actual boundary against a stale persisted system=True
        # flag without constructing its optional output controller.
        boundary = providers.PlaybackSourceProvider.__new__(providers.PlaybackSourceProvider)
        boundary.settings_getter = lambda _key, default=None: True
        require("provider_boundary_rejects_system_capture", not boundary._system_enabled())
        owned = _DiagnosticOwnedSignals()
        bridge = windows_smtc.WindowsSmtcBridge(owned, lambda: True)
        try:
            require("smtc_worker_absent", bridge._worker is None)
            require("smtc_timer_inactive", not bridge._timer.isActive())
        finally:
            bridge.close()
            bridge.deleteLater()
            owned.deleteLater()
            provider.suspend_live_audio()
            provider.close_audio_outputs()
        require("unsupported_worker_guards_not_called", not forbidden)
    return {"owned_only": True, "provider_loop": False, "smtc_worker": False,
        "system_sdk_loaded": False, "isolated_lyric_store": True}


def _owned_player_check(app, directory, require):
    """Use production tasks/cache/player/PCM bridge with an offline opener."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtMultimedia import QMediaPlayer
    from api.lyrics import LocalLyricsStore
    from api.online_music import LyricPayload, OnlineSong, StreamInfo
    from api.owned_stream_cache import OwnedStreamCache
    from local.owned_audio_bridge import OwnedAudioBridge
    from local.owned_music_player import OwnedMusicPlayer

    bodies = {}
    for song_id in ("one", "two"):
        body = io.BytesIO()
        with wave.open(body, "wb") as wav:
            wav.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x02\x00\xfe\xff" * round(44100 * 1.8))
        bodies["https://gluspot-local-diagnostic.invalid/" + song_id + ".wav"] = body.getvalue()
    requests = []

    class _OfflineResponse(io.BytesIO):
        status = 200

        def __init__(self, url, body):
            super().__init__(body)
            self._url = url
            self.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(body))}

        def geturl(self):
            return self._url

    class _OfflineOpener:
        def open(self, request, timeout=None):
            url = request.full_url
            if url not in bodies:
                raise RuntimeError("Diagnostic rejected a non-fixture audio address")
            requests.append(url)
            return _OfflineResponse(url, bodies[url])

    class _OfflineMusicService:
        def stream_url(self, source, song):
            if source != "diagnostic" or song not in {"one", "two"}:
                raise RuntimeError("Diagnostic rejected a non-fixture catalog identity")
            return StreamInfo(source, song,
                "https://gluspot-local-diagnostic.invalid/" + song + ".wav", True)

        def lyrics(self, source, song):
            return LyricPayload(source, song)

    before = {thread.ident for thread in threading.enumerate()}
    cache = OwnedStreamCache(root=directory / "owned-stream-cache", opener=_OfflineOpener())
    store = LocalLyricsStore(root=directory / "owned-lyrics", generated_root=directory / "owned-generated-lyrics")
    owned = None
    bridge = None
    task_threads = []
    observed = []
    result = {}
    try:
        owned = OwnedMusicPlayer(_OfflineMusicService(), store, stream_cache=cache)
        # Neither decoder has a source until after both native outputs are
        # explicitly silent. No route controller/default-device API is used.
        for output in (owned.audio, owned.dj_audio):
            output.setVolume(0)
            output.setMuted(True)
        bridge = OwnedAudioBridge(owned)
        require("production_bridge_pcm_supported", bridge.supported and bridge.output is not None)
        require("production_bridge_no_route_controller", bridge._controller is None and not bridge._direct)
        bridge.output.audioBufferReceived.connect(lambda buffer: observed.append(buffer.frameCount()))
        task_threads = [thread for thread in threading.enumerate()
            if thread.ident not in before and thread.name.startswith("GluSpotMusic-")]
        require("production_owned_task_workers_started", len(task_threads) == 8)
        songs = (OnlineSong("diagnostic", "one", "本地播放检查一", duration_seconds=1.8),
            OnlineSong("diagnostic", "two", "本地播放检查二", duration_seconds=1.8))
        require("production_play_queue_accepted", owned.play_queue(songs))
        _pump_until(app, lambda: owned.track is not None
            and owned.track.track_id == "owned:diagnostic:one"
            and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and owned.player.position() >= 120, "Production owned player did not start the first source")
        require("production_owned_track_playing", owned.track.is_playing and owned.status == "playing")
        require("production_pause_accepted", owned.send_playback_command("toggle_play_pause"))
        _pump_until(app, lambda: owned.player.playbackState() == QMediaPlayer.PlaybackState.PausedState,
            "Production owned pause was not acknowledged")
        paused = owned.player.position()
        _pump_for(app, .15)
        require("production_pause_clock_held", abs(owned.player.position() - paused) <= 60
            and not owned.track.is_playing)
        require("production_seek_accepted", owned.seek_playback_position(.7, owned.track.track_id))
        _pump_until(app, lambda: 650 <= owned.player.position() <= 900, "Production owned seek failed")
        require("production_resume_accepted", owned.send_playback_command("toggle_play_pause"))
        _pump_until(app, lambda: owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and owned.player.position() >= 760, "Production owned resume did not advance")
        require("production_next_accepted", owned.send_playback_command("next"))
        _pump_until(app, lambda: owned.track is not None
            and owned.track.track_id == "owned:diagnostic:two"
            and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and owned.player.position() >= 120, "Production owned next source did not play")
        require("production_bridge_follows_source", bridge.media_token()[0] == "owned:diagnostic:two")
        _pump_until(app, lambda: owned.track is not None
            and owned.track.track_id == "owned:diagnostic:one"
            and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and owned.player.position() >= 100, "Production owned EOF did not continue to the next song")
        require("production_real_pcm_received", any(frames > 0 for frames in observed))
        require("production_owned_status_healthy", owned.status == "playing" and not owned.failure_reason)
        require("production_outputs_remain_silent", all(output.isMuted() and output.volume() == 0
            for output in (owned.audio, owned.dj_audio)))
        require("production_downloads_strictly_offline", bool(requests)
            and all(url in bodies for url in requests))
        result.update(queue_sources=2, automatic_eof_continuation=True,
            decoded_buffers=len(observed), offline_response_count=len(requests),
            private_mute=True, private_volume=0, route_controller_started=False,
            worker_count_started=len(task_threads))
    finally:
        if bridge is not None:
            bridge.close()
            bridge.deleteLater()
        if owned is not None:
            owned.close()
            owned.deleteLater()
        else:
            cache.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _pump_until(app, lambda: not any(thread.is_alive() for thread in task_threads),
            "Production owned task workers did not exit after close", timeout=3)
        _pump_for(app, .05)
        require("production_owned_task_workers_released", not any(thread.is_alive() for thread in task_threads))
        result["worker_count_alive_after_close"] = sum(thread.is_alive() for thread in task_threads)
    return result


def _surface_check(app, require):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QWidget
    from api.models import AlbumQueueEntry, AudioFrame, ColorToken, IslandVisualData, TimedLyricLine, TrackData
    from api.providers import EmptyIslandDataProvider
    from local.music_surface_window import MusicSurfaceWindow
    from local.playback_entry_window import PlaybackEntryWindow

    art = QImage(128, 128, QImage.Format.Format_RGB32)
    art.fill(QColor(146, 77, 109))
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    art.save(buffer, "PNG")
    buffer.close()
    rows = tuple(TimedLyricLine(float(index * 2), text) for index, text in enumerate((
        "这是一段离线检查", "没有连接音乐账号", "歌词与封面在本地绘制", "程序内播放兼容检查")))
    track = TrackData("本地兼容检查", "GluSpot", tuple(row.text for row in rows),
        ColorToken(146, 77, 109), ColorToken(38, 43, 76), (AudioFrame(.1, .2, .1),),
        album_art_bytes=bytes(encoded), duration_seconds=8.0, position_seconds=1.0,
        timed_lyrics=rows, track_id="owned:diagnostic:local", source_app="GluSpot",
        album_queue=(AlbumQueueEntry("本地兼容检查", "GluSpot", album_art_bytes=bytes(encoded),
            track_id="owned:diagnostic:local"),))
    data = IslandVisualData(track, (), ())

    class _DiagnosticSurfaceOwner(QWidget):
        def __init__(self):
            super().__init__()
            self.data_provider = EmptyIslandDataProvider(data)
            self.media_track_id = track.track_id
            self.track_title, self.artist = track.title, track.artist
            self.album_color = QColor(*track.album_color.rgba())
            self.secondary_color = QColor(*track.secondary_color.rgba())
            self.album_art = art
            self.lyrics = list(track.lyrics)
            self.timed_lyrics = [(row.time_seconds, row.text) for row in track.timed_lyrics]
            self.media_position_seconds = track.position_seconds
            self.media_is_playing = True
            self._music_windowed_preference = True

        def _estimated_media_position(self, _now):
            return self.media_position_seconds

        def _restore_island_from_music_surface(self):
            return None

    owner = _DiagnosticSurfaceOwner()
    surface = None
    entry = None
    frames = []
    try:
        entry = PlaybackEntryWindow(owned=False, system=True)
        require("first_use_owned_only", entry.selection == (True, False))
        require("first_use_system_choice_disabled", not entry.system_button.isEnabled())
        require("first_use_owned_choice_preserved", not entry.owned_button.isEnabled())
        require("first_use_hidden", not entry.isVisible())
        entry_frame = QImage(entry.size(), QImage.Format.Format_ARGB32_Premultiplied)
        entry_frame.fill(Qt.GlobalColor.transparent)
        entry.render(entry_frame)
        require("first_use_hidden_render", not entry_frame.isNull()
            and entry_frame.pixelColor(entry_frame.width() // 2, entry_frame.height() // 2).alpha() > 0)
        surface = MusicSurfaceWindow(owner)
        surface.resize(960, 540)
        for layout in ("cover", "cover_lyrics", "lyrics", "playlist"):
            surface.set_content_layout(layout, animate=False)
            image = QImage(surface.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            surface.render(image)
            require("hidden_surface_" + layout, not image.isNull()
                and image.pixelColor(image.width() // 2, image.height() // 2).alpha() > 0)
            frames.append({"layout": layout, "width": image.width(), "height": image.height(),
                "sha256": hashlib.sha256(bytes(image.constBits())).hexdigest()})
        require("music_surface_remains_hidden", not surface.isVisible() and not owner.isVisible())
        require("surface_layouts_distinct", len({frame["sha256"] for frame in frames}) == 4)
        app.processEvents()
        return {"entry_selection": [True, False], "entry_hidden": True,
            "music_surface_hidden": True, "frames": frames,
            "fixture_provider": "api.providers.EmptyIslandDataProvider"}
    finally:
        if surface is not None:
            surface.shutdown()
            surface.deleteLater()
        if entry is not None:
            entry.close()
            entry.deleteLater()
        owner.close()
        owner.deleteLater()
        app.processEvents()


def run_legacy_package_check(report_path):
    """Return 0 only when the actual legacy package passes every required check."""
    started = time.monotonic()
    report = {"schema": "gluspot-legacy-package-check-v1", "app": APP_NAME, "version": APP_VERSION,
        "started_utc": datetime.now(timezone.utc).isoformat(), "passed": False,
        "host": {"platform": sys.platform, "machine": platform.machine(),
            "process_bits": struct.calcsize("P") * 8, "python": platform.python_version(),
            "frozen": bool(getattr(sys, "frozen", False))},
        "checks": [], "errors": [], "qt_messages": [], "qt_callback_errors": [],
        "limits": ["Checks run on the reported host; this is not Windows 7 certification",
            "No online login, network request, system capture, headset gesture or receiver was tested",
            "Private Qt decoding is muted at volume zero; no Windows default endpoint was changed",
            "Only the requested report survives; test WAVs and lyric directories are temporary"]}

    def require(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise RuntimeError("Required package check failed: " + name)

    previous_exception_hook = sys.excepthook
    qt_message_handler = None
    app = None
    original_environment = {key: os.environ.get(key) for key in (
        "GLUSPOT_ALBUM_ART_CACHE_DIR", "APPDATA", "LOCALAPPDATA")}
    try:
        from local.runtime_platform import legacy_qt_runtime, windows_version
        report["host"]["windows_version"] = windows_version()
        require("python_38_runtime", sys.version_info[:2] == (3, 8))
        import PySide2
        from PySide6 import QtCore
        report["host"].update(pyside_version=PySide2.__version__, qt_version=QtCore.qVersion())
        require("pyside_515_runtime", PySide2.__version__ == "5.15.2.1")
        require("qt_515_runtime", QtCore.qVersion().startswith("5.15."))
        require("legacy_import_profile", legacy_qt_runtime())
        require("windows_native_platform", sys.platform == "win32")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([APP_NAME, "--legacy-package-check"])
        require("qt_windows_platform_plugin", app.platformName() == "windows")

        def qt_message(kind, context, text):
            report["qt_messages"].append({"type": int(kind), "message": str(text)[:1200]})

        def callback_error(type_, value, traceback):
            report["qt_callback_errors"].append(_brief_error(value))

        qt_message_handler = QtCore.qInstallMessageHandler(qt_message)
        sys.excepthook = callback_error
        with tempfile.TemporaryDirectory(prefix="GluSpot-legacy-package-check-") as temporary:
            directory = Path(temporary)
            os.environ.update(GLUSPOT_ALBUM_ART_CACHE_DIR=str(directory / "artwork"),
                APPDATA=str(directory / "appdata"), LOCALAPPDATA=str(directory / "localappdata"))
            initial_threads = {thread.ident for thread in threading.enumerate()}
            importlib.import_module("local.app")
            require("application_import_only", not any(thread.ident not in initial_threads
                for thread in threading.enumerate()))
            report["playback_policy"] = _policy_check(directory, require)
            report["media"] = _media_check(app, directory, require)
            report["owned_player"] = _owned_player_check(app, directory, require)
            report["surfaces"] = _surface_check(app, require)
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
            app.processEvents()
            require("system_sdk_modules_absent", not any(name == "winsdk" or name.startswith("winsdk.")
                for name in sys.modules))
            require("system_media_worker_threads_absent", not any(thread.name in {
                "WindowsMediaProvider", "GluSpotSMTC"} for thread in threading.enumerate()))
            require("qt_callbacks_without_exceptions", not report["qt_callback_errors"])
        report["passed"] = True
    except Exception as exc:
        report["errors"].append(_brief_error(exc))
    finally:
        sys.excepthook = previous_exception_hook
        if app is not None:
            try:
                from PySide6.QtCore import qInstallMessageHandler
                qInstallMessageHandler(qt_message_handler)
            except Exception as exc:
                report["errors"].append(_brief_error(exc))
                report["passed"] = False
        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    try:
        target = Path(report_path).expanduser().absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        if sys.stderr is not None:
            sys.stderr.write("Cannot write legacy package diagnostic: " + _brief_error(exc) + "\n")
        return 2
    return 0 if report["passed"] else 1
