from __future__ import annotations

import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

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
        self.assertEqual(self.window.selection, (True, True))
        self.window.owned_button.click()
        self.assertEqual(self.window.selection, (False, True))
        self.window.system_button.click()
        self.assertEqual(self.window.selection, (False, True))
        self.assertIn("至少保留", self.window.mode_hint.text())
        self.assertIsNotNone(self.window.system_button._rejected_at)
        self.window.owned_button.click()
        self.assertEqual(self.window.selection, (True, True))
        self.window.system_button.click()
        self.assertEqual(self.window.selection, (True, False))

    def test_compact_verification_retry_label_fits_the_action(self):
        self._login_page()
        self.window.resize(330, 400)
        self.window.set_login_snapshot(SimpleNamespace(
            source_id="netease", state="error", failure_stage="account_status",
            message="扫码已确认，账号核验失败；点击重试核验"))
        button = self.window.refresh_button
        self.assertEqual(button.text(), "重试账号核验")
        self.assertLessEqual(button.fontMetrics().horizontalAdvance(button.text()), button.width() - 20)
        self.window.resize(960, 540)
        self.window._layout_children()
        self.assertEqual(button.font().pixelSize(), 15)

    def test_disposing_entry_after_page_resize_preserves_shared_screen_wrapper(self):
        shared = QGuiApplication.primaryScreen()
        entry = PlaybackEntryWindow()
        entry.show_sources()
        entry._set_expanded(1.0)
        entry.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(isValid(shared))
        self.assertIs(QGuiApplication.primaryScreen(), shared)

    def test_continue_requires_explicit_click_and_only_emits_selection(self):
        spy = QSignalSpy(self.window.selectionAccepted)
        self.assertEqual(spy.count(), 0)
        self.window.continue_button.click()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), [True, True])
        self.assertEqual(self.window.page, "modes")

    def test_source_order_and_unsupported_source_are_explicit(self):
        self.window.set_sources(tuple(_source(item.source_id, item.source_id != "qishui") for item in ENTRY_SOURCES))
        self.assertEqual(tuple(self.window.source_buttons), ("netease", "kugou", "qq", "qishui"))
        self.assertFalse(self.window.source_buttons["qishui"].isEnabled())
        self.assertIn("暂无经过验证", self.window.source_buttons["qishui"].toolTip())
        spy = QSignalSpy(self.window.sourceSelected)
        self.window.select_source("qishui")
        self.assertEqual(spy.count(), 0)

    def test_unavailable_source_tiles_explain_runtime_and_unsupported_states(self):
        sources = (
            SimpleNamespace(source_id="netease", available=False, reason="在线音乐运行时未安装"),
            SimpleNamespace(source_id="kugou", available=False, reason="在线音乐适配依赖未安装，请运行 prepare-runtime.ps1"),
            SimpleNamespace(source_id="qq", available=True, reason=""),
            SimpleNamespace(source_id="qishui", available=False, reason="汽水音乐尚无已验证且可随本项目分发的扫码适配器"),
        )
        self.window.set_sources(sources)
        netease = self.window.source_buttons["netease"]
        kugou = self.window.source_buttons["kugou"]
        qishui = self.window.source_buttons["qishui"]
        self.assertEqual(netease.availability_status, "需安装运行时")
        self.assertEqual(kugou.availability_status, "需安装运行时")
        self.assertEqual(qishui.availability_status, "暂不支持")
        self.assertIn("需安装运行时", netease.accessibleName())
        self.assertIn("在线音乐运行时未安装", netease.accessibleDescription())
        self.assertIn("暂不支持", qishui.accessibleName())
        self.assertIn("汽水音乐尚无已验证", qishui.accessibleDescription())
        self.assertFalse(netease.isEnabled())
        self.assertFalse(kugou.isEnabled())
        self.assertFalse(qishui.isEnabled())
        self.assertTrue(self.window.source_buttons["qq"].isEnabled())
        self.assertEqual(self.window.message_label.text(), "选择音乐来源")

    def test_all_unavailable_sources_footer_names_recovery_paths(self):
        self.window.set_sources(tuple(
            SimpleNamespace(source_id=item.source_id, available=False,
                            reason=("在线音乐运行时未安装" if item.source_id != "qishui" else
                                    "汽水音乐尚无已验证且可随本项目分发的扫码适配器"))
            for item in ENTRY_SOURCES
        ))
        self.assertEqual(self.window.message_label.text(), "没有可用音乐来源；请安装在线音乐运行时；汽水音乐暂不支持。")

    def test_source_recovery_clears_diagnostic_and_restores_selectable_tile(self):
        self.window.set_sources((SimpleNamespace(source_id="netease", available=False,
                                                 reason="在线音乐适配依赖版本不匹配"),))
        button = self.window.source_buttons["netease"]
        self.assertEqual(button.availability_status, "需更新运行时")
        self.assertIn("请更新在线音乐运行时", self.window.message_label.text())
        self.window.set_sources((_source("netease"),))
        self.assertTrue(button.isEnabled())
        self.assertEqual(button.availability_status, "")
        self.assertEqual(button.accessibleName(), "网易云音乐")
        self.assertEqual(button.accessibleDescription(), "")
        self.assertEqual(button.toolTip(), "网易云音乐")
        self.assertEqual(self.window.message_label.text(), "选择音乐来源")
        spy = QSignalSpy(self.window.sourceSelected)
        button.click()
        self.assertEqual(spy.at(0), ["netease"])

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
            if state == "unsupported":
                self.assertFalse(self.window.refresh_button.isEnabled())
            elif state in {"expired", "error", "cancelled"}:
                self.assertTrue(self.window.refresh_button.isEnabled(), state)

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
        button.setChecked(False)
        button.setChecked(True)
        started = button._selected_at
        button.advance(started + .99)
        self.assertEqual(button._progress, 1.0)
        self.assertIsNone(button._selected_at)

    def test_first_run_uses_compact_black_island_without_repainting_flow_background(self):
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.show()
        self.window._frame_timer.stop()
        self.assertLessEqual(self.window.width(), 370)
        self.assertEqual(self.window.height(), 176)
        with patch.object(self.window._flow, "render") as flow:
            self.window._advance()
        flow.assert_not_called()
        self.assertTrue(self.window._background_frame.isNull())
        image = self.window.grab().toImage()
        ratio = image.devicePixelRatio()
        self.assertEqual(image.pixelColor(round(15 * ratio), round(88 * ratio)), QColor("#000000"))
        self.assertEqual(self.window.continue_button.text(), "确定")
        self.assertEqual(self.window.mode_hint.text(), "可在设置中更改")

    def test_installer_island_expands_at_the_same_anchor_and_releases_snapshot(self):
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        origin = QRect(510, 12, 340, 52)
        self.window.show_choices_from(origin)
        self.window.show()
        self.assertEqual(self.window.geometry(), origin)
        self.assertFalse(self.window._page_morph_to.isNull())
        self.assertTrue(self.window.continue_button.isHidden())
        self.window._page_morph.setCurrentTime(210)
        self.assertGreater(self.window.height(), origin.height())
        self.assertLess(self.window.height(), 176)
        self.window._page_morph.setCurrentTime(420)
        self.assertEqual(self.window.height(), 176)
        self.assertEqual(self.window.geometry().center().x(), origin.center().x())
        self.assertTrue(self.window._page_morph_to.isNull())
        self.assertFalse(self.window.continue_button.isHidden())
        self.assertFalse(self.window.owned_button.isHidden())

    def test_confirmation_expands_same_window_to_source_login_without_immediate_controls(self):
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.show()
        self.app.processEvents()
        origin = QRect(self.window.geometry())
        self.window.show_login()
        self.assertEqual(self.window.page, "sources")
        self.assertEqual(self.window.geometry(), origin)
        self.assertFalse(self.window._page_morph_from.isNull())
        self.assertTrue(self.window.source_buttons["netease"].isHidden())
        self.window._page_morph.setCurrentTime(420)
        self.assertEqual(self.window.width(), 560)
        self.assertEqual(self.window.height(), 264)
        self.assertFalse(self.window.source_buttons["netease"].isHidden())
        self.assertTrue(self.window._page_morph_from.isNull())
        self.assertTrue(self.window._page_morph_to.isNull())

    def test_close_during_page_expansion_discards_both_snapshots(self):
        self.window.show_choices_from(QRect(510, 12, 340, 52))
        self.window.show()
        self.window.close()
        self.assertFalse(self.window._page_morph.state() == self.window._page_morph.State.Running)
        self.assertTrue(self.window._page_morph_from.isNull())
        self.assertTrue(self.window._page_morph_to.isNull())

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
        self.assertTrue(self.window.rect().contains(self.window.enter_button.geometry()))
        self.assertGreaterEqual(self.window.enter_button.height(), 44)
        self.assertEqual(self.window.enter_button.text(), "进入音乐库")
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

    def test_source_and_account_navigation_use_clear_targets_within_window(self):
        self._login_page()
        for button in (self.window.back_button, self.window.close_button, self.window.refresh_button, *self.window.source_buttons.values()):
            self.assertGreaterEqual(button.width(), 44)
            self.assertGreaterEqual(button.height(), 44)
            self.assertTrue(self.window.rect().contains(button.geometry()))
        self.assertGreaterEqual(self.window.height() - 24 - self.window.refresh_button.geometry().bottom(), 16)
        self.assertFalse(self.window.message_label.geometry().intersects(self.window.refresh_button.geometry()))
        self.window.set_account(AccountSnapshot("netease", True, "fixture", "较长的布局测试账号昵称" * 3, state="verified"))
        self.assertTrue(self.window.rect().contains(self.window.enter_button.geometry()))
        self.assertFalse(self.window.enter_button.geometry().intersects(self.window.source_buttons["qq"].geometry()))

    def test_modes_sources_and_account_keep_navigation_on_the_library_header_baseline(self):
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.close_button.geometry(), QRect(self.window.width() - 68, 16, 44, 44))
        self.window.set_mode_back_enabled(False)
        self.window.show_sources()
        for account in (None, AccountSnapshot("netease", True, "fixture", "测试账号", state="verified")):
            with self.subTest(account_card=account is not None):
                if account is not None:
                    self.window.set_sources((_source("netease"),))
                    self.window.select_source("netease")
                    self.window._expansion.stop()
                    self.window._set_expanded(1.0)
                    self.window.set_account(account)
                self.assertEqual(self.window.close_button.geometry(), QRect(self.window.width() - 68, 16, 44, 44))
                self.assertEqual(self.window.back_button.geometry(), QRect(24, 16, 44, 44))
                image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                with patch("local.playback_entry_window.QPainter", return_value=painter, RenderHint=QPainter.RenderHint), \
                        patch.object(painter, "drawText", wraps=painter.drawText) as drawn:
                    self.window.paintEvent(None)
                heading = next(call.args[0] for call in drawn.call_args_list if call.args[-1] == "音乐来源")
                self.assertEqual(heading, QRectF(84 if account is not None else 24, 20, self.window.width() - 168, 36))
                self.assertEqual(heading.center().y(), self.window.close_button.geometry().y() + self.window.close_button.height() / 2)
                self.assertFalse(heading.intersects(QRectF(self.window.close_button.geometry())))
                if account is not None:
                    self.assertFalse(heading.intersects(QRectF(self.window.back_button.geometry())))

    def test_login_page_adapts_to_short_display_without_cutting_navigation_or_actions(self):
        self._login_page()
        available = QRect(0, 0, 520, 520)
        screen = SimpleNamespace(availableGeometry=lambda: available)
        with patch.object(QGuiApplication, "screenAt", return_value=screen):
            self.window._set_expanded(1.0)
        self.assertLessEqual(self.window.width(), available.width())
        self.assertLessEqual(self.window.height(), available.height())
        for button in (self.window.back_button, self.window.close_button, self.window.refresh_button, *self.window.source_buttons.values()):
            self.assertTrue(self.window.rect().contains(button.geometry()))
            self.assertGreaterEqual(button.height(), 44)
            self.assertGreaterEqual(button.width(), 44)
        self.assertFalse(self.window.message_label.geometry().intersects(self.window.refresh_button.geometry()))
        self.assertGreaterEqual(self.window.height() - 24 - self.window.refresh_button.geometry().bottom(), 16)
        self.assertFalse(self.window.grab().isNull())

    def test_active_source_click_keeps_existing_qr_and_does_not_restart_login(self):
        self._login_page()
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes()))
        qr_key = self.window._qr_image.cacheKey()
        selected = QSignalSpy(self.window.sourceSelected)
        refresh = QSignalSpy(self.window.refreshRequested)
        self.window.source_buttons["netease"].click()
        self.assertTrue(self.window.source_buttons["netease"].isChecked())
        self.assertEqual(self.window.login_state, "waiting")
        self.assertEqual(self.window._qr_image.cacheKey(), qr_key)
        self.assertEqual(selected.count(), 0)
        self.assertEqual(refresh.count(), 0)

    def test_active_source_click_keeps_verified_account_and_explicit_library_action(self):
        self._login_page()
        account = AccountSnapshot("netease", True, "fixture", "测试账号", state="verified")
        self.window.set_account(account)
        selected = QSignalSpy(self.window.sourceSelected)
        self.window.source_buttons["netease"].click()
        self.assertIs(self.window._account, account)
        self.assertEqual(self.window.enter_button.text(), "进入音乐库")
        self.assertEqual(selected.count(), 0)

    def test_login_back_returns_to_source_picker_before_optional_mode_page(self):
        self._login_page()
        self.window.set_mode_back_enabled(True)
        back = QSignalSpy(self.window.backRequested)
        self.assertEqual(self.window.back_button.accessibleName(), "返回音乐来源")
        self.window.back_button.click()
        self.assertEqual(self.window.page, "sources")
        self.assertEqual(self.window.selected_source, "")
        self.assertEqual(back.count(), 1)
        self.assertEqual(self.window.back_button.accessibleName(), "返回播放模式")
        self.window.back_button.click()
        self.assertEqual(self.window.page, "modes")
        self.assertEqual(back.count(), 2)

    def test_failure_and_cancelled_states_expose_explicit_retry_without_waiting_copy(self):
        self._login_page()
        for state, placeholder in (("error", "暂时无法连接"), ("cancelled", "登录已取消"), ("expired", "二维码已过期")):
            with self.subTest(state=state):
                self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state=state))
                self.assertEqual(self.window._login_placeholder_text(), placeholder)
                self.assertTrue(self.window.refresh_button.isEnabled())
                self.assertEqual(self.window.refresh_button.text(), "刷新二维码" if state == "expired" else "重试登录")
                self.assertEqual(self.window.refresh_button.accessibleName(), self.window.refresh_button.text())
                refreshed = QSignalSpy(self.window.refreshRequested)
                self.window.refresh_button.click()
                self.assertEqual(refreshed.count(), 1)
                self.assertEqual(self.window.login_state, "starting")
                self.assertFalse(self.window.refresh_button.isEnabled())

    def test_standard_scan_instruction_is_concise_but_distinct_backend_diagnostics_are_preserved(self):
        self._login_page()
        self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=_image_bytes(),
                                                     message="使用手机音乐应用扫码登录"))
        self.assertEqual(self.window.message_label.text(), "等待扫码")
        self.assertEqual(self.window.message_label.accessibleDescription(), "使用手机音乐应用扫码登录")
        for state, message in (("waiting", "请使用对应平台最新版 App 扫码"),
                               ("error", "登录请求失败，请刷新二维码重试"),
                               ("error", "账号读取失败，请重试")):
            with self.subTest(state=state, message=message):
                self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state=state,
                                                             qr_image_bytes=_image_bytes(), message=message))
                self.assertEqual(self.window.message_label.text(), message)
                if state == "error":
                    self.assertEqual(self.window.refresh_button.text(), "刷新二维码" if "刷新" in message else "重试登录")

    def test_login_qr_status_and_action_remain_adjacent_without_overlap_on_short_display(self):
        self._login_page()
        available = QRect(0, 0, 520, 520)
        with patch.object(QGuiApplication, "screenAt", return_value=SimpleNamespace(availableGeometry=lambda: available)):
            self.window._set_expanded(1.0)
        qr, detail = self.window._login_content_rects()
        self.assertGreaterEqual(qr.width(), 160)
        self.assertTrue(QRectF(self.window.rect()).contains(qr))
        self.assertLess(qr.right(), detail.left())
        self.assertGreaterEqual(self.window.message_label.geometry().left(), detail.left() - 1)
        self.assertGreaterEqual(self.window.refresh_button.geometry().left(), detail.left() - 1)
        self.assertFalse(qr.intersects(QRectF(self.window.message_label.geometry())))
        self.assertFalse(qr.intersects(QRectF(self.window.refresh_button.geometry())))

    def test_source_glass_backdrop_is_bounded_static_and_rebuilt_after_hide(self):
        self.window.show_sources()
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window._background_frame.isNull())
        self.assertLessEqual(self.window._background_frame.width(), 256)
        self.assertLessEqual(self.window._background_frame.height(), 256)
        cache_key = self.window._background_frame.cacheKey()
        self.window._layout_children()
        self.assertEqual(self.window._background_frame.cacheKey(), cache_key)
        self.assertFalse(self.window._frame_timer.isActive())
        self.window.hide()
        self.assertTrue(self.window._background_frame.isNull())
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window._background_frame.isNull())
        self.assertFalse(self.window._frame_timer.isActive())

    def test_compact_460_and_360_displays_keep_qr_status_retry_and_account_action_contained(self):
        self._login_page()
        for height in (460, 360):
            with self.subTest(height=height):
                available = QRect(0, 0, 560, height)
                screen = SimpleNamespace(availableGeometry=lambda: available)
                with patch.object(QGuiApplication, "screenAt", return_value=screen):
                    self.window.select_source("qq")
                    self.window.select_source("netease")
                    self.window._expansion.stop()
                    self.window._set_expanded(1.0)
                    self.window.set_login_snapshot(SimpleNamespace(source_id="netease", state="error",
                                                                 message="登录请求失败，请刷新二维码重试"))
                    bounds = QRectF(self.window.rect())
                    qr, _detail = self.window._login_content_rects()
                    title, helper, message, action = self.window._login_detail_geometry()
                    self.assertGreaterEqual(qr.width(), 96)
                    for rect in (qr, title, helper, message, action):
                        self.assertTrue(bounds.contains(rect), rect)
                    self.assertFalse(message.intersects(action))
                    self.assertFalse(helper.intersects(message))
                    self.assertFalse(qr.intersects(action))
                    self.assertFalse(self.window.message_label.geometry().intersects(self.window.refresh_button.geometry()))
                    for button in (self.window.close_button, self.window.back_button,
                                   self.window.refresh_button, *self.window.source_buttons.values()):
                        self.assertTrue(self.window.rect().contains(button.geometry()), button.geometry())
                        self.assertGreaterEqual(button.height(), 44)
                        self.assertGreaterEqual(button.width(), 44)
                    self.window.set_account(AccountSnapshot("netease", True, "fixture", "测试账号", state="verified"))
                    avatar, name, enter = self.window._account_content_rects()
                    for rect in (avatar, name, enter):
                        self.assertTrue(QRectF(self.window.rect()).contains(rect), rect)
                    self.assertFalse(name.intersects(enter))
                    self.assertFalse(avatar.intersects(enter))
                    self.assertTrue(self.window.rect().contains(self.window.enter_button.geometry()))
                    self.assertGreaterEqual(self.window.enter_button.height(), 44)

    def test_normal_height_expansion_does_not_move_sources_into_compact_row(self):
        self.window.show_sources()
        self.window.set_sources(tuple(_source(item.source_id) for item in ENTRY_SOURCES))
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 960, 1080))
        with patch.object(QGuiApplication, "screenAt", return_value=screen):
            self.window.select_source("netease")
            self.window._expansion.stop()
            for fraction in (0.0, .3, .65, 1.0):
                self.window._set_expanded(fraction)
                self.assertEqual(self.window.source_buttons["netease"].geometry().y(), 112)
                self.assertEqual(self.window.source_buttons["netease"].height(), 96)


if __name__ == "__main__":
    unittest.main()
