"""Offscreen check that the two search demos place groups differently."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from demo_sections import SectionSearch
from demo_shelves import ShelfSearch


def main():
    app = QApplication.instance() or QApplication([])
    section = SectionSearch()
    shelf = ShelfSearch()
    section.show()
    shelf.show()
    app.processEvents()
    section.repaint()
    shelf.repaint()
    for window in (section, shelf):
        missing = [kind for kind in ("song", "album", "artist", "playlist") if kind not in window.group_rects]
        if missing:
            raise SystemExit(f"{window.windowTitle()} missing {missing}")
    song_y = section.group_rects["song"].y()
    album_y = section.group_rects["album"].y()
    artist_y = section.group_rects["artist"].y()
    playlist_y = section.group_rects["playlist"].y()
    if not (song_y < album_y < artist_y < playlist_y):
        raise SystemExit(f"section layout is not vertical: {section.group_rects}")
    shelf_song = shelf.group_rects["song"]
    shelf_album = shelf.group_rects["album"]
    if shelf_album.y() <= shelf_song.y():
        raise SystemExit("shelf groups did not stack as separate rows")
    if abs(shelf_song.x() - shelf_album.x()) > 1:
        raise SystemExit("shelf headings should share the left edge")
    # Cards in one shelf sit on the same horizontal line; sections stack rows.
    if section.group_rects["album"].y() - section.group_rects["song"].y() < 80:
        raise SystemExit("section groups are too close to be separate lists")
    section.query.setText("海")
    shelf.query.setText("海")
    section.repaint()
    shelf.repaint()
    for kind in ("song", "album", "playlist"):
        if kind not in section.group_rects or kind not in shelf.group_rects:
            raise SystemExit(f"query 海 dropped {kind}")
    if "artist" in section.group_rects:
        raise SystemExit("query 海 should not invent an artist group")
    print("search_t110 layout ok")
    print(f"sections y song={song_y:.0f} album={album_y:.0f} artist={artist_y:.0f} playlist={playlist_y:.0f}")
    print(f"shelves y song={shelf_song.y():.0f} album={shelf_album.y():.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
