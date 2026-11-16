"""One reading path keeps measured word seeking and the original full lift."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTransform

from api.models import TimedLyricUnit
from local.music_lyric_motion import GLYPH_FLOAT_LIFT
from local.music_lyric_presentation import split_display_layers
from local.music_surface_window import MusicSurfaceWindow
from tests import test_narrow_chrome as fixtures


class SingleReadingSchemeTests(unittest.TestCase):
    setUpClass = fixtures.NarrowChromeTests.__dict__['setUpClass']
    setUp = fixtures.NarrowChromeTests.setUp
    tearDown = fixtures.NarrowChromeTests.tearDown

    def test_legacy_owner_values_cannot_change_original_lift_or_restore_gold(self):
        for old in (0, 1, 2, 4, 'bad'):
            self.owner.music_surface_reading_lift_level = old
            self.assertEqual(self.window._reading_lift_height(), GLYPH_FLOAT_LIFT)
        self.assertFalse(hasattr(self.window, '_reading_strategy'))
        self.assertFalse(hasattr(self.window, '_maybe_start_obo_reveal'))
        self.owner.music_surface_reading_strategy = 'gold'
        self.window._update_transport_feedback_regions(100.)
        self.assertNotIn('obo_reveal', self.window._transport_feedback)

    def test_narrow_and_wide_share_identical_reading_ink_for_the_same_row(self):
        wide = MusicSurfaceWindow(self.owner)
        self.addCleanup(wide.shutdown)
        text = '你好 see you tomorrow'
        font = wide._surface_font(38, QFont.Weight.Bold)
        images = []
        for surface in (wide, self.window):
            surface._lyric_spring_active = False
            layout, height = surface._wrapped_lyric_layout(text, font, 420.)
            image = QImage(480, 150, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                surface._paint_wrapped_lifted_lyric(
                    painter, QRectF(20., 20., 420., height), text, font, layout, .43)
            images.append(image)
        self.assertEqual(images[0], images[1])
        self.assertTrue(any(bytes(images[0].constBits())))

    def test_real_unit_row_keeps_word_targets_after_setting_deletion(self):
        text = 'See you tomorrow'
        units = (TimedLyricUnit(1., 2., 'See '), TimedLyricUnit(3., 4., 'you '),
                 TimedLyricUnit(5., 6., 'tomorrow'))
        layers = split_display_layers(text)
        font = self.window._surface_font(38, QFont.Weight.Bold)
        shaped, height = self.window._wrapped_lyric_layout(text, font, 600.)
        self.window._lyric_word_hit_track_key = (self.window._visual_track_identity(), str(getattr(self.owner, 'media_source_app', '') or ''))
        self.window._populate_lyric_word_hit_rects(
            0, text, text, layers, shaped, units, QRectF(20., 20., 600., height), (),
            QTransform(), QRectF(0., 0., 800., 200.), 0.)
        targets = list(self.window._lyric_word_hit_rects.values())
        self.assertEqual([seconds for _rect, seconds in targets], [1., 3., 5.])
        for rect, seconds in targets:
            self.assertEqual(self.window._lyric_word_target_at(rect.center())[2], seconds)
