"""Attach-only native chrome/mode/back probe with a separate ImageGrab observer.

Uses the established run_live temporary-profile entry. No transport, seek,
collection mutation, queue selection, player launch or player restore is allowed.
"""
from __future__ import annotations

import argparse
from collections import deque
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

REPO = Path(r"E:\grok-gluspot\_wt_capture2")
PYTHON = Path(r"E:\gpt-island\.venv\Scripts\python.exe")


def port_present():
    try:
        with socket.create_connection(("127.0.0.1", 13371), timeout=.5):
            return True
    except OSError:
        return False


def visible_gluspot():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    @callback_type
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title, kind = ctypes.create_unicode_buffer(1024), ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, len(title))
            user32.GetClassNameW(hwnd, kind, len(kind))
            if kind.value.startswith("Qt") and title.value.startswith("GluSpot"):
                found.append(int(hwnd))
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows(visit, 0)
    return found


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def attach_readiness(*, playing, duration, position_source, source_lines,
                     position, initial_position, elapsed):
    """Keep the advancing-playback gate independent of lyric availability."""
    playback_ready = bool(playing and duration > 0
                          and position_source not in {"", "none", "capture-unavailable"})
    lyric_ready = bool(source_lines)
    advancing = (initial_position is not None and position - initial_position >= .1)
    if playback_ready and advancing and lyric_ready:
        return "full"
    if elapsed >= 45:
        if playback_ready and advancing:
            return "degraded"
        raise RuntimeError("No advancing real playback with source lyrics; physical acceptance unavailable")
    return None


