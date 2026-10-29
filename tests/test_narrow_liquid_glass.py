"""Production chrome paints one shared face per capsule and keeps slot feedback."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from _demos.narrow3.layout import NarrowLayout
from local.music_control_glass import ControlMotion
from tests import test_narrow_chrome as fixtures


class NarrowLiquidGlassTests(unittest.TestCase):
    setUpClass = fixtures.NarrowChromeTests.__dict__['setUpClass']
    setUp = fixtures.NarrowChromeTests.setUp
    tearDown = fixtures.NarrowChromeTests.tearDown

    def narrow_layout(self):
        self.window.resize(480, 640)
        self.window.visibility.opacity.start = self.window.visibility.opacity.target = 1.
        self.window.visibility.opacity.duration = 0
        self.window.dj_available = True
        self.window.narrow = True
        self.window._finish_width_chrome()
        return NarrowLayout(480, 640, library_available=True, dj_available=True)

    def draw_calls(self, layout):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(self.window._clear_control_renderer, 'paint') as renderer, \
                patch.object(self.window, '_paint_surface_glass') as members, \
                patch.object(self.window, '_paint_transport_icon'), \
                QPainter(image) as painter:
            self.window.paint_controls(painter, layout, 1.)
        return {call.kwargs['role']: call for call in renderer.call_args_list}, members.call_args_list

    def start_shrink(self, progress):
        layout = self.narrow_layout()
        self.window._width_chrome_from = self.window._width_control_rects(False)
        self.window._width_chrome_active = True
        self.window._width_chrome_progress = progress
        return layout

    def test_steady_narrow_has_exactly_two_capsules_and_independent_back(self):
        calls, member_faces = self.draw_calls(self.narrow_layout())
        self.assertEqual(set(calls), {'chrome-utilities', 'chrome-collection'})
        for call in calls.values():
            self.assertEqual(call.kwargs['kind'], 'capsule')
            self.assertIsNone(call.kwargs['path'])
            self.assertFalse(call.kwargs['fusing'])
        self.assertEqual([call.kwargs['role'] for call in member_faces], ['library'])

    def test_mid_fusion_uses_one_path_without_moving_material_or_A(self):
        # B is halfway through .30–.55; bottom collection has already gone.
        calls, member_faces = self.draw_calls(self.start_shrink(.425))
        self.assertEqual(set(calls), {'chrome-utilities'})
        call = calls['chrome-utilities']
        self.assertEqual(call.kwargs['kind'], 'path')
        self.assertTrue(call.kwargs['fusing'])
        self.assertIsNone(call.kwargs['state'])
        self.assertFalse(call.kwargs['path'].isEmpty())
        self.assertEqual([call.kwargs['role'] for call in member_faces], ['library'])

    def test_A_has_no_glass_or_hits_in_the_phase_gap(self):
        calls, _members = self.draw_calls(self.start_shrink(.4))
        self.assertNotIn('chrome-collection', calls)
        self.assertNotIn('collection', calls)
        hit_keys = {key for group in self.window.glass_groups() for key in group}
        self.assertTrue(hit_keys.isdisjoint({'collection', 'favorite', 'playlist_add', 'personal_dj'}))

    def test_degraded_DJ_still_slides_through_the_wide_bottom_phase(self):
        self.narrow_layout()
        self.window._owned_personal_dj_enabled = True
        self.window.resize(320, 640)
        self.window._width_chrome_animation.stop()
        self.window.narrow = True
        self.window._finish_width_chrome()
        layout = NarrowLayout(320, 640, dj_available=True)
        self.assertFalse(layout.dj.isValid(), 'DJ yields to the five utility entries')
        wide = self.window._width_control_rects(False)
        self.assertTrue(wide['personal_dj'].isValid())
        self.window._width_chrome_from = wide
        self.window._width_phase_from = (0., 0., 1.)
        self.window._width_chrome_active = True

        def painted_DJ(progress):
            self.window._advance_width_chrome(progress)
            calls, members = self.draw_calls(layout)
            faces = [call.args[1] for call in members if call.kwargs['role'] == 'dj-icon']
            hits = {key: rect for group in self.window.glass_groups() for key, rect in group.items()}
            return faces, hits

        for progress in (0., .1):
            faces, hits = painted_DJ(progress)
            self.assertEqual(faces, [hits['personal_dj']])
            self.assertGreaterEqual(faces[0].left(), wide['personal_dj'].left())
        faces, hits = painted_DJ(.27)
        self.assertEqual(len(faces), 1, 'the alpha tail finishes the visible exit')
        self.assertNotIn('personal_dj', hits, 'the shared reveal gate disables the tail hit')
        faces, hits = painted_DJ(1.)
        self.assertFalse(faces)
        self.assertNotIn('personal_dj', hits)

        self.window.narrow = False
        self.window._width_chrome_from = self.window._width_control_rects(True)
        self.window._width_phase_from = (1., 1., 0.)
        faces, hits = painted_DJ(.85)
        self.assertEqual(faces, [hits['personal_dj']])
        self.assertGreater(faces[0].left(), wide['personal_dj'].left())
        faces, hits = painted_DJ(1.)
        self.assertEqual(faces, [wide['personal_dj']])

    def test_translation_is_a_member_of_the_shared_right_capsule_and_toggles(self):
        layout = self.narrow_layout()
        calls, member_faces = self.draw_calls(layout)
        utility = calls['chrome-utilities']
        self.assertEqual(len(utility.kwargs['hover_segments']), 5)
        self.assertTrue(layout.capsule_b.contains(layout._top_rects['translation']))
        self.assertEqual(len(member_faces), 1)  # independent Back only
        point = layout._top_rects['translation'].center()
        self.assertEqual(self.window._live_action_at(point), 'translation')
        self.owner.set_enable_dual_line_lyrics = lambda enabled: setattr(self.owner, 'enable_dual_line_lyrics', enabled)
        enabled = self.owner.enable_dual_line_lyrics
        with patch.object(self.window, '_send_media_command') as media, \
                patch.object(self.window, '_seek_to_lyric') as seek:
            self.window._begin_live_press(point)
            self.window._finish_live_press(point)
        self.assertEqual(self.owner.enable_dual_line_lyrics, not enabled)
        media.assert_not_called()
        seek.assert_not_called()

    def test_wide_fullscreen_focus_slot_keeps_window_mode_dispatch(self):
        self.narrow_layout()
        self.window.narrow = False
        self.window._fullscreen = True
        self.window.resize(960, 540)
        self.window._finish_width_chrome()
        self.window._sync_chrome_accessibility()
        button = self.window._chrome_focus_buttons['mode']
        self.assertFalse(button.isHidden())
        with patch.object(self.window, 'toggle_presentation_mode') as toggle:
            button.click()
        toggle.assert_called_once_with()

    def test_collection_hover_is_clipped_to_each_original_action_slot(self):
        layout = self.narrow_layout()
        self.window._glass_feedback.hover.update(favorite=.72, playlist_add=.18)
        calls, _members = self.draw_calls(layout)
        segments = calls['chrome-collection'].kwargs['hover_segments']
        hovered = {round(amount, 2): slot for amount, slot in segments if amount > 0.}
        self.assertEqual(set(hovered), {.72, .18})
        self.assertEqual(hovered[.72], layout.collection_slots[0])
        self.assertEqual(hovered[.18], layout.collection_slots[1])

    def test_static_shared_face_receives_the_pressed_member_state(self):
        layout = self.narrow_layout()
        state = ControlMotion(held=True, lift=.7, glow=.8, contact=layout.favorite.center())
        self.window._glass_feedback.controls['collection'] = state
        calls, _members = self.draw_calls(layout)
        self.assertIs(calls['chrome-collection'].kwargs['state'], state)
        self.window._width_chrome_from = self.window._width_control_rects(False)
        self.window._width_chrome_active = True
        self.window._width_chrome_progress = .8
        calls, _members = self.draw_calls(layout)
        self.assertIsNone(calls['chrome-collection'].kwargs['state'])

    def test_each_capsule_action_has_its_own_named_focus_slot(self):
        self.narrow_layout()
        self.window._sync_chrome_accessibility()
        expected = {'library': '返回音乐库', 'favorite': '喜欢', 'playlist_add': '收藏到歌单',
                    'personal_dj': '私人DJ', 'audio_output': '音频设备', 'content_layout': '内容布局',
                    'playback_mode': '播放顺序', 'translation': '中文翻译', 'return': '窗口大小', 'media:previous': '上一首',
                    'media:toggle_play_pause': '播放或暂停', 'media:next': '下一首'}
        visible = {key: button for key, button in self.window._chrome_focus_buttons.items()
                   if not button.isHidden()}
        self.assertEqual(set(visible), set(expected))
        for key, name in expected.items():
            button = visible[key]
            self.assertEqual(button.accessibleName(), name)
            self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.TabFocus)
            self.assertFalse(button.geometry().isEmpty())
        self.owner._music_library_back_callback = None
        self.window._sync_chrome_accessibility()
        self.assertTrue(self.window._chrome_focus_buttons['library'].isHidden())

    def test_fused_outline_grows_padding_continuously_into_static_capsule(self):
        layout = self.narrow_layout()
        slots = {key: layout._top_rects[key] for key in
                 ('audio_output', 'content_layout', 'playback_mode', 'translation', 'return')}
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(self.window._clear_control_renderer, 'paint') as renderer, QPainter(image) as p:
            self.window._width_chrome_active = True
            self.window._paint_chrome_capsule(p, slots, role='endpoint', fusion=.998, padding=3.992)
            fused = renderer.call_args.args[1]
            self.window._width_chrome_active = False
            self.window._paint_chrome_capsule(p, slots, role='endpoint', fusion=1., padding=4.)
            capsule = renderer.call_args.args[1]
        self.assertAlmostEqual(fused.left(), capsule.left(), delta=.02)
        self.assertAlmostEqual(fused.right(), capsule.right(), delta=.02)
        self.assertEqual(fused.top(), capsule.top())
        self.assertEqual(fused.bottom(), capsule.bottom())

    def test_content_backdrop_is_cached_at_ten_hz_and_frozen_for_merge(self):
        self.narrow_layout()
        def ink(painter):
            painter.fillRect(0, 0, 360, 80, QColor('red'))
        with patch.object(self.window, 'isVisible', return_value=True), \
                patch.object(self.window, '_paint_wide_lyrics_mounted', side_effect=ink) as paint_content, \
                patch.object(self.window, 'paint_header'), \
                patch.object(self.window, 'grab', side_effect=AssertionError('No widget grabs')), \
                patch.object(self.window, '_paint_chrome_bands', side_effect=AssertionError('No glass in backdrop')):
            self.window._refresh_chrome_backdrop(100.)
            self.assertEqual(paint_content.call_count, 1)
            snapshot = QImage(self.window._surface_glass_backdrop())
            self.assertEqual(snapshot.pixelColor(30, 30), QColor('red'))
            self.window.visibility.progress = .9
            self.window._refresh_chrome_backdrop(100.05)
            self.assertEqual(paint_content.call_count, 1)
            self.window._refresh_chrome_backdrop(100.101)
            self.assertEqual(paint_content.call_count, 2)
            self.window._width_chrome_active = True
            self.window.visibility.progress = 1.
            self.window._refresh_chrome_backdrop(100.5)
            self.assertEqual(paint_content.call_count, 2)
            self.window._width_glass_backdrop = QImage(snapshot)
            self.window._chrome_backdrop = QImage()
            self.assertEqual(self.window._surface_glass_backdrop(), snapshot)


if __name__ == '__main__':
    unittest.main()
