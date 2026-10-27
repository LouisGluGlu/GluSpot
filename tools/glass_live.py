"""Interactive, isolated Clear glass fixture for comparing refresh intervals."""
import argparse
from collections import deque
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

parser = argparse.ArgumentParser()
parser.add_argument('--throttle', type=float, default=0.)
parser.add_argument('--auto-press', action='store_true')
parser.add_argument('--seconds', type=float, default=0.)
args = parser.parse_args()
os.environ['GLUSPOT_GLASS_MOVING_REFRESH'] = str(max(0., args.throttle))
sandbox = tempfile.TemporaryDirectory(prefix='gluspot-glass-')
for name in ('HOME', 'APPDATA', 'LOCALAPPDATA'):
    os.environ[name] = sandbox.name

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from api.online_music import OnlineSong
from local.music_surface_window import MusicSurfaceWindow
from test_music_surface_transition import _TransitionOwner
from test_owned_playback_surface import _Provider
from test_song_collection_surface import Collection

app = QApplication.instance() or QApplication([])
owner = _TransitionOwner()
owner.media_track_id = 'owned:netease:1'
owner.track_title, owner.artist = 'Somebody Told Me', 'Charlie Puth'
owner._song_collection_controller = Collection()
owner._music_library_back_callback = Mock()
provider = _Provider()
provider.fm = True
provider.get_owned_personal_dj_enabled = lambda: True
provider.is_owned_personal_dj_speaking = lambda: False
provider.get_owned_playback_queue = lambda: (1, 2, tuple(OnlineSong('netease', str(i), f'Song {i + 1}', artist='GluSpot') for i in range(12)))
owner.data_provider = provider
owner.settings_getter = lambda key, default=None: {'v2_music_glass_level': 50, 'v2_theme_mode': 'light'}.get(key, default)
owner.album_color, owner.secondary_color = QColor(180, 120, 25), QColor(100, 31, 28)
art = QImage(600, 600, QImage.Format.Format_RGB32)
with QPainter(art) as p:
    g = QLinearGradient(0, 0, 600, 600)
    g.setColorAt(0, QColor(196, 128, 40)); g.setColorAt(1, QColor(90, 30, 30))
    p.fillRect(art.rect(), g)
    r = QRadialGradient(420, 180, 260)
    r.setColorAt(0, QColor(250, 220, 150)); r.setColorAt(1, QColor(250, 220, 150, 0))
    p.fillRect(art.rect(), r)
owner.album_art = art
owner.timed_lyrics = [(0., 'The room is filled with music'), (3., 'A quiet light along the edge'), (6., 'But on the way home'), (9., 'You kept looking at your phone'), (12., 'And the night moves on')]
owner.media_position_seconds = 6.4
window = MusicSurfaceWindow(owner)
window.resize(960, 540)
window._sync_owned_playback_mode()
window.set_content_layout('lyrics', animate=False)
window._layout_animation.stop()
window._finish_content_layout()
window._cover_layout_from = window._cover_layout_to = float(window._no_lyrics_cover_target())
window._reset_control_idle()

class PaintStats(QObject):
    def __init__(self):
        super().__init__()
        self.frames = deque()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            now = time.monotonic()
            self.frames.append(now)
            while self.frames and self.frames[0] < now - 1.:
                self.frames.popleft()
        return False

stats = PaintStats()
window.installEventFilter(stats)
mode = '不降频' if args.throttle <= 0 else f'降频({1 / args.throttle:.0f}Hz)'
def update_title():
    now = time.monotonic()
    while stats.frames and stats.frames[0] < now - 1.:
        stats.frames.popleft()
    frames = list(stats.frames)
    gaps = [b - a for a, b in zip(frames, frames[1:])]
    window.setWindowTitle(f'GluSpot 清透玻璃 · {mode} · {len(frames)} fps · 最长间隔 {max(gaps, default=0.) * 1000:.1f} ms')
title_timer = QTimer()
title_timer.timeout.connect(update_title)
title_timer.start(250)
update_title()
window.show()

if args.auto_press:
    phase = [0]
    pressed_point = [None]
    keys = ('playback_mode', 'media:toggle_play_pause', 'favorite')
    def auto_press():
        window._reset_control_idle()
        if phase[0] % 2 == 0:
            window._sync_window_control_rects()
            key = keys[(phase[0] // 2) % len(keys)]
            hits = (window._song_collection_rects(window.rect()) if key == 'favorite' else
                    window._media_button_rects if key.startswith('media:') else window._button_rects)
            key = key.split(':', 1)[-1]
            if key in hits:
                point = hits[key].center().toPoint()
                QTest.mouseMove(window, point)
                QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=point)
                pressed_point[0] = point
        elif pressed_point[0] is not None:
            QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=pressed_point[0])
            pressed_point[0] = None
        phase[0] += 1
    auto_timer = QTimer()
    auto_timer.timeout.connect(auto_press)
    auto_timer.start(800)
if args.seconds > 0:
    QTimer.singleShot(round(args.seconds * 1000), app.exit)
try:
    app.exec()
finally:
    window.shutdown()
    owner.close()
    sandbox.cleanup()
