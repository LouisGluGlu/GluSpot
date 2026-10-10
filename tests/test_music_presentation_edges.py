"""Regressions for duet timing and the two layers of sung brackets."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from api.models import TimedLyricUnit
from local.music_surface_window import MusicSurfaceWindow


class _Owner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.track_title = "Final duet"
        self.artist = "薛之谦、刘惜君"
        self.media_track_id = "final-duet-brackets"
        self.media_is_playing = True
        self.media_duration_seconds = 18.0
        self.media_position_seconds = 10.5
        self.media_position_source = "local-seek"
        self.media_position_sampled_at = 100.0
        self.data_provider = self
        self.timed_lyrics = []
        self.timed_lyric_units = []
        self.timed_lyric_timing_kinds = []

    def _estimated_media_position(self, _now):
        return self.media_position_seconds

    def _run_background(self, _name, callback):
        callback()


def _character_units(text: str, start: float) -> tuple[TimedLyricUnit, ...]:
    return tuple(
        TimedLyricUnit(start + index * 0.16, start + (index + 1) * 0.16, char)
        for index, char in enumerate(text)
    )


class MusicPresentationEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.image = QImage(960, 540, QImage.Format.Format_RGB32)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _render_at(self, now: float, position: float):
        self.owner.media_position_seconds = position
        self.owner.media_position_sampled_at = now
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(self.image)

    def test_final_staggered_duet_keeps_first_voices_bracket_and_translation(self):
        first_primary = "薛：I sing (echo)"
        second_primary = "刘：I answer (reply)"
        self.owner.timed_lyrics = [
            (0.0, "Opening line"),
            (5.0, "Another opening line"),
            (10.0, first_primary + "\n翻译：我唱（回声）"),
            (10.75, second_primary + "\n翻译：我答（回应）"),
        ]
        self.owner.timed_lyric_units = [
            (), (),
            _character_units(first_primary, 10.0),
            _character_units(second_primary, 10.75),
        ]
        self.owner.timed_lyric_timing_kinds = ["line", "line", "unit", "unit"]

        self._render_at(100.0, 10.5)
        self._render_at(100.2, 10.7)
        self.assertEqual(len(self.window._display_duet_presentation.groups), 1)
        self.assertEqual(self.window._display_timed_units, tuple(self.owner.timed_lyric_units))
        self.assertEqual(self.window._display_timed_kinds, ("line", "line", "unit", "unit"))
        self.assertEqual(self.window._song_layouts[2][0].text(), "I sing")
        self.assertEqual(self.window._song_secondary_layouts[2][0].text(), "我唱")
        self.assertEqual(self.window._song_parenthetical_layouts[2][0].text(), "echo")
        self.assertEqual(self.window._song_parenthetical_translation_layouts[2][0].text(), "回声")

        # The second voice starts before the first voice finishes. Both sung
        # brackets and their translations must stay coupled to their voices.
        self._render_at(100.6, 11.5)
        captured: dict[str, tuple[float, float]] = {}

        def capture(_painter, row, text, _font, _layout, progress, **_kwargs):
            if text in {"echo", "回声", "reply", "回应"}:
                captured[text] = (row.top(), progress)

        with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
            self._render_at(101.0, 12.0)
        self.assertEqual(self.window._lyric_motion.active_index, 3)
        self.assertEqual(set(captured), {"echo", "回声", "reply", "回应"})
        self.assertGreater(captured["回声"][0], captured["echo"][0])
        self.assertGreater(captured["回应"][0], captured["reply"][0])
        self.assertAlmostEqual(captured["echo"][1], captured["回声"][1])
        self.assertAlmostEqual(captured["reply"][1], captured["回应"][1])

    def test_bracket_translation_shares_entrance_and_remains_after_reading(self):
        self.owner.artist = "Solo"
        self.owner.media_track_id = "single-bracket-entrance"
        self.owner.timed_lyrics = [
            (0.0, "First line"),
            (10.0, "Shine (echo) on me\n翻译：照耀（回声）我"),
            (20.0, "Third line"),
            (30.0, "Fourth line"),
        ]
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["line"] * 4

        self._render_at(100.0, 0.5)
        factors = self.window._parenthetical_reserve_factors(0, 100.0)
        self.assertEqual(factors[1], 0.0)
        bracket = self.window._song_parenthetical_layouts[1]
        translated = self.window._song_parenthetical_translation_layouts[1]
        self.assertIsNotNone(bracket)
        self.assertIsNotNone(translated)
        self.assertAlmostEqual(translated[2], bracket[2] + bracket[1])
        self.assertGreaterEqual(
            self.window._song_parenthetical_extra_heights[1],
            translated[2] + translated[1] - self.window._song_row_heights[1],
        )

        def captured_at(now: float, position: float):
            layers = {}

            def capture(_painter, row, text, _font, _layout, progress, **_kwargs):
                if text in {"echo", "回声"}:
                    layers[text] = (row.top(), progress)

            with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
                self._render_at(now, position)
            return layers

        # Start the entrance, then sample its moving phase and settled phase.
        self._render_at(100.5, 10.5)
        entering = captured_at(100.62, 10.62)
        settled = captured_at(101.1, 11.1)
        self.assertEqual(set(entering), {"echo", "回声"})
        self.assertEqual(set(settled), {"echo", "回声"})
        self.assertAlmostEqual(
            entering["回声"][0] - entering["echo"][0],
            settled["回声"][0] - settled["echo"][0], delta=0.01,
        )
        self.assertAlmostEqual(entering["echo"][1], entering["回声"][1])

        self._render_at(101.5, 20.5)
        completed = {}
        native = self.window._draw_sprung_cached_wrapped_layout

        def record_completed(painter, layout, origin, text, *args, **kwargs):
            if text in {"echo", "回声"}:
                # Fractional blur recursively draws weighted cached layers.
                # Inspect the logical layer's opacity before those weights.
                completed.setdefault(text, (origin.y(), painter.opacity(), kwargs.get("blur_level")))
            return native(painter, layout, origin, text, *args, **kwargs)

        with patch.object(self.window, "_draw_sprung_cached_wrapped_layout", side_effect=record_completed):
            self._render_at(101.65, 20.65)
        self.assertEqual(set(completed), {"echo", "回声"})
        self.assertAlmostEqual(
            completed["回声"][0] - completed["echo"][0],
            settled["回声"][0] - settled["echo"][0], delta=0.01,
        )
        self.assertAlmostEqual(completed["echo"][1], completed["回声"][1])
        self.assertGreater(completed["echo"][1], 0.90)
        self.assertEqual(completed["echo"][2], completed["回声"][2])

    def test_next_rows_translation_and_both_bracket_layers_are_queued_for_prewarm(self):
        self.owner.artist = "Solo"
        self.owner.media_track_id = "next-annotation-prewarm"
        self.owner.music_sparse_highlight_seconds_per_unit = 100.0
        self.owner.timed_lyrics = [
            (0.0, "First line"),
            (10.0, "Shine (echo) on me\n翻译：照耀（回声）我"),
            (20.0, "Third line"),
            (30.0, "Fourth line"),
        ]
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["line"] * 4

        for target in ("照耀我", "echo", "回声"):
            with self.subTest(layer=target):
                with patch.object(
                    self.window, "_lyric_reading_assets_ready",
                    side_effect=lambda text, *_args: text != target,
                ):
                    self._render_at(100.0, 0.5)
                self.assertIsNotNone(self.window._pending_reading_prewarm)
                self.assertEqual(self.window._pending_reading_prewarm[1], target)


if __name__ == "__main__":
    unittest.main()
