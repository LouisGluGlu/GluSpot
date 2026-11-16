"""Opt-in music-page ink using the existing layout, audio clock and seek path."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import math

from PIL import Image, ImageFilter
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QTransform

from local.apple_lyric_style import (
    blur_layers, character_spans, displayed_focus, first_unit_start,
    line_appearance, row_timing, unit_progress,
)
from local.lyric_unit_timing import _aligned_offsets
from local.music_lyric_motion import LogicalLineSpringChain
from local.music_lyric_presentation import INTRO_COUNTDOWN_TEXT


class MusicAppleLyricsMixin:
    def _apple_lyrics_enabled(self) -> bool:
        """The blurred Apple column is retired.

        Line spacing and per-word reading stay on the existing painter.
        The old ``v2_music_apple_lyrics_enabled`` switch is ignored.
        """
        return False

    def on_apple_lyric_style_changed(self) -> None:
        """Retire geometry/hits, retaining the accepted player clock."""
        self._cancel_lyric_resize()
        self._song_layout_key = self._song_layout_build_key = None
        self._song_layout_build = None
        self._lyric_hit_rects.clear()
        self._lyric_word_hit_rects.clear()
        self._pressed_lyric_index = self._pressed_lyric_word = None
        self._pressed_lyric_track_key = None
        self._lyric_raster_cache.clear()
        self._lyric_raster_cache_bytes = 0
        self._lyric_blur_cache.clear()
        self._lyric_blur_cache_bytes = 0
        self._lyric_blur_prewarm_queue.clear()
        self._pending_glow_prewarm = self._pending_reading_prewarm = None
        self._apple_fragment_cache = {}
        self._apple_timing_key = None
        self._obo_advanced_focus_index = self._obo_advanced_focus_pose = None
        self._obo_advanced_handoff_last_at = None
        self._logical_line_spring = LogicalLineSpringChain()
        self._lyric_spring_active = False
        self._clear_lyric_browse()
        self.update()

    def _apple_timing(self, lines, timed):
        units, kinds = self._display_timed_units, self._display_timed_kinds
        duration = float(getattr(self.owner, "media_duration_seconds", 0.0) or 0.0)
        key = (self._display_lyrics_cache_key, tuple(timed), tuple(lines), duration)
        if getattr(self, "_apple_timing_key", None) != key:
            starts = tuple(first_unit_start(start, units[i] if i < len(units) else (),
                kinds[i] if i < len(kinds) else "line") for i, (start, _text) in enumerate(timed))
            plans = tuple(row_timing(self._primary_timed_lyric_text(text),
                units[i] if i < len(units) else (), kinds[i] if i < len(kinds) else "line",
                start=start, end=starts[i + 1] if i + 1 < len(starts) else duration or None)
                for i, (start, text) in enumerate(timed))
            self._apple_timing_key, self._apple_timing_value = key, (starts, plans)
        return self._apple_timing_value

    def _apple_remember_blur(self, key, image):
        size = int(image.sizeInBytes())
        if size <= self.LYRIC_BLUR_CACHE_MAX_BYTES:
            self._lyric_blur_cache[key] = (image, size)
            self._lyric_blur_cache_bytes += size
            while (len(self._lyric_blur_cache) > self.LYRIC_BLUR_CACHE_MAX_ENTRIES
                   or self._lyric_blur_cache_bytes > self.LYRIC_BLUR_CACHE_MAX_BYTES):
                _image, old_size = self._lyric_blur_cache.pop(next(iter(self._lyric_blur_cache)))
                self._lyric_blur_cache_bytes -= old_size
        return image

    def _apple_cached_blur(self, source, radius, ink):
        if radius <= 0.0:
            return source
        key = ("apple", int(source.cacheKey()), radius, ink.rgba())
        cached = self._lyric_blur_cache.pop(key, None)
        if cached is not None:
            self._lyric_blur_cache[key] = cached
            return cached[0]
        rgba = source.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()))
        alpha = pixels.getchannel("A").filter(ImageFilter.GaussianBlur(radius * source.devicePixelRatioF()))
        colored = Image.new("RGBA", pixels.size, (ink.red(), ink.green(), ink.blue(), 0))
        colored.putalpha(alpha)
        result = QImage(colored.tobytes(), rgba.width(), rgba.height(), rgba.width() * 4,
                        QImage.Format.Format_RGBA8888).copy()
        result.setDevicePixelRatio(source.devicePixelRatioF())
        return self._apple_remember_blur(key, result)

    def _apple_raster(self, text, font, width, height, layout, ink):
        source, padding = self._lyric_raster(text, font, width, height, layout, ink)
        # Large experimental blur packs also need transparent edge room.
        knobs = self._apple_lyric_knobs
        reserve = math.ceil(knobs.blur_px * (1.0 + knobs.blur_distance_growth * knobs.blur_distance_limit) * 2.0)
        extra = max(0.0, reserve - padding)
        if extra == 0.0:
            return source, padding
        key = ("apple-padding", int(source.cacheKey()), reserve)
        cached = self._lyric_blur_cache.get(key)
        if cached is not None:
            return cached[0], padding + extra
        dpr = source.devicePixelRatioF()
        image = QImage(source.width() + math.ceil(extra * 2 * dpr),
                       source.height() + math.ceil(extra * 2 * dpr), source.format())
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.drawImage(QPointF(extra, extra), source)
        return self._apple_remember_blur(key, image), padding + extra

    def _apple_fragments(self, text, layout, plan, offsets):
        """Cache unit fragments in actual Qt wrap coordinates, including UTF-16."""
        if plan is None or offsets is None or len(offsets) != len(text) + 1:
            return ()
        key = (text, layout.font().toString(), tuple((layout.lineAt(i).textStart(),
            layout.lineAt(i).textLength(), layout.lineAt(i).width()) for i in range(layout.lineCount())),
            plan.display_units, offsets)
        cache = getattr(self, "_apple_fragment_cache", None)
        if cache is None:
            self._apple_fragment_cache = cache = {}
        if key in cache:
            return cache[key]
        aligned = _aligned_offsets(plan.text, plan.display_units)
        result = []
        if aligned is not None:
            for index, unit in enumerate(plan.display_units):
                start, end = bisect_left(offsets, aligned[index]), bisect_left(offsets, aligned[index + 1])
                fragments, total = [], 0.0
                for ordinal in range(layout.lineCount()):
                    visual = layout.lineAt(ordinal)
                    first = len(self._utf16_slice(text, 0, visual.textStart()))
                    last = first + len(self._utf16_slice(text, visual.textStart(), visual.textLength()))
                    left, right = max(first, start), min(last, end)
                    if right <= left:
                        continue
                    x0 = visual.cursorToX(self._utf16_offset(text, left))[0]
                    x1 = visual.cursorToX(self._utf16_offset(text, right))[0]
                    box = QRectF(min(x0, x1), visual.y(), abs(x1 - x0), visual.height())
                    fragments.append((box, total))
                    total += box.width()
                if total > 0.0:
                    result.append((unit, tuple(fragments), total, max(1, len(character_spans(unit.text)))))
        cache[key] = tuple(result)
        while len(cache) > self.LYRIC_RASTER_CACHE_MAX_ENTRIES:
            cache.pop(next(iter(cache)))
        return cache[key]

    def _paint_apple_layer(self, painter, text, font, width, item, origin, ink,
                           appearance, position, *, plan=None, offsets=None, translation=False):
        layout, height = item[:2]
        source, padding = self._apple_raster(text, font, width, height, layout, ink)
        layers = blur_layers(appearance.blur, self._apple_lyric_knobs)
        base = appearance.translation_alpha if translation else appearance.unread_alpha
        difference = 0.0 if translation else appearance.read_alpha - base
        parent_opacity = painter.opacity()
        raster_origin = origin - QPointF(padding, padding)
        fragments = self._apple_fragments(text, layout, plan, offsets) if difference > 0.0 else ()
        # Resting complete/unstarted rows need only their cached glyph image.
        # A paused frame in a gap creates no coverage bitmap.
        if fragments and all(unit_progress(unit, position) >= 1.0 for unit, *_rest in fragments):
            base, fragments = appearance.read_alpha, ()
        elif fragments and all(unit_progress(unit, position) <= 0.0 for unit, *_rest in fragments):
            fragments = ()
        mask = None
        if fragments:
            mask = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
            mask.setDevicePixelRatio(source.devicePixelRatioF())
            mask.fill(Qt.GlobalColor.transparent)
            with QPainter(mask) as mask_painter:
                mask_painter.translate(padding, padding)
                for unit, pieces, width_sum, count in fragments:
                    progress = unit_progress(unit, position)
                    if progress <= 0.0:
                        continue
                    feather = self._apple_lyric_knobs.feather_chars * width_sum / count
                    for box, preceding in pieces:
                        if progress >= 1.0:
                            mask_painter.fillRect(box, Qt.GlobalColor.white)
                        elif feather <= 0.0:
                            filled = QRectF(box)
                            filled.setWidth(max(0.0, min(box.width(), width_sum * progress - preceding)))
                            mask_painter.fillRect(filled, Qt.GlobalColor.white)
                        else:
                            front = box.left() + (width_sum + feather) * progress - preceding
                            gradient = QLinearGradient(front - feather, 0.0, front, 0.0)
                            gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
                            gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
                            mask_painter.fillRect(box, gradient)
            coverage = mask
            mask = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
            mask.setDevicePixelRatio(source.devicePixelRatioF())
            mask.fill(QColor(255, 255, 255, round(base * 255)))
            with QPainter(mask) as mask_painter:
                # SourceOver gives base + coverage * (read - base). Plus with
                # painter opacity clamps before interpolation in Qt raster,
                # which would leave completed glyphs below full brightness.
                mask_painter.setOpacity(difference / max(1e-9, 1.0 - base))
                mask_painter.drawImage(QPointF(), coverage)
        for radius, weight in layers:
            raster = self._apple_cached_blur(source, radius, ink)
            if mask is not None:
                read = raster.copy()
                with QPainter(read) as layer:
                    layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                    layer.drawImage(QPointF(), mask)
                painter.setOpacity(parent_opacity * weight)
                painter.drawImage(raster_origin, read)
            else:
                painter.setOpacity(parent_opacity * base * weight)
                painter.drawImage(raster_origin, raster)
        painter.setOpacity(parent_opacity)

    def _paint_apple_existing_active(self, painter, text, font, width, item, plan, position, lyric_now):
        """Existing float, glow and wrap spring on the Apple-spaced active row."""
        layout = item[0]
        progress = 0.0
        if plan is not None:
            sampled = plan.reading_progress(position)
            if sampled is not None:
                progress = sampled
        # No wrap bounce: the lines of a wrapped lyric stay still while the
        # reading front crosses from one to the next.
        line_offsets = (0.0,) * max(1, layout.lineCount())
        row = QRectF(0, 0, width, max(item[1], layout.boundingRect().height()))
        self._paint_lyric_scatter_glow(painter, text, font, width, layout, QPointF(), line_offsets, progress)
        self._paint_wrapped_lifted_lyric(
            painter, row, text, font, layout, progress, line_offsets=line_offsets,
        )

    def _paint_apple_lyric_column(self, painter, rect, *, lines, timed, visual_layers,
            lyric_now, text_left, text_width, surface_scale, lyric_transform, lyric_cover,
            painted_cover, cover_lyrics_layout, cover_fraction, unobscured_path,
            previous, transition, outgoing=False):
        knobs = self._apple_lyric_knobs
        position = float(self._lyric_motion.last_position or 0.0)
        starts, plans = self._apple_timing(lines, timed)
        # Preparation inserts middle-gap countdown rows at the preceding end.
        # They are decoration, not the next sung onset. Keep their ink/slot but
        # never let that synthetic boundary initiate the sheet's scroll.
        focus_rows = tuple(i for i, text in enumerate(lines) if i == 0 or text != INTRO_COUNTDOWN_TEXT)
        focus_slots = {index: slot for slot, index in enumerate(focus_rows)}
        sheet_focus = displayed_focus(tuple(starts[i] for i in focus_rows if i < len(starts)), position, knobs)
        slot = min(len(focus_rows) - 1, math.floor(sheet_focus))
        first, following = focus_rows[slot], focus_rows[min(len(focus_rows) - 1, slot + 1)]
        focus = first + (following - first) * (sheet_focus - slot)
        anchor, _index, manual = self._lyric_view_anchor(round(focus), len(lines), lyric_now)
        if manual:
            focus = anchor
            slot = max(0, bisect_right(focus_rows, anchor) - 1)
            first, following = focus_rows[slot], focus_rows[min(len(focus_rows) - 1, slot + 1)]
            sheet_focus = slot + (anchor - first) / max(1, following - first)
        def appearance_at(index):
            ordinal = focus_slots.get(index, bisect_left(focus_rows, index))
            distance = ordinal - sheet_focus
            if index not in focus_slots:
                distance = max(1.0, abs(distance))
            return line_appearance(distance, knobs)
        self._apple_focus = focus
        self._lyric_spring_active = False
        self._last_lyric_emphasis_mode = "none"
        self._translation_focus_entry = None
        primary = [layer.primary_text or " " for layer in visual_layers]
        translations = tuple(layer.translation_text or "" for layer in visual_layers)
        asides = tuple(layer.parenthetical_text or "" for layer in visual_layers)
        aside_translations = tuple(layer.parenthetical_translation_text or "" for layer in visual_layers)
        font = self._surface_font(self.LYRIC_PIXEL_SIZE * surface_scale * (.74 if cover_lyrics_layout else 1.), QFont.Weight.Bold)
        secondary = self._surface_font(font.pixelSize() * knobs.translation_size_ratio, QFont.Weight.Normal)
        aside_font = self._surface_font(font.pixelSize() * knobs.parenthetical_size_ratio, QFont.Weight.Bold)
        if self._lyric_resize_capturing:
            layouts, _tops = self._lyric_resize_last_layouts
        else:
            layouts, _tops = self._song_lyric_layouts(primary, font, text_width,
                font.pixelSize() * knobs.block_gap_ratio, defer=len(primary) > 48,
                secondary_lines=translations, secondary_font=secondary,
                parenthetical_lines=asides, parenthetical_translation_lines=aside_translations,
                parenthetical_font=aside_font, translation_gap=font.pixelSize() * knobs.translation_gap_ratio)
            if not self._lyric_resize_probe:
                self._lyric_resize_last_layouts = (layouts, _tops)
        top, height = rect.top(), rect.height()
        self._lyric_anchor_fraction = knobs.anchor_fraction
        if cover_lyrics_layout:
            top, height = lyric_cover.top() + lyric_cover.height() * .42, lyric_cover.height() * .55
        elif not self._content_layout_is_narrow() and self._lyric_resize_reuse_scale is None:
            fraction = float(self._width_layout_fraction or 0.0)
            self._lyric_anchor_fraction = knobs.anchor_fraction * fraction + (
                (self._wide_layout_g(rect).axis - rect.top()) / max(1.0, height)) * (1.0 - fraction)
        clip = QRectF(text_left, top, text_width, height)
        self._lyric_clip_rect = lyric_transform.mapRect(clip)
        self._lyric_ink_clip_rect = lyric_transform.mapRect(rect)
        bleed = max(self._lyric_blur_bleed(2), knobs.blur_px * (
            1.0 + knobs.blur_distance_growth * knobs.blur_distance_limit) * 2.0)
        damage = lyric_transform.mapRect(QRectF(text_left - bleed, rect.top(),
            text_width + 2.0 * bleed, rect.height())).intersected(QRectF(self.rect()))
        self._lyric_animation_rect = (painted_cover if cover_lyrics_layout else damage).toAlignedRect()
        self._active_lyric_animation_rect = QRect(self._lyric_animation_rect)
        self._lyric_hit_rects, self._lyric_word_hit_rects = {}, {}
        self._lyric_word_hit_track_key = (self._visual_track_identity(), str(getattr(self.owner, "media_source_app", "") or ""))
        if not layouts:
            # Deferred long-song shaping still uses the existing loading ink.
            self._paint_lyric_loading(painter, self._lyric_clip_rect, lyric_now, 1.0, force=True)
            return
        if self._song_layout_build is not None:
            translations, aside_translations = self._song_layout_key[4], self._song_layout_key[7]
        translation_texts = tuple("\n".join(filter(None, pair)) for pair in zip(translations, aside_translations))
        self._translation_known_tokens = {(i, text) for i, text in enumerate(translation_texts) if text}
        poses = tuple(self._translation_layer_pose(i, text, lyric_now) for i, text in enumerate(translation_texts))
        tops, heights, cursor = [], [], 0.0
        for index, (_layout, row_height) in enumerate(layouts):
            tops.append(cursor)
            aside = self._song_parenthetical_layouts[index]
            aside_translation = self._song_parenthetical_translation_layouts[index]
            extra = (aside[1] if aside is not None else 0.0)
            if aside_translation is not None:
                extra += (aside_translation[2] + aside_translation[1] - row_height - extra) * poses[index][0]
            heights.append(row_height + extra + self._song_translation_extra_heights[index] * poses[index][0])
            cursor += heights[-1] + font.pixelSize() * knobs.block_gap_ratio
        lower, upper = first, following
        fraction = sheet_focus - slot
        def center(index):
            return tops[index] + layouts[index][1] * appearance_at(index).scale * .5
        camera = top + height * self._lyric_anchor_fraction - (center(lower) * (1.0 - fraction) + center(upper) * fraction)
        self._apple_row_tops = tuple(tops)
        self._apple_camera = camera
        ink = self._sung_lyric_color()
        opacity = 1.0 if cover_lyrics_layout else 1.0 - cover_fraction
        if previous and not outgoing:
            opacity *= max(0.0, min(1.0, (transition - .35) / .65))
        slide = 0.0 if cover_lyrics_layout else self._lyric_horizontal_slide(cover_fraction, rect.right(), text_left)
        slack = float(getattr(self, "_lyric_edge_slack", 0.0) or 0.0)
        painter.save()
        painter.setOpacity(opacity)
        if cover_lyrics_layout:
            painter.setClipPath(self._cover_clip_path(painted_cover, self._album_art_radius(painted_cover.width())), Qt.ClipOperation.IntersectClip)
        else:
            painter.setClipRect(self._lyric_ink_clip_rect, Qt.ClipOperation.IntersectClip)
            if unobscured_path is not None:
                painter.setClipPath(unobscured_path, Qt.ClipOperation.IntersectClip)
        painter.setWorldTransform(lyric_transform, True)
        painter.translate(slide, slack)
        for index in range(len(layouts)):
            appearance = appearance_at(index)
            y = tops[index] + camera
            bottom = (tops[index + 1] if index + 1 < len(tops) else cursor) + camera
            if appearance.read_alpha <= 0.0 or y > rect.bottom() or bottom < rect.top():
                continue
            layout, row_height = layouts[index]
            row_transform = QTransform()
            row_transform.translate(text_left + slide, y + slack)
            row_transform.scale(appearance.scale, appearance.scale)
            hit_transform = row_transform * lyric_transform
            inverse, _ok = hit_transform.inverted()
            hit_clip = inverse.mapRect(self._lyric_clip_rect.intersected(painted_cover if cover_lyrics_layout else rect))
            painter.save()
            painter.translate(text_left, y)
            painter.scale(appearance.scale, appearance.scale)
            if primary[index] == INTRO_COUNTDOWN_TEXT:
                if index + 1 < len(timed) and timed[index][0] <= position < timed[index + 1][0]:
                    painter.setOpacity(painter.opacity() * appearance.read_alpha)
                    self._paint_intro_countdown(painter, QRectF(0, 0, text_width, row_height), font,
                        (position - timed[index][0]) / max(1e-9, timed[index + 1][0] - timed[index][0]))
                painter.restore()
                continue
            plan = plans[index] if index < len(plans) else None
            source = timed[index][1] if index < len(timed) else lines[index]
            offsets = self._visible_primary_source_offsets(source, visual_layers[index])
            # The focused sentence keeps the existing per-word float, glow and
            # wrap spring. Apple spacing, lead and the inactive-line veil stay.
            if index == first and primary[index] != INTRO_COUNTDOWN_TEXT:
                self._paint_apple_existing_active(
                    painter, primary[index], font, text_width, layouts[index],
                    plan, position, lyric_now,
                )
            else:
                self._paint_apple_layer(painter, primary[index], font, text_width, layouts[index], QPointF(),
                    ink, appearance, position, plan=plan, offsets=offsets)
            if visual_layers[index].role_tag:
                painter.save()
                painter.setOpacity(painter.opacity() * appearance.unread_alpha)
                self._paint_lyric_role_tag(painter, visual_layers[index].role_tag, font, QPointF())
                painter.restore()
            if not outgoing and index < len(timed) and opacity > 0.0:
                hit = hit_transform.mapRect(QRectF(0, 0, text_width, heights[index])).intersected(self._lyric_clip_rect)
                if cover_lyrics_layout:
                    hit = hit.intersected(painted_cover)
                if hit.isValid():
                    self._lyric_hit_rects[index] = hit
                self._populate_lyric_word_hit_rects(index, primary[index], source, visual_layers[index], layout,
                    plan.source_units if plan is not None else (), QRectF(0, 0, text_width, row_height), (),
                    hit_transform, hit_clip, 0.0, visible_source_offsets=offsets)
            for item, text, layer_font, translated in (
                    (self._song_parenthetical_layouts[index], asides[index], aside_font, False),
                    (self._song_parenthetical_translation_layouts[index], aside_translations[index], secondary, True),
                    (self._song_secondary_layouts[index], translations[index], secondary, True)):
                if item is None or not text or translated and poses[index][1] <= 0.0:
                    continue
                painter.save()
                if translated:
                    painter.setOpacity(painter.opacity() * poses[index][1])
                self._paint_apple_layer(painter, text, layer_font, text_width, item, QPointF(0, item[2]),
                    ink, appearance, position, plan=None if translated else plan,
                    offsets=None if translated else self._parenthetical_source_offsets(source, visual_layers[index], text),
                    translation=translated)
                if not translated and not outgoing and index < len(timed) and opacity > 0.0:
                    self._populate_lyric_word_hit_rects(index, text, source, visual_layers[index], item[0],
                        plan.source_units if plan is not None else (), QRectF(0, item[2], text_width, item[1]), (),
                        hit_transform, hit_clip, 0.0,
                        visible_source_offsets=self._parenthetical_source_offsets(source, visual_layers[index], text))
                painter.restore()
            painter.restore()
        painter.restore()
