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
class TimedLyricUnit:
    start_seconds: float
    # None means the source did not provide an end timestamp. Callers may
    # estimate display progress, but must not treat that estimate as evidence
    # of a sustained syllable for highlight selection.
    end_seconds: float | None
    text: str


@dataclass(frozen=True)
class TimedLyricLine:
    time_seconds: float
    text: str
    units: tuple[TimedLyricUnit, ...] = ()
    timing_kind: str = "line"


@dataclass(frozen=True)
class AlbumQueueEntry:
    title: str
    artist: str = ""
    album_title: str = ""
    album_art_bytes: bytes = b""
    track_id: str = ""
    album_color: ColorToken | None = None
    secondary_color: ColorToken | None = None


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
    album_queue: tuple[AlbumQueueEntry, ...] = ()
    lyric_offset_seconds: float = 0.0
    # Provider-selected adapter owning the per-player lyric offset. The UI
    # uses this identity only to apply a slider change to the current song.
    lyric_offset_adapter: str = "system"
    # The media provider is the authority for whether artwork came from an
    # original-art source or the Windows media session's thumbnail.
    album_art_source: str = ""
    # Track-bound acquisition state. A failed attempt retains the preview;
    # only a decoded original replacing that preview ends the visual wait.
    album_art_lookup_state: str = ""
    # Original player progress observation or verified local Seek event time.
    # Projection and visual polls keep this unchanged so a cached read or
    # already-handled Seek is not mistaken for new clock evidence.
    position_sampled_at: float = 0.0
    # Original source position paired with position_sampled_at, before delivery
    # or cached-frame projection. None means this source has no observation.
    # A verified local Seek retains its initial target, not projected playback.
    position_observed_seconds: float | None = None


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
    alert_title: str = ""
    alert_body: str = ""
    alert_priority: int = 50
    progress: float = 0.0


@dataclass(frozen=True)
class IslandVisualData:
    track: TrackData
    messages: tuple[MessageData, ...]
    plugins: tuple[PluginData, ...]
