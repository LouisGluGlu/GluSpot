"""Opt-in, bounded Qt callback probe; --live wraps the normal app (parent only).

Fixture example: QT_QPA_PLATFORM=offscreen python perf_probe.py --repo PATH --output before.json
PC after restart: python perf_probe.py --repo E:\\path\\checkout --live --output pc-after.json
The live form starts GluSpot normally; open its music surface and play >=8 seconds.
It neither launches nor restarts NetEase. No settings/profile changes in live mode.
Callback timings are inclusive and are not compositor FPS. No credentials recorded.
"""
import argparse
from collections import defaultdict
from contextlib import ExitStack
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
from unittest.mock import patch

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--repo', type=Path, default=Path('/workspace/gluspot/grok-integrate4'))
p.add_argument('--output', type=Path, required=True)
p.add_argument('--live', action='store_true')
p.add_argument('--size', default='433x550')
p.add_argument('--warmup', type=float, default=3.)
p.add_argument('--seconds', type=float, default=5.)
p.add_argument('--refresh', type=int, default=165, help='Fixture island display cap; real Qt surface screen remains reported')
p.add_argument('--hidden-controls', action='store_true', help='Fixture idle chrome rather than permanently revealed chrome')
a, app_args = p.parse_known_args()
if a.seconds < 3:
    p.error('--seconds must be >=3')
