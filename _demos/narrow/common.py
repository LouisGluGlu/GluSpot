"""Standalone, fixture-only narrow music surface studies."""
from __future__ import annotations

import argparse
from collections import deque
import io
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

# Set these before importing any project module or constructing QApplication.
_sandbox = tempfile.TemporaryDirectory(prefix='gluspot-narrow-')
for _name in ('HOME', 'APPDATA', 'LOCALAPPDATA'):
    os.environ[_name] = _sandbox.name
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageFilter
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget
from local.music_control_glass import GlassControlRenderer
from local.music_surface_palette import artwork_surface_palette, background_role_color

COLORS = [(28, 52, 110), (196, 108, 52), (30, 74, 58), (222, 170, 186)]
LYRICS = [(i * 4.0, text) for i, text in enumerate([
    '在夜色中慢慢走', '把心事交给晚风', '那些没有说完的话', '会在星光里重逢',
    '让时间轻轻流过', '我们还在这条路上', '等天边亮起微光', '再一起迎接远方'])]
SONGS = [('夜航的信', '林间电台'), ('晚风经过', '陈小雨'), ('云与海之间', '南方来信'),
         ('星光落下来', '木子'), ('慢慢的旅程', '夏日回声'), ('下一站黎明', '白昼乐队'),
         ('你听见了吗', '无名旅人'), ('再见之前', '海边房间')]


def font(size, bold=False):
    f = QFont('Microsoft YaHei', size)
    f.setBold(bold)
    return f


def cover(rgb):
    image = QImage(256, 256, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(image)
    g = QLinearGradient(0, 0, 256, 256)
    c = QColor(*rgb)
    g.setColorAt(0, c.lighter(155)); g.setColorAt(.55, c); g.setColorAt(1, c.darker(210))
    p.fillRect(image.rect(), g)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(255, 255, 255, 55), 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(45, 45, 166, 166))
    p.drawEllipse(QRectF(67, 67, 122, 122))
    p.setFont(font(17, True)); p.setPen(QColor(255, 255, 255, 180))
    p.drawText(QRectF(0, 100, 256, 50), Qt.AlignmentFlag.AlignCenter, 'GLUSPOT')
    p.end()
    return image


def mix(a, b, t):
    return a + (b - a) * t


def rectmix(a, b, t):
    return QRectF(mix(a.x(), b.x(), t), mix(a.y(), b.y(), t),
                  mix(a.width(), b.width(), t), mix(a.height(), b.height(), t))


