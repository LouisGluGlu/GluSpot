from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from api.models import TimedLyricLine


@dataclass(frozen=True)
class NeteasePositionSample:
    position_seconds: float
    duration_seconds: float
    source: str
    confidence: float
    lyric_index: int = -1
    lyric_text: str = ""
    sampled_at: float = 0.0
    is_playing: bool | None = None


@dataclass(frozen=True)
class QueueTrack:
    title: str
    artist: str = ""
    album_title: str = ""
    duration_seconds: float = 0.0
    album_art_url: str = ""


class NeteasePositionReader:
    def __init__(
        self,
        service_name: str = "netease",
        *,
        window_hints: Sequence[str] | None = None,
        queue_paths: Sequence[Path] | None = None,
        anchor_fraction: float = 0.46,
        anchor_cap_seconds: float = 1.65,
        tail_anchor_seconds: float = 1.15,
        line_end_margin_seconds: float = 0.08,
        min_read_interval: float = 0.55,
    ):
        self._service_name = str(service_name or "netease").strip().casefold() or "netease"
        self._source_prefix = self._service_name
        hints = window_hints or ("cloudmusic", "orpheus")
        self._window_hints = tuple(
            str(hint or "").strip().casefold()
            for hint in hints
            if str(hint or "").strip()
        )
        if queue_paths is None and self._service_name == "netease":
            root = Path.home() / "AppData" / "Local" / "NetEase" / "CloudMusic" / "webdata" / "file"
            self._queue_files = (root / "playingList", root / "fmPlay")
        else:
            self._queue_files = tuple(Path(path) for path in (queue_paths or ()))
        # Queue metadata, adjacent lyrics, and upcoming artwork are read on
        # the same media refresh. Reuse one parsed snapshot until the player
        # changes the file, so the first frame of the next track does not
        # parse the playlist repeatedly.
        self._queue_payload_cache: dict[Path, tuple[tuple[int, int, int, int, int], object]] = {}
        self._queue_payload_lock = threading.Lock()
        self._anchor_fraction = float(anchor_fraction)
        self._anchor_cap_seconds = float(anchor_cap_seconds)
        self._tail_anchor_seconds = float(tail_anchor_seconds)
        self._line_end_margin_seconds = float(line_end_margin_seconds)
        self._last_sample: NeteasePositionSample | None = None
        self._last_read_at = 0.0
        self._last_error = ""
        self._last_progress_probe: dict[str, object] = {}
        self._last_physical_seek_attempted = False
        self._pywinauto_available: bool | None = None
        self._last_track_key = ""
        self._last_track_duration = 0.0
        self._last_lyric_signature: tuple[object, ...] = ()
        self._last_lyric_source_rows: tuple[tuple[float, str], ...] = ()
        self._lock = threading.Lock()
        self._refreshing = False
        self._refresh_token = 0
        self._min_read_interval = float(min_read_interval)
        self._pending_lyric_index = -1
        self._pending_lyric_index_since = 0.0
        self._pending_lyric_index_last_seen = 0.0
        self._last_ui_playing: bool | None = None

    def read(
        self,
        title: str,
        artist: str,
        timed_lyrics: Sequence[TimedLyricLine],
        duration_seconds: float,
        interactive: bool = False,
    ) -> NeteasePositionSample | None:
        now = time.monotonic()
        track_key = f"{self._normalize_text(title)}\0{self._normalize_text(artist)}"
        duration = max(0.0, float(duration_seconds or 0.0))
        lyric_signature = self._lyric_signature(timed_lyrics)
        source_rows = tuple(
            (float(line.time_seconds), self._normalize_text(str(line.text).splitlines()[0] if str(line.text).splitlines() else ""))
            for line in timed_lyrics
        )
        with self._lock:
            previous_duration = self._last_track_duration
            duration_changed = (
                duration > 0.0
                and previous_duration > 0.0
                and abs(duration - previous_duration) > max(5.0, max(duration, previous_duration) * 0.04)
            )
            timeline_changed = bool(self._last_lyric_signature and lyric_signature != self._last_lyric_signature)
            enrichment = timeline_changed and self._same_primary_source_timeline(self._last_lyric_source_rows, source_rows)
            if (
                track_key != self._last_track_key
                or duration_changed
                or (timeline_changed and not enrichment)
            ):
                self._last_track_key = track_key
                self._last_sample = None
                self._last_read_at = 0.0
                self._refreshing = False
                # Preserve a monotonically increasing generation across an
                # A -> B -> A track sequence. Otherwise an old A reader can
                # collide with the new A generation and overwrite its sample.
                self._refresh_token += 1
                self._pending_lyric_index = -1
                self._pending_lyric_index_since = 0.0
                self._pending_lyric_index_last_seen = 0.0
                self._last_ui_playing = None
            elif enrichment:
                # Companion rows may change the raw source indexes, but are
                # not a new player observation. Keep the validated clock and
                # its pending evidence; invalidate only the old UIA scan.
                if self._last_sample is not None and self._last_sample.lyric_index >= 0:
                    remapped = self._remap_source_index(self._last_sample.lyric_index, self._last_lyric_source_rows, source_rows)
                    self._last_sample = replace(self._last_sample, lyric_index=remapped)
                if self._pending_lyric_index >= 0:
                    self._pending_lyric_index = self._remap_source_index(self._pending_lyric_index, self._last_lyric_source_rows, source_rows)
                self._refresh_token += 1
                self._refreshing = False
                self._last_read_at = 0.0
            if duration > 0.0:
                self._last_track_duration = duration
            self._last_lyric_signature = lyric_signature
            self._last_lyric_source_rows = source_rows

            sample = self._last_sample
            min_interval = 0.25 if interactive else self._min_read_interval
            due = now - self._last_read_at >= min_interval
            should_refresh = due and not self._refreshing and bool(timed_lyrics) and bool(title)
            if should_refresh:
                self._last_read_at = now
                self._refreshing = True
                self._refresh_token += 1
                refresh_token = self._refresh_token
                thread = threading.Thread(
                    target=self._refresh_sample,
                    args=(
                        track_key,
                        title,
                        artist,
                        tuple(timed_lyrics),
                        duration,
                        bool(interactive),
                        refresh_token,
                    ),
                    name=f"{self._service_name.title()}PositionReader",
                    daemon=True,
                )
                thread.start()
            return sample

    @staticmethod
    def _lyric_signature(timed_lyrics: Sequence[TimedLyricLine]) -> tuple[object, ...]:
        """Cheap generation hint for same-title versions without hashing full lyrics."""
        if not timed_lyrics:
            return ()
        indices = sorted({0, 1, len(timed_lyrics) // 2, len(timed_lyrics) - 2, len(timed_lyrics) - 1})
        return (len(timed_lyrics),) + tuple(
            (round(float(timed_lyrics[index].time_seconds), 2), str(timed_lyrics[index].text))
            for index in indices if 0 <= index < len(timed_lyrics)
        )

    @staticmethod
    def _same_primary_source_timeline(
        previous: Sequence[tuple[float, str]], incoming: Sequence[tuple[float, str]],
    ) -> bool:
        """Recognize companion enrichment without cleaning or renumbering source rows."""
        if not previous or not incoming:
            return False
        previous_primary: dict[float, str] = {}
        incoming_primary: dict[float, str] = {}
        for start, text in previous:
            previous_primary.setdefault(start, text)
        for start, text in incoming:
            incoming_primary.setdefault(start, text)
        return previous_primary == incoming_primary

    @staticmethod
    def _remap_source_index(
        index: int, previous: Sequence[tuple[float, str]], incoming: Sequence[tuple[float, str]],
    ) -> int:
        if not 0 <= index < len(previous):
            return index
        key = previous[index]
        matches = [candidate for candidate, row in enumerate(incoming) if row == key]
        if matches:
            return min(matches, key=lambda candidate: abs(candidate - index))
        # A removed translation can have been the UIA Text which matched
        # that clock group. Fall back to its original timestamp's primary,
        # never to the row which happened to inherit the numeric index.
        return next((candidate for candidate, row in enumerate(incoming) if row[0] == key[0]), index)

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            sample = self._last_sample
            refreshing = self._refreshing
        return {
            "service_name": self._service_name,
            "source_prefix": self._source_prefix,
            "available": self._pywinauto_available,
            "refreshing": refreshing,
            "last_error": self._last_error,
            "last_source": "" if sample is None else sample.source,
            "last_confidence": 0.0 if sample is None else sample.confidence,
            "last_lyric_index": -1 if sample is None else sample.lyric_index,
            "last_lyric_text": "" if sample is None else sample.lyric_text,
            "last_sample_age": None if sample is None or sample.sampled_at <= 0.0 else round(time.monotonic() - sample.sampled_at, 3),
            "last_progress_probe": self._last_progress_probe,
        }

    @property
    def source_prefix(self) -> str:
        return self._source_prefix

    def duration_for_track(self, title: str, artist: str) -> float:
        return self._duration_from_local_queue(title, artist)

    def current_track(self) -> QueueTrack | None:
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._pywinauto_available = False
            self._last_error = f"pywinauto unavailable: {exc}"
            return None
        self._pywinauto_available = True
        try:
            windows = self._netease_windows(Desktop(backend="uia"), "", require_title=False)
        except Exception as exc:
            self._last_error = f"uia current track read failed: {exc}"
            return None
        for window in list(windows)[:3]:
            try:
                window_title = str(window.window_text() or "").strip()
            except Exception:
                continue
            track = self._track_from_window_title(window_title)
            if track is None:
                continue
            queued_track = self._queue_track_for_metadata(track.title, track.artist)
            return queued_track or track
        return None

    def position_from_progress_point(self, point: tuple[int, int], duration_seconds: float) -> float | None:
        try:
            duration = max(0.0, float(duration_seconds))
            point_x, point_y = int(point[0]), int(point[1])
        except Exception:
            return None
        if duration <= 0.0:
            return None
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._pywinauto_available = False
            self._last_error = f"pywinauto unavailable: {exc}"
            return None
        self._pywinauto_available = True
        try:
            windows = self._netease_windows(Desktop(backend="uia"), "", require_title=False)
        except Exception as exc:
            self._last_error = f"uia progress point read failed: {exc}"
            return None
        for window in list(windows)[:3]:
            try:
                rect = window.rectangle()
                left = self._rect_value(rect, "left")
                top = self._rect_value(rect, "top")
                right = self._rect_value(rect, "right")
                bottom = self._rect_value(rect, "bottom")
            except Exception:
                continue
            width = right - left
            height = bottom - top
            if width <= 240.0 or height <= 240.0:
                continue
            if point_x < left or point_x > right or point_y < top or point_y > bottom:
                continue
            y_ratio = (float(point_y) - top) / height
            if y_ratio < 0.865 or y_ratio > 0.925:
                continue
            fraction = (float(point_x) - left) / max(1.0, width)
            return max(0.0, min(duration, duration * max(0.0, min(1.0, fraction))))
        return None

    def seek_playback_position(
        self,
        position_seconds: float,
        duration_seconds: float,
        title: str = "",
        artist: str = "",
    ) -> bool:
        """Seek only through a writable UIA range whose new value can be read back."""
        try:
            duration = max(0.0, float(duration_seconds))
            position = max(0.0, min(duration, float(position_seconds)))
        except Exception:
            return False
        self._last_physical_seek_attempted = False
        if duration <= 0.0:
            return False
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._pywinauto_available = False
            self._last_error = f"pywinauto unavailable: {exc}"
            return False
        self._pywinauto_available = True
        try:
            windows = self._netease_windows(Desktop(backend="uia"), title, require_title=bool(title))
        except Exception as exc:
            self._last_error = f"uia seek failed: {exc}"
            return False
        fraction = max(0.0, min(1.0, position / duration))
        for window in list(windows)[:3]:
            if not title or not self._seek_window_matches_track(window, title, artist):
                continue
            range_written = False
            for control in self._progress_seek_controls(window):
                if self._set_progress_control_value(control, fraction):
                    range_written = True
                    if self._seek_window_matches_track(window, title, artist) and self.verify_playback_position(
                        position, duration, title, artist, window=window
                    ):
                        self._last_error = ""
                        return True
            if range_written:
                continue
            if self._click_verified_progress_strip(window, fraction, position, duration, title, artist):
                self._last_error = ""
                return True
        self._last_error = "uia seek did not confirm actual player progress"
        return False

    @property
    def last_physical_seek_attempted(self) -> bool:
        return bool(self._last_physical_seek_attempted)

    def verify_playback_position(
        self,
        position_seconds: float,
        duration_seconds: float,
        title: str = "",
        artist: str = "",
        *,
        window=None,
    ) -> bool:
        """Confirm a seek from the exact player window that was written."""
        try:
            duration = max(0.0, float(duration_seconds))
            target = max(0.0, min(duration, float(position_seconds)))
        except Exception:
            return False
        if duration <= 0.0 or not str(title or "").strip():
            return False
        if window is not None:
            if not self._seek_window_matches_track(window, title, artist):
                return False
            sample = self._position_from_progress_windows((window,), duration)
        else:
            current_track = self.current_track()
            if current_track is None or self._normalize_text(current_track.title) != self._normalize_text(title):
                return False
            artist_key = self._normalize_text(artist)
            if artist_key and artist_key != self._normalize_text(current_track.artist):
                return False
            sample = self._position_from_visible_progress(title, duration, artist)
        if sample is None or sample.source != f"{self._source_prefix}-uia-progress":
            return False
        tolerance = max(1.6, min(3.0, duration * 0.012))
        return abs(float(sample.position_seconds) - target) <= tolerance

    def _click_verified_progress_strip(
        self,
        window,
        fraction: float,
        position_seconds: float,
        duration_seconds: float,
        title: str,
        artist: str = "",
    ) -> bool:
        """Reveal a hidden player temporarily; verify every write against playback."""
        if self._service_name != "netease" or not title or not self._seek_window_matches_track(window, title, artist):
            return False
        with self._temporarily_reveal_player_window(window, title, artist) as visible_window:
            if visible_window is None:
                return False
            for control in self._progress_seek_controls(visible_window):
                if self._set_progress_control_value(control, fraction) and self._seek_window_matches_track(
                    visible_window, title, artist
                ) and self.verify_playback_position(
                    position_seconds, duration_seconds, title, artist, window=visible_window
                ):
                    return True
            return self._click_visible_verified_progress_strip(
                visible_window, fraction, position_seconds, duration_seconds, title, artist
            )

    @contextmanager
    def _temporarily_reveal_player_window(self, window, title: str, artist: str):
        """Restore native visibility, minimized state, and foreground on exit."""
        try:
            handle = int(getattr(window, "handle", 0) or 0)
        except Exception:
            handle = 0
        if handle <= 0:
            # Test doubles without native HWNDs still exercise verification.
            yield window
            return
        try:
            import win32con
            import win32gui
            from pywinauto import Desktop
        except ImportError:
            yield None
            return
        try:
            if not self._native_window_matches_track(handle, title, artist, win32gui):
                yield None
                return
            originally_visible = bool(win32gui.IsWindowVisible(handle))
            originally_minimized = bool(win32gui.IsIconic(handle))
            previous_foreground = int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            yield None
            return
        changed_state = False
        try:
            if originally_minimized or not originally_visible:
                win32gui.ShowWindow(handle, win32con.SW_RESTORE if originally_minimized else win32con.SW_SHOW)
                changed_state = True
            ready = None
            for attempt in range(8):
                try:
                    if win32gui.IsWindowVisible(handle) and self._native_window_matches_track(
                        handle, title, artist, win32gui
                    ):
                        candidate = Desktop(backend="uia").window(handle=handle).wrapper_object()
                        info = candidate.element_info
                        if (
                            int(candidate.handle) == handle
                            and str(info.class_name).casefold() == "orpheusbrowserhost"
                            and self._native_process_executable(int(info.process_id)) == "cloudmusic.exe"
                            and self._window_matches_track(candidate, title, artist)
                        ):
                            ready = candidate
                            break
                except Exception:
                    pass
                if attempt < 7:
                    time.sleep(0.075)
            yield ready
        finally:
            if changed_state:
                try:
                    if originally_minimized and win32gui.IsWindow(handle):
                        win32gui.ShowWindow(handle, win32con.SW_MINIMIZE)
                    if not originally_visible and win32gui.IsWindow(handle):
                        win32gui.ShowWindow(handle, win32con.SW_HIDE)
                except Exception:
                    pass
            try:
                # Do not steal focus if the user switched to a third window
                # while this seek was running. A just-hidden target can yield
                # foreground HWND 0, in which case restore the former owner.
                current_foreground = int(win32gui.GetForegroundWindow() or 0)
                if (
                    previous_foreground
                    and previous_foreground != handle
                    and current_foreground in (0, handle)
                    and win32gui.IsWindow(previous_foreground)
                ):
                    try:
                        Desktop(backend="uia").window(handle=previous_foreground).wrapper_object().set_focus()
                    except Exception:
                        win32gui.SetForegroundWindow(previous_foreground)
            except Exception:
                pass

    def _native_window_matches_track(self, handle: int, title: str, artist: str, win32gui) -> bool:
        try:
            if not win32gui.IsWindow(handle) or win32gui.GetClassName(handle).casefold() != "orpheusbrowserhost":
                return False
            import win32process
            process_id = int(win32process.GetWindowThreadProcessId(handle)[1])
            if self._native_process_executable(process_id) != "cloudmusic.exe":
                return False
            caption = str(win32gui.GetWindowText(handle) or "")
        except Exception:
            return False
        track = self._track_from_window_title(caption)
        if track is None or self._normalize_text(track.title) != self._normalize_text(title):
            return False
        artist_key = self._normalize_text(artist)
        return not artist_key or artist_key in self._normalize_text(track.artist)

    def _seek_window_matches_track(self, window, title: str, artist: str) -> bool:
        if not self._window_matches_track(window, title, artist):
            return False
        try:
            handle = int(getattr(window, "handle", 0) or 0)
        except Exception:
            return False
        if handle <= 0:
            # Non-native test wrappers preserve the semantic verification.
            return not hasattr(window, "handle")
        try:
            import win32gui
            import win32process
            info = window.element_info
            process_id = int(win32process.GetWindowThreadProcessId(handle)[1])
            if self._service_name == "qq":
                class_name = str(win32gui.GetClassName(handle) or "")
                return (
                    win32gui.IsWindow(handle)
                    and int(info.process_id) == process_id
                    and str(info.class_name).casefold() == class_name.casefold()
                    and self._native_process_executable(process_id) == "qqmusic.exe"
                    and any(token in f"{class_name} qqmusic.exe".casefold() for token in self._window_hints)
                    and self._window_matches_track(window, title, artist)
                )
            return (
                int(info.process_id) == process_id
                and str(info.class_name).casefold() == "orpheusbrowserhost"
                and self._native_process_executable(process_id) == "cloudmusic.exe"
                and self._native_window_matches_track(handle, title, artist, win32gui)
            )
        except Exception:
            return False

    def _click_visible_verified_progress_strip(
        self, window, fraction: float, position_seconds: float, duration_seconds: float, title: str, artist: str
    ) -> bool:
        try:
            if not window.is_visible():
                return False
            if not self._seek_window_matches_track(window, title, artist):
                return False
            rect = window.rectangle()
            top = self._rect_value(rect, "top")
            width = self._rect_value(rect, "right") - self._rect_value(rect, "left")
            height = self._rect_value(rect, "bottom") - top
            if width < 640.0 or height < 420.0:
                return False
        except Exception:
            return False
        playback_button_found = False
        for attempt in range(7):
            try:
                buttons = window.descendants(control_type="Button")
            except Exception:
                buttons = ()
            for button in buttons[:120]:
                try:
                    automation_id = str(getattr(button.element_info, "automation_id", "") or "").strip().casefold()
                    button_rect = button.rectangle()
                    center_y = (self._rect_value(button_rect, "top") + self._rect_value(button_rect, "bottom")) / 2.0
                except Exception:
                    continue
                if automation_id == "btn_pc_minibar_play" and center_y >= top + height * 0.86:
                    playback_button_found = True
                    break
            if playback_button_found:
                break
            if attempt < 6:
                time.sleep(0.075)
        if not playback_button_found:
            return False
        try:
            window.set_focus()
            active = False
            for attempt in range(4):
                if window.is_active():
                    active = True
                    break
                if attempt < 3:
                    time.sleep(0.025)
            if not active:
                return False
            if not window.is_active() or not self._seek_window_matches_track(window, title, artist):
                return False
            handle = int(getattr(window, "handle", 0) or 0)
            if handle > 0:
                import win32gui
                if not win32gui.IsWindowVisible(handle):
                    return False
            # The verified CloudMusic build exposes its thin progress strip at
            # 89% of the client height and across the full player width.
            x = max(2.0, min(width - 3.0, max(0.0, min(1.0, float(fraction))) * (width - 1.0)))
            y = height * 0.89
            window.click_input(coords=(int(round(x)), int(round(y))))
            self._last_physical_seek_attempted = True
        except Exception:
            return False
        for attempt in range(4):
            if self.verify_playback_position(
                position_seconds, duration_seconds, title, artist, window=window
            ):
                return True
            if attempt < 3:
                time.sleep(0.065)
        return False

    def _progress_seek_controls(self, window) -> list[object]:
        try:
            window_rect = window.rectangle()
            window_left = self._rect_value(window_rect, "left")
            window_top = self._rect_value(window_rect, "top")
            window_right = self._rect_value(window_rect, "right")
            window_bottom = self._rect_value(window_rect, "bottom")
        except Exception:
            return []
        window_width = window_right - window_left
        window_height = window_bottom - window_top
        if window_width <= 240.0 or window_height <= 180.0:
            return []
        controls: list[tuple[float, object]] = []
        for control_type in ("Slider", "ProgressBar"):
            try:
                candidates = window.descendants(control_type=control_type)
            except Exception:
                candidates = ()
            for control in candidates:
                try:
                    rect = control.rectangle()
                    left = self._rect_value(rect, "left")
                    top = self._rect_value(rect, "top")
                    right = self._rect_value(rect, "right")
                    bottom = self._rect_value(rect, "bottom")
                except Exception:
                    continue
                width = right - left
                height = bottom - top
                center_y = (top + bottom) / 2.0
                if width < max(160.0, window_width * 0.32) or height <= 0.0 or height > 80.0:
                    continue
                if center_y < window_top + window_height * 0.55 or center_y > window_bottom + 4.0:
                    continue
                if left < window_left - 4.0 or right > window_right + 4.0:
                    continue
                bottomness = (center_y - window_top) / window_height
                width_ratio = width / window_width
                controls.append((bottomness * 2.0 + width_ratio, control))
        controls.sort(key=lambda item: item[0], reverse=True)
        return [control for _score, control in controls]

    def _set_progress_control_value(self, control, fraction: float) -> bool:
        fraction = max(0.0, min(1.0, float(fraction)))
        try:
            interface = control.iface_range_value
            minimum = float(interface.CurrentMinimum)
            maximum = float(interface.CurrentMaximum)
            read_only = bool(interface.CurrentIsReadOnly)
            if not read_only and maximum > minimum:
                target = minimum + (maximum - minimum) * fraction
                interface.SetValue(target)
                tolerance = max((maximum - minimum) * 0.012, 0.001)
                for attempt in range(5):
                    try:
                        actual = float(interface.CurrentValue)
                    except Exception:
                        return False
                    if abs(actual - target) <= tolerance:
                        return True
                    if attempt < 4:
                        time.sleep(0.025)
        except Exception:
            return False
        return False

    def _window_matches_track(self, window, title: str, artist: str = "") -> bool:
        """Use the live caption as a guard against seeking a replaced track."""
        title_key = self._normalize_text(title)
        if not title_key:
            return False
        try:
            caption = str(window.window_text() or "").strip()
        except Exception:
            return False
        caption_parts = caption.rsplit(" - ", 1)
        if self._normalize_text(caption_parts[0]) != title_key:
            return False
        artist_key = self._normalize_text(artist)
        if not artist_key:
            return True
        if len(caption_parts) <= 1:
            return False
        caption_artist = caption_parts[1]
        if artist_key == self._normalize_text(caption_artist):
            return True
        # Artist names may be joined in the caption. Match a complete credit,
        # never a substring such as "Artist" inside "Other Artist".
        credits = re.split(r"\s*(?:,|，|、|/|&|;|；|\bfeat\.?\b|\bft\.?\b)\s*", caption_artist, flags=re.I)
        return any(artist_key == self._normalize_text(credit) for credit in credits)

    def track_metadata(self, title: str, artist: str) -> QueueTrack | None:
        """Resolve queue metadata without requiring the player window to be visible."""
        return self._queue_track_for_metadata(title, artist)

    def adjacent_track(self, title: str, artist: str, direction: str) -> QueueTrack | None:
        step = -1 if str(direction or "").strip().casefold() == "previous" else 1
        for path in self._queue_paths():
            payload = self._queue_payload(path)
            if payload is None:
                continue
            track = self._find_adjacent_track(payload, title, artist, step)
            if track is not None:
                return track
        return None

    def upcoming_tracks(self, title: str, artist: str, count: int = 2) -> tuple[QueueTrack, ...]:
        limit = max(0, min(6, int(count)))
        if limit <= 0:
            return ()
        for path in self._queue_paths():
            payload = self._queue_payload(path)
            if payload is None:
                continue
            tracks = self._find_upcoming_tracks(payload, title, artist, limit)
            if tracks:
                return tracks
        return ()

    def reset(self):
        with self._lock:
            self._last_sample = None
            self._last_read_at = 0.0
            self._last_track_key = ""
            self._last_track_duration = 0.0
            self._last_lyric_signature = ()
            self._last_lyric_source_rows = ()
            self._refreshing = False
            self._refresh_token += 1
            self._pending_lyric_index = -1
            self._pending_lyric_index_since = 0.0
            self._pending_lyric_index_last_seen = 0.0
            self._last_ui_playing = None

    def _refresh_sample(
        self,
        track_key: str,
        title: str,
        artist: str,
        timed_lyrics: tuple[TimedLyricLine, ...],
        duration_seconds: float,
        interactive: bool = False,
        refresh_token: int | None = None,
    ):
        try:
            duration = duration_seconds
            if duration <= 0.0:
                duration = self._duration_from_local_queue(title, artist)

            sample = self._position_from_visible_progress(title, duration, artist)
            if sample is None:
                sample = self._position_from_visible_lyrics(title, artist, timed_lyrics, duration)
            with self._lock:
                if track_key != self._last_track_key:
                    return
                if refresh_token is not None and refresh_token != self._refresh_token:
                    return
                if sample is not None:
                    if sample.source == f"{self._source_prefix}-uia-lyrics":
                        sample = self._prefer_recent_progress_sample(self._last_sample, sample)
                        if sample.source == f"{self._source_prefix}-uia-lyrics":
                            sample = self._smooth_visible_sample(self._last_sample, sample, timed_lyrics)
                            sample = self._debounce_visible_line_jump(self._last_sample, sample, timed_lyrics)
                    else:
                        self._pending_lyric_index = -1
                        self._pending_lyric_index_since = 0.0
                        self._pending_lyric_index_last_seen = 0.0
                    self._last_sample = sample
                elif duration > 0.0 and self._last_sample is not None:
                    previous = self._last_sample
                    self._last_sample = NeteasePositionSample(
                        position_seconds=previous.position_seconds,
                        duration_seconds=duration,
                        # A failed UIA scan is not a new source of position.
                        # Retain the exact/coarse provenance and original age
                        # so the next lyric fallback cannot bypass stability.
                        source=previous.source,
                        confidence=previous.confidence,
                        lyric_index=previous.lyric_index,
                        lyric_text=previous.lyric_text,
                        sampled_at=previous.sampled_at,
                        is_playing=previous.is_playing,
                    )
        finally:
            with self._lock:
                if track_key == self._last_track_key and (
                    refresh_token is None or refresh_token == self._refresh_token
                ):
                    self._refreshing = False

    def _position_from_visible_progress(
        self, title: str, duration_seconds: float, artist: str = ""
    ) -> NeteasePositionSample | None:
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._pywinauto_available = False
            self._last_error = f"pywinauto unavailable: {exc}"
            return None
        self._pywinauto_available = True
        try:
            windows = self._netease_windows(Desktop(backend="uia"), title, require_title=True)
            if artist:
                windows = [window for window in windows if self._window_matches_track(window, title, artist)]
        except Exception as exc:
            self._last_error = f"uia progress read failed: {exc}"
            return None
        return self._position_from_progress_windows(windows, duration_seconds)

    def _position_from_progress_windows(
        self,
        windows: Sequence[object],
        duration_seconds: float,
    ) -> NeteasePositionSample | None:
        """Read progress only from the explicitly supplied window wrappers."""
        try:
            bounded_windows = tuple(windows)
            ui_playing = self._playback_state_from_windows(bounded_windows)
            self._last_ui_playing = ui_playing
            snapshot = self._visible_text_snapshot(bounded_windows)
            texts = [str(item["text"]) for item in snapshot]
        except Exception as exc:
            self._last_error = f"uia progress read failed: {exc}"
            return None
        position, duration = self._progress_from_combined_text_controls(snapshot, duration_seconds)
        mode = "text-pair" if position is not None else ""
        if position is None:
            controls = self._time_controls_from_text_snapshot(snapshot)
            position, duration = self._progress_from_time_controls(controls, duration_seconds)
            if position is not None:
                mode = "progress-bar"
        self._last_progress_probe = {
            "windows": len(bounded_windows),
            "texts": len(texts),
            "duration_hint": round(float(duration_seconds), 3),
            "matched": position is not None,
            "mode": mode,
        }
        if position is None:
            return None
        return NeteasePositionSample(
            position_seconds=position,
            duration_seconds=max(float(duration_seconds), float(duration or 0.0)),
            source=f"{self._source_prefix}-uia-progress",
            confidence=0.92,
            sampled_at=time.monotonic(),
            is_playing=ui_playing,
        )

    def _position_from_visible_lyrics(
        self,
        title: str,
        artist: str,
        timed_lyrics: Sequence[TimedLyricLine],
        duration_seconds: float,
    ) -> NeteasePositionSample | None:
        if not timed_lyrics:
            return None
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._pywinauto_available = False
            self._last_error = f"pywinauto unavailable: {exc}"
            return None
        self._pywinauto_available = True

        try:
            rows = self._visible_lyric_rows(Desktop(backend="uia"), title, require_title=True, artist=artist)
        except Exception as exc:
            self._last_error = f"uia read failed: {exc}"
            return None

        indexed = self._lyric_indices_map(timed_lyrics)
        previous_sample = self._last_sample
        matched_rows = []
        for row in rows:
            text = self._row_match_text(row["texts"], indexed)
            choices = indexed.get(self._normalize_text(text), ()) if text else ()
            if choices:
                matched_rows.append((row, text, choices))
        candidates: list[tuple[float, int, str, float, float, bool]] = []
        for row_index, (row, text, lyric_indices) in enumerate(matched_rows):
            lyric_index = self._contextual_visible_lyric_index(
                row_index, matched_rows, timed_lyrics, previous_sample,
            )
            height = float(row.get("max_text_height", 0.0))
            top = float(row.get("top", 0.0))
            rank_bonus = max(0.0, 1.0 - row_index * 0.08)
            score = height * 2.2 + rank_bonus
            candidates.append((score, lyric_index, text, top, height, False))

        if not candidates:
            return None
        # A neighbour's text control can temporarily have the tallest ink
        # while NetEase scrolls its lyric list. Prefer rows that the previous
        # player sample says are already due before comparing text height.
        # When the player was explicitly seeked, none may be due; retain the
        # visual fallback and let the jump confirmation below handle it.
        visually_active_indices = self._visually_active_lyric_rows(matched_rows)
        visually_active = [candidate for index, candidate in enumerate(candidates) if index in visually_active_indices]
        if len(visually_active) == 1:
            # A single enlarged row can hint at a player-side seek, but a
            # selected viewport row may look identical. Debounce it here;
            # the provider still checks an independently advancing clock.
            candidates = visually_active
        elif previous_sample is not None and previous_sample.is_playing is not False:
            now = time.monotonic()
            elapsed = max(0.0, now - float(previous_sample.sampled_at)) if previous_sample.sampled_at > 0.0 else 0.0
            projected = max(0.0, float(previous_sample.position_seconds)) + elapsed
            # Plain visible rows are a browsed viewport, not a playback
            # cursor. Only its source-time group can advance naturally;
            # a persistent scroll to another paragraph is not a Seek.
            due_start = max(
                (float(line.time_seconds) for line in timed_lyrics if float(line.time_seconds) <= projected + 0.65),
                default=float(timed_lyrics[0].time_seconds),
            )
            due_candidates = [
                candidate for candidate in candidates
                if abs(float(timed_lyrics[candidate[1]].time_seconds) - due_start) <= 0.0005
            ]
            if not due_candidates:
                # Repeated text without distinct ordered neighbours cannot
                # prove its next occurrence. Keeping the already mapped
                # source row is safe; selecting a different browsed one is not.
                due_candidates = [
                    candidate for candidate in candidates
                    if candidate[1] == previous_sample.lyric_index
                    and previous_sample.source == f"{self._source_prefix}-uia-lyrics"
                ]
            if not due_candidates:
                return None
            candidates = due_candidates
        candidates.sort(reverse=True)
        _score, lyric_index, lyric_text, _top, _height, _active = candidates[0]
        position = self._estimated_anchor_position(timed_lyrics, lyric_index)
        return NeteasePositionSample(
            position_seconds=position,
            duration_seconds=max(0.0, duration_seconds),
            source=f"{self._source_prefix}-uia-lyrics",
            confidence=0.86,
            lyric_index=lyric_index,
            lyric_text=lyric_text,
            sampled_at=time.monotonic(),
            is_playing=self._last_ui_playing,
        )

    @classmethod
    def _visually_active_lyric_rows(
        cls,
        matched_rows: Sequence[tuple[dict[str, object], str, tuple[int, ...]]],
    ) -> set[int]:
        """Find one enlarged row at NetEase's playback slot using relative ink."""
        candidates: list[tuple[int, float, float]] = []
        for index, (row, _text, _indices) in enumerate(matched_rows):
            geometry = cls._lyric_row_geometry(row)
            if geometry is not None:
                candidates.append((index, geometry[0], geometry[1]))
        if len(candidates) < 2:
            return set()
        slot_rows = [item for item in candidates if item[1] > 0.0]
        if len(slot_rows) != 1:
            return set()
        target_index, _slot, target_ink = slot_rows[0]
        others = sorted(ink for index, _slot, ink in candidates if index != target_index and ink > 0.0)
        if not others:
            return set()
        reference_ink = others[len(others) // 2]
        return {target_index} if target_ink >= reference_ink * 1.14 else set()

    @staticmethod
    def _lyric_row_geometry(row: dict[str, object]) -> tuple[float, float] | None:
        try:
            top = float(row["top"])
            bottom = float(row["bottom"])
            container_top = float(row["container_top"])
            container_bottom = float(row["container_bottom"])
            text_height = float(row["max_text_height"])
            line_height = float(row.get("max_text_line_height", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        span = container_bottom - container_top
        if span < 160.0 or bottom <= top:
            return None
        playback_slot = container_top + span * 0.28
        slot = 1.0 if top <= playback_slot <= bottom else -1.0
        # A row can wrap several lines. UIA may report one Text rectangle
        # spanning them; infer a line step from its height and the ListItem's
        # stable leading. Prefer an explicit per-line measurement if present.
        if line_height <= 0.0:
            visual_lines = max(1, round((bottom - top) / max(1.0, container_bottom - container_top) * 8.8))
            line_height = text_height / visual_lines
        return slot, line_height

    def _position_from_visible_lyric_time_anchor(
        self,
        timed_lyrics: Sequence[TimedLyricLine],
        duration_seconds: float,
    ) -> NeteasePositionSample | None:
        if not timed_lyrics:
            return None
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._pywinauto_available = False
            self._last_error = f"pywinauto unavailable: {exc}"
            return None
        self._pywinauto_available = True
        try:
            rows = self._visible_lyric_rows(Desktop(backend="uia"), "", require_title=False)
        except Exception as exc:
            self._last_error = f"uia lyric time read failed: {exc}"
            return None
        return self._progress_from_lyric_time_rows(rows, timed_lyrics, duration_seconds)

    def _progress_from_lyric_time_rows(
        self,
        rows: Sequence[dict[str, object]],
        timed_lyrics: Sequence[TimedLyricLine],
        duration_seconds: float,
    ) -> NeteasePositionSample | None:
        if not rows or not timed_lyrics:
            return None
        indexed = self._lyric_indices_map(timed_lyrics)
        candidates: list[tuple[float, int, str, float]] = []
        for row_index, row in enumerate(rows):
            texts = tuple(str(text or "").strip() for text in row.get("texts", ()) if str(text or "").strip())
            if len(texts) < 2:
                continue
            lyric_text = self._row_match_text(texts, indexed)
            if not lyric_text:
                continue
            lyric_indices = indexed.get(self._normalize_text(lyric_text), ())
            if not lyric_indices:
                continue
            time_values = [value for value in (self._parse_time_text(text) for text in texts) if value is not None]
            if not time_values:
                continue
            lyric_index, nearest_time = min(
                (
                    (index, time_value)
                    for index in lyric_indices
                    for time_value in time_values
                ),
                key=lambda pair: abs(pair[1] - max(0.0, float(timed_lyrics[pair[0]].time_seconds))),
            )
            line_start = max(0.0, float(timed_lyrics[lyric_index].time_seconds))
            if abs(nearest_time - line_start) > 1.25:
                continue
            height = float(row.get("max_text_height", 0.0) or 0.0)
            top = float(row.get("top", 0.0) or 0.0)
            bottom = float(row.get("bottom", top) or top)
            center_score = 0.0
            container_top = float(row.get("container_top", 0.0) or 0.0)
            container_bottom = float(row.get("container_bottom", 0.0) or 0.0)
            if container_bottom > container_top:
                row_center = (top + bottom) / 2.0
                container_center = (container_top + container_bottom) / 2.0
                center_score = max(0.0, 1.0 - abs(row_center - container_center) / max(1.0, (container_bottom - container_top) / 2.0))
            rank_bonus = max(0.0, 1.0 - row_index * 0.05)
            score = height * 2.0 + center_score * 5.0 + rank_bonus
            candidates.append((score, lyric_index, lyric_text, nearest_time))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        _score, lyric_index, lyric_text, position = candidates[0]
        return NeteasePositionSample(
            position_seconds=max(0.0, position),
            duration_seconds=max(0.0, duration_seconds),
            source=f"{self._source_prefix}-uia-lyric-time",
            confidence=0.82,
            lyric_index=lyric_index,
            lyric_text=lyric_text,
            sampled_at=time.monotonic(),
        )

    def _estimated_anchor_position(self, timed_lyrics: Sequence[TimedLyricLine], lyric_index: int) -> float:
        if not timed_lyrics:
            return 0.0
        index = max(0, min(len(timed_lyrics) - 1, int(lyric_index)))
        line_start = max(0.0, float(timed_lyrics[index].time_seconds))
        next_start = next(
            (float(line.time_seconds) for line in timed_lyrics[index + 1:]
             if float(line.time_seconds) > line_start + 0.08),
            None,
        )
        if next_start is not None:
            span = max(0.35, next_start - line_start)
            return max(line_start, min(next_start - self._line_end_margin_seconds, line_start + min(self._anchor_cap_seconds, span * self._anchor_fraction)))
        return line_start + self._tail_anchor_seconds

    def _line_end_position(self, timed_lyrics: Sequence[TimedLyricLine], lyric_index: int, duration_seconds: float = 0.0) -> float:
        if not timed_lyrics:
            return max(0.0, float(duration_seconds))
        index = max(0, min(len(timed_lyrics) - 1, int(lyric_index)))
        line_start = max(0.0, float(timed_lyrics[index].time_seconds))
        next_start = next(
            (float(line.time_seconds) for line in timed_lyrics[index + 1:]
             if float(line.time_seconds) > line_start + 0.08),
            None,
        )
        if next_start is not None:
            return max(line_start, next_start - self._line_end_margin_seconds)
        if duration_seconds > line_start:
            return max(line_start, float(duration_seconds) - self._line_end_margin_seconds)
        return line_start + max(self._tail_anchor_seconds, 2.2)

    def _smooth_visible_sample(
        self,
        previous: NeteasePositionSample | None,
        sample: NeteasePositionSample,
        timed_lyrics: Sequence[TimedLyricLine],
    ) -> NeteasePositionSample:
        if sample.is_playing is False:
            return sample
        if previous is None:
            return sample
        if sample.source != f"{self._source_prefix}-uia-lyrics" or previous.source != sample.source:
            return sample
        if previous.lyric_index != sample.lyric_index or sample.lyric_index < 0:
            return sample
        elapsed = max(0.0, float(sample.sampled_at) - float(previous.sampled_at))
        if elapsed <= 0.0:
            return sample
        line_end = self._line_end_position(timed_lyrics, sample.lyric_index, sample.duration_seconds)
        advanced = min(line_end, max(float(sample.position_seconds), float(previous.position_seconds) + elapsed))
        if advanced <= float(sample.position_seconds) + 0.01:
            return sample
        return NeteasePositionSample(
            position_seconds=advanced,
            duration_seconds=sample.duration_seconds,
            source=sample.source,
            confidence=sample.confidence,
            lyric_index=sample.lyric_index,
            lyric_text=sample.lyric_text,
            sampled_at=sample.sampled_at,
            is_playing=sample.is_playing,
        )

    def _prefer_recent_progress_sample(
        self,
        previous: NeteasePositionSample | None,
        sample: NeteasePositionSample,
    ) -> NeteasePositionSample:
        """Do not replace a precise progress read with one transient lyric fallback."""
        if previous is None:
            return sample
        if previous.source != f"{self._source_prefix}-uia-progress":
            return sample
        if sample.source != f"{self._source_prefix}-uia-lyrics":
            return sample
        age = float(sample.sampled_at) - float(previous.sampled_at)
        if 0.0 <= age <= 1.5:
            return previous
        return sample

    def _debounce_visible_line_jump(
        self,
        previous: NeteasePositionSample | None,
        sample: NeteasePositionSample,
        timed_lyrics: Sequence[TimedLyricLine] = (),
    ) -> NeteasePositionSample:
        """Require a coarse lyric-row jump to persist before changing anchors."""
        lyric_source = f"{self._source_prefix}-uia-lyrics"
        if (
            previous is None
            or sample.source != lyric_source
            or sample.lyric_index < 0
        ):
            self._pending_lyric_index = -1
            self._pending_lyric_index_since = 0.0
            self._pending_lyric_index_last_seen = 0.0
            return sample
        if previous.source == f"{self._source_prefix}-uia-progress":
            # A missing progress control can expose a coarse lyric row far
            # from the last exact reading. Confirm that row over time before
            # switching source; a single UIA snapshot may have picked the
            # wrong repeated chorus or a neighbouring visual row.
            projected = float(previous.position_seconds) + max(
                0.0, float(sample.sampled_at) - float(previous.sampled_at)
            )
            if abs(float(sample.position_seconds) - projected) <= 2.5:
                self._pending_lyric_index = -1
                self._pending_lyric_index_since = 0.0
                self._pending_lyric_index_last_seen = 0.0
                return sample
        elif previous.source != lyric_source or previous.lyric_index < 0 or sample.lyric_index == previous.lyric_index:
            self._pending_lyric_index = -1
            self._pending_lyric_index_since = 0.0
            self._pending_lyric_index_last_seen = 0.0
            return sample
        if previous.source == f"{self._source_prefix}-uia-progress":
            return self._confirm_pending_lyric_jump(previous, sample)
        distance = abs(int(sample.lyric_index) - int(previous.lyric_index))
        if (
            sample.lyric_index > previous.lyric_index
            and distance <= 2
            and (
                not timed_lyrics
                or self._coarse_line_time_is_due(previous, sample.lyric_index, timed_lyrics, now=sample.sampled_at)
            )
        ):
            self._pending_lyric_index = -1
            self._pending_lyric_index_since = 0.0
            self._pending_lyric_index_last_seen = 0.0
            return sample
        return self._confirm_pending_lyric_jump(previous, sample)

    @staticmethod
    def _coarse_line_time_is_due(
        previous: NeteasePositionSample,
        lyric_index: int,
        timed_lyrics: Sequence[TimedLyricLine],
        *,
        now: float,
    ) -> bool:
        if not 0 <= int(lyric_index) < len(timed_lyrics):
            return False
        sampled_at = float(getattr(previous, "sampled_at", 0.0) or 0.0)
        elapsed = max(0.0, float(now) - sampled_at) if sampled_at > 0.0 else 0.0
        projected = max(0.0, float(previous.position_seconds))
        if previous.is_playing is not False:
            projected += elapsed
        line_start = max(0.0, float(timed_lyrics[int(lyric_index)].time_seconds))
        return line_start <= projected + 0.65

    def _confirm_pending_lyric_jump(
        self,
        previous: NeteasePositionSample,
        sample: NeteasePositionSample,
    ) -> NeteasePositionSample:
        sampled_at = float(sample.sampled_at)
        if (
            self._pending_lyric_index != int(sample.lyric_index)
            or sampled_at - self._pending_lyric_index_last_seen > 1.8
            or sampled_at < self._pending_lyric_index_last_seen
        ):
            self._pending_lyric_index = int(sample.lyric_index)
            self._pending_lyric_index_since = sampled_at
            self._pending_lyric_index_last_seen = sampled_at
            return previous
        self._pending_lyric_index_last_seen = sampled_at
        if sampled_at - self._pending_lyric_index_since < 0.8:
            return previous
        self._pending_lyric_index = -1
        self._pending_lyric_index_since = 0.0
        self._pending_lyric_index_last_seen = 0.0
        return sample

    def _nearest_visible_lyric_index(
        self,
        indices: Sequence[int],
        timed_lyrics: Sequence[TimedLyricLine],
        previous: NeteasePositionSample | None,
    ) -> int:
        choices = tuple(int(index) for index in indices)
        if not choices:
            return 0
        if previous is None:
            return choices[0]
        reference_position = max(0.0, float(previous.position_seconds))
        reference_index = int(previous.lyric_index)
        if previous.source == f"{self._source_prefix}-uia-lyrics" and reference_index in choices:
            # Repeated text alone cannot prove that the player advanced to
            # another occurrence. Its estimated in-row clock is deliberately
            # not evidence of that semantic line change.
            return reference_index
        return min(
            choices,
            key=lambda index: (
                abs(max(0.0, float(timed_lyrics[index].time_seconds)) - reference_position),
                abs(index - reference_index) if reference_index >= 0 else 0,
            ),
        )

    def _contextual_visible_lyric_index(
        self,
        row_index: int,
        matched_rows: Sequence[tuple[dict[str, object], str, tuple[int, ...]]],
        timed_lyrics: Sequence[TimedLyricLine],
        previous: NeteasePositionSample | None,
    ) -> int:
        """Map repeated text using ordered neighbouring rows, never its clock alone."""
        row, text, choices = matched_rows[row_index]
        fallback = self._nearest_visible_lyric_index(choices, timed_lyrics, previous)
        if len(choices) <= 1:
            return fallback

        group_keys = ("container_left", "container_right", "container_top", "container_bottom")
        group = tuple(row.get(key) for key in group_keys)
        context = [
            (index, item_text, item_choices)
            for index, (item, item_text, item_choices) in enumerate(matched_rows)
            if tuple(item.get(key) for key in group_keys) == group
        ]
        target_position = next((offset for offset, item in enumerate(context) if item[0] == row_index), -1)
        if target_position < 0:
            return fallback
        target_text = self._normalize_text(text)
        semantic_order: list[int] = []
        last_time: float | None = None
        group_index = -1
        for line in timed_lyrics:
            start = max(0.0, float(line.time_seconds))
            if last_time is None or abs(start - last_time) > 0.08:
                group_index += 1
                last_time = start
            semantic_order.append(group_index)
        ranked = []
        for choice in choices:
            matches = distinct_matches = gap_cost = 0
            for direction in (-1, 1):
                cursor = int(choice)
                for distance in range(1, 4):
                    visible_position = target_position + direction * distance
                    if not 0 <= visible_position < len(context):
                        break
                    _index, neighbour_text, neighbour_choices = context[visible_position]
                    ordered = [
                        index for index in neighbour_choices
                        if direction * (int(index) - cursor) > 0
                        and 0 <= direction * (semantic_order[int(index)] - semantic_order[cursor]) <= 3
                    ]
                    if not ordered:
                        break
                    match = min(ordered, key=lambda index: abs(int(index) - cursor))
                    gap_cost += max(0, abs(semantic_order[int(match)] - semantic_order[cursor]) - 1)
                    cursor = int(match)
                    matches += 1
                    if self._normalize_text(neighbour_text) != target_text:
                        distinct_matches += 1
            ranked.append(((distinct_matches, matches, -gap_cost), int(choice)))
        ranked.sort(reverse=True)
        best_score, best_index = ranked[0]
        if best_score[0] <= 0 or (len(ranked) > 1 and ranked[1][0] == best_score):
            return fallback
        return best_index

    def _visible_lyric_rows(
        self, desktop, title: str, require_title: bool = True, artist: str = ""
    ) -> list[dict[str, object]]:
        windows = self._netease_windows(desktop, title, require_title=require_title)
        if artist:
            windows = [window for window in windows if self._window_matches_track(window, title, artist)]
        self._last_ui_playing = self._playback_state_from_windows(windows)
        if not windows:
            return []

        rows: list[dict[str, object]] = []
        for window in windows:
            lyric_lists = []
            for control in window.descendants(control_type="List"):
                rect = control.rectangle()
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width >= 320 and height >= 220:
                    lyric_lists.append(control)
            for lyric_list in lyric_lists:
                list_rect = lyric_list.rectangle()
                list_has_rows = False
                for item in lyric_list.descendants(control_type="ListItem"):
                    rect = item.rectangle()
                    if not self._visible_in(rect, list_rect):
                        continue
                    texts = []
                    max_text_height = 0.0
                    max_text_line_height = 0.0
                    for text_control in item.descendants(control_type="Text"):
                        text = text_control.window_text().strip()
                        text_rect = text_control.rectangle()
                        if not text or not self._visible_in(text_rect, list_rect):
                            continue
                        texts.append(text)
                        text_height = float(text_rect.bottom - text_rect.top)
                        max_text_height = max(max_text_height, text_height)
                        # UIA Text controls expose the rendered rectangle but
                        # not font point size. Wrapped lines in NetEase grow
                        # by near-integer line steps; preserve the first step
                        # separately from total ink for a DPI-relative test.
                        line_count = max(1, text.count("\n") + 1)
                        max_text_line_height = max(max_text_line_height, text_height / line_count)
                    if texts:
                        list_has_rows = True
                        rows.append(
                            {
                                "top": float(rect.top),
                                "left": float(rect.left),
                                "right": float(rect.right),
                                "bottom": float(rect.bottom),
                                "container_left": float(list_rect.left),
                                "container_right": float(list_rect.right),
                                "container_top": float(list_rect.top),
                                "container_bottom": float(list_rect.bottom),
                                "texts": tuple(texts),
                                "max_text_height": max_text_height,
                                "max_text_line_height": max_text_line_height,
                            }
                        )
                if not list_has_rows:
                    rows.extend(self._visible_text_rows_from_container(lyric_list, list_rect))
        rows.sort(key=lambda row: (float(row["top"]), float(row["bottom"])))
        return rows

    def _playback_state_from_windows(self, windows: Sequence[object]) -> bool | None:
        """Read the configured player's own play/pause button state.

        NetEase keeps a single ``btn_pc_minibar_play`` automation element and
        changes its accessible name to the action that would occur on click:
        ``play`` means the player is paused, while ``pause`` means it is
        currently playing.  Unknown/localized values deliberately remain
        ``None`` instead of overriding the media session state.
        """
        for window in tuple(windows)[:3]:
            try:
                buttons = window.descendants(control_type="Button")
            except Exception:
                continue
            for button in buttons[:160]:
                try:
                    info = button.element_info
                    automation_id = str(getattr(info, "automation_id", "") or "").strip().casefold()
                    if automation_id != "btn_pc_minibar_play":
                        continue
                    name = str(button.window_text() or getattr(info, "name", "") or "").strip().casefold()
                except Exception:
                    continue
                compact = "".join(name.split())
                if compact in {"play", "播放", "播放音乐"}:
                    return False
                if compact in {"pause", "暂停", "暂停播放"}:
                    return True
        return None

    def _visible_text_rows_from_container(self, container, container_rect) -> list[dict[str, object]]:
        controls: list[dict[str, object]] = []
        try:
            text_controls = container.descendants(control_type="Text")
        except Exception:
            return []
        for text_control in text_controls[:260]:
            try:
                text = str(text_control.window_text() or "").strip()
                rect = text_control.rectangle()
            except Exception:
                continue
            if not text or not self._visible_in(rect, container_rect):
                continue
            controls.append(
                {
                    "text": text,
                    "left": float(rect.left),
                    "right": float(rect.right),
                    "top": float(rect.top),
                    "bottom": float(rect.bottom),
                    "height": float(rect.bottom - rect.top),
                }
            )
        return self._group_text_controls_into_rows(
            controls,
            float(container_rect.left),
            float(container_rect.right),
            float(container_rect.top),
            float(container_rect.bottom),
        )

    def _group_text_controls_into_rows(
        self,
        controls: Sequence[dict[str, object]],
        container_left: float,
        container_right: float,
        container_top: float,
        container_bottom: float,
    ) -> list[dict[str, object]]:
        rows: list[list[dict[str, object]]] = []
        for control in sorted(controls, key=lambda item: (float(item["top"]), float(item["left"]))):
            center = (float(control["top"]) + float(control["bottom"])) / 2.0
            placed = False
            for row in rows:
                row_center = sum((float(item["top"]) + float(item["bottom"])) / 2.0 for item in row) / max(1, len(row))
                row_height = max(float(item["height"]) for item in row)
                if abs(center - row_center) <= max(9.0, row_height * 0.62):
                    row.append(control)
                    placed = True
                    break
            if not placed:
                rows.append([control])
        result: list[dict[str, object]] = []
        for row in rows:
            row.sort(key=lambda item: float(item["left"]))
            texts = tuple(str(item["text"]) for item in row if str(item.get("text", "")).strip())
            if len(texts) < 2:
                continue
            result.append(
                {
                    "left": min(float(item["left"]) for item in row),
                    "right": max(float(item["right"]) for item in row),
                    "container_left": container_left,
                    "container_right": container_right,
                    "top": min(float(item["top"]) for item in row),
                    "bottom": max(float(item["bottom"]) for item in row),
                    "container_top": container_top,
                    "container_bottom": container_bottom,
                    "texts": texts,
                    "max_text_height": max(float(item["height"]) for item in row),
                    "max_text_line_height": max(float(item["height"]) for item in row),
                }
            )
        return result

    def _netease_windows(self, desktop, title: str, require_title: bool) -> list:
        windows = []
        normalized_title = self._normalize_text(title)
        # pywinauto Desktop.windows accepts these filters. Make hidden top-level
        # player windows explicit: a minimized player can still expose a
        # writable UIA RangeValue without requiring a foreground mouse click.
        candidates = desktop.windows(top_level_only=True, visible_only=False, enabled_only=False)
        for window in candidates:
            try:
                info = window.element_info
                class_name = str(getattr(info, "class_name", "") or "")
                process_id = int(getattr(info, "process_id", 0) or 0)
                window_title = str(window.window_text() or "")
            except Exception:
                # A UIA element may disappear during enumeration; it must not
                # prevent a different player window from being considered.
                continue
            try:
                process_hint = str(getattr(info, "process_name", "") or process_id)
            except Exception:
                process_hint = str(process_id)
            haystack = f"{class_name} {window_title} {process_hint}".casefold()
            if not any(token in haystack for token in self._window_hints):
                continue
            if require_title and normalized_title:
                window_track = self._track_from_window_title(window_title)
                if window_track is None or self._normalize_text(window_track.title) != normalized_title:
                    continue
            windows.append(window)
        if not windows and self._service_name == "netease":
            for window in self._native_hidden_player_windows(desktop):
                try:
                    window_title = str(window.window_text() or "")
                except Exception:
                    continue
                if require_title and normalized_title:
                    window_track = self._track_from_window_title(window_title)
                    if window_track is None or self._normalize_text(window_track.title) != normalized_title:
                        continue
                windows.append(window)
        return windows

    def _native_hidden_player_windows(self, desktop) -> list[object]:
        """Resolve a hidden native CloudMusic top-level HWND to a UIA wrapper."""
        if self._service_name != "netease":
            return []
        try:
            import win32gui
            import win32process
        except ImportError:
            return []
        windows: list[object] = []
        process_names: dict[int, str] = {}

        def collect(handle, _unused):
            try:
                if win32gui.IsWindowVisible(handle) or win32gui.GetClassName(handle).casefold() != "orpheusbrowserhost":
                    return
                process_id = int(win32process.GetWindowThreadProcessId(handle)[1])
                if process_id not in process_names:
                    process_names[process_id] = self._native_process_executable(process_id)
                if process_names[process_id] != "cloudmusic.exe":
                    return
                window = desktop.window(handle=handle).wrapper_object()
                info = window.element_info
                if int(info.process_id) != process_id or str(info.class_name).casefold() != "orpheusbrowserhost":
                    return
                windows.append(window)
            except Exception:
                # A player window may be destroyed while enumerating.
                return

        try:
            win32gui.EnumWindows(collect, None)
        except Exception:
            return windows
        return windows

    @staticmethod
    def _native_process_executable(process_id: int) -> str:
        """Read process identity with query-only rights before trusting a HWND."""
        handle = None
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.QueryFullProcessImageNameW.argtypes = (
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
            )
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel32.OpenProcess(0x1000, False, int(process_id))
            if not handle:
                return ""
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                return Path(path.value).name.casefold()
        except Exception:
            return ""
        finally:
            if handle:
                try:
                    kernel32.CloseHandle(handle)
                except Exception:
                    pass
        return ""

    def _visible_text_snapshot(self, windows: Sequence[object]) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for window_index, window in enumerate(list(windows)[:3]):
            try:
                controls = window.descendants(control_type="Text")
            except Exception:
                continue
            try:
                window_rect = window.rectangle()
            except Exception:
                window_rect = None
            window_left = self._rect_value(window_rect, "left") if window_rect is not None else 0.0
            window_top = self._rect_value(window_rect, "top") if window_rect is not None else 0.0
            window_right = self._rect_value(window_rect, "right") if window_rect is not None else 0.0
            window_bottom = self._rect_value(window_rect, "bottom") if window_rect is not None else 0.0
            for control in controls[:240]:
                try:
                    text = str(control.window_text() or "").strip()
                except Exception:
                    continue
                if not text:
                    continue
                item: dict[str, object] = {"text": text, "window_index": window_index}
                if window_rect is not None:
                    try:
                        rect = control.rectangle()
                        left = self._rect_value(rect, "left")
                        top = self._rect_value(rect, "top")
                        right = self._rect_value(rect, "right")
                        bottom = self._rect_value(rect, "bottom")
                    except Exception:
                        left = top = right = bottom = 0.0
                    if right > left and bottom > top:
                        item.update(
                            {
                                "left": left,
                                "top": top,
                                "right": right,
                                "bottom": bottom,
                                "window_left": window_left,
                                "window_top": window_top,
                                "window_right": window_right,
                                "window_bottom": window_bottom,
                            }
                        )
                values.append(item)
        return values

    def _visible_texts(self, windows: Sequence[object]) -> list[str]:
        values: list[str] = []
        for window in list(windows)[:3]:
            try:
                controls = window.descendants(control_type="Text")
            except Exception:
                continue
            for control in controls[:180]:
                try:
                    text = str(control.window_text() or "").strip()
                except Exception:
                    continue
                if text:
                    values.append(text)
        return values

    def _visible_time_controls(self, windows: Sequence[object]) -> list[dict[str, float | str | int]]:
        return self._time_controls_from_text_snapshot(self._visible_text_snapshot(windows))

    def _time_controls_from_text_snapshot(self, snapshot: Sequence[dict[str, object]]) -> list[dict[str, float | str | int]]:
        values: list[dict[str, float | str | int]] = []
        for item in snapshot:
            text = str(item.get("text", "") or "").strip()
            if self._parse_time_text(text) is None:
                continue
            required = ("left", "top", "right", "bottom", "window_left", "window_top", "window_right", "window_bottom")
            if any(key not in item for key in required):
                continue
            values.append(
                {
                    "text": text,
                    "left": float(item["left"]),
                    "top": float(item["top"]),
                    "right": float(item["right"]),
                    "bottom": float(item["bottom"]),
                    "window_left": float(item["window_left"]),
                    "window_top": float(item["window_top"]),
                    "window_right": float(item["window_right"]),
                    "window_bottom": float(item["window_bottom"]),
                    "window_index": int(item.get("window_index", 0) or 0),
                }
            )
        return values

    def _progress_from_combined_text_controls(
        self, snapshot: Sequence[dict[str, object]], duration_seconds: float
    ) -> tuple[float | None, float | None]:
        """Accept a combined time label only in the player's lower control band."""
        candidates: list[tuple[float, float, float]] = []
        for item in snapshot:
            required = ("top", "bottom", "window_top", "window_bottom")
            if any(key not in item for key in required) or not self._in_player_bottom_band(item):
                continue
            position, duration = self._progress_from_texts((str(item.get("text", "")),), duration_seconds)
            if position is None or duration is None:
                continue
            window_height = float(item["window_bottom"]) - float(item["window_top"])
            control_height = float(item["bottom"]) - float(item["top"])
            if window_height <= 0.0 or control_height <= 0.0 or control_height > 48.0:
                continue
            center_y = (float(item["top"]) + float(item["bottom"])) / 2.0
            candidates.append(((center_y - float(item["window_top"])) / window_height, position, duration))
        if not candidates:
            return None, None
        _score, position, duration = max(candidates)
        return position, duration

    def _progress_from_texts(self, texts: Sequence[str], duration_seconds: float) -> tuple[float | None, float | None]:
        for text in texts:
            match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*[/|｜]\s*(\d{1,2}:\d{2}(?::\d{2})?)", text)
            if not match:
                continue
            current = self._parse_time_text(match.group(1))
            duration = self._parse_time_text(match.group(2))
            if current is None or duration is None or current > duration + 2.0:
                continue
            if duration_seconds > 0.0 and abs(duration - duration_seconds) > max(4.0, duration_seconds * 0.08):
                continue
            return max(0.0, current), max(0.0, duration)

        # Unrelated MM:SS labels are usually playlist row durations. Separate
        # labels need the geometric player-bar validation at the call site.
        return None, None

    def _progress_from_time_controls(
        self,
        controls: Sequence[dict[str, float | str | int]],
        duration_seconds: float,
    ) -> tuple[float | None, float | None]:
        parsed: list[tuple[dict[str, float | str | int], float]] = []
        for control in controls:
            value = self._parse_time_text(str(control.get("text", "")))
            if value is not None:
                parsed.append((control, value))
        if len(parsed) < 2:
            return None, None

        known_duration = max(0.0, float(duration_seconds))
        tolerance = max(4.0, known_duration * 0.08)
        candidates: list[tuple[float, float, float]] = []
        for duration_control, duration in parsed:
            if known_duration > 0.0 and abs(duration - known_duration) > tolerance:
                continue
            if known_duration <= 0.0 and duration < 20.0:
                continue
            for current_control, current in parsed:
                if current_control is duration_control:
                    continue
                if current < 0.0 or current > duration + 2.0:
                    continue
                if current >= duration - 0.35:
                    continue
                if not self._looks_like_progress_time_pair(current_control, duration_control):
                    continue
                score = self._progress_time_pair_score(current_control, duration_control)
                candidates.append((score, current, duration))

        if not candidates:
            return None, None
        candidates.sort(reverse=True)
        _score, current, duration = candidates[0]
        return max(0.0, current), max(0.0, duration)

    def _progress_from_loose_time_controls(
        self,
        controls: Sequence[dict[str, float | str | int]],
        duration_seconds: float,
    ) -> tuple[float | None, float | None]:
        parsed: list[tuple[dict[str, float | str | int], float]] = []
        for control in controls:
            value = self._parse_time_text(str(control.get("text", "")))
            if value is not None:
                parsed.append((control, value))
        if len(parsed) < 2:
            return None, None

        known_duration = max(0.0, float(duration_seconds))
        tolerance = max(4.0, known_duration * 0.08)
        candidates: list[tuple[float, float, float]] = []
        for duration_control, duration in parsed:
            if known_duration > 0.0:
                if abs(duration - known_duration) > tolerance:
                    continue
            elif duration < 20.0:
                continue
            for current_control, current in parsed:
                if current_control is duration_control:
                    continue
                if current < 0.0 or current > duration + 2.0:
                    continue
                if current >= duration - 0.35:
                    continue
                if not self._looks_like_loose_progress_time_pair(current_control, duration_control):
                    continue
                score = self._loose_progress_time_pair_score(current_control, duration_control)
                candidates.append((score, current, duration))

        if not candidates:
            return None, None
        candidates.sort(reverse=True)
        _score, current, duration = candidates[0]
        return max(0.0, current), max(0.0, duration)

    def _looks_like_progress_time_pair(
        self,
        current: dict[str, float | str | int],
        duration: dict[str, float | str | int],
    ) -> bool:
        if int(current.get("window_index", -1)) != int(duration.get("window_index", -2)):
            return False
        if not self._same_time_row(current, duration):
            return False
        if float(current["right"]) > float(duration["left"]) + 6.0:
            return False
        if not (self._in_player_bottom_band(current) and self._in_player_bottom_band(duration)):
            return False
        current_h = float(current["bottom"]) - float(current["top"])
        duration_h = float(duration["bottom"]) - float(duration["top"])
        if max(current_h, duration_h) > 42.0:
            return False
        return True

    def _looks_like_loose_progress_time_pair(
        self,
        current: dict[str, float | str | int],
        duration: dict[str, float | str | int],
    ) -> bool:
        if int(current.get("window_index", -1)) != int(duration.get("window_index", -2)):
            return False
        if not (self._in_player_lower_band(current) and self._in_player_lower_band(duration)):
            return False
        current_h = float(current["bottom"]) - float(current["top"])
        duration_h = float(duration["bottom"]) - float(duration["top"])
        if max(current_h, duration_h) > 48.0:
            return False
        current_center_y = (float(current["top"]) + float(current["bottom"])) / 2.0
        duration_center_y = (float(duration["top"]) + float(duration["bottom"])) / 2.0
        if abs(current_center_y - duration_center_y) > max(36.0, max(current_h, duration_h) * 1.8):
            return False
        if float(current["right"]) > float(duration["left"]) + 24.0:
            return False

        window_left = float(current.get("window_left", 0.0))
        window_right = float(current.get("window_right", 0.0))
        window_width = max(1.0, window_right - window_left)
        current_center_x = (float(current["left"]) + float(current["right"])) / 2.0
        duration_center_x = (float(duration["left"]) + float(duration["right"])) / 2.0
        separation = duration_center_x - current_center_x
        if separation < max(48.0, window_width * 0.16):
            return False
        if duration_center_x < window_left + window_width * 0.52:
            return False
        return True

    def _same_time_row(self, a: dict[str, float | str | int], b: dict[str, float | str | int]) -> bool:
        a_center = (float(a["top"]) + float(a["bottom"])) / 2.0
        b_center = (float(b["top"]) + float(b["bottom"])) / 2.0
        a_h = float(a["bottom"]) - float(a["top"])
        b_h = float(b["bottom"]) - float(b["top"])
        return abs(a_center - b_center) <= max(10.0, min(a_h, b_h) * 0.72)

    def _in_player_bottom_band(self, control: dict[str, float | str | int]) -> bool:
        window_top = float(control.get("window_top", 0.0))
        window_bottom = float(control.get("window_bottom", 0.0))
        window_height = window_bottom - window_top
        if window_height <= 0.0:
            return False
        center_y = (float(control["top"]) + float(control["bottom"])) / 2.0
        bottom_ratio = 0.56 if window_height <= 280.0 else 0.72
        return center_y >= window_top + window_height * bottom_ratio

    def _in_player_lower_band(self, control: dict[str, float | str | int]) -> bool:
        window_top = float(control.get("window_top", 0.0))
        window_bottom = float(control.get("window_bottom", 0.0))
        window_height = window_bottom - window_top
        if window_height <= 0.0:
            return False
        center_y = (float(control["top"]) + float(control["bottom"])) / 2.0
        lower_ratio = 0.50 if window_height <= 280.0 else 0.58
        return center_y >= window_top + window_height * lower_ratio

    def _progress_time_pair_score(
        self,
        current: dict[str, float | str | int],
        duration: dict[str, float | str | int],
    ) -> float:
        window_top = float(current.get("window_top", 0.0))
        window_bottom = float(current.get("window_bottom", 0.0))
        window_height = max(1.0, window_bottom - window_top)
        current_center = (float(current["top"]) + float(current["bottom"])) / 2.0
        duration_center = (float(duration["top"]) + float(duration["bottom"])) / 2.0
        bottomness = ((current_center + duration_center) / 2.0 - window_top) / window_height
        alignment = 1.0 - min(1.0, abs(current_center - duration_center) / 18.0)
        separation = min(1.0, max(0.0, (float(duration["left"]) - float(current["right"])) / 180.0))
        return bottomness * 2.0 + alignment + separation * 0.35

    def _loose_progress_time_pair_score(
        self,
        current: dict[str, float | str | int],
        duration: dict[str, float | str | int],
    ) -> float:
        window_left = float(current.get("window_left", 0.0))
        window_right = float(current.get("window_right", 0.0))
        window_top = float(current.get("window_top", 0.0))
        window_bottom = float(current.get("window_bottom", 0.0))
        window_width = max(1.0, window_right - window_left)
        window_height = max(1.0, window_bottom - window_top)
        current_center_y = (float(current["top"]) + float(current["bottom"])) / 2.0
        duration_center_y = (float(duration["top"]) + float(duration["bottom"])) / 2.0
        current_center_x = (float(current["left"]) + float(current["right"])) / 2.0
        duration_center_x = (float(duration["left"]) + float(duration["right"])) / 2.0
        bottomness = ((current_center_y + duration_center_y) / 2.0 - window_top) / window_height
        alignment = 1.0 - min(1.0, abs(current_center_y - duration_center_y) / 40.0)
        separation = min(1.0, max(0.0, (duration_center_x - current_center_x) / max(1.0, window_width * 0.55)))
        rightness = min(1.0, max(0.0, (duration_center_x - window_left) / window_width))
        return bottomness * 1.4 + alignment + separation * 0.45 + rightness * 0.25

    def _parse_time_text(self, text: str) -> float | None:
        text = str(text or "").strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
            return None
        parts = [int(part) for part in text.split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            if seconds >= 60:
                return None
            return float(minutes * 60 + seconds)
        hours, minutes, seconds = parts
        if minutes >= 60 or seconds >= 60:
            return None
        return float(hours * 3600 + minutes * 60 + seconds)

    def _visible_in(self, rect, container) -> bool:
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return False
        return rect.bottom > container.top and rect.top < container.bottom and rect.right > container.left and rect.left < container.right

    def _rect_value(self, rect, name: str) -> float:
        try:
            return float(getattr(rect, name, 0.0) or 0.0)
        except Exception:
            return 0.0

    def _row_match_text(self, texts: Iterable[str], indexed: dict[str, int]) -> str:
        values = [str(text or "").strip() for text in texts if str(text or "").strip()]
        for text in values:
            normalized = self._normalize_text(text)
            if normalized in indexed:
                return text
        if len(values) >= 2:
            combined = "".join(values)
            if self._normalize_text(combined) in indexed:
                return combined
            spaced = " ".join(values)
            if self._normalize_text(spaced) in indexed:
                return spaced
        return ""

    def _lyric_index_map(self, timed_lyrics: Sequence[TimedLyricLine]) -> dict[str, int]:
        indexed: dict[str, int] = {}
        for index, line in enumerate(timed_lyrics):
            normalized = self._normalize_text(line.text)
            if normalized and normalized not in indexed:
                indexed[normalized] = index
        return indexed

    def _lyric_indices_map(self, timed_lyrics: Sequence[TimedLyricLine]) -> dict[str, tuple[int, ...]]:
        grouped: dict[str, list[int]] = {}
        for index, line in enumerate(timed_lyrics):
            # Provider originals may already carry a translated companion in
            # one source row; the player's Text controls still expose each
            # subrow separately. They all map to the same raw source index.
            for text in dict.fromkeys((str(line.text), *str(line.text).splitlines())):
                normalized = self._normalize_text(text)
                if normalized:
                    choices = grouped.setdefault(normalized, [])
                    if index not in choices:
                        choices.append(index)
        return {text: tuple(indices) for text, indices in grouped.items()}

    def _duration_from_local_queue(self, title: str, artist: str) -> float:
        for path in self._queue_paths():
            payload = self._queue_payload(path)
            if payload is None:
                continue
            duration = self._find_duration(payload, title, artist)
            if duration > 0.0:
                return duration
        return 0.0

    def _queue_paths(self) -> tuple[Path, ...]:
        return self._queue_files

    def _queue_payload(self, path: Path) -> object | None:
        with self._queue_payload_lock:
            for _attempt in range(2):
                try:
                    before = path.stat()
                    signature = self._queue_file_signature(before)
                    cached = self._queue_payload_cache.get(path)
                    if cached is not None and cached[0] == signature:
                        return cached[1]
                    payload = json.loads(path.read_text(encoding="utf-8-sig"))
                    after = path.stat()
                except (OSError, UnicodeError, ValueError, TypeError):
                    self._queue_payload_cache.pop(path, None)
                    return None
                if signature != self._queue_file_signature(after):
                    continue
                self._queue_payload_cache[path] = (signature, payload)
                while len(self._queue_payload_cache) > 4:
                    self._queue_payload_cache.pop(next(iter(self._queue_payload_cache)))
                return payload
            # The player was replacing the playlist while we read it; let the
            # next poll retry instead of handing out a mixed generation.
            self._queue_payload_cache.pop(path, None)
            return None

    @staticmethod
    def _queue_file_signature(stat) -> tuple[int, int, int, int, int]:
        # File identity catches atomic replacements even when a player copies
        # the original timestamp and writes another playlist of equal size.
        return (stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size)

    def _find_duration(self, payload: object, title: str, artist: str) -> float:
        title_key = self._normalize_text(title)
        artist_key = self._normalize_text(artist)
        best = 0.0
        for item in self._walk_dicts(payload):
            name = self._normalize_text(str(item.get("name") or item.get("title") or ""))
            if not name or name != title_key:
                continue
            artists = item.get("artists") or item.get("ar") or item.get("artist") or ()
            artist_text = self._normalize_text(self._artist_text(artists))
            if artist_key and artist_key not in artist_text:
                continue
            duration = item.get("duration") or item.get("dt") or item.get("durationMs") or 0
            try:
                best = max(best, float(duration) / 1000.0)
            except Exception:
                continue
        return best

    def _find_adjacent_track(self, payload: object, title: str, artist: str, step: int) -> QueueTrack | None:
        title_key = self._normalize_text(title)
        artist_key = self._normalize_text(artist)
        if not title_key:
            return None
        for items in self._walk_lists(payload):
            tracks = [item for item in items if isinstance(item, dict) and self._normalize_text(str(item.get("name") or item.get("title") or ""))]
            if len(tracks) < 2:
                continue
            for index, item in enumerate(tracks):
                name = self._normalize_text(str(item.get("name") or item.get("title") or ""))
                if name != title_key:
                    continue
                artists = item.get("artists") or item.get("ar") or item.get("artist") or ()
                artist_text = self._normalize_text(self._artist_text(artists))
                if artist_key and artist_key not in artist_text:
                    continue
                target_index = index + int(step)
                if target_index < 0 or target_index >= len(tracks):
                    return None
                return self._queue_track_from_item(tracks[target_index])
        return None

    def _find_upcoming_tracks(self, payload: object, title: str, artist: str, count: int) -> tuple[QueueTrack, ...]:
        title_key = self._normalize_text(title)
        artist_key = self._normalize_text(artist)
        if not title_key:
            return ()
        limit = max(0, int(count))
        for items in self._walk_lists(payload):
            tracks = [item for item in items if isinstance(item, dict) and self._normalize_text(str(item.get("name") or item.get("title") or ""))]
            if len(tracks) < 2:
                continue
            for index, item in enumerate(tracks):
                name = self._normalize_text(str(item.get("name") or item.get("title") or ""))
                if name != title_key:
                    continue
                artists = item.get("artists") or item.get("ar") or item.get("artist") or ()
                artist_text = self._normalize_text(self._artist_text(artists))
                if artist_key and artist_key not in artist_text:
                    continue
                out: list[QueueTrack] = []
                for queued in tracks[index + 1 : index + 1 + limit]:
                    track = self._queue_track_from_item(queued)
                    if track is not None:
                        out.append(track)
                return tuple(out)
        return ()

    def _queue_track_from_item(self, item: dict[str, object]) -> QueueTrack | None:
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            return None
        artists = item.get("artists") or item.get("ar") or item.get("artist") or ()
        album = item.get("album") or item.get("al") or {}
        album_title = ""
        if isinstance(album, dict):
            album_title = str(album.get("name") or album.get("title") or "").strip()
        album_art_url = self._album_art_url_from_item(item, album)
        duration = item.get("duration") or item.get("dt") or item.get("durationMs") or 0
        try:
            duration_seconds = max(0.0, float(duration) / 1000.0)
        except Exception:
            duration_seconds = 0.0
        return QueueTrack(
            title=title,
            artist=self._artist_text(artists).strip(),
            album_title=album_title,
            duration_seconds=duration_seconds,
            album_art_url=album_art_url,
        )

    def _album_art_url_from_item(self, item: dict[str, object], album: object) -> str:
        candidates: list[object] = []
        if isinstance(album, dict):
            candidates.extend(
                album.get(key)
                for key in (
                    "picUrl",
                    "picurl",
                    "coverUrl",
                    "cover",
                    "img80x80",
                    "img1v1Url",
                    "blurPicUrl",
                )
            )
        candidates.extend(
            item.get(key)
            for key in (
                "picUrl",
                "picurl",
                "coverUrl",
                "cover",
                "albumPicUrl",
                "image",
                "img80x80",
            )
        )
        for value in candidates:
            text = str(value or "").strip()
            if text.startswith(("http://", "https://")):
                return text
        return ""

    def _queue_track_for_metadata(self, title: str, artist: str) -> QueueTrack | None:
        title_key = self._normalize_text(title)
        artist_key = self._normalize_text(artist)
        if not title_key:
            return None
        for path in self._queue_paths():
            payload = self._queue_payload(path)
            if payload is None:
                continue
            for items in self._walk_lists(payload):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = self._normalize_text(str(item.get("name") or item.get("title") or ""))
                    if name != title_key:
                        continue
                    artists = item.get("artists") or item.get("ar") or item.get("artist") or ()
                    artist_text = self._normalize_text(self._artist_text(artists))
                    if artist_key and artist_key not in artist_text:
                        continue
                    return self._queue_track_from_item(item)
        return None

    def _track_from_window_title(self, window_title: str) -> QueueTrack | None:
        value = str(window_title or "").strip()
        if not value or len(value) > 280:
            return None
        lowered = value.casefold()
        if lowered in {"netease cloud music", "cloudmusic", "orpheusbrowserhost"}:
            return None
        for separator in (" - ",):
            if separator not in value:
                continue
            title, artist = value.rsplit(separator, 1)
            title = title.strip()
            artist = artist.strip()
            if not title or not artist:
                continue
            if len(title) > 160 or len(artist) > 120:
                continue
            return QueueTrack(title=title, artist=artist)
        return None

    def _walk_lists(self, value: object):
        if isinstance(value, list):
            yield value
            for child in value:
                yield from self._walk_lists(child)
        elif isinstance(value, dict):
            for child in value.values():
                yield from self._walk_lists(child)

    def _walk_dicts(self, value: object):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_dicts(child)

    def _artist_text(self, artists: object) -> str:
        if isinstance(artists, str):
            return artists
        if isinstance(artists, dict):
            return str(artists.get("name") or artists.get("artistName") or "")
        if isinstance(artists, list):
            return " ".join(self._artist_text(item) for item in artists)
        return ""

    def _normalize_text(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").casefold()
        value = re.sub(r"\s+", "", value)
        value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value)
        return value


class QQPositionReader(NeteasePositionReader):
    def __init__(self):
        super().__init__(
            "qq",
            window_hints=("qqmusic", "qmusic", "tencentmusic"),
            queue_paths=(),
            anchor_fraction=0.38,
            anchor_cap_seconds=1.35,
            tail_anchor_seconds=1.0,
            line_end_margin_seconds=0.06,
            min_read_interval=0.50,
        )
