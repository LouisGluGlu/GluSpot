"""Offscreen first-enable, health and explicit-restart UI contracts."""
import concurrent.futures
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel

from api.capture.netease import NetEaseInstallation
from api.providers import PlaybackSourceProvider, empty_visual_data


class Locator:
    def __init__(self, installation):
        self.installation = installation
        self.calls = 0

    def locate(self):
        self.calls += 1
        return self.installation


class Provider:
    def __init__(self):
        self.repairs = 0
        self.disables = 0
        self.future = None
        self.health = dict(state='ready', ready=True, message='精确进度已连接')

    def repair_netease_capture(self):
        self.repairs += 1
        return self.future if self.future is not None else True

    def disable_netease_capture(self):
        self.disables += 1
        return True

    def netease_capture_health(self):
        return dict(self.health)


class NetEaseCaptureSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Load UI fixtures when this class runs, keeping test discovery
        # independent of the opt-in panel's import order.
        from local.netease_capture_settings import ENABLE_NOTICE, NetEaseCaptureSettings
        from local.settings_window import SettingsWindow
        from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager
        cls.panel_type = NetEaseCaptureSettings
        cls.settings_window_type = SettingsWindow
        cls.island_type = FakeIslandWidget
        cls.settings_type = FakeSettingsManager
        cls.enable_notice = ENABLE_NOTICE

    def panel(self, *, platform='win32', installed=True, accept=True):
        self.values = {}
        self.provider = Provider()
        self.confirmations = []
        self.locator = Locator(NetEaseInstallation('C:/CloudMusic/cloudmusic.exe') if installed else None)

        def confirm():
            self.confirmations.append(True)
            return accept

        # Run the discovery job synchronously, then deliver its queued UI signal.
        # Observing locator.calls alone does not mean _located has run yet.
        pool = SimpleNamespace(start=lambda job: job.run())
        with patch('local.netease_capture_settings.QThreadPool') as thread_pool:
            thread_pool.globalInstance.return_value = pool
            panel = self.panel_type(self.values.get, self.values.__setitem__,
                                    lambda: self.provider, locator=self.locator,
                                    platform=platform, confirm=confirm)
        self.addCleanup(panel.close)
        self.app.processEvents()
        if platform == 'win32':
            self.assertIsNone(panel._job)
        return panel

    def test_platform_gate_does_not_probe_or_show(self):
        panel = self.panel(platform='linux')
        self.assertTrue(panel.isHidden())
        self.assertEqual(self.locator.calls, 0)
        self.assertFalse(self.values)

    def test_missing_installation_stays_hidden(self):
        panel = self.panel(installed=False)
        self.assertTrue(panel.isHidden())
        self.assertEqual(self.locator.calls, 1)

    def test_decline_has_no_settings_or_player_effect(self):
        panel = self.panel(accept=False)
        self.assertFalse(panel.isHidden())
        panel.enable_button.click()
        self.assertEqual(len(self.confirmations), 1)
        self.assertFalse(self.values)
        self.assertEqual(self.provider.repairs, 0)

    def test_single_accept_enables_flags_and_only_netease_profile(self):
        panel = self.panel()
        self.values['v2_capture_app_profiles'] = {'netease': {'mode': 'off'}, 'qq': {'mode': 'smtc'}}
        panel.enable_button.click()
        self.assertEqual(len(self.confirmations), 1)
        self.assertTrue(self.values['v2_system_capture_enabled'])
        self.assertTrue(self.values['v2_capture_position_enabled'])
        self.assertTrue(self.values['netease.cef.enabled'])
        self.assertEqual(self.values['netease.transport'], 'relaunch')
        self.assertEqual(self.values['v2_capture_app_profiles'],
                         {'netease': {'mode': 'auto'}, 'qq': {'mode': 'smtc'}})
        self.assertEqual(self.provider.repairs, 1)
        self.assertTrue(panel.enable_button.isHidden())

    def test_health_and_repair_use_existing_provider_without_second_confirmation(self):
        panel = self.panel()
        panel.enable()
        self.assertTrue(panel.lamp.property('active'))
        self.assertEqual(panel.status_label.text(), '精确进度已连接')
        self.provider.health = dict(state='repair', ready=False, message='未连接；可修复并重启网易云')
        panel.refresh()
        self.assertFalse(panel.lamp.property('active'))
        self.assertIn('可修复', panel.status_label.text())
        panel.repair_button.click()
        self.assertEqual(self.provider.repairs, 2)
        self.assertEqual(len(self.confirmations), 1)

    def test_future_is_observed_without_blocking_or_double_restart(self):
        panel = self.panel()
        self.provider.future = concurrent.futures.Future()
        panel.enable()
        self.assertFalse(panel.repair_button.isEnabled())
        panel.repair()
        self.assertEqual(self.provider.repairs, 1)
        self.assertFalse(self.provider.future.done())
        self.provider.future.set_result(False)
        panel._check_operation()
        self.assertTrue(panel.repair_button.isEnabled())
        self.assertIn('请重试', panel.status_label.text())

    def test_disable_preserves_other_capture_and_closes_transport(self):
        panel = self.panel()
        panel.enable()
        panel.disable_button.click()
        self.assertFalse(self.values['netease.cef.enabled'])
        self.assertEqual(self.values['netease.transport'], 'off')
        self.assertTrue(self.values['v2_capture_position_enabled'])
        self.assertEqual(self.provider.disables, 1)
        self.assertEqual(panel.status_label.text(), '未启用')

    def test_enable_disclosure_covers_system_source_and_restart_terms(self):
        panel = self.panel()
        self.assertTrue(any('系统抓取' in label.text() for label in panel.findChildren(QLabel)))
        for text in ('系统抓取', '重启', '127.0.0.1', '没有鉴权', '其他进程或用户', '用户协议'):
            self.assertIn(text, self.enable_notice)


    def test_window_source_switch_and_provider_route_follow_opt_in(self):
        island = self.island_type()
        self.addCleanup(island.close)
        settings = self.settings_type()
        settings.set('v2_system_capture_enabled', False)
        window = self.settings_window_type(island, settings)
        self.addCleanup(window.close)
        system = Provider()
        system.get_visual_data = lambda: empty_visual_data("Cloud", "Artist", "cloudmusic.exe")
        island.data_provider = SimpleNamespace(system=system)
        locator = Locator(NetEaseInstallation('C:/CloudMusic/cloudmusic.exe'))
        pool = SimpleNamespace(start=lambda job: job.run())
        with patch('local.netease_capture_settings.QThreadPool') as thread_pool:
            thread_pool.globalInstance.return_value = pool
            panel = self.panel_type(window._setting_value, window._save,
                                    window._system_media_provider,
                                    locator=locator, platform='win32', confirm=lambda: True)
        self.addCleanup(panel.close)
        self.app.processEvents()
        self.assertFalse(window.system_capture_switch.isChecked())
        source = PlaybackSourceProvider.__new__(PlaybackSourceProvider)
        source.system, source.owned = system, SimpleNamespace(track=None)
        source.settings_getter = window._setting_value
        self.assertEqual(source.get_visual_data().track.title, '程序内播放')
        panel.enable_button.click()
        self.assertTrue(settings.get('v2_system_capture_enabled'))
        self.assertTrue(window.system_capture_switch.isChecked())
        self.assertTrue(settings.get('v2_owned_playback_enabled'))
        self.assertEqual(source.get_visual_data().track.title, 'Cloud')
        owned_data = empty_visual_data('Owned', 'Artist', 'GluSpot')
        source.owned = SimpleNamespace(track=owned_data.track, visual_data=lambda: owned_data)
        self.assertEqual(source.get_visual_data().track.title, 'Owned')

    def test_media_page_resolves_wrapper_system_provider(self):
        island = self.island_type()
        self.addCleanup(island.close)
        system = Provider()
        island.data_provider = SimpleNamespace(system=system)
        window = self.settings_window_type(island, self.settings_type())
        self.addCleanup(window.close)
        self.assertIs(window._system_media_provider(), system)
        self.assertIsInstance(window.netease_capture_settings, self.panel_type)
        self.assertTrue(window.netease_capture_settings.isHidden())
        island.data_provider = system
        self.assertIs(window._system_media_provider(), system)
