from __future__ import annotations

import unittest

from api.message_detector import WeChatSessionCandidate, WindowCandidate, WindowsMessageDetector


class FakeWeChatItem:
    def descendants(self, control_type=None):
        return []


class MessageDetectorTests(unittest.TestCase):
    def test_extracts_wechat_toast_text(self):
        detector = WindowsMessageDetector()
        candidate = WindowCandidate(
            title="新通知",
            class_name="Windows.UI.Core.CoreWindow",
            texts=("微信", "张三", "晚上开会吗？"),
        )

        message = detector._message_from_candidate(candidate)

        self.assertIsNotNone(message)
        self.assertEqual(message.app, "微信")
        self.assertEqual(message.sender, "张三")
        self.assertEqual(message.text, "晚上开会吗？")

    def test_ignores_unrelated_window(self):
        detector = WindowsMessageDetector()
        candidate = WindowCandidate(title="README.md - Notepad", class_name="Notepad", texts=("README",))

        self.assertIsNone(detector._message_from_candidate(candidate))

    def test_extracts_wechat_system_notification_text(self):
        detector = WindowsMessageDetector()

        message = detector._message_from_notification_parts("微信", "Tencent.WeChat", ("张三", "晚上开会吗？"))

        self.assertIsNotNone(message)
        self.assertEqual(message.app, "微信")
        self.assertEqual(message.sender, "张三")
        self.assertEqual(message.text, "晚上开会吗？")

    def test_extracts_wechat_session_unread_message(self):
        detector = WindowsMessageDetector()
        texts = ["张三", "21:08", "晚上开会吗？", "2"]

        unread = detector._wechat_unread_count_from_item(FakeWeChatItem(), texts)
        text = detector._wechat_last_session_text("张三", texts)

        self.assertEqual(unread, 2)
        self.assertEqual(text, "晚上开会吗？")

    def test_extracts_wechat_4_session_unread_message(self):
        detector = WindowsMessageDetector()
        texts = ["贝", "已置顶", "[1条]", "但是变卡了很多", "01:47"]

        unread = detector._wechat_unread_count_from_texts(texts)
        text = detector._wechat_last_session_text("贝", texts)

        self.assertEqual(unread, 1)
        self.assertEqual(text, "但是变卡了很多")

    def test_wechat_session_message_contract(self):
        detector = WindowsMessageDetector()
        candidate = WeChatSessionCandidate("项目群", "李四: 版本已发", 1, ("项目群", "李四: 版本已发", "1"))

        message = detector._message_from_wechat_session(candidate)

        self.assertIsNotNone(message)
        self.assertEqual(message.app, "微信")
        self.assertEqual(message.sender, "项目群")
        self.assertEqual(message.text, "李四: 版本已发")

    def test_wechat_unread_prefers_changed_session(self):
        detector = WindowsMessageDetector()
        old_session = WeChatSessionCandidate("张三", "旧消息", 1, ("张三", "旧消息", "1"))
        changed_session = WeChatSessionCandidate("项目群", "李四: 新消息", 2, ("项目群", "李四: 新消息", "2"))
        previous = {
            old_session.session: detector._wechat_session_key(old_session),
            changed_session.session: "1\0李四: 旧消息\0项目群|李四: 旧消息|1",
        }

        message = detector._message_from_wechat_unread(3, [old_session, changed_session], previous)

        self.assertIsNotNone(message)
        self.assertEqual(message.sender, "项目群")
        self.assertEqual(message.text, "李四: 新消息")


    def test_wechat_unread_avoids_guessing_when_many_sessions_are_unchanged(self):
        detector = WindowsMessageDetector()
        first = WeChatSessionCandidate("A", "old", 2, ("A", "old", "2"))
        second = WeChatSessionCandidate("B", "old", 1, ("B", "old", "1"))
        previous = {
            first.session: detector._wechat_session_key(first),
            second.session: detector._wechat_session_key(second),
        }

        message = detector._message_from_wechat_unread(3, [first, second], previous, total_delta=2)

        self.assertIsNotNone(message)
        self.assertEqual(message.sender, message.app)

    def test_wechat_notification_toggle_can_disable_toast_messages(self):
        detector = WindowsMessageDetector(lambda key, default=None: False if key == "v2_enable_wechat_notification" else default)

        message = detector._message_from_notification_parts("微信", "Tencent.WeChat", ("张三", "晚上开会吗？"))

        self.assertIsNone(message)

    def test_wechat_notification_toggle_can_disable_toast_messages_even_when_detail_enabled(self):
        def settings(key, default=None):
            if key == "v2_enable_wechat_notification":
                return False
            if key == "v2_enable_wechat_detail_notification":
                return True
            return default

        detector = WindowsMessageDetector(settings)

        message = detector._message_from_notification_parts("wechat", "Tencent.WeChat", ("Alice", "hello"))

        self.assertIsNone(message)

    def test_wechat_summary_unread_is_suppressed_when_new_notification_toggle_is_off(self):
        def settings(key, default=None):
            if key == "v2_enable_wechat_notification":
                return False
            if key == "v2_enable_wechat_detail_notification":
                return True
            return default

        detector = WindowsMessageDetector(settings)
        detector._wechat_initialized = True
        detector._wechat_previous_total_unread_count = 1
        detector._native_wechat_windows = lambda: []
        detector._set_wechat_native_diagnostics = lambda _windows: None
        detector._wechat_main_native_windows = lambda _windows: []
        detector._uiautomation_wechat_total_unread = lambda _windows: 2
        detector._wechat_should_scan_sessions = lambda *_args, **_kwargs: False
        detector._set_wechat_diagnostics = lambda *_args, **_kwargs: None

        messages = detector._wechat_messages(10.0)

        self.assertEqual(messages, [])

    def test_wechat_detail_message_is_suppressed_when_new_notification_toggle_is_off(self):
        def settings(key, default=None):
            if key == "v2_enable_wechat_notification":
                return False
            if key == "v2_enable_wechat_detail_notification":
                return True
            return default

        detector = WindowsMessageDetector(settings)
        detector._wechat_initialized = True
        detector._wechat_previous_total_unread_count = 0
        detector._wechat_seen_sessions = {}
        session = WeChatSessionCandidate("Project", "new detail", 1, ("Project", "new detail", "1"))
        detector._native_wechat_windows = lambda: []
        detector._set_wechat_native_diagnostics = lambda _windows: None
        detector._wechat_main_native_windows = lambda _windows: []
        detector._uiautomation_wechat_total_unread = lambda _windows: 1
        detector._wechat_should_scan_sessions = lambda *_args, **_kwargs: True
        detector._uiautomation_wechat_sessions = lambda _windows: [session]
        detector._set_wechat_diagnostics = lambda *_args, **_kwargs: None

        messages = detector._wechat_messages(10.0)

        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
