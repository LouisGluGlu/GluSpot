from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from api.online_music import OnlineMusicError, OnlineMusicService


class LoginRuntimePreparationTests(unittest.TestCase):
    def make_service(self, *, prepare=True):
        backend = SimpleNamespace(
            available=lambda source: (source != "qishui", "not available"),
            request=Mock(side_effect=AssertionError("preparation must not request account or QR data")),
            close=Mock())
        if prepare:
            backend.prepare_login = Mock(return_value=True)
        store = SimpleNamespace(load=Mock(), save=Mock(), delete=Mock())
        service = OnlineMusicService(backend, store)
        self.addCleanup(service.close)
        return service, backend, store

    def test_construction_and_source_listing_do_not_prepare_any_runtime(self):
        service, backend, store = self.make_service()
        service.list_sources()
        backend.prepare_login.assert_not_called()
        backend.request.assert_not_called()
        store.load.assert_not_called()

    def test_preparation_delegates_without_account_or_storage_side_effects(self):
        service, backend, store = self.make_service()
        account = service.account_status("netease")
        generations = dict(service._generations)
        self.assertTrue(service.prepare_login_runtime("netease"))
        backend.prepare_login.assert_called_once_with("netease")
        backend.request.assert_not_called()
        store.load.assert_not_called()
        store.save.assert_not_called()
        self.assertIs(service.account_status("netease"), account)
        self.assertEqual(service._generations, generations)

    def test_optional_backend_can_decline_offline_preparation(self):
        service, backend, store = self.make_service(prepare=False)
        self.assertFalse(service.prepare_login_runtime("qq"))
        backend.request.assert_not_called()
        store.load.assert_not_called()

    def test_unavailable_and_closed_sources_cannot_prepare_a_child(self):
        service, backend, store = self.make_service()
        with self.assertRaises(OnlineMusicError) as raised:
            service.prepare_login_runtime("qishui")
        self.assertEqual(raised.exception.code, "unavailable")
        service.close()
        with self.assertRaises(OnlineMusicError) as raised:
            service.prepare_login_runtime("netease")
        self.assertEqual(raised.exception.code, "closed")
        backend.prepare_login.assert_not_called()
        store.load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
