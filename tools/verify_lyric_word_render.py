"""Offline word-reading evidence through production parsers and Qt painters.

Run serially at each DPR, for example::

    python tools/verify_lyric_word_render.py --output .runtime/words-dpr1 --expect-dpr 1

Set QT_SCALE_FACTOR=2 for DPR 2. Optional ``--real-dir`` reads previously saved
NetEase, QQ QRC and AMLL JSON responses; no real lyric is bundled in this tool.
There is no network, account/settings access, audio engine or native playback.
Only the ignored output receives screenshots containing those local lyrics.
The JSON report contains hashes, clocks, counts and branch names, not lyrics.
This checks rendered frames; it cannot establish physical-display smoothness.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from contextlib import ExitStack
from dataclasses import asdict, replace
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteSnapshot
from api.lyric_timeline import lyric_timing_quality
from api.lyrics import LocalLyricsStore, LyricsMatch
from api.qrc import decrypt_qrc
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_presentation import INTRO_COUNTDOWN_TEXT
from local.music_lyric_reading import LyricReadingRenderer
from local.music_surface_window import MusicSurfaceWindow
import local.music_surface_window as surface_module


class _OfflineProvider:
    def get_audio_route_snapshot(self):
        return AudioRouteSnapshot(state="unavailable", capture_scope="owned-player")


class _Owner(QWidget):
    """Value-only owner; production player construction creates no audio here."""

    def __init__(self, match, title, artist):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self._music_windowed_preference = True
        self.music_intro_cleanup_enabled = True
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.track_title, self.artist, self.album_title = title, artist, "Offline render evidence"
        self.media_track_id = match.source
        self.media_is_playing = True
        self.media_position_seconds = 0.
        self.media_position_source = "local-seek"
        self.media_position_sampled_at = 100.
        self.media_position_observed_seconds = 0.
        self.media_duration_seconds = max((row.time_seconds for row in match.timed_lyrics), default=0.) + 10.
        self.timed_lyrics = [(row.time_seconds, row.text) for row in match.timed_lyrics]
        self.lyrics = [row.text for row in match.timed_lyrics]
        self.timed_lyric_units = [row.units for row in match.timed_lyrics]
        self.timed_lyric_timing_kinds = [row.timing_kind for row in match.timed_lyrics]
        self.data_provider = _OfflineProvider()
        self._estimated_media_position = lambda _now: self.media_position_seconds
        self._music_library_back_callback = lambda: None
        self.album_color, self.secondary_color = QColor("#667b88"), QColor("#3a4452")
        self.album_art = QImage(300, 300, QImage.Format.Format_RGB32)
        self.album_art.fill(QColor("#425766"))


def _digest(value):
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _row_summary(rows):
    return [{"at": row.time_seconds, "text_sha256": _digest(row.text),
             "characters": len(row.text), "kind": row.timing_kind, "source_units": len(row.units)}
            for row in rows]


def _plans_summary(plans):
    return [{"at": plan.start_seconds, "end": plan.end_seconds,
             "text_sha256": _digest(plan.text), "quality": plan.quality,
             "source_units": len(plan.source_units), "display_units": len(plan.display_units),
             "estimated_units": sum(bool(unit.estimated) for unit in plan.display_units)}
            for plan in plans]


def _generic_matches(store):
    # Newly authored, copyright-free test phrases; genuine markers deliberately
    # include unequal lengths and a multiword measured phrase.
    real = store.parse_online_lyrics(source="fixture:real-word", raw_qrc=(
        "[8000,4000]Soft(8000,700) light(8700,3300)\n"
        "[17000,4000]Moving(17000,900) slowly(17900,3100)\n"
        "[26000,4000]New(26000,1800) morning(27800,2200)\n"
        "[35000,3000]Quiet(35000,1000) room(36000,2000)"))
    line = store.parse_online_lyrics(source="fixture:line", raw_lrc=(
        "[00:00.00]作词 Lyricist：Offline fixture\n"
        "[00:08.00]Soft light\n[00:17.00]Moving slowly\n"
        "[00:26.00]New morning\n[00:35.00]Quiet room"))
    phrase = store.parse_online_lyrics(source="fixture:segment", raw_qrc=(
        "[8000,4000]Soft light(8000,4000)\n"
        "[17000,4000]Moving slowly(17000,4000)\n"
        "[26000,4000]New morning(26000,4000)\n"
        "[35000,3000]Quiet room(35000,3000)"))
    if real is None or line is None or phrase is None:
        raise AssertionError("Production parser rejected an authored timing fixture")
    plain = [row for row in line.timed_lyrics if row.time_seconds >= 8.]
    mixed_rows = (real.timed_lyrics[0], plain[1], phrase.timed_lyrics[2], real.timed_lyrics[3])
    mixed = replace(real, source="fixture:mixed", timed_lyrics=mixed_rows,
                    lyrics=tuple(row.text for row in mixed_rows))
    many_words = store.parse_online_lyrics(source="fixture:line-many-words", raw_lrc=(
        "[00:08.00]Soft light moves slowly across this quiet room\n"
        "[00:14.00]New morning brings a gentle sound to everyone\n"
        "[00:21.00]We follow every word and keep the rhythm\n"
        "[00:28.00]Another simple line remains for the next frame\n"
        "[00:37.00]Last line"))
    if many_words is None:
        raise AssertionError("Production parser rejected the multiword fixture")
    return [(match, "Offline timing fixture", "Offline fixture", None)
            for match in (real, line, phrase, mixed, many_words)]


def _real_matches(store, directory):
    """Read saved responses only; never search/download or persist their lyrics."""
    matches = []
    for path in sorted(directory.glob("*.json")):
        name = path.name
        if not name.startswith(("netease-lyric-", "qq-qrc-", "amll-netease-", "amll-qq-")):
            continue
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Oversized local response: " + name)
        raw = path.read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
        if name.startswith("netease-lyric-"):
            match = store.parse_online_lyrics(raw_netease=raw, source="offline:" + path.stem)
        elif name.startswith("qq-qrc-"):
            encoded = payload["request"]["data"]["lyric"]
            match = store.parse_online_lyrics(raw_qrc=decrypt_qrc(encoded), source="offline:" + path.stem)
        else:
            match = store.parse_online_lyrics(raw_ttml=payload["data"]["lyrics"], source="offline:" + path.stem)
        if match is None:
            raise AssertionError("Saved response was not parsed: " + name)
        # Identifiers are evidence labels; real lyrics remain in the input/output
        # ignored directory, never in committed fixture data or report text.
        matches.append((match, "Local response render", "Don Toliver/Doja Cat",
                        {"file": name, "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}))
    return matches


def _island(owner, style):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    for name in ("lyrics", "timed_lyrics", "timed_lyric_units", "timed_lyric_timing_kinds",
                 "artist", "track_title", "media_track_id", "media_is_playing", "enable_dual_line_lyrics"):
        setattr(visual, name, getattr(owner, name))
    visual.lyric_flow_style = style
    visual._u = float
    visual.font_shrink_scale = 1.
    visual.high_quality_font_rendering = False
    visual.music_ambience_style = "sea_level"
    visual.music_surface_reading_strategy = owner.music_surface_reading_strategy
    visual.location_mode = "free"
    visual.top_notch_height_mode = "standard"
    visual.lyric_font_size = 18
    visual.dual_line_font_size = 12
    visual.dual_line_secondary_weight = 72
    visual.dual_line_secondary_spacing = 100
    visual.lyric_index = visual.prev_lyric_index = 0
    visual.lyric_changed_at = 100.
    visual.lyric_transition_kind = ""
    visual.expanded = True
    visual.scene_suppressed = False
    visual._should_paint_album_island = lambda: False
    visual._stable_lyric_source_position = lambda _now: owner.media_position_seconds
    visual._effective_lyric_offset_seconds = lambda: 0.
    visual._prepare_island_lyric_offset = lambda *_args: None
    visual._lyric_layout_cache = {}
    visual._wave_reading_renderer = LyricReadingRenderer()
    return visual


def _trace_painter(surface, method_name, trace):
    """Wrap a checked production painter interface, retaining its real call."""
    original = getattr(surface, method_name)
    signature = inspect.signature(original)

    def observed(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = bound.arguments
        event = {"method": method_name}
        if "text" in values:
            event["text_sha256"] = _digest(str(values["text"]))
        for key in ("progress", "position", "real_progresses", "line_end"):
            if key in values:
                event[key] = values[key]
        # This is the actual painter chosen after focus/emphasis decisions,
        # rather than the active row's merely available reading plan.
        trace.append(event)
        result = original(*args, **kwargs)
        if method_name in {"_reading_front", "_real_visual_line_progresses"}:
            event["result"] = result
        return result

    return patch.object(surface, method_name, side_effect=observed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-dpr", type=float, required=True)
    parser.add_argument("--real-dir", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".runtime").resolve()):
        parser.error("Rendered local lyrics must stay in this checkout's ignored .runtime directory")
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    callbacks, checks, datasets, timings = [], [], [], []
    original_hook = sys.excepthook
    sys.excepthook = lambda kind, value, tb: callbacks.append("".join(traceback.format_exception(kind, value, tb)))
    report = {"scope": "Offline parsers and production Qt painters; no physical-display or listening acceptance",
              "no_account_settings_network_or_audio": True, "callbacks": callbacks,
              "checks": checks, "datasets": datasets, "source_sha256": {
                  name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                      "api/lyrics.py", "api/lyric_timeline.py", "local/lyric_unit_timing.py",
                      "local/music_surface_window.py", "local/island_stack_visual.py")}}

    def check(label, condition):
        checks.append({"check": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)

    def save(image, name):
        check(name + " saves", image.save(str(output / name)))
        return hashlib.sha256(bytes(image.constBits())).hexdigest()

    try:
        with tempfile.TemporaryDirectory(prefix="lyric-render-", dir=output) as temporary:
            store = LocalLyricsStore(Path(temporary) / "manual", generated_root=Path(temporary) / "cache",
                settings_getter=lambda key, default=None: True if key == "v2_enable_dual_line_lyrics" else default)
            matches = _generic_matches(store)
            if args.real_dir:
                matches.extend(_real_matches(store, args.real_dir.resolve()))
            cases = [(match, title, artist, provenance, strategy)
                     for match, title, artist, provenance in matches
                     for strategy in ("gold", "low_perception")]
            for dataset_index, (match, title, artist, provenance, strategy) in enumerate(cases):
                source_before = _digest(match)
                record = {"source": match.source, "input": provenance, "strategy": strategy,
                          "quality": asdict(lyric_timing_quality(match.timed_lyrics)),
                          "source_rows": _row_summary(match.timed_lyrics), "surfaces": [], "islands": []}
                datasets.append(record)
                owner = _Owner(match, title, artist)
                owner.music_surface_reading_strategy = strategy
                surface = MusicSurfaceWindow(owner)
                surface.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                for timer in surface.findChildren(QTimer):
                    timer.stop()
                try:
                    surface.resize(960, 540)
                    check("DPR matches requested run", abs(surface.devicePixelRatioF() - args.expect_dpr) < .01)
                    baseline = None
                    for fullscreen in (False, True):
                        surface._fullscreen = fullscreen
                        surface.resize(1280, 720) if fullscreen else surface.resize(960, 540)
                        for layout in ("lyrics", "cover_lyrics"):
                            surface.set_content_layout(layout, animate=False)
                            lines, timed = surface._display_lyrics()
                            surface._advance_song_layout_build(max_rows=None)
                            summary = _plans_summary(surface._display_row_timings)
                            signature = (tuple(timed), summary)
                            if baseline is None:
                                baseline = signature
                            check("all four surfaces share timeline and reading plans", signature == baseline)
                            usable = [plan for plan in surface._display_row_timings
                                      if plan.has_reading_timing and plan.text != INTRO_COUNTDOWN_TEXT]
                            check("dataset has a reading plan", bool(usable))
                            representatives = {}
                            for candidate in usable:
                                representatives.setdefault(candidate.quality, candidate)
                            samples = []
                            for quality, candidate in representatives.items():
                                start = candidate.start_seconds
                                end = candidate.end_seconds or start + 1.
                                samples.extend([(quality + "-start", start),
                                    (quality + "-middle", start + (end-start)*.5),
                                    (quality + "-end", max(start, end-.01)),
                                    (quality + "-seek-back", start + (end-start)*.2)])
                            plan = list(representatives.values())[-1]
                            entry = {"fullscreen": fullscreen, "layout": layout,
                                     "mapped_units": sum(map(len, surface._display_timed_units)),
                                     "badge": surface._timing_quality_badge(lines, timed),
                                     "plans": summary, "frames": []}
                            record["surfaces"].append(entry)
                            native_front = surface_module.display_row_reading_front

                            def render(stamp):
                                trace, paint_trace = [], []
                                def traced_front(row_plan, position, offsets):
                                    front = native_front(row_plan, position, offsets)
                                    trace.append({"text_sha256": _digest(row_plan.text),
                                                  "position": position, "front": front,
                                                  "quality": row_plan.quality})
                                    return front
                                with ExitStack() as stack:
                                    stack.enter_context(patch("time.monotonic", return_value=stamp))
                                    stack.enter_context(patch.object(surface_module, "display_row_reading_front", side_effect=traced_front))
                                    for method in (
                                        "_paint_wrapped_lifted_lyric", "_paint_lifted_run",
                                        "_paint_sparse_glyph_run", "_paint_cold_emphasis_reading",
                                        "_draw_sprung_cached_wrapped_layout", "_draw_future_wrapped_layout",
                                        "_draw_low_perception_block_wipe", "_reading_front",
                                        "_real_visual_line_progresses",
                                    ):
                                        stack.enter_context(_trace_painter(surface, method, paint_trace))
                                    began = time.perf_counter()
                                    image = QImage(round(surface.width()*args.expect_dpr),
                                                   round(surface.height()*args.expect_dpr), QImage.Format.Format_RGB32)
                                    image.setDevicePixelRatio(args.expect_dpr)
                                    surface.render(image)
                                    timings.append((time.perf_counter()-began)*1000.)
                                return image, trace, paint_trace

                            for sample_index, (label, position) in enumerate(samples):
                                owner.media_position_seconds = owner.media_position_observed_seconds = position
                                owner.media_position_sampled_at = 100. + sample_index
                                owner.media_is_playing = False
                                surface._last_lyric_render_branch = None
                                image, trace, paint_trace = render(100. + sample_index)
                                name = f"{dataset_index:02d}-{'full' if fullscreen else 'window'}-{layout}-{label}.png"
                                pixel_sha = save(image, name)
                                entry["frames"].append({"label": label, "position": position, "file": name,
                                    "pixels_sha256": pixel_sha, "branch": surface._last_lyric_render_branch,
                                    "active_quality": getattr(surface, "_last_lyric_timing_quality", None),
                                    "emphasis_mode": getattr(surface, "_last_lyric_emphasis_mode", None),
                                    "native_reading_calls": trace, "actual_painter_calls": paint_trace})
                                check("production painter trace is populated", bool(paint_trace))
                                if label == "estimated-middle" and match.source == "fixture:line":
                                    for call in paint_trace:
                                        if call["method"] == "_paint_cold_emphasis_reading":
                                            check("cold emphasis retains the shared partial word front",
                                                  call.get("real_progresses") is not None
                                                  and any(0. < front < 1. for front in call["real_progresses"]))
                                if strategy == "low_perception":
                                    check("low perception never uses the retired half-second block wipe",
                                          not any(call["method"] == "_draw_low_perception_block_wipe" for call in paint_trace))
                                    if match.source == "fixture:line-many-words" and label == "estimated-middle":
                                        check("low-perception multiword midpoint reaches partial actual ink fronts",
                                              any(call["method"] == "_paint_lifted_run" and 0. < call["progress"] < 1.
                                                  for call in paint_trace))
                                        check("low-perception multiword midpoint maps timed units into actual wrapped painter",
                                              any(call["method"] == "_paint_wrapped_lifted_lyric" and call.get("real_progresses") is not None
                                                  for call in paint_trace))
                            _paused_image, paused_trace, _paused_paint = render(500.)
                            check("pause retains actual renderer clock/front after wall time advances",
                                  bool(trace) and paused_trace == trace)
                            entry["paused_render_trace"] = paused_trace
                            check("seek back immediately reduces reading front",
                                  plan.reading_progress(samples[-1][1]) < plan.reading_progress(samples[-2][1]))
                    # Actual island viewport painters consume the same raw rows
                    # and position. Styles retain their production typography.
                    for style in ("wave_stream", "gelv_songscatter"):
                        visual = _island(owner, style)
                        for expanded in (False, True):
                            entry = {"style": style, "expanded": expanded, "frames": []}
                            record["islands"].append(entry)
                            for sample_index, (label, position) in enumerate(samples):
                                owner.media_position_seconds = position
                                visual.lyric_index = max(0, bisect_right([row[0] for row in visual.timed_lyrics], position)-1)
                                visual.prev_lyric_index = visual.lyric_index
                                visual.media_is_playing = False
                                phase = visual._lyric_phase(200. + sample_index)
                                paused_phase = visual._lyric_phase(900. + sample_index)
                                check("island paused front ignores elapsed wall time",
                                      phase.get("timed_character_front") == paused_phase.get("timed_character_front")
                                      and phase.get("progress") == paused_phase.get("progress"))
                                image = QImage(round(440*args.expect_dpr), round(200*args.expect_dpr), QImage.Format.Format_ARGB32_Premultiplied)
                                image.setDevicePixelRatio(args.expect_dpr)
                                image.fill(QColor("#17212a"))
                                with patch("time.monotonic", return_value=200. + sample_index):
                                    with QPainter(image) as painter:
                                        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                                        if expanded:
                                            visual._paint_lyric_viewport(painter, QRectF(10., 10., 420., 180.))
                                        else:
                                            visual._paint_compact_lyric_viewport(painter, QRectF(10., 10., 420., 80.))
                                name = f"{dataset_index:02d}-island-{style}-{'expanded' if expanded else 'compact'}-{label}.png"
                                entry["frames"].append({"file": name, "pixels_sha256": save(image, name),
                                    "position": position, "active_index": phase.get("active_index"),
                                    "quality": phase.get("timing_quality"), "progress": phase.get("progress"),
                                    "whole_row": phase.get("whole_row"), "gap": phase.get("gap_kind"),
                                    "display_units": len(phase.get("timed_units", ()))})
                    check("raw source remains unchanged after all rendering", _digest(match) == source_before)
                    check("owner source units remain unchanged", owner.timed_lyric_units == [row.units for row in match.timed_lyrics])
                finally:
                    surface.shutdown()
                    surface.close()
                    owner.close()
                    surface.deleteLater()
                    owner.deleteLater()
                    app.processEvents()
        check("no Qt/Python callback exception", not callbacks)
        report.update(ok=True, dpr=args.expect_dpr, player_frames=len(timings),
                      quality_counts=dict(Counter(plan["quality"] for data in datasets for plan in data["surfaces"][0]["plans"])))
        if timings:
            timings.sort()
            report["paint_ms_p50"] = timings[len(timings)//2]
            report["paint_ms_p95"] = timings[min(len(timings)-1, int(len(timings)*.95))]
    except BaseException:
        report["ok"] = False
        report["error"] = traceback.format_exc()
        raise
    finally:
        sys.excepthook = original_hook
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report.get("ok"), "datasets": len(datasets), "checks": len(checks),
                          "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
