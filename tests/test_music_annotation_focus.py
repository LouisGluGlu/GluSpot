from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _Owner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.media_is_playing = True
        self.media_track_id = "annotation-focus"
        self.media_position_source = "player"
        self.media_position_seconds = 0.0
        self.timed_lyrics = [
            (0.0, "Hello world"),
            (3.0, "Next (echo) sentence\n翻译：下一句（回声）"),
            (6.0, "Later sentence"),
            (9.0, "Final sentence"),
        ]
        self.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=0.7, text="Hello "),
            SimpleNamespace(start_seconds=0.7, end_seconds=1.2, text="world"),
        ), (), (), ()]
        self.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]

    def _run_background(self, _name, callback):
        callback()


class MusicAnnotationFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _image(self):
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.black)
        return image

    def _render(self, position, now, *, image=None, region=None):
        self.owner.media_position_seconds = position
        self.owner.media_position_sampled_at = now
        self.owner.media_position_observed_seconds = position
        image = image if image is not None else self._image()
        captured = {}
        original = self.window._paint_wrapped_lifted_lyric

        def record(painter, row, text, font, layout, progress, *args, **kwargs):
            if text in {"echo", "回声"}:
                captured[text] = (progress, painter.opacity(), QRect(row.toAlignedRect()))
            return original(painter, row, text, font, layout, progress, *args, **kwargs)

        with (
            patch("local.music_surface_window.time.monotonic", return_value=now),
            patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=record),
        ):
            if region is None:
                self.window.render(image)
            else:
                self.window.render(image, region.topLeft(), QRegion(region))
        return image, captured

    def test_early_focused_annotations_enter_unread_and_do_not_restart_at_source_time(self):
        for fullscreen in (False, True):
            for next_kind in ("line", "unit", "synthetic"):
                with self.subTest(fullscreen=fullscreen, next_kind=next_kind):
                    self.window._fullscreen = fullscreen
                    self.window.resize(1280, 720) if fullscreen else self.window.resize(960, 540)
                    self.owner.media_track_id = f"annotation-focus-{fullscreen}-{next_kind}"
                    self.owner.timed_lyric_timing_kinds[1] = next_kind
                    if next_kind == "unit":
                        source = "Next (echo) sentence"
                        self.owner.timed_lyric_units[1] = tuple(
                            SimpleNamespace(start_seconds=3.0 + index * 0.1,
                                            end_seconds=3.0 + (index + 1) * 0.1, text=char)
                            for index, char in enumerate(source)
                        )
                    else:
                        self.owner.timed_lyric_units[1] = ()
                    self._render(0.4, 100.0)
                    _, not_focused = self._render(0.5, 100.1)
                    self.assertEqual(not_focused, {})

                    self._render(1.2, 101.0)
                    entered_at = self.window._parenthetical_visible_since.get(1)
                    self.assertEqual(entered_at, 101.0)
                    _, waiting = self._render(1.3, 101.1)
                    self.assertEqual(self.window._lyric_motion.active_index, 0)
                    self.assertEqual(set(waiting), {"echo", "回声"})
                    for progress, opacity, _row in waiting.values():
                        self.assertEqual(progress, 0.0)
                        self.assertGreater(opacity, 0.0)
                    self.assertAlmostEqual(waiting["echo"][1], waiting["回声"][1])

                    # Keep the source clock advancing with wall time: this
                    # tests annotation ownership across a natural row change,
                    # rather than an unconfirmed player-clock discontinuity.
                    self._render(2.99, 102.79)
                    self._render(3.0, 102.80)
                    _, reading = self._render(3.04, 102.84)
                    self.assertEqual(self.window._lyric_motion.active_index, 1)
                    self.assertEqual(self.window._parenthetical_visible_since[1], entered_at)
                    self.assertEqual(set(reading), {"echo", "回声"})
                    self.assertTrue(all(value[0] == 0.0 for value in reading.values()))
                    self.assertTrue(all(value[1] == 1.0 for value in reading.values()))

    def test_early_annotation_remains_in_partial_repaint_bounds(self):
        self.window._fullscreen = True
        self.window.resize(1920, 1080)
        self._render(0.4, 100.0)
        partial, _ = self._render(1.2, 101.0)
        # Settle the row spring before the annotation entrance completes:
        # the active dirty region must include the focused future row.
        partial, captured = self._render(1.3, 101.1, image=partial)
        self.assertEqual(set(captured), {"echo", "回声"})
        active = self.window._active_lyric_animation_rect
        self.assertTrue(active.isValid())
        for _progress, _opacity, row in captured.values():
            self.assertTrue(active.intersects(row))

        partial, captured_partial = self._render(1.4, 101.2, image=partial, region=active)
        full, captured_full = self._render(1.4, 101.2)
        self.assertEqual(set(captured_partial), {"echo", "回声"})
        self.assertEqual(set(captured_full), {"echo", "回声"})
        logical = active.intersected(self.window.rect())
        partial_crop = partial.copy(logical)
        full_crop = full.copy(logical)
        self.assertEqual(bytes(partial_crop.constBits()), bytes(full_crop.constBits()))


if __name__ == "__main__":
    unittest.main()
