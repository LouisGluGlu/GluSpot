"""Song-page output glass follows the scene without hover-driven expansion."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFocusEvent, QImage, QPainter
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

    def test_available_rows_follow_scene_ink_while_selected_rows_keep_black_ink(self):
        for ink in (QColor('white'), QColor('black'), QColor('#8f8f8f')):
            with patch.object(self.page, '_scene_control_ink', return_value=ink, create=True):
                self.panel._sync_surface_style()
                self.assertEqual(self.panel._ink, ink)
                self.assertEqual(self.panel._muted, ink)
                for row in self.panel._rows.values():
                    expected = QColor('black') if row.state == 'active' else ink
                    self.assertEqual(row.ink, expected)
                    if row.state == 'active':
                        self.assertEqual(row.muted, expected)
                        self.assertEqual(row.accent, expected)

    def test_confirmed_members_keep_white_face_and_black_content_in_native_and_morph_rows(self):
        self.provider.snapshot = replace(self.provider.snapshot, state='casting',
            member_ids=('local', 'bluetooth'), group_id='fixture-group')
        self.panel.sync_snapshot()
        self.panel._animation_timer.stop()
        for mode in ('dark', 'light'):
            self.page.mode = mode
            for color in (QColor('#339f79'), QColor('#d07732')):
                self.page._background_cache.fill(color)
                with patch.object(self.page, '_scene_control_ink', return_value=QColor('white'), create=True):
                    self.panel._sync_surface_style()
                for identity in ('local', 'bluetooth'):
                    row = self.panel._rows[identity]
                    self.assertEqual(row.ink, QColor('black'))
                    self.assertEqual(row.volume_slider.ink, QColor('black'))
                    self.assertEqual(row.volume_slider.fill, QColor('#0a84ff'))
                    # Native child painting and transformed unfolding painting
                    # must both show the member state immediately, even before
                    # its normal selection-intensity animation has advanced.
                    row.progress = 0.
                    for morph in (False, True):
                        with self.subTest(mode=mode, scene=color.name(), member=identity, morph=morph):
                            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
                            image.fill(color)
                            if morph:
                                with QPainter(image) as painter:
                                    self.panel._paint_morph_row(painter, row, QRectF(row.rect()))
                            else:
                                row.render(image)
                            face = image.pixelColor(round(row.width() * .65), row.height() - 7)
                            self.assertGreaterEqual(min(face.getRgb()[:3]), 240)
                            self.assertLessEqual(max(face.getRgb()[:3]) - min(face.getRgb()[:3]), 4)
                            # Actual device-name pixels remain black against
                            # that white face; do not just check the ink field.
                            dark_name_pixels = sum(
                                image.pixelColor(x, y).lightness() < 70
                                for y in range(9, 26) for x in range(53, 124))
                            self.assertGreater(dark_name_pixels, 15)
                available = self.panel._rows['homepod']
                self.assertEqual(available.ink, QColor('white'))
                image = QImage(available.size(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(color)
                with QPainter(image) as painter:
                    available.paint_contents(painter)
                face = image.pixelColor(round(available.width() * .65), available.height() - 7)
                self.assertLess(min(face.getRgb()[:3]), 225)

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

    def test_selected_face_remains_white_and_glass_thumb_visible_even_at_zero_glass_strength(self):
        row = self.panel._rows['local']
        slider = row.volume_slider
        geometry = slider.geometry()
        for level in (0, 50, 100):
            self.page.owner.settings_getter = lambda key, default=None, value=level: (
                value if key == 'v2_music_glass_level' else self.page.mode if key == 'v2_theme_mode' else default)
            self.panel._sync_surface_style()
            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor('#14161b'))
            with QPainter(image) as painter:
                row.paint_contents(painter)
            face = image.pixelColor(round(row.width() * .65), row.height() - 7)
            self.assertGreaterEqual(min(face.getRgb()[:3]), 240)
            fraction = slider.value() / 100.
            thumb = image.pixelColor(round(slider.x() + 8 + (slider.width() - 16) * fraction),
                                     slider.y() + slider.height() // 2)
            self.assertGreater(abs(face.lightness() - thumb.lightness()), 8)
            self.assertEqual(slider.geometry(), geometry)

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

    def test_actual_gain_segment_stays_blue_across_scene_theme_and_glass_strength(self):
        slider = self.panel._rows['local'].volume_slider
        slider.setValue(50)
        for mode in ('dark', 'light'):
            self.page.mode = mode
            for color in (QColor('#339f79'), QColor('#d07732')):
                self.page._background_cache.fill(color)
                for level in (0, 50, 100):
                    with self.subTest(mode=mode, scene=color.name(), glass=level):
                        self.page.owner.settings_getter = lambda key, default=None, value=level: (
                            value if key == 'v2_music_glass_level' else
                            self.page.mode if key == 'v2_theme_mode' else default)
                        self.panel._sync_surface_style()
                        self.panel._capture_material(force=True)
                        image = QImage(slider.size(), QImage.Format.Format_ARGB32_Premultiplied)
                        image.fill(Qt.GlobalColor.transparent)
                        with QPainter(image) as painter:
                            slider.paint_contents(painter)
                        # Sample away from both the rounded end and the thumb.
                        read = image.pixelColor(round(8 + (slider.width() - 16) * .25),
                                                slider.height() // 2)
                        unread = image.pixelColor(round(8 + (slider.width() - 16) * .75),
                                                  slider.height() // 2)
                        self.assertEqual(read, QColor('#0a84ff'))
                        self.assertGreater(read.blue() - read.green(),
                                           unread.blue() - unread.green() + 60)

    def test_zero_gain_has_no_blue_read_segment_and_full_gain_fills_the_track(self):
        slider = self.panel._rows['local'].volume_slider
        for value in (0, 100):
            slider.setValue(value)
            image = QImage(slider.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                slider.paint_contents(painter)
            for fraction in (.25, .75):
                sample = image.pixelColor(round(8 + (slider.width() - 16) * fraction),
                                          slider.height() // 2)
                if value:
                    self.assertEqual(sample, QColor('#0a84ff'))
                else:
                    self.assertLess(sample.blue() - sample.green(), 40)

    def test_linked_setting_changes_shell_alpha_and_slider_scene_material(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter
        from local.audio_output_panel import paint_liquid_glass
        self.panel.scroll.hide()
        self.panel.status_label.hide()
        self.panel._scroll_opacity.setOpacity(1.)
        alpha = []
        keys = []
        for level in (10, 50, 90):
            self.page.owner.settings_getter = lambda key, default=None, value=level: (
                value if key == 'v2_music_glass_level' else self.page.mode if key == 'v2_theme_mode' else default)
            with patch.object(self.page, 'render', side_effect=AssertionError('use displayed scene')):
                self.panel._sync_surface_style()
                self.panel._capture_material(force=True)
            keys.append(self.panel._material_key)
            image = QImage(self.panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            self.panel.render(image)
            alpha.append(image.pixelColor(self.panel.width() // 2, self.panel.height() // 2).alpha())
            slider = self.panel._rows['local'].volume_slider
            slider_image = QImage(slider.size(), QImage.Format.Format_ARGB32_Premultiplied)
            slider_image.fill(Qt.GlobalColor.transparent)
            with QPainter(slider_image) as painter, \
                    patch('local.audio_output_panel.paint_liquid_glass', wraps=paint_liquid_glass) as paint:
                slider.paint_contents(painter)
            self.assertEqual(len(paint.call_args_list), 2)
            for call in paint.call_args_list:
                self.assertTrue(call.kwargs['refractive'])
                self.assertEqual(call.kwargs['material_level'], level)
                self.assertIsInstance(call.kwargs['backdrop'], QImage)
                self.assertIsInstance(call.kwargs['backdrop_rect'], QRectF)
            center = self.panel._material.pixelColor(self.panel._material.width() // 2, self.panel._material.height() // 2)
            self.assertGreater(center.green(), center.red() + 40)
        self.assertTrue(alpha[0] < alpha[1] < alpha[2], alpha)
        self.assertEqual(len(set(keys)), 3)


if __name__ == '__main__':
    unittest.main()
