from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot
from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow


def _source(source_id, available=True):
    return SimpleNamespace(source_id=source_id, available=available,
                           reason="暂无经过验证的登录接口" if not available else "")


def _image_bytes() -> bytes:
    image = QImage(40, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("black"))
    output = QByteArray()
    buffer = QBuffer(output)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(output)


class PlaybackEntryWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = PlaybackEntryWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _login_page(self):
        self.window.show_login()
        self.window.set_sources(tuple(_source(item.source_id) for item in ENTRY_SOURCES))
        self.window.select_source("netease")
        self.window._expansion.stop()
        self.window._set_expanded(1.0)

    def test_default_and_two_modes_keep_at_least_one_selected(self):
        self.assertEqual(self.window.selection, (True, False))
        self.window.owned_button.click()
        self.assertEqual(self.window.selection, (True, False))
        self.window.system_button.click()
        self.assertEqual(self.window.selection, (True, True))
        self.window.owned_button.click()
        self.assertEqual(self.window.selection, (False, True))
        self.window.system_button.click()
        self.assertEqual(self.window.selection, (False, True))

    def test_continue_requires_explicit_click_and_only_emits_selection(self):
        spy = QSignalSpy(self.window.selectionAccepted)
        self.assertEqual(spy.count(), 0)
        self.window.continue_button.click()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), [True, False])
        self.assertEqual(self.window.page, "modes")

    def test_source_order_and_unsupported_source_are_explicit(self):
        self.window.set_sources(tuple(_source(item.source_id, item.source_id != "qishui") for item in ENTRY_SOURCES))
        self.assertEqual(tuple(self.window.source_buttons), ("netease", "kugou", "qq", "qishui"))
        self.assertFalse(self.window.source_buttons["qishui"].isEnabled())
        self.assertIn("暂无经过验证", self.window.source_buttons["qishui"].toolTip())
        spy = QSignalSpy(self.window.sourceSelected)
        self.window.select_source("qishui")
        self.assertEqual(spy.count(), 0)

    def test_source_choice_emits_id_and_displays_no_invented_qr(self):
        self.window.show_login()
        self.window.set_sources((_source("netease"),))
        spy = QSignalSpy(self.window.sourceSelected)
        self.window.select_source("netease")
        self.assertEqual(spy.at(0), ["netease"])
        self.assertEqual(self.window.login_state, "starting")
        self.assertTrue(self.window._qr_image.isNull())

    def test_backend_supplied_image_only_and_other_source_result_is_ignored(self):
        self._login_page()
        self.window.set_login_snapshot(SimpleNamespace(source_id="qq", state="waiting", qr_image_bytes=_image_bytes()))
        self.assertTrue(self.window._qr_image.isNull())
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes()))
        self.assertFalse(self.window._qr_image.isNull())
        self.assertEqual(self.window.login_state, "waiting")

    def test_url_without_service_image_does_not_generate_or_fetch_qr(self):
        self._login_page()
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_url="https://example.invalid/login", qr_image_bytes=b""))
        self.assertTrue(self.window._qr_image.isNull())
        self.assertIn("未提供", self.window.message_label.text())

    def test_error_expiry_and_unsupported_clear_previous_qr(self):
        self._login_page()
        for state in ("expired", "error", "unsupported", "confirmed", "cancelled"):
            self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes()))
            self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state=state, qr_image_bytes=_image_bytes()))
            self.assertTrue(self.window._qr_image.isNull(), state)
        self.assertFalse(self.window.refresh_button.isEnabled())

    def test_invalid_and_oversized_payload_cannot_populate_qr(self):
        self._login_page()
        for payload in (b"not an image", b"x" * (4 * 1024 * 1024 + 1)):
            self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=payload))
            self.assertTrue(self.window._qr_image.isNull())

    def test_refresh_invalidates_old_qr_and_emits_request(self):
        self._login_page()
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes()))
        spy = QSignalSpy(self.window.refreshRequested)
        self.window.refresh_button.click()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(self.window.login_state, "starting")
        self.assertTrue(self.window._qr_image.isNull())

    def test_back_releases_qr_and_late_login_result_is_ignored(self):
        self._login_page()
        spy = QSignalSpy(self.window.backRequested)
        self.window.show_choices()
        self.assertEqual(spy.count(), 1)
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes()))
        self.assertEqual(self.window.page, "modes")
        self.assertEqual(self.window.selected_source, "")
        self.assertTrue(self.window._qr_image.isNull())

    def test_timer_only_runs_for_visible_choices_and_stops_after_hide(self):
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window._frame_timer.isActive())
        self.window.show_login()
        self.assertFalse(self.window._frame_timer.isActive())
        self.window.show_choices()
        self.assertTrue(self.window._frame_timer.isActive())
        self.window.hide()
        self.assertFalse(self.window._frame_timer.isActive())
        self.assertTrue(self.window._background_frame.isNull())
        self.assertEqual(len(self.window.owned_button._reading.runs), 0)

    def test_selection_sweep_finishes_within_one_second(self):
        button = self.window.system_button
        button.setChecked(True)
        started = button._selected_at
        button.advance(started + .99)
        self.assertEqual(button._progress, 1.0)
        self.assertIsNone(button._selected_at)

    def test_close_emits_without_quitting_application(self):
        self.window.show()
        spy = QSignalSpy(self.window.closed)
        self.window.close()
        self.assertEqual(spy.count(), 1)
        self.assertIsNotNone(QApplication.instance())
        self.assertFalse(self.window._frame_timer.isActive())

    def test_hide_discards_late_login_callback(self):
        self.window.show()
        self._login_page()
        self.window.hide()
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes()))
        self.assertTrue(self.window._qr_image.isNull())
        self.assertEqual(self.window.login_state, "unavailable")

    def test_contraction_freezes_snapshot_reaches_target_and_calls_once(self):
        self.window.show()
        self.app.processEvents()
        target = QRect(450, 40, 260, 64)
        calls = []
        self.window.contract_to(target, lambda: calls.append("finished"))
        self.assertFalse(self.window._frame_timer.isActive())
        self.assertFalse(self.window._contraction_frame.isNull())
        self.window._contraction.setCurrentTime(225)
        self.assertGreater(self.window.width(), target.width())
        self.assertLess(self.window.width(), 840)
        self.window._contraction.setCurrentTime(450)
        self.assertEqual(self.window.geometry(), target)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(calls, ["finished"])
        self.assertTrue(self.window._contraction_frame.isNull())

    def test_close_during_contraction_releases_callback_and_snapshot(self):
        self.window.show()
        calls = []
        self.window.contract_to(QRect(0, 0, 220, 50), lambda: calls.append(True))
        self.window.close()
        self.assertEqual(calls, [])
        self.assertIsNone(self.window._contraction_finished)
        self.assertTrue(self.window._contraction_frame.isNull())

    def test_contraction_rejects_invalid_geometry(self):
        with self.assertRaises(ValueError):
            self.window.contract_to(QRect())

    def test_completed_onboarding_back_returns_only_to_source_selector(self):
        self._login_page()
        self.window.set_mode_back_enabled(False)
        spy = QSignalSpy(self.window.backRequested)
        self.window.back_button.click()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(self.window.page, "sources")
        self.assertEqual(self.window.selected_source, "")
        self.assertEqual(self.window._expanded, 0.0)
        self.assertTrue(self.window.back_button.isHidden())
        self.assertTrue(self.window.owned_button.isHidden())

    def test_reference_sizes_render_and_reuse_existing_flow(self):
        self.window.show()
        self.app.processEvents()
        image = self.window.transition_snapshot()
        self.assertFalse(image.isNull())
        self.assertLessEqual(self.window._background_frame.width(), 256)
        self.assertLessEqual(self.window._background_frame.height(), 256)
        for button in (self.window.owned_button, self.window.system_button, self.window.continue_button):
            self.assertTrue(self.window.rect().contains(button.geometry()))
        self._login_page()
        self.assertTrue(self.window.rect().contains(self.window.refresh_button.geometry()))
        self.assertTrue(self.window.rect().contains(self.window.message_label.geometry()))

    def test_verified_selected_account_replaces_qr_with_avatar_card_and_enter(self):
        self._login_page()
        account = AccountSnapshot("netease", True, "test", "测试昵称", state="verified")
        self.window.set_account(account)
        self.assertEqual(self.window.height(), 368)
        self.assertEqual(self.window.enter_button.text(), "进入")
        self.assertTrue(self.window.refresh_button.isHidden())
        self.assertTrue(self.window.message_label.isHidden())
        self.assertTrue(self.window._qr_image.isNull())
        spy = QSignalSpy(self.window.loginAccepted)
        self.window.enter_button.click()
        self.assertEqual(spy.at(0), ["netease"])

    def test_wrong_or_signed_out_account_cannot_create_account_card(self):
        self._login_page()
        for account in (AccountSnapshot("qq", True, "other", "other"), AccountSnapshot("netease")):
            self.window.set_account(account)
            self.assertIsNone(self.window._account)

    def test_confirmed_snapshot_account_displays_card_and_hide_discards_avatar(self):
        self._login_page()
        self.window.show()
        account = AccountSnapshot("netease", True, "test", "测试昵称", state="verified")
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="confirmed", account=account))
        self.assertEqual(self.window._account, account)
        image = QImage(800, 600, QImage.Format.Format_RGB32)
        image.fill(QColor("#135578"))
        self.window.set_account_artwork(image)
        self.assertFalse(self.window._account_image.isNull())
        self.assertLessEqual(self.window._account_image.width(), 512)
        self.window.hide()
        self.assertIsNone(self.window._account)
        self.assertTrue(self.window._account_image.isNull())
        self.window.set_account_artwork(image)
        self.assertTrue(self.window._account_image.isNull())

    def test_switching_source_clears_previous_platform_card(self):
        self._login_page()
        self.window.set_account(AccountSnapshot("netease", True, "test", "测试昵称"))
        self.window.select_source("qq")
        self.assertIsNone(self.window._account)
        self.assertEqual(self.window.login_state, "starting")
        self.assertEqual(self.window.enter_button.text(), "继续")


if __name__ == "__main__":
    unittest.main()
