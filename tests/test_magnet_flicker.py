"""The magnet tile does not flicker while the island is dragged over it."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import TaskbarDropOverlay


class MagnetOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_repeated_moves_raise_once_and_repaint_only_on_change(self):
        overlay = TaskbarDropOverlay()
        self.addCleanup(overlay.deleteLater)
        screen, tile = QRectF(0, 0, 1920, 1080), QRectF(600, 1040, 192, 40)
        with patch.object(TaskbarDropOverlay, "_raise_as_topmost") as raised, \
                patch.object(TaskbarDropOverlay, "update") as repainted:
            for _ in range(30):                       # thirty mouse moves over the tile
                overlay.show_magnet(screen, tile, "吸附", False, radius=8)
            self.assertEqual(raised.call_count, 1)
            self.assertEqual(repainted.call_count, 1)
            overlay.show_magnet(screen, tile, "松手吸附", False, radius=8)
            self.assertEqual(repainted.call_count, 2)  # the caption changed
            self.assertEqual(raised.call_count, 1)
        overlay.hide()


if __name__ == "__main__":
    unittest.main()


class IslandHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_island_says_what_a_release_does_instead_of_a_tile(self):
        from types import SimpleNamespace
        from local.island_stack_visual import IslandStackVisual
        visual = IslandStackVisual()
        try:
            tile = QRectF(800, 8, 320, 46)
            visual._magnet_latch = SimpleNamespace(pinned=False, armed=False)
            visual._magnet_overlap = 0.2
            visual._sync_magnet_overlay(tile, "吸附", False)
            self.assertEqual(visual._magnet_hint, "near")
            visual._magnet_latch.armed = True
            visual._sync_magnet_overlay(tile, "松手吸附", False)
            self.assertEqual(visual._magnet_hint, "armed")
            visual._magnet_latch.pinned = True
            visual._sync_magnet_overlay(tile, "", True)
            self.assertEqual(visual._magnet_hint, "pinned")
            self.assertEqual(visual.MAGNET_HINTS["pinned"], "松手固定")
            # No tile layer is ever shown.
            overlay = getattr(visual, "taskbar_drop_overlay", None)
            self.assertTrue(overlay is None or not overlay.isVisible())
            visual._hide_magnet_overlay()
            self.assertEqual(visual._magnet_hint, "")
            visual.grab()                                   # paints without a hint
        finally:
            visual.close()
