"""Native music navigation/output previews with labelled offline UI fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.music_surface_window import MusicSurfaceWindow
from tools.preview_personal_fm_controls import native_image, physical_rect


class _FixtureProvider:
    def __init__(self):
        self.owned = True
        self.mode = "sequential"
        self.snapshot = AudioRouteSnapshot(state="ready", capture_scope="owned-player",
            active_id="fixture-local", devices=(AudioRouteDevice("fixture-local", "本地扬声器 · UI FIXTURE",
                kind="speaker", volume=.5),), status_message="选择程序内音乐的播放设备")

    def _owned_active(self):
        return self.owned

    def get_owned_playback_mode(self):
        return self.mode if self.owned else None

    def set_owned_playback_mode(self, mode):
        self.mode = mode
        return True

    def get_audio_route_snapshot(self):
        return self.snapshot

    def refresh_audio_outputs(self):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/music-navigation-preview"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        raise RuntimeError("Run this preview with the native Windows Qt platform.")
    owner = QWidget()
    owner.data_provider = _FixtureProvider()
    owner._music_library_back_callback = lambda: None
    owner.album_color, owner.secondary_color = QColor("#166581"), QColor("#532b45")
    cover = QImage(256, 256, QImage.Format.Format_RGB32)
    cover.fill(QColor("#166581"))
    with QPainter(cover) as painter:
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Segoe UI", 20))
        painter.drawText(cover.rect(), 0x84, "UI FIXTURE")
    owner.album_art = cover
    owner.track_title, owner.artist, owner.album_title = "音乐导航 · UI FIXTURE", "测试歌手", "布局预览"
    owner.media_track_id, owner.media_position_seconds, owner.media_is_playing = "fixture-song", 5.0, True
    owner.timed_lyrics = [(0.0, "这是一组用于布局检查的歌词"), (4.0, "这是正在显示的歌词"),
                          (8.0, "这是一组离线 UI FIXTURE"), (12.0, "不含账号和真实播放数据")]
    owner.lyrics = [text for _, text in owner.timed_lyrics]
    owner._estimated_media_position = lambda _: owner.media_position_seconds
    owner.music_flow_intensity = 0.0
    window = MusicSurfaceWindow(owner)
    window.resize(960, 540)
    window.show()
    window._timer.stop()
    window._audio_output_status_timer.stop()
    app.processEvents()
    proof = {"platform": app.platformName(), "offline_fixture_only": True,
             "dpr": window.devicePixelRatioF(), "library_frames": []}
    for fullscreen in (True, False):
        window._fullscreen = fullscreen
        window._reset_control_idle()
        window.grab().save(str(options.output / ("owned-fullscreen.png" if fullscreen else "owned-window.png")))
    for size in ((960, 540), (640, 360)):
        window.resize(*size)
        window._fullscreen = False
        frames = []
        for label, instant, expansion, reverse_at, reveal_at in (
            ("circle", 100, False, None, None),
            ("expanding", 100.12, True, None, None),
            ("capsule", 100.36, True, None, None),
            ("reverse-start", 100.18, True, 100.18, None),
            ("reversing", 100.3, True, 100.18, None),
            ("returned-circle", 100.54, True, 100.18, None),
            ("fading", 102.12, True, None, None),
            ("hidden", 102.6, True, None, None),
            ("revealing", 102.85, True, None, 102.6),
            ("revealed-circle", 103.2, True, None, 102.6),
        ):
            window._reset_control_idle(now=100)
            window._control_layer_next_prewarm_at = 0
            window._set_library_expanded(expansion, 100)
            if reverse_at is not None:
                window._set_library_expanded(False, reverse_at)
            if reveal_at is not None:
                with patch("local.music_surface_window.time.monotonic", return_value=reveal_at), \
                        patch.object(window, "isActiveWindow", return_value=True):
                    window._note_pointer_moved(reveal_at)
            # Fill the existing blur levels through the actual idle gate.
            if instant >= 102 and reveal_at is None:
                for step in range(20):
                    with patch("local.music_surface_window.time.monotonic", return_value=100.5 + step / 20):
                        window.grab()
            with patch("local.music_surface_window.time.monotonic", return_value=instant):
                frame = window.grab().toImage()
                face = window._button_rects.get("library")
                shape = window._library_button_face(QRectF(window.rect()))
                reveal = window._control_reveal_at(instant)
                fraction = window._library_expansion_at(instant)
                miss = not window._window_control_contains("library", shape, shape.topLeft() + QPointF(.5, .5))
            path = options.output / f"library-{size[0]}-{label}.png"
            frame.save(str(path))
            logical_crop = QRectF(0, 0, 150, 82)
            crop = frame.copy(physical_rect(logical_crop, proof["dpr"]))
            crop_path = options.output / f"library-{size[0]}-{label}-control.png"
            crop.save(str(crop_path))
            frames.append({"label": label, "instant": instant, "expansion": fraction,
                           "control_reveal": reveal, "clickable": face is not None,
                           "corner_misses": miss, "crop": crop,
                           "path": str(path.resolve()), "crop_path": str(crop_path.resolve())})
        sheet = native_image(750, 220, proof["dpr"])
        sheet.fill(QColor("#202d38"))
        with QPainter(sheet) as painter:
            painter.setFont(QFont("Segoe UI", 9))
            painter.setPen(QColor("#eeeeee"))
            for index, row in enumerate(frames):
                x, y = index % 5 * 150, index // 5 * 110
                painter.drawImage(QRectF(x, y, 150, 82), row["crop"])
                painter.drawText(QRectF(x, y + 82, 150, 23), Qt.AlignmentFlag.AlignCenter, row["label"])
        sheet_path = options.output / f"library-{size[0]}-comparison.png"
        sheet.save(str(sheet_path))
        proof["library_frames"].append({"size": size, "comparison": str(sheet_path.resolve()),
            "frames": [{key: value for key, value in row.items() if key != "crop"} for row in frames]})
    window.resize(960, 540)
    window._control_last_motion_at = time.monotonic() - 12
    window.grab().save(str(options.output / "owned-idle.png"))
    window._reset_control_idle()
    for mode in ("sequential", "shuffle", "repeat_one"):
        owner.data_provider.mode = mode
        window._sync_owned_playback_mode()
        window.grab().save(str(options.output / f"owned-mode-{mode}.png"))
    owner.data_provider.mode = "sequential"
    window._sync_owned_playback_mode()
    window._toggle_audio_output_panel()
    panel = window._audio_output_panel
    panel._reveal, panel._reveal_velocity, panel._appearance_elapsed = 1.0, 0.0, 1.0
    panel._apply_reveal_pose()
    panel._animation_timer.stop()
    panel._poll_timer.stop()
    app.processEvents()
    window.grab().save(str(options.output / "owned-output-default.png"))
    owner.data_provider.owned = False
    window._sync_owned_playback_mode()
    owner.data_provider.snapshot = AudioRouteSnapshot(
        devices=(AudioRouteDevice("fixture-local", "本地扬声器 · UI FIXTURE", kind="speaker", volume=.5),
                 AudioRouteDevice("fixture-airplay", "客厅 HomePod · UI FIXTURE", kind="homepod", transport="airplay")),
        active_id="fixture-local", state="error",
        status_message="UI FIXTURE · 连接失败，请检查设备访问设置和网络后重试。此长消息用于验证说明不会挤占设备列表。")
    panel.sync_snapshot()
    for row in panel._rows.values():
        row.progress = 1.0 if row.state == "active" else 0.0
        row.elapsed = 1.0
    panel._apply_reveal_pose()
    app.processEvents()
    window.grab().save(str(options.output / "output-error.png"))
    panel.dismiss(immediate=True)
    window._reset_control_idle()
    window.grab().save(str(options.output / "system-window.png"))
    owner.data_provider.owned = True
    window._sync_owned_playback_mode()
    window.resize(640, 360)
    window._reset_control_idle()
    app.processEvents()
    window.grab().save(str(options.output / "owned-small-window.png"))
    window.shutdown()
    owner.close()
    (options.output / "evidence.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
    print(options.output.resolve())


if __name__ == "__main__":
    main()