def main(*, observer_script=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt" or Path(sys.executable).resolve() != PYTHON.resolve():
        raise SystemExit("Use the fixed PC Python through run_pc.ps1")
    args.out.mkdir(parents=True, exist_ok=True)
    state = dict(schema=1, passed=False, errors=[], phase="preflight", callbacks=[],
                 frames_expected=[], mode_cycles={}, width_samples=[], forbidden_actions=[],
                 entry="run_live([]), attach_only", settings_scope="temporary profile only",
                 capture="PIL.ImageGrab physical desktop", model="sol",
                 attach_mode="waiting", source_lines=0,
                 word_click=dict(status="UNVERIFIED", reason="Attach-only probe forbids lyric seek"))
    result_path = args.out / "native-result.json"
    observer = None
    original_bootstrap = None
    app_module = None
    started = time.monotonic()
    try:
        if not port_present():
            raise RuntimeError("NetEase 13371 absent; stop without restarting it")
        if visible_gluspot():
            raise RuntimeError("Close only the previous GluSpot view before running the bundle")
        os.chdir(REPO)
        sys.path[:0] = [str(REPO), str(REPO / "_demos/narrow3")]
        from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRectF, Qt, QTimer
        from PySide6.QtGui import QCursor, QFont, QMouseEvent
        from PySide6.QtTest import QTest
        from api.capture.netease import NetEaseLocator, _windows_processes
        from _demos.narrow3.live import run_live
        from local.music_surface_layout import CONTENT_LAYOUTS
        from local.music_lyric_fit import cjk_unit, fits, required_width
        from local.music_lyric_motion import GLYPH_FLOAT_LIFT
        from local.music_surface_window import MUSIC_WINDOW_ASPECT_RATIO
        from local.settings_store import DEFAULT_SETTINGS
        import local.app as app_module

        async def denied(*_args, **_kwargs):
            raise RuntimeError("PC verification forbids NetEase launch/restore")

        NetEaseLocator.launch = NetEaseLocator.restore = denied

        def player_pids():
            return sorted(pid for pid, path in _windows_processes()
                          if "cloudmusic" in str(path).casefold())

        state["player_pids_before"] = player_pids()
        state["tip"] = subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
        original_bootstrap = app_module.bootstrap
        observer = subprocess.Popen(
            [str(PYTHON), "-X", "utf8", str(observer_script or Path(__file__).with_name("screen_observer.py")),
             "--out", str(args.out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def instrumented_bootstrap(**kwargs):
            runtime = original_bootstrap(**kwargs)
            app, window, owner = runtime.app, runtime.view_window, runtime.window
            state["phase"] = "waiting_playback"
            capture_state = dict(capture_sequence=False, capture_tag=None, done=False)
            target_widget = [window]
            poll_start = [None]
            actions = deque()

            def fail(error):
                state["errors"].append(type(error).__name__ + ": " + str(error))
                state["phase"] = "error"
                write(result_path, state)
                app.quit()

            def guarded(function):
                def invoke():
                    try:
                        function()
                    except Exception as error:
                        fail(error)
                return invoke

            def geometry():
                return list(window.geometry().getRect())

            def groups():
                if window.narrow:
                    return {key: rect for group in window.glass_groups() for key, rect in group.items()}
                return window._top_control_hover_group(QRectF(window.rect()))

            def publish():
                widget = target_widget[0]
                transition = getattr(window, "_width_transition", None)
                marks = getattr(window, "_width_resize_marks", None)
                message = dict(capture_state, phase=state["phase"], probe_pid=os.getpid(),
                               attach_mode=state["attach_mode"], source_lines=len(window.source_lines()),
                               hwnd=int(widget.winId()), geometry=geometry(),
                               narrow=bool(window.narrow), visibility=float(window.visibility.progress),
                               width_chrome_progress=getattr(window, "_width_chrome_progress", None),
                               width_chrome_active=bool(getattr(window, "_width_chrome_active", False)),
                               width_layout_fraction=getattr(window, "_width_layout_fraction", None),
                               resize_chrome_hold=bool(getattr(window, "_resize_chrome_hold", False)),
                               dpr=float(window.devicePixelRatioF()),
                               width_transition_state=transition.state if transition is not None else None,
                               width_transition_duration=transition.duration if transition is not None else None,
                               width_transition_history=getattr(window, "_width_transition_history", []),
                               width_marks=(dict(minimum=marks.minimum, breakpoint=marks.breakpoint,
                                   maximum=marks.maximum, height=marks.height,
                                   current=marks.mark(window.width()), split_unreachable=marks.split_unreachable)
                                   if marks is not None else None),
                               rects={key: list(rect.getRect()) for key, rect in groups().items()})
                write(args.out / "probe-state.json", message)

            publish_timer = QTimer(window)
            publish_timer.timeout.connect(guarded(publish))
            publish_timer.start(25)

            def forbidden(name):
                def action(*_args, **_kwargs):
                    state["forbidden_actions"].append(name)
                    raise RuntimeError("Probe unexpectedly attempted " + name)
                return action

            for name in ("_send_media_command", "_seek_to_lyric", "_select_playback_queue",
                         "_toggle_personal_dj"):
                setattr(window, name, forbidden(name))
            # The owned=False top-left slot is allowed to dispatch collection;
            # record that action without writing favorites or opening a playlist.
            state["suppressed_collection"] = []
            window._activate_song_collection = lambda *a, **kw: state["suppressed_collection"].append(state["phase"])
            for name in ("_music_login_callback", "_music_library_back_callback"):
                original = getattr(owner, name)

                def observed(*a, _original=original, _name=name, **kw):
                    state["callbacks"].append(_name)
                    return _original(*a, **kw)

                setattr(owner, name, observed)
            original_toggle = window.toggle_wide
            state["toggle_calls"] = []

            def observed_toggle(*a, **kw):
                state["toggle_calls"].append(dict(phase=state["phase"]))
                return original_toggle(*a, **kw)

            window.toggle_wide = observed_toggle
            window.hints = False

            def reveal():
                window._reset_control_idle()
                QTest.mouseMove(window, QPoint(window.width() // 2, window.height() // 2))
                QTest.mouseMove(window, QPoint(window.width() // 2, 35))
                window._pointer_active_until = time.monotonic() + 3
                window.visibility.advance(time.monotonic(), True, True, window.mode == "playlist")

            def hide():
                QCursor.setPos(window.mapToGlobal(QPoint(-30, -30)))
                window._pointer_active_until = 0
                window._pointer_hover = window._focus_reveal = False
                window.shelf = window.footer_hover = False
                window.visibility.idle_since = time.monotonic() - window.visibility.IDLE_DELAY
                window.visibility.advance(time.monotonic(), False, False, window.mode == "playlist")

            def capture(label):
                capture_state["capture_tag"] = label
                state["frames_expected"].append(label)
                publish()

            def add(delay, phase, function):
                actions.append((delay, phase, function))

            def advance():
                if not actions:
                    finish()
                    return
                delay, phase, function = actions.popleft()
                state["phase"] = phase
                function()
                publish()
                QTimer.singleShot(delay, guarded(advance))

            def configure(width, height):
                window.setMinimumHeight(540 if height == 540 else 560)
                window.resize(width, height)
                window.move(60, 60)
                window.show()
                window.raise_()
                window.activateWindow()
                reveal()

            def mode_start(label, width):
                configure(width, 640)
                window.set_content_layout("lyrics", animate=False)
                window._finish_content_layout()
                state["mode_cycles"][label] = []

            def mode_frame(label, expected):
                actual = window._effective_content_layout()
                if actual != expected:
                    raise RuntimeError("Layout cycle mismatch: " + label + "/" + actual)
                state["mode_cycles"][label].append(actual)
                reveal()
                capture(label + "-" + actual)

            def mode_click():
                rect = groups().get("content_layout")
                if rect is None:
                    raise RuntimeError("Layout button has no visible hit rectangle")
                QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier, rect.center().toPoint())
                reveal()

            def fixed_left():
                point = QPointF(34, 34)
                before = geometry()
                action = window._live_action_at(point)
                toggles, callbacks = len(state["toggle_calls"]), len(state["callbacks"])
                QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier, point.toPoint())
                state["owned_false_fixed_left"] = dict(before=before, after=geometry(), action=action,
                    back_available=window._library_back_available(),
                    passed=(not window._library_back_available() and action not in {"return", "library"}
                            and before == geometry() and window.width() != 960 and window.narrow
                            and len(state["toggle_calls"]) == toggles and len(state["callbacks"]) == callbacks))
                capture("owned-false-fixed-left-after")

            def right_return():
                rect = groups()["return"]
                state["right_return_hit"] = window._live_action_at(rect.center())
                if rect.left() <= window.width() / 2:
                    raise RuntimeError("Intentional window-mode button moved onto the left")
                QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier, rect.center().toPoint())

            def check_right_return():
                state["right_return_pass"] = (state["right_return_hit"] == "return"
                    and (window.width(), window.height()) == (960, 540) and not window.narrow)
                capture("intentional-right-return-wide")

            def width_sample(width):
                window.resize(width, 640)
                record_width_sample(width, "resize")
                QTimer.singleShot(40, guarded(lambda: capture("width-sweep-" + str(width))))

            def record_width_sample(width, method):
                state["width_samples"].append(dict(width=width, narrow=bool(window.narrow),
                    actual_width=window.width(), height=window.height(), method=method,
                    snapshots_null=window._layout_snapshot.isNull() and window._layout_target_snapshot.isNull(),
                    mode=window._effective_content_layout(),
                    width_chrome_progress=getattr(window, "_width_chrome_progress", None),
                    width_layout_fraction=getattr(window, "_width_layout_fraction", None),
                    at=round(time.monotonic() - started, 4)))
                if window._effective_content_layout() != "lyrics":
                    raise RuntimeError("Width sweep changed the selected content layout")

            def fit_at(width, height, previous):
                surface = QRectF(0., 0., width, height)
                scale = window._surface_scale(surface)
                font = window._surface_font(window.LYRIC_PIXEL_SIZE * scale, QFont.Weight.Bold)
                _content, _cover, column = window._wide_content_column_geometry(surface)
                dpr = window.devicePixelRatioF()
                unit, required = cjk_unit(font, dpr=dpr), required_width(font, dpr=dpr)
                narrow = not fits(column.width(), font, previous=previous, dpr=dpr)
                return dict(width=width, height=height, narrow=narrow, font_pixels=font.pixelSize(),
                    column_width=column.width(), required_width=required, cjk_unit=unit,
                    hysteresis_half_glyph=unit * .5, dpr=dpr)

            def content_breakpoints():
                tables = []
                for height in (540, 640, 900):
                    previous = False
                    entry = None
                    for width in range(1300, 329, -1):
                        measured = fit_at(width, height, previous)
                        previous = measured["narrow"]
                        if previous:
                            entry = measured
                            break
                    if entry is None:
                        raise RuntimeError("No measured content breakpoint in supported scan")
                    previous, exit_sample = True, None
                    for width in range(entry["width"], 1601):
                        measured = fit_at(width, height, previous)
                        previous = measured["narrow"]
                        if not previous:
                            exit_sample = measured
                            break
                    if exit_sample is None:
                        raise RuntimeError("No measured content breakpoint exit in supported scan")
                    tables.append(dict(height=height, entry=entry, exit=exit_sample,
                        ladder=[fit_at(width, height, None) for width in (1300, 960, 700, 483, 360)]))
                state["content_breakpoints"] = tables
                state["height_changes_breakpoint"] = len({row["entry"]["width"] for row in tables}) > 1
                return next(row for row in tables if row["height"] == 640)

            drag = {}

            def drag_event(kind, global_point, buttons):
                local = QPointF(window.mapFromGlobal(global_point))
                button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
                event = QMouseEvent(kind, local, QPointF(global_point), button, buttons,
                                    Qt.KeyboardModifier.NoModifier)
                QCoreApplication.sendEvent(window, event)

            def start_drag():
                drag["start_width"] = window.width()
                drag["origin"] = window.mapToGlobal(QPoint(window.width() - 2, window.height() // 2))
                drag["last"] = drag["origin"]
                drag_event(QEvent.Type.MouseButtonPress, drag["origin"], Qt.MouseButton.LeftButton)
                if "right" not in window._resize_edges:
                    raise RuntimeError("Edge press failed to enter the real right-resize input path")

            def drag_to(width):
                point = drag["origin"] + QPoint(width - drag["start_width"], 0)
                drag_event(QEvent.Type.MouseMove, point, Qt.MouseButton.LeftButton)
                drag["last"] = point
                record_width_sample(width, "Qt mouse edge drag")
                index = len(state["width_samples"])
                QTimer.singleShot(15, guarded(lambda: capture(f"width-drag-{index}-{width}")))

            def finish_drag():
                drag_event(QEvent.Type.MouseButtonRelease, drag["last"], Qt.MouseButton.NoButton)
                state["width_drag_input_released"] = not window._resize_edges and not window._moving
                samples = [sample for sample in state["width_samples"] if sample["method"] == "Qt mouse edge drag"]
                state["width_drag_pass"] = bool(
                    samples and {sample["narrow"] for sample in samples} == {False, True}
                    and all(sample["actual_width"] == sample["width"] and sample["snapshots_null"] for sample in samples))

            def drag_mark(mark):
                marks = window._width_resize_marks
                if marks is None:
                    raise RuntimeError("Width gesture did not publish its frozen marks")
                width = (marks.minimum + (mark - 10.) * (marks.breakpoint - marks.minimum) / 15.
                         if mark <= 25. else marks.breakpoint + (mark - 25.) * (marks.maximum - marks.breakpoint) / 25.)
                drag_to(round(width))

            def reading_evidence():
                retired = ("v2_music_surface_reading_strategy", "v2_music_surface_reading_lift_level")
                state["reading"] = dict(scheme="low_perception", lift=window._reading_lift_height(),
                    original_lift=GLYPH_FLOAT_LIFT,
                    retired_keys_absent=all(key not in DEFAULT_SETTINGS for key in retired),
                    strategy_accessor_absent=not hasattr(window, "_reading_strategy"),
                    gold_ring_method_absent=not hasattr(window, "_maybe_start_obo_reveal"),
                    gold_feedback_absent="obo_reveal" not in window._transport_feedback,
                    visible_word_targets=len(window._lyric_word_hit_rects))
                state["reading"]["passed"] = bool(
                    state["reading"]["lift"] == GLYPH_FLOAT_LIFT
                    and all(state["reading"][key] for key in (
                        "retired_keys_absent", "strategy_accessor_absent", "gold_ring_method_absent", "gold_feedback_absent")))
                capture("low-perception-original-lift")

            def enable_back():
                configure(483, 560)
                runtime.settings.set("v2_owned_playback_enabled", True)
                reveal()

            def click_back():
                state["owned_true_back_before"] = geometry()
                state["login_required"] = bool(window._library_login_required())
                rect = groups().get("library")
                if rect is None:
                    raise RuntimeError("Owned=True did not show the library-back control")
                state["owned_true_hit"] = window._live_action_at(rect.center())
                QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier, rect.center().toPoint())

            def capture_destination():
                destination = (runtime.playback_entry.window if state["login_required"]
                               else runtime.music_library.window)
                expected = "_music_login_callback" if state["login_required"] else "_music_library_back_callback"
                state["owned_true_back"] = dict(before=state["owned_true_back_before"], after=geometry(),
                    passed=(state["owned_true_hit"] == "library" and destination.isVisible()
                            and state["callbacks"] == [expected]
                            and geometry() == state["owned_true_back_before"]))
                target_widget[0] = destination
                destination.raise_()
                destination.activateWindow()
                capture("owned-true-back-destination")

            def finish():
                capture_state["capture_sequence"] = False
                state["player_pids_after"] = player_pids()
                state["original_player_pids_retained"] = bool(state["player_pids_before"]) and set(
                    state["player_pids_before"]).issubset(state["player_pids_after"])
                state["port_13371_after"] = port_present()
                state["diagnostics"] = window.diagnostic_snapshot()
                state["width_transition_history"] = getattr(window, "_width_transition_history", [])
                state["phase"] = "done"
                publish_timer.stop()
                publish()
                write(result_path, state)
                app.quit()

            def begin():
                runtime.settings.set("v2_owned_playback_enabled", False)
                breakpoint = content_breakpoints()
                for label, width in (("narrow", 360), ("wide", 1100)):
                    add(700, label + "-setup", lambda label=label, width=width: mode_start(label, width))
                    for expected in ("lyrics", "playlist", "cover", "cover_lyrics"):
                        add(250, label + "-" + expected, lambda label=label, expected=expected: mode_frame(label, expected))
                        add(650, label + "-cycle", mode_click)
                add(700, "back-disabled-setup", lambda: configure(483, 560))
                add(250, "back-disabled-before", lambda: capture("owned-false-before"))
                add(300, "back-disabled-fixed-left", fixed_left)
                add(700, "intentional-right-return", right_return)
                add(250, "intentional-right-return-result", check_right_return)
                add(700, "slide-setup", lambda: configure(483, 640))
                add(750, "hide", lambda: (capture_state.update(capture_sequence=True), hide()))
                add(500, "hidden-steady", lambda: capture("hidden-steady"))
                add(750, "reveal", reveal)
                add(500, "revealed-steady", lambda: capture("revealed-steady"))
                add(250, "reading-evidence", reading_evidence)
                add(0, "width-sweep-setup", lambda: capture_state.update(capture_sequence=True))
                entry, exit_width = breakpoint["entry"]["width"], breakpoint["exit"]["width"]
                state["t156_breakpoint"] = window._width_breakpoint_width(height=640)
                low, high = max(330, entry - 70), exit_width + 70
                if high + 120 > window._available_geometry().width():
                    raise RuntimeError("Desktop too narrow for this DPI's breakpoint drag; use a larger work area")
                for width in (330, 400, round(state["t156_breakpoint"]), 460, 640,
                              high, exit_width + 2, entry + 2, entry, entry - 2, low,
                              entry - 2, entry + 2, exit_width - 2, exit_width, exit_width + 2, high):
                    add(600, "width-sweep", lambda width=width: width_sample(width))
                add(450, "width-drag-setup", lambda: configure(high, 640))
                add(40, "width-drag", start_drag)
                drag_widths = list(range(high, low - 1, -4)) + [low]
                drag_widths += list(range(low, high + 1, 4)) + [high]
                for width in drag_widths:
                    add(75, "width-drag", lambda width=width: drag_to(width))
                add(1100, "width-drag-settle", finish_drag)
                add(650, "seam-360x640", lambda: configure(360, 640))
                add(150, "seam-360x640", lambda: capture("seam-360x640"))
                add(650, "seam-480x800", lambda: configure(480, 800))
                add(150, "seam-480x800", lambda: capture("seam-480x800"))
                add(650, "fast-drag-setup", lambda: configure(330, 640))
                add(40, "fast-drag", start_drag)
                add(650, "fast-drag", lambda: drag_to(min(window._available_geometry().width() - 120, round(640 * MUSIC_WINDOW_ASPECT_RATIO))))
                add(650, "fast-drag-settle", finish_drag)
                add(800, "return-drag-setup", lambda: configure(330, 640))
                add(2500, "return-drag", start_drag)
                add(20, "return-drag", lambda: drag_mark(30))
                add(1300, "return-drag", lambda: drag_mark(18))
                add(650, "return-drag-settle", finish_drag)
                add(650, "back-enabled-setup", lambda: (capture_state.update(capture_sequence=False), enable_back()))
                add(250, "back-enabled-before", lambda: capture("owned-true-back-before"))
                add(800, "back-enabled-click", click_back)
                add(400, "back-enabled-destination", capture_destination)
                advance()

            def poll():
                elapsed = time.monotonic() - started
                playback_ready = (owner.media_is_playing and owner.media_duration_seconds > 0
                                  and owner.media_position_source not in {"", "none", "capture-unavailable"})
                source_lines = len(window.source_lines())
                state.update(playback_ready=bool(playback_ready), lyric_ready=bool(source_lines),
                             source_lines=source_lines)
                if playback_ready:
                    state.setdefault("playback_ready_after_seconds", round(elapsed, 3))
                if source_lines:
                    state.setdefault("lyric_ready_after_seconds", round(elapsed, 3))
                position = window.playback_position()
                if playback_ready and poll_start[0] is None:
                    poll_start[0] = position
                mode = attach_readiness(playing=owner.media_is_playing,
                    duration=owner.media_duration_seconds, position_source=owner.media_position_source,
                    source_lines=source_lines, position=position, initial_position=poll_start[0], elapsed=elapsed)
                if mode is not None:
                    timer.stop()
                    state["real_playback_ready"] = True
                    state["attach_mode"] = mode
                    if mode == "degraded":
                        state["word_click"] = dict(status="UNVERIFIED", reason="No source lyrics after 45 seconds; degraded attach")
                    state["position_source"] = owner.media_position_source
                    begin()

            timer = QTimer(window)
            timer.timeout.connect(guarded(poll))
            timer.start(100)
            QTimer.singleShot(125000, guarded(lambda: fail(RuntimeError("Native probe deadline exceeded"))))
            return runtime

        app_module.bootstrap = instrumented_bootstrap
        run_live([])
    except Exception as error:
        state["errors"].append(type(error).__name__ + ": " + str(error))
    finally:
        if app_module is not None and original_bootstrap is not None:
            app_module.bootstrap = original_bootstrap
        write(args.out / "probe-state.json", dict(done=True,
              attach_mode=state["attach_mode"], source_lines=state["source_lines"]))
        if observer is not None:
            try:
                state["observer_exit"] = observer.wait(timeout=15)
            except subprocess.TimeoutExpired:
                observer.terminate()
                observer.wait(timeout=5)
                state["errors"].append("Screen observer failed to stop")
        observer_path = args.out / "screen-observer.json"
        if observer_path.exists():
            observed = json.loads(observer_path.read_text(encoding="utf-8"))
            state["missing_frames"] = sorted(set(state["frames_expected"]) - set(observed["tags"]))
            state["physical_captures_pass"] = bool(observed["passed"] and not state["missing_frames"])
            state["sequence_frame_counts"] = {phase: sum(frame["phase"] == phase for frame in observed["frames"])
                                              for phase in ("reveal", "hide", "hidden-steady", "revealed-steady", "width-sweep", "width-drag")}
        state["modes_pass"] = all(set(cycle) == set(CONTENT_LAYOUTS)
            for cycle in state["mode_cycles"].values()) if state["mode_cycles"] else False
        state["passed"] = bool(state.get("real_playback_ready") and state["modes_pass"]
            and state.get("owned_false_fixed_left", {}).get("passed")
            and state.get("owned_true_back", {}).get("passed") and state.get("right_return_pass")
            and state.get("original_player_pids_retained") and state.get("port_13371_after")
            and state.get("physical_captures_pass") and not state["errors"] and not state["forbidden_actions"])
        state["passed"] = bool(state["passed"] and state.get("reading", {}).get("passed")
                               and state.get("height_changes_breakpoint") and state.get("width_drag_input_released")
                               and state.get("width_drag_pass"))
        state["physical_smoothness"] = "manual_review_required"
        state["width_drag"] = "content-derived sweep plus Qt mouse press/move/release through real edge-resize handlers; physical mouse feel remains manual"
        state["flicker_metrics"] = "screen-observer.json has raw consecutive top/bottom-band MAD; diagnostic only"
        state["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write(result_path, state)
    return 0 if state["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
