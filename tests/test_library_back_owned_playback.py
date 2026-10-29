"""t137u: library back availability vs owned playback (mocks only)."""
from __future__ import annotations
import unittest
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow


class _Owner:
    def __init__(self, owned=True, login_required=False):
        self.settings = {"v2_owned_playback_enabled": owned}
        self.settings_getter = lambda key, default=None: self.settings.get(key, default)
        self._music_login_required = lambda: login_required
        self._music_login_callback = Mock(name="login")
        self._music_library_back_callback = Mock(name="library")
        self.data_provider = Mock()
        self.data_provider._owned_active = lambda: False  # capture-only path


class LibraryBackOwnedPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, **kwargs):
        owner = _Owner(**kwargs)
        # Avoid full MusicSurfaceWindow init weight: bind methods onto a simple object
        w = MusicSurfaceWindow.__new__(MusicSurfaceWindow)
        w.owner = owner
        w._library_loading = None
        return w, owner

    def test_owned_false_hides_back(self):
        w, owner = self._window(owned=False)
        self.assertFalse(MusicSurfaceWindow._library_back_available(w))

    def test_owned_true_shows_even_without_owned_active(self):
        w, owner = self._window(owned=True, login_required=False)
        self.assertTrue(MusicSurfaceWindow._library_back_available(w))
        self.assertTrue(MusicSurfaceWindow.return_to_library(w))
        owner._music_library_back_callback.assert_called_once()
        owner._music_login_callback.assert_not_called()

    def test_login_required_routes_to_login(self):
        w, owner = self._window(owned=True, login_required=True)
        self.assertTrue(MusicSurfaceWindow._library_back_available(w))
        self.assertTrue(MusicSurfaceWindow.return_to_library(w))
        owner._music_login_callback.assert_called_once()
        owner._music_library_back_callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