a.repo = a.repo.resolve()
sys.path[:0] = [str(a.repo), str(a.repo / 'tests')]
os.chdir(a.repo)
with ExitStack() as stack:
    if not a.live:
        temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='gluspot-t168-probe-')))
        for key in ('APPDATA', 'LOCALAPPDATA', 'GLUSPOT_LYRICS_CACHE_DIR', 'GLUSPOT_ALBUM_ART_CACHE_DIR'):
            os.environ[key] = str(temporary / key)
        import local.settings_store as store
        original = store.SettingsManager.__init__
        def private(self, *, settings_path=None):
            original(self, settings_path=settings_path or str(temporary / 'settings.json'))
        stack.enter_context(patch.object(store.SettingsManager, '__init__', private))
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    from local.island_stack_visual import IslandStackVisual
    from local.music_surface_window import MusicSurfaceWindow
    from _demos.narrow3.narrow_window import NarrowStudyWindow
    instrumentation = stack.enter_context(ExitStack())
    samples = defaultdict(list)
    labels = []
    objects = {}
    phase = {'start': None, 'cpu': None, 'done': False, 'candidate': None}
    initial = {}
    def counters(w):
        d = w.diagnostic_snapshot() if hasattr(w, 'diagnostic_snapshot') else {}
        return {k: d.get(k) for k in ('paints', 'samples', 'commits', 'veil_prepares', 'lyric_layer_builds')}
    def observe(cls, method, label):
        labels.append(label)
        fn = getattr(cls, method)
        def wrapped(self, *args, **kwargs):
            begun = time.perf_counter()
            objects[label.split('.')[0]] = self
            try:
                return fn(self, *args, **kwargs)
            finally:
                if phase['start'] is not None and not phase['done']:
                    samples[label].append((time.perf_counter() - begun) * 1000.)
        instrumentation.enter_context(patch.object(cls, method, wrapped))
    for cls, prefix, methods in (
        (IslandStackVisual, 'island', ('_tick', 'paintEvent', 'update')),
        (MusicSurfaceWindow, 'surface', ('_on_animation_tick', 'paintEvent', 'update')),
        (NarrowStudyWindow, 'narrow', ('tick', 'paintEvent', 'update', '_refresh_chrome_backdrop', 'refresh_veil', 'refresh_background_veil'))):
        for method in methods:
            observe(cls, method, prefix + '.' + method)
    def distribution(v):
        v = sorted(v)
        return dict(count=len(v), p50=round(v[len(v)//2], 3) if v else None,
                    p95=round(v[min(len(v)-1, int(len(v)*.95))], 3) if v else None,
                    max=round(max(v), 3) if v else None, total=round(sum(v), 3))
    def timers(w):
        return {name: dict(active=t.isActive(), interval_ms=t.interval()) for name in ('timer', '_timer', 'study_timer')
                if (t := getattr(w, name, None)) is not None}
    def poll():
        if phase['done']:
            return
        windows = [w for w in QApplication.topLevelWidgets() if isinstance(w, MusicSurfaceWindow)
                   and w.isVisible() and not w.isMinimized() and getattr(w.owner, 'media_is_playing', False)]
        if not windows:
            phase['candidate'] = None
            phase['start'] = None
            samples.clear()
            return
        w = windows[0]
        now = time.perf_counter()
        if phase['candidate'] is None:
            phase['candidate'] = now
        if phase['start'] is None and now - phase['candidate'] >= a.warmup:
            samples.clear()
            initial.update(counters(w))
            phase.update(start=now, cpu=time.process_time())
        if phase['start'] is None or now - phase['start'] < a.seconds:
            return
        phase['done'] = True
        elapsed = now - phase['start']
        counts = counters(w)
        result = dict(platform=QApplication.platformName(), live=a.live, hidden_controls=a.hidden_controls if not a.live else None,
                      size=[w.width(), w.height()], dpr=w.devicePixelRatioF(),
                      screen_refresh_hz=w.screen().refreshRate(), fixture_island_refresh_cap=None if a.live else a.refresh,
                      seconds=elapsed, cpu_seconds=time.process_time()-phase['cpu'],
                      cpu_percent_one_core=100*(time.process_time()-phase['cpu'])/elapsed,
                      timers={'island': timers(w.owner), 'surface': timers(w)},
                      callbacks={k: distribution(samples[k]) for k in labels},
                      counters_delta={k: counts[k]-initial[k] if counts[k] is not None and initial[k] is not None else None for k in counts},
                      resize_frozen=getattr(w, '_lyric_resize_frozen', None),
                      resize_skeleton=getattr(w, '_lyric_resize_skeleton', None))
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2), flush=True)
        poller.stop()
        # Restore instrumented methods immediately after the bounded window.
        slots = [(w.owner, 'timer', '_tick'), (w, '_timer', '_on_animation_tick'),
                 (w, 'study_timer', 'tick')]
        connected = [(obj, timer_name, method, getattr(obj, method))
                     for obj, timer_name, method in slots
                     if hasattr(obj, timer_name) and hasattr(obj, method)]
        for obj, timer_name, method, callback in connected:
            getattr(obj, timer_name).timeout.disconnect(callback)
        instrumentation.close()
        for obj, timer_name, method, callback in connected:
            getattr(obj, timer_name).timeout.connect(getattr(obj, method))
        if not a.live:
            loop.quit()
    poller = None
    def start_polling():
        global poller
        poller = QTimer(QApplication.instance())
        poller.timeout.connect(poll)
        poller.start(100)
    if a.live:
        # Normal bootstrap creates QApplication, including when --narrow-live
        # selects the attach-only view. A timer started before that has no
        # event dispatcher and silently never samples.
        import local.app as app_module
        bootstrap = app_module.bootstrap
        def observed_bootstrap(*args, **kwargs):
            runtime = bootstrap(*args, **kwargs)
            start_polling()
            return runtime
        stack.enter_context(patch.object(app_module, 'bootstrap', observed_bootstrap))
        sys.argv = [str(a.repo / 'local/main.py'), *app_args]
        runpy.run_path(str(a.repo / 'local/main.py'), run_name='__main__')
    else:
        from api.models import IslandVisualData
        from api.providers import EmptyIslandDataProvider
        from tests.test_narrow_v4 import lyric_track
        app = QApplication.instance() or QApplication([])
        start_polling()
        stack.enter_context(patch.object(IslandStackVisual, '_display_refresh_rate_fps_cap', return_value=a.refresh))
        track = lyric_track(is_playing=True, position_seconds=35.)
        provider = EmptyIslandDataProvider(IslandVisualData(track, (), ()))
        owner = IslandStackVisual(data_provider=provider)
        owner._apply_visual_data(provider.get_visual_data())
        owner.settings_getter = lambda key, default=None: 'lyrics' if key == 'v2_music_content_layout' else default
        owner.animation_fps = owner.lyric_animation_fps = 165
        owner.timer.start(owner._timer_interval_ms())
        owner.album_art = QImage(256, 256, QImage.Format.Format_RGB32)
        owner.album_art.fill(QColor('#2b628f'))
        w = NarrowStudyWindow(owner, 'B', 'left', live=True)
        owner.music_fullscreen_overlay = w
        w.resize(*map(int, a.size.split('x')))
        w.show()
        w._pointer_hover = not a.hidden_controls
        w._control_last_motion_at = time.monotonic() + (0 if a.hidden_controls else 1000)
        loop = QEventLoop()
        QTimer.singleShot(int((a.warmup+a.seconds+15)*1000), loop.quit)
        loop.exec()
        w.shutdown()
        w.close()
        owner._application_quitting = True
        owner.timer.stop()
        owner.close()
        app.processEvents()
        if not phase['done']:
            raise RuntimeError('No completed playing music-surface sample')
