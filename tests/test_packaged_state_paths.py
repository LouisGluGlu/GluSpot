from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from local import branding


class PackagedStatePathTests(unittest.TestCase):
    def test_frozen_copy_uses_its_own_program_directory_and_no_external_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            first = folder / "first-copy" / "GluSpot.exe"
            second = folder / "second-copy" / "GluSpot.exe"
            for executable in (first, second):
                with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)):
                    state = executable.parent / "GluSpot"
                    self.assertEqual(Path(branding.app_data_dir()), state)
                    self.assertEqual(Path(branding.local_app_data_dir()), state)
                    self.assertEqual(Path(branding.settings_file_for()), state / "settings.json")
                    self.assertEqual(branding.legacy_app_data_dirs(), [])
                    self.assertEqual(branding.custom_icon_dir(), state / "icons")

    def test_explicit_storage_override_and_source_behavior_remain_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(sys, "frozen", True, create=True):
                self.assertEqual(Path(branding.settings_file_for(temporary)), Path(temporary) / "GluSpot" / "settings.json")
            with patch.object(sys, "frozen", False, create=True), patch.dict("os.environ", {"APPDATA": temporary, "LOCALAPPDATA": temporary}):
                self.assertEqual(Path(branding.app_data_dir()), Path(temporary) / "GluSpot")
                self.assertIn(str(Path(temporary) / "DynamicIsland"), branding.legacy_app_data_dirs())

    def test_frozen_account_store_uses_sibling_state_even_without_localappdata(self):
        from api.online_music_credentials import ProtectedCredentialStore
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "GluSpot.exe"
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)), patch.dict("os.environ", {}, clear=True):
                store = ProtectedCredentialStore()
                self.assertEqual(store.root, executable.parent / "GluSpot" / "accounts")

    def test_generated_lyrics_cache_is_local_to_each_frozen_copy(self):
        from api.lyrics import LocalLyricsStore
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "GluSpot.exe"
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)), patch.dict("os.environ", {}, clear=True):
                self.assertEqual(LocalLyricsStore._default_generated_root(Path(temporary) / "lyrics"),
                                 executable.parent / "GluSpot" / "lyrics-cache")


if __name__ == "__main__":
    unittest.main()
