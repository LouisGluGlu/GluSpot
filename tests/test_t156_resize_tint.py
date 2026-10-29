"""Resize reveal ownership and cheap tint refresh over a frozen blur scene."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF
from PySide6.QtGui import QColor, QImage

from local.liquid_glass import ChromeBackdropTint
from tests import test_narrow_chrome as fixtures


class ResizeRevealTests(unittest.TestCase):
    setUpClass = fixtures.NarrowChromeTests.__dict__['setUpClass']
    setUp = fixtures.NarrowChromeTests.setUp
    tearDown = fixtures.NarrowChromeTests.tearDown

    def test_hold_reveals_immediately_and_cannot_idle_or_retarget(self):
        window = self.window
        window.visibility.progress = 0.
        window._set_resize_chrome_hold(True)
        generation = window.visibility.opacity.generation
        for now in (10., 20., 100.):
            window.visibility.advance(now, False, False, False)
            self.assertTrue(window._resize_chrome_hold)
            self.assertEqual(window.visibility.progress, 1.)
            self.assertEqual(window.visibility.opacity.target, 1.)
            self.assertEqual(window._control_reveal_at(now), 1.)
        self.assertEqual(window.visibility.opacity.generation, generation)

    def test_release_rearms_pointer_hysteresis_and_idle_timer(self):
        window = self.window
        window._set_resize_chrome_hold(True)
        outside = window.mapToGlobal(QPoint(window.width() // 2, window.height() // 2))
        with patch('local.music_surface_window.time.monotonic', return_value=100.), \
                patch('local.music_surface_window.QCursor.pos', return_value=outside):
            window._set_resize_chrome_hold(False)
        self.assertFalse(window._resize_chrome_hold)
        self.assertFalse(window.visibility.resize_hold)
        self.assertFalse(window._pointer_hover)
        self.assertEqual(window.visibility.idle_since, 100.)
        self.assertEqual(window.visibility.progress, 1.)
        window.visibility.advance(101.3, False, False, False)
        self.assertEqual(window.visibility.opacity.target, 0.)

    def test_lost_release_fallbacks_clear_hold(self):
        for kind in (QEvent.Type.Leave, QEvent.Type.WindowDeactivate):
            with self.subTest(kind=kind):
                self.window._set_resize_chrome_hold(True)
                self.app.sendEvent(self.window, QEvent(kind))
                self.assertFalse(self.window._resize_chrome_hold)
                self.assertFalse(self.window.visibility.resize_hold)

    def test_resize_keeps_collection_and_available_dj_in_transition_gap(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter
        from _demos.narrow3.layout import NarrowLayout
        window = self.window
        window.resize(330, 640)
        window.narrow = True
        window.dj_available = True
        window._set_resize_chrome_hold(True)
        window._width_chrome_from = window._width_control_rects(False)
        window._width_phase_from = (0., 0., 1.)
        window._width_chrome_active = True
        window._width_chrome_progress = .4
        groups = window.glass_groups()
        self.assertEqual(window._chrome_collection_reveal(), 1.)
        self.assertIn('personal_dj', groups[0])
        self.assertTrue(QRectF(window.rect()).contains(groups[0]['personal_dj']))
        collection = groups[0]['collection']
        self.assertEqual(window._live_action_at(collection.center() + QPointF(
            collection.width() * .25, 0)), 'playlist_add')
        output = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with patch.object(window, '_paint_surface_glass') as glass, QPainter(output) as painter:
            window.paint_controls(painter, NarrowLayout(330, 640, dj_available=True), 1.)
        self.assertIn('dj-icon', [call.kwargs.get('role') for call in glass.call_args_list])

    def test_resize_freezes_prepared_veil_until_release(self):
        window = self.window
        scene = QImage(64, 64, QImage.Format.Format_RGB32)
        scene.fill(QColor('orange'))
        window._background_cache = scene
        window.refresh_background_veil(100.)
        prepares = window.background_veil.veil_prepares
        window._set_resize_chrome_hold(True)
        window._background_cache.fill(QColor('blue'))
        for now in (100.1, 100.2, 100.3, 100.4, 100.5):
            window.refresh_background_veil(now)
        self.assertEqual(window.background_veil.veil_prepares, prepares)
        self.assertGreaterEqual(window._diagnostics['veil_stale_frames'], 5)
        window._set_resize_chrome_hold(False)
        window.refresh_background_veil(101.)
        self.assertEqual(window.background_veil.veil_prepares, prepares + 1)


class BackdropTintTests(unittest.TestCase):
    def scene(self, color):
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        return image

    def test_live_luma_selects_opposite_face_and_formula(self):
        bounds = QRectF(0, 0, 64, 64)
        rect = QRectF(8, 8, 32, 32)
        tint = ChromeBackdropTint()
        dark = tint.sample(self.scene('black'), bounds, rect, 1.)
        bright = tint.sample(self.scene('white'), bounds, rect, 1.2)
        middle = tint.sample(self.scene('#808080'), bounds, rect, 1.4)
        self.assertGreater(dark[1].lightness(), 200)
        self.assertLess(bright[1].lightness(), 50)
        self.assertEqual(dark[1].alpha(), bright[1].alpha())
        self.assertGreaterEqual(bright[1].alpha() - middle[1].alpha(), 12)
        self.assertEqual(bright[2].alpha(), round(bright[1].alpha() * 1.2))

    def test_epoch_and_geometry_refresh_share_one_100ms_budget(self):
        bounds = QRectF(0, 0, 64, 64)
        rect = QRectF(8, 8, 32, 32)
        scene = self.scene('black')
        tint = ChromeBackdropTint()
        initial = tint.sample(scene, bounds, rect, 1., role='chrome')
        scene.fill(QColor('white'))
        for index in range(1, 6):
            with patch('local.liquid_glass.QImage', side_effect=AssertionError('new image')):
                cached = tint.sample(scene, bounds, rect.translated(index, 0),
                                     1. + index * .01, role='chrome')
            self.assertEqual(cached, initial)
        updated = tint.sample(scene, bounds, rect, 1.101, role='chrome')
        self.assertGreater(updated[0], initial[0])
        self.assertEqual(len(tint.samples), 1)
