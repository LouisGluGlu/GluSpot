"""Real Qt account-library/playback/surface smoke with isolated local fixtures.

Run with the project's PySide6 environment. Screenshots, settings, generated
PCM and reports stay under ignored .runtime. No real account, external network,
system capture, global volume or startup hook is used. The only audio output
belongs to this QMediaPlayer and is set to zero before playback starts.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QRect, QTimer, qVersion
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import AccountSnapshot, CatalogPage, LyricPayload, MusicSource, OnlinePlaylist, OnlineSong, StreamInfo
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider
from local.island_stack_visual import IslandStackVisual
from local.music_library_controller import MusicLibraryController
from local.music_surface_window import MusicSurfaceWindow
from local.music_tasks import MusicTasks
from local.owned_music_player import OwnedMusicPlayer
from local.settings_store import SettingsManager
from local.ui_theme import set_theme_settings_getter
from tools.smoke_owned_playback import _wav_bytes


def _settings_scope(stack: ExitStack, output: Path):
    folder = output / "settings"
    stack.enter_context(patch("local.settings_store.APP_DATA_DIR", str(folder)))
    stack.enter_context(patch("local.settings_store.SETTINGS_FILE", str(folder / "settings.json")))
    stack.enter_context(patch("local.settings_store.legacy_app_data_dirs", return_value=()))


def _configure(settings):
    for key, value in {
        "v2_owned_playback_enabled": True, "v2_system_capture_enabled": False,
        "v2_playback_onboarding_completed": True, "v2_startup_mode": "none",
        "v2_online_music_source": "netease", "v2_super_topmost": False,
        "v2_location_mode": "free", "v2_enabled_plugins": [],
    }.items():
        settings.set(key, value)
    settings.save_now()


class FixtureSystem(EmptyIslandDataProvider):
    """An empty source injected through the established provider boundary."""
    def __init__(self, output, **_kwargs):
        super().__init__()
        self.message_detector = SimpleNamespace(messages=lambda: ())
        self.plugin_provider = SimpleNamespace(plugins=lambda: ())
        self.lyrics_store = LocalLyricsStore(root=output / "lyrics")
        self.suspensions = 0
        self.closed = False
        self.started = False
        self.refresh_requests = 0

    def suspend_live_audio(self):
        self.suspensions += 1

    def has_live_audio(self):
        return False

    def _request_immediate_media_refresh(self):
        self.refresh_requests += 1

    def start_audio_outputs(self):
        self.started = True

    def close_audio_outputs(self):
        self.closed = True


class FixtureService:
    def __init__(self, loopback_url=""):
        self.url = loopback_url
        self.main_thread = threading.get_ident()
        self.calls = []
        self.closed = False
        self.song = OnlineSong("netease", "fixture-song", "真实 Qt 时钟", "合成音频", duration_seconds=6)
        self.playlist_song = OnlineSong("netease", "fixture-playlist-song", "窗口模式联调", "合成音频", duration_seconds=6)

    def _record(self, operation, **details):
        self.calls.append({"operation": operation, "worker_thread": threading.get_ident() != self.main_thread, **details})

    def list_sources(self):
        self._record("sources")
        return (MusicSource("netease", "网易云音乐", True),)

    def has_login_record(self, _source):
        self._record("login_record")
        return False

    def account_status(self, source, refresh=False):
        self._record("account", refresh=refresh)
        return AccountSnapshot(source, True, "fixture-user", "合成账号", state="verified")

    def playlists(self, source, limit=50, offset=0):
        self._record("playlists", offset=offset, limit=limit)
        item = OnlinePlaylist(source, "fixture-list" if offset == 0 else "fixture-list-2", "本机音频歌单" if offset == 0 else "第二页歌单", track_count=1)
        return CatalogPage((item,), 50 if offset == 0 else 100, offset == 0, 2)

    def search(self, source, query, *, kind="songs", limit=30, offset=0):
        self._record("search", kind=kind, offset=offset, limit=limit)
        if kind == "songs":
            song = self.song if offset == 0 else OnlineSong(source, "fixture-search-2", "搜索第二页", "合成音频", duration_seconds=6)
            return CatalogPage((song,), 30 if offset == 0 else 60, offset == 0, 2)
        return CatalogPage((OnlinePlaylist(source, "fixture-search-list", "搜索歌单", track_count=1),), 30, False, 1)

    def playlist_tracks(self, source, _playlist, limit=100, offset=0):
        self._record("playlist_tracks", offset=offset, limit=limit)
        # Follow a real cursor even when the first page has no playable rows.
        return CatalogPage((), 100, True, 1) if offset == 0 else CatalogPage((self.playlist_song,), 200, False, 1)

    def daily_recommendations(self, source):
        self._record("daily", source=source)
        return (self.song,)

    def personal_fm(self, source):
        self._record("fm", source=source)
        return (self.playlist_song,)

    def stream_url(self, source, song_id):
        self._record("stream")
        return StreamInfo(source, song_id, self.url, True, quality="fixture-pcm")

    def lyrics(self, source, song_id):
        self._record("lyrics")
        return LyricPayload(source, song_id,
            raw_lrc="[00:00.00]本机音频联调\n[00:01.00]歌词跟随真实时钟\n[00:02.00]封面入口已交接\n[00:03.00]页面交接完成\n[00:04.00]窗口读取同一来源\n[00:05.00]播放到达结尾",
            translation="[00:00.00]Local audio\n[00:01.00]Real playback clock\n[00:02.00]Cover handoff\n[00:03.00]Surface ready\n[00:04.00]Shared source\n[00:05.00]Playlist loop",
            source="local-http-fixture")

    def close(self):
        self.closed = True


def _wait(predicate, *, timeout=10):
    if predicate():
        return True
    loop, timer = QEventLoop(), QTimer()
    deadline = time.monotonic() + timeout
    timer.setInterval(20)
    def poll():
        if predicate() or time.monotonic() >= deadline:
            loop.quit()
    timer.timeout.connect(poll)
    timer.start()
    loop.exec()
    timer.stop()
    return bool(predicate())


def _loopback(output):
    # Reuse the real decode smoke's PCM and repeat it to allow UI transitions.
    with wave.open(io.BytesIO(_wav_bytes()), "rb") as original:
        params, frames = original.getparams(), original.readframes(original.getnframes())
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams(params)
        stream.writeframes(frames * 3)
    payload = buffer.getvalue()
    (output / "fixture.wav").write_bytes(payload)
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            if self.path != "/fixture.wav":
                self.send_error(404)
                return
            low, high, partial = 0, len(payload) - 1, False
            header = self.headers.get("Range", "")
            if header.startswith("bytes="):
                try:
                    parts = header[6:].split("-", 1)
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
            requests.append({"bytes": len(body), "partial": partial})
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
    thread = threading.Thread(target=server.serve_forever, name="GluSpotLibraryFixture", daemon=True)
    thread.start()
    return server, thread, requests


def run_library(output):
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    checks, errors, snapshots = {}, [], []
    server, server_thread, requests = _loopback(output)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = FixtureService(f"http://127.0.0.1:{server.server_port}/fixture.wav")
    with ExitStack() as stack:
        _settings_scope(stack, output)
        settings = SettingsManager()
        _configure(settings)
        set_theme_settings_getter(settings.get)
        IslandStackVisual.load_embedded_lyric_fonts()
        system = FixtureSystem(output)
        island = None
        def runtime_setting(key, default=None):
            if key == "v2_enable_dual_line_lyrics":
                return bool(getattr(island, "enable_dual_line_lyrics", False))
            return settings.get(key, default)
        system.lyrics_store.settings_getter = runtime_setting
        audio = QAudioOutput()
        audio.setVolume(0)
        owned = OwnedMusicPlayer(service, system.lyrics_store, audio=audio)
        provider = PlaybackSourceProvider(system, owned, runtime_setting)
        provider.settings_setter = settings.set
        island = IslandStackVisual(data_provider=provider)
        island.settings_getter, island.settings_setter = runtime_setting, settings.set
        island.set_super_topmost(False, notify=False)
        island._music_windowed_preference = False
        entry = SimpleNamespace(_show_island=island.show, show_login=lambda *_args: None)
        controller = MusicLibraryController(settings, service, owned, island, entry)
        island._music_library_back_callback = controller.return_from_music_surface
        owned.changed.connect(lambda: island._apply_visual_data(provider.get_visual_data()))
        owned.player.errorOccurred.connect(lambda code, _message: errors.append(code.name))
        window = controller.window
        positions = []
        owned.player.positionChanged.connect(positions.append)

        def check(name, passed):
            checks[name] = bool(passed)
            if not passed:
                raise AssertionError(name)

        def screenshot(widget, name):
            path = output / name
            check(f"screenshot_{name}", widget.grab().save(str(path)))
            snapshots.append(str(path))

        def query(value):
            window.search.blockSignals(True)
            window.search.setText(value)
            window.search.blockSignals(False)
            controller.search(value)

        def row(key):
            return next(item for item in window._rows if item.collection.key == key)

        def activate(key, mode):
            item = row(key)
            window.scroll.ensureWidgetVisible(item)
            app.processEvents()
            image = QImage(256, 256, QImage.Format.Format_RGB32)
            image.fill(QColor("#16BCFF"))
            window.set_artwork(key, image)
            local = item.cover.artwork_rect().toAlignedRect()
            origin = QRect(item.cover.mapToGlobal(local.topLeft()), local.size())
            previous_track = owned.track
            item.cover.click() if mode == "default" else item.mode_buttons[mode].click()
            check(f"{mode}_surface_visible", _wait(lambda: controller._opening is None and owned.track is not None and owned.track is not previous_track and island._music_surface_active and island.music_fullscreen_overlay is not None and island.music_fullscreen_overlay.isVisible()))
            surface = island.music_fullscreen_overlay
            check(f"{mode}_real_surface", type(surface) is MusicSurfaceWindow)
            check(f"{mode}_cover_origin", surface._island_origin_geometry == origin)
            check(f"{mode}_cover_snapshot", not surface._island_origin_snapshot.isNull())
            check(f"{mode}_mode_settled", _wait(lambda: surface._pending_fullscreen is None))
            check(f"{mode}_presentation", surface._fullscreen == (mode == "default"))
            check(f"{mode}_qt_clock", _wait(lambda: owned.player.position() >= 250 and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState))
            check(f"{mode}_timed_lyrics", _wait(lambda: owned.track is not None and len(owned.track.timed_lyrics) == 6))
            check(f"{mode}_provider_clock", provider.get_visual_data().track.position_source == "owned-player" and island.media_track_id == owned.track.track_id)
            check(f"{mode}_terminal_artwork", owned.track.album_art_lookup_state == "miss" and not surface._artwork_is_loading())
            check(f"{mode}_loading_released", _wait(lambda: not surface._art_loading_waiting and not surface._lyric_loading_waiting and not surface._lyric_loading_visible(time.monotonic())))
            check(f"{mode}_surface_lyrics", _wait(lambda: surface._has_timed_lyrics and len(surface._display_lyrics()[1]) == 6 and not surface._no_lyrics_cover_target()))
            screenshot(surface, f"surface-{mode}.png")
            return surface

        try:
            controller.open_source("netease")
            check("account_catalog_loaded", _wait(lambda: not controller._pending and len(window.collections) >= 3))
            check("synthetic_account_visible", window._account.logged_in and window._account.nickname == "合成账号")
            controller.load_more()
            check("catalog_cursor_page2", _wait(lambda: not controller._pending and any(item.key == "playlist:netease:fixture-list-2" for item in window.collections)))
            check("daily_fm_present", {"daily", "fm"}.issubset({item.kind for item in window.collections}))
            screenshot(window, "library-catalog.png")
            query("fixture")
            check("search_songs_and_playlists", _wait(lambda: not controller._pending and {"song", "playlist"}.issubset({item.kind for item in window.collections})))
            controller.load_more()
            check("search_cursor_page2", _wait(lambda: not controller._pending and any(item.key == "song:netease:fixture-search-2" for item in window.collections)))
            screenshot(window, "library-search.png")
            surface = activate("song:netease:fixture-song", "default")
            retained_epoch = owned._epoch
            for mode in ("shuffle", "repeat_one", "sequential"):
                check(f"song_mode_{mode}", surface._cycle_owned_playback_mode()
                      and provider.get_owned_playback_mode() == mode)
            check("song_mode_saved_without_restart", settings.get("v2_owned_playback_mode") == "sequential"
                  and owned._epoch == retained_epoch)
            requests_before = sum(call["operation"] == "stream" for call in service.calls)
            lyric_requests_before = sum(call["operation"] == "lyrics" for call in service.calls)
            position_before = owned.player.position()
            surface._toggle_translation()
            check("current_song_translation_on", _wait(lambda: "Local audio" in "\n".join(owned.track.lyrics)))
            check("current_song_translation_clock_retained", owned._epoch == retained_epoch
                  and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                  and owned.player.position() >= position_before)
            surface._toggle_translation()
            check("current_song_translation_off", _wait(lambda: "Local audio" not in "\n".join(owned.track.lyrics)))
            check("translation_no_stream_reload", sum(call["operation"] == "stream" for call in service.calls) == requests_before)
            check("translation_uses_retained_payload", sum(call["operation"] == "lyrics" for call in service.calls) == lyric_requests_before)
            check("translation_off_keeps_playing", owned._epoch == retained_epoch
                  and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                  and owned.player.position() >= position_before)
            check("translation_default_stays_fixed", not settings.get("v2_enable_dual_line_lyrics"))
            retained_collections = window.collections
            retained_track = owned.track.track_id
            check("song_back_available", surface._library_back_available())
            check("song_back_library_visible", surface.return_to_library() and _wait(window.isVisible))
            check("song_back_preserves_search", window.search.text() == "fixture" and window.collections is retained_collections)
            check("song_back_preserves_playback", owned.track.track_id == retained_track
                  and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
            check("provider_pause", provider.send_playback_command("toggle_play_pause") and _wait(lambda: owned.player.playbackState() == QMediaPlayer.PlaybackState.PausedState))
            check("provider_seek", provider.seek_playback_position(1, owned.track.track_id) and _wait(lambda: owned.player.position() >= 950))
            controller.open_source("netease")
            check("library_reopened", _wait(lambda: not controller._pending and len(window.collections) >= 3))
            activate("playlist:netease:fixture-list", "window")
            check("playlist_cursor_followed", [call["offset"] for call in service.calls if call["operation"] == "playlist_tracks"] == [0, 100])
            check("playlist_new_track", owned.track.track_id == "owned:netease:fixture-playlist-song")
            playlist_epoch = owned._epoch
            check("qt_eof_loops_playlist", _wait(lambda: owned._epoch > playlist_epoch
                  and owned.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState, timeout=9))
            check("qt_clock_advanced", max(positions, default=0) >= 5500)
            check("provider_capture_suspended", system.suspensions > 0 and not system.started and not provider.has_live_audio())
            check("owned_output_route", provider.get_audio_route_snapshot().capture_scope == "owned-player")
            check("real_qt_and_workers", type(owned.player) is QMediaPlayer and type(owned.tasks) is MusicTasks and type(controller.tasks) is MusicTasks)
            check("all_service_io_on_workers", bool(service.calls) and all(call["worker_thread"] for call in service.calls))
            check("loopback_requested", bool(requests))
            check("private_audio_volume_zero", audio.volume() == 0)
            check("no_qt_errors", not errors)
        except Exception as exc:
            checks["completed_all_stages"] = False
            errors.append(type(exc).__name__ + ":" + str(exc))
        finally:
            controller.close()
            provider.close_audio_outputs()
            island.close()
            settings.save_now()
            set_theme_settings_getter(None)
            app.processEvents()
            checks["closed_tasks_and_provider"] = bool(controller.tasks._closed and controller.art_tasks._closed and owned.tasks._closed and owned.artwork_tasks._closed and system.closed)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
        report = {
            "passed": all(checks.values()), "checks": checks, "errors": errors,
            "qt_version": qVersion(), "elapsed_seconds": round(time.monotonic() - started, 3),
            "service_calls": service.calls, "http_requests": requests, "snapshots": snapshots,
            "scope": "Synthetic immutable account/catalog fixtures, actual MusicTasks, QMediaPlayer, PlaybackSourceProvider, library and existing V2 surfaces. Settings and lyrics isolated under output. Only own QAudioOutput volume set to zero.",
            "limits": "No real account authorization, remote artwork, audible sound, AirPlay/direct PCM routing, acoustic synchronization or sustained performance proof.",
        }
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_constructor(output):
    """Exercise the real application constructor/start/close in a fresh process."""
    from local.app import LocalIslandApp
    output.mkdir(parents=True, exist_ok=True)
    checks, errors = {}, []
    service = FixtureService()
    system = FixtureSystem(output)
    def settings_factory():
        settings = SettingsManager()
        _configure(settings)
        return settings
    def silent_player(*args, **kwargs):
        audio = QAudioOutput()
        audio.setVolume(0)
        return OwnedMusicPlayer(*args, audio=audio, **kwargs)

    def system_factory(**kwargs):
        system.lyrics_store.settings_getter = kwargs["settings_getter"]
        return system
    with ExitStack() as stack:
        _settings_scope(stack, output)
        stack.enter_context(patch("local.app.SettingsManager", side_effect=settings_factory))
        stack.enter_context(patch("local.app.WindowsMediaProvider", side_effect=system_factory))
        stack.enter_context(patch("local.app.make_online_music_service", return_value=service))
        stack.enter_context(patch("local.app.OwnedMusicPlayer", side_effect=silent_player))
        stack.enter_context(patch("local.app.MainThreadWatchdog", return_value=None))
        stack.enter_context(patch.object(LocalIslandApp, "_setup_tray"))
        stack.enter_context(patch.object(LocalIslandApp, "_setup_mydockfinder_listener"))
        stack.enter_context(patch("local.app._mydockfinder_process_diagnostics", return_value={"running": False}))
        instance = LocalIslandApp()
        checks["actual_constructor"] = type(instance) is LocalIslandApp
        checks["actual_controllers"] = type(instance.music_library) is MusicLibraryController
        checks["retired_notice_not_constructed"] = not hasattr(instance, "audio_device_notice") and not hasattr(instance, "audio_device_notice_timer")
        checks["plugins_inactive"] = instance.window.enabled_plugin_count == 0 and not instance.window.persistent_plugin
        checks["fixed_default_typography"] = instance.window.lyric_font_size == 18 and instance.window.wave_font_weight == "bold"
        instance.window.set_enable_dual_line_lyrics(True)
        checks["session_translation_reaches_parser"] = system.lyrics_store._dual_line_lyrics_enabled() and instance.window.settings_getter("v2_enable_dual_line_lyrics")
        checks["session_translation_does_not_change_default"] = not instance.settings.get("v2_enable_dual_line_lyrics")
        instance.window.set_enable_dual_line_lyrics(False)
        checks["idle_translation_refreshes_system"] = system.refresh_requests == 2
        checks["own_volume_zero"] = instance.owned_player.audio.volume() == 0
        timer = QTimer(instance.app)
        timer.setInterval(50)
        deadline = time.monotonic() + 8
        def finish():
            ready = instance.playback_entry.window.isVisible() and bool(instance.playback_entry._sources)
            if ready or time.monotonic() >= deadline:
                checks["actual_start_login_route"] = ready
                checks["entry_screenshot"] = instance.playback_entry.window.grab().save(str(output / "app-entry.png"))
                timer.stop()
                instance.quit()
        timer.timeout.connect(finish)
        timer.start()
        instance.run()
        checks["actual_close_hooks"] = bool(service.closed and system.closed and instance.music_library._closed and instance.playback_entry._closed and instance.owned_player._closed)
        checks["capture_never_started"] = not system.started
        checks["isolated_settings_saved"] = (output / "settings/settings.json").is_file()
        set_theme_settings_getter(None)
    report = {"passed": all(checks.values()), "checks": checks, "errors": errors,
              "scope": "Real LocalIslandApp constructor, saved-settings application, entry startup and aboutToQuit cleanup. Synthetic service/provider, isolated settings, own muted player; tray, watchdog and external process listener disabled.",
              "limits": "Does not exercise real system provider, user settings, startup hooks, service credentials, or account authorization."}
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/music-library-smoke")
    parser.add_argument("--app-constructor", action="store_true")
    options = parser.parse_args()
    output = options.output.resolve()
    report = run_constructor(output) if options.app_constructor else run_library(output)
    if not options.app_constructor:
        child_output = output / "app-constructor"
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--app-constructor", "--output", str(child_output)], cwd=ROOT, timeout=25, capture_output=True, text=True, encoding="utf-8", errors="replace")
        child_result = child_output / "result.json"
        report["app_constructor"] = json.loads(child_result.read_text(encoding="utf-8")) if child_result.is_file() else {"passed": False, "exit_code": child.returncode, "diagnostic": child.stderr[-1600:]}
        report["passed"] = report["passed"] and child.returncode == 0 and report["app_constructor"]["passed"]
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "errors": report["errors"], "app_constructor": report.get("app_constructor"), "result": str(output / "result.json")}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
