"""Render authored bilingual fixtures offscreen, without playback or accounts.

Run with /workspace/gluspot/qtvenv/bin/python. Each surface resolves its own
variant; this tool renders classic and A/B/C in one isolated invocation.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def _repository() -> Path:
    for candidate in (Path(__file__).resolve().parents[1],
                      Path("/workspace/gluspot/wt-translation-variants")):
        if (candidate / "local/music_surface_window.py").is_file():
            return candidate
    raise RuntimeError("Run from the translation variants checkout")


ROOT = _repository()
sys.path.insert(0, str(ROOT))

ENGLISH = (
    "Soft light across the quiet room",
    "Moving slowly through the morning",
    "Hold this color in your hands",
    "Listen once and let it stay",
)
CHINESE = ("柔光铺过安静的房间", "缓慢穿过清晨", "把这抹颜色握在手里", "听一遍 就让它留下")
STAMPS = ("00:00.00", "00:08.00", "00:17.00", "00:26.00")
SIZES = ((433, 550), (960, 540))
FLOAT_MS = (0, 80, 160, 280, 480)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("/workspace/gluspot/handoff/translation-variants"))
    parser.add_argument("--variants", nargs="+", choices=("classic", "a", "b", "c"),
                        default=("classic", "a", "b", "c"))
    args = parser.parse_args()
    output = args.output.resolve()
    shots = output / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    # Set private runtime paths before importing Qt or any application modules.
    with tempfile.TemporaryDirectory(prefix="gluspot-translation-render-") as temporary:
        private = Path(temporary)
        (private / "runtime").mkdir(mode=0o700)
        with patch.dict(os.environ, {
            "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": "1",
            "APPDATA": str(private / "appdata"), "LOCALAPPDATA": str(private / "localappdata"),
            "XDG_CONFIG_HOME": str(private / "config"), "XDG_CACHE_HOME": str(private / "cache"),
            "XDG_DATA_HOME": str(private / "data"),
            "XDG_RUNTIME_DIR": str(private / "runtime"),
        }):
            _render(args.variants, output, shots, private)


def _render(variants, output, shots, private):
    from PIL import Image, ImageDraw, ImageFont
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient
    from PySide6.QtWidgets import QApplication, QWidget

    from api.audio_routes import AudioRouteSnapshot
    from api.lyrics import LocalLyricsStore
    from local.music_surface_window import MusicSurfaceWindow

    class OfflineProvider:
        def get_audio_route_snapshot(self):
            return AudioRouteSnapshot(state="unavailable", capture_scope="owned-player")

    class Owner(QWidget):
        def __init__(self, match):
            super().__init__()
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            self._music_windowed_preference = True
            self.music_intro_cleanup_enabled = False
            self.enable_dual_line_lyrics = True
            self.track_title = "Soft light"
            self.artist = "GluSpot · Authored fixture"
            self.album_title = "Translation style comparison"
            self.media_track_id = match.source
            self.media_is_playing = True
            self.media_position_source = "local-seek"
            self.media_position_seconds = self.media_position_observed_seconds = 0.5
            self.media_position_sampled_at = 100.0
            self.media_duration_seconds = 36.0
            self.timed_lyrics = [(row.time_seconds, row.text) for row in match.timed_lyrics]
            self.lyrics = [row.text for row in match.timed_lyrics]
            self.timed_lyric_units = [row.units for row in match.timed_lyrics]
            self.timed_lyric_timing_kinds = [row.timing_kind for row in match.timed_lyrics]
            self.data_provider = OfflineProvider()
            self._estimated_media_position = lambda _now: self.media_position_seconds
            self._music_library_back_callback = lambda: None
            self.album_color, self.secondary_color = QColor("#778caa"), QColor("#645785")
            self.album_art = QImage(480, 480, QImage.Format.Format_RGB32)
            self.album_art.fill(QColor("#29384f"))
            with QPainter(self.album_art) as painter:
                for x, y, radius, color in ((100, 140, 330, "#ac8f83"),
                                            (370, 380, 380, "#4b6390")):
                    glow = QRadialGradient(x, y, radius)
                    glow.setColorAt(0.0, QColor(color))
                    glow.setColorAt(1.0, QColor(0, 0, 0, 0))
                    painter.fillRect(self.album_art.rect(), glow)

    app = QApplication.instance() or QApplication([])
    store = LocalLyricsStore(private / "manual", generated_root=private / "generated",
                             settings_getter=lambda key, default=None:
                             True if key == "v2_enable_dual_line_lyrics" else default)
    match = store.parse_online_lyrics(
        source="fixture:translation-variants",
        raw_lrc="\n".join(f"[{stamp}] {line}" for stamp, line in zip(STAMPS, ENGLISH)),
        translation="\n".join(f"[{stamp}] {line}" for stamp, line in zip(STAMPS, CHINESE)),
    )
    assert match is not None and len(match.timed_lyrics) == 4
    assert all(text in row.text for text, row in zip(CHINESE, match.timed_lyrics))
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    font = ImageFont.truetype(font_path, 19)
    small_font = ImageFont.truetype(font_path, 16)
    title_font = ImageFont.truetype(font_path, 29)
    report = {"fixture": "authored bilingual LRC + translation via LocalLyricsStore",
              "sizes": SIZES, "variants": {}}

    def label(draw, at, text, selected_font=font, fill="#e9edf5"):
        draw.text(at, text, font=selected_font, fill=fill)

    for variant in variants:
        frames, strips, records = {}, {}, []
        for size in SIZES:
            width, height = size
            with patch.dict(os.environ, {"GLUSPOT_TRANSLATION_VARIANT": variant}), \
                    patch("time.monotonic", return_value=100.0):
                owner = Owner(match)
                surface = MusicSurfaceWindow(owner)
                surface.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                surface.resize(width, height)
                surface.set_content_layout("lyrics", animate=False)
                for timer in surface.findChildren(QTimer):
                    timer.stop()

            def render(now, position, name=None):
                owner.media_position_seconds = owner.media_position_observed_seconds = position
                owner.media_position_sampled_at = now
                trace = []

                def observer(method):
                    original = getattr(surface, method)
                    signature = inspect.signature(original)

                    def draw(*values, **kwargs):
                        bound = signature.bind(*values, **kwargs)
                        arguments = bound.arguments
                        text = arguments.get("text", "")
                        if text in CHINESE:
                            trace.append({"text": text, "method": method,
                                          "opacity": arguments["painter"].opacity(),
                                          "blur": kwargs.get("blur_level", 0.0)})
                        return original(*values, **kwargs)
                    return draw

                with ExitStack() as stack:
                    stack.enter_context(patch("time.monotonic", return_value=now))
                    for method in ("_paint_wrapped_lifted_lyric", "_draw_sprung_cached_wrapped_layout",
                                   "_draw_future_wrapped_layout"):
                        stack.enter_context(patch.object(surface, method, side_effect=observer(method)))
                    image = QImage(width, height, QImage.Format.Format_RGB32)
                    image.fill(QColor("#171b27"))
                    surface.render(image)
                if name:
                    path = shots / f"{variant}-{width}x{height}-{name}.png"
                    assert image.save(str(path))
                    with Image.open(path) as saved:
                        result = saved.convert("RGB")
                    records.append({"shot": str(path), "monotonic": now, "position": position,
                                    "translations": trace})
                    return result

            try:
                # Warm on the previous focused row, then advance media and monotonic
                # clocks together at a fixed 50 Hz between captured instants.
                for step in range(31):
                    render(100.0 + step * .02, 6.9 + step * .02)
                previous = 0.0
                strip = []
                for milliseconds in FLOAT_MS:
                    elapsed = milliseconds / 1000.0
                    cursor = previous + .02
                    while cursor < elapsed - 1e-8:
                        render(101.1 + cursor, 8.0 + cursor)
                        cursor += .02
                    strip.append(render(101.1 + elapsed, 8.0 + elapsed, f"float-{milliseconds:03d}ms"))
                    previous = elapsed
                strips[size] = strip
                for step in range(1, 51):
                    render(101.58 + step * .02, 8.48 + step * .02)
                frames[(size, "active")] = render(105.1, 12.0, "active-focus-1")
                assert any(item["text"] == CHINESE[1] for item in records[-1]["translations"]), \
                    "Focused translation did not reach a production painter"
                # Same line is now previous/inactive, with the next line focused.
                for step in range(51):
                    render(110.1 + step * .02, 17.0 + step * .02)
                frames[(size, "inactive")] = render(113.6, 20.5, "inactive-line-1-focus-2")
                if variant != "classic":
                    assert not any(item["method"] == "_paint_wrapped_lifted_lyric"
                                   for record in records for item in record["translations"])
            finally:
                surface.shutdown()
                surface.close()
                owner.close()

        sheet = Image.new("RGB", (1465, 2190), "#151923")
        draw = ImageDraw.Draw(sheet)
        style = getattr(surface, "_translation_style", None)
        heading = {"classic": "Classic · current baseline", "a": "A · Apple-Music-like",
                   "b": "B · NetEase-readable", "c": "C · Synthesis"}[variant]
        label(draw, (24, 18), heading, title_font)
        if style is None:
            params = "ratio 16/38 | alpha 1.00/1.00 | gap max(4, .12*main) | karaoke on"
        else:
            gap = {"classic": "max(4, .12*main)", "a": ".30*main", "b": "8*scale",
                   "c": "max(4, .22*main)"}[variant]
            params = (f"ratio {style.size_ratio:.3f} | alpha {style.alpha_active:.2f}/{style.alpha_inactive:.2f}"
                      f" | gap {gap} | karaoke {'on' if style.karaoke_on_translation else 'off'}")
        label(draw, (24, 61), params)
        if style is not None:
            label(draw, (24, 88), f"entry {style.entry_duration_s:.2f}s | opacity delay {style.opacity_stagger_s:.2f}s"
                  f" | slide {style.slide_factor:.2f} | blur x{style.blur_factor:.2f}"
                  f" | inactive scale {style.inactive_scale:.2f}", small_font)
        for row, state in enumerate(("active", "inactive")):
            y = 128 + row * 596
            for size, x in zip(SIZES, (24, 481)):
                caption = "focus 1 @12.0s · previous + future visible" if state == "active" else "line 1 inactive @20.5s · focus 2"
                label(draw, (x, y), f"{size[0]} x {size[1]} | {caption}", small_font)
                sheet.paste(frames[(size, state)], (x, y + 30))
        y = 1336
        label(draw, (24, y), "Float · line 0 -> line 1 · media 8.000s + elapsed · monotonic advances equally")
        for size in SIZES:
            y += 38
            label(draw, (24, y), f"{size[0]} x {size[1]} · lyric crops, same scale across all variants", small_font)
            y += 29
            for index, (milliseconds, frame) in enumerate(zip(FLOAT_MS, strips[size])):
                # Keep the entire narrow lyric viewport; wide crops the lyric
                # column. The crop is fixed per size, never fitted per variant.
                crop = frame.crop((8, 90, 425, 485) if size[0] == 433 else (430, 70, 950, 470))
                crop.thumbnail((272, 300), Image.Resampling.LANCZOS)
                x = 24 + index * 287
                label(draw, (x, y), f"+{milliseconds:03d} ms", small_font)
                sheet.paste(crop, (x, y + 25))
            y += 300
        target = output / ("baseline.png" if variant == "classic" else f"variant-{variant.upper()}.png")
        sheet.save(target)
        assert target.stat().st_size > 40_000
        report["variants"][variant] = {"sheet": str(target), "frames": records}
        print(target)
    (output / "render-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
