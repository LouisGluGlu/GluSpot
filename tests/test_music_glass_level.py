"""The user's linked setting changes background opacity/blur in one direction."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local import liquid_glass as glass, settings_store
from local.music_surface_window import MusicSurfaceWindow
from local.settings_window import SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager
from tests.test_music_surface_transition import _TransitionOwner


class MusicGlassLevelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        glass.clear_glass_cache()

    def striped_scene(self):
        image = QImage(240, 120, QImage.Format.Format_RGB32)
        with QPainter(image) as painter:
            for x in range(240):
                painter.fillRect(x, 0, 1, 120, QColor('#137aaf') if x % 24 < 12 else QColor('#f8a16b'))
        return image

    def test_invalid_persisted_values_are_bounded_and_survive_reload(self):
        for raw, expected in ((None, 50), (True, 50), ('bad', 50), (float('nan'), 50),
                              (float('inf'), 50), (-1, 0), (101, 100), ('37', 37)):
            self.assertEqual(settings_store.normalize_music_glass_level(raw), expected)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'v2_music_glass_level': 300}), encoding='utf-8')
            with patch.object(settings_store, 'APP_DATA_DIR', directory), \
                    patch.object(settings_store, 'SETTINGS_FILE', str(path)):
                manager = settings_store.SettingsManager()
                self.assertEqual(manager.get('v2_music_glass_level'), 100)
                manager.set('v2_music_glass_level', 23)
                manager.save_now()
                reloaded = settings_store.SettingsManager()
                self.assertEqual(reloaded.get('v2_music_glass_level'), 23)
                manager._save_timer.stop()
                reloaded._save_timer.stop()

    def test_one_settings_slider_persists_both_material_effects_and_syncs(self):
        island, settings = FakeIslandWidget(), FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            slider = window.music_glass_level
            self.assertIn('越高越不透明', slider.toolTip())
            slider.setValue(79)
            self.assertEqual(settings.get('v2_music_glass_level'), 79)
            self.assertEqual(slider._value_label.text(), '79%')
            settings.set('v2_music_glass_level', 11)
            window.sync_from_widget()
            self.assertEqual(slider.value(), 11)
            self.assertEqual(slider._value_label.text(), '11%')
        finally:
            window.close()
            island.close()
            window.deleteLater()
            island.deleteLater()
            self.app.processEvents()

    def test_more_opaque_material_actually_has_more_alpha_and_less_scene_detail(self):
        scene = self.striped_scene()
        alpha, detail = [], []
        rect = QRectF(0, 0, 120, 60)
        for level in (0, 25, 50, 75, 100):
            image = QImage(120, 60, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                glass.paint_liquid_glass(painter, rect, refractive=True, backdrop=scene,
                    backdrop_rect=rect, material_level=level)
            alpha.append(image.pixelColor(60, 30).alpha())
            material = glass._render_glass_material(scene, QRectF(60, 30, 120, 60), 120, 60, 20, level)
            # Compare actual blur independent of compositing alpha/theme veil.
            values = [material.pixelColor(x, material.height() // 2).red()
                      for x in range(15, material.width() - 15)]
            detail.append(float(np.std(values)))
        self.assertEqual(alpha, sorted(alpha))
        self.assertGreater(alpha[-1], 245)
        self.assertEqual(alpha[0], 0)
        self.assertGreater(detail[0], detail[1])
        self.assertGreater(detail[1], detail[2])
        # Strong blur is already a flat field; bounded 8-bit resampling may
        # vary by a shade rather than decreasing strictly below one byte.
        self.assertLess(max(detail[2:]), 4., detail)
        self.assertGreater(detail[0], detail[-1] * 4)

    def test_level_change_invalidates_throttled_cache_immediately_without_unbounding_it(self):
        scene, rect = self.striped_scene(), QRectF(0, 0, 120, 60)
        images = []
        with patch('local.liquid_glass.time.monotonic', return_value=100.):
            for level in (15, 85, 15):
                image = QImage(120, 60, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    glass.paint_liquid_glass(painter, rect, refractive=True, backdrop=scene,
                        material_level=level, cache_token='one-live-control')
                images.append(image)
        self.assertNotEqual(images[0], images[1])
        self.assertEqual(images[0], images[2])
        self.assertLessEqual(glass._glass_material_cache_bytes, glass.GLASS_MATERIAL_CACHE_MAX_BYTES)
        for image in glass._glass_material_cache.values():
            self.assertLessEqual(max(image.width(), image.height()), glass.GLASS_MATERIAL_MAX_EDGE)

    def test_pause_glyph_itself_contains_scene_pigment_with_no_background_plate(self):
        owner = _TransitionOwner()
        values = {'v2_music_glass_level': 50, 'v2_theme_mode': 'dark'}
        owner.settings_getter = lambda key, default=None: values.get(key, default)
        window = MusicSurfaceWindow(owner)
        window.resize(240, 120)
        window._background_cache = QImage(240, 120, QImage.Format.Format_RGB32)
        window._background_cache.fill(QColor('#168ba8'))
        window._reset_control_idle(now=100.)
        try:
            target = QImage(240, 120, QImage.Format.Format_ARGB32_Premultiplied)
            target.fill(Qt.GlobalColor.transparent)
            with QPainter(target) as painter, \
                    patch('local.music_surface_window.time.monotonic', return_value=100.), \
                    patch.object(window, '_display_playing_state', return_value=True):
                window._paint_transport_icon(painter, QRectF(80, 20, 80, 80), 'toggle_play_pause', QColor('white'))
            left, right = target.pixelColor(99, 60), target.pixelColor(141, 60)
            for color in (left, right):
                # Contrast opal remains inside the silhouette and still
                # transmits the teal scene rather than becoming solid ink.
                self.assertGreater(color.blue(), color.red() + 15)
                self.assertGreater(color.green(), color.red() + 12)
                self.assertGreater(color.alpha(), 160)
            self.assertEqual(target.pixelColor(120, 60).alpha(), 0)
            self.assertEqual(target.pixelColor(81, 60).alpha(), 0)
            window._control_layer_cache[('fixture',)] = target
            window._control_layer_cache_bytes = target.sizeInBytes()
            window._control_prewarm_remaining = 0
            window._control_prewarm_requested = True
            before_motion = window._control_last_motion_at
            values['v2_music_glass_level'] = 90
            window.refresh_glass_material()
            self.assertFalse(window._control_layer_cache)
            self.assertEqual(window._control_layer_cache_bytes, 0)
            self.assertGreater(window._control_prewarm_remaining, 0)
            self.assertFalse(window._control_prewarm_requested)
            self.assertEqual(window._control_last_motion_at, before_motion)
            self.assertEqual(window._surface_glass_level(), 90)
            # SettingsManager's existing app callback refreshes the same live
            # window/fullscreen object, rather than requiring navigation.
            from local.app import LocalIslandApp
            app = SimpleNamespace(window=SimpleNamespace(music_fullscreen_overlay=window))
            with patch.object(window, 'refresh_glass_material', wraps=window.refresh_glass_material) as refresh:
                LocalIslandApp._playback_mode_changed(app, 'v2_music_glass_level', 90)
            refresh.assert_called_once_with()
        finally:
            window.shutdown()
            owner.close()

    def test_one_shared_ink_accounts_for_stronger_theme_veil(self):
        owner = _TransitionOwner()
        values = {'v2_music_glass_level': 100, 'v2_theme_mode': 'dark'}
        owner.settings_getter = lambda key, default=None: values.get(key, default)
        window = MusicSurfaceWindow(owner)
        window._background_cache = QImage(240, 120, QImage.Format.Format_RGB32)
        try:
            for theme in ('dark', 'light'):
                values['v2_theme_mode'] = theme
                for level in (0, 50, 100):
                    values['v2_music_glass_level'] = level
                    for background in ('#168ba8', '#767676', '#202126', '#e6e6e6'):
                        window._background_cache.fill(QColor(background))
                        window._control_color_transitions.clear()
                        ink = window._scene_control_ink()
                        veil = glass.glass_material_tint(light=theme == 'light', material_level=level)
                        mapped = QColor(*(round(a * (1. - veil.alphaF()) + b * veil.alphaF())
                                          for a, b in zip(QColor(background).getRgb()[:3], veil.getRgb()[:3])))
                        self.assertGreaterEqual(window._contrast_ratio(ink, mapped), 4.5)
                        self.assertEqual(window._surface_glass_ink(QRectF(0, 0, 40, 40)),
                                         window._surface_glass_ink(QRectF(180, 80, 40, 40), active=True))
        finally:
            window.shutdown()
            owner.close()

    def test_transport_hover_brightens_glyph_pixels_without_adding_a_plate(self):
        owner = _TransitionOwner()
        owner.settings_getter = lambda key, default=None: 'dark' if key == 'v2_theme_mode' else default
        window = MusicSurfaceWindow(owner)
        window.resize(240, 120)
        window._background_cache = QImage(240, 120, QImage.Format.Format_RGB32)
        window._background_cache.fill(QColor('#202020'))
        window._reset_control_idle(now=100.)
        try:
            images = []
            for hovered, pressed in ((False, False), (True, False), (True, True)):
                image = QImage(240, 120, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter, \
                        patch('local.music_surface_window.time.monotonic', return_value=100.), \
                        patch.object(window, '_display_playing_state', return_value=True):
                    window._paint_transport_icon(painter, QRectF(80, 20, 80, 80),
                        'toggle_play_pause', QColor('white'), hovered=hovered, pressed=pressed)
                images.append(image)
                self.assertEqual(image.pixelColor(120, 60).alpha(), 0)
                self.assertEqual(image.pixelColor(81, 60).alpha(), 0)
            center = [image.pixelColor(99, 60).lightness() for image in images]
            # Hover still brightens transmitted opal inside the silhouette.
            self.assertGreater(center[1], center[0] + 8)
            self.assertLess(center[2], center[1])
        finally:
            window.shutdown()
            owner.close()

    def test_transport_materials_keep_scene_chroma_and_common_ink_across_levels(self):
        owner = _TransitionOwner()
        values = {'v2_theme_mode': 'light', 'v2_music_glass_level': 50}
        owner.settings_getter = lambda key, default=None: values.get(key, default)
        window = MusicSurfaceWindow(owner)
        window.resize(240, 120)
        window._reset_control_idle(now=100.)
        try:
            for theme in ('dark', 'light'):
                values['v2_theme_mode'] = theme
                for background in ('#168ba8', '#e6e6e6', '#202020'):
                    window._background_cache = QImage(240, 120, QImage.Format.Format_RGB32)
                    window._background_cache.fill(QColor(background))
                    for level in (0, 50, 100):
                        values['v2_music_glass_level'] = level
                        window._control_color_transitions.clear()
                        for hovered, pressed in ((False, False), (True, False), (True, True)):
                            for command, playing, sample_x in (
                                    ('previous', False, 120), ('toggle_play_pause', False, 120),
                                    ('toggle_play_pause', True, 99), ('next', False, 120)):
                                with self.subTest(theme=theme, background=background, level=level,
                                                  hovered=hovered, pressed=pressed,
                                                  command=command, playing=playing):
                                    outputs = []
                                    # All silhouettes use the shared scene decision,
                                    # including when callers pass opposite legacy ink.
                                    for ink in (QColor('black'), QColor('white')):
                                        image = QImage(240, 120, QImage.Format.Format_ARGB32_Premultiplied)
                                        image.fill(Qt.GlobalColor.transparent)
                                        with QPainter(image) as painter, \
                                                patch('local.music_surface_window.time.monotonic', return_value=100.), \
                                                patch.object(window, '_display_playing_state', return_value=playing):
                                            window._paint_transport_icon(painter, QRectF(80, 20, 80, 80),
                                                command, ink, hovered=hovered, pressed=pressed)
                                        outputs.append(image)
                                        actual = image.pixelColor(sample_x, 60)
                                        self.assertEqual(image.pixelColor(81, 21).alpha(), 0)
                                        if level > 0:
                                            self.assertGreater(actual.alpha(), 160)
                                            if background == '#168ba8':
                                                self.assertGreater(actual.blue(), actual.red() + 8)
                                                self.assertGreater(actual.green(), actual.red() + 6)
                                        if command == 'toggle_play_pause' and playing:
                                            self.assertEqual(image.pixelColor(120, 60).alpha(), 0)
                                    self.assertEqual(outputs[0], outputs[1])
        finally:
            window.shutdown()
            owner.close()

    def test_transport_opacity_follows_level_while_scene_still_changes_glyph_pixels(self):
        owner = _TransitionOwner()
        values = {'v2_theme_mode': 'dark', 'v2_music_glass_level': 50}
        owner.settings_getter = lambda key, default=None: values.get(key, default)
        window = MusicSurfaceWindow(owner)
        window.resize(240, 120)
        window._reset_control_idle(now=100.)
        try:
            scene_colors = {}
            for scene_index, background in enumerate(('#168ba8', '#a88b16')):
                window._background_cache = QImage(240, 120, QImage.Format.Format_RGB32)
                window._background_cache.fill(QColor(background))
                colors = []
                for level in (0, 25, 50, 75, 100):
                    values['v2_music_glass_level'] = level
                    window._control_color_transitions.clear()
                    image = QImage(240, 120, QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(Qt.GlobalColor.transparent)
                    with QPainter(image) as painter, \
                            patch('local.music_surface_window.time.monotonic', return_value=100. + scene_index), \
                            patch.object(window, '_display_playing_state', return_value=True):
                        window._paint_transport_icon(painter, QRectF(80, 20, 80, 80),
                                                     'toggle_play_pause', QColor('white'))
                    colors.append(image.pixelColor(99, 60))
                    self.assertEqual(image.pixelColor(120, 60).alpha(), 0)
                    self.assertEqual(image.pixelColor(81, 60).alpha(), 0)
                alpha = [color.alpha() for color in colors]
                self.assertEqual(alpha, sorted(alpha))
                self.assertGreater(alpha[-1], alpha[0] + 160)
                scene_colors[background] = colors
            for index in range(1, 5):
                teal = scene_colors['#168ba8'][index]
                warm = scene_colors['#a88b16'][index]
                self.assertGreater(teal.blue() - teal.red(), 8)
                self.assertGreater(warm.red() - warm.blue(), 8)
                self.assertGreater(abs(teal.blue() - warm.blue()), 8)
        finally:
            window.shutdown()
            owner.close()

    def test_transport_opal_uses_shared_scene_decision_even_with_opposite_caller_ink(self):
        owner = _TransitionOwner()
        owner.settings_getter = lambda key, default=None: 'dark' if key == 'v2_theme_mode' else default
        window = MusicSurfaceWindow(owner)
        window.resize(240, 120)
        window._reset_control_idle(now=100.)
        window._background_cache = QImage(240, 120, QImage.Format.Format_RGB32)
        window._background_cache.fill(QColor('#168ba8'))
        try:
            centers = []
            for shared in (QColor('black'), QColor('white')):
                outputs = []
                for caller in (QColor('black'), QColor('white')):
                    image = QImage(240, 120, QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(Qt.GlobalColor.transparent)
                    with QPainter(image) as painter, \
                            patch('local.music_surface_window.time.monotonic', return_value=100.), \
                            patch.object(window, '_display_playing_state', return_value=True), \
                            patch.object(window, '_scene_control_ink', return_value=shared) as ink:
                        window._paint_transport_icon(painter, QRectF(80, 20, 80, 80),
                                                     'toggle_play_pause', caller)
                    ink.assert_called_once_with()
                    outputs.append(image)
                self.assertEqual(outputs[0], outputs[1])
                centers.append(outputs[0].pixelColor(99, 60).lightness())
            self.assertGreater(centers[1], centers[0] + 30)
        finally:
            window.shutdown()
            owner.close()


if __name__ == '__main__':
    unittest.main()
