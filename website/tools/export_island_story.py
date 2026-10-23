"""Export four silent, native GluSpot island-mode story frames."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"


def _default_app_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "local" / "island_stack_visual.py").is_file():
            return candidate
    raise FileNotFoundError("Could not locate local/island_stack_visual.py; pass --app-root")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path,
                        help="GluSpot source root (defaults to nearest ancestor)")
    parser.add_argument("--output", type=Path,
                        help="Output directory (defaults to APP_ROOT/website/public/assets/island-story)")
    args = parser.parse_args()
    app_root = (args.app_root or _default_app_root()).resolve()
    if not (app_root / "local" / "island_stack_visual.py").is_file():
        parser.error(f"--app-root does not contain local/island_stack_visual.py: {app_root}")
    output = (args.output or app_root / "website" / "public" / "assets" / "island-story").resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(app_root))

    from PIL import Image
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from local.island_stack_visual import IslandStackVisual
    from local.music_surface_palette import artwork_surface_palette
    from tests.test_finder_bar_surface import FinderBarSurfaceTests

    modes = (
        ("free", 360, 46, "It's been the same old thing", "一成不变"),
        ("top", 360, 62, "Since it ended", "自结束时"),
        ("tasker", 300, 40, "Since you left me", "自你离我而去"),
        ("finder", 360, 30, "Since I moved on", "自我继续前行"),
    )
    app = QApplication.instance() or QApplication([])
    IslandStackVisual.load_embedded_lyric_fonts()
    app.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
    artwork = app_root / "website" / "public" / "assets" / "chace-belated-suffocation.jpg"
    if not artwork.is_file():
        artwork = output.parent / "chace-belated-suffocation.jpg"
    if not artwork.is_file():
        raise FileNotFoundError(f"Missing source artwork: {artwork}")

    records = []
    for mode, width, height, english, translation in modes:
        fixture = FinderBarSurfaceTests()
        fixture.app = app
        fixture.setUp()
        try:
            visual = fixture.visual
            visual._solid_album_palette_async_enabled = False
            visual._music_system_dark = True
            visual.set_music_ambience_style("solid_album", notify=False)
            visual.album_art = QImage(str(artwork))
            palette = artwork_surface_palette(visual.album_art, QColor("#555555"), QColor("#777777"))
            visual.album_color = QColor(palette.primary)
            visual.secondary_color = QColor(palette.secondary)
            visual._lyric_presentation_store = None
            visual.track_title = "The Rain Song"
            visual.artist = "Chace"
            # A single supplied source row keeps the selected line fixed. Its
            # displayed translation uses the existing native dual-line path;
            # no source lyric timestamps are invented for this still.
            visual.lyrics = [f"{english}\n{translation}"]
            visual.timed_lyrics = []
            visual.timed_lyric_units = []
            visual.timed_lyric_timing_kinds = []
            visual.lyric_index = 0
            visual.enable_dual_line_lyrics = True
            visual.media_is_playing = False
            visual.media_position_seconds = 0.0
            visual.media_position_observed_seconds = 0.0
            visual.media_position_sampled_at = 100.0
            visual.media_position_sync_at = 100.0
            visual.media_duration_seconds = 0.0
            visual.location_mode = mode
            visual.finder_mode = mode == "finder"
            visual.mydockfinder_running = mode == "finder"
            visual.finder_bar_enabled = True
            # Native Tasker/Finder previews use the transparent material.
            # Their lyrics and cover remain painted by _paint_music; only the
            # bar's optional background layer is omitted.
            native_bar_style = "transparent" if mode in {"tasker", "finder"} else None
            if native_bar_style is not None:
                visual.finder_bar_style = native_bar_style
            visual.finder_bar_rect = QRectF(0, 0, width, height)
            visual.taskbar_follow_base_rect = QRectF(0, 0, width, height)
            visual.taskbar_material_color = QColor("#273033")
            visual.finder_material.mid_color = QColor("#273033")
            visual.taskbar_selected_drop_zone = QRectF()

            image = QImage(width * 2, height * 2, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(2.0)
            image.fill(Qt.GlobalColor.transparent)
            with patch("local.island_stack_visual.time.monotonic", return_value=100.0), QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                visual._paint_music(painter, QRectF(0, 0, width, height), False)

            filename = f"chace-{mode}.png"
            target = output / filename
            if not image.save(str(target), "PNG"):
                raise RuntimeError(f"Could not save {target}")
            with Image.open(target) as decoded:
                rgba = decoded.convert("RGBA")
                alpha = rgba.getchannel("A")
                alpha_bounds = alpha.getbbox()
                transparent_pixels = alpha.histogram()[0]
                pixel_size = list(rgba.size)
                bounds_valid = bool(alpha_bounds and 0 <= alpha_bounds[0] < alpha_bounds[2] <= pixel_size[0]
                                    and 0 <= alpha_bounds[1] < alpha_bounds[3] <= pixel_size[1])
                alpha_extrema = list(alpha.getextrema())
                total_pixels = pixel_size[0] * pixel_size[1]
                alpha_verified = (alpha_extrema == [0, 255] and transparent_pixels > 0 and bounds_valid)
                if not alpha_verified:
                    raise RuntimeError(f"Alpha/bounds verification failed for {filename}")
            records.append({
                "file": filename,
                "mode": mode,
                "logical_size": [width, height],
                "dpr": 2,
                "pixel_size": pixel_size,
                "ambience_style": "solid_album",
                "native_bar_style": native_bar_style,
                "source_cover": "chace-belated-suffocation.jpg",
                "lyric_source": "user-provided Chace excerpt",
                "lyric": english,
                "translation": translation,
                "dual_line_enabled": True,
                "native_geometry_note": "The native painter decides whether the translation fits at this mode's fixed height.",
                "demonstration_clock": "Fixed offscreen paint instant; no original lyric timestamps supplied or fabricated.",
                "palette": {
                    "primary": palette.primary.name(QColor.NameFormat.HexRgb),
                    "secondary": palette.secondary.name(QColor.NameFormat.HexRgb),
                },
                "alpha_verification": {
                    "verified": alpha_verified,
                    "extrema": alpha_extrema,
                    "transparent_pixels": transparent_pixels,
                    "total_pixels": total_pixels,
                    "transparent_fraction": round(transparent_pixels / total_pixels, 6),
                    "bounds_pixels": list(alpha_bounds),
                    "bounds_within_image": bounds_valid,
                },
                "source": "local/island_stack_visual.py::IslandStackVisual._paint_music",
            })
        finally:
            fixture.tearDown()

    (output / "provenance.json").write_text(json.dumps({
        "description": "Four native GluSpot island modes exported offscreen from fixed Chace source rows; silent demo, no account/network/audio/desktop capture.",
        "source_renderer": "local/island_stack_visual.py::IslandStackVisual._paint_music",
        "application_version": "6.417",
        "source_artwork": "chace-belated-suffocation.jpg",
        "source_lyrics": [
            {"lyric": english, "translation": translation} for _, _, _, english, translation in modes
        ],
        "assets": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"exported": len(records), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
