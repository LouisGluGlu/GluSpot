"""Probe a running NetEase Windows media session without opening a Qt window.

Run from the repository root: python tools/capture_device_probe.py
This changes the player's position and attempts to restore it before exiting.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.capture import capture_enabled
from api.capture.capabilities import capability_for
from api.capture.runtime import CaptureRuntime
from api.capture.smtc import seconds
from api.models import IslandVisualData, TrackData


# Deliberately independent of the application's persistent settings manager.
_SETTINGS = {"v2_capture_position_enabled": True}


def settings_getter(key, default=None):
    return _SETTINGS.get(key, default)


def metadata_key(properties):
    return tuple(str(getattr(properties, name, '') or '') for name in
                 ('title', 'artist', 'album_title'))


async def run_probe(manager, report):
    if not capture_enabled(settings_getter):
        raise RuntimeError('Capture position is not enabled in probe settings')
    sessions = [session for session in manager.get_sessions()
                if capability_for(str(session.source_app_user_model_id)).service == 'netease']
    if len(sessions) != 1:
        raise RuntimeError(f'Expected one NetEase SMTC session, found {len(sessions)}')
    session = sessions[0]
    app_id = str(session.source_app_user_model_id)
    properties = await session.try_get_media_properties_async()
    key = metadata_key(properties)
    if not key[0]:
        raise RuntimeError('NetEase session has no track title')
    timeline = session.get_timeline_properties()
    duration = seconds(timeline.end_time) - seconds(timeline.start_time)
    if duration <= 0 or seconds(timeline.start_time) != 0:
        raise RuntimeError('Probe requires a known duration and zero-based timeline')
    playback = session.get_playback_info()
    playing = getattr(playback.playback_status, 'value', playback.playback_status) == 4
    track = TrackData(title=key[0], artist=key[1], lyrics=(), album_color='',
                      secondary_color='', audio_frames=(), source_app_id=app_id,
                      track_id='probe:' + repr(key), duration_seconds=duration,
                      is_playing=playing)
    data = IslandVisualData(track=track, messages=(), plugins=())
    report.update(session=app_id, title=key[0], artist=key[1], duration_seconds=duration,
                  initially_playing=playing)

    async def matches(candidate):
        if str(candidate.source_app_user_model_id) != app_id:
            return False
        if not any(str(item.source_app_user_model_id) == app_id for item in manager.get_sessions()):
            return False
        return metadata_key(await candidate.try_get_media_properties_async()) == key

    async def guard():
        return await matches(session)

    runtime = CaptureRuntime()
    original = None
    mutated = False
    try:
        await runtime.bind(session, track, matches)
        initial = runtime.project(data).track
        if initial.position_source != 'capture-model':
            raise RuntimeError('No valid initial capture position')
        original = initial.position_seconds
        report['original_position_seconds'] = original
        start = time.monotonic()
        previous = None
        for index in range(21):
            await asyncio.sleep(max(0, start + index * 0.5 - time.monotonic()))
            now = time.monotonic()
            current = runtime.project(data, now=now).track
            if current.position_source != 'capture-model':
                raise RuntimeError('Capture projection became unavailable')
            sample = {'elapsed_seconds': now - start, 'position_seconds': current.position_seconds,
                      'wall_delta_seconds': None, 'position_delta_seconds': None, 'jitter_ms': None}
            if previous is not None:
                sample['wall_delta_seconds'] = now - previous[0]
                sample['position_delta_seconds'] = current.position_seconds - previous[1]
                sample['jitter_ms'] = 1000 * (sample['position_delta_seconds'] - sample['wall_delta_seconds'])
            report['samples'].append(sample)
            previous = now, current.position_seconds
            print('sample ' + json.dumps(sample), flush=True)
        report['max_jitter_ms'] = max(abs(s['jitter_ms']) for s in report['samples'][1:])
        print(f"max jitter: {report['max_jitter_ms']:.1f} ms", flush=True)
        for requested in (30.0, 75.0, 120.0):
            target = min(duration, requested)
            started = time.monotonic()
            mutated = True
            attempts = runtime.seek_attempts
            success = await runtime.seek(target, track.track_id, guard)
            result = runtime.executor.last_result if runtime.seek_attempts > attempts else None
            await asyncio.sleep(1.5)
            backend = (runtime.source if result and result.backend == 'smtc'
                       else runtime.fallback if result and result.backend == 'uia' else None)
            sample = await backend.read() if backend is not None else None
            expected = None
            error = None
            if sample is not None:
                expected = min(duration, target + (max(0, sample.observed_at - started) * sample.rate
                                                   if sample.is_playing else 0))
                error = (sample.position_seconds - expected) * 1000
            entry = {'target_seconds': target, 'succeeded': success,
                     'backend': result.backend if result and success else None,
                     'state': runtime.executor.state.value,
                     'reason': result.reason if result else 'not-started',
                     'readback_seconds': sample.position_seconds if sample else None,
                     'expected_seconds': expected, 'error_ms': error}
            report['seeks'].append(entry)
            print('seek ' + json.dumps(entry), flush=True)
            if not success or sample is None:
                raise RuntimeError('Seek or independent readback failed')
        report['passed'] = True
    finally:
        try:
            if original is not None and mutated:
                attempts = runtime.seek_attempts
                success = await runtime.seek(original, track.track_id, guard)
                result = runtime.executor.last_result if runtime.seek_attempts > attempts else None
                report['restore'] = {'target_seconds': original, 'succeeded': success,
                                     'state': runtime.executor.state.value,
                                     'reason': result.reason if result else 'not-started'}
                print('restore ' + json.dumps(report['restore']), flush=True)
                if not success:
                    report['passed'] = False
        finally:
            await runtime.close()


def main():
    report = {'passed': False, 'samples': [], 'seeks': [], 'restore': None}
    try:
        if sys.platform != 'win32':
            raise RuntimeError('This real-device probe requires Windows')
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

        async def run():
            manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            await run_probe(manager, report)

        asyncio.run(run())
    except (Exception, KeyboardInterrupt) as exc:
        report['passed'] = False
        report['error'] = f'{type(exc).__name__}: {exc}'
    print(json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
