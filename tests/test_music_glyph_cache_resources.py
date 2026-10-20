from __future__ import annotations

import unittest

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class MusicGlyphCacheResourceTests(unittest.TestCase):
    """Native pixel accounting and pressure must preserve the visible effect.

    Fixed limits apply to cold cache contents. The active lyric and one partial
    build are measured working sets and may exceed a small test budget.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.window = MusicSurfaceWindow(self.owner)
        self.font = self.window._surface_font(28, QFont.Weight.Bold)
        self.unread = QColor(155, 95, 190)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _assert_accounting(self):
        window = self.window
        self.assertEqual(window._glyph_run_cache_bytes, sum(
            int(image.sizeInBytes()) for image, _boundaries in window._glyph_run_cache.values()))
        self.assertEqual(window._isolated_glyph_cache_bytes, sum(
            int(image.sizeInBytes()) for units in window._isolated_glyph_cache.values()
            for image, _left in units))
        for build in window._isolated_glyph_builds.values():
            self.assertEqual(build["bytes"], sum(int(image.sizeInBytes()) for image, _left in build["result"]))
        self.assertEqual(window._isolated_glyph_builds_bytes, sum(
            build["bytes"] for build in window._isolated_glyph_builds.values()))
        self.assertEqual(window._glyph_tint_cache_bytes, sum(
            int(image.sizeInBytes()) for image in window._glyph_tint_cache.values()))
        self.assertEqual(window._sparse_glyph_halo_cache_bytes, sum(
            int(image.sizeInBytes()) for image, _padding in window._sparse_glyph_halo_cache.values()))

    def _assert_cold_shape_bounds(self):
        window = self.window
        protected = window._emphasis_protected_glyph_runs
        for cache, count_limit, byte_limit, working, size_of in (
            (window._glyph_run_cache, window.GLYPH_RUN_CACHE_MAX_ENTRIES,
             window.GLYPH_RUN_CACHE_MAX_BYTES, window._glyph_cache_key,
             lambda value: int(value[0].sizeInBytes())),
            (window._isolated_glyph_cache, window.ISOLATED_GLYPH_CACHE_MAX_ENTRIES,
             window.ISOLATED_GLYPH_CACHE_MAX_BYTES, None,
             lambda value: sum(int(image.sizeInBytes()) for image, _left in value)),
            (window._isolated_glyph_builds, window.ISOLATED_GLYPH_BUILD_MAX_ENTRIES,
             window.ISOLATED_GLYPH_BUILD_MAX_BYTES, window._isolated_glyph_build_last_key,
             lambda value: value["bytes"]),
        ):
            cold = [value for key, value in cache.items() if key not in protected and key != working]
            self.assertLessEqual(len(cold), count_limit)
            self.assertLessEqual(sum(size_of(value) for value in cold), byte_limit)

    def _small_budgets(self):
        window = self.window
        window.GLYPH_RUN_CACHE_MAX_ENTRIES = 3
        window.GLYPH_RUN_CACHE_MAX_BYTES = 4096
        window.ISOLATED_GLYPH_CACHE_MAX_ENTRIES = 3
        window.ISOLATED_GLYPH_CACHE_MAX_BYTES = 8192
        window.ISOLATED_GLYPH_BUILD_MAX_ENTRIES = 2
        window.ISOLATED_GLYPH_BUILD_MAX_BYTES = 4096

    def _prepare_current(self, text, color=None):
        color = color or self.unread
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 700.0)
        self.window._protect_lyric_emphasis_assets(text, self.font, layout, unread_color=color)
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            segment = self.window._utf16_slice(text, line.textStart(), line.textLength())
            for source, _left in self.window._isolated_glyph_units(segment, self.font, "character"):
                self.window._tinted_glyph_image(source, color)
                self.window._sparse_glyph_halo(source)
        return layout

    def test_runs_and_completed_units_have_count_and_byte_bounds(self):
        self._small_budgets()
        for index in range(35):
            text = f"Line {index} has shaped words and punctuation."
            image, boundaries = self.window._glyph_run(text, self.font, "reading")
            self.assertFalse(image.isNull())
            self.assertGreater(len(boundaries), 2)
            units = self.window._isolated_glyph_units(text, self.font, "reading")
            self.assertGreater(len(units), 2)
            self.assertTrue(all(not image.isNull() for image, _left in units))
            self._assert_accounting()
            self._assert_cold_shape_bounds()
        self.assertEqual(self.window._isolated_glyph_builds_bytes, 0)

    def test_partial_build_survives_its_budget_and_transfers_bytes_on_completion(self):
        self._small_budgets()
        for index in range(12):
            _units, done = self.window._advance_isolated_glyph_units(
                f"Partial phrase {index} keeps progressing", self.font, "character", max_units=1)
            self.assertFalse(done)
            self._assert_accounting()
            self._assert_cold_shape_bounds()
        text = "Current partial line completes under a very small budget"
        key = (text, self.font.toString(), "character")
        _units, done = self.window._advance_isolated_glyph_units(text, self.font, "character", max_units=1)
        self.assertFalse(done)
        self.assertIn(key, self.window._isolated_glyph_builds)
        first_size = self.window._isolated_glyph_builds[key]["bytes"]
        _units, done = self.window._advance_isolated_glyph_units(text, self.font, "character", max_units=1)
        self.assertFalse(done)
        self.assertGreater(self.window._isolated_glyph_builds[key]["bytes"], first_size)
        units, done = self.window._advance_isolated_glyph_units(text, self.font, "character")
        self.assertTrue(done)
        self.assertNotIn(key, self.window._isolated_glyph_builds)
        self.assertGreater(len(units), 10)
        self._assert_accounting()
        self._assert_cold_shape_bounds()

    def test_cached_complete_row_releases_previous_partial_working_set_exception(self):
        self._small_budgets()
        self.window.ISOLATED_GLYPH_CACHE_MAX_BYTES = 1024 * 1024
        self.window._isolated_glyph_units("Cached complete row", self.font, "character")
        text = "Old partial working set should become cold on a cached row hit"
        key = (text, self.font.toString(), "character")
        self.window._advance_isolated_glyph_units(text, self.font, "character", max_units=8)
        self.assertIn(key, self.window._isolated_glyph_builds)
        self.assertGreater(self.window._isolated_glyph_builds[key]["bytes"],
                           self.window.ISOLATED_GLYPH_BUILD_MAX_BYTES)
        _units, complete = self.window._advance_isolated_glyph_units(
            "Cached complete row", self.font, "character", max_units=1)
        self.assertTrue(complete)
        self._assert_accounting()
        self._assert_cold_shape_bounds()

    def test_ready_current_effect_and_partial_current_run_survive_future_pressure(self):
        self._small_budgets()
        text = "A visible lyric retains its own prepared gentle glow"
        layout = self._prepare_current(text)
        self.window.GLYPH_TINT_CACHE_MAX_ENTRIES = 2
        self.window.GLYPH_TINT_CACHE_MAX_BYTES = 4096
        self.window.SPARSE_GLYPH_HALO_CACHE_MAX_ENTRIES = 2
        self.window.SPARSE_GLYPH_HALO_CACHE_MAX_BYTES = 4096
        for index in range(15):
            self.window._advance_isolated_glyph_units(
                f"Future partial {index} progresses slowly", self.font, "character", max_units=1)
            self.window._isolated_glyph_units(f"Future completed {index}", self.font, "character")
            self.assertTrue(self.window._lyric_emphasis_assets_ready(
                text, self.font, 700.0, layout, "scatter_glow", self.unread))
            self._assert_accounting()
            self._assert_cold_shape_bounds()
        self.window._protect_lyric_emphasis_assets("", self.font, None)
        self._assert_accounting()
        self._assert_cold_shape_bounds()
        self.assertLessEqual(len(self.window._glyph_tint_cache), 2)
        self.assertLessEqual(self.window._glyph_tint_cache_bytes, 4096)
        self.assertLessEqual(len(self.window._sparse_glyph_halo_cache), 2)
        self.assertLessEqual(self.window._sparse_glyph_halo_cache_bytes, 4096)

    def test_protected_partial_build_keeps_its_progress_when_next_row_is_started(self):
        self._small_budgets()
        text = "Current partial lyric is not restarted by the next prewarm"
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 700.0)
        self.window._protect_lyric_emphasis_assets(text, self.font, layout, unread_color=self.unread)
        first_line = layout.lineAt(0)
        segment = self.window._utf16_slice(text, first_line.textStart(), first_line.textLength())
        key = (segment, self.font.toString(), "character")
        self.window._advance_isolated_glyph_units(segment, self.font, "character", max_units=2)
        current_build = self.window._isolated_glyph_builds[key]
        for index in range(12):
            self.window._advance_isolated_glyph_units(
                f"Upcoming build {index}", self.font, "character", max_units=1)
        self.assertIs(self.window._isolated_glyph_builds[key], current_build)
        self.assertEqual(current_build["next_index"], 2)
        self.window._advance_isolated_glyph_units(segment, self.font, "character", max_units=1)
        self.assertEqual(current_build["next_index"], 3)
        self._assert_accounting()
        self._assert_cold_shape_bounds()

    def test_old_tint_colors_are_cold_but_current_tint_stays_ready(self):
        text = "Colors keep the current glow prepared"
        layout = self._prepare_current(text)
        self.window.GLYPH_TINT_CACHE_MAX_ENTRIES = 1
        self.window.GLYPH_TINT_CACHE_MAX_BYTES = 1024
        sources = [source for source, _left in self.window._isolated_glyph_cache[
            next(iter(self.window._emphasis_protected_glyph_runs))]]
        current_rgba = QColor(self.unread).rgba()
        for index in range(20):
            for source in sources:
                self.window._tinted_glyph_image(source, QColor.fromHsv(index * 17 % 360, 180, 200))
            self.assertTrue(self.window._lyric_emphasis_assets_ready(
                text, self.font, 700.0, layout, "scatter_glow", self.unread))
            self.assertTrue(all(key[1] == current_rgba for key in self.window._glyph_tint_cache))
            self._assert_accounting()
        next_color = QColor(42, 133, 188)
        self.window._protect_lyric_emphasis_assets(text, self.font, layout, unread_color=next_color)
        for source in sources:
            self.window._tinted_glyph_image(source, next_color)
        self.assertTrue(self.window._lyric_emphasis_assets_ready(
            text, self.font, 700.0, layout, "scatter_glow", next_color))
        self.assertTrue(all(key[1] == next_color.rgba() for key in self.window._glyph_tint_cache))
        self._assert_accounting()


if __name__ == "__main__":
    unittest.main()
