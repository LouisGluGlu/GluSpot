"""Review deterministic owner-bar motion through the native Qt V2 renderer.

Only the OS bar/screen probes and foreground-fullscreen evidence are fixtures.
Placement, follow targets, scene policy, opacity springs and paintEvent are the
production implementations. No desktop input, screen capture or audio is used.
This cannot certify physical MyDockFinder/Explorer timing or display smoothness.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from local.island_stack_visual import IslandStackVisual
from local.mydockfinder import FinderGlassMaterial
from tests.test_finder_bar_surface import FinderBarSurfaceTests


DURATION = 1.7
START = 100.0
SCREEN = QRect(-660, 0, 660, 360)


def smooth(value):
    value = max(0., min(1., value))
    return value * value * (3. - 2. * value)


def owner_fraction(seconds):
    """Visible -> hidden -> partial reveal -> reverse -> hidden -> visible."""
    if seconds < .15:
        return 0.
    if seconds < .4:
        return smooth((seconds - .15) / .25)
    if seconds < .45:
        return 1.
    if seconds < .49:
        return 1. - .65 * smooth((seconds - .45) / .04)
    if seconds < .53:
        return .35 + .65 * smooth((seconds - .49) / .04)
    if seconds < .8:
        return 1.
    if seconds < 1.1:
        return 1. - smooth((seconds - .8) / .3)
    return 0.


def rect_values(rect):
    return [round(float(value), 5) for value in (rect.x(), rect.y(), rect.width(), rect.height())]


def percentile(values, fraction=.95):
    values = sorted(values)
    return values[min(len(values) - 1, max(0, math.ceil(len(values) * fraction) - 1))]


def draw_frame(visual, owner_rect, dpr, mode, seconds, frame_index):
    # QWidget.render invokes production paintEvent including its scene opacity.
    snapshot = QImage(round(visual.width()*dpr), round(visual.height()*dpr),
                      QImage.Format.Format_ARGB32_Premultiplied)
    snapshot.setDevicePixelRatio(dpr)
    snapshot.fill(Qt.GlobalColor.transparent)
    paint_started = time.perf_counter()
    with QPainter(snapshot) as painter:
        # QWidget.render's default masked-source bounding box can rebase a
        # centered native mask to targetOffset(0, 0). Preserve the actual mask
        # explicitly while rendering in the complete widget coordinate space.
        mask = visual.mask()
        if not mask.isEmpty():
            painter.setClipRegion(mask)
        visual.render(painter, QPoint(), QRegion(visual.rect()),
                      QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren
                      | QWidget.RenderFlag.IgnoreMask)
    paint_ms = (time.perf_counter() - paint_started) * 1000.

    width, height = 660, 160
    viewport_y = -30 if mode == "finder" else 270
    frame = QImage(round(width*dpr), round(height*dpr), QImage.Format.Format_ARGB32_Premultiplied)
    frame.setDevicePixelRatio(dpr)
    frame.fill(QColor("#161b21"))
    with QPainter(frame) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(QRectF(0, max(0, -viewport_y), width,
                                 min(height, SCREEN.height()-viewport_y)-max(0, -viewport_y)))
        painter.fillRect(QRectF(0, 0, width, height), QColor("#e9eef2"))
        host = QRectF(owner_rect).translated(-SCREEN.left(), -viewport_y)
        painter.fillRect(host, QColor("#d0d8df"))
        painter.drawImage(QPointF(visual.x()-SCREEN.left(), visual.y()-viewport_y), snapshot)
        painter.setClipping(False)
        painter.fillRect(QRectF(0, 120, width, 40), QColor("#161b21"))
        painter.setPen(QColor("#dae1e7"))
        painter.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 9))
        painter.drawText(QRectF(12, 127, 636, 25), Qt.AlignmentFlag.AlignVCenter,
                         f"{mode.title()} / {seconds:.3f}s / frame {frame_index} / DPR {dpr:g}")
    return frame, paint_ms


def run_case(app, output, mode, dpr, rate):
    fixture = FinderBarSurfaceTests()
    fixture.app = app
    material = FinderGlassMaterial(QColor("#e5ebef"), QColor("#d0d8df"), QColor("#b8c3cc"),
                                   88, 166, 50, 24., 11, "offline-fixture")
    # Block constructor-time material discovery/screen sampling as well.
    with patch("local.island_stack_visual.read_finder_material", return_value=material):
        fixture.setUp()
    visual = fixture.visual
    visual.high_quality_font_rendering = True
    visual.resize(660, 460)
    visual.bar_hide_follow_enabled = True
    visual.finder_bar_style = "fade"
    visual.taskbar_auto_hide_mode = "auto"
    visual.taskbar_position_ratio = .30
    visual.music_ambience_style = "system"
    visual._music_system_dark = False
    visual._solid_album_palette_async_enabled = False
    visual.taskbar_material_color = QColor("#d0d8df")
    visual.finder_material.mid_color = QColor(visual.taskbar_material_color)
    visual._taskbar_drop_hint_visible = False
    screen = SimpleNamespace(name=lambda: "fixture", geometry=lambda: QRect(SCREEN),
                             availableGeometry=lambda: QRect(-660, 0, 660, 320), devicePixelRatio=lambda: dpr)
    state = {"now": START, "offset": 0, "fullscreen": False}
    base = QRectF(-660, 0 if mode == "finder" else 320, 660, 24 if mode == "finder" else 40)
    owner = lambda: QRectF(base).translated(0, state["offset"])
    snapshots = []
    redirects = []
    animation_redirects = []
    real_retarget = visual._retarget_bar_visibility
    real_animate = visual._animate_taskbar_follow_to_pos
    animations = {}

    def retarget(visible, now):
        before = visual._bar_visibility_opacity(now)
        velocity = visual._bar_visibility_velocity
        previous_target = getattr(visual, "_bar_visibility_target", 1.)
        real_retarget(visible, now)
        redirects.append({"time": round(now-START, 6), "visible": visible,
                          "value_before": before, "velocity_before": velocity, "target_before": previous_target,
                          "value_jump": abs(visual._bar_visibility_value-before),
                          "velocity_jump": abs(visual._bar_visibility_velocity-velocity)})

    def animate(target):
        before = QPoint(visual.pos())
        result = real_animate(target)
        animation = getattr(visual, "taskbar_follow_animation", None)
        if animation is not None and id(animation) not in animations:
            animations[id(animation)] = state["now"]
            animation_redirects.append({"time": round(state["now"]-START, 6),
                                       "position_jump": (visual.pos()-before).manhattanLength()})
        return result

    visual._retarget_bar_visibility = retarget
    visual._animate_taskbar_follow_to_pos = animate
    visual._refresh_finder_bar_material = lambda *args, **kwargs: None
    visual._update_taskbar_material_for_screen = lambda *args, **kwargs: None
    # The song data is a fixed offline provider snapshot; executing the actual
    # tick must not replace it with EmptyProvider's intentionally empty track.
    visual._refresh_media_data = lambda _now: False
    visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {"auto_hide": True, "rect": QRectF(base), "edge": "bottom"}
    visual._windows_taskbar_rect_for_screen = lambda _screen: owner()
    visual._detect_external_scene_suppression = lambda: ((True, "fullscreen") if state["fullscreen"] else (False, ""))
    media_fields = ("media_is_playing", "media_position_seconds", "media_position_sync_at", "media_track_id")
    playback_before = {key: getattr(visual, key) for key in media_fields}
    drag_before = [bool(getattr(visual, key, False)) for key in ("dragging", "drag_candidate")]
    frames = []
    selected = {round(value*rate) for value in (0, .1833, .275, .3917, .4333, .4667, .4917, .5167,
                                                .5417, .6667, .8, .875, 1., 1.1, 1.5083, 1.7)}
    retained_mode = None
    try:
        with ExitStack() as stack:
            for name in ("screens", "screenAt", "primaryScreen"):
                value = [screen] if name == "screens" else screen
                stack.enter_context(patch("local.island_stack_visual.QGuiApplication."+name, return_value=value))
            stack.enter_context(patch("local.island_stack_visual.time.monotonic", side_effect=lambda: state["now"]))
            stack.enter_context(patch("local.island_stack_visual.read_finder_material", return_value=visual.finder_material))
            stack.enter_context(patch("local.island_stack_visual.QCursor.pos", return_value=QPoint(-2000, -2000)))
            stack.enter_context(patch("local.island_stack_visual.QGuiApplication.mouseButtons", return_value=Qt.MouseButton.NoButton))
            if mode == "finder":
                # Windows/Qt retain global monitor origins while scaling sizes.
                resting = [-660, 0, -660+660*dpr, 24*dpr]
                snapshot = dict(fixture.bar, monitor_name="fixture", physical_monitor_rect=[-660, 0, -660+660*dpr, 360*dpr],
                                physical_resting_rect=resting, physical_rect=resting, visible=True, window_visible=True)
                visual.set_finder_bar_presence([snapshot])
                assert visual._finder_bar_active(), "Finder fixture did not map to its intended screen"
                current_snapshot = [snapshot]
                stack.enter_context(patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda _old: current_snapshot[0]))
            else:
                visual.mydockfinder_running = False
                visual.finder_bar_enabled = False
                visual.finder_mode = False
                visual.location_mode = "tasker"
            # An offscreen virtual window exercises the same isVisible() branch
            # as the real app, including QPropertyAnimation follow redirection.
            visual.show()
            # Settle only the ordinary initial-content spring before measuring
            # owner motion. This does not skip any hide/reveal transition.
            warm_count = round(rate*.5)
            visual.last_frame_at = START-.5-1/rate
            visual.last_tick_at = START-.5-1/rate
            for warm in range(warm_count):
                state["now"] = START-.5+warm/rate
                visual._tick()
                draw_frame(visual, owner(), dpr, mode, -.5+warm/rate, -warm_count+warm)
            initial_pos = None
            for index in range(round(DURATION*rate)+1):
                seconds = index/rate
                state["now"] = START+seconds
                fraction = owner_fraction(seconds)
                state["offset"] = round((-24 if mode == "finder" else 40)*fraction)
                state["fullscreen"] = .1 <= seconds < 1.5
                animation = getattr(visual, "taskbar_follow_animation", None)
                if animation is not None:
                    began = animations.setdefault(id(animation), state["now"])
                    animation.setCurrentTime(max(0, round((state["now"]-began)*1000)))
                if mode == "finder":
                    current_snapshot[0] = dict(snapshot, physical_rect=[resting[0], state["offset"]*dpr, resting[2], (state["offset"]+24)*dpr],
                                               visible=state["offset"] == 0, window_visible=state["offset"] > -24)
                visual._tick()
                if mode == "finder":
                    target_y = float(state["offset"])
                else:
                    target = visual._taskbar_follow_target_for_screen(screen, base)
                    target_y = float(target.window_pos.y())
                scale, opacity, hidden, edge = visual._scene_transition_params(state["now"])
                if initial_pos is None:
                    initial_pos = QPoint(visual.pos())
                if seconds >= 1.5:
                    retained_mode = visual.location_mode
                frame, paint_ms = draw_frame(visual, owner(), dpr, mode, seconds, index)
                main_visual = next((item for item in visual._build_visuals() if item.z_index == 10), None)
                painted_cover = QRectF(getattr(visual, "music_cover_rect", QRectF()))
                if index in selected:
                    path = output/f"{mode}-{rate}hz-dpr{dpr:g}-{index:03}.png"
                    assert frame.save(str(path))
                    frames.append((frame, str(path)))
                record = {"frame": index, "time": round(seconds, 6), "owner_rect": rect_values(owner()),
                          "owner_offset": state["offset"], "island_pos": [visual.x(), visual.y()],
                          "target_y": target_y, "target_error_px": abs(visual.y()-target_y)*dpr,
                          "owner_displacement_error_px": abs((visual.y()-initial_pos.y())-state["offset"])*dpr,
                          "opacity": opacity, "scale": scale, "edge_slide": edge, "paint_ms": paint_ms,
                          "suppressed": bool(visual.scene_suppressed), "input_passthrough": bool(visual.input_passthrough_enabled),
                          "mode": visual.location_mode, "fullscreen": state["fullscreen"]}
                record["main_target_local_rect"] = rect_values(main_visual.rect) if main_visual is not None else None
                record["painted_cover_local_rect"] = rect_values(painted_cover)
                record["native_mask_bounds"] = rect_values(QRectF(visual.mask().boundingRect()))
                previous = snapshots[-1] if snapshots else record
                record["owner_step_physical_px"] = (record["owner_offset"]-previous["owner_offset"])*dpr
                record["island_step_physical_px"] = (record["island_pos"][1]-previous["island_pos"][1])*dpr
                record["step_residual_physical_px"] = abs(record["island_step_physical_px"]-record["owner_step_physical_px"])
                snapshots.append(record)
                assert {key: getattr(visual, key) for key in media_fields} == playback_before, "Follow changed playback clock/track"
                assert [bool(getattr(visual, key, False)) for key in ("dragging", "drag_candidate")] == drag_before, "Follow changed drag/input state"
                assert record["input_passthrough"] == record["suppressed"], "Suppression and input capture disagree"
                assert scale == 1. and edge == 0., "Bar follow introduced a second edge animation"
    finally:
        visual._stop_taskbar_position_animation()
        fixture.tearDown()
    montage = QImage(round(1320*dpr), round(math.ceil(len(frames)/2)*160*dpr), QImage.Format.Format_ARGB32_Premultiplied)
    montage.setDevicePixelRatio(dpr)
    montage.fill(QColor("#161b21"))
    with QPainter(montage) as painter:
        for index, (frame, _path) in enumerate(frames):
            painter.drawImage(QRectF((index%2)*660, (index//2)*160, 660, 160), frame)
    contact = output/f"{mode}-{rate}hz-dpr{dpr:g}-contact.png"
    assert montage.save(str(contact))
    result = {"mode": mode, "dpr": dpr, "sample_hz": rate, "frame_count": len(snapshots),
              "target_error_max_physical_px": max(row["target_error_px"] for row in snapshots),
              "owner_displacement_error_max_physical_px": max(row["owner_displacement_error_px"] for row in snapshots),
              "owner_step_residual_max_physical_px": max(row["step_residual_physical_px"] for row in snapshots),
              "opacity_redirect_max_jump": max((row["value_jump"] for row in redirects), default=0.),
              "velocity_redirect_max_jump": max((row["velocity_jump"] for row in redirects), default=0.),
              "animation_redirect_max_logical_px_jump": max((row["position_jump"] for row in animation_redirects), default=0.),
              "paint_p95_ms": percentile([row["paint_ms"] for row in snapshots]),
              "paint_median_ms": statistics.median(row["paint_ms"] for row in snapshots),
              "playback_fields_unchanged": playback_before, "input_passthrough_matches_suppression": True,
              "mode_after_fullscreen": retained_mode, "opacity_redirects": redirects,
              "position_animation_redirects": animation_redirects, "contact_sheet": str(contact),
              "frames": [path for _frame, path in frames], "trajectory": snapshots}
    result["checks"] = {
        "target_within_one_logical_pixel": result["target_error_max_physical_px"] <= dpr,
        "owner_motion_within_one_logical_pixel": result["owner_displacement_error_max_physical_px"] <= dpr,
        "opacity_redirect_continuous": result["opacity_redirect_max_jump"] <= 1e-12,
        "velocity_redirect_continuous": result["velocity_redirect_max_jump"] <= 1e-12,
        "position_redirect_continuous": result["animation_redirect_max_logical_px_jump"] == 0,
        "fullscreen_exit_keeps_mode": retained_mode == mode,
        "owner_hide_and_reveal_occurred": len(redirects) >= 4 and any(row["suppressed"] for row in snapshots)
            and not snapshots[-1]["suppressed"] and snapshots[-1]["opacity"] == 1.,
        "midflight_opacity_reversal_exercised": any(.05 < row["value_before"] < .95 for row in redirects),
    }
    return result


def verify(output):
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "offscreen":
        raise RuntimeError("Use QT_QPA_PLATFORM=offscreen; this verifier never opens real desktop windows")
    IslandStackVisual.load_embedded_lyric_fonts()
    app.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
    cases = [run_case(app, output, mode, dpr, rate) for rate in (60, 120) for dpr in (1., 2.) for mode in ("finder", "tasker")]
    report = {"scope": "Native Qt offscreen QWidget paintEvent and complete production _tick; fixed offline song/provider snapshot; synthetic OS owner rectangles at 60/120 Hz; no physical input/audio",
              "limitations": "Physical MyDockFinder/Explorer polling jitter, DWM composition, display smoothness and hardware playback remain unverified.",
              "paint_timing_scope": "QWidget.render through production paintEvent, excluding image composition/file writes/OS bar probes",
              "owner_error_definition": "Island displacement minus owner displacement from first visible frame; includes existing Tasker centering lift. Target error separately measures production follow target.",
              "cases": cases}
    (output/"trajectory.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = [{key: case[key] for key in ("mode", "dpr", "sample_hz", "frame_count", "target_error_max_physical_px",
               "owner_displacement_error_max_physical_px", "opacity_redirect_max_jump", "paint_p95_ms", "mode_after_fullscreen")}
               for case in cases]
    (output/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    failed = [(case["mode"], case["dpr"], case["sample_hz"], key) for case in cases for key, passed in case["checks"].items() if not passed]
    if failed:
        raise AssertionError(f"Follow acceptance failed; inspect trajectory.json: {failed}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(verify(options.output.resolve()), indent=2))
