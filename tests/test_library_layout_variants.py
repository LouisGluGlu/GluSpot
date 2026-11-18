from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TrackData
from api.online_music import AccountSnapshot, OnlineSong
from local.music_library_window import LibraryCollection, MusicLibraryWindow


VARIANTS = ("sidebar", "listen", "compact")
SIZES = ((960, 540), (433, 550), (391, 508))


class LibraryLayoutVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080),
                                 geometry=lambda: QRect(0, 0, 1920, 1080))
        screen_patch = patch.object(QGuiApplication, "screenAt", return_value=screen)
        screen_patch.start()
        self.addCleanup(screen_patch.stop)
        env_patch = patch.dict(os.environ, {"GLUSPOT_LIBRARY_VARIANT": "classic"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.window = self.new_window()
        self.account = AccountSnapshot("netease", True, "library-variants", "LouisHerm", state="verified")
        self.catalog = (
            LibraryCollection("daily", "daily", "每日推荐", "fixture:daily", group="for_you"),
            LibraryCollection("fm", "fm", "私人漫游", "fixture:fm", group="for_you"),
            LibraryCollection("recommended", "playlist", "午后灵感", "fixture:recommended", "r1", group="recommended"),
            LibraryCollection("personal", "playlist", "我的收藏", "fixture:personal", "p1", group="personal"),
        )
        self.track = TrackData("Trippin", "Kara Marni", (), ColorToken(75, 113, 140),
                               ColorToken(142, 103, 125), (), track_id="fixture:track",
                               duration_seconds=172, position_seconds=96)
        self.art = QImage(128, 128, QImage.Format.Format_RGB32)
        self.art.fill(QColor("#78a1b8"))

    def new_window(self):
        window = MusicLibraryWindow()
        window.set_background_context({"v2_music_flow_intensity": 0.}.get, lambda *args: None)
        self.addCleanup(self.dispose, window)
        return window

    @classmethod
    def dispose(cls, window):
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.app.processEvents()

    def prepare(self, variant, size=(960, 540)):
        w = self.window
        w.set_library_variant(variant)
        w.resize(*size)
        w.set_account(self.account)
        w.set_collections(self.catalog)
        w.set_now_playing(self.track)
        for item in self.catalog:
            w.set_artwork(item.key, self.art)
        w.show()
        self.app.processEvents()
        return w

    def test_environment_defaults_invalid_fallback_and_runtime_selection(self):
        for value, expected in (("", "sidebar"), ("unknown", "sidebar"), ("classic", "classic"),
                                ("sidebar", "sidebar"), ("listen", "listen"), ("compact", "compact")):
            with self.subTest(value=value), patch.dict(os.environ, {"GLUSPOT_LIBRARY_VARIANT": value}):
                self.assertEqual(self.new_window().library_variant, expected)
        with patch.dict(os.environ):
            os.environ.pop("GLUSPOT_LIBRARY_VARIANT", None)
            self.assertEqual(self.new_window().library_variant, "sidebar")
        self.window.set_library_variant(" SIDEBAR ")
        self.assertEqual(self.window.library_variant, "sidebar")
        with self.assertRaises(ValueError):
            self.window.set_library_variant("not-a-layout")
        self.assertEqual(self.window.library_variant, "sidebar")

    def test_classic_keeps_folded_catalog_and_original_search_progress(self):
        w = self.prepare("classic")
        self.assertEqual([group.group_key for group in w._groups], ["for_you", "recommended", "personal"])
        self.assertFalse(w._rows)
        self.assertFalse(w._shelves)
        self.assertTrue(w.progress_button.isVisible())
        self.assertFalse(w.search.expanded)
        self.assertTrue(all(button.isHidden() for button in w._variant_view.buttons.values()))
        self.assertTrue(w._variant_view.rail_source.isHidden())
        self.assertTrue(w.expand_button.isVisible())

    def test_variant_round_trip_preserves_editor_catalog_artwork_account_and_track(self):
        w = self.prepare("sidebar")
        editor = w.search
        w.search.setText("Kara Marni")
        w._search_timer.stop()
        result = LibraryCollection("result", "song", "Trippin", "", song=OnlineSong("netease", "s1", "Trippin", "Kara Marni"))
        w.set_search_results((result,))
        keys = {key: image.cacheKey() for key, image in w._artwork.items()}
        search = QSignalSpy(w.searchRequested)
        playback = QSignalSpy(w.collectionRequested)
        source = QSignalSpy(w.sourceSwitchRequested)
        for variant in ("listen", "compact", "classic", "sidebar"):
            with self.subTest(variant=variant):
                w.set_library_variant(variant)
                self.assertIs(w.search, editor)
                self.assertEqual(editor.text(), "Kara Marni")
                self.assertIs(w._account, self.account)
                self.assertEqual(w._catalog, self.catalog)
                self.assertEqual(w.collections, (result,))
                self.assertIs(w._now_playing, self.track)
                self.assertEqual({key: image.cacheKey() for key, image in w._artwork.items()}, keys)
                self.assertEqual([row.collection for row in w._rows], [result])
        self.assertEqual((search.count(), playback.count(), source.count()), (0, 0, 0))

    def test_returning_to_classic_restores_original_chrome_and_editor_margins(self):
        w = self.prepare("classic")
        controls = (w.search, w.progress_button, w.expand_button, w.account_name_label,
                    w.source_button, w.logout_button, w.close_button)
        for query in ("", "Kara Marni"):
            w.search.setText(query)
            w._search_timer.stop()
            w.search.set_expanded(bool(query))
            w.search.settle()
            w._layout()
            baseline = [control.geometry() for control in controls]
            margins = w.search.textMargins()
            self.assertEqual((margins.left(), margins.right()), (16, 100 if query else 52))
            for variant in VARIANTS:
                with self.subTest(query=query, variant=variant):
                    w.set_library_variant(variant)
                    w.set_library_variant("classic")
                    self.assertTrue(all(control.isVisible() for control in controls))
                    self.assertEqual([control.geometry() for control in controls], baseline)
                    self.assertEqual(w.search.textMargins(), margins)
                    self.assertEqual(w.search.expanded, bool(query))
                    self.assertEqual(w.source_button.login_style, "classic")
                    self.assertTrue(all(button.isHidden() for button in w._variant_view.buttons.values()))
                    self.assertTrue(w._variant_view.rail_source.isHidden())
                    self.assertTrue(w._variant_view.play_button.isHidden())

    def test_requested_sizes_render_loaded_loading_and_empty_with_contained_controls(self):
        w = self.window
        for variant in VARIANTS:
            for size in SIZES:
                for state in ("loaded", "loading", "empty"):
                    with self.subTest(variant=variant, size=size, state=state):
                        w.set_account(AccountSnapshot("netease", state != "empty", f"{variant}-{size}-{state}", "LouisHerm"))
                        w.set_library_variant(variant)
                        w.resize(*size)
                        w.set_collections(self.catalog if state == "loaded" else ())
                        w.set_now_playing(self.track if state == "loaded" else None)
                        w.set_loading(state == "loading")
                        w.show()
                        self.app.processEvents()
                        view = w._variant_view
                        bounds = QRectF(w.rect())
                        for rect in (view.cluster, view.player, view.pane, QRectF(w.scroll.geometry())):
                            self.assertTrue(bounds.contains(rect), (bounds, rect))
                            self.assertGreater(rect.width(), 0)
                            self.assertGreater(rect.height(), 0)
                        for button in (w.source_button, w.logout_button, w.close_button, *view.buttons.values(),
                                       view.rail_source, view.play_button, w.empty_action, w.recovery_action):
                            if button.isVisible():
                                self.assertTrue(w.rect().contains(button.geometry()), (button.accessibleName(), button.geometry()))
                        if w.search.isVisible():
                            self.assertTrue(w.rect().contains(w.search.geometry()))
                            self.assertGreater(w.search.width(), 44)
                            self.assertFalse(w.search.geometry().intersects(w.now_playing_button.geometry()))
                        self.assertTrue(w.progress_button.isHidden())
                        if variant == "sidebar":
                            # No skeleton in the refreshed sidebar: its heading's dots run instead.
                            self.assertFalse(w._catalog_skeleton.isVisible())
                            if state == "loading":
                                self.assertTrue(view._dots_timer.isActive())
                        else:
                            self.assertEqual(w._catalog_skeleton.isVisible(), state == "loading")
                        self.assertEqual(w.empty_action.isVisible(), state == "empty")
                        self.assertEqual(w.now_playing_button.isVisible(), state == "loaded" and variant != "sidebar")
                        self.assertEqual(w.now_playing_island.isVisible(), variant == "sidebar")
                        frame = w.grab().toImage()
                        self.assertFalse(frame.isNull())
                        self.assertEqual(frame.size(), w.size())

    def test_sections_and_integrated_search_keep_cancellation_authority(self):
        for variant in ("sidebar", "compact"):
            with self.subTest(variant=variant):
                w = self.prepare(variant, (391, 508))
                if w.search.text():
                    w.search.clear()
                    w._search_timer.stop()
                view = w._variant_view
                for section in ("recommended", "personal", "for_you"):
                    QTest.mouseClick(view.buttons[section], Qt.MouseButton.LeftButton)
                    self.assertEqual(view.section, section)
                    self.assertTrue(view.buttons[section].isChecked())
                    self.assertEqual([row.collection for row in w._rows], list(w._catalog_groups()[section]))
                requested = QSignalSpy(w.searchRequested)
                QTest.mouseClick(view.buttons["search"], Qt.MouseButton.LeftButton)
                self.assertTrue(w.search.isVisible())
                self.assertTrue(w.search.hasFocus())
                self.assertEqual(requested.count(), 0)
                w.search.setText("Trippin")
                w._search_timer.stop()
                returned = QSignalSpy(w.backRequested)
                QTest.mouseClick(view.buttons["personal"], Qt.MouseButton.LeftButton)
                self.assertEqual(returned.count(), 1)
                self.assertEqual(w.search.text(), "Trippin")

    def test_platform_cover_and_search_results_preserve_existing_signal_contracts(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                w = self.prepare(variant)
                platform = QSignalSpy(w.sourceSwitchRequested)
                playback = QSignalSpy(w.nowPlayingRequested)
                w.source_button.click()
                if variant == "sidebar":
                    w._variant_view.rail_source.click()
                w.now_playing_button.click()
                self.assertEqual(platform.count(), 2 if variant == "sidebar" else 1)
                self.assertEqual(playback.count(), 1)
                original = w._account
                self.assertIs(original, self.account)
                result = LibraryCollection("song-result", "song", "Trippin", "fixture:result",
                                           song=OnlineSong("netease", "s1", "Trippin", "Kara Marni"))
                w.search.setText("Trippin")
                w._search_timer.stop()
                w.set_search_results((result,))
                w.set_artwork(result.key, self.art)
                row = w._rows[0]
                self.assertEqual(row.cover.image, w._artwork[result.key])
                queued = QSignalSpy(w.queueNextRequested)
                row.queue_button.click()
                self.assertEqual(queued.count(), 1)
                self.assertEqual(queued.at(0), [result])
                opened = QSignalSpy(w.collectionRequested)
                row.cover.click()
                self.assertEqual(opened.count(), 1)
                collection, mode, global_rect = opened.at(0)
                self.assertEqual((collection, mode), (result, "default"))
                self.assertTrue(global_rect.contains(row.cover.mapToGlobal(row.cover.rect().center())))
                self.assertIs(w._opening_cover, row.cover)
                row.cover.click()
                self.assertEqual(opened.count(), 1)
                w.finish_open_transition()
                self.assertTrue(w.content.isEnabled())
                returned = QSignalSpy(w.backRequested)
                w.clear_search_button.click()
                self.assertEqual(returned.count(), 1)
                self.assertEqual(w.search.text(), "Trippin")
                self.assertIs(w._account, original)
                w.search.clear()
                w._search_timer.stop()

    def test_controller_clear_restores_selected_catalog_and_integrated_search_chrome(self):
        w = self.window
        # The real controller owns Back cancellation and clears the editor.
        w.backRequested.connect(w.search.clear)
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                self.prepare(variant, (391, 508))
                view = w._variant_view
                if variant in {"sidebar", "compact"}:
                    view.buttons["personal"].click()
                    view.buttons["search"].click()
                else:
                    w.find_shortcut.activated.emit()
                w.search.setText("Trippin")
                w._search_timer.stop()
                result = LibraryCollection("result", "song", "Trippin", "",
                                           song=OnlineSong("netease", "s1", "Trippin", "Kara Marni"))
                w.set_search_results((result,))
                self.assertEqual([row.collection for row in w._rows], [result])
                self.assertTrue(w.clear_search_button.isVisible())
                returned = QSignalSpy(w.backRequested)
                w.clear_search_button.click()
                self.assertEqual(returned.count(), 1)
                self.assertEqual(w.search.text(), "")
                self.assertEqual(w.collections, self.catalog)
                if variant in {"sidebar", "compact"}:
                    self.assertEqual(view.section, "personal")
                    self.assertTrue(view.buttons["personal"].isChecked())
                    self.assertFalse(w.search.isVisible())
                else:
                    self.assertTrue(w.search.isVisible())
                    self.assertEqual([shelf.group_key for shelf in w._shelves], ["for_you", "recommended", "personal"])
                if variant in {"sidebar", "compact"}:
                    self.assertEqual([row.collection for row in w._rows], [self.catalog[-1]])
                    self.assertTrue(w._rows[0].isVisible())

    def test_platform_status_reuses_login_states_without_upcoming_dot(self):
        w = self.prepare("sidebar")
        for account, expected in ((self.account, "connected"), (AccountSnapshot("netease"), "signed_out"),
                                  (AccountSnapshot("netease", state="expired"), "error"),
                                  (AccountSnapshot("spotify", True, "unsupported"), "upcoming")):
            with self.subTest(expected=expected):
                w.set_account(account)
                for button in (w.source_button, w._variant_view.rail_source):
                    self.assertEqual(button.source_status, expected)
                    self.assertEqual(button.status_rect().isEmpty(), expected == "upcoming")
                    self.assertIn(button.connection_status, button.accessibleName())

    def test_visible_artwork_requests_deduplicate_and_reach_collection_covers(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                w = self.prepare(variant)
                w._artwork.clear()
                w._artwork_pending.clear()
                requested = QSignalSpy(w.artworkNeeded)
                w._request_visible_artwork()
                self.assertGreater(requested.count(), 0)
                requests = [requested.at(index) for index in range(requested.count())]
                self.assertIn(["daily", "fixture:daily"], requests)
                self.assertEqual(len({key for key, url in requests}), len(requests))
                w._request_visible_artwork()
                self.assertEqual(requested.count(), len(requests))
                w.set_artwork("daily", self.art)
                self.assertNotIn("daily", w._artwork_pending)
                if w._shelves:
                    cover = next(cover for shelf in w._shelves for item, cover, label in shelf.tiles if item.key == "daily")
                else:
                    cover = next(row.cover for row in w._rows if row.collection.key == "daily")
                self.assertEqual(cover.image, w._artwork["daily"])

    def test_find_reveals_existing_search_and_enter_submits_once(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                w = self.prepare(variant, (391, 508))
                if w.search.text():
                    w.search.clear()
                    w._search_timer.stop()
                editor = w.search
                requested = QSignalSpy(w.searchRequested)
                w.find_shortcut.activated.emit()
                self.assertIs(w.search, editor)
                self.assertTrue(editor.isVisible())
                self.assertTrue(editor.hasFocus())
                self.assertEqual(requested.count(), 0)
                QTest.keyClicks(editor, "Kara")
                self.assertTrue(w._search_timer.isActive())
                QTest.keyClick(editor, Qt.Key.Key_Return)
                self.assertEqual(requested.count(), 1)
                self.assertEqual(requested.at(0), ["Kara"])
                self.assertFalse(w._search_timer.isActive())

    def test_earlier_sidebar_uses_login_rail(self):
        from local.login_layout_variants import login_variant_layout
        for size in SIZES:
            w = self.prepare("sidebar", size)
            w._variant_view.toggle_refresh()
            self.assertFalse(w._variant_view.refresh)
            view = w._variant_view
            expected = login_variant_layout("sidebar", *size, (True,) * 4)
            self.assertEqual(view.rail.topLeft(), expected.rail.topLeft())
            self.assertTrue(w.source_button.isVisible())
            view.toggle_refresh()

    def test_refreshed_sidebar_header_rail_footer_and_denser_rows(self):
        from local.library_layout_variants import REFRESH_HEADER, REFRESH_STRIDE
        for size in SIZES:
            w = self.prepare("sidebar", size)
            view = w._variant_view
            self.assertTrue(view.refresh)
            # Only the window's close stays at the top right; the header is 60 px.
            self.assertFalse(w.source_button.isVisible())
            self.assertEqual(w.close_button.geometry().right(), w.width() - 19)
            self.assertEqual(view.rail.top(), REFRESH_HEADER + 4)
            self.assertEqual(view.pane.top(), view.rail.top())
            # Account actions sit together in the rail footer, under the rule.
            for button in (view.rail_source, w.logout_button):
                self.assertTrue(button.isVisible())
                self.assertTrue(view.rail.contains(QRectF(button.geometry())))
                self.assertGreater(button.y(), view.rule.y())
            sections = tuple(view.buttons.values())
            self.assertLess(max(b.geometry().bottom() for b in sections), view.rule.y())
            rows = [row for row in w._rows if not row.card]
            for a, b in zip(rows, rows[1:]):
                self.assertEqual(b.y() - a.y(), REFRESH_STRIDE)

    def test_now_playing_bar_takes_the_window_shape_pose(self):
        from local.library_layout_variants import bar_mode_for
        self.assertEqual(bar_mode_for(960, 540), "row")       # wide and short: one line
        self.assertEqual(bar_mode_for(1280, 820), "tall")     # wide and tall: the expanded island
        self.assertEqual(bar_mode_for(433, 550), "pill")      # narrow and short: the free island
        self.assertEqual(bar_mode_for(433, 900), "tall")
        for size, mode in (((960, 540), "row"), ((433, 550), "pill"), ((1280, 820), "tall")):
            w = self.prepare("sidebar", size)
            island = w.now_playing_island
            self.assertEqual(island.bar_mode, mode)
            self.assertEqual(island.height(), island.bar_height(mode))
            if mode == "pill":
                self.assertLessEqual(island.geometry().bottom(), w.height() - 14)
            else:
                # The bar is the window's bottom edge, side to side.
                self.assertEqual((island.geometry().left(), island.geometry().bottom()), (1, w.height() - 2))
                self.assertEqual(island.width(), w.width() - 2)
                self.assertGreater(island.merged_bottom_radius, 0)
            island.grab()
            if mode == "pill":
                self.assertEqual(island.music_button_rects, {})
                self.assertLessEqual(island.width(), island.PILL_WIDTH)
            else:
                self.assertEqual(len(island.music_button_rects), 3)
                self.assertTrue(all(island.rect().contains(r.toAlignedRect())
                                    for r in island.music_button_rects.values()))
            if mode == "row":
                # Title column, lyric in the middle, transport on the right.
                controls = min(r.left() for r in island.music_button_rects.values())
                self.assertLess(island.music_cover_rect.right(), controls)

    def test_pull_past_the_top_shows_the_dots_and_reloads_only_that_section(self):
        from local.library_layout_variants import PULL_TO_REFRESH, REFRESH_MIN_S
        w = self.prepare("sidebar", (433, 550))
        view = w._variant_view
        view.select_section("recommended")
        self.app.processEvents()
        self.assertTrue(view.pull_active())
        self.assertFalse(view.dots_rect.isEmpty())
        self.assertTrue(view.pane.contains(view.dots_rect))
        reloads = QSignalSpy(w.sectionRefreshRequested)
        view.set_pull(.5)
        self.assertEqual(view.pull_amount, .5)
        self.assertEqual(reloads.count(), 0)
        view.start_refresh()
        self.assertEqual(reloads.count(), 1)
        self.assertEqual(reloads.at(0), ["recommended"])
        self.assertIsNotNone(view.refreshing_since)
        view.start_refresh()                             # one reload at a time
        self.assertEqual(reloads.count(), 1)
        view.refreshing_since -= REFRESH_MIN_S + .1      # and the section is not loading
        view._tick_dots()
        self.assertIsNone(view.refreshing_since)
        self.assertEqual(view.pull_amount, 0.0)
        self.assertGreater(PULL_TO_REFRESH, 120)         # more than one wheel notch

    def test_a_row_shows_once_its_cover_is_in_and_rows_keep_their_order(self):
        from local.library_layout_variants import APPEAR_STAGGER_S
        w = self.prepare("sidebar", (433, 550))
        view = w._variant_view
        view._shown_keys.clear()
        view._appear_at.clear()
        view._waiting_since.clear()
        view.select_section("recommended")
        self.app.processEvents()
        rows = list(w._rows)
        self.assertTrue(rows)
        self.assertTrue(all(getattr(row, "waiting_since", None) is not None for row in rows))
        self.assertTrue(all(row.graphicsEffect() is not None and row.graphicsEffect().opacity() == 0.0
                            for row in rows))
        # The first row's cover has not come yet: nothing after it shows either.
        first = rows[0]
        w._artwork.pop(first.collection.key, None)
        first.cover.loading, first.cover.image = True, QImage()
        view._tick_appear()
        self.assertTrue(all(getattr(row, "appear_at", None) is None for row in rows))
        # It arrives: rows appear in order, one after another.
        w.set_artwork(first.collection.key, self.art)
        view._tick_appear()
        starts = [row.appear_at for row in rows if getattr(row, "appear_at", None) is not None]
        self.assertEqual(len(starts), len(rows))
        self.assertEqual(starts, sorted(starts))
        if len(starts) > 1:
            self.assertAlmostEqual(starts[1] - starts[0], APPEAR_STAGGER_S, delta=.01)
        # Coming back to a section that has appeared shows it at once.
        for row in rows:
            row.appear_at = 0.0
        view._tick_appear()
        view.select_section("personal")
        view.select_section("recommended")
        self.assertTrue(all(row.graphicsEffect() is None for row in w._rows))

    def test_scrolled_list_fades_under_a_layered_heading(self):
        w = self.prepare("sidebar", (433, 550))
        view = w._variant_view
        bar = w.scroll.verticalScrollBar()
        bar.setRange(0, 400)
        bar.setValue(0)
        self.assertIsNone(w.scroll.graphicsEffect())             # at the top nothing changes
        self.assertFalse(view.heading_rule.isEmpty())
        self.assertLessEqual(view.heading_rule.bottom(), w.scroll.geometry().top())
        bar.setValue(120)
        effect = w.scroll.graphicsEffect()
        self.assertIsNotNone(effect)
        self.assertTrue(getattr(effect, "_gluspot_heading", False))
        self.assertTrue(view._heading_layered())
        bar.setValue(0)
        self.assertIsNone(w.scroll.graphicsEffect())

    def test_selection_plate_slides_to_the_chosen_section(self):
        w = self.prepare("sidebar")
        view = w._variant_view
        view.select_section("for_you")
        view._plate_motion.stop()
        view._sync_plate(False)
        start = QRectF(view.plate)
        view.select_section("personal")
        self.assertEqual(view._plate_motion.state(), view._plate_motion.State.Running)
        view._move_plate(.5)
        self.assertGreater(view.plate.y(), start.y())
        self.assertLess(view.plate.y(), view.buttons["personal"].y())
        view._move_plate(1.)
        self.assertEqual(view.plate, QRectF(view.buttons["personal"].geometry()))

    def test_refreshed_background_follows_the_playback_theme(self):
        w = self.prepare("sidebar")
        settings = {"v2_music_ambience_style": "sea_level"}
        w.set_background_context(settings.get)
        w._background_palette = w._background_palette or SimpleNamespace(
            ambient_colors=(QColor("#2b4f9a"), QColor("#c84a3a")), ambient_weights=(.6, .4),
            primary=QColor("#2b4f9a"), secondary=QColor("#c84a3a"))
        w._background_key = None
        self.assertEqual(w._song_field_style(), "sea_level")
        w._update_background()
        frame = w._background_frame
        self.assertLessEqual(max(frame.width(), frame.height()), 256)
        corner, centre = frame.pixelColor(1, 1), frame.pixelColor(frame.width() // 2, frame.height() // 2)
        self.assertNotEqual(corner.rgb(), centre.rgb())      # a vignette, not a flat fill
        settings["v2_music_ambience_style"] = "solid_album"
        w._background_key = None
        w._update_background()
        frame = w._background_frame
        self.assertEqual(frame.pixelColor(1, 1).rgb(), frame.pixelColor(frame.width() - 2, frame.height() - 2).rgb())
        settings["v2_music_ambience_style"] = "system"
        self.assertEqual(w._song_field_style(), "")

    def test_playlist_page_back_key_sits_in_its_toolbar_row(self):
        w = self.prepare("sidebar", (433, 550))
        view = w._variant_view
        playlist = next(item for item in self.catalog if item.kind == "playlist")
        view.select_section("recommended")
        w.open_playlist_page(playlist)
        self.app.processEvents()
        page = w.playlist_page
        self.assertEqual(page.crumb, "推荐歌单")
        self.assertTrue(QRectF(page.geometry()).intersects(view.pane))
        self.assertLessEqual(page.back_button.geometry().bottom(), page.TOOLBAR)
        self.assertGreaterEqual(page.scroll.geometry().top(), page.TOOLBAR)
        self.assertEqual(page.canvas.cover_rect().top(), page.canvas.header_top())
        self.assertLess(page.canvas.header_top(), 20)
        view.toggle_refresh()
        self.assertEqual(page.crumb, "")
        self.assertEqual(page.back_button.text(), "‹ 返回")
        view.toggle_refresh()
        w.close_playlist_page()

    def test_bar_pose_holds_near_a_threshold_while_resizing(self):
        from local.library_layout_variants import BAR_SLACK, bar_mode_for
        self.assertEqual(bar_mode_for(640 - BAR_SLACK + 4, 540, "row"), "row")
        self.assertEqual(bar_mode_for(640 - BAR_SLACK - 4, 540, "row"), "pill")
        self.assertEqual(bar_mode_for(960, 700 - BAR_SLACK + 4, "tall"), "tall")
        self.assertEqual(bar_mode_for(960, 640, "tall"), "row")
        w = self.prepare("sidebar", (960, 540))
        view = w._variant_view
        self.assertTrue(view._rail_is_wide(960))
        # Height alone never folds the rail.
        w.resize(960, 400)
        self.app.processEvents()
        self.assertEqual(view.rail.width(), 156)

    def test_open_playlist_page_hides_the_section_heading(self):
        w = self.prepare("sidebar", (433, 550))
        view = w._variant_view
        view.select_section("personal")
        playlist = next(item for item in self.catalog if item.kind == "playlist")
        painted = []
        import local.library_layout_variants as variants
        original = variants._type

        def record(painter, rect, text, *args, **kwargs):
            painted.append(text)
            return original(painter, rect, text, *args, **kwargs)

        with patch.object(variants, "_type", side_effect=record):
            w.grab()
            self.assertIn("个人歌单", painted)
            painted.clear()
            w.open_playlist_page(playlist)
            self.app.processEvents()
            w.grab()
        self.assertNotIn("个人歌单", painted)
        w.close_playlist_page()

    def test_library_colour_follows_the_sea_colour_change(self):
        w = self.prepare("sidebar")
        w.set_background_context({"v2_music_ambience_style": "sea_level"}.get)
        w._background_palette = w._background_palette or SimpleNamespace(
            ambient_colors=(QColor("#2b4f9a"), QColor("#e0a020")), ambient_weights=(.6, .4),
            primary=QColor("#2b4f9a"), secondary=QColor("#e0a020"))
        frames = {}
        for mix in (0.0, 0.5, 1.0):
            with patch.object(MusicLibraryWindow, "_sea_change_mix", return_value=mix):
                w._background_key = None
                w._update_background()
                frames[mix] = w._background_frame.pixelColor(128, 80).rgb()
                state_key = w._background_key
                self.assertFalse(w._update_background())      # same state: nothing re-rendered
                self.assertEqual(w._background_key, state_key)
        self.assertEqual(len(set(frames.values())), 3)          # before, between, after differ
        # A resize keeps the same small field (no re-render while dragging).
        with patch.object(MusicLibraryWindow, "_sea_change_mix", return_value=1.0):
            w._background_key = None
            w._update_background()
            before = w._background_frame.cacheKey()
            w.resize(700, 500)
            self.app.processEvents()
            self.assertEqual(w._background_frame.cacheKey(), before)

    def test_row_bar_keeps_three_keys_and_dims_an_unavailable_previous(self):
        w = self.prepare("sidebar", (960, 540))
        island = w.now_playing_island
        self.assertEqual(island.bar_mode, "row")
        with patch.object(type(island), "_supported_music_transport_commands",
                          lambda self, commands: [c for c in commands if c != "music:previous"]):
            island.grab()
        self.assertNotIn("music:previous", island.music_button_rects)
        self.assertEqual(len(island.music_button_rects), 2)
        # The cover sits the same distance from the top, bottom and left edges.
        cover = island.music_cover_rect
        self.assertAlmostEqual(cover.top() - 1, island.height() - 1 - cover.bottom(), delta=1)
        # Merged into the bottom edge, the cover lines up with the rail.
        self.assertAlmostEqual(island.x() + cover.left(), w._variant_view.rail.x(), delta=1.5)

    def test_hovered_and_selected_rows_never_share_a_colour(self):
        from local.row_states import hover_colors, selected_color
        for dark in (True, False):
            wash, frame = hover_colors(dark)
            self.assertNotEqual(wash.rgba(), selected_color(dark).rgba())
            self.assertGreater(frame.alpha(), 0)            # hover is an outline; selected has none

    def test_sidebar_uses_login_rail_and_entire_mini_bar_opens_playback(self):
        from local.login_layout_variants import login_variant_layout
        for size in SIZES:
            w = self.prepare("sidebar", size)
            view = w._variant_view
            from local.library_layout_variants import RAIL_NARROW, RAIL_WIDE
            self.assertEqual(view.rail.x(), 16)
            self.assertIn(view.rail.width(), (RAIL_WIDE, RAIL_NARROW))
            self.assertLess(view.rail.bottom(), view.player.top())
            island = w.now_playing_island
            if island.bar_mode == "pill":
                self.assertAlmostEqual(view.player.center().x(), w.width() / 2, delta=1)
            else:
                self.assertEqual((view.player.left(), view.player.right()), (1, w.width() - 1))
            self.assertLess(view.rail_source.geometry().bottom(), view.player.top())
            self.assertTrue(view.rail.contains(QRectF(view.rail_source.geometry())))
            buttons = tuple(view.buttons.values())
            self.assertEqual(len({b.x() for b in buttons}), 1)
            self.assertTrue(all(a.geometry().bottom() < b.y() for a, b in zip(buttons, buttons[1:])))
            opened = QSignalSpy(w.nowPlayingRequested)
            island = w.now_playing_island
            island.grab()
            commands = QSignalSpy(island.commandRequested)
            for key, rect in island.music_button_rects.items():
                QTest.mouseClick(island, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
                self.assertEqual(commands.at(commands.count() - 1), [key.split(":", 1)[1]])
                self.assertTrue(island.rect().contains(rect.toAlignedRect()))
            # The free island (pill) has no transport; the whole pill opens playback.
            self.assertEqual(commands.count(), 0 if island.bar_mode == "pill" else 3)
            self.assertEqual(opened.count(), 0)
            QTest.mouseClick(island, Qt.MouseButton.LeftButton, pos=island.music_cover_rect.center().toPoint())
            self.assertEqual(opened.count(), 1)
            w.set_now_playing(None)
            self.assertTrue(view.player_button.isHidden())
            QTest.mouseClick(island, Qt.MouseButton.LeftButton)
            self.assertEqual(opened.count(), 1)

    def test_sidebar_status_pulse_updates_only_dots_and_stops_when_hidden(self):
        w = self.prepare("sidebar")
        view = w._variant_view
        self.assertTrue(view.status_timer.isActive())
        # The refreshed header has no platform key; the rail's one pulses.
        with patch.object(view.rail_source, "update") as update:
            view.advance_status_pulse(view.status_started_at + 1.)
            update.assert_called_once_with(view.rail_source.status_rect())
        self.assertEqual(view.rail_source.status_opacity, 1.)
        w.set_account(AccountSnapshot("netease", state="expired"))
        self.assertFalse(view.status_timer.isActive())
        w.set_account(self.account)
        self.assertTrue(view.status_timer.isActive())
        w.hide()
        self.assertFalse(view.status_timer.isActive())

    def test_sidebar_no_artwork_url_uses_platform_fallback_without_endless_skeleton(self):
        w = self.prepare("sidebar")
        w.set_collections((LibraryCollection("daily-no-cover", "daily", "每日推荐", "", group="for_you"),))
        cover = w._rows[0].cover
        self.assertTrue(cover.image.isNull())
        self.assertFalse(cover.loading)
        self.assertFalse(w._artwork_skeleton_timer.isActive())

    def test_pagination_and_catalog_loading_remain_explicit_and_independent(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                w = self.prepare(variant)
                if variant == "sidebar":
                    w._variant_view.select_section("recommended")
                requested = QSignalSpy(w.moreRequested)
                w.set_has_more(True)
                self.assertTrue(w.more_button.isVisible())

                w.more_button.click()
                w.more_button.click()
                self.assertEqual(requested.count(), 1)
                w.set_has_more(True)
                w.more_button.click()
                self.assertEqual(requested.count(), 2)
                w.set_catalog_loading("recommended", True)
                # The refreshed sidebar shows the skeleton only on a section's
                # first load; a later page fills in row by row.
                self.assertEqual(w._catalog_skeleton.isVisible(), variant != "sidebar")
                self.assertFalse(w.more_button.isVisible())
                self.assertTrue(w.search.isEnabled())
                w.set_catalog_loading("recommended", False)
                self.assertFalse(w._catalog_skeleton.isVisible())
                self.assertTrue(w.more_button.isVisible())

    def test_sidebar_loading_and_pagination_belong_to_visible_section(self):
        w = self.prepare("sidebar")
        w.set_has_more(True, groups={"personal": True, "recommended": False})
        w.set_catalog_loading("personal", True)
        self.assertFalse(w._catalog_skeleton.isVisible())
        self.assertFalse(w.more_button.isVisible())
        w._variant_view.select_section("personal")
        self.assertFalse(w._catalog_skeleton.isVisible())      # it already has rows
        w.set_catalog_loading("personal", False)
        self.assertTrue(w.more_button.isVisible())
        w._variant_view.select_section("recommended")
        self.assertFalse(w.more_button.isVisible())
        requested = QSignalSpy(w.moreRequested)
        w.scroll.verticalScrollBar().setRange(0, 500)
        w._scroll_changed(500)
        self.assertEqual(requested.count(), 0)


if __name__ == "__main__":
    unittest.main()
