"""Isolated real-provider narrow window; never loads or saves account settings."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def run_live(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netease-relaunch", action="store_true",
                        help="Explicitly enable the existing NetEase capture/relaunch path")
    parser.add_argument("--grab", type=Path, help="Save idle/revealed frames only after real playback arrives")
    parser.add_argument("--seconds", type=float)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root), str(Path(__file__).parent)]
    # Use the existing cache overrides. No SettingsManager or user's settings
    # files are opened; the provider receives an in-memory settings getter.
    with tempfile.TemporaryDirectory(prefix="gluspot-narrow-live-") as runtime:
        previous = {name: os.environ.get(name) for name in
                    ("GLUSPOT_LYRICS_CACHE_DIR", "GLUSPOT_ALBUM_ART_CACHE_DIR")}
        os.environ["GLUSPOT_LYRICS_CACHE_DIR"] = str(Path(runtime) / "lyrics")
        os.environ["GLUSPOT_ALBUM_ART_CACHE_DIR"] = str(Path(runtime) / "artwork")
        try:
            return _run(args)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _run(args):
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication
    from api.providers import WindowsMediaProvider
    from local.island_stack_visual import IslandStackVisual
    from narrow_window import NarrowStudyWindow

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("GluSpot")
    settings = {
        "v2_system_capture_enabled": True,
        "v2_owned_playback_enabled": False,
        "v2_capture_position_enabled": args.netease_relaunch,
        "netease.cef.enabled": args.netease_relaunch,
        "netease.transport": "relaunch" if args.netease_relaunch else "off",
        "v2_music_glass_level": 50,
        "v2_enable_live_audio_analysis": False,
        "v2_music_reactive_background_enabled": False,
    }
    getter = lambda key, default=None: settings.get(key, default)
    provider = WindowsMediaProvider(settings_getter=getter)
    owner = IslandStackVisual(data_provider=provider)
    owner.settings_getter = getter
    owner.timer.stop()
    window = NarrowStudyWindow(owner, "B", "left", live=True)
    window.hints = False
    window._timer.stop()
    window._audio_output_status_timer.stop()
    window.show()
    outcome = {"exit_code": 0}
    started = time.monotonic()
    evidence = []

    def save_frame(name):
        path = args.grab / name
        window.repaint()
        if not window.grab().save(str(path)):
            outcome["exit_code"] = 1
            app.quit()
            return
        evidence.append({
            "path": str(path), "track_id": owner.media_track_id,
            "title": owner.track_title, "artist": owner.artist,
            "position_seconds": owner._estimated_media_position(time.monotonic()),
            "position_source": owner.media_position_source,
            "position_sampled_at": owner.media_position_sampled_at,
            "timed_lyric_rows": len(owner.timed_lyrics),
            "controls_opacity": window.visibility.footer.value(time.monotonic()),
        })
        print("saved", path, flush=True)

    def reveal():
        from PySide6.QtTest import QTest
        QTest.mouseMove(window, QPoint(window.width() // 2, window.height() // 2))
        QTimer.singleShot(350, finish_grab)

    def finish_grab():
        save_frame("narrow_B_mouse_moved.png")
        report = {"source": "live WindowsMediaProvider", "platform": sys.platform,
                  "capture_relaunch_enabled": args.netease_relaunch,
                  "render": "native Qt window grab", "frames": evidence}
        (args.grab / "live-evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        app.quit()

    def idle_grab():
        save_frame("narrow_A_idle.png")
        reveal()

    def await_playback():
        # A dependency/empty fallback is not a real playback screenshot.
        ready = (owner.media_is_playing and owner.media_duration_seconds > 0
                 and owner.media_source_app not in {"", "dependency", "system"}
                 and owner.media_position_source != "none"
                 and bool(window.source_lines()))
        if ready and (not args.netease_relaunch or owner.media_position_source == "capture-model"):
            wait.stop()
            QCursor.setPos(window.mapToGlobal(QPoint(-40, -40)))
            window._pointer_active_until = 0.0
            window.shelf = window.footer_hover = False
            QTimer.singleShot(1700, idle_grab)
        elif time.monotonic() - started >= 40:
            print("No real playback snapshot arrived; screenshots were not generated.", file=sys.stderr)
            outcome["exit_code"] = 2
            app.quit()

    wait = QTimer(window)
    if args.grab:
        args.grab.mkdir(parents=True, exist_ok=True)
        wait.timeout.connect(await_playback)
        wait.start(100)
    if args.seconds:
        QTimer.singleShot(round(args.seconds * 1000), app.quit)
    try:
        app.exec()
    finally:
        window.study_timer.stop()
        window.shutdown()
        owner.close()
        provider.close_audio_outputs()
    return outcome["exit_code"]


if __name__ == "__main__":
    raise SystemExit(run_live())