class Demo(QWidget):
    def __init__(self, variant, bench):
        super().__init__()
        self.variant = variant
        self.bench = bench
        self.samples = deque(maxlen=max(1, bench))
        self.frames = deque()
        self.mode = 'lyrics'; self.color_index = 0; self.paused = False; self.hints = True
        self.position = 5.0; self.last_tick = time.monotonic(); self.scroll = 0
        self.narrow = False; self.start_value = 0.; self.target = 0.; self.transition_start = 0.
        self.fade_from = 0.; self.duration = .25 if variant == 'B' else .4
        self.renderer = GlassControlRenderer(); self.text_cache = {}; self.hit = []
        self.scene = QImage(); self.scene_key = None
        self.toggles = [False, False, True, True]
        self.covers = [cover(c) for c in COLORS]
        self.backgrounds = []
        for image, rgb in zip(self.covers, COLORS):
            c = QColor(*rgb)
            palette = artwork_surface_palette(image, c, c.darker(150))
            self.backgrounds.append(background_role_color(palette.primary).darker(135))
        for _, line in LYRICS:
            for blur in range(5):
                self.lyric_image(line, 512, 23, blur)
        self.glass_images = {}
        self.prepare_glass()
        self.setMinimumSize(320, 400); self.resize(960, 540)
        self.setWindowTitle(self.title(0))
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(16)

    def title(self, fps):
        label = {'A': '封面飞入', 'B': '淡入切换', 'C': '无底图标'}[self.variant]
        return f'GluSpot 窄窗演示 · 方案{self.variant} {label} · {fps} fps'

    def progress(self):
        if not self.transition_start:
            return self.target
        t = min(1., (time.monotonic() - self.transition_start) / self.duration)
        return mix(self.start_value, self.target, 1 - (1 - t) ** 3)

    def resizeEvent(self, event):
        narrow = self.width() < 620
        if narrow != self.narrow:
            self.start_value = self.progress(); self.fade_from = float(self.narrow)
            self.target = float(narrow); self.narrow = narrow; self.transition_start = time.monotonic()
        super().resizeEvent(event)

    def tick(self):
        now = time.monotonic(); dt = min(.1, now - self.last_tick); self.last_tick = now
        if not self.paused:
            self.position = (self.position + dt) % (len(LYRICS) * 4)
        while self.frames and self.frames[0] < now - 1: self.frames.popleft()
        self.setWindowTitle(self.title(len(self.frames)))
        self.update()

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key.Key_Escape: self.close()
        elif k == Qt.Key.Key_L: self.mode = 'lyrics'
        elif k == Qt.Key.Key_P: self.mode = 'list'
        elif k == Qt.Key.Key_Tab: self.mode = 'list' if self.mode == 'lyrics' else 'lyrics'
        elif k == Qt.Key.Key_N: self.resize(360, 720)
        elif k == Qt.Key.Key_W: self.resize(960, 540)
        elif k == Qt.Key.Key_Left: self.resize(max(320, self.width() - 60), self.height())
        elif k == Qt.Key.Key_Right: self.resize(self.width() + 60, self.height())
        elif Qt.Key.Key_1 <= k <= Qt.Key.Key_4:
            self.color_index = k - Qt.Key.Key_1; self.renderer.clear(); self.prepare_glass()
        elif k == Qt.Key.Key_Space: self.paused = not self.paused
        elif k == Qt.Key.Key_H: self.hints = not self.hints
        self.update()

    def wheelEvent(self, event):
        if self.mode == 'list':
            self.scroll = max(0, min(400, self.scroll - event.angleDelta().y() / 120 * 55)); self.update()

    def mousePressEvent(self, event):
        for r, action in reversed(self.hit):
            if r.contains(event.position()):
                if isinstance(action, int): self.toggles[action] = not self.toggles[action]
                elif action in ('lyrics', 'list'): self.mode = action
                self.update(); return

    def text(self, p, rect, value, size, alpha=255, bold=False, align=None):
        p.setFont(font(size, bold)); p.setPen(QColor(255, 255, 255, int(alpha)))
        p.drawText(rect, align or (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), value)

    def art(self, p, r, image):
        path = QPainterPath(); path.addRoundedRect(r, min(8., r.width() / 8), min(8., r.height() / 8))
        p.save(); p.setClipPath(path); p.drawImage(r, image); p.restore()

    def lyric_image(self, text, width, size, blur):
        key = (text, width, size, blur)
        if key in self.text_cache: return self.text_cache[key]
        img = QImage(max(1, width), 120, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img); p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(font(size, True)); p.setPen(Qt.GlobalColor.white)
        p.drawText(QRectF(4, 4, width - 8, 110), Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop, text); p.end()
        if blur:
            data = QByteArray(); buf = QBuffer(data); buf.open(QIODevice.OpenModeFlag.WriteOnly); img.save(buf, 'PNG'); buf.close()
            pil = Image.open(io.BytesIO(bytes(data))).filter(ImageFilter.GaussianBlur(blur))
            out = io.BytesIO(); pil.save(out, format='PNG'); img = QImage.fromData(out.getvalue(), 'PNG')
        if len(self.text_cache) > 180: self.text_cache.clear()
        self.text_cache[key] = img
        return img

    def prepare_glass(self):
        # The backdrop is a solid color; cache the real material at two sizes.
        # Scaling these patches during reflow avoids rebuilding glass on every frame.
        self.glass_images.clear()
        if self.variant == 'C': return
        for width in (40, 72):
            for selected in (False, True):
                scene = QImage(width, 40, QImage.Format.Format_ARGB32_Premultiplied)
                scene.fill(self.backgrounds[self.color_index])
                result = QImage(width, 40, QImage.Format.Format_ARGB32_Premultiplied)
                result.fill(Qt.GlobalColor.transparent)
                p = QPainter(result)
                self.renderer.paint(p, QRectF(0, 0, width, 40), scene=scene,
                    scene_bounds=QRectF(0, 0, width, 40), ink=QColor('white'),
                    now=time.monotonic(), role=f'cached-{width}-{selected}',
                    kind='capsule', selected=selected)
                p.end(); self.glass_images[(width, selected)] = result

    def button(self, p, r, glyph, selected, role, scene, plain=False, action=None):
        if plain:
            if selected:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 48)); p.drawRoundedRect(r, r.height()/2, r.height()/2)
        else:
            p.drawImage(r, self.glass_images[(40 if role.startswith('bottom') else 72, selected)])
        self.text(p, r, glyph, 18, 245 if selected else 190, True, Qt.AlignmentFlag.AlignCenter)
        if action is not None: self.hit.append((r, action))

    def paintEvent(self, event):
        started = time.perf_counter(); self.frames.append(time.monotonic()); p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg = self.backgrounds[self.color_index]
        p.fillRect(self.rect(), bg)
        key = (self.width(), self.height(), self.color_index)
        if key != self.scene_key:
            self.scene = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
            self.scene.fill(bg); self.scene_key = key
        scene = self.scene
        self.hit = []; self.renderer.begin_frame(time.monotonic())
        v = self.progress()
        if self.variant == 'B' and self.transition_start and abs(v - self.target) > .001:
            t = min(1., (time.monotonic() - self.transition_start) / self.duration)
            p.save(); p.setOpacity(1 - t); self.draw_layout(p, self.fade_from, scene); p.restore()
            p.save(); p.setOpacity(t); self.draw_layout(p, self.target, scene); p.restore()
        else: self.draw_layout(p, v, scene)
        self.renderer.end_frame()
        if self.hints:
            self.text(p, QRectF(12, self.height()-23, self.width()-24, 17), 'L 歌词  P 列表  Tab 切换  N/W 宽窄  ←/→ 调宽  1–4 配色  空格 暂停  H 隐藏  Esc 退出', 7, 135)
        p.end(); self.samples.append((time.perf_counter() - started) * 1000)

    def draw_layout(self, p, v, scene):
        w, h = self.width(), self.height(); margin = 28
        large = min(300., max(130., h * .55), max(130., w * .38))
        wide_art = QRectF(margin, max(34., (h-large)/2 - 30), large, large)
        small_art = QRectF(margin, 35, 56, 56)
        art = rectmix(wide_art, small_art, v); self.art(p, art, self.covers[self.color_index])
        wide_title = QRectF(margin, wide_art.bottom()+10, large, 28)
        narrow_title = QRectF(98, 37, max(30, w-180), 28)
        title = rectmix(wide_title, narrow_title, v)
        self.text(p, title, '夜航的信', 13, 255, True)
        self.text(p, title.translated(0, 22), '林间电台', 10, 170)
        p.save(); p.setOpacity(v)
        self.text(p, QRectF(w-85, 44, 30, 30), '☆', 20, 230, align=Qt.AlignmentFlag.AlignCenter)
        self.text(p, QRectF(w-48, 44, 30, 30), '···', 18, 230, True, Qt.AlignmentFlag.AlignCenter)
        p.restore()
        wide_x = min(w-150., margin + large + 50.)
        x = mix(wide_x, margin, v); top = mix(45., 145., v)
        width = max(80., w - x - margin)
        if self.mode == 'lyrics': self.draw_lyrics(p, x, top, width, h - top - 35, v)
        else: self.draw_list(p, x, mix(35., 117., v), width, h, scene)

    def draw_lyrics(self, p, x, y, width, height, v):
        p.save(); p.setClipRect(QRectF(x, y, width, max(0, height)))
        size = 23; line_step = mix(76, 92, v)
        current = int(self.position // 4) % len(LYRICS)
        phase = (self.position % 4) / 4
        slide = 0 if phase < .8 else (1 - (1 - (phase-.8)/.2)**3) * line_step
        for offset in range(-1, 6):
            idx = (current + offset) % len(LYRICS)
            yy = y + 30 + offset * line_step - slide
            if yy < y - 110 or yy > y + height: continue
            distance = abs(offset); alpha = [1., .46, .26, .15, .08, .04][min(distance, 5)]
            image = self.lyric_image(LYRICS[idx][1], 512, size, min(4, max(0, distance)))
            p.setOpacity(alpha); p.drawImage(QPointF(x-4, yy), image)
        p.restore()

    def draw_list(self, p, x, top, width, h, scene):
        plain = self.variant == 'C'; gap = 9.; bw = (width - 3*gap)/4
        for i, glyph in enumerate(('⤨', '↻', '∞', '◉')):
            self.button(p, QRectF(x+i*(bw+gap), top, bw, 39), glyph, self.toggles[i], f'toggle{i}', scene, plain, i)
        self.text(p, QRectF(x, top+51, width, 24), '继续播放', 12, 245, True)
        self.text(p, QRectF(x, top+73, width, 20), '来自 夜航精选', 9, 165)
        list_top = top+101; controls_top = max(list_top+70, h-210)
        bottom = controls_top-12
        p.save(); p.setClipRect(QRectF(x, list_top, width, max(0, bottom-list_top)))
        for i, (name, artist) in enumerate(SONGS):
            yy = list_top + i*61-self.scroll
            if yy+52 < list_top or yy > bottom: continue
            opacity = min(1., max(0., (bottom-yy)/55))
            p.save(); p.setOpacity(opacity)
            self.art(p, QRectF(x, yy, 48, 48), self.covers[(i+1)%4])
            self.text(p, QRectF(x+60, yy+2, width-96, 23), name, 11, 245, True)
            self.text(p, QRectF(x+60, yy+25, width-96, 19), artist, 9, 165)
            self.text(p, QRectF(x+width-25, yy+5, 25, 38), '☰', 14, 120, align=Qt.AlignmentFlag.AlignCenter)
            p.restore()
        p.restore()
        self.bar(p, x, controls_top, width, .33)
        self.text(p, QRectF(x, controls_top+5, width/2, 20), '0:42', 8, 145)
        self.text(p, QRectF(x+width/2, controls_top+5, width/2, 20), '−3:18', 8, 145, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for i, glyph in enumerate(('◀◀', 'Ⅱ', '▶▶')):
            self.text(p, QRectF(x+i*width/3, controls_top+27, width/3, 46), glyph, 20 if i != 1 else 27, 240, True, Qt.AlignmentFlag.AlignCenter)
        self.text(p, QRectF(x, controls_top+84, 23, 25), '♪', 12, 150)
        self.bar(p, x+28, controls_top+95, width-56, .6)
        self.text(p, QRectF(x+width-24, controls_top+84, 24, 25), '♫', 12, 150)
        for i, (glyph, mode) in enumerate((('❝', 'lyrics'), ('◉', None), ('☷', 'list'))):
            r = QRectF(x + (i+.5)*width/3-20, controls_top+120, 40, 40)
            self.button(p, r, glyph, mode == self.mode, f'bottom{i}', scene, plain, mode)

    @staticmethod
    def bar(p, x, y, width, value):
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255,255,255,55)); p.drawRoundedRect(QRectF(x,y,width,4), 2, 2)
        p.setBrush(QColor(255,255,255,190)); p.drawRoundedRect(QRectF(x,y,width*value,4), 2, 2)


