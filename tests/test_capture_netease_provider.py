"""NetEase transport-A lifecycle stays behind the provider's opt-in flags."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.providers import WindowsMediaProvider
from api.capture.netease import NetEaseInstallation, NetEaseLaunch
from api.capture.cdp import CdpSession
from tests.capture_cdp_fake import FakeCdpServer
from tests.test_capture_smtc import Session as MediaSession, visual

from tests.capture_async import CaptureAsyncTestCase


class Session:
    def __init__(self, port, **kwargs):
        self.port, self.kwargs = port, kwargs
        self.ready = False
        self._closed = False
        self.latest = None
        self.start = AsyncMock()
        self.close = AsyncMock(side_effect=self.mark_closed)

    def mark_closed(self):
        self._closed = True


class NetEaseProviderTests(CaptureAsyncTestCase):
    def provider(self, values=None):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        defaults = {'v2_capture_position_enabled': True,
                    'netease.cef.enabled': True, 'netease.transport': 'relaunch'}
        defaults.update(values or {})
        provider.settings_getter = lambda key, default=None: defaults.get(key, default)
        provider._netease_cdp_session = None
        provider._last_data = None
        provider._netease_locator = SimpleNamespace(
            locate=lambda: NetEaseInstallation('/apps/cloudmusic.exe', '3.1.42'),
            launch=AsyncMock(return_value=NetEaseLaunch(
                12345, NetEaseInstallation('/apps/cloudmusic.exe', '3.1.42'), 99)))
        return provider, defaults

    async def test_all_flags_require_explicit_opt_in(self):
        for settings in ({'v2_capture_position_enabled': False}, {'netease.cef.enabled': False},
                         {'netease.cef.enabled': 'true'}, {'netease.transport': 'off'},
                         {'netease.transport': 'pipe'}, {'netease.transport': 'port'},
                         {'v2_capture_app_profiles': {'netease': {'mode': 'off'}}}):
            provider, _ = self.provider(settings)
            with self.subTest(settings=settings), patch('api.capture.cdp.CdpSession', Session):
                self.assertIsNone(await provider._ensure_netease_cdp())
                provider._netease_locator.launch.assert_not_awaited()

    async def test_transport_launch_is_idempotent_and_uses_adapter_version(self):
        provider, _ = self.provider()
        with patch('api.capture.cdp.CdpSession', Session):
            first = await provider._ensure_netease_cdp()
            second = await provider._ensure_netease_cdp()
        self.assertIs(first, second)
        provider._netease_locator.launch.assert_awaited_once()
        self.assertEqual(provider._netease_locator.launch.await_args.kwargs, {'relaunch': True})
        self.assertEqual(first.kwargs, {'adapter': 'auto', 'version': '3.1.42'})
        first.start.assert_awaited_once()
        self.assertEqual(provider.netease_capture_health()['state'], 'connecting')

    async def test_concurrent_media_refreshes_join_one_launch(self):
        provider, _ = self.provider()
        with patch('api.capture.cdp.CdpSession', Session):
            first, second = await asyncio.gather(provider._ensure_netease_cdp(),
                                                  provider._ensure_netease_cdp())
        self.assertIs(first, second)
        provider._netease_locator.launch.assert_awaited_once()

    async def test_disable_during_launch_never_starts_a_cdp_worker(self):
        provider, values = self.provider()
        entered, release = asyncio.Event(), asyncio.Event()
        async def launch(*args, **kwargs):
            entered.set()
            await asyncio.wait_for(release.wait(), 2)
            return NetEaseLaunch(12345, NetEaseInstallation('/apps/cloudmusic.exe'), 99)
        provider._netease_locator.launch.side_effect = launch
        with patch('api.capture.cdp.CdpSession', side_effect=Session) as factory:
            starting = asyncio.create_task(provider._ensure_netease_cdp())
            await asyncio.wait_for(entered.wait(), 2)
            values['netease.cef.enabled'] = False
            closing = asyncio.create_task(provider._close_netease_cdp())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            self.assertIsNone(await starting)
            await closing
            factory.assert_not_called()
        self.assertIsNone(provider._netease_cdp_session)

    async def test_disabled_provider_does_not_allocate_transport_state(self):
        provider, _ = self.provider({'v2_capture_position_enabled': False})
        await provider._close_netease_cdp()
        self.assertFalse(hasattr(provider, '_netease_start_lock'))

    async def test_invalid_adapter_never_restarts_player(self):
        provider, _ = self.provider({'netease.adapter': 'unknown-script'})
        self.assertIsNone(await provider._ensure_netease_cdp())
        provider._netease_locator.launch.assert_not_awaited()

    async def test_failure_backoff_does_not_restart_on_each_media_refresh(self):
        provider, _ = self.provider()
        provider._netease_locator.launch.side_effect = OSError('unavailable')
        await provider._ensure_netease_cdp()
        await provider._ensure_netease_cdp()
        provider._netease_locator.launch.assert_awaited_once()

    async def test_missing_installation_keeps_requested_nonzero_timeline_policy(self):
        provider, _ = self.provider()
        provider._capture_runtime = None
        provider._netease_locator.locate = lambda: None
        provider._seek_session_matches_track = AsyncMock(return_value=True)
        media = MediaSession()
        media.get_timeline_properties = lambda: SimpleNamespace(position=0, start_time=0, end_time=0)
        try:
            await provider._bind_capture_session(media, visual().track)
            runtime = provider._capture_runtime
            self.assertTrue(runtime.cef_requested)
            self.assertIsNone(runtime.cef_session)
            self.assertFalse(runtime.diagnostics()['valid'])
        finally:
            if provider._capture_runtime is not None:
                await provider._capture_runtime.close()
            provider._capture_uia_worker.close()

    async def test_disable_closes_transport_and_reports_off(self):
        provider, values = self.provider()
        provider._netease_cdp_session = cdp = Session(12345)
        cdp.ready = True
        self.assertTrue(provider.netease_capture_health()['ready'])
        values['netease.cef.enabled'] = False
        self.assertIsNone(await provider._ensure_netease_cdp())
        cdp.close.assert_awaited_once()
        self.assertEqual(provider.netease_capture_health()['state'], 'off')

    async def test_repair_refuses_uncertain_physical_mutation(self):
        provider, _ = self.provider()
        provider._capture_runtime = SimpleNamespace(executor=SimpleNamespace(busy=False, uncertain=True))
        self.assertFalse(await provider._repair_netease_capture())
        provider._netease_locator.launch.assert_not_awaited()

    async def test_repair_retains_quarantine_after_runtime_was_closed(self):
        provider, _ = self.provider()
        provider._capture_runtime = None
        provider._capture_executor = SimpleNamespace(busy=False, uncertain=True)
        self.assertFalse(await provider._repair_netease_capture())
        provider._netease_locator.launch.assert_not_awaited()

    async def test_restore_requires_matching_metadata_and_verifies_readback(self):
        provider, _ = self.provider()
        track = SimpleNamespace(source_app_id='cloudmusic.exe', title='song', artist='artist',
                                duration_seconds=180, position_seconds=33)
        provider._get_session = AsyncMock(return_value=SimpleNamespace(source_app_user_model_id='cloudmusic.exe'))
        provider._seek_session_matches_track = AsyncMock(return_value=True)
        async with FakeCdpServer() as server:
            server.sample['playing'] = False
            cdp = provider._netease_cdp_session = CdpSession(server.port)
            await cdp.start()
            try:
                await provider._restore_netease_position(cdp, track)
                self.assertEqual(provider._netease_restore_status, 'verified')
                self.assertEqual(server.sample['position'], 33)
            finally:
                await cdp.close()

    async def test_restore_refuses_changed_native_track(self):
        provider, _ = self.provider()
        track = SimpleNamespace(source_app_id='cloudmusic.exe', title='song', artist='artist',
                                duration_seconds=180, position_seconds=33)
        provider._netease_expected_native = 'previous-native-track'
        provider._get_session = AsyncMock(return_value=SimpleNamespace(source_app_user_model_id='cloudmusic.exe'))
        provider._seek_session_matches_track = AsyncMock(return_value=True)
        async with FakeCdpServer() as server:
            cdp = provider._netease_cdp_session = CdpSession(server.port)
            await cdp.start()
            try:
                await provider._restore_netease_position(cdp, track)
                self.assertEqual(provider._netease_restore_status, 'track-changed')
                self.assertEqual(server.sample['position'], 12)
            finally:
                await cdp.close()
