"""Shared chrome samples stay bounded while their single outline fuses."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform
from PySide6.QtWidgets import QApplication

from local import music_control_glass as controls
from local.liquid_glass import GLASS_CAPSULE_MATERIAL_MAX_EDGE, GLASS_MATERIAL_MAX_EDGE


class ChromeCapsuleMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scene = QImage(480, 120, QImage.Format.Format_RGB32)
        with QPainter(self.scene) as p:
            p.fillRect(0, 0, 240, 120, QColor('#17859b'))
            p.fillRect(240, 0, 240, 120, QColor('#c87551'))
        self.bounds = QRectF(0, 0, 480, 120)

    def draw(self, renderer, rect, now=100., **kwargs):
        image = QImage(480, 120, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as p:
            renderer.paint(p, rect, scene=self.scene, scene_bounds=self.bounds,
                           ink=QColor('white'), now=now, role='chrome', **kwargs)
        return image

    def test_members_fuse_with_growing_neck_and_exact_capsule_endpoint(self):
        rects = [QRectF(40 + 40*i, 35, 32, 32) for i in range(4)]
        disconnected = controls.fused_control_path(rects, 0.)
        midway = controls.fused_control_path(rects, .5)
        capsule = controls.fused_control_path(rects, 1.)
        self.assertEqual(capsule.boundingRect(), QRectF(40, 35, 152, 32))
        self.assertFalse(disconnected.contains(QPointF(76, 51)))
        self.assertTrue(midway.contains(QPointF(76, 51)))
        self.assertTrue(capsule.contains(QPointF(76, 36)))
        self.assertFalse(midway.contains(QPointF(76, 36)))
        self.assertFalse(capsule.contains(QPointF(40, 35)))

    def test_capsules_have_independent_sampling_limit_and_visible_scene(self):
        self.assertEqual(GLASS_MATERIAL_MAX_EDGE, 224)
        self.assertEqual(GLASS_CAPSULE_MATERIAL_MAX_EDGE, 384)
        for kind, limit in (('circle', 224), ('capsule', 384), ('path', 384)):
            renderer = controls.GlassControlRenderer()
            with patch.object(controls, '_shade', wraps=controls._shade) as shade, \
                    patch.object(controls, '_sampling_lookup', wraps=controls._sampling_lookup) as lookup:
                image = self.draw(renderer, QRectF(130, 35, 180, 32), kind=kind)
                self.assertEqual(shade.call_args.args[7], limit)
                self.assertEqual(max(lookup.call_args.args[2:]), limit)
            if kind == 'capsule':
                left, right = image.pixelColor(150, 51), image.pixelColor(290, 51)
                self.assertGreater(left.blue(), left.red())
                self.assertGreater(right.red(), right.blue())

    def test_morph_geometry_is_throttled_but_outline_and_level_update_immediately(self):
        renderer = controls.GlassControlRenderer()
        rects = [QRectF(80 + 40*i, 35, 32, 32) for i in range(4)]
        with patch.object(controls, '_shade', wraps=controls._shade) as shade:
            for index in range(14):
                now = 100. + index * .03
                path = controls.fused_control_path(rects, index / 13.)
                image = self.draw(renderer, path.boundingRect(), now=now,
                                  path=path, kind='path', fusing=True)
                if index == 1:
                    # The cached sample may stay frozen, but its mask follows
                    # the live neck so it is already visible between members.
                    self.assertGreater(image.pixelColor(116, 51).alpha(), 0)
            self.assertEqual(shade.call_count, 4)
            self.draw(renderer, path.boundingRect(), now=100.391,
                      path=path, kind='path', fusing=True, level=85)
            self.assertEqual(shade.call_count, 5)
            self.assertEqual(renderer.patches['chrome'][0][3], 85)

    def test_static_capsule_ten_frames_match_and_level_has_no_stale_face(self):
        renderer = controls.GlassControlRenderer()
        rect = QRectF(130, 35, 180, 32)
        images = [self.draw(renderer, rect, now=100. + frame*.033, kind='capsule')
                  for frame in range(10)]
        self.assertTrue(all(image == images[0] for image in images))
        changed = self.draw(renderer, rect, now=100.298, kind='capsule', level=85)
        self.assertNotEqual(changed, images[0])
        self.assertEqual(renderer.versions['chrome'], 2)

    def test_first_capsule_endpoint_replaces_a_squeezed_path_without_stale_rims(self):
        renderer = controls.GlassControlRenderer()
        path = controls.fused_control_path([QRectF(80, 35, 8, 32),
                                           QRectF(91, 35, 8, 32)], .4)
        self.draw(renderer, path.boundingRect(), path=path, kind='path', fusing=True)
        rect = QRectF(80, 35, 152, 32)
        with patch.object(controls, '_shade', wraps=controls._shade) as shade:
            endpoint = self.draw(renderer, rect, now=100.001, kind='capsule', fusing=True)
            self.assertEqual(shade.call_count, 1)
        fresh = self.draw(controls.GlassControlRenderer(), rect, kind='capsule', fusing=True)
        self.assertEqual(endpoint, fresh)

    def test_fusing_distance_optimization_matches_hypot_reference_and_preserves_transport(self):
        original = controls.control_geometry

        def hypot_geometry(w, h, radius, dpr, spec, signature=None):
            if signature is None:
                return original(w, h, radius, dpr, spec, signature)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) + .5
            path = controls._restore_path(signature)
            nearest = np.full((h, w), np.inf, np.float32)
            for polygon in path.toSubpathPolygons(QTransform().scale(dpr, dpr)):
                points = list(polygon)
                for a, b in zip(points, points[1:] + points[:1]):
                    vx, vy = b.x() - a.x(), b.y() - a.y()
                    t = np.clip(((xx - a.x()) * vx + (yy - a.y()) * vy)
                                / max(1e-10, vx * vx + vy * vy), 0., 1.)
                    nearest = np.minimum(nearest, np.hypot(xx - a.x() - t * vx,
                                                         yy - a.y() - t * vy))
            mask = QImage(w, h, QImage.Format.Format_Grayscale8)
            mask.fill(0)
            with QPainter(mask) as p:
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.scale(dpr, dpr)
                p.fillPath(path, QColor('white'))
            cover = np.frombuffer(mask.constBits(), np.uint8).reshape(h, mask.bytesPerLine())[:, :w]
            dist = np.where(cover >= 128, -nearest, nearest)
            ny, nx = np.gradient(dist)
            norm = np.maximum(np.hypot(nx, ny), 1e-4)
            return controls._material_maps(dist, nx / norm, ny / norm, xx, yy, dpr, spec)

        def render(path, ratio, geometry, fusing):
            # Sampling maps also cache geometry-dependent refraction. Rebuild
            # them for each independent implementation of the distance field.
            controls._sampling_maps.cache_clear()
            controls._sampling_lookup.cache_clear()
            image = QImage(480 * ratio, 120 * ratio, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(ratio)
            image.fill(Qt.GlobalColor.transparent)
            with patch.object(controls, 'control_geometry', geometry), QPainter(image) as p:
                controls.GlassControlRenderer().paint(
                    p, path.boundingRect(), scene=self.scene, scene_bounds=self.bounds,
                    ink=QColor('white'), now=100., role='reference', path=path,
                    kind='path', fusing=fusing)
            return np.frombuffer(image.constBits(), np.uint8).astype(np.int16)

        self.addCleanup(controls._sampling_maps.cache_clear)
        self.addCleanup(controls._sampling_lookup.cache_clear)
        for ratio in (1, 2):
            for fusion in (.04, .24, .48, .72, .96):
                with self.subTest(dpr=ratio, fusion=fusion):
                    side, gap = 44. - fusion * 12., 12. - fusion * 10.
                    path = controls.fused_control_path(
                        [QRectF(40. + i * (side + gap), 35., side, side) for i in range(5)], fusion)
                    actual = render(path, ratio, original, True)
                    reference = render(path, ratio, hypot_geometry, True)
                    self.assertLessEqual(int(np.abs(actual - reference).max()), 1)
            # Glyph/transport paths keep their original hypot calculation.
            actual = render(path, ratio, original, False)
            reference = render(path, ratio, hypot_geometry, False)
            np.testing.assert_array_equal(actual, reference)

    def test_morph_signature_excludes_translation(self):
        renderer = controls.GlassControlRenderer()
        path = controls.fused_control_path([QRectF(80, 35, 32, 32), QRectF(120, 35, 32, 32)], .5)
        self.draw(renderer, path.boundingRect(), path=path, kind='path', fusing=True)
        first_geometry = renderer.patches['chrome'][0][0]
        moved = path.translated(11.375, 2.125)
        self.draw(renderer, moved.boundingRect(), now=100.11, path=moved, kind='path', fusing=True)
        self.assertEqual(renderer.patches['chrome'][0][0], first_geometry)

    def test_capsule_hover_does_not_expose_rectangular_sample_corners(self):
        renderer = controls.GlassControlRenderer()
        rect = QRectF(130, 35, 180, 32)
        image = self.draw(renderer, rect, kind='capsule', hover=1.)
        self.assertEqual(image.pixelColor(130, 35).alpha(), 0)
        self.assertGreater(image.pixelColor(220, 51).alpha(), 0)


if __name__ == '__main__':
    unittest.main()
