"""Visible Windows Qt callback timings, without captures in the sample period.

This is NOT compositor FPS or physical input latency. Native paint/timer
callbacks are timed independently; their inclusive distributions must not be
added. Run serially, with the same foreground/occlusion conditions for pairs.
The fixtures have no accounts, network, settings writes or actual audio.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch


class _PixelDiagnosticTrace:
    """Opt-in instrumentation; never use these timings as a formal pair."""
    MAX_RECORDS = 12000

    def __init__(self):
        self.lock = threading.Lock()
        self.records = {}
        self.managers = {}
        self.tasks = {}
        self.request_context = {}
        self.awaiting_paint = set()
        self.resource_high_water = {}
        self.resource_samples = 0
        self.surface_id = None
        self.omitted = 0

    def _record(self, key, **fields):
        with self.lock:
            record = self.records.get(key)
            if record is None:
                if len(self.records) >= self.MAX_RECORDS:
                    self.omitted += 1
                    return
                record = {'service': key[0], 'serial': key[1]}
                self.records[key] = record
            record.update(fields)

    def _sample_resources(self, manager):
        values = {'active': int(manager.active is not None), 'pending': len(manager.pending),
                  'input_bytes': manager.input_bytes, 'cache_bytes': manager.cache_bytes,
                  'owners': len(manager.owners), 'roles': len(manager.latest)}
        with self.lock:
            self.resource_samples += 1
            for name, value in values.items():
                self.resource_high_water[name] = max(value, self.resource_high_water.get(name, 0))

    def install(self, guards):
        # Keep the decorated _completed slot and its existing queued signal
        # connection intact. _deliver is called inside that original slot.
        from local.liquid_glass_tasks import GlassPixelTasks
        from local.music_tasks import MusicTasks
        request = GlassPixelTasks.request
        deliver = GlassPixelTasks._deliver
        submit = MusicTasks.submit

        def observed_request(manager, owner, role, *args, **kwargs):
            service = self.managers.setdefault(id(manager), len(self.managers) + 1)
            self.tasks[id(manager.tasks)] = service
            began, before = time.perf_counter(), manager._serial
            previous = self.request_context.get(service)
            self.request_context[service] = began
            try:
                return request(manager, owner, role, *args, **kwargs)
            finally:
                self.request_context[service] = previous
                self._sample_resources(manager)
                if manager._serial > before:
                    key = (service, manager._serial)
                    self._record(key, request_at=began, role=str(role[0] if isinstance(role, tuple) else role),
                                 owner_surface=id(owner.window()),
                                 request_return_at=time.perf_counter())

        def observed_submit(worker, token, action):
            service = self.tasks.get(id(worker))
            if type(token) is not int or service is None:
                return submit(worker, token, action)
            key, began = (service, token), time.perf_counter()
            with self.lock:
                known = self.records.get(key, {}).get('request_at')
            self._record(key, submitted_at=began,
                         request_at=known if known is not None else self.request_context.get(service))

            def timed_action():
                started, value = time.perf_counter(), None
                self._record(key, worker_started_at=started)
                try:
                    value = action()
                    return value
                finally:
                    ended = time.perf_counter()
                    # The checked pixel work contract returns (QImage,
                    # pure-render seconds). Do not retain the image here.
                    pure_ms = (float(value[1]) * 1000 if isinstance(value, tuple)
                               and len(value) == 2 and isinstance(value[1], (int, float)) else None)
                    self._record(key, worker_ended_at=ended,
                                 worker_action_ms=(ended-started)*1000,
                                 pure_pixel_render_ms=pure_ms)

            accepted = submit(worker, token, timed_action)
            self._record(key, submit_accepted=bool(accepted))
            return accepted

        def observed_deliver(manager, work, value, error):
            service = self.managers.get(id(manager))
            key = (service, work.serial)
            self._record(key, gui_deliver_at=time.perf_counter(), worker_error=error is not None)
            try:
                return deliver(manager, work, value, error)
            finally:
                ended = time.perf_counter()
                self._sample_resources(manager)
                ready = manager.cache.get(work.slot)
                accepted = ready is not None and ready.serial == work.serial
                self._record(key, gui_deliver_ended_at=ended, accepted=accepted,
                             accepted_at=ended if accepted else None)
                if accepted:
                    with self.lock:
                        if key in self.records:
                            self.awaiting_paint.add(key)

        guards.enter_context(patch.object(GlassPixelTasks, 'request', observed_request))
        guards.enter_context(patch.object(GlassPixelTasks, '_deliver', observed_deliver))
        guards.enter_context(patch.object(MusicTasks, 'submit', observed_submit))

    def paint_completed(self, started, ended):
        with self.lock:
            for key in tuple(self.awaiting_paint):
                record = self.records[key]
                if record.get('owner_surface') == self.surface_id and record['accepted_at'] <= started:
                    record['first_following_surface_paint_at'] = started
                    record['first_following_surface_paint_ms'] = (ended-started)*1000
                    self.awaiting_paint.discard(key)

    def result(self, origin, distribution):
        with self.lock:
            records = [dict(record) for record in self.records.values()]
            high_water = dict(self.resource_high_water)
            resource_samples = self.resource_samples
        stages = {
            'gui_request_to_submit_ms': ('request_at', 'submitted_at'),
            'submit_to_worker_start_ms': ('submitted_at', 'worker_started_at'),
            'worker_end_to_gui_deliver_ms': ('worker_ended_at', 'gui_deliver_at'),
            'gui_delivery_ms': ('gui_deliver_at', 'gui_deliver_ended_at'),
            'accepted_to_first_following_surface_paint_ms': ('accepted_at', 'first_following_surface_paint_at'),
        }
        samples = {name: [] for name in (*stages, 'pure_pixel_render_ms', 'worker_action_ms')}
        for record in records:
            record.pop('owner_surface', None)
            for name, (first, last) in stages.items():
                if record.get(first) is not None and record.get(last) is not None:
                    record[name] = (record[last]-record[first])*1000
                    samples[name].append(record[name])
            for name in ('pure_pixel_render_ms', 'worker_action_ms'):
                if record.get(name) is not None:
                    samples[name].append(record[name])
            for name in tuple(record):
                if name.endswith('_at') and record[name] is not None:
                    record[name] = (record[name]-origin)*1000
        return {
            'scope': 'Instrumented diagnostic only; do not compare with uninstrumented formal timings. '
                     'Request-to-submit includes application pending time. Worker action wraps the original '
                     'pure pixel work; pure_pixel_render_ms uses its own checked duration. The first following '
                     'surface paint is a Qt callback, not proof of sampling that result or compositor presentation.',
            'record_limit': self.MAX_RECORDS, 'omitted_record_updates': self.omitted,
            'resource_high_water': high_water, 'resource_sample_count': resource_samples,
            'resource_sample_scope': 'GUI request returns and _deliver exits only; sampled peaks, '
                                     'not continuous RSS or an independent timer. roles counts current '
                                     '(owner, role) request slots, not unique role names.',
            'timestamp_unit': 'milliseconds relative to window show request',
            'stages': {name: distribution(values) for name, values in samples.items()},
            'serials': records,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', default='windows', choices=('windows',))
    parser.add_argument('--scenario', default='long-song', choices=('long-song', 'unit-slow'))
    parser.add_argument('--size', default='960x540')
    parser.add_argument('--expect-dpr', type=float, required=True)
    parser.add_argument('--production-store', action='store_true')
    parser.add_argument('--synchronous-materials', action='store_true')
    parser.add_argument('--diagnostic', action='store_true',
                        help='Instrument pixel serial stages; results are diagnostic, not formal comparison data')
    parser.add_argument('--cold-seconds', type=float, default=2.)
    parser.add_argument('--stable-seconds', type=float, default=6.)
    args = parser.parse_args()
    if args.cold_seconds < 1 or args.stable_seconds < 4:
        raise ValueError('At least one cold and four stable seconds required')
    os.environ['QT_QPA_PLATFORM'] = args.platform
    source = args.source_root.resolve()
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / 'tests'))
    from benchmark_music_surface import SyntheticOwner
    from perf_music_surface import (cache_snapshot, distribution, process_resources,
                                    source_manifest, validate_prepared)
    from PySide6.QtCore import QEventLoop, QTimer, qVersion
    from PySide6.QtWidgets import QApplication
    from local.music_surface_window import MusicSurfaceWindow

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    owner = SyntheticOwner(args.scenario)
    owner.media_epoch = time.monotonic()
    store = None
    preparation = {'mode': 'legacy-synchronous-accessor'}
    if args.production_store:
        from local.music_lyric_preparation_store import LyricPresentationStore
        store = LyricPresentationStore(owner)
        owner._lyric_presentation_store = store
        began = time.perf_counter()
        prepared = store.get()
        while prepared is None and time.perf_counter() - began < 10.:
            app.processEvents()
            time.sleep(.001)
            prepared = store.get()
        if not validate_prepared(prepared, owner.timed_lyrics, getattr(owner, 'timed_lyric_units', ())):
            raise AssertionError('Complete source rows and real anchors required before showing')
        preparation = {'mode': 'production-worker-store',
                       'submit_to_ready_ms': (time.perf_counter()-began)*1000,
                       'worker_ms': store.prepare_seconds*1000}

    paints, ticks, intervals = [], [], []
    diagnostic = _PixelDiagnosticTrace() if args.diagnostic else None
    shown_at = [None]
    last_paint = [None]
    class ObservedSurface(MusicSurfaceWindow):
        def paintEvent(self, event):
            start = time.perf_counter()
            super().paintEvent(event)
            if shown_at[0] is not None:
                elapsed = start - shown_at[0]
                paints.append((elapsed, (time.perf_counter()-start)*1000))
                if last_paint[0] is not None:
                    intervals.append((elapsed, (start-last_paint[0])*1000))
                last_paint[0] = start
            if diagnostic is not None:
                diagnostic.paint_completed(start, time.perf_counter())

        def _on_animation_tick(self):
            start = time.perf_counter()
            self._control_last_motion_at = time.monotonic()
            super()._on_animation_tick()
            if shown_at[0] is not None:
                ticks.append((start-shown_at[0], (time.perf_counter()-start)*1000))

    before = source_manifest()
    with ExitStack() as guards:
        if diagnostic is not None:
            diagnostic.install(guards)
        if args.synchronous_materials:
            import local.music_surface_window as surface_module
            import local.liquid_glass as glass
            guards.enter_context(patch.object(surface_module, 'live_glass_paint', return_value=False))
            guards.enter_context(patch.object(glass, 'live_glass_paint', return_value=False))
        window = ObservedSurface(owner)
        if diagnostic is not None:
            diagnostic.surface_id = id(window)
        width, height = map(int, args.size.split('x'))
        window.resize(width, height)
        # Pin pairs to the same native origin. Large logical DPR2 windows can
        # exceed this monitor; record the exposed intersection explicitly.
        area = window.screen().availableGeometry()
        window.move(area.topLeft())
        window._fullscreen = True
        window._reset_control_idle(now=time.monotonic())
        if abs(window.devicePixelRatioF()-args.expect_dpr) > .05:
            raise AssertionError('Actual native DPR differs from requested DPR')
        result = {'scope': __doc__, 'qt': qVersion(), 'platform': app.platformName(),
                  'scenario': args.scenario, 'requested_size': [width, height],
                  'actual_logical_size': [window.width(), window.height()],
                  'actual_dpr': window.devicePixelRatioF(), 'preparation': preparation,
                  'synchronous_materials': args.synchronous_materials,
                  'diagnostic': args.diagnostic,
                  'resources_before': process_resources(), 'source_before': before}
        owner.media_epoch = time.monotonic()
        shown_at[0] = time.perf_counter()
        window.show()
        window.raise_()
        window.activateWindow()
        loop = QEventLoop()
        QTimer.singleShot(round((args.cold_seconds+args.stable_seconds)*1000), loop.quit)
        loop.exec()
        measured_seconds = time.perf_counter()-shown_at[0]
        result['observed_seconds'] = measured_seconds
        result['visible_at_finish'] = window.isVisible() and not window.isMinimized()
        result['first_paint_ms'] = paints[0][1] if paints else None
        result['screen_refresh_rate'] = window.screen().refreshRate()
        result['screen_available_logical_size'] = [window.screen().availableGeometry().width(),
                                                   window.screen().availableGeometry().height()]
        exposed = window.geometry().intersected(area)
        result['available_intersection_size'] = [exposed.width(), exposed.height()]
        result['wholly_inside_available_geometry'] = area.contains(window.geometry())
        result['phases'] = {}
        for label, low, high in (('cold', 0., args.cold_seconds),
                                 ('stable', args.cold_seconds, measured_seconds)):
            result['phases'][label] = {name: distribution([value for at, value in samples if low <= at < high])
                                      for name, samples in (('paint_ms', paints), ('tick_ms', ticks),
                                                            ('paint_callback_interval_ms', intervals))}
        result['caches'] = cache_snapshot(window)
        result['resources_live'] = process_resources()
        try:
            from local.liquid_glass_tasks import glass_pixel_tasks
            service = glass_pixel_tasks(create=False)
        except ImportError:
            service = None
        if service is not None:
            result['pixel_tasks'] = {name: getattr(service, name) for name in (
                'submitted', 'hits', 'misses', 'discarded', 'rejected', 'cache_bytes', 'input_bytes')}
            result['pixel_tasks'].update(owners=len(service.owners), slots=len(service.latest),
                                         pending=len(service.pending), active=service.active is not None)
        if diagnostic is not None:
            result['pixel_diagnostic'] = diagnostic.result(shown_at[0], distribution)
        window.shutdown()
        if store is not None:
            store.close()
        if service is not None:
            service.close()
        owner.close()
        window.deleteLater()
        owner.deleteLater()
        retired_at = time.perf_counter()
        while process_resources()['music_worker_threads'] and time.perf_counter()-retired_at < 2.:
            app.processEvents()
            time.sleep(.002)
        result['resources_after_shutdown'] = process_resources()
        result['worker_retirement_ms'] = (time.perf_counter()-retired_at)*1000
        result['source_after'] = source_manifest()
        result['application_source_unchanged'] = before == result['source_after']
    if not paints or not result['visible_at_finish'] or not result['application_source_unchanged']:
        raise AssertionError('Missing native paint, invisible fixture or changed application source')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['phases'], ensure_ascii=False))


if __name__ == '__main__':
    main()
