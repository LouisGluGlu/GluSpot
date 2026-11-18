"""NetEase opened without its debug port is reopened once with precise progress."""
import tests.isolate_appdata  # noqa: F401  # before product imports capture %APPDATA%

import asyncio
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from api.providers import WindowsMediaProvider


class _Stop(BaseException):
    """Ends the watcher's endless loop once the scripted polls run out."""


def _provider(settings, attached=None):
    provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
    provider.settings_getter = lambda key, default=None: settings.get(key, default)
    provider._netease_cdp_session = None
    provider.repairs = 0
    provider.attaches = 0

    async def ensure():
        provider.attaches += 1
        return attached

    async def repair():
        provider.repairs += 1
        return True

    provider._ensure_netease_cdp_locked = ensure
    provider._repair_netease_capture = repair
    provider._netease_cef_enabled = lambda: True
    provider._netease_explicit_relaunch_allowed = lambda: True
    return provider


def _run(provider, states):
    """Run the watcher over a sequence of "is NetEase running" polls."""
    polls = list(states)

    def processes():
        return [(1, "cloudmusic.exe")] if polls.pop(0) else []

    calls = []

    async def sleep(_seconds):
        # Out of scripted polls, or a watcher that stopped polling (switch off).
        calls.append(_seconds)
        if not polls or len(calls) > 4 * len(states) + 4:
            raise _Stop()
        await asyncio.sleep(0)

    async def main():
        try:
            await provider._watch_netease_starts(processes=processes, sleep=sleep)
        except _Stop:
            pass
    asyncio.run(main())


class AutoPreciseWatchTests(unittest.TestCase):
    def test_opening_netease_reopens_it_once_with_the_port(self):
        provider = _provider({"netease.auto_precise": True})
        _run(provider, [False, False, True, True, True])
        self.assertEqual(provider.repairs, 1)
        self.assertEqual(provider.attaches, 1)          # tried to attach first

    def test_a_player_already_open_when_gluspot_starts_is_left_alone(self):
        provider = _provider({"netease.auto_precise": True})
        _run(provider, [True, True, True])
        self.assertEqual(provider.repairs, 0)

    def test_a_player_opened_with_the_port_is_only_attached(self):
        provider = _provider({"netease.auto_precise": True}, attached=object())
        _run(provider, [False, True, True])
        self.assertEqual((provider.attaches, provider.repairs), (1, 0))

    def test_the_switch_off_never_restarts_the_player(self):
        provider = _provider({"netease.auto_precise": False})
        _run(provider, [False, True, False, True])
        self.assertEqual((provider.attaches, provider.repairs), (0, 0))

    def test_closing_and_reopening_counts_again(self):
        provider = _provider({"netease.auto_precise": True})
        _run(provider, [False, True, True, False, True, True])
        self.assertEqual(provider.repairs, 2)


def _flip(switch, value):
    """What a click on the switch does: set it, then announce it."""
    switch.setChecked(value)
    switch.toggled.emit(value)


class AutoPreciseSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, settings, consent):
        from local.netease_capture_settings import NetEaseCaptureSettings
        asked = []

        def confirm():
            asked.append(True)
            return consent
        panel = NetEaseCaptureSettings(lambda k, d=None: settings.get(k, d), settings.__setitem__,
                                       lambda: None, platform="unsupported", confirm=confirm)
        panel.installation = object()
        self.addCleanup(panel.deleteLater)
        return panel, asked

    def test_turning_it_on_asks_for_the_same_consent_and_turns_precise_progress_on(self):
        settings = {"netease.auto_precise": False}
        panel, asked = self.panel(settings, consent=True)
        self.assertFalse(panel.auto_switch.isChecked())
        _flip(panel.auto_switch, True)
        self.assertEqual(asked, [True])
        self.assertTrue(settings["netease.auto_precise"])
        self.assertTrue(settings["netease.cef.enabled"])
        self.assertEqual(settings["netease.transport"], "relaunch")

    def test_declining_leaves_it_off(self):
        settings = {"netease.auto_precise": False}
        panel, _asked = self.panel(settings, consent=False)
        _flip(panel.auto_switch, True)
        self.assertFalse(panel.auto_switch.isChecked())
        self.assertFalse(settings["netease.auto_precise"])
        self.assertNotIn("netease.cef.enabled", settings)

    def test_with_precise_progress_on_it_is_a_plain_switch(self):
        settings = {"netease.auto_precise": True, "v2_capture_position_enabled": True,
                    "netease.cef.enabled": True, "netease.transport": "relaunch"}
        panel, asked = self.panel(settings, consent=True)
        self.assertTrue(panel.auto_switch.isChecked())
        _flip(panel.auto_switch, False)
        self.assertFalse(settings["netease.auto_precise"])
        self.assertEqual(asked, [])


if __name__ == "__main__":
    unittest.main()
