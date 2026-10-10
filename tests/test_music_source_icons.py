from __future__ import annotations

import hashlib
import re
import unittest
import xml.etree.ElementTree as ET

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from local.branding import ROOT_DIR
from local.music_source_icons import music_source_icon_image


USER_SVG_HASHES = {
    "qq": "3289a77ba0bef15680ebfeb1eb915502afeb12eb1a67b800b145dc60992907c4",
    "qishui": "1333697a2a9e0370049f75e886d2628c86ef669465cb24ec550b45b9dc3f1cfa",
    "kugou": "a17073123a027618e7442e81504f11100d390489b84edb01d841db9dd9fb8687",
    "netease": "09b41126393bda55692630e3bc455013b2b403eede123f3d462171752aa08486",
    "search": "67232ae5aa071ed77b8dd289fc418516b5009e93fa1bc2d66aa1eda22826c250",
    "logout": "726af968163ac94794ca55ce20ad3c79d85ad37d73aeafacba74902b92afb804",
    "switch": "37b1d01432b58498395fb9e5d0e2a93f3b650a03e5c156be153dec62c8ed62b4",
}


class MusicSourceIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_supplied_assets_keep_original_bytes_are_local_geometry_and_render(self):
        for key, digest in USER_SVG_HASHES.items():
            with self.subTest(icon=key):
                path = ROOT_DIR / "assets" / "music_sources" / f"{key}.svg"
                data = path.read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
                root = ET.fromstring(data)
                self.assertEqual({element.tag.rsplit("}", 1)[-1] for element in root.iter()}, {"svg", "path"})
                self.assertNotRegex(data, rb"<!ENTITY|<!DOCTYPE[^>]*\[|\bon\w+\s*=")
                for element in root.iter():
                    for name, value in element.attrib.items():
                        if name.rsplit("}", 1)[-1] in {"href", "src"}:
                            self.assertTrue(value.startswith("#"))
                        for reference in re.findall(r"url\((.*?)\)", value):
                            self.assertTrue(reference.strip().startswith("#"))
                self.assertTrue(QSvgRenderer(str(path)).isValid())
                image = music_source_icon_image(key, QSize(72, 72))
                alpha = [image.pixelColor(x, y).alpha() for y in range(72) for x in range(72)]
                self.assertGreater(sum(value > 0 for value in alpha), 100)

    def test_non_square_slot_preserves_svg_ratio_and_centers_ink(self):
        image = music_source_icon_image("search", QSize(180, 90))
        visible = [(x, y) for y in range(90) for x in range(180) if image.pixelColor(x, y).alpha() > 0]
        xs, ys = zip(*visible)
        self.assertLessEqual(max(xs) - min(xs) + 1, 90)
        self.assertLessEqual(max(ys) - min(ys) + 1, 90)
        self.assertGreaterEqual(min(xs), 45)
        self.assertLessEqual(max(xs), 134)

    def test_monochrome_tint_retains_shape_and_source_color_render_stays_original(self):
        original = music_source_icon_image("switch", QSize(64, 64))
        tinted = music_source_icon_image("switch", QSize(64, 64), tint=QColor("white"))
        for y in range(64):
            for x in range(64):
                before, after = original.pixelColor(x, y), tinted.pixelColor(x, y)
                self.assertEqual(before.alpha(), after.alpha())
                if before.alpha() == 255:
                    self.assertEqual(before.name(), "#000000")
                    self.assertEqual(after.name(), "#ffffff")
        qq = music_source_icon_image("qq", QSize(64, 64))
        colors = {qq.pixelColor(x, y).name() for y in range(64) for x in range(64)
                  if qq.pixelColor(x, y).alpha() == 255}
        self.assertIn("#33c079", colors)
        self.assertIn("#fddb00", colors)


if __name__ == "__main__":
    unittest.main()
