"""Time library search keystrokes and result publication.

Prints one line per phase. Query text is not included. Network is not called.
Run from the repository root with QT_QPA_PLATFORM=offscreen.
"""
from __future__ import annotations

import os
import statistics
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from api.online_music import OnlineSong
from local.music_library_window import LibraryCollection, MusicLibraryWindow


def _ms(start):
    return (time.perf_counter() - start) * 1000.0


def once(app):
    window = MusicLibraryWindow()
    window.set_library_variant("classic")
    catalog = tuple(
        LibraryCollection(f"playlist-{index}", "playlist",
                          f"海阔天空 {index}" if index % 17 == 0 else f"歌单 {index}", "", str(index))
        for index in range(200)
    )
    window.set_collections(catalog)
    window.resize(960, 720)
    window.show()
    app.processEvents()
    samples = {}
    start = time.perf_counter()
    window.search.setText("海阔")
    app.processEvents()
    samples["keystroke_match"] = (_ms(start), len(window.collections))
    start = time.perf_counter()
    window.search.setText("海阔天空")
    app.processEvents()
    samples["keystroke_refine"] = (_ms(start), len(window.collections))
    songs = tuple(
        LibraryCollection(f"song-{index}", "song", f"海阔天空 {index}", "",
                          song=OnlineSong("netease", str(index), f"海阔天空 {index}", "Beyond"))
        for index in range(30)
    )
    window._search_timer.stop()
    start = time.perf_counter()
    window.set_search_results(songs)
    app.processEvents()
    samples["publish_30"] = (_ms(start), len(window._rows))
    start = time.perf_counter()
    window.set_search_results(songs)
    app.processEvents()
    samples["republish_30"] = (_ms(start), len(window._rows))
    start = time.perf_counter()
    window.search.clear()
    app.processEvents()
    samples["clear_restore"] = (_ms(start), len(window.collections))
    window.close()
    app.processEvents()
    return samples


def main():
    app = QApplication.instance() or QApplication([])
    runs = [once(app) for _ in range(7)]
    for name in ("keystroke_match", "keystroke_refine", "publish_30", "republish_30", "clear_restore"):
        times = [item[name][0] for item in runs]
        rows = runs[-1][name][1]
        print(f"phase={name} median_ms={statistics.median(times):.3f} min_ms={min(times):.3f} "
              f"max_ms={max(times):.3f} rows={rows}")


if __name__ == "__main__":
    main()
