"""Render inert lyric fixtures through the real Qt player and island painters.

Run with the workspace Python; QT_SCALE_FACTOR=2 repeats at DPR 2. No account,
provider polling, audio playback or installed application is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication
from PIL import Image, ImageDraw

from tests.test_music_lyric_interlude_render import MusicLyricInterludeRenderTests
from tests.test_island_lyric_intervals import IslandLyricIntervalsTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    fixture = MusicLyricInterludeRenderTests()
    fixture.setUp()
    fixture.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    saved, timings = [], []
    try:
        for fullscreen in (False, True):
            fixture.window._fullscreen = fullscreen
            fixture.window.resize(1280, 720) if fullscreen else fixture.window.resize(960, 540)
            for layout in ('lyrics', 'cover_lyrics'):
                fixture.window.set_content_layout(layout, animate=False)
                fixture.window._advance_song_layout_build(max_rows=None)
                fixture.owner.media_is_playing = True
                for frame in range(91):
                    stamp = 100. + frame / 60.
                    position = 11.5 + frame / 60.
                    fixture.owner.media_position_seconds = position
                    fixture.owner.media_position_observed_seconds = position
                    fixture.owner.media_position_sampled_at = stamp
                    with patch('local.music_surface_window.time.monotonic', return_value=stamp):
                        began = time.perf_counter()
                        image = fixture.render()
                        timings.append((time.perf_counter() - began) * 1000.)
                    if frame in (0, 30, 60, 90):
                        name = f'{"fullscreen" if fullscreen else "window"}-{layout}-{frame:03d}.png'
                        assert image.save(str(args.output / name))
                        saved.append(name)
        visual = IslandLyricIntervalsTests.visual()
        visual._music_lyric_base_text_color = lambda _alpha: QColor('#708090')
        visual._music_lyric_text_color = lambda _alpha: QColor('white')
        for position in (10., 11.25, 12.5, 13.75, 14.99):
            gap = visual._island_lyric_gap(position)
            assert gap is not None
            visual._island_display_phase = lambda: {'gap_kind': gap.kind, 'gap_progress': gap.progress}
            image = QImage(300, 70, QImage.Format.Format_RGB32)
            image.fill(QColor('#17212a'))
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                assert visual._paint_island_lyric_gap(painter, QRectF(0., 0., 300., 70.))
            name = f'island-{position:.2f}.png'
            assert image.save(str(args.output / name))
            saved.append(name)
    finally:
        fixture.tearDown()
    thumbnails = []
    for name in saved:
        tile = Image.new('RGB', (480, 300), '#0c0f14')
        with Image.open(args.output / name) as original:
            original.thumbnail((480, 270))
            tile.paste(original, ((480 - original.width) // 2, 24))
        ImageDraw.Draw(tile).text((10, 5), name, fill='white')
        thumbnails.append(tile)
    sheet = Image.new('RGB', (480 * 4, 300 * ((len(thumbnails) + 3) // 4)), '#0c0f14')
    for index, tile in enumerate(thumbnails):
        sheet.paste(tile, (index % 4 * 480, index // 4 * 300))
    sheet.save(args.output / 'contact-sheet.png')
    timings.sort()
    report = {'dpr': app.devicePixelRatio(), 'player_frames': len(timings), 'saved': saved,
        'paint_ms_p50': timings[len(timings) // 2], 'paint_ms_p95': timings[int(len(timings) * .95)],
        'scope': 'Inert fixture / real Qt QImage renderer; no acoustic or physical-display validation'}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
