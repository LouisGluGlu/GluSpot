from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorToken:
    red: int
    green: int
    blue: int
    alpha: int = 255

    def rgba(self) -> tuple[int, int, int, int]:
        return self.red, self.green, self.blue, self.alpha


@dataclass(frozen=True)
class AudioFrame:
    low: float
    mid: float
    high: float


@dataclass(frozen=True)
class TimedLyricLine:
    time_seconds: float
    text: str


@dataclass(frozen=True)
class TrackData:
    title: str
    artist: str
    lyrics: tuple[str, ...]
    album_color: ColorToken
    secondary_color: ColorToken
    audio_frames: tuple[AudioFrame, ...]
    album_art_bytes: bytes = b""
    source_app: str = ""
    is_playing: bool = True
    duration_seconds: float = 0.0
    position_seconds: float = 0.0
    timed_lyrics: tuple[TimedLyricLine, ...] = ()
    lyrics_source: str = ""
    track_id: str = ""
    position_source: str = "windows-media"
    position_confidence: float = 1.0
    source_app_foreground: bool = False
    position_interactive: bool = False


@dataclass(frozen=True)
class MessageData:
    app: str
    sender: str
    text: str
    name_color: ColorToken
    avatar_color: ColorToken
    avatar_image_bytes: bytes = b""


@dataclass(frozen=True)
class PluginData:
    label: str
    detail: str
    summary: str
    color: ColorToken
    icon: str
    plugin_id: str = ""
    status: str = "normal"
    primary_value: str = ""
    secondary_value: str = ""


@dataclass(frozen=True)
class IslandVisualData:
    track: TrackData
    messages: tuple[MessageData, ...]
    plugins: tuple[PluginData, ...]
