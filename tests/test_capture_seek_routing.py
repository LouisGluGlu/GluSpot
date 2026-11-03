"""Provider decisions must stay visible and use the launched CEF session."""
import asyncio
import subprocess
import sys
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from api.capture.netease import NetEaseInstallation, NetEaseLaunch
from api.capture.runtime import CaptureRuntime
from api.capture.seek import record_seek_decision
from api.providers import PlaybackSourceProvider, WindowsMediaProvider
from tests.capture_async import CaptureAsyncTestCase
from tests.capture_cdp_fake import FakeCdpServer
from tests.test_capture_cef import Clock, FakeCdp
from tests.test_capture_smtc import Session, matches, visual


class SeekDecisionLogTests(unittest.TestCase):
    def test_normal_stderr_receives_decisions_without_global_logging_setup(self):
        result = subprocess.run([
            sys.executable, '-c',
            "from api.capture.seek import record_seek_decision; "
            "record_seek_decision('runtime-result', 'verified', target=42, "
            "backend='cef', port=8178, error_seconds=.007)"
        ], capture_output=True, text=True, timeout=10, check=True)
        self.assertIn('GluSpot.capture.seek stage=runtime-result reason=verified', result.stderr)
        self.assertIn('backend=cef target_seconds=42 port=8178 error_seconds=0.007', result.stderr)

    def test_log_rejects_unrecognized_strings_and_non_numeric_payloads(self):
        with self.assertNoLogs('GluSpot.capture.seek'):
            record_seek_decision('arbitrary input', 'verified')
            record_seek_decision('provider', 'arbitrary input')
        with self.assertLogs('GluSpot.capture.seek', level='INFO') as records:
            record_seek_decision('provider', 'queued', target='private payload',
                                 port='private payload', backend='private payload',
                                 error_seconds=float('nan'))
        self.assertNotIn('private payload', records.output[0])
        self.assertNotIn('nan', records.output[0])

    def test_owned_priority_and_disabled_system_have_explicit_decisions(self):
        provider = PlaybackSourceProvider.__new__(PlaybackSourceProvider)
        settings = {'v2_owned_playback_enabled': True, 'v2_system_capture_enabled': True}
        provider.settings_getter = lambda key, default=None: settings.get(key, default)
        provider.owned = SimpleNamespace(track=object(), seek_playback_position=Mock(return_value=True))
        provider.system = SimpleNamespace(seek_playback_position=Mock(return_value=True))
        with self.assertLogs('GluSpot.capture.seek') as records:
            self.assertTrue(provider.seek_playback_position(42, 'painted-track'))
        self.assertIn('reason=owned-active', records.output[0])
        provider.owned.seek_playback_position.assert_called_once_with(42, 'painted-track')
        provider.system.seek_playback_position.assert_not_called()
        settings['v2_owned_playback_enabled'] = False
        settings['v2_system_capture_enabled'] = False
        with self.assertLogs('GluSpot.capture.seek') as records:
            self.assertFalse(provider.seek_playback_position(42, 'painted-track'))
        self.assertIn('reason=system-disabled', records.output[0])


