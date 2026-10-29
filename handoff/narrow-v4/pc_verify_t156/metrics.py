"""Analyse saved physical captures only; no Qt, playback or settings access."""
from __future__ import annotations
import json
from pathlib import Path

def seam_scan(image):
    rgb = image.convert("RGB")
    x = rgb.width // 2
    luma = [(.2126 * r + .7152 * g + .0722 * b) / 255
            for r, g, b in (rgb.getpixel((x, y)) for y in range(rgb.height))]
    steps = [abs(b - a) for a, b in zip(luma, luma[1:])]
    maximum = max(steps, default=0.)
    return dict(width=rgb.width, height=rgb.height, x=x,
                veil_seam_max_step=maximum, max_step_row=steps.index(maximum) + 1 if steps else None,
                threshold=3 / 255, status="PASS" if maximum < 3 / 255 else "FAIL",
                caveat="Raw live centerline includes artwork and lyric ink; inspect saved PNG")

def chrome_pixels(image, state):
    """Save a physical contrast signal per actual hit rect, plus visibility state."""
    from PIL import ImageStat
    geometry = state["geometry"]
    sx, sy = image.width / geometry[2], image.height / geometry[3]
    patches = {}
    for key, (x, y, width, height) in (state.get("rects") or {}).items():
        bbox = (max(0, round(x * sx)), max(0, round(y * sy)),
                min(image.width, round((x + width) * sx)), min(image.height, round((y + height) * sy)))
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            patches[key] = dict(nonempty=False, pixel_variation=0.)
        else:
            variation = sum(ImageStat.Stat(image.crop(bbox)).stddev)
            patches[key] = dict(nonempty=variation > 0, pixel_variation=variation)
    return dict(hold=state.get("resize_chrome_hold"), progress=state.get("visibility"), patches=patches,
                diagnostic="Patch contrast is a signal, not a foreground/background pixel classifier")

def transition_timing(history):
    unique = {tuple((row.get(key) for key in ("state", "at"))): row for row in history}
    rows = []
    for row in sorted(unique.values(), key=lambda row: row["at"]):
        if not rows or row["state"] != rows[-1]["state"]:
            rows.append(row)
    transitions = []
    for index, row in enumerate(rows):
        if row["state"] not in {"SPLITTING", "MERGING"}:
            continue
        duration = row["duration"]
        following = rows[index + 1] if index + 1 < len(rows) else None
        elapsed = following["at"] - row["at"] if following else None
        transitions.append(dict(state=row["state"], expected_seconds=duration,
            actual_seconds=elapsed, fast=duration <= .25,
            status=("UNVERIFIED" if elapsed is None else
                "PASS" if .19 <= duration <= .501 and elapsed >= duration - .03 else "FAIL")))
    return dict(transitions=transitions,
        slow_seen=any(row["expected_seconds"] >= .45 for row in transitions),
        fast_seen=any(row["fast"] for row in transitions))

def analyse(out):
    from PIL import Image
    out = Path(out)
    native = json.loads((out / "native-result.json").read_text(encoding="utf-8"))
    observed = json.loads((out / "screen-observer.json").read_text(encoding="utf-8"))
    scans = []
    history = list(native.get("width_transition_history", []))
    drags = []
    readiness_valid = True
    for frame in observed["frames"]:
        readiness_valid &= frame.get("attach_mode") in {"full", "degraded"} and isinstance(frame.get("source_lines"), int)
        history.extend(frame.get("width_transition_history") or [])
        if frame["phase"] == "width-sweep" or Path(frame["file"]).stem in {"seam-360x640", "seam-480x800"}:
            with Image.open(out / frame["file"]) as shot:
                scans.append(dict(file=frame["file"], geometry=frame.get("geometry"), dpr=frame.get("dpr"), **seam_scan(shot)))
        if frame["phase"] in {"width-drag", "fast-drag"}:
            drags.append(frame)
    timing = transition_timing(history)
    for name, data in (("seam-scan.json", dict(samples=scans)), ("transition-timing.json", timing),
                       ("resize-chrome.json", dict(frames=drags))):
        (out / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
    hold_frames = [row for row in drags if row.get("resize_chrome_hold")]
    chrome_good = bool(hold_frames) and all(row.get("visibility") == 1.
        and row.get("chrome_pixels", {}).get("patches")
        and all(patch["nonempty"] for patch in row["chrome_pixels"]["patches"].values())
        for row in hold_frames)
    checks = [dict(id="A1", scope="PC", status=("UNVERIFIED" if not scans else
        "PASS" if all(row["status"] == "PASS" for row in scans) else "FAIL")),
        dict(id="A4", scope="PC", status="PASS" if chrome_good else "FAIL",
             note="Physical patch contrast and held visibility; human confirmation of every icon remains required"),
        dict(id="A6/A7", scope="PC", status=("FAIL" if any(row["status"] == "FAIL" for row in timing["transitions"])
             else "PASS" if timing["slow_seen"] and timing["fast_seen"] else "UNVERIFIED")),
        dict(id="A13", scope="PC", status="PASS" if readiness_valid else "FAIL"),
        dict(id="A14", scope="PC", status="UNVERIFIED", note="Review PNGs on actual display")]
    report = dict(scope="PC", checks=checks, attach_mode=native.get("attach_mode"),
                  word_click=native.get("word_click"), physical_review="UNVERIFIED")
    (out / "acceptance-table.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "acceptance-table.md").write_text("| Gate | Scope | Result |\n| --- | --- | --- |\n" +
        "".join(f"| {row['id']} | {row['scope']} | {row['status']} |\n" for row in checks), encoding="utf-8")
    return report
