from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from api.audio_routes import AudioRouteDevice
from local.audio_output_panel import _OutputRow
from local.ios_widgets import IosSwitch
from local.liquid_glass import GlassPushButton, clear_glass_cache, widget_glass_backdrop
from local.mac_style import MacTrafficButton
from local.music_surface_palette import relative_luminance
from local.music_library_window import _CollectionCover, _LibraryActionButton, _ModeButton
from local.playback_entry_window import ENTRY_SOURCES, EntryNavigationButton, MusicSourceButton, PlaybackModeButton
from local.settings_window import ColorButton, SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


class LiquidGlassWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        clear_glass_cache()
        self.owner = QWidget()
        self.owner.resize(400, 240)
        self.owner._background_cache = QImage(400, 240, QImage.Format.Format_RGB32)
        self.owner._background_cache.fill(QColor("#b55365"))

    def tearDown(self):
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()
        clear_glass_cache()

    def render(self, widget):
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        return image

    def test_standard_button_keeps_native_keyboard_check_disable_and_text_contract(self):
        button = GlassPushButton("继续", self.owner)
        button.resize(120, 44)
        button.setCheckable(True)
        button.setStyleSheet("QPushButton { color: white; background: transparent; }")
        button.setGlassLight(False)
        button.setGlassTint(QColor("#7351b3"))
        button.show()
        self.owner.show()
        spy = QSignalSpy(button.clicked)
        self.assertIsInstance(button, QPushButton)
        QTest.keyClick(button, Qt.Key.Key_Space)
        self.assertEqual(spy.count(), 1)
        self.assertTrue(button.isChecked())
        button.setEnabled(False)
        button.click()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(button.text(), "继续")
        self.assertFalse(self.render(button).isNull())

    def test_partial_control_samples_only_its_position_in_the_ancestor_raster(self):
        self.owner._background_cache = QImage(200, 120, QImage.Format.Format_RGB32)
        child = QWidget(self.owner)
        child.setGeometry(40, 30, 180, 21)
        backdrop, source = widget_glass_backdrop(child, QRectF(144, 1.5, 36, 18))
        self.assertIs(backdrop, self.owner._background_cache)
        self.assertEqual(source, QRectF(92, 15.75, 18, 9))
        _, full = widget_glass_backdrop(child)
        self.assertEqual(full, QRectF(20, 15, 90, 10.5))

    def test_standard_label_honors_stylesheet_padding_and_foreground_pixels(self):
        button = GlassPushButton("字体", self.owner)
        button.resize(120, 44)
        button.setGlassLight(False)
        button.setProperty("glassActive", False)
        button.setStyleSheet("QPushButton { border: none; padding-left: 23px; padding-right: 7px; text-align: left; color: #ef3fe7; }")
        image = self.render(button)
        text_x = [x for y in range(image.height()) for x in range(image.width())
                  if (pixel := image.pixelColor(x, y)).red() > 170 and pixel.blue() > 160 and pixel.green() < 100]
        self.assertTrue(text_x)
        self.assertGreaterEqual(min(text_x), 23)
        self.assertLess(min(text_x), 35)

    def test_custom_faces_react_to_the_real_cached_background_without_changing_actions(self):
        controls = (
            EntryNavigationButton("back", "返回", self.owner),
            PlaybackModeButton("程序内播放", self.owner),
            MusicSourceButton(ENTRY_SOURCES[0], self.owner),
            _LibraryActionButton("switch", "切换平台", self.owner),
            _ModeButton("window", self.owner),
            _CollectionCover("netease", "fm", self.owner),
        )
        for control in controls:
            with self.subTest(control=type(control).__name__):
                control.resize(132, 76)
                control.move(25, 30)
                self.owner._background_cache.fill(QColor("#b55365"))
                clear_glass_cache()
                warm = self.render(control)
                self.owner._background_cache.fill(QColor("#347b9d"))
                clear_glass_cache()
                cool = self.render(control)
                self.assertNotEqual(bytes(warm.constBits()), bytes(cool.constBits()))
                spy = QSignalSpy(control.clicked)
                control.click()
                self.assertEqual(spy.count(), 1)

    def test_mode_selection_keeps_the_existing_reading_sweep_and_dark_unselected_ink_surface(self):
        button = PlaybackModeButton("系统抓取", self.owner)
        button.resize(220, 80)
        unselected = self.render(button)
        button.setChecked(True)
        button.settle()
        selected = self.render(button)
        self.assertEqual(button._progress, 1)
        self.assertGreater(selected.pixelColor(20, 40).lightness(), unselected.pixelColor(20, 40).lightness())
        self.assertLessEqual(button.SWEEP_SECONDS, 1)
        button.setChecked(False)
        self.assertEqual(button._progress, 0)

    def test_actual_artwork_is_preserved_and_only_placeholder_gets_glass(self):
        cover = _CollectionCover("netease", "fm", self.owner)
        cover.resize(120, 120)
        cover.image = QImage(120, 120, QImage.Format.Format_RGB32)
        cover.image.fill(QColor("#e5a827"))
        original = cover.image.copy()
        with patch("local.music_library_window.paint_liquid_glass") as material:
            actual = self.render(cover)
        material.assert_not_called()
        self.assertEqual(actual.pixelColor(60, 60), QColor("#e5a827"))
        self.assertEqual(cover.image, original)
        cover.image = QImage()
        with patch("local.music_library_window.paint_liquid_glass") as material:
            self.render(cover)
        material.assert_called_once()
        self.assertIs(material.call_args.kwargs["backdrop"], self.owner._background_cache)

    def test_settings_theme_and_dynamic_search_buttons_keep_foreground_contrast(self):
        island, settings = FakeIslandWidget(), FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            for dark in (False, True):
                with self.subTest(dark=dark), patch.object(window, "_settings_dark_mode", return_value=dark):
                    window._apply_theme()
                    window._show_settings_search_results("缓存")
                    buttons = window.findChildren(GlassPushButton)
                    self.assertGreater(len(buttons), 4)
                    self.assertTrue(any(button.objectName() == "SearchResultAction" for button in buttons))
                    for button in buttons:
                        if isinstance(button, ColorButton):
                            continue
                        expected = not dark and button.objectName() not in {"Primary", "Danger"}
                        self.assertEqual(button.property("glassLight"), expected, button.objectName())
                        if button.objectName() == "DetailToggle":
                            self.assertFalse(button.property("glassActive"))
            swatch = ColorButton("#a2294f", lambda: None, self.owner)
            self.assertEqual(swatch.property("glassTint"), QColor("#a2294f"))
            swatch.set_color("#3f94c6")
            self.assertEqual(swatch.property("glassTint"), QColor("#3f94c6"))
            self.assertEqual(self.render(swatch).pixelColor(17, 17), QColor("#3f94c6"))
        finally:
            window.close()
            island.close()

    def test_switch_retains_track_geometry_toggle_and_disabled_feedback(self):
        switch = IosSwitch("监听", parent=self.owner)
        switch.resize(180, 21)
        spy = QSignalSpy(switch.toggled)
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(170, 10))
        self.assertTrue(switch.isChecked())
        self.assertEqual(spy.count(), 1)
        enabled = self.render(switch)
        switch.setEnabled(False)
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(170, 10))
        self.assertEqual(spy.count(), 1)
        self.assertTrue(switch.isChecked())
        self.assertEqual(switch.TRACK_WIDTH, 36)
        self.assertEqual(switch.TRACK_HEIGHT, 18)
        self.assertNotEqual(enabled, self.render(switch))

    def test_enabled_switch_state_captions_have_readable_pixels_in_both_themes(self):
        switch = IosSwitch("监听", parent=self.owner)
        switch.resize(180, 21)
        for dark in (False, True):
            for checked in (False, True):
                with self.subTest(dark=dark, checked=checked), patch("local.ios_widgets.app_dark_mode", return_value=dark):
                    switch.setChecked(checked)
                    image = self.render(switch)
                    dpr = image.devicePixelRatio()
                    left = 149 if checked else 164
                    background = image.pixelColor(round((left + 2) * dpr), round(4 * dpr))
                    backdrop_luminance = relative_luminance(background)
                    ratios = []
                    for y in range(round(6 * dpr), round(15 * dpr)):
                        for x in range(round(left * dpr), round((left + 10) * dpr)):
                            glyph = image.pixelColor(x, y)
                            luminance = relative_luminance(glyph)
                            ratios.append((max(luminance, backdrop_luminance) + .05) /
                                          (min(luminance, backdrop_luminance) + .05))
                    self.assertGreaterEqual(max(ratios), 4.5)

    def test_device_glass_retains_actual_volume_boundary_and_mute_restore_actions(self):
        device = AudioRouteDevice("local", "本地音频", volume=.25)
        row = _OutputRow(device, self.owner)
        row.resize(330, 56)
        row.state, row.progress = "active", 1
        row.set_device(device)
        at_quarter = self.render(row)
        self.assertAlmostEqual(row.volume_fill_rect().width() / 328, .25)
        row.set_device(replace(device, volume=.75))
        at_three_quarters = self.render(row)
        self.assertAlmostEqual(row.volume_fill_rect().width() / 328, .75)
        self.assertGreater(at_three_quarters.pixelColor(164, 5).lightness(), at_quarter.pixelColor(164, 5).lightness())
        row.set_device(replace(device, volume=.75, volume_muted=True))
        self.assertEqual(row.volume_fill_rect().width(), 0)
        self.assertEqual(row.displayed_volume, 0)
        muted = QSignalSpy(row.mute_requested)
        solo = QSignalSpy(row.solo_requested)
        row.click()
        row.solo_button.click()
        self.assertEqual(muted.at(0), ["local"])
        self.assertEqual(solo.at(0), ["local"])

    def test_traffic_buttons_keep_three_distinct_status_colors_and_hover_symbols(self):
        colors = []
        for name, symbol in (("MacTrafficRed", "×"), ("MacTrafficYellow", "−"), ("MacTrafficGreen", "+")):
            button = MacTrafficButton(name, name, symbol, self.owner)
            ordinary = self.render(button)
            button._hovered = True
            hovered = self.render(button)
            self.assertNotEqual(ordinary, hovered)
            colors.append(ordinary.pixelColor(5, 5).rgb())
        self.assertEqual(len(set(colors)), 3)
