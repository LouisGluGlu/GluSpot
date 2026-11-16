"""Status lamp, playlist fill, island lyric edge, and the library error toast."""
import unittest

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.island_stack_visual import IslandStackVisual
from local.library_error_toast import (
    FADE_SECONDS, VISIBLE_SECONDS, LibraryErrorToast, error_toast_rect, paint_library_error_toast,
)
from local.music_surface_layout import playlist_column_for_cover, queue_selection_radius
from local.personal_dj_icon import personal_dj_fixed_status_light, personal_dj_icon_bounds
from local.playback_entry_window import _entry_font


class StatusLightTests(unittest.TestCase):
    def test_light_stays_on_the_icon_corner_and_idle_moves_inward(self):
        button = QRectF(10, 20, 36, 36)
        speaking = personal_dj_fixed_status_light(button, idle=False)
        idle = personal_dj_fixed_status_light(button, idle=True)
        inner = button.adjusted(button.width() * 0.18, button.height() * 0.14,
                                -button.width() * 0.18, -button.height() * 0.14)
        glyph = personal_dj_icon_bounds(inner, bands=None, elapsed=0.0)
        self.assertAlmostEqual(speaking.center().x(), glyph.right(), delta=0.01)
        self.assertAlmostEqual(speaking.center().y(), glyph.bottom(), delta=0.01)
        self.assertLess(idle.center().x(), speaking.center().x())
        self.assertLess(idle.center().y(), speaking.center().y())
        # Ring travel must not move the lamp. The helper ignores elapsed.
        again = personal_dj_fixed_status_light(button, idle=False)
        self.assertEqual(again, speaking)


class PlaylistFillTests(unittest.TestCase):
    def test_missing_cover_expands_the_playlist_and_keeps_corners_inset(self):
        surface = QRectF(0, 0, 360, 640)
        cover = QRectF(20, -180, 96, 96)
        short = QRectF(20, 220, 320, 260)
        filled = playlist_column_for_cover(surface, cover, short, scale=1.0)
        self.assertLess(filled.top(), 20)
        self.assertGreater(filled.bottom(), 600)
        self.assertGreater(filled.height(), short.height())
        self.assertGreaterEqual(filled.left(), 8)
        self.assertLessEqual(filled.right(), surface.right() - 8)

    def test_visible_cover_keeps_the_shorter_column(self):
        surface = QRectF(0, 0, 360, 640)
        cover = QRectF(20, 70, 96, 96)
        column = QRectF(20, 190, 320, 360)
        self.assertEqual(playlist_column_for_cover(surface, cover, column, scale=1.0), column)

    def test_selection_radius_reads_as_a_rounded_card(self):
        height = 52.0
        radius = queue_selection_radius(height)
        self.assertAlmostEqual(radius, height * 0.20)
        self.assertGreater(radius, 8.0)
        self.assertLess(radius, height * 0.5)


class IslandLyricEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_expanded_lyrics_share_the_cover_left_edge(self):
        visual = IslandStackVisual()
        visual.timer.stop()
        try:
            rect = QRectF(0, 0, 420, 220)
            cover = QRectF(rect.left() + visual._u(16), rect.top() + visual._u(14), visual._u(58), visual._u(58))
            viewport = visual._expanded_lyric_viewport(rect, cover)
            self.assertAlmostEqual(viewport.left(), cover.left())
            self.assertGreater(viewport.top(), cover.bottom())
            text = "短句"
            from local.island_stack_visual import IslandStackVisual as _Visual
            row = QRectF(viewport.left(), viewport.top(), viewport.width(), 28)
            # A fitting line starts on the cover's left edge when alignment is left.
            width = 40.0
            self.assertEqual(visual._island_lyric_row_x(row, width, False, {}), row.left())
            self.assertGreater(visual._island_lyric_row_x(row, width, True, {}), row.left())
            self.assertIs(_Visual, IslandStackVisual)
        finally:
            visual.close()


class LibraryErrorToastTests(unittest.TestCase):
    def test_toast_holds_ten_seconds_then_fades(self):
        toast = LibraryErrorToast()
        toast.present("音频连接失败，请检查网络后重试", 0.0)
        self.assertEqual(toast.opacity(0.0), 1.0)
        self.assertEqual(toast.opacity(VISIBLE_SECONDS), 1.0)
        mid = toast.opacity(VISIBLE_SECONDS + FADE_SECONDS * 0.5)
        self.assertLess(mid, 1.0)
        self.assertGreater(mid, 0.0)
        self.assertEqual(toast.opacity(VISIBLE_SECONDS + FADE_SECONDS), 0.0)
        self.assertTrue(toast.finished(VISIBLE_SECONDS + FADE_SECONDS))

    def test_pill_is_centered_just_above_the_player(self):
        font = _entry_font(13)
        rect = error_toast_rect(960, 700, "音频连接失败，请检查网络后重试", font)
        self.assertLess(rect.bottom(), 700)
        self.assertGreater(rect.bottom(), 700 - 48)
        self.assertAlmostEqual(rect.center().x(), 480, delta=1.0)

    def test_paint_uses_a_glass_pill_with_ink(self):
        app = QApplication.instance() or QApplication([])
        image = QImage(400, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)

        host = QWidget()
        host.resize(400, 80)
        rect = QRectF(40, 16, 280, 36)
        with QPainter(image) as painter:
            paint_library_error_toast(painter, host, rect, "音频连接失败，请检查网络后重试", 1.0)
        host.close()
        self.assertGreater(image.pixelColor(int(rect.center().x()), int(rect.center().y())).alpha(), 40)
        edge = image.pixelColor(int(rect.left() + 2), int(rect.center().y()))
        self.assertGreater(max(edge.red(), edge.green(), edge.blue(), edge.alpha()), 0)
        self.assertIs(app, QApplication.instance())
        self.assertNotEqual(QColor(edge), QColor(0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
