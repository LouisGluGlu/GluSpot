"""Capture only the probe's native client area with Pillow ImageGrab."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows desktop observer only")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    from PIL import ImageChops, ImageGrab, ImageStat

    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    state_file = args.out / "probe-state.json"
    frames = args.out / "frames"
    frames.mkdir(exist_ok=True)
    report = dict(capture="PIL.ImageGrab physical desktop client-area crop",
                  requested_hz=30, frames=[], errors=[], diagnostic_only=True)
    last = None
    seen_tags = set()
    started = time.monotonic()
    previous = None
    try:
        while time.monotonic() - started < 180:
            tick = time.monotonic()
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(.02)
                continue
            if state.get("done"):
                break
            tag = state.get("capture_tag")
            unique_tag = bool(tag and tag not in seen_tags)
            if not unique_tag and not state.get("capture_sequence"):
                previous = None
                time.sleep(.02)
                continue
            hwnd = int(state["hwnd"])
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != state["probe_pid"] or not user32.IsWindowVisible(hwnd):
                raise RuntimeError("Capture target is not the visible probe process")
            rect = wintypes.RECT()
            origin = wintypes.POINT(0, 0)
            if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
                raise RuntimeError("Cannot obtain native client bounds")
            bbox = (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
            if rect.right <= 0 or rect.bottom <= 0:
                raise RuntimeError("Probe client bounds are empty")
            virtual = (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77))
            virtual_end = (virtual[0] + user32.GetSystemMetrics(78),
                           virtual[1] + user32.GetSystemMetrics(79))
            if (bbox[0] < virtual[0] or bbox[1] < virtual[1]
                    or bbox[2] > virtual_end[0] or bbox[3] > virtual_end[1]):
                raise RuntimeError("Probe client is partly outside the physical desktop")
            shot = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
            label = tag if unique_tag else f'{state["phase"]}-{len(report["frames"]):04d}'
            path = frames / (label + ".png")
            shot.save(path)
            if unique_tag:
                seen_tags.add(tag)
            sample = dict(file="frames/" + path.name, phase=state["phase"],
                          at=round(time.monotonic() - started, 4), bbox=list(bbox),
                          foreground=user32.GetForegroundWindow() == hwnd,
                          visibility=state.get("visibility"), narrow=state.get("narrow"),
                          rects=state.get("rects"), geometry=state.get("geometry"),
                          width_chrome_progress=state.get("width_chrome_progress"),
                          width_chrome_active=state.get("width_chrome_active"),
                          width_layout_fraction=state.get("width_layout_fraction"),
                          pixel_variation=round(sum(ImageStat.Stat(shot).stddev), 4))
            for key in ("attach_mode", "source_lines", "resize_chrome_hold", "dpr",
                        "width_transition_state", "width_transition_duration",
                        "width_transition_history", "width_marks"):
                sample[key] = state.get(key)
            if state["phase"] in {"width-drag", "fast-drag"}:
                from metrics import chrome_pixels
                sample["chrome_pixels"] = chrome_pixels(shot, state)
            if previous is not None and previous[0] == state["phase"] and previous[1].size == shot.size:
                difference = ImageChops.difference(previous[1], shot)
                height = shot.height
                for name, top, bottom in (("top", 0, round(height * .34)),
                                          ("bottom", round(height * .66), height)):
                    sample[name + "_mad"] = round(
                        sum(ImageStat.Stat(difference.crop((0, top, shot.width, bottom))).mean) / 3, 5)
            report["frames"].append(sample)
            previous = (state["phase"], shot)
            last = state
            time.sleep(max(0., 1 / 30 - (time.monotonic() - tick)))
        else:
            raise RuntimeError("Probe did not finish before observer deadline")
    except Exception as error:
        report["errors"].append(type(error).__name__ + ": " + str(error))
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["tags"] = sorted(seen_tags)
        report["capture_count"] = len(report["frames"])
        report["all_foreground"] = bool(report["frames"]) and all(
            frame["foreground"] for frame in report["frames"])
        report["all_nonempty"] = bool(report["frames"]) and all(
            frame["pixel_variation"] > 1 for frame in report["frames"])
        report["last_phase"] = last.get("phase") if last else None
        report["passed"] = bool(report["capture_count"] and report["all_foreground"]
                                and report["all_nonempty"] and not report["errors"])
        (args.out / "screen-observer.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
