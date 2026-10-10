"""Capture native Qt library/account states using clearly labelled UI fixtures.

This is a layout check, not a provider login or playlist integration check.
No network, credentials, settings, playback or production data is used.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, OnlineSong
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow


def _fixture_image(label="UI FIXTURE"):
    image = QImage(256, 256, QImage.Format.Format_RGB32)
    image.fill(QColor("#1b657f"))
    with QPainter(image) as painter:
        painter.setFont(QFont("Microsoft YaHei UI", 18))
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(8, 8, 240, 240), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, label)
    return image


def _capture(window, path: Path):
    # Flatten only the transparent outside corners for portable image-viewer
    # display; the pixels inside the native QWidget remain its exact grab.
    window.grab().toImage().convertToFormat(QImage.Format.Format_RGB32).save(str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/music-library-preview"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    account = AccountSnapshot("netease", True, "ui-fixture", "音乐账户用户名", state="verified")
    library = MusicLibraryWindow()
    library.set_account(account)
    catalog = (LibraryCollection("daily", "daily", "每日推荐", ""),
               LibraryCollection("fm", "fm", "私人漫游", ""),
               LibraryCollection("playlist-fixture", "playlist", "歌单名", "", "fixture"))
    library.set_collections(catalog)
    library.set_artwork("account", _fixture_image("头像\nUI FIXTURE"))
    for item in catalog:
        library.set_artwork(item.key, _fixture_image("封面\nUI FIXTURE"))
    library.show()
    app.processEvents()
    _capture(library, options.output / "library-expanded.png")
    for item in catalog:
        library.set_artwork(item.key, QImage())
    app.processEvents()
    _capture(library, options.output / "library-artwork-pending.png")
    for item in catalog:
        library.set_artwork(item.key, _fixture_image("封面\nUI FIXTURE"))
    library.set_error("UI FIXTURE · 播放请求未完成，请重试或更换歌曲")
    app.processEvents()
    _capture(library, options.output / "library-error-fixture.png")
    library.set_error("")
    library.set_toolbar_expanded(False)
    library._toolbar.setCurrentTime(library.TOOLBAR_ANIMATION_MS)
    app.processEvents()
    _capture(library, options.output / "library-compact.png")
    library.set_toolbar_expanded(True)
    library._toolbar.setCurrentTime(library.TOOLBAR_ANIMATION_MS)
    library.search.setText("布局测试")
    library._search_timer.stop()
    song = OnlineSong("netease", "fixture", "布局测试歌曲", "UI FIXTURE 歌手")
    library.set_search_results((LibraryCollection("song-fixture", "song", song.title, "", song=song), catalog[2]))
    library.set_artwork("song-fixture", _fixture_image("搜索结果\nUI FIXTURE"))
    app.processEvents()
    _capture(library, options.output / "library-search-fixture.png")
    library.search.clear()
    library.set_collections((LibraryCollection("long-fixture", "playlist", "这是用于检查省略与对齐的较长歌单名称 · UI FIXTURE", "", "fixture"),))
    library.resize(520, 440)
    app.processEvents()
    _capture(library, options.output / "library-minimum-expanded.png")
    library.set_toolbar_expanded(False)
    library._toolbar.setCurrentTime(library.TOOLBAR_ANIMATION_MS)
    app.processEvents()
    _capture(library, options.output / "library-minimum-compact.png")
    library.close()
    entry = PlaybackEntryWindow()
    entry.show_login()
    entry.set_sources(tuple(SimpleNamespace(source_id=item.source_id, available=item.source_id != "qishui", reason="暂无已验证接口") for item in ENTRY_SOURCES))
    entry.select_source("qq")
    entry._expansion.stop()
    entry._set_expanded(1.0)
    entry.set_account(AccountSnapshot("qq", True, "ui-fixture", "用户名", state="verified"))
    entry.set_account_artwork(_fixture_image("用户头像\nUI FIXTURE"))
    entry.show()
    app.processEvents()
    _capture(entry, options.output / "account-card-fixture.png")
    entry.close()
    print(options.output.resolve())


if __name__ == "__main__":
    main()
