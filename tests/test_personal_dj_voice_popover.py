from __future__ import annotations

import unittest
from unittest.mock import Mock

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from api.online_music import PersonalDjVoice
from local.personal_dj_voice_popover import PersonalDjVoicePopover
from tests.test_personal_dj_voice_window import _Tasks


class _Surface(QWidget):
    def _personal_dj_geometry(self, rect, now):
        return QRectF(360, 160, 88, 88), QRectF(300, 270, 240, 44)

    def _personal_dj_control_shift(self, rect, reveal):
        return QPointF(0, 0)

    def _personal_dj_reveal_at(self, now):
        return 1.

    def _future_lyric_color(self, rect):
        return QColor(90, 160, 200)

    def _set_dj_voice_selector_hover(self, value):
        self.hovered = value

    def _close_dj_voice_selector(self):
        self.hovered = False


class VoicePopoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tasks = _Tasks()
        self.provider = Mock()
        self.provider.get_owned_personal_dj_voice_key.return_value = ""
        self.catalog = tuple(PersonalDjVoice(key, name, "真实目录描述", i == 0) for i, (key, name) in enumerate(
            (("F793-C2", "豆花"), ("M791-C2", "黄俊"), ("M792-NP", "李源"), ("M795-NP", "庞舸"))))
        self.provider.list_personal_dj_voices.return_value = self.catalog
        self.surface = _Surface()
        self.surface.resize(600, 360)
        self.surface.show()
        self.popup = PersonalDjVoicePopover(self.provider, self.surface, tasks=self.tasks)

    def tearDown(self):
        self.popup.hide()
        self.surface.close()
        self.surface.deleteLater()
        self.app.processEvents()

    def open(self):
        self.popup.present(True)
        token, fetch = self.tasks.actions[-1]
        self.tasks.completed.emit(token, fetch(), None)

    def test_hover_slider_uses_same_caption_geometry_and_worker_catalog(self):
        self.popup.present(True)
        self.assertEqual(self.popup.geometry(), QRectF(300, 270, 240, 44).toRect())
        self.provider.list_personal_dj_voices.assert_not_called()
        token, fetch = self.tasks.actions[-1]
        self.tasks.completed.emit(token, fetch(), None)
        self.assertEqual([v.name for v in self.popup._voices], ["豆花", "黄俊", "李源", "庞舸"])
        self.assertEqual(self.popup._selected, 0)

    def test_selection_does_not_toggle_dj_and_uses_actual_provider_key(self):
        self.open()
        self.provider.get_owned_personal_dj_voice_key.return_value = "M795-NP"
        self.provider.set_owned_personal_dj_voice.return_value = True
        self.assertTrue(self.popup.choose(3))
        self.provider.set_owned_personal_dj_voice.assert_called_once_with("M795-NP")
        self.provider.preview_owned_personal_dj_voice.assert_called_once_with("M795-NP")
        self.provider.set_owned_personal_dj_enabled.assert_not_called()
        self.assertEqual(self.popup._selected, 3)

    def test_failed_selection_restores_actual_default_and_unknown_index_is_rejected(self):
        self.open()
        self.provider.set_owned_personal_dj_voice.return_value = False
        self.assertFalse(self.popup.choose(2))
        self.assertEqual(self.popup._selected, 0)
        self.provider.preview_owned_personal_dj_voice.assert_not_called()
        self.provider.set_owned_personal_dj_voice.reset_mock()
        self.assertFalse(self.popup.choose(4))
        self.provider.set_owned_personal_dj_voice.assert_not_called()

    def test_closed_old_account_request_and_reload_are_fenced(self):
        self.popup.present(True)
        token, fetch = self.tasks.actions[-1]
        self.popup.present(False)
        self.tasks.completed.emit(token, fetch(), None)
        self.assertFalse(self.popup._voices)
        self.popup.present(True)
        self.tasks.completed.emit(token, fetch(), None)
        self.assertFalse(self.popup._voices)
        token, _ = self.tasks.actions[-1]
        self.tasks.completed.emit(token, None, "auth_required")
        self.assertIn("登录", self.popup._status)

    def test_drag_commits_only_on_release_and_keyboard_remains_available(self):
        self.open()
        self.provider.set_owned_personal_dj_voice.return_value = True
        self.provider.get_owned_personal_dj_voice_key.return_value = "M792-NP"
        event = lambda typ, p, button: QMouseEvent(typ, QPointF(*p), QPointF(*p), button,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.popup.mousePressEvent(event(QMouseEvent.Type.MouseButtonPress, (25, 22), Qt.MouseButton.LeftButton))
        self.popup.mouseMoveEvent(event(QMouseEvent.Type.MouseMove, (145, 22), Qt.MouseButton.NoButton))
        self.provider.set_owned_personal_dj_voice.assert_not_called()
        self.popup.mouseReleaseEvent(event(QMouseEvent.Type.MouseButtonRelease, (145, 22), Qt.MouseButton.LeftButton))
        self.provider.set_owned_personal_dj_voice.assert_called_once_with("M792-NP")
        self.provider.set_owned_personal_dj_voice.reset_mock()
        self.popup.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
        self.provider.set_owned_personal_dj_voice.assert_called_once_with("M795-NP")
        self.popup.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(self.surface.hovered)

    def test_native_slider_preserves_clear_names_with_no_opaque_extra_plate(self):
        self.open()
        image = self.popup.grab().toImage()
        self.assertGreater(image.width(), 0)
        self.assertGreater(image.height(), 0)
        # Four distinct text groups remain above a single rounded slider face.
        dpr = image.devicePixelRatio()
        for i in range(4):
            whites = sum(image.pixelColor(x, y).lightness() > 155
                         for y in range(round(13 * dpr), round(32 * dpr))
                         for x in range(round((9 + i * 58) * dpr), round((54 + i * 58) * dpr)))
            self.assertGreater(whites, 10)


if __name__ == "__main__":
    unittest.main()
