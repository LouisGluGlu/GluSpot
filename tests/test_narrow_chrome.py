"""Narrow chrome has two shared frames and only transport below content."""
import itertools
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from _demos.narrow3.layout import NarrowLayout
from _demos.narrow3.narrow_window import NarrowStudyWindow


class NarrowChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = IslandStackVisual(data_provider=EmptyIslandDataProvider())
        self.owner.timer.stop()
        self.window = NarrowStudyWindow(self.owner, 'B', 'left', live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.visibility.progress = 1.0
        self.owner._music_library_back_callback = Mock()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner._application_quitting = True
        self.owner.close()
        self.app.processEvents()

    def test_top_targets_fit_without_overlap_and_bottom_has_only_transport(self):
        for width, library, dj in itertools.product((320, 336, 360, 480, 640), (False, True), (False, True)):
            with self.subTest(width=width, library=library, dj=dj):
                self.window.resize(width, 640)
                self.window.dj_available = dj
                self.owner.settings_getter = lambda key, default=True: library if key == 'v2_owned_playback_enabled' else default
                layout = NarrowLayout(width, 640, library_available=library, dj_available=dj)
                groups = self.window.glass_groups()
                top = {**groups[0], **groups[1]}
                self.assertEqual('library' in top, library)
                self.assertEqual('personal_dj' in top, layout.dj.isValid())
                self.assertEqual(set(groups[2]), {'media:previous', 'media:toggle_play_pause', 'media:next'})
                self.assertEqual(set(top) - {'library', 'personal_dj'},
                                 {'collection', 'audio_output', 'playback_mode', 'content_layout', 'translation', 'return'})
                for rect in top.values():
                    self.assertGreaterEqual(rect.left(), 16)
                    self.assertLessEqual(rect.right(), width - 16 + .001)
                    self.assertLessEqual(rect.bottom(), 52)
                for first, second in itertools.combinations(top.values(), 2):
                    self.assertFalse(first.intersects(second))
                ordered = sorted(top, key=lambda key: top[key].left())
                expected = ((['library'] if library else []) + ['collection']
                            + (['personal_dj'] if layout.dj.isValid() else [])
                            + ['audio_output', 'content_layout', 'playback_mode', 'translation', 'return'])
                self.assertEqual(ordered, expected)
                for rect in groups[2].values():
                    self.assertGreater(rect.top(), self.window.height() / 2)

    def test_top_controls_hit_their_single_live_actions(self):
        self.window.resize(360, 640)
        self.window.dj_available = True
        self.owner.settings_getter = lambda key, default=True: default
        for group in self.window.glass_groups():
            for key, rect in group.items():
                if key == 'collection':
                    for fraction, action in ((.25, 'favorite'), (.75, 'playlist_add')):
                        point = QPointF(rect.left() + fraction * rect.width(), rect.center().y())
                        self.assertEqual(self.window._live_action_at(point), action)
                else:
                    self.assertEqual(self.window._live_action_at(rect.center()), key)
        self.window.visibility.progress = 0.0
        self.assertTrue(all(not group for group in self.window.glass_groups()))

    def test_no_library_slot_when_shared_back_gate_is_unavailable(self):
        self.owner._music_library_back_callback = None
        self.assertFalse(self.window._library_back_available())
        self.assertFalse(self.window.glass_groups()[1])
        for width in (320, 330, 360):
            rects = NarrowLayout(width, 640, library_available=False, dj_available=True)._top_rects
            self.assertNotIn('library', rects)
            self.assertGreaterEqual(min(rect.left() for rect in rects.values()), 16)

    def test_capsule_budget_and_alignment_are_independent_of_back(self):
        for width, library, dj in itertools.product((320, 336, 360, 480, 640),
                                                   (False, True), (False, True)):
            with self.subTest(width=width, library=library, dj=dj):
                layout = NarrowLayout(width, 640, library_available=library, dj_available=dj)
                reference = NarrowLayout(width, 640, library_available=True, dj_available=dj)
                self.assertEqual(layout.capsule_a, reference.capsule_a)
                self.assertEqual(layout.capsule_b, reference.capsule_b)
                self.assertEqual(layout.capsule_b.right(), width - 16)
                self.assertGreaterEqual(layout.capsule_a.left(), 16 + layout.button_size + 8)
                self.assertLessEqual(layout.capsule_a.right() + 8, layout.capsule_b.left() + .001)
                self.assertEqual(len(layout.collection_slots), 2)
                self.assertEqual(layout.dj.isValid(), dj and width >= 480)
                self.assertTrue(all(layout.capsule_a.contains(slot) for slot in layout.collection_slots))
                for key in ('audio_output', 'content_layout', 'playback_mode', 'translation', 'return'):
                    self.assertTrue(layout.capsule_b.contains(layout._top_rects[key]))
                if library:
                    self.assertEqual((layout.back.x(), layout.back.y()), (16, 16))
                if layout.dj.isValid():
                    self.assertTrue(layout.capsule_a.contains(layout.dj))


if __name__ == '__main__':
    unittest.main()
