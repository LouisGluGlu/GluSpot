"""Observe production Qt animation lifecycles with offline, visible fixtures.

This is an animation-correctness / event-loop diagnostic, not an FPS benchmark.
It uses the native Windows platform and real wall time. Paints induced by image
exports / QWidget.grab are recorded separately from ordinary event-loop paints.
It never opens an installed GluSpot, account store, network or audio device.
Run separately from performance baselines, after the source tree is frozen.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import (QAbstractAnimation, QBuffer, QCoreApplication, QEvent,
                            QEventLoop, QIODevice, QPoint, QPointF, QRect, QRectF,
                            Qt, QTimer, qVersion)
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteSnapshot
from api.models import ColorToken, TimedLyricLine, TimedLyricUnit
from api.online_music import AccountSnapshot, MusicSource, OnlineSong
from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.liquid_glass_tasks import glass_pixel_tasks
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.music_navigation_transition import MusicNavigationTransition
from local.music_surface_window import MusicSurfaceWindow
from local.music_tasks import MusicTasks
from local.playback_entry_window import PlaybackEntryWindow


def distribution(values):
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(values), "p50_ms": statistics.median(values),
            "p95_ms": ordered[max(0, math.ceil(len(values) * .95) - 1)],
            "p99_ms": ordered[max(0, math.ceil(len(values) * .99) - 1)],
            "max_ms": ordered[-1],
            "over_8_33_ms": sum(value > 8.33 for value in values),
            "over_16_67_ms": sum(value > 16.67 for value in values)}


def source_manifest():
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for base in (ROOT / "api", ROOT / "local") for path in sorted(base.rglob("*.py"))
            if "__pycache__" not in path.parts}


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Fixture PNG encoding failed")
    return bytes(buffer.data())


def artwork(index=0):
    image = QImage(320, 320, QImage.Format.Format_RGB32)
    with QPainter(image) as painter:
        gradient = QLinearGradient(0, 0, 320, 320)
        gradient.setColorAt(0, QColor.fromHsv((index * 37 + 25) % 360, 150, 180))
        gradient.setColorAt(1, QColor.fromHsv((index * 37 + 225) % 360, 190, 90))
        painter.fillRect(image.rect(), gradient)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, f"OFFLINE\n{index + 1:02d}")
    return image


def lyric_rows(index=0):
    rows = []
    for line in range(18):
        start = line * 3.
        text = f"Offline song {index + 1} keeps moving {line + 1}"
        words = text.split(" ")
        units = tuple(TimedLyricUnit(start + position * .3,
                                    start + (position + 1) * .3, word + (" " if position + 1 < len(words) else ""))
                      for position, word in enumerate(words))
        rows.append(TimedLyricLine(start, text + "\n离线动画验收，未连接真实播放", units, "unit"))
    return tuple(rows)


def track(index=0, *, playing=True):
    empty = empty_visual_data().track
    rows = lyric_rows(index)
    return replace(empty, title=f"离线动画曲目 {index + 1}", artist="Synthetic provider",
                   track_id=f"owned:animation-fixture:{index}", is_playing=playing,
                   duration_seconds=60., position_seconds=5., position_sampled_at=time.monotonic(),
                   position_observed_seconds=5., position_source="local-playback",
                   source_app="animation-fixture", lyrics=tuple(row.text for row in rows),
                   timed_lyrics=rows, album_art_bytes=png_bytes(artwork(index)),
                   album_color=ColorToken(142, 89, 57), secondary_color=ColorToken(36, 52, 99))


class Recorder:
    def __init__(self):
        self.stage = ""
        self.exporting = False
        self.started = time.perf_counter()
        self.samples = []

    def add(self, widget, kind, began, **extra):
        if self.stage:
            self.samples.append({"stage": self.stage, "widget": widget,
                "kind": kind, "at_ms": (began - self.started) * 1000,
                "duration_ms": (time.perf_counter() - began) * 1000, **extra})


class MeasuredWidget:
    """Keep actual production paint/timer dispatch; observe around it only."""
    recorder = None
    metric_name = "widget"
    _capture_depth = 0

    def update(self, *args):
        began = time.perf_counter()
        result = super().update(*args)
        if self.recorder:
            self.recorder.add(self.metric_name, "python_update_request", began)
        return result

    def grab(self, *args):
        self._capture_depth += 1
        try:
            return super().grab(*args)
        finally:
            self._capture_depth -= 1

    def paintEvent(self, event):
        began = time.perf_counter()
        try:
            return super().paintEvent(event)
        finally:
            if self.recorder:
                capture = self._capture_depth > 0 or self.recorder.exporting
                self.recorder.add(self.metric_name, "capture_paint" if capture else "eventloop_paint", began,
                                  visible=self.isVisible(), minimized=self.isMinimized())


class MeasuredSurface(MeasuredWidget, MusicSurfaceWindow):
    metric_name = "surface"

    def _on_animation_tick(self):
        began = time.perf_counter()
        try:
            return super()._on_animation_tick()
        finally:
            if self.recorder:
                self.recorder.add(self.metric_name, "animation_tick", began,
                                  timer_interval_ms=self._timer.interval())


class MeasuredLibrary(MeasuredWidget, MusicLibraryWindow):
    metric_name = "library"


class MeasuredEntry(MeasuredWidget, PlaybackEntryWindow):
    metric_name = "entry"


class MeasuredNavigation(MeasuredWidget, MusicNavigationTransition):
    metric_name = "navigation"
    pose_observer = prepared_observer = None

    def _start_prepared(self, frame, destination, target, *, saved=None):
        # Observe the actual ready handoff, after GUI target capture and the
        # current-pose snapshot, rather than merely the earlier intent.
        retarget = self.isVisible() and self._origin_frame is not None and frame is not self._origin_frame
        before = self.pose_observer() if retarget and self.pose_observer is not None else None
        result = super()._start_prepared(frame, destination, target, saved=saved)
        if before is not None and self.prepared_observer is not None:
            self.prepared_observer(before, self.pose_observer(), bool(result))
        return result


class MeasuredIsland(MeasuredWidget, IslandStackVisual):
    metric_name = "island"


class FixtureProvider(EmptyIslandDataProvider):
    def __init__(self):
        super().__init__(replace(empty_visual_data(), track=track()))
        self.current = 0
        self.commands = []
        self.seeks = []
        self.queue = tuple(OnlineSong("netease", f"offline-{index}", f"离线曲目 {index + 1}",
                           "Synthetic provider", "Animation fixture", 60.) for index in range(18))
        self.cover = png_bytes(artwork())

    def _owned_active(self):
        return True

    def get_owned_playback_mode(self):
        return "sequential"

    def get_owned_playback_modes(self):
        return "sequential", "shuffle", "repeat_one"

    def get_owned_playback_queue(self):
        return "offline-queue-generation", self.current, self.queue

    def get_owned_queue_artwork(self, entry):
        return self.cover

    def is_owned_personal_fm(self):
        return False

    def get_audio_route_snapshot(self):
        return AudioRouteSnapshot(capture_scope="owned-player", state="unavailable")

    def send_playback_command(self, command):
        self.commands.append(str(command))
        return False

    def seek_playback_position(self, seconds, expected_track_id=None):
        self.seeks.append((seconds, expected_track_id))
        return False


class FixtureOwner(QWidget):
    def __init__(self, provider):
        super().__init__()
        self.data_provider = provider
        self.tasks = MusicTasks(self, worker_count=1)
        self.settings = {"v2_music_flow_intensity": .74, "v2_animation_fps": 120,
                         "v2_music_content_layout": "lyrics"}
        self.settings_getter = self.settings.get
        self.settings_setter = self.settings.__setitem__
        self._music_windowed_preference = True
        self.enable_dual_line_lyrics = True
        self._music_library_back_callback = lambda: None
        self._estimated_media_position = self.estimated_position
        self.set_track()

    def _run_background(self, name, action):
        if not self.tasks.submit((name, 0), action):
            raise RuntimeError("Offline fixture worker budget exhausted")

    def closeEvent(self, event):
        self.tasks.close()
        super().closeEvent(event)

    def set_track(self, index=0):
        incoming = track(index)
        self.media_track_id = incoming.track_id
        self.track_title, self.artist, self.album_title = incoming.title, incoming.artist, "Animation fixture"
        self.album_color = QColor(*incoming.album_color.rgba())
        self.secondary_color = QColor(*incoming.secondary_color.rgba())
        self.album_art = QImage.fromData(incoming.album_art_bytes)
        self.timed_lyrics = [(row.time_seconds, row.text) for row in incoming.timed_lyrics]
        self.timed_lyric_units = [tuple((unit.start_seconds, unit.end_seconds, unit.text)
                                      for unit in row.units) for row in incoming.timed_lyrics]
        self.timed_lyric_timing_kinds = [row.timing_kind for row in incoming.timed_lyrics]
        self.lyrics = list(incoming.lyrics)
        self.media_duration_seconds = incoming.duration_seconds
        self.media_source_app = "animation-fixture"
        self.media_is_playing = True
        self.seek(5., source="local-playback")
        self.data_provider.current = index

    def estimated_position(self, instant):
        return min(self.media_duration_seconds, self.media_position_seconds +
                   (max(0., instant - self.media_position_sampled_at) if self.media_is_playing else 0.))

    def seek(self, seconds, *, source="local-seek"):
        self.media_position_seconds = seconds
        self.media_position_observed_seconds = seconds
        self.media_position_sampled_at = time.monotonic()
        self.media_position_source = source


class Verification:
    def __init__(self, args):
        self.args = args
        self.app = QApplication.instance() or QApplication([])
        if self.app.platformName() != "windows":
            raise RuntimeError("Use the native Windows Qt platform for this verification")
        self.app.setQuitOnLastWindowClosed(False)
        self.recorder = Recorder()
        self.windows = []
        self._phase_captures_active = False
        self._pending_frame_exports = []
        self.report = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "platform": self.app.platformName(), "qt": qVersion(),
            "scope": "Native Qt wall-time event-loop animation correctness and scheduling; no DWM/physical FPS claim",
            "fixtures": "Offline provider/owner/account snapshots; no installed instance, credentials, network or audio",
            "instrumentation": "Python update requests only; event-loop paints exclude observed grab/export paints; wrappers add overhead",
            "smoke": args.smoke, "checks": [], "frames": [], "phases": [], "callback_errors": [],
            "python_threads_before": threading.active_count(),
            "source_before": source_manifest()}
        self.original_hook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: self.report["callback_errors"].append(
            "".join(traceback.format_exception(kind, value, tb)))

    def register(self, widget):
        widget.recorder = self.recorder
        self.windows.append(widget)
        return widget

    def check(self, label, value, **details):
        self.record_check(label, value, **details)
        if not value:
            raise AssertionError(label)

    def record_check(self, label, value, **details):
        # Finish-time and Qt-callback failures must still reach evidence.json.
        self.report["checks"].append({"check": label, "passed": bool(value), **details})

    def pump(self, milliseconds):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def phase(self, name, duration, action=None, observe=None, captures=()):
        self.recorder.stage = name
        self._phase_captures_active = True
        began = time.perf_counter()
        samples = []
        timer = QTimer()
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(16)
        capture_timers = []
        def sample():
            samples.append({"at_ms": (time.perf_counter() - began) * 1000,
                            **(observe() if observe else {})})
        timer.timeout.connect(sample)
        try:
            if action:
                action()
            for delay, widget, label in captures:
                capture_timer = QTimer()
                capture_timer.setSingleShot(True)
                capture_timer.timeout.connect(lambda page=widget, frame=label: self.capture(page, frame))
                capture_timer.start(delay)
                capture_timers.append(capture_timer)
            timer.start()
            self.pump(duration)
        finally:
            timer.stop()
            for capture_timer in capture_timers:
                capture_timer.stop()
            self.recorder.stage = ""
            self._phase_captures_active = False
        phase = {"name": name, "requested_wait_ms": duration,
                 "wall_ms": (time.perf_counter() - began) * 1000, "observations": samples}
        phase["observer_interval_ms"] = distribution([b["at_ms"] - a["at_ms"] for a, b in zip(samples, samples[1:])])
        self.report["phases"].append(phase)
        # PNG encoding, filesystem writes and hashing are diagnostic exports,
        # not app work. Keep the native grab at its requested animation time,
        # but perform exports only after this event-loop phase has finished.
        export_started = time.perf_counter()
        self.flush_frame_exports()
        phase["deferred_export_ms"] = (time.perf_counter() - export_started) * 1000
        return phase

    def capture(self, widget, name):
        began = time.perf_counter()
        self.recorder.exporting = True
        try:
            image = widget.grab().toImage()
        finally:
            self.recorder.exporting = False
        path = self.args.output / f"{len(self.report['frames']) + 1:02d}-{name}.png"
        self.check(f"{name}: nonempty render", not image.isNull())
        frame = {"name": name, "path": str(path.resolve()),
            "physical_size": [image.width(), image.height()], "dpr": image.devicePixelRatio(),
            "visible": widget.isVisible(), "minimized": widget.isMinimized(),
            "opacity": widget.windowOpacity(), "capture_raster_ms": (time.perf_counter() - began) * 1000,
            "export_deferred_until_phase_end": self._phase_captures_active}
        self.report["frames"].append(frame)
        self._pending_frame_exports.append((frame, image))
        if not self._phase_captures_active:
            self.flush_frame_exports()

    def flush_frame_exports(self):
        pending, self._pending_frame_exports = self._pending_frame_exports, []
        for frame, image in pending:
            began = time.perf_counter()
            path = Path(frame["path"])
            saved = image.save(str(path))
            self.record_check(f"{frame['name']}: image saved", saved)
            if saved:
                frame["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            frame["export_ms"] = (time.perf_counter() - began) * 1000

    def surface_state(self):
        page = self.surface
        return {"layout": page._effective_content_layout(), "layout_fraction": page._layout_transition_fraction,
                "reveal": page._control_reveal_at(time.monotonic()),
                "visible": page.isVisible(), "minimized": page.isMinimized(),
                "tick_timer_active": page._timer.isActive(), "tick_timer_interval_ms": page._timer.interval()}

    def setup(self):
        self.provider = FixtureProvider()
        self.owner = self.register(FixtureOwner(self.provider))
        self.surface = self.register(MeasuredSurface(self.owner))
        available = self.app.primaryScreen().availableGeometry()
        # At native DPR 2 this 1080p monitor has a smaller logical work area.
        # Exercise an actually attainable normal window rather than expect
        # the production screen-bound clamp to restore an off-screen 540px.
        bounds = QRect(0, 0, min(960, available.width() - 32), min(540, available.height() - 32))
        bounds.moveCenter(available.center())
        self.surface._normal_geometry = QRect(bounds)
        self.surface.show_from_island(fullscreen=False, origin_geometry=QRect(), origin_snapshot=QImage())
        self.normal_window_size = self.surface.size()
        self.library = self.register(MeasuredLibrary())
        self.library.set_account(AccountSnapshot("netease", True, "offline-fixture", "离线动画验收", state="verified"))
        self.library.set_background_context(self.owner.settings.get, self.owner._run_background)
        self.library.set_now_playing(self.provider.visual_data.track)
        collections = tuple(LibraryCollection(f"offline-playlist-{index}", "playlist", f"离线歌单 {index + 1}", "", f"{index}")
                            for index in range(12))
        self.library.set_collections(collections)
        self.library.setGeometry(bounds)
        self.entry = self.register(MeasuredEntry(owned=True, system=True))
        self.entry.set_sources((MusicSource("netease", "网易云音乐", True), MusicSource("qq", "QQ 音乐", True)))
        self.entry.show_sources()
        self.entry.setGeometry(bounds)
        self.navigation = self.register(MeasuredNavigation())
        self.navigation.pose_observer = self.navigation_state
        self.navigation.prepared_observer = self.navigation_ready
        self.report["dpr"] = self.surface.devicePixelRatioF()
        self.check("Requested native DPR", abs(self.report["dpr"] - self.args.expect_dpr) < .01)
        self.phase("initial-production-paint", 300, observe=self.surface_state)
        self.capture(self.surface, "initial-lyrics")

    def controls_and_layouts(self):
        page = self.surface
        self.phase("controls-hover-enter", 180,
                   lambda: (setattr(page, "_hovered_window_button", "library"),
                            page._set_control_hover("library", "", time.monotonic()), page.update()), self.surface_state,
                   captures=((80, page, "hover-midpoint"),))
        self.capture(page, "hover")
        self.phase("controls-hover-leave", 180,
                   lambda: (setattr(page, "_hovered_window_button", ""),
                            page._set_control_hover("", "", time.monotonic()), page.update()), self.surface_state)
        self.phase("controls-real-idle-hide", 2600, page._reset_control_idle, self.surface_state,
                   captures=((2200, page, "controls-hiding-midpoint"),))
        self.check("Idle controls reach hidden pose", page._control_reveal_at(time.monotonic()) < .01)
        self.capture(page, "controls-hidden")
        self.phase("controls-reveal", 620, page._reset_control_idle, self.surface_state)
        self.check("Controls recover visible pose", page._control_reveal_at(time.monotonic()) > .99)
        self.capture(page, "controls-visible")
        for layout in ("cover", "cover_lyrics", "playlist", "lyrics", "playlist", "cover_lyrics", "cover", "lyrics"):
            label = f"layout-{len(self.report['phases'])}-{layout}"
            self.phase(label, 660, lambda value=layout: page.set_content_layout(value), self.surface_state,
                       captures=((280, page, label + "-midpoint"),))
            self.check(label + ": settles", page._layout_transition_fraction >= 1. and page._layout_snapshot.isNull())
            self.capture(page, label)
        self.phase("layout-interrupted-forward", 160, lambda: page.set_content_layout("cover_lyrics"), self.surface_state)
        self.phase("layout-interrupted-reverse", 680, lambda: page.set_content_layout("lyrics"), self.surface_state)
        self.check("Interrupted layout releases snapshots", page._layout_snapshot.isNull() and page._layout_target_snapshot.isNull())
        self.capture(page, "layout-reversed")

    def presentation_scenarios(self):
        page = self.surface
        self.phase("window-to-fullscreen", 600, page.toggle_presentation_mode, self.surface_state,
                   captures=((170, page, "presentation-midpoint"),))
        self.check("Native fullscreen transition settles", page._fullscreen and page._pending_fullscreen is None
                   and page._geometry_transition_snapshot.isNull())
        for layout in ("cover", "cover_lyrics", "playlist", "lyrics"):
            label = f"fullscreen-{layout}"
            self.phase(label, 680, lambda value=layout: page.set_content_layout(value), self.surface_state,
                       captures=((280, page, label + "-midpoint"),))
            self.check(label + ": settles", page._layout_transition_fraction >= 1. and page._layout_snapshot.isNull())
            self.capture(page, label)
        self.phase("fullscreen-to-window", 600, page.toggle_presentation_mode, self.surface_state)
        self.check("Window geometry restored", not page._fullscreen and page._pending_fullscreen is None
                   and page.size() == self.normal_window_size,
                   actual=[page.width(), page.height()],
                   expected=[self.normal_window_size.width(), self.normal_window_size.height()])
        self.phase("presentation-interrupted-forward", 110, page.toggle_presentation_mode, self.surface_state)
        self.phase("presentation-interrupted-reverse", 620, page.toggle_presentation_mode, self.surface_state)
        self.check("Presentation reversal releases snapshots", not page._fullscreen and page._pending_fullscreen is None
                   and page._geometry_transition_snapshot.isNull())
        self.capture(page, "presentation-reversed-window")

    def seek_track_and_visibility(self):
        page, owner = self.surface, self.owner
        owner.media_is_playing = False
        owner.seek(5.)
        self.phase("paused-clock", 250, observe=self.surface_state)
        self.check("Paused fixture position stays fixed", owner.estimated_position(time.monotonic()) == 5.)
        self.phase("seek-forward", 300, lambda: owner.seek(24.), self.surface_state)
        self.capture(page, "seek-forward")
        self.phase("seek-backward", 300, lambda: owner.seek(3.), self.surface_state)
        self.capture(page, "seek-backward")
        self.phase("track-switch", 750, lambda: owner.set_track(1), self.surface_state)
        self.check("New track bound to lyric renderer", page._browse_track_key == owner.media_track_id,
                   actual=page._browse_track_key, expected=owner.media_track_id)
        self.capture(page, "new-track")
        self.phase("surface-hidden", 220, page.hide, self.surface_state)
        self.check("Hidden surface timer stopped", not page._timer.isActive() and not page._audio_output_status_timer.isActive())
        self.phase("surface-reopen", 350, lambda: page.show_from_island(fullscreen=False), self.surface_state)
        self.check("Reopened surface timers resume", page.isVisible() and page._timer.isActive())
        self.phase("surface-minimized", 250, page.showMinimized, self.surface_state)
        self.check("Minimized surface timer stopped", page.isMinimized() and not page._timer.isActive())
        self.phase("surface-unminimized", 350, page.showNormal, self.surface_state)
        self.check("Unminimized surface timer resumes", not page.isMinimized() and page._timer.isActive())
        self.capture(page, "reopened")

    def start_navigation(self, origin, destination, separate):
        frame = self.navigation.origin_frame(origin, separate_cover=separate, freeze_cover=True)
        destination.show()
        self.check("Navigation snapshot starts", self.navigation.request_start(frame, destination))
        origin.hide()

    def navigation_state(self):
        navigation = self.navigation
        state = {"fraction": navigation._fraction, "visible": navigation.isVisible(),
                "cover_handoff": navigation._cover_transition,
                "pending_target": navigation._pending_start is not None,
                "origin_rasterized": navigation._origin_presented,
                "library_frozen": self.library._navigation_cover_frozen,
                "library_background_timer": self.library._background_timer.isActive(),
                "library_rotation_timer": self.library._album_rotation_timer.isActive(),
                "library_skeleton_timer": self.library._artwork_skeleton_timer.isActive()}
        if navigation._origin_frame is not None:
            rect = navigation._window_rect_at()
            state["window_pose"] = [rect.x(), rect.y(), rect.width(), rect.height()]
            state["velocity"] = list(navigation._pose_velocity())
            if navigation._cover_transition:
                cover = navigation._cover_rect_at()
                state["cover_pose"] = [cover.x(), cover.y(), cover.width(), cover.height(), navigation._cover_angle_at()]
        service = glass_pixel_tasks(create=False)
        state["glass_jobs"] = {"active": service.active is not None, "pending": len(service.pending),
            "input_bytes": service.input_bytes, "cached_bytes": service.cache_bytes} if service is not None else None
        return state

    def reverse_navigation(self, origin, destination, separate):
        before = self.navigation_state()
        self.start_navigation(origin, destination, separate)
        after = self.navigation_state()
        self.check("Navigation reverse keeps current visible window pose", before.get("window_pose") == after.get("window_pose"))
        if before["cover_handoff"]:
            self.check("Navigation reverse keeps current album pose", before.get("cover_pose") == after.get("cover_pose"))
        self.check("Navigation reverse keeps incoming velocity while preparing", before.get("velocity") == after.get("velocity"))
        self.report.setdefault("navigation_retargets", []).append({"before": before, "after_request": after})

    def navigation_ready(self, before, after, started):
        evidence = {"before_ready_handoff": before, "after_ready_handoff": after, "started": started}
        self.report.setdefault("navigation_ready_handoffs", []).append(evidence)
        self.record_check("Prepared retarget starts from its ready picture", started)
        for key, label in (("window_pose", "window pose"), ("velocity", "incoming velocity")):
            a, b = before.get(key, []), after.get(key, [])
            error = max((abs(x-y) for x, y in zip(a, b)), default=None)
            self.record_check("Prepared retarget preserves " + label,
                bool(a) and len(a) == len(b) and error is not None and error <= 1e-5,
                maximum_absolute_difference=error)
        if before["cover_handoff"] and after["cover_handoff"]:
            a, b = before.get("cover_pose", []), after.get("cover_pose", [])
            error = max((abs(x-y) for x, y in zip(a, b)), default=None)
            self.record_check("Prepared retarget preserves album pose", bool(a) and len(a) == len(b)
                and error is not None and error <= 1e-5, maximum_absolute_difference=error)

    def navigation_scenarios(self):
        self.surface.hide()
        self.library.show()
        self.phase("library-background-live", 300)
        self.capture(self.library, "library")
        self.phase("library-cover-to-surface", 260,
                   lambda: self.start_navigation(self.library, self.surface, True), self.navigation_state)
        self.check("Origin library frozen throughout overlay", self.library._navigation_cover_frozen)
        self.capture(self.navigation, "cover-navigation-midpoint")
        self.phase("library-cover-to-surface-finish", 450, observe=self.navigation_state)
        self.check("Cover navigation target restored", not self.navigation.isVisible() and self.surface.isEnabled()
                   and self.surface.windowOpacity() > .99 and not self.library._navigation_cover_frozen)
        self.phase("surface-cover-to-library", 680,
                   lambda: self.start_navigation(self.surface, self.library, True), self.navigation_state)
        self.check("Returned library resumes visible clocks", self.library._background_timer.isActive()
                   and self.library._album_rotation_timer.isActive())
        self.capture(self.library, "library-returned")
        self.phase("navigation-reverse-forward-midpoint", 180,
                   lambda: self.start_navigation(self.library, self.surface, True), self.navigation_state,
                   captures=((120, self.navigation, "navigation-before-reverse"),))
        self.check("Native cover transition reaches an intermediate pose", self.navigation.isVisible()
                   and self.navigation._cover_transition and 0. < self.navigation._fraction < 1.)
        self.phase("navigation-reverse-from-visible-pose", 760,
                   lambda: self.reverse_navigation(self.surface, self.library, True), self.navigation_state,
                   captures=((160, self.navigation, "navigation-reverse-midpoint"),))
        self.check("Native reverse observed its actual prepared handoff",
                   len(self.report.get("navigation_ready_handoffs", ())) >= 1)
        self.check("Native reversed navigation settles and restores destination", not self.navigation.isVisible()
                   and self.library.isEnabled() and self.library.windowOpacity() > .99
                   and not self.library._navigation_cover_frozen and self.library._background_timer.isActive())
        self.capture(self.library, "navigation-reverse-settled-library")
        self.phase("library-to-login", 370,
                   lambda: self.start_navigation(self.library, self.entry, False), self.navigation_state)
        self.check("Plain navigation restores login", self.entry.isVisible() and self.entry.isEnabled()
                   and self.entry.windowOpacity() > .99 and not self.library._navigation_cover_frozen)
        self.capture(self.entry, "login-sources")
        self.phase("login-select-source", 450, lambda: self.entry.select_source("netease"))
        self.capture(self.entry, "login-expanded-no-network")
        self.phase("login-to-library-midpoint", 90,
                   lambda: self.start_navigation(self.entry, self.library, False), self.navigation_state)
        self.check("Plain handoff target background is frozen", self.library._navigation_cover_frozen
                   and not self.library._background_timer.isActive() and not self.library._album_rotation_timer.isActive())
        self.phase("navigation-hide-cancel", 160, self.navigation.hide, self.navigation_state)
        self.check("External overlay hide cleans target", not self.navigation.isVisible()
                   and self.navigation._destination is None and self.library.isEnabled()
                   and self.library.windowOpacity() > .99 and not self.library._navigation_cover_frozen)
        self.check("Cancel releases both snapshot images", self.navigation._origin.isNull() and self.navigation._target.isNull())
        self.capture(self.library, "cancel-restored-library")
        for index in range(4):
            source, target = (self.library, self.entry) if index % 2 == 0 else (self.entry, self.library)
            source.show()
            self.phase(f"navigation-continuous-{index}-midpoint", 72,
                       lambda a=source, b=target: self.start_navigation(a, b, False), self.navigation_state)
            cancel = self.navigation.cancel if index % 2 == 0 else self.navigation.hide
            self.phase(f"navigation-continuous-{index}-cancel", 80, cancel, self.navigation_state)
            self.check(f"Repeated navigation cancel {index} restores input and opacity", target.isEnabled()
                       and target.windowOpacity() > .99 and not self.navigation.isVisible())
            self.check(f"Repeated navigation cancel {index} releases pending and rasters",
                       self.navigation._pending_start is None and self.navigation._origin.isNull()
                       and self.navigation._target.isNull() and not self.library._navigation_cover_frozen)
        self.phase("library-hidden", 160, self.library.hide)
        self.check("Hidden library clocks stop", not self.library._background_timer.isActive()
                   and not self.library._album_rotation_timer.isActive() and not self.library._artwork_skeleton_timer.isActive())

    def compact_handoff(self):
        for widget in (self.surface, self.library, self.entry, self.navigation):
            widget.hide()
        island = self.register(MeasuredIsland(self.provider))
        # Isolate placement evidence only; preserve all production animation,
        # timer, show/hide and paint dispatch. No Windows taskbar/Dock mutation.
        island._force_super_topmost = lambda: None
        island._release_super_topmost = lambda: None
        island._mydockfinder_available = lambda: False
        island._finder_bar_available = lambda: False
        island._effective_lyric_offset_seconds = lambda: 0.
        island._detect_external_scene_suppression = lambda: (False, "")
        island._owner_bar_hidden = lambda *_args, **_kwargs: False
        island._refresh_taskbar_follow_position = lambda: False
        island._refresh_taskbar_above_follow_position = lambda: False
        island.settings_changed_callback = lambda _snapshot: None
        island.music_auto_detect = False
        island.music_ambience_style = "pure_black"
        island.location_mode = "free"
        island.finder_mode = island.attach_to_real_finder = False
        page = self.register(MeasuredSurface(island))
        page.metric_name = "compact-surface"
        island.music_fullscreen_overlay = page
        minimum = QRect(0, 0, 330, 400)
        minimum.moveCenter(page._available_geometry().center())
        page._normal_geometry = QRect(minimum)
        self.phase("compact-open-production-window", 400,
                   lambda: island._show_music_fullscreen(fullscreen=False, origin_geometry=QRect(), origin_snapshot=QImage()))
        self.check("Compact source visible at actual minimum", page.isVisible() and page.size() == minimum.size())
        page._resize_edges = {"right", "bottom"}
        target = []
        def compress():
            page._update_window_compact(165., 200.)
            target.append(QRect(page._compact_target))
        self.phase("window-to-free-real-handoff", 700, compress)
        self.check("Free island visible after real handoff", island.isVisible() and not island.isMinimized()
                   and island.windowOpacity() > .99 and not page.isVisible() and island.location_mode == "free")
        self.check("Free handoff releases navigation ownership", not island._music_surface_active
                   and not island._navigation_surface_blocks_island() and not island.scene_suppressed)
        delta = island._current_main_global_rect().center() - QRectF(target[0]).center()
        self.check("Free destination remains at compression preview", abs(delta.x()) * island.devicePixelRatioF() <= 1.01
                   and abs(delta.y()) * island.devicePixelRatioF() <= 1.01, delta=[delta.x(), delta.y()])
        self.check("Free input mask restored", island.mask().isEmpty()
                   and not island.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.capture(island, "free-island-after-compression")
        self.phase("free-island-remains-visible", 400)
        self.check("Free island survives later event-loop ticks", island.isVisible() and not island.scene_suppressed)
        self.check("Fixture caused no audio commands or seek", not self.provider.commands and not self.provider.seeks)
        island.music_fullscreen_overlay = None
        island._keep_minimum_surface = False

    def finish(self):
        self.recorder.stage = ""
        self._phase_captures_active = False
        self.flush_frame_exports()
        for widget in reversed(self.windows):
            if isinstance(widget, MusicSurfaceWindow):
                widget.shutdown()
            if isinstance(widget, MusicNavigationTransition):
                widget.cancel()
            if isinstance(widget, IslandStackVisual):
                widget._keep_minimum_surface = False
                widget.music_fullscreen_overlay = None
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.pump(120)
        # The pixel worker belongs to QApplication, rather than an individual
        # page. Distinguish one reusable idle worker from pending window work,
        # then exercise its actual application-shutdown release explicitly.
        service = glass_pixel_tasks(create=False)
        if service is not None:
            deadline = time.monotonic() + 2.
            while service.active is not None and time.monotonic() < deadline:
                self.pump(20)
            self.report["glass_after_window_close"] = {"closed": service.closed,
                "active": service.active is not None, "pending": len(service.pending),
                "input_bytes": service.input_bytes, "cache_bytes": service.cache_bytes,
                "owners": len(service.owners), "submitted": service.submitted,
                "discarded": service.discarded}
            self.record_check("Closed windows release pending glass work and cached pictures",
                service.active is None and not service.pending and service.input_bytes == 0
                and service.cache_bytes == 0)
            service.close()
            self.pump(120)
            self.report["glass_after_application_shutdown"] = {"closed": service.closed,
                "active": service.active is not None, "pending": len(service.pending),
                "input_bytes": service.input_bytes, "cache_bytes": service.cache_bytes}
            self.record_check("Application shutdown closes its glass runtime",
                service.closed and service.active is None and not service.pending
                and service.input_bytes == 0 and service.cache_bytes == 0)
        deadline = time.monotonic() + 2.
        while any(thread.name.startswith("GluSpotMusic-") for thread in threading.enumerate()) and time.monotonic() < deadline:
            self.pump(20)
        self.report["python_threads_after_shutdown"] = threading.active_count()
        self.report["music_worker_threads_after_shutdown"] = sum(
            thread.name.startswith("GluSpotMusic-") for thread in threading.enumerate())
        self.record_check("Application shutdown releases all owned music workers",
                          self.report["music_worker_threads_after_shutdown"] == 0)
        self.report["source_after"] = source_manifest()
        self.report["source_unchanged"] = self.report["source_before"] == self.report["source_after"]
        self.report["samples"] = self.recorder.samples
        for phase in self.report["phases"]:
            phase_samples = [item for item in self.recorder.samples if item["stage"] == phase["name"]]
            phase["metrics"] = {}
            for widget in sorted({item["widget"] for item in phase_samples}):
                rows = [item for item in phase_samples if item["widget"] == widget]
                phase["metrics"][widget] = {}
                for kind in sorted({item["kind"] for item in rows}):
                    group = [item for item in rows if item["kind"] == kind]
                    phase["metrics"][widget][kind] = {
                        "duration": distribution([item["duration_ms"] for item in group]),
                        "interval": distribution([b["at_ms"] - a["at_ms"] for a, b in zip(group, group[1:])])}
        self.report["passed"] = (not self.report.get("failure") and not self.report["callback_errors"]
            and all(item["passed"] for item in self.report["checks"])
            and (self.report["source_unchanged"] or not self.args.strict_source))
        path = self.args.output / "evidence.json"
        path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.excepthook = self.original_hook
        print(json.dumps({"passed": self.report["passed"], "checks": len(self.report["checks"]),
              "frames": len(self.report["frames"]), "phases": len(self.report["phases"]),
              "source_unchanged": self.report["source_unchanged"], "evidence": str(path.resolve())}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/animation-lifecycle")
    parser.add_argument("--expect-dpr", type=float, default=1.)
    parser.add_argument("--strict-source", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Short development dispatch check; not full animation acceptance")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    verification = Verification(args)
    try:
        verification.setup()
        if args.smoke:
            verification.phase("development-smoke-layout", 680,
                               lambda: verification.surface.set_content_layout("cover_lyrics"), verification.surface_state)
            verification.capture(verification.surface, "development-smoke")
        else:
            verification.controls_and_layouts()
            verification.presentation_scenarios()
            verification.seek_track_and_visibility()
            verification.navigation_scenarios()
            verification.compact_handoff()
    except Exception:
        verification.report["failure"] = traceback.format_exc()
        print(verification.report["failure"], file=sys.stderr)
    finally:
        verification.finish()
    return 0 if verification.report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
