from __future__ import annotations

from pathlib import Path
import unittest

from api.providers import MISSING_WINSDK_VISUAL_DATA, NO_SESSION_VISUAL_DATA, empty_visual_data


ROOT_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (ROOT_DIR / "api", ROOT_DIR / "local")
MOJIBAKE_MARKERS = (
    "\ufffd",
    "鏆",
    "濯",
    "鎵",
    "姝岃瘝",
    "缃戞",
    "閫€",
    "璁剧疆",
    "绠＄悊",
    "鈥",
    "鈼",
    "掳",
)


class TextEncodingTest(unittest.TestCase):
    def test_user_facing_python_strings_do_not_contain_common_mojibake(self):
        for source_dir in SOURCE_DIRS:
            for path in source_dir.glob("*.py"):
                text = path.read_text(encoding="utf-8")
                with self.subTest(path=path.relative_to(ROOT_DIR)):
                    for marker in MOJIBAKE_MARKERS:
                        self.assertNotIn(marker, text)

    def test_provider_empty_states_are_readable_chinese(self):
        empty = empty_visual_data().track
        self.assertEqual(empty.title, "暂无媒体")
        self.assertEqual(empty.artist, "打开音乐播放器后会自动同步")
        self.assertEqual(empty.lyrics, ("暂无歌词",))
        self.assertEqual(MISSING_WINSDK_VISUAL_DATA.track.title, "媒体依赖缺失")
        self.assertEqual(MISSING_WINSDK_VISUAL_DATA.track.artist, "运行 setup-env.bat 后重新启动")
        self.assertEqual(NO_SESSION_VISUAL_DATA.track.artist, "播放一首歌后会自动同步")


if __name__ == "__main__":
    unittest.main()
