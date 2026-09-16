import unittest

from local.album_art_pipeline import album_art_bytes_signature, album_art_update_plan


class AlbumArtPipelineTests(unittest.TestCase):
    def test_signature_is_stable_for_album_art_bytes(self):
        first = album_art_bytes_signature(b"cover-data")
        second = album_art_bytes_signature(b"cover-data")

        self.assertEqual(first, second)
        self.assertNotEqual(first, "")
        self.assertEqual(album_art_bytes_signature(b""), "")
        self.assertEqual(album_art_bytes_signature("cover-data"), "")

    def test_same_signature_reuses_existing_cover_without_cache_clear(self):
        plan = album_art_update_plan(
            incoming_signature="sig-1",
            current_signature="sig-1",
            cover_loaded=True,
            black_placeholder_loaded=False,
            has_existing_cover=True,
            track_changed=False,
            same_visible_track=True,
            message_refresh=False,
        )

        self.assertEqual(plan.action, "reuse")
        self.assertFalse(plan.clear_scaled_cache)
        self.assertEqual(plan.next_signature, "sig-1")

    def test_message_refresh_black_placeholder_preserves_existing_cover(self):
        plan = album_art_update_plan(
            incoming_signature="black",
            current_signature="current",
            cover_loaded=True,
            black_placeholder_loaded=True,
            has_existing_cover=True,
            track_changed=False,
            same_visible_track=True,
            message_refresh=True,
        )

        self.assertEqual(plan.action, "preserve")
        self.assertTrue(plan.reject_black_placeholder)
        self.assertFalse(plan.clear_scaled_cache)
        self.assertEqual(plan.next_signature, "current")

    def test_message_refresh_black_placeholder_without_existing_cover_is_ignored(self):
        plan = album_art_update_plan(
            incoming_signature="black",
            current_signature="",
            cover_loaded=True,
            black_placeholder_loaded=True,
            has_existing_cover=False,
            track_changed=False,
            same_visible_track=True,
            message_refresh=True,
        )

        self.assertEqual(plan.action, "ignore")
        self.assertTrue(plan.reject_black_placeholder)
        self.assertFalse(plan.clear_scaled_cache)

    def test_missing_cover_for_same_visible_track_preserves_existing_cover(self):
        plan = album_art_update_plan(
            incoming_signature="",
            current_signature="current",
            cover_loaded=False,
            black_placeholder_loaded=False,
            has_existing_cover=True,
            track_changed=False,
            same_visible_track=True,
            message_refresh=True,
        )

        self.assertEqual(plan.action, "preserve")
        self.assertEqual(plan.next_signature, "current")

    def test_loaded_non_placeholder_cover_sets_new_cover(self):
        plan = album_art_update_plan(
            incoming_signature="new",
            current_signature="old",
            cover_loaded=True,
            black_placeholder_loaded=False,
            has_existing_cover=True,
            track_changed=True,
            same_visible_track=False,
            message_refresh=False,
        )

        self.assertEqual(plan.action, "set")
        self.assertTrue(plan.clear_scaled_cache)
        self.assertEqual(plan.next_signature, "new")

    def test_track_change_without_cover_clears_existing_cover(self):
        plan = album_art_update_plan(
            incoming_signature="",
            current_signature="old",
            cover_loaded=False,
            black_placeholder_loaded=False,
            has_existing_cover=True,
            track_changed=True,
            same_visible_track=False,
            message_refresh=False,
        )

        self.assertEqual(plan.action, "clear")
        self.assertTrue(plan.clear_scaled_cache)
        self.assertTrue(plan.should_clear_current_cover)


if __name__ == "__main__":
    unittest.main()
