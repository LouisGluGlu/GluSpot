"""Retired app-level arrival notices must stay absent from the startup path."""
from __future__ import annotations

import ast
import inspect
import textwrap
import unittest

from local.app import LocalIslandApp


class AudioDeviceSurfaceDispatchTests(unittest.TestCase):
    def test_application_has_no_notice_dispatch_or_notification_timer(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(LocalIslandApp.__init__)))
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertNotIn("AudioDeviceNotice", identifiers)
        self.assertNotIn("audio_device_notice", names)
        self.assertNotIn("audio_device_notice_timer", names)
        self.assertNotIn("_poll_audio_device_notice", LocalIslandApp.__dict__)

    def test_audio_route_lifecycle_is_retained(self):
        source = inspect.getsource(LocalIslandApp.__init__)
        self.assertIn("self.data_provider.start_audio_outputs()", source)
        self.assertIn("self.data_provider.close_audio_outputs", source)
        self.assertIn("self._setup_mydockfinder_listener()", source)


if __name__ == "__main__":
    unittest.main()
