"""Attach-only capture discovers a running player without process mutation."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from api.capture.netease import NetEaseInstallation, NetEaseLaunch, NetEaseLocator
from api.providers import WindowsMediaProvider
from tests.capture_async import CaptureAsyncTestCase
from tests.test_capture_netease_provider import Session
from tests.test_capture_netease_relaunch import PATH, OTHER, Player
from tests.test_capture_smtc import visual


class NetEaseAttachLocatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.player = Player()
        self.installation = NetEaseInstallation(PATH, '3.1.42', (1,))

    def locator(self):
        return NetEaseLocator(
            PATH, processes=self.player.processes, registry_paths=lambda: [],
            exists=lambda path: path == PATH, version=lambda path: '3.1.42',
            spawn=self.player.spawn, stop=self.player.stop,
            owner_dir=Path(self.temp.name), debug_ports=self.player.debug_ports,
            healthy=self.player.healthy)

    def assert_no_process_changes(self):
        self.assertEqual(self.player.stopped, [])
        self.assertEqual(self.player.spawns, [])

    async def test_healthy_running_debug_port_is_reused(self):
        self.player.ports = [(1, PATH, 13371)]
        launch = await self.locator().attach(self.installation)
        self.assertEqual((launch.port, launch.pid), (13371, 1))
        self.assertEqual(launch.installation, self.installation)
        self.assertTrue(launch.reused)
        self.assert_no_process_changes()

    async def test_locator_can_find_the_running_installation(self):
        self.player.ports = [(1, PATH, 13371)]
        launch = await self.locator().attach()
        self.assertEqual(launch.installation.path, PATH)
        self.assertEqual(launch.port, 13371)
        self.assertTrue(launch.reused)
        self.assert_no_process_changes()

    async def test_absent_or_unhealthy_endpoint_never_starts_or_stops_player(self):
        for ports, healthy in (([], True), ([(1, PATH, 13371)], False)):
            self.player.ports, self.player.health = ports, healthy
            with self.subTest(ports=ports, healthy=healthy):
                self.assertIsNone(await self.locator().attach(self.installation))
                self.assert_no_process_changes()
                self.assertEqual(self.player.processes(), [(1, PATH)])

    async def test_stale_pid_wrong_executable_and_invalid_ports_are_not_attached(self):
        self.player.ports = [(99, PATH, 13371), (1, OTHER, 13371),
                             (1, PATH, 0), (1, PATH, 65536), (1, PATH, True)]
        self.assertIsNone(await self.locator().attach(self.installation))
        self.assert_no_process_changes()

    async def test_missing_player_never_spawns_even_with_a_stale_debug_endpoint(self):
        self.player.running = []
        self.player.ports = [(1, PATH, 13371)]
        self.assertIsNone(await self.locator().attach(self.installation))
        self.assert_no_process_changes()

    async def test_missing_installation_does_not_create_a_player(self):
        locator = NetEaseLocator(
            processes=lambda: [], registry_paths=lambda: [], exists=lambda path: False,
            spawn=self.player.spawn, stop=self.player.stop,
            owner_dir=Path(self.temp.name), debug_ports=self.player.debug_ports,
            healthy=self.player.healthy)
        self.assertIsNone(await locator.attach())
        self.assert_no_process_changes()


class NetEaseAttachProviderTests(CaptureAsyncTestCase):
    def provider(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        settings = {'v2_capture_position_enabled': True, 'netease.cef.enabled': True,
                    'netease.transport': 'attach'}
        provider.settings_getter = lambda key, default=None: settings.get(key, default)
        provider._netease_cdp_session = None
        provider._last_data = visual()
        installation = NetEaseInstallation(PATH, '3.1.42', (1,))
        provider._netease_locator = SimpleNamespace(
            locate=lambda: installation,
            attach=AsyncMock(return_value=NetEaseLaunch(13371, installation, 1, reused=True)),
            launch=AsyncMock(), restore=AsyncMock())
        provider._restore_netease_position = AsyncMock()
        provider._attach_test_settings = settings
        return provider

    def assert_no_process_changes(self, provider):
        provider._netease_locator.launch.assert_not_awaited()
        provider._netease_locator.restore.assert_not_awaited()
        provider._restore_netease_position.assert_not_awaited()

    async def test_attach_reuses_existing_port_idempotently_without_rewind(self):
        provider = self.provider()
        with patch('api.capture.cdp.CdpSession', Session):
            first = await provider._ensure_netease_cdp()
            second = await provider._ensure_netease_cdp()
            await asyncio.sleep(0)
        self.assertIs(first, second)
        self.assertEqual(first.port, 13371)
        self.assertEqual(first.kwargs, {'adapter': 'auto', 'version': '3.1.42'})
        provider._netease_locator.attach.assert_awaited_once()
        first.start.assert_awaited_once()
        self.assert_no_process_changes(provider)

    async def test_no_existing_port_stays_disconnected_without_relaunch(self):
        provider = self.provider()
        provider._netease_locator.attach.return_value = None
        with patch('api.capture.cdp.CdpSession', Session):
            self.assertIsNone(await provider._ensure_netease_cdp())
            self.assertIsNone(await provider._ensure_netease_cdp())
        provider._netease_locator.attach.assert_awaited_once()
        self.assertIsNone(provider._netease_cdp_session)
        self.assert_no_process_changes(provider)

    async def test_cdp_start_failure_detaches_without_spawning_recovery(self):
        provider = self.provider()
        session = Session(13371)
        session.start.side_effect = OSError('CDP startup failed')
        with patch('api.capture.cdp.CdpSession', return_value=session):
            self.assertIsNone(await provider._ensure_netease_cdp())
        session.close.assert_awaited_once()
        self.assertIn('CDP startup failed', provider.netease_capture_health()['reason'])
        self.assertIsNone(provider._netease_cdp_session)
        self.assert_no_process_changes(provider)

    async def test_late_cdp_failure_preserves_the_running_player(self):
        provider = self.provider()
        session = Session(13371)
        session.wait_ready.return_value = False
        with patch('api.capture.cdp.CdpSession', return_value=session):
            await provider._ensure_netease_cdp()
            await provider._netease_watch_task
        session.close.assert_awaited_once()
        self.assertIsNone(provider._netease_cdp_session)
        self.assertIn('connection failed', provider.netease_capture_health()['reason'])
        self.assert_no_process_changes(provider)

    async def test_repair_in_attach_mode_can_only_attach_again(self):
        provider = self.provider()
        provider._request_immediate_media_refresh = lambda: None
        with patch('api.capture.cdp.CdpSession', Session):
            self.assertTrue(await provider._repair_netease_capture())
            self.assertTrue(await provider._repair_netease_capture())
        provider._netease_locator.attach.assert_awaited_once()
        self.assert_no_process_changes(provider)

    async def test_disabling_during_attach_does_not_request_player_recovery(self):
        provider = self.provider()
        launch = provider._netease_locator.attach.return_value

        async def disable_before_return(_installation):
            provider._attach_test_settings['netease.cef.enabled'] = False
            return launch

        provider._netease_locator.attach.side_effect = disable_before_return
        with patch('api.capture.cdp.CdpSession', Session) as session_type:
            self.assertIsNone(await provider._ensure_netease_cdp())
        self.assertIsNone(provider._netease_cdp_session)
        self.assert_no_process_changes(provider)
