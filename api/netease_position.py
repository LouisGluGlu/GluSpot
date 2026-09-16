from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
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
        self._anchor_fraction = float(anchor_fraction)
        self._anchor_cap_seconds = float(anchor_cap_seconds)
        self._tail_anchor_seconds = float(tail_anchor_seconds)
        self._line_end_margin_seconds = float(line_end_margin_seconds)
        self._last_sample: NeteasePositionSample | None = None
        self._last_read_at = 0.0
        self._last_error = ""
        self._last_progress_probe: dict[str, object] = {}
        self._pywinauto_available: bool | None = None
        self._last_track_key = ""
        self._lock = threading.Lock()
        self._refreshing = False
        self._refresh_token = 0
        self._min_read_interval = float(min_read_interval)

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
        with self._lock:
            if track_key != self._last_track_key:
                self._last_track_key = track_key
                self._last_sample = None
                self._last_read_at = 0.0
                self._refreshing = False
                self._refresh_token = 0

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
                        float(duration_seconds),
                        bool(interactive),
                        refresh_token,
                    ),
                    name=f"{self._service_name.title()}PositionReader",
                    daemon=True,
                )
                thread.start()
            return sample

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

    def adjacent_track(self, title: str, artist: str, direction: str) -> QueueTrack | None:
        step = -1 if str(direction or "").strip().casefold() == "previous" else 1
        for path in self._queue_paths():
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
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
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
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
            self._refreshing = False
            self._refresh_token += 1

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

            sample = self._position_from_visible_progress(title, duration)
            if sample is None:
                sample = self._position_from_visible_lyrics(title, artist, timed_lyrics, duration)
            with self._lock:
                if track_key != self._last_track_key:
                    return
                if refresh_token is not None and refresh_token != self._refresh_token:
                    return
                if sample is not None:
                    if sample.source == f"{self._source_prefix}-uia-lyrics":
                        sample = self._smooth_visible_sample(self._last_sample, sample, timed_lyrics)
                    self._last_sample = sample
                elif duration > 0.0 and self._last_sample is not None:
                    self._last_sample = NeteasePositionSample(
                        position_seconds=self._last_sample.position_seconds,
                        duration_seconds=duration,
                        source=f"{self._source_prefix}-duration",
                        confidence=0.32,
                        lyric_index=self._last_sample.lyric_index,
                        lyric_text=self._last_sample.lyric_text,
                        sampled_at=self._last_sample.sampled_at,
                    )
        finally:
            with self._lock:
                if track_key == self._last_track_key and (
                    refresh_token is None or refresh_token == self._refresh_token
                ):
                    self._refreshing = False

    def _position_from_visible_progress(self, title: str, duration_seconds: float) -> NeteasePositionSample | None:
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._pywinauto_available = False
            self._last_error = f"pywinauto unavailable: {exc}"
            return None
        self._pywinauto_available = True
        try:
            windows = self._netease_windows(Desktop(backend="uia"), title, require_title=False)
            texts = self._visible_texts(windows)
        except Exception as exc:
            self._last_error = f"uia progress read failed: {exc}"
            return None
        position, duration = self._progress_from_texts(texts, duration_seconds)
        self._last_progress_probe = {
            "windows": len(windows),
            "texts": len(texts),
            "duration_hint": round(float(duration_seconds), 3),
            "matched": position is not None,
            "mode": "" if position is None else "text",
        }
        if position is None:
            return None
        return NeteasePositionSample(
            position_seconds=position,
            duration_seconds=max(float(duration_seconds), float(duration or 0.0)),
            source=f"{self._source_prefix}-uia-progress",
            confidence=0.92,
            sampled_at=time.monotonic(),
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
            rows = self._visible_lyric_rows(Desktop(backend="uia"), title, require_title=True)
        except Exception as exc:
            self._last_error = f"uia read failed: {exc}"
            return None

        indexed = self._lyric_index_map(timed_lyrics)
        candidates: list[tuple[float, int, str, float, float]] = []
        for row_index, row in enumerate(rows):
            text = self._row_match_text(row["texts"], indexed)
            if not text:
                continue
            lyric_index = indexed.get(self._normalize_text(text))
            if lyric_index is None:
                continue
            height = float(row.get("max_text_height", 0.0))
            top = float(row.get("top", 0.0))
            rank_bonus = max(0.0, 1.0 - row_index * 0.08)
            score = height * 2.2 + rank_bonus
            candidates.append((score, lyric_index, text, top, height))

        if not candidates:
            return None
        candidates.sort(reverse=True)
        _score, lyric_index, lyric_text, _top, _height = candidates[0]
        position = self._estimated_anchor_position(timed_lyrics, lyric_index)
        return NeteasePositionSample(
            position_seconds=position,
            duration_seconds=max(0.0, duration_seconds),
            source=f"{self._source_prefix}-uia-lyrics",
            confidence=0.86,
            lyric_index=lyric_index,
            lyric_text=lyric_text,
            sampled_at=time.monotonic(),
        )

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
        indexed = self._lyric_index_map(timed_lyrics)
        candidates: list[tuple[float, int, str, float]] = []
        for row_index, row in enumerate(rows):
            texts = tuple(str(text or "").strip() for text in row.get("texts", ()) if str(text or "").strip())
            if len(texts) < 2:
                continue
            lyric_text = self._row_match_text(texts, indexed)
            if not lyric_text:
                continue
            lyric_index = indexed.get(self._normalize_text(lyric_text))
            if lyric_index is None:
                continue
            line_start = max(0.0, float(timed_lyrics[lyric_index].time_seconds))
            time_values = [value for value in (self._parse_time_text(text) for text in texts) if value is not None]
            if not time_values:
                continue
            nearest_time = min(time_values, key=lambda value: abs(value - line_start))
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
        if index < len(timed_lyrics) - 1:
            next_start = max(line_start, float(timed_lyrics[index + 1].time_seconds))
            span = max(0.35, next_start - line_start)
            return min(next_start - self._line_end_margin_seconds, line_start + min(self._anchor_cap_seconds, span * self._anchor_fraction))
        return line_start + self._tail_anchor_seconds

    def _line_end_position(self, timed_lyrics: Sequence[TimedLyricLine], lyric_index: int, duration_seconds: float = 0.0) -> float:
        if not timed_lyrics:
            return max(0.0, float(duration_seconds))
        index = max(0, min(len(timed_lyrics) - 1, int(lyric_index)))
        line_start = max(0.0, float(timed_lyrics[index].time_seconds))
        if index < len(timed_lyrics) - 1:
            next_start = max(line_start + 0.35, float(timed_lyrics[index + 1].time_seconds))
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
        )

    def _visible_lyric_rows(self, desktop, title: str, require_title: bool = True) -> list[dict[str, object]]:
        windows = self._netease_windows(desktop, title, require_title=require_title)
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
                    for text_control in item.descendants(control_type="Text"):
                        text = text_control.window_text().strip()
                        text_rect = text_control.rectangle()
                        if not text or not self._visible_in(text_rect, list_rect):
                            continue
                        texts.append(text)
                        max_text_height = max(max_text_height, float(text_rect.bottom - text_rect.top))
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
                            }
                        )
                if not list_has_rows:
                    rows.extend(self._visible_text_rows_from_container(lyric_list, list_rect))
        rows.sort(key=lambda row: (float(row["top"]), float(row["bottom"])))
        return rows

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
                }
            )
        return result

    def _netease_windows(self, desktop, title: str, require_title: bool) -> list:
        windows = []
        normalized_title = self._normalize_text(title)
        for window in desktop.windows():
            info = window.element_info
            class_name = str(getattr(info, "class_name", "") or "")
            process_id = int(getattr(info, "process_id", 0) or 0)
            window_title = window.window_text() or ""
            process_hint = str(process_id)
            try:
                process_hint = str(getattr(info, "process_name", "") or process_hint)
            except Exception:
                pass
            haystack = f"{class_name} {window_title} {process_hint}".casefold()
            if not any(token in haystack for token in self._window_hints):
                continue
            if require_title and normalized_title and normalized_title not in self._normalize_text(window_title):
                continue
            windows.append(window)
        return windows

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

        time_values: list[float] = []
        for text in texts:
            parsed = self._parse_time_text(str(text or "").strip())
            if parsed is not None:
                time_values.append(parsed)
        if len(time_values) < 2:
            return None, None
        duration = max(time_values)
        if duration_seconds > 0.0:
            duration = duration_seconds
        candidates = [value for value in time_values if value <= duration + 2.0]
        if len(candidates) < 2:
            return None, None
        current_candidates = [value for value in candidates if value < duration - 1.0]
        if not current_candidates:
            return None, None
        current = current_candidates[-1]
        if duration_seconds > 0.0 and duration > 0.0 and current > duration + 2.0:
            return None, None
        return max(0.0, current), max(0.0, duration)

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

    def _duration_from_local_queue(self, title: str, artist: str) -> float:
        for path in self._queue_paths():
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            duration = self._find_duration(payload, title, artist)
            if duration > 0.0:
                return duration
        return 0.0

    def _queue_paths(self) -> tuple[Path, ...]:
        return self._queue_files

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
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
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