def run(variant):
    parser = argparse.ArgumentParser(); parser.add_argument('--grab', type=Path); parser.add_argument('--seconds', type=float, default=0); parser.add_argument('--bench', type=int, default=0)
    args = parser.parse_args(); app = QApplication(sys.argv[:1]); window = Demo(variant, max(0, args.bench)); window.show()
    if args.grab:
        args.grab.mkdir(parents=True, exist_ok=True)
        def save(name): window.grab().save(str(args.grab / f'narrow_{variant}_{name}.png'))
        def narrow(): window.resize(360,720)
        def list_mode(): window.mode = 'list'; window.update()
        def wide_again(): window.mode = 'lyrics'; window.resize(960,540)
        QTimer.singleShot(600, lambda: save('wide_lyrics'))
        QTimer.singleShot(700, narrow)
        QTimer.singleShot(1250, lambda: save('narrow_lyrics'))
        QTimer.singleShot(1300, list_mode)
        QTimer.singleShot(1600, lambda: save('narrow_list'))
        QTimer.singleShot(1700, wide_again)
        QTimer.singleShot(2250, narrow)
        for i in range(5): QTimer.singleShot(2250 + i*90, lambda i=i: save(f'transition_{i}'))
        QTimer.singleShot(2800, app.quit)
    elif args.seconds > 0:
        QTimer.singleShot(int(args.seconds*1000), app.quit)
    app.exec()
    if args.bench:
        values = sorted(window.samples)
        if values:
            print(f'方案 {variant}: paint frames={len(values)} median={statistics.median(values):.3f} ms p95={values[math.ceil(.95*len(values))-1]:.3f} ms', flush=True)
        else: print(f'方案 {variant}: no paint samples', flush=True)
