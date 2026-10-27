"""Grab the real MusicSurfaceWindow (production code path) for visual reference.

Usage: python _demos/narrow3/ref_real_window.py --cover PATH --out DIR [--sizes 960x540,360x640]
Isolated: HOME/APPDATA/LOCALAPPDATA point to a temp dir; no network, no audio.
"""
import argparse, os, sys, tempfile, time
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
ap = argparse.ArgumentParser()
ap.add_argument('--cover', type=Path, required=True)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--sizes', default='960x540,360x640')
ap.add_argument('--tag', default='real')
args = ap.parse_args()
_sb = tempfile.TemporaryDirectory(prefix='gluspot-ref-')
for n in ('HOME', 'APPDATA', 'LOCALAPPDATA'):
    os.environ[n] = _sb.name

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from api.online_music import OnlineSong
from local.music_surface_window import MusicSurfaceWindow
from local.music_surface_palette import artwork_surface_palette
from test_music_surface_transition import _TransitionOwner
from test_owned_playback_surface import _Provider
from test_song_collection_surface import Collection

app = QApplication.instance() or QApplication([])
art = QImage(str(args.cover)).convertToFormat(QImage.Format.Format_RGB32)
assert not art.isNull(), args.cover
LYR = ['在夜色中慢慢走', '把心事交给晚风', '那些没有说完的话', '会在星光里重逢', '让时间轻轻流过', '我们还在这条路上', '等天边亮起微光', '再一起迎接远方']
SONGS = [('夜航的信', '林间电台'), ('晚风经过', '陈小雨'), ('云与海之间', '南方来信'), ('星光落下来', '木子'),
         ('慢慢的旅程', '夏日回声'), ('下一站黎明', '白昼乐队'), ('你听见了吗', '无名旅人'), ('再见之前', '海边房间')]

def make(w, h, layout):
    owner = _TransitionOwner()
    owner.media_track_id = 'owned:netease:1'
    owner.track_title, owner.artist = SONGS[0]
    owner._song_collection_controller = Collection()
    owner._music_library_back_callback = Mock()
    pv = _Provider(); pv.fm = True
    pv.get_owned_personal_dj_enabled = lambda: True
    pv.is_owned_personal_dj_speaking = lambda: False
    pv.get_owned_playback_queue = lambda: (0, 0, tuple(OnlineSong('netease', str(i), t, artist=a) for i, (t, a) in enumerate(SONGS)))
    owner.data_provider = pv
    owner.settings_getter = lambda key, default=None: {'v2_music_glass_level': 50}.get(key, default)
    c = QColor(art.pixel(art.width() // 2, art.height() // 2))
    try:
        pal = artwork_surface_palette(art, c, c.darker(150))
        owner.album_color, owner.secondary_color = pal.primary, pal.secondary
    except Exception:
        owner.album_color, owner.secondary_color = c, c.darker(150)
    owner.album_art = art
    owner.timed_lyrics = [(i * 4., t) for i, t in enumerate(LYR)]
    owner.media_position_seconds = 5.6
    win = MusicSurfaceWindow(owner)
    win.resize(w, h); win.move(80, 80)
    win._sync_owned_playback_mode()
    win.set_content_layout(layout, animate=False)
    win._layout_animation.stop(); win._finish_content_layout()
    win._reset_control_idle()
    return win

jobs = [(tuple(map(int, s.split('x'))), lay) for s in args.sizes.split(',') for lay in ('lyrics', 'playlist')]
args.out.mkdir(parents=True, exist_ok=True)
state = {'i': 0, 'win': None}

def step():
    if state['win'] is not None:
        (w, h), lay = jobs[state['i']]
        img = state['win'].grab()
        name = f'{args.tag}_{w}x{h}_{lay}.png'
        img.save(str(args.out / name)); print('saved', name, img.width(), img.height(), flush=True)
        state['win'].hide(); state['win'].deleteLater(); state['win'] = None; state['i'] += 1
        if state['i'] >= len(jobs):
            app.quit(); return
        QTimer.singleShot(200, step); return
    (w, h), lay = jobs[state['i']]
    state['win'] = make(w, h, lay); state['win'].show()
    QTimer.singleShot(1500, step)

QTimer.singleShot(0, step)
app.exec()
