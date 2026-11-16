"""Library cards follow the available width and do not flip on a one-pixel wobble."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from local.library_layout_variants import LibraryVariantPresentation


class LibraryGridColumnTests(unittest.TestCase):
    def view(self, width, height=800):
        presentation = LibraryVariantPresentation.__new__(LibraryVariantPresentation)
        presentation.owner = SimpleNamespace(width=lambda: width, height=lambda: height)
        return presentation

    def test_wide_library_adds_columns_instead_of_leaving_a_side_gap(self):
        columns, side = self.view(1000)._stable_grid(1000)
        self.assertGreaterEqual(columns, 5)
        self.assertGreaterEqual(side, 132)
        self.assertLessEqual(side, 176)
        used = columns * side + 24 * (columns - 1)
        self.assertLess(1000 - used, 132 + 24)

    def test_width_wobble_near_a_column_boundary_keeps_the_previous_count(self):
        view = self.view(920)
        first, _side = view._stable_grid(920)
        nudged, _side = view._stable_grid(900)
        self.assertEqual(nudged, first)
        wider, _side = view._stable_grid(1400)
        self.assertGreater(wider, first)
