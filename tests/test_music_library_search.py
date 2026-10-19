from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from local.music_library_window import MusicLibraryWindow


class MusicLibrarySearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MusicLibraryWindow()
        self.window.show()
        self.app.processEvents()
        self.search = self.window.search

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _phase(self, phase):
        self.search._motion.stop()
        self.search._set_phase(phase)

    def test_idle_search_is_rightmost_icon_and_progress_keeps_its_own_track(self):
        self.assertEqual(self.search.width(), 44)
        self.assertEqual(self.search.geometry().right(), self.window.width() - 25)
        self.assertLess(self.window.progress_button.geometry().right(), self.search.x())
        self.assertEqual(self.search._ink, 0.)
        self.assertFalse(self.search.expanded)
        self.app.sendEvent(self.search, QEvent(QEvent.Type.Enter))
        self.assertFalse(self.search.expanded)

    def test_icon_expands_in_place_and_does_not_submit_empty_search(self):
        spy = QSignalSpy(self.window.searchRequested)
        identity = self.search
        self.window.search_button.click()
        self.assertTrue(self.search.expanded)
        self.assertTrue(self.search.hasFocus())
        self._phase(.4)
        self.assertGreater(self.search.width(), 44)
        self.assertEqual(self.search._ink, 0.)
        self.assertIs(self.window.search, identity)
        self.assertEqual(spy.count(), 0)

    def test_text_and_caret_fade_only_after_geometry_is_stable(self):
        self.search.set_expanded(True)
        self._phase(self.search.GEOMETRY_END)
        geometry = self.search.geometry()
        self.assertEqual(self.search._ink, 0.)
        self._phase(.86)
        self.assertEqual(self.search.geometry(), geometry)
        self.assertGreater(self.search._ink, 0.)
        self.assertLess(self.search._ink, 1.)
        self._phase(1.)
        self.assertEqual(self.search.geometry(), geometry)
        self.assertEqual(self.search.palette().color(QPalette.ColorRole.Text).alpha(), 255)
        self.assertEqual(self.window.progress_button.width(), 44)
        self.assertEqual(self.search.x(), 24 + 44 + 8)

    def test_query_changes_after_expansion_keep_native_text_and_placeholder_visible(self):
        self.search.set_expanded(True, focus=True)
        self._phase(1.)
        identity = self.search
        geometry = self.search.geometry()
        for query in ("Search song", "", "Another song", ""):
            with self.subTest(query=query):
                # Do not force another animation frame after changing text:
                # an already settled editor must repair its own native ink.
                self.search.setText(query)
                self.window._search_timer.stop()
                self.app.processEvents()
                self.assertEqual(self.search._phase, 1.)
                self.assertEqual(self.search.palette().color(QPalette.ColorRole.Text).alpha(), 255)
                self.assertEqual(self.search.palette().color(QPalette.ColorRole.PlaceholderText).alpha(), 255)
                self.assertEqual(self.search.geometry(), geometry)
                self.assertIs(self.window.search, identity)
                self._assert_native_ink_is_painted()

    def test_query_changes_during_text_fade_preserve_current_alpha_and_native_ink(self):
        self.search.set_expanded(True, focus=True)
        self._phase(.86)
        alpha = self.search.palette().color(QPalette.ColorRole.Text).alpha()
        for query in ("Typing during fade", ""):
            with self.subTest(query=query):
                self.search.setText(query)
                self.window._search_timer.stop()
                self.app.processEvents()
                self.assertEqual(self.search.palette().color(QPalette.ColorRole.Text).alpha(), alpha)
                self._assert_native_ink_is_painted()

    def test_settled_query_keeps_the_native_editing_caret_visible(self):
        previous_blink = self.app.cursorFlashTime()
        try:
            self.app.setCursorFlashTime(0)
            self.search.set_expanded(True, focus=True)
            self._phase(1.)
            self.search.setText("Search song")
            self.window._search_timer.stop()
            self.app.processEvents()
            self.assertTrue(self.search.hasFocus())
            cursor = self.search.cursorRect().intersected(self.search.rect())
            editing = self.search.grab().toImage()
            self.search.setReadOnly(True)
            without_caret = self.search.grab().toImage()
            cursor = self._physical_rect(editing, cursor)
            changed = sum(editing.pixel(x, y) != without_caret.pixel(x, y)
                          for y in range(cursor.top(), cursor.bottom() + 1)
                          for x in range(cursor.left(), cursor.right() + 1))
            self.assertGreater(changed, 0, "native editing caret is invisible")
        finally:
            self.search.setReadOnly(False)
            self.app.setCursorFlashTime(previous_blink)

    def _assert_native_ink_is_painted(self):
        image = self.search.grab().toImage()
        ink = self.search._ink
        try:
            # The same field/glass/icons remain; omit only native text/caret.
            self.search._ink = 0.
            blank = self.search.grab().toImage()
        finally:
            self.search._ink = ink
        area = self._physical_rect(image, self.search.rect().adjusted(18, 6, -116, -6))
        changed = sum(image.pixel(x, y) != blank.pixel(x, y)
                      for y in range(area.top(), area.bottom() + 1)
                      for x in range(area.left(), area.right() + 1))
        self.assertGreater(changed, 20, "native input text/placeholder is invisible")

    @staticmethod
    def _physical_rect(image, logical):
        scale = image.devicePixelRatio()
        return QRect(round(logical.x() * scale), round(logical.y() * scale),
                     round(logical.width() * scale), round(logical.height() * scale)).intersected(image.rect())

    def test_ring_reverses_the_same_animation_and_keeps_query_selection_and_undo(self):
        self.search.set_expanded(True, focus=True)
        self._phase(1.)
        QTest.keyClicks(self.search, "first")
        self.window._search_timer.stop()
        self.search.setSelection(1, 3)
        selection = self.search.selectedText()
        identity = self.search
        progress_identity = self.window.progress_button
        spy = QSignalSpy(self.window.searchRequested)
        QTest.mouseClick(self.window.progress_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.search.expanded)
        self.assertEqual(self.search._motion.startValue(), 1.)
        self._phase(.86)
        self.assertEqual(self.window.progress_button.width(), 44)
        self._phase(.3)
        self.assertEqual(self.search._ink, 0.)
        self._phase(0.)
        self.assertEqual(self.search.width(), 44)
        self.assertEqual(self.search.text(), "first")
        self.assertEqual(self.search.selectedText(), selection)
        self.assertIs(self.window.search, identity)
        self.assertIs(self.window.progress_button, progress_identity)
        self.assertEqual(spy.count(), 0)
        self.search.set_expanded(True, focus=True)
        self._phase(1.)
        self.assertEqual(self.search.selectedText(), selection)
        self.search.undo()
        self.assertEqual(self.search.text(), "")

    def test_rapid_reversal_starts_from_current_pose_and_tracks_resize(self):
        self.search.set_expanded(True)
        self._phase(.4)
        before = self.search.geometry()
        self.window.progress_button.click()
        self.assertEqual(self.search._motion.startValue(), .4)
        self.assertEqual(self.search.geometry(), before)
        self.search.set_expanded(True)
        self.assertEqual(self.search._motion.startValue(), .4)
        for width in (520, 960):
            self.window.resize(width, 500)
            self.assertEqual(self.search.geometry().right(), width - 25)
            self.assertLess(self.window.progress_button.geometry().right(), self.search.x())
        self._phase(1.)
        self.assertEqual(self.search.x(), 76)

    def test_real_ring_click_preserves_reversed_utf16_selection_and_cursor(self):
        self.search.set_expanded(True, focus=True)
        self._phase(1.)
        self.search.setText("a🎵歌曲b")
        self.window._search_timer.stop()
        self.search.setSelection(5, -4)
        state = self.search.selectedText(), self.search.cursorPosition(), self.search.selectionLength()
        QTest.mouseClick(self.window.progress_button, Qt.MouseButton.LeftButton)
        self._phase(0.)
        self.search.set_expanded(True, focus=True)
        self._phase(1.)
        self.assertEqual((self.search.selectedText(), self.search.cursorPosition(), self.search.selectionLength()), state)

    def test_ctrl_find_and_query_changes_reuse_editor_and_debounce(self):
        self.window._focus_empty_search()
        self.assertTrue(self.search.expanded)
        self.search.setText("query")
        self.assertTrue(self.window._search_timer.isActive())
        self._phase(1.)
        spy = QSignalSpy(self.window.searchRequested)
        QTest.keyClick(self.search, Qt.Key.Key_Return)
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), ["query"])
        self.assertFalse(self.window._search_timer.isActive())

    def test_hiding_settles_pending_motion_without_late_reopen(self):
        self.search.set_expanded(True)
        self._phase(.4)
        self.window.hide()
        self.assertEqual(self.search._phase, 1.)
        self.assertEqual(self.search._motion.state().value, 0)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.search._phase, 1.)
        self.window.progress_button.click()
        self.window.hide()
        self.assertEqual(self.search._phase, 0.)

    def test_disabled_input_cannot_expand_or_submit_through_icon(self):
        self.search.setEnabled(False)
        spy = QSignalSpy(self.window.searchRequested)
        self.search.set_expanded(True, focus=True)
        self.window.search_button.click()
        self.assertFalse(self.search.expanded)
        self.assertEqual(spy.count(), 0)