class CaptureSeekRoutingTests(CaptureAsyncTestCase):
    def provider(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        settings = {'v2_capture_position_enabled': True, 'netease.cef.enabled': True,
                    'netease.transport': 'relaunch'}
        provider.settings_getter = lambda key, default=None: settings.get(key, default)
        provider._last_data = visual()
        provider._loop = asyncio.get_running_loop()
        provider._thread = threading.current_thread()
        provider._request_immediate_media_refresh = Mock()
        provider._get_session = AsyncMock(return_value=Session())
        provider._seek_session_matches_track = AsyncMock(return_value=True)
        return provider

    async def test_busy_uncertain_and_stale_gesture_are_logged_without_writes(self):
        provider = self.provider()
        runtime = provider._capture_runtime = SimpleNamespace(seek=AsyncMock())
        for busy, uncertain, reason in ((True, False, 'busy'),
                                         (False, True, 'previous-request-uncertain')):
            provider._capture_executor = SimpleNamespace(busy=busy, uncertain=uncertain)
            with self.assertLogs('GluSpot.capture.seek') as records:
                self.assertFalse(await asyncio.to_thread(provider.seek_playback_position, 42, 'song'))
            self.assertIn('reason=' + reason, records.output[0])
        provider._capture_executor = SimpleNamespace(busy=False, uncertain=False)
        with self.assertLogs('GluSpot.capture.seek') as records:
            self.assertFalse(await asyncio.to_thread(provider.seek_playback_position, 42, 'old-track'))
        self.assertIn('reason=expected-track-mismatch', records.output[0])
        with self.assertLogs('GluSpot.capture.seek') as records:
            self.assertFalse(provider.seek_playback_position(42, 'song'))
        self.assertIn('reason=owning-thread', records.output[0])
        runtime.seek.assert_not_called()

    async def test_guard_checks_track_after_awaiting_selected_session(self):
        provider = self.provider()
        async def selecting():
            provider._last_data = replace(provider._last_data,
                                          track=replace(provider._last_data.track, track_id='replacement'))
            return Session()
        provider._get_session.side_effect = selecting
        async def guarded_seek(_target, _track_id, guard):
            return await guard()
        provider._capture_runtime = SimpleNamespace(seek=AsyncMock(side_effect=guarded_seek))
        with self.assertLogs('GluSpot.capture.seek') as records:
            self.assertFalse(await asyncio.to_thread(provider.seek_playback_position, 42, 'song'))
        self.assertTrue(any('stage=provider-guard reason=track-changed' in line for line in records.output))
        provider._seek_session_matches_track.assert_not_awaited()

    async def test_guard_checks_track_after_awaiting_metadata_match(self):
        provider = self.provider()
        async def matching(*_args):
            provider._last_data = replace(provider._last_data,
                                          track=replace(provider._last_data.track, track_id='replacement'))
            return True
        provider._seek_session_matches_track.side_effect = matching
        async def guarded_seek(_target, _track_id, guard):
            return await guard()
        provider._capture_runtime = SimpleNamespace(seek=AsyncMock(side_effect=guarded_seek))
        with self.assertLogs('GluSpot.capture.seek') as records:
            self.assertFalse(await asyncio.to_thread(provider.seek_playback_position, 42, 'song'))
        self.assertTrue(any('stage=provider-guard reason=track-changed' in line for line in records.output))

    async def test_relaunch_port_reaches_provider_runtime_and_physical_seek(self):
        provider = self.provider()
        provider._last_data = None  # No pre-relaunch track restoration in this fixture.
        installation = NetEaseInstallation('/fixture/cloudmusic.exe', '3.1.42')
        async with FakeCdpServer() as server:
            server.sample['playing'] = False
            provider._netease_locator = SimpleNamespace(
                locate=lambda: installation,
                launch=AsyncMock(return_value=NetEaseLaunch(server.port, installation, 99)))
            runtime = None
            cdp = await provider._ensure_netease_cdp()
            try:
                self.assertTrue(await cdp.wait_ready(timeout=2))
                provider._last_data = visual()
                await provider._bind_capture_session(Session(), provider._last_data.track)
                runtime = provider._capture_runtime
                self.assertIs(runtime.cef_session, cdp)
                self.assertEqual(runtime.cef_session.port, server.port)
                with self.assertLogs('GluSpot.capture.seek') as records:
                    self.assertTrue(await asyncio.to_thread(provider.seek_playback_position, 42, 'song'))
                self.assertEqual(server.sample['position'], 42)
                self.assertTrue(any('backend=cef' in line and 'port=' + str(server.port) in line
                                    for line in records.output))
                self.assertTrue(any('reason=verified' in line and 'error_seconds=' in line
                                    for line in records.output))
                self.assertEqual(runtime.executor.last_result.backend, 'cef')
            finally:
                if runtime is not None:
                    await runtime.close()
                worker = getattr(provider, '_capture_uia_worker', None)
                if worker is not None:
                    worker.close()
                await provider._close_netease_cdp()

    async def test_cancelled_preflight_does_not_disable_the_next_cef_seek(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            self.assertFalse(await runtime.seek(42, 'song', AsyncMock(return_value=False)))
            self.assertEqual(runtime.executor.last_result.reason, 'identity-changed')
            self.assertFalse(cdp.writes)
            self.assertTrue(runtime._cef_seek_enabled)
            self.assertTrue(await runtime.seek(42, 'song', AsyncMock(return_value=True)))
            self.assertEqual(len(cdp.writes), 1)
            self.assertEqual(runtime.executor.last_result.backend, 'cef')
        finally:
            await runtime.close()

    async def test_unverified_cef_attempt_allows_next_gesture_without_rebind(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        cdp.move = False
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            self.assertFalse(await runtime.seek(42, 'song', AsyncMock(return_value=True)))
            self.assertEqual(runtime.executor.last_result.reason, 'unverified')
            self.assertEqual(len(cdp.writes), 1)
            self.assertTrue(runtime._cef_seek_enabled)
            # Even a paused clock with no progress after the failed request
            # can preflight the next gesture; only its readback needs an edge.
            cdp.move = True
            self.assertTrue(await runtime.seek(60, 'song', AsyncMock(return_value=True)))
            self.assertEqual(len(cdp.writes), 2)
            self.assertEqual(runtime.executor.last_result.backend, 'cef')
        finally:
            await runtime.close()
