"""Song-page output glass follows the scene without hover-driven expansion."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFocusEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from local.audio_output_panel import AudioOutputPanel
from local.music_surface_window import MusicSurfaceWindow
from tests.test_audio_output_panel import _RouteProvider


class _SongPage(QWidget):
    def __init__(self):
        super().__init__()
        self.mode = "dark"
        self.owner = SimpleNamespace(settings_getter=lambda key, default=None:
            self.mode if key == "v2_theme_mode" else default)
        self.resize(960, 600)
        self._background_cache = QImage(240, 150, QImage.Format.Format_RGB32)
        self._background_cache_key = (self.width(), self.height())
        self._background_cache.fill(QColor('#339f79'))

    def _background_sample_colors(self, rect):
        return (self._background_cache.pixelColor(0, 0),)

    def _paint_surface_glass(self, *args, **kwargs):
        pass


class AudioOutputPanelGlassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = _SongPage()
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.page.show()
        self.provider = _RouteProvider()
        self.panel = AudioOutputPanel(self.provider, self.page)
        self.panel.open_at(QPoint(920, 60))
        self.panel._animation_timer.stop()
        self.panel._poll_timer.stop()
        self.panel._reveal = self.panel._reveal_target = 1.
        self.panel._reveal_velocity = 0.
        self.panel._apply_reveal_pose()
        self.panel._capture_material(force=True)
        self.provider.calls.clear()

    def tearDown(self):
        self.panel.dismiss(immediate=True)
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def test_material_preserves_green_scene_with_one_mild_theme_veil(self):
        with patch.object(self.page, "render", side_effect=AssertionError("cached scene exists")):
            self.panel._capture_material(force=True)
        color = self.panel._material.pixelColor(self.panel._material.width() // 2,
                                                self.panel._material.height() // 2)
        self.assertGreater(color.green(), color.red() + 60)
        self.assertGreater(color.blue(), color.red() + 30)
        self.assertGreater(color.green(), 115)
        row = self.panel._rows['local']
        self.assertTrue(row.property('sharedPanelGlass'))
        self.assertFalse(row.property('glassRefractive'))

    def test_shell_retains_translucency_and_uses_shared_edge_renderer(self):
        self.panel.scroll.hide()
        self.panel.status_label.hide()
        self.panel._scroll_opacity.setOpacity(1.)
        image = QImage(self.panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        from local.audio_output_panel import paint_liquid_glass
        with patch('local.audio_output_panel.paint_liquid_glass', wraps=paint_liquid_glass) as edge:
            self.panel.render(image)
        self.assertTrue(any(call.args[1] == self.panel.render_pose.shell_rect for call in edge.call_args_list))
        center = image.pixelColor(self.panel.width() // 2, self.panel.height() // 2)
        self.assertGreater(center.alpha(), 180)
        self.assertLess(center.alpha(), 240)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_scene_and_existing_theme_choose_readable_ink_without_grey_replacement(self):
        for mode in ('dark', 'light'):
            self.page.mode = mode
            for color in (QColor('#1d3427'), QColor('#c4eec7'), QColor('#777d79')):
                self.page._background_cache.fill(color)
                self.panel._sync_surface_style()
                self.assertEqual(self.panel._theme_light, mode == 'light')
                self.assertGreaterEqual(MusicSurfaceWindow._contrast_ratio(self.panel._ink, self.panel._background), 4.5)
                self.assertGreaterEqual(MusicSurfaceWindow._contrast_ratio(self.panel._muted, self.panel._background), 4.5)

    def test_song_page_output_uses_the_same_decided_ink_as_other_controls(self):
        for ink in (QColor('white'), QColor('black'), QColor('#8f8f8f')):
            with patch.object(self.page, '_scene_control_ink', return_value=ink, create=True):
                self.panel._sync_surface_style()
                self.assertEqual(self.panel._ink, ink)
                self.assertEqual(self.panel._muted, ink)
                self.assertTrue(all(row.ink == ink for row in self.panel._rows.values()))

    def test_enter_and_stationary_hover_do_not_open_details_or_change_typography(self):
        row = self.panel._rows['local']
        geometry, font_size = row.geometry(), row.name_font_size
        point = QPointF(row.mapToGlobal(row.rect().center()))
        with patch('local.audio_output_panel.time.monotonic', return_value=100.):
            self.panel._update_volume_hover(point, 100.)
        with patch('local.audio_output_panel.time.monotonic', return_value=101.):
            self.panel._update_volume_hover(point, 101.)
            self.panel._reveal_hover_details()
        row.advance(1.)
        self.assertTrue(self.panel.property('controlHoverDisabled'))
        self.assertIsNone(getattr(row, '_control_hover_registration', None))
        self.assertFalse(self.panel._hover_detail_timer.isActive())
        self.assertFalse(row.details_visible)
        self.assertEqual(row.name_font_size, font_size)
        self.assertEqual(row.geometry(), geometry)

    def test_slider_click_and_keyboard_still_adjust_volume_with_fixed_name_size(self):
        row = self.panel._rows['local']
        row.volume_slider.setFocus(Qt.FocusReason.TabFocusReason)
        self.app.sendEvent(row.volume_slider, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
        row.advance(1.)
        self.assertTrue(row.details_visible)
        self.assertEqual(row.name_font_size, 14)
        self.assertTrue(row.volume_slider.isEnabled())
        QTest.keyClick(row.volume_slider, Qt.Key.Key_Right)
        self.assertTrue(any(call[0] == 'volume' and call[1] == 'local' for call in self.provider.calls))


if __name__ == '__main__':
    unittest.main()
