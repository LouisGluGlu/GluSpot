from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from local import settings_store
from local.playback_entry_controller import system_session_hint
from local.playback_entry_window import PlaybackEntryWindow
from tests import test_playback_entry_controller as entry_fixture


class FirstRunIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_directory_does_not_copy_external_completed_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old-appdata" / "DynamicIsland"
            old.mkdir(parents=True)
            (old / "settings.json").write_text(json.dumps({"v2_playback_onboarding_completed": True}), encoding="utf-8")
            fresh = root / "fresh-program" / "GluSpot"
            with patch("local.branding.sys.frozen", True, create=True), \
                    patch.object(settings_store, "APP_DATA_DIR", str(fresh)), \
                    patch.object(settings_store, "SETTINGS_FILE", str(fresh / "settings.json")), \
                    patch.dict("os.environ", {"APPDATA": str(root / "old-appdata")}):
                manager = settings_store.SettingsManager()
                self.assertFalse(manager.get("v2_playback_onboarding_completed"))
                self.assertTrue(manager.get("v2_owned_playback_enabled"))
                self.assertTrue(manager.get("v2_system_capture_enabled"))
                manager.set("v2_playback_onboarding_completed", True)
                manager.save_now()
                restored = settings_store.SettingsManager()
                self.assertTrue(restored.get("v2_playback_onboarding_completed"))
                restored.save_now()

    def test_smtc_hint_distinguishes_published_session_from_unknown_player_setting(self):
        snapshot = SimpleNamespace(track=SimpleNamespace(source_app_id="fixture-player"))
        self.assertIn("已检测到", system_session_hint("ready", snapshot))
        missing = system_session_hint("no_session")
        self.assertIn("若音乐正在播放", missing)
        self.assertIn("无法读取其内部开关", missing)
        self.assertNotIn("未开启", missing)
        self.assertIn("不可用", system_session_hint("missing_winsdk"))


class EntryCloseTests(unittest.TestCase):
    """Reuse the controller fixture without live media, accounts or tray windows."""

    setUpClass = classmethod(entry_fixture.PlaybackEntryControllerTests.setUpClass.__func__)
    make_controller = entry_fixture.PlaybackEntryControllerTests.make_controller

    def test_closing_choices_keeps_island_visible(self):
        self.make_controller().start()
        self.window.close()
        self.assertEqual(self.island.shown, 1)

    def test_closing_choices_respects_explicit_tray_hide_and_application_quit(self):
        for attribute in ("_tray_hidden", "_application_quitting"):
            with self.subTest(attribute=attribute):
                self.make_controller().start()
                setattr(self.island, attribute, True)
                self.window.close()
                self.assertEqual(self.island.shown, 0)

    def test_tray_hide_cancels_pending_accounts_and_cannot_reopen_login(self):
        for attribute in ("_tray_hidden", "_application_quitting"):
            with self.subTest(attribute=attribute):
                controller = self.make_controller(completed=True, system=True)
                controller.start()
                pending = self.tasks.latest("accounts")
                setattr(self.island, attribute, True)
                self.window.close()
                self.tasks.run(pending)
                self.assertIsNone(controller._route_pending)
                self.assertFalse(controller._probe_timer.isActive())
                self.assertFalse(self.window.visible)
                self.assertEqual(self.island.shown, 0)
                controller.show_login()
                self.assertFalse(self.window.visible)

    def test_hidden_async_login_confirmation_cannot_enter_library(self):
        calls = []
        controller = self.make_controller()
        controller.login_callback = calls.append
        controller.show_login()
        self.island._tray_hidden = True
        controller.enter_account("netease")
        self.assertEqual(calls, [])


class EntryChoiceCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = PlaybackEntryWindow()
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_short_and_narrow_choices_keep_action_and_status_separate(self):
        for width, height in ((840, 650), (560, 460), (560, 360), (360, 360)):
            with self.subTest(size=(width, height)):
                screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, width, height))
                with patch.object(QGuiApplication, "screenAt", return_value=screen):
                    self.window.show_modes()
                self.window.show()
                self.app.processEvents()
                self.window.set_system_session_hint(system_session_hint("no_session"))
                owned, system, hint, action = (
                    self.window.owned_button, self.window.system_button,
                    self.window.mode_hint, self.window.continue_button,
                )
                for control in (owned, system, hint, action, self.window.close_button):
                    self.assertTrue(self.window.rect().contains(control.geometry()), control.geometry())
                for button in (owned, system, action):
                    self.assertGreaterEqual(button.height(), 44)
                    self.assertGreaterEqual(button.width(), 44)
                self.assertLess(owned.geometry().right(), system.geometry().left())
                self.assertLess(owned.geometry().bottom(), action.geometry().top())
                self.assertLess(action.geometry().right(), hint.geometry().left())
                self.assertFalse(hint.geometry().intersects(action.geometry()))
                self.assertLessEqual(hint.heightForWidth(hint.width()), hint.height())
                self.assertFalse(self.window.grab().isNull())

    def test_visible_descriptions_and_selection_feedback_keep_existing_contract(self):
        self.assertEqual(self.window.selection, (True, True))
        self.assertIn("音乐库", self.window.owned_button.accessibleDescription())
        self.assertIn("播放器", self.window.system_button.accessibleDescription())
        self.window.owned_button.click()
        self.window.system_button.click()
        self.assertEqual(self.window.selection, (False, True))
        self.assertIn("至少保留", self.window.mode_hint.text())
        self.assertIsNotNone(self.window.system_button._rejected_at)
        # Actual SMTC evidence remains visible, without duplicating the
        # checked states or claiming to read another player's setting.
        self.window._mode_warning_until = 0
        self.window.set_system_session_hint("若音乐正在播放，请检查播放器的系统媒体控制设置。")
        self.assertEqual(self.window.mode_hint.text(), "可在设置中更改")
        self.assertIn("若音乐正在播放", self.window.mode_hint.accessibleDescription())


if __name__ == "__main__":
    unittest.main()
