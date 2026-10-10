"""Native music navigation/output previews with labelled offline UI fixtures."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.music_surface_window import MusicSurfaceWindow


class _FixtureProvider:
    def __init__(self):
        self.owned = True
        self.mode = "sequential"
        self.snapshot = AudioRouteSnapshot(state="unavailable", capture_scope="owned-player",
            status_message="程序内播放使用系统默认输出；暂不支持同步播放到多个设备")

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
    window = MusicSurfaceWindow(owner)
    window.resize(960, 540)
    window.show()
    window._timer.stop()
    app.processEvents()
    for fullscreen in (True, False):
        window._fullscreen = fullscreen
        window._reset_control_idle()
        window.grab().save(str(options.output / ("owned-fullscreen.png" if fullscreen else "owned-window.png")))
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
    print(options.output.resolve())


if __name__ == "__main__":
    main()
