"""Original synthetic words from PR #21, adapted to real provider models."""
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QWidget

from api.models import TimedLyricLine, TimedLyricUnit
from local.music_surface_window import MusicSurfaceWindow


LINES = (
    TimedLyricLine(.4, "夜色把窗照得慢\nThe night keeps the window slow", tuple(
        TimedLyricUnit(start, end, text) for text, start, end in (
            ("夜", .4, .68), ("色", .68, .98), ("把", .98, 1.26),
            ("窗", 1.26, 1.58), ("照", 1.58, 1.90), ("得", 1.90, 2.20), ("慢", 2.20, 4.55))), "unit"),
    TimedLyricLine(3.8, "风从谱边走过去\nWind walks along the score", tuple(
        TimedLyricUnit(start, end, text) for text, start, end in (
            ("风", 3.8, 4.1), ("从", 4.1, 4.4), ("谱", 4.4, 4.8),
            ("边", 4.8, 5.15), ("走", 5.15, 5.7), ("过", 5.7, 6.05), ("去", 6.05, 6.45))), "unit"),
    TimedLyricLine(9.7, "桥下的灯一盏盏亮\nLights under the bridge come on"),
    TimedLyricLine(12.6, "把今天折进口袋\nI fold today into a pocket", tuple(
        TimedLyricUnit(start, end, text) for text, start, end in (
            ("把", 12.6, 12.9), ("今", 12.9, 13.15), ("天", 13.15, 13.5),
            ("折", 13.5, 13.85), ("进", 13.85, 14.15), ("口", 14.15, 14.55), ("袋", 14.55, 15.3))), "unit"),
)


class LyricOwner(QWidget):
    def __init__(self, enabled=None, position=3.35, lines=LINES):
        super().__init__()
        self.preferences = {} if enabled is None else {"v2_music_apple_lyrics_enabled": enabled}
        self.settings_getter = self.preferences.get
        self._music_windowed_preference = True
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.track_title, self.artist = "Lyric study", "GluSpot · original fixture"
        self.media_track_id = "apple-lyric-fixture"
        self.media_source_app = "fixture"
        self.media_position_source = "local-seek"
        self.media_position_sampled_at = 100.0
        self.media_position_seconds = position
        self.media_duration_seconds = 16.0
        self.media_is_playing = True
        self.timed_lyrics = [(line.time_seconds, line.text) for line in lines]
        self.timed_lyric_units = tuple(line.units for line in lines)
        self.timed_lyric_timing_kinds = tuple(line.timing_kind for line in lines)
        self.lyrics = [line.text for line in lines]
        self.album_color, self.secondary_color = QColor(75, 47, 43), QColor(25, 29, 48)
        self.seek_calls = []
        self.data_provider = self

    def seek_playback_position(self, seconds):
        self.seek_calls.append(seconds)
        return True

    def _run_background(self, _name, callback):
        callback()


def make_surface(enabled=None, position=3.35, lines=LINES, size=(1280, 720), fullscreen=False):
    owner = LyricOwner(enabled, position, lines)
    with patch("local.music_surface_window.time.monotonic", return_value=100.0):
        surface = MusicSurfaceWindow(owner)
        surface.resize(*size)
        surface._fullscreen = fullscreen
    return owner, surface


def render(surface, now=100.0, dpr=1.0):
    image = QImage(round(surface.width() * dpr), round(surface.height() * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(QColor(0, 0, 0))
    with patch("local.music_surface_window.time.monotonic", return_value=now):
        surface.render(image)
    return image
