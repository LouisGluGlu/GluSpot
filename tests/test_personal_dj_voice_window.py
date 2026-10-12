from __future__ import annotations

import unittest
from unittest.mock import Mock

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from api.online_music import PersonalDjVoice
from local.personal_dj_voice_window import PersonalDjVoiceWindow
from local.settings_window import SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


class _Tasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.actions = []
        self.closed = False

    def submit(self, token, action):
        self.actions.append((token, action))
        return True

    def close(self):
        self.closed = True


class PersonalDjVoiceWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tasks = _Tasks()
        self.provider = Mock()
        self.provider.get_owned_personal_dj_voice_key.return_value = ""
        self.provider.list_personal_dj_voices.return_value = (
            PersonalDjVoice("F793-C2", "豆花", "温柔的陪伴", True),
            PersonalDjVoice("M791-C2", "黄俊", "音乐故事", False),
            PersonalDjVoice("M792-NP", "李源", "轻松的讲解", False),
            PersonalDjVoice("M795-NP", "庞舸", "深入乐评", False),
        )
        self.window = PersonalDjVoiceWindow(self.provider, tasks=self.tasks)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def complete(self):
        token, fetch = self.tasks.actions[-1]
        self.tasks.completed.emit(token, fetch(), None)

    def test_catalog_is_fetched_in_worker_and_actual_names_default_are_shown(self):
        self.window.reload()
        self.provider.list_personal_dj_voices.assert_not_called()
        self.assertFalse(self.window.refresh.isEnabled())
        self.complete()
        self.assertEqual([b.accessibleName() for b in self.window._buttons], ["豆花", "黄俊", "李源", "庞舸"])
        self.assertEqual([b.isChecked() for b in self.window._buttons], [True, False, False, False])

    def test_selection_uses_real_key_and_reports_saved_state(self):
        self.window.reload()
        self.complete()
        self.provider.get_owned_personal_dj_voice_key.return_value = "M792-NP"
        self.provider.set_owned_personal_dj_voice.return_value = True
        self.window._buttons[2].click()
        self.provider.set_owned_personal_dj_voice.assert_called_once_with("M792-NP")
        self.provider.preview_owned_personal_dj_voice.assert_called_once_with("M792-NP")
        self.assertEqual([b.isChecked() for b in self.window._buttons], [False, False, True, False])
        self.assertIn("李源", self.window.status.text())

    def test_unknown_key_never_reaches_provider(self):
        self.window.reload()
        self.complete()
        self.window.choose("invented")
        self.provider.set_owned_personal_dj_voice.assert_not_called()

    def test_failed_selection_restores_actual_previous_selection(self):
        self.provider.get_owned_personal_dj_voice_key.return_value = "M791-C2"
        self.provider.set_owned_personal_dj_voice.return_value = False
        self.window.reload()
        self.complete()
        self.window._buttons[2].click()
        self.assertEqual([b.isChecked() for b in self.window._buttons], [False, True, False, False])
        self.assertIn("无法切换", self.window.status.text())
        self.provider.preview_owned_personal_dj_voice.assert_not_called()

    def test_audition_fault_does_not_revert_successful_voice_preference(self):
        self.window.reload()
        self.complete()
        self.provider.get_owned_personal_dj_voice_key.return_value = "M792-NP"
        self.provider.set_owned_personal_dj_voice.return_value = True
        self.provider.preview_owned_personal_dj_voice.side_effect = RuntimeError("offline")
        self.window.choose("M792-NP")
        self.assertEqual([b.isChecked() for b in self.window._buttons], [False, False, True, False])
        self.assertIn("已选择李源", self.window.status.text())

    def test_failed_selection_restores_service_default_when_key_is_empty(self):
        self.provider.set_owned_personal_dj_voice.return_value = False
        self.window.reload()
        self.complete()
        self.window._buttons[2].click()
        self.assertEqual([b.isChecked() for b in self.window._buttons], [True, False, False, False])

    def test_reloaded_closed_and_error_results_cannot_repopulate_old_choices(self):
        self.window.reload()
        old, fetch = self.tasks.actions[-1]
        self.window.reload()
        self.tasks.completed.emit(old, fetch(), None)
        self.assertFalse(self.window._buttons)
        token, _ = self.tasks.actions[-1]
        self.tasks.completed.emit(token, None, "auth_required")
        self.assertIn("登录网易云", self.window.status.text())
        self.window.reload()
        token, fetch = self.tasks.actions[-1]
        self.window.reject()
        self.tasks.completed.emit(token, fetch(), None)
        self.assertFalse(self.window._buttons)

    def test_settings_entry_calls_actual_selector_callback(self):
        island, settings = FakeIslandWidget(), FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            callback = Mock()
            window.personal_dj_voice_callback = callback
            window.personal_dj_voice_button.click()
            callback.assert_called_once_with()
        finally:
            window.close()
            island.close()


if __name__ == "__main__":
    unittest.main()
