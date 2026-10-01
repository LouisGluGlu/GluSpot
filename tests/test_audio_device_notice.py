import unittest
from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteSnapshot
from local.audio_device_notice import AudioDeviceNotice
from local.island_stack_visual import IslandStackVisual


class AudioDeviceNoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_notice_retargets_and_expires_without_paint_advancing_time(self):
        notice = AudioDeviceNotice()
        notice.present("蓝牙耳机", "蓝牙设备已接入", "headphones")
        notice._timer.stop()
        before = notice._elapsed
        image = QImage(notice.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        notice.render(image)
        self.assertEqual(notice._elapsed, before)
        notice._elapsed = 3.31
        notice._advance()
        self.assertFalse(notice.isVisible())
        self.assertFalse(notice._timer.isActive())
        notice.present("HomePod", "投放已连接", "homepod")
        self.assertEqual(notice._elapsed, 0)
        notice.close()

    def test_output_delay_changes_reading_but_not_seek_offsets_or_player_clock(self):
        snapshot = AudioRouteSnapshot(state="casting", cast_latency_seconds=1.5)
        provider = SimpleNamespace(get_audio_route_snapshot=lambda: snapshot)
        owner = SimpleNamespace(lyric_offset_seconds=0.2, media_app_lyric_offset_seconds=0.3,
                                data_provider=provider, media_position_seconds=10)
        offset = IslandStackVisual._effective_lyric_offset_seconds
        self.assertEqual(offset(owner), -1.0)
        self.assertEqual(offset(owner, include_output_latency=False), 0.5)
        self.assertEqual(owner.media_position_seconds, 10)
        snapshot = replace(snapshot, state="ready", cast_latency_seconds=0)
        self.assertEqual(offset(owner), 0.5)


if __name__ == "__main__":
    unittest.main()
