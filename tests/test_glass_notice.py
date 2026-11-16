import unittest

from PySide6.QtWidgets import QApplication, QWidget

from local.glass_notice import FADE_MS, HOLD_S, SHOW_DELAY_MS, notice_for


class GlassNoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.host.resize(960, 540)
        self.notice = notice_for(self.host)

    def tearDown(self):
        self.host.deleteLater()

    def test_every_message_leaves_after_five_seconds_then_returns_on_the_next_trigger(self):
        self.assertEqual(HOLD_S, 5.0)
        self.notice.show_message("这台 Mac 只允许同一 Apple ID 的设备隔空播放。", kind="error")
        self.assertEqual(self.notice._hold.interval(), 5000 + SHOW_DELAY_MS + FADE_MS)
        self.assertTrue(self.notice._delay.isActive())
        # Time runs out: it fades and hides.
        self.notice._begin()
        self.notice.dismiss()
        self.notice._fade.setCurrentTime(FADE_MS)
        self.assertFalse(self.notice.isVisible())
        self.assertEqual(self.notice.text(), "")
        # The same message on the next trigger shows again.
        self.notice.show_message("这台 Mac 只允许同一 Apple ID 的设备隔空播放。", kind="error")
        self.assertTrue(self.notice._delay.isActive())
        self.assertTrue(self.host.rect().contains(self.notice.geometry()))


if __name__ == "__main__":
    unittest.main()
