"""Fixture-only GluSpot narrow-window studies; no player or user data."""
import argparse
import io
from PIL import Image, ImageFilter
from collections import deque
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

_SANDBOX = tempfile.TemporaryDirectory(prefix='gluspot-narrow2-')
for key in ('HOME', 'APPDATA', 'LOCALAPPDATA'):
    os.environ[key] = _SANDBOX.name
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget
from local.music_control_glass import ControlFeedback, GlassControlRenderer
from local.music_surface_palette import artwork_surface_palette, background_role_color
from local.audio_output_icons import paint_audio_output_status
from local.music_surface_layout import paint_content_layout_glyph
from local.music_mode_controls import paint_presentation_glyph
from local.music_source_icons import paint_music_source_icon
from local.personal_dj_icon import paint_personal_dj_icon
from local.music_surface_window import MusicSurfaceWindow
from local.music_surface_background import AlbumFlowBackgroundRenderer

COLORS = [(28,52,110),(196,108,52),(30,74,58),(222,170,186)]
SONGS = [('夜航的信','林间电台'),('晚风经过','陈小雨'),('云与海之间','南方来信'),('星光落下来','木子'),('慢慢的旅程','夏日回声'),('下一站黎明','白昼乐队'),('你听见了吗','无名旅人'),('再见之前','海边房间')]
LYRICS = ['在夜色中慢慢走','把心事交给晚风','那些没有说完的话','会在星光里重逢','让时间轻轻流过','我们还在这条路上','等天边亮起微光','再一起迎接远方']
LABELS = {'A':'悬浮玻璃岛','B':'并入头部','C':'宽窗四角'}

def rects(variant, w, h):
    """Interactive rectangles in narrow mode, shared with layout checker."""
    result = {'back':(12,12,32,32)}
    for i, key in enumerate(('audio','layout','mode','translation','window')):
        result[key] = (w-196+i*38,12,32,32)
    if variant == 'A':
        y=h-80
        for i,key in enumerate(('previous','play','next')): result[key]=(28+i*38,y+16,30,32)
        result.update(favorite=(w-142,y+16,32,32), playlist_add=(w-110,y+16,32,32), dj=(w-64,y+16,32,32))
    elif variant == 'B':
        for i,key in enumerate(('previous','play','next')): result[key]=(w/2-100+i*85,124,30,36)
        result.update(favorite=(w-142,h-56,32,32), playlist_add=(w-110,h-56,32,32), dj=(w-64,h-56,32,32))
    else:
        for i,key in enumerate(('previous','play','next')): result[key]=(16+i*44,h-56,30,32)
        result.update(favorite=(w-142,h-56,32,32), playlist_add=(w-110,h-56,32,32), dj=(w-64,h-56,32,32))
    return result

def content_rect(variant, w, h):
    top = 188 if variant == 'B' else 124
    bottom = h - (80 if variant == 'A' else 96 if variant == 'C' else 64)
    return (24, top, w - 48, max(0, bottom - top))

def time_rects(variant, w, h):
    y = 157 if variant == 'B' else h - 89
    return [(24, y, 60, 13), (w - 84, y, 60, 13)] if variant != 'A' else []

def font(px, bold=False):
    f=QFont('Microsoft YaHei'); f.setPixelSize(round(px)); f.setBold(bold); return f

def cover(rgb):
    im=QImage(256,256,QImage.Format.Format_ARGB32_Premultiplied); im.fill(Qt.GlobalColor.transparent)
    p=QPainter(im); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g=QLinearGradient(0,0,256,256); c=QColor(*rgb); g.setColorAt(0,c.lighter(150)); g.setColorAt(.55,c); g.setColorAt(1,c.darker(210)); p.fillRect(im.rect(),g)
    p.setPen(QPen(QColor(255,255,255,60),2)); p.drawEllipse(QRectF(45,45,166,166)); p.drawEllipse(QRectF(66,66,124,124)); p.setFont(font(17,True)); p.drawText(QRectF(0,100,256,50),Qt.AlignmentFlag.AlignCenter,'GLUSPOT'); p.end(); return im

def blend(a,b,t): return a+(b-a)*t
def mixrect(a,b,t): return QRectF(*(blend(x,y,t) for x,y in zip((a.x(),a.y(),a.width(),a.height()),(b.x(),b.y(),b.width(),b.height()))))

class Demo(QWidget):
    def __init__(self, variant, bench):
        super().__init__(); self.variant=variant; self.bench=bench; self.samples=deque(maxlen=max(1,bench)); self.anim_samples=deque(maxlen=max(1,bench)); self.frames=deque(); self.mode='lyrics'; self.track=0; self.color=0; self.paused=False; self.dj=True; self.favorite=False; self.hints=True; self.pos=4.8; self.scroll=0; self.narrow=False; self.start=0.; self.target=0.; self.transition=0.; self.last=time.monotonic(); self.last_activity=self.last; self.header=1.; self.hovered=None; self.hits={}; self.feedback=ControlFeedback(); self.renderer=GlassControlRenderer(); self.covers=[cover(c) for c in COLORS]; self.backgrounds=[]; self.bg_cache={}; self.lyric_cache={}; self.row_cache={}; self.tile_cache={}; self.frame_budget=0; self.first_paint=None; self.press_samples=deque(maxlen=max(1,bench)); self.palettes=[]; self.island_refresh=0.
        for image,rgb in zip(self.covers,COLORS):
            c=QColor(*rgb); palette=artwork_surface_palette(image,c,c.darker(150)); self.backgrounds.append(background_role_color(palette.primary)); self.palettes.append(palette)
        # Warm expensive color fields and text masks before the first displayed frame.
        original=self.color
        for color in range(4):
            self.color=color
            for wide in (True,False):
                key=(color,wide); c=QColor(*COLORS[color]); renderer=AlbumFlowBackgroundRenderer()
                from PySide6.QtCore import QSize
                field=renderer.render(QSize(256,144) if wide else QSize(144,256),(c,c.lighter(150),c.darker(165)),time.monotonic())
                accent=QPainter(field); radius=max(field.width(),field.height())*.85
                for x,y,light in ((.12,.18,True),(.86,.84,False)):
                    grad=QRadialGradient(field.width()*x,field.height()*y,radius)
                    shade=c.lighter(190) if light else c.darker(250); shade.setAlpha(75 if light else 100); grad.setColorAt(0,shade); shade.setAlpha(0); grad.setColorAt(1,shade); accent.fillRect(field.rect(),grad)
                accent.end(); self.bg_cache[key]=field
        self.color=original
        for line in LYRICS:
            for radius in (2,5): self.blurred_line(line,radius)
        warm=QImage(500,100,QImage.Format.Format_ARGB32_Premultiplied); warm.fill(Qt.GlobalColor.transparent); painter=QPainter(warm)
        for size in range(7,27):
            for bold in (False,True):
                painter.setFont(font(size,bold)); metrics=QFontMetrics(painter.font())
                for text in [item for song in SONGS for item in song]+LYRICS:
                    painter.drawText(0,30,metrics.elidedText(text,Qt.TextElideMode.ElideRight,300))
        painter.end()
        self.last=self.last_activity=time.monotonic()
        self.setMouseTracking(True); self.setMinimumSize(320,400); self.resize(960,540); self.timer=QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(16)
    def progress(self):
        if not self.transition: return self.target
        t=min(1.,(time.monotonic()-self.transition)/.42); return blend(self.start,self.target,1-(1-t)**3)
    def resizeEvent(self,e):
        ratio=self.width()/max(1,self.height()); dest=self.narrow
        if ratio<.9: dest=True
        elif ratio>1.1: dest=False
        if dest!=self.narrow: self.start=self.progress(); self.target=float(dest); self.narrow=dest; self.transition=time.monotonic()
        super().resizeEvent(e)
    def tick(self):
        now=time.monotonic(); dt=min(.08,now-self.last); self.last=now
        if not self.paused: self.pos+=dt
        desired=0. if self.narrow and self.mode=='lyrics' and now-self.last_activity>3 else 1.
        self.header += (desired-self.header)*min(1.,dt/ .18*3)
        groups=[{k:r for k,r in self.hits.items() if not k.startswith('row')}]; self.feedback.advance(groups,self.hovered,now)
        while self.frames and self.frames[0]<now-1: self.frames.popleft()
        self.setWindowTitle(f'GluSpot 窄窗v2 · 方案{self.variant} {LABELS[self.variant]} · {len(self.frames)} fps'); self.update()
    def keyPressEvent(self,e):
        k=e.key(); self.last_activity=time.monotonic()
        if k==Qt.Key.Key_Escape: self.close()
        elif k==Qt.Key.Key_L: self.mode='lyrics'
        elif k==Qt.Key.Key_P: self.mode='list'
        elif k==Qt.Key.Key_Tab: self.mode='list' if self.mode=='lyrics' else 'lyrics'
        elif k==Qt.Key.Key_N: self.resize(360,640)
        elif k==Qt.Key.Key_W: self.resize(960,540)
        elif k==Qt.Key.Key_Left: self.resize(max(320,self.width()-60),self.height())
        elif k==Qt.Key.Key_Right: self.resize(self.width()+60,self.height())
        elif Qt.Key.Key_1<=k<=Qt.Key.Key_4: self.color=k-Qt.Key.Key_1
        elif k==Qt.Key.Key_Space: self.paused=not self.paused
        elif k==Qt.Key.Key_H: self.hints=not self.hints
        self.update()
    def mouseMoveEvent(self,e):
        if e.position().y()<self.height()/2: self.last_activity=time.monotonic()
        self.hovered=next((k for k,r in reversed(list(self.hits.items())) if r.contains(e.position())),None); self.feedback.move(e.position())
    def mousePressEvent(self,e):
        for k,r in reversed(list(self.hits.items())):
            if r.contains(e.position()): self.feedback.press(k,e.position(),time.monotonic()); self.pressed=k; break
    def mouseReleaseEvent(self,e):
        k=getattr(self,'pressed',None); self.feedback.release(); self.pressed=None
        if k and k in self.hits and self.hits[k].contains(e.position()):
            if k=='play': self.paused=not self.paused
            elif k=='favorite': self.favorite=not self.favorite
            elif k=='dj': self.dj=not self.dj
            elif k=='layout': self.mode='list' if self.mode=='lyrics' else 'lyrics'
            elif k in ('previous','next'): self.track=(self.track+(-1 if k=='previous' else 1))%len(SONGS); self.color=self.track%4
            elif k.startswith('row'): self.track=int(k[3:]); self.color=self.track%4
        self.last_activity=time.monotonic(); self.update()
    def wheelEvent(self,e):
        if self.mode=='list': self.scroll=max(0,min(8*58-160,self.scroll-e.angleDelta().y()/120*58)); self.update()
    def background(self):
        key=(self.color, self.width() > self.height())
        if key not in self.bg_cache:
            from PySide6.QtCore import QSize
            c=QColor(*COLORS[self.color])
            renderer=AlbumFlowBackgroundRenderer()
            image=renderer.render(QSize(256,144) if key[1] else QSize(144,256),
                                  (c, c.lighter(150), c.darker(165)), time.monotonic())
            self.bg_cache[key]=image
        return self.bg_cache[key]
    def text(self,p,r,s,size,alpha=255,bold=False,align=Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft):
        p.setFont(font(size,bold)); p.setPen(QColor(255,255,255,round(alpha))); p.drawText(r,align,QFontMetrics(p.font()).elidedText(s,Qt.TextElideMode.ElideRight,max(0,int(r.width()))))
    def art(self,p,r,image):
        p.save(); path=QPainterPath(); path.addRoundedRect(r,min(12,r.width()*.16),min(12,r.width()*.16)); p.setClipPath(path); p.drawImage(r,image); p.restore()
    _rounded_polygon_path = staticmethod(MusicSurfaceWindow._rounded_polygon_path)
    def _display_playing_state(self): return not self.paused
    def _surface_font(self, size, weight=QFont.Weight.Bold):
        f=font(size); f.setWeight(weight); return f
    def _surface_glass_backdrop(self): return self.background()
    def _surface_glass_level(self): return 50
    def _scene_control_ink(self): return QColor('white')
    @property
    def _glass_feedback(self): return self.feedback
    @property
    def _clear_control_renderer(self): return self
    def paint(self, painter, rect, **kw):
        # Adapter for the original transport painter: retain its exact path.
        command=kw['role'][1]; key='play' if command=='toggle_play_pause' else command
        self.glass(painter, rect, key, kw['scene'], 'path', kw['path'])
    def glass(self,p,r,key,scene,kind='circle',path=None):
        state=self.feedback.state(key); hover=self.feedback.hover.get(key,0.)
        if key=='collection':
            candidates=[self.feedback.state(k) for k in ('favorite','playlist_add')]
            candidates=[candidate for candidate in candidates if candidate is not None]
            if candidates: state=max(candidates,key=lambda candidate: candidate.lift+candidate.glow)
            hover=max(self.feedback.hover.get('favorite',0.),self.feedback.hover.get('playlist_add',0.))
        rr=QRectF(r)
        if state: rr.translate(state.offset)
        box=path.boundingRect().adjusted(-2,-2,2,2) if path is not None else QRectF(rr)
        if path is not None and state: box.translate(state.offset)
        signature=(key, self.color, round(box.width()), round(box.height()), round(self.devicePixelRatioF(),2), self.paused if key=='play' else None)
        cached=self.tile_cache.get(signature)
        if key=='island' and time.monotonic()-self.island_refresh >= .1 and abs(self.progress()-self.target)<.001:
            cached=None
        if cached is None:
            # During motion use the most recent tile, and rebuild at most one per frame.
            cached=next((image for sig,image in reversed(list(self.tile_cache.items())) if sig[0]==key and (key!='play' or sig[-1]==self.paused)),None)
            if self.frame_budget and abs(self.progress()-self.target)<.001:
                self.frame_budget-=1
                scale=min(1.,100./max(box.width(),box.height()))
                tw=max(2,round(box.width()*scale)); th=max(2,round(box.height()*scale))
                tmp=QImage(tw,th,QImage.Format.Format_ARGB32_Premultiplied); tmp.fill(Qt.GlobalColor.transparent)
                sample=scene.copy(box.toAlignedRect()).scaled(tw,th,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
                if sample.isNull(): sample=self.background().scaled(tw,th)
                q=QPainter(tmp); q.setRenderHint(QPainter.RenderHint.Antialiasing)
                localpath=None
                if path is not None:
                    from PySide6.QtGui import QTransform
                    transform=QTransform(); transform.scale(scale,scale); transform.translate(-box.x(),-box.y()); localpath=transform.map(path)
                self.renderer.clear()
                self.renderer.paint(q,QRectF(0,0,tw,th),scene=sample,scene_bounds=QRectF(0,0,tw,th),ink=QColor('white'),now=time.monotonic(),role=key,kind=kind,path=localpath)
                q.end(); cached=tmp; self.tile_cache[signature]=cached
                if key=='island': self.island_refresh=time.monotonic()

        if cached is not None:
            grown=QRectF(box)
            if state:
                lift=max(0.,state.lift); grown.adjust(-grown.width()*.07*lift,-grown.height()*.07*lift,grown.width()*.07*lift,grown.height()*.07*lift)
            p.drawImage(grown,cached)
            if hover or (state and state.glow):
                p.save(); shape=QPainterPath()
                if path is not None: shape=path.translated(state.offset if state else QPointF())
                else: shape.addRoundedRect(grown,grown.height()/2,grown.height()/2)
                p.setClipPath(shape); p.fillRect(grown,QColor(255,255,255,round(26*hover)))
                if state and state.glow:
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                    for radius,alpha in ((22,42),(12,50),(5,65)):
                        glow=QRadialGradient(state.contact,radius); glow.setColorAt(0,QColor(255,255,255,round(alpha*state.glow))); glow.setColorAt(1,QColor(255,255,255,0)); p.fillRect(grown,glow)
                p.restore()
        return rr
    def transport_path(self,r,key):
        raise RuntimeError('transport paths are supplied by MusicSurfaceWindow')
    def control(self,p,key,r,scene):
        self.hits[key]=r
        if key in ('previous','play','next'):
            MusicSurfaceWindow._paint_transport_icon(self,p,r,'toggle_play_pause' if key=='play' else key,QColor('white'),scene_ink=QColor('white')); return
        if key in ('favorite','playlist_add'): return
        face=self.glass(p,r,key,scene,'dj' if key=='dj' else 'circle'); icon=face.adjusted(7,7,-7,-7); white=QColor(255,255,255,230)
        if key=='back': paint_music_source_icon(p,icon,'library_back',tint=white)
        elif key=='audio': paint_audio_output_status(p,icon,(),white)
        elif key=='layout': paint_content_layout_glyph(p,icon,'lyrics' if self.mode=='lyrics' else 'playlist',white)
        elif key=='window': paint_presentation_glyph(p,icon,'island',white)
        elif key=='dj':
            paint_personal_dj_icon(p,icon,white)
            if self.dj: p.setPen(QPen(QColor('#35d98b'),2)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(face.adjusted(1,1,-1,-1))
        elif key=='translation':
            p.fillPath(MusicSurfaceWindow._translation_symbol_path(self,icon),white)
        else: paint_music_source_icon(p,icon,'sequence',tint=white)
    def paintEvent(self,e):
        start=time.perf_counter(); now=time.monotonic(); self.frames.append(now); p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform); scene=self.background(); p.drawImage(QRectF(self.rect()),scene); self.hits={}; self.frame_budget=1; self.renderer.begin_frame(now); v=self.progress(); w=self.width(); h=self.height()
        wideart=QRectF(48,52,min(w*.34,h*.61),min(w*.34,h*.61)); small=QRectF(16,56,56,56); art=mixrect(wideart,small,v)
        p.save(); p.setOpacity(blend(1,self.header,v)); self.art(p,art,self.covers[self.color]); title=mixrect(QRectF(54,wideart.bottom()+14,wideart.width(),32),QRectF(84,59,max(20,w-100),24),v); self.text(p,title,SONGS[self.track][0],blend(25,16,v),255,True); self.text(p,title.translated(0,blend(30,22,v)),SONGS[self.track][1],blend(16,12,v),155); p.restore()
        nx=24; ny=188 if self.variant=='B' else 124; ny-= (1-self.header)*(64 if self.variant=='B' else 56) if self.mode=='lyrics' else 0; bottom=h-(80 if self.variant=='A' else 96 if self.variant=='C' else 64); wx=wideart.right()+80; content=QRectF(blend(wx,nx,v),blend(65,ny,v),max(1,blend(w-wx-40,w-48,v)),max(1,blend(h-100,bottom-ny,v)))
        # Render both layouts into cached-friendly images and fade their bottom edges.
        scene=QImage(self.size(),QImage.Format.Format_ARGB32_Premultiplied); backdrop=QPainter(scene); backdrop.drawImage(QRectF(self.rect()),self.background()); backdrop.setRenderHint(QPainter.RenderHint.Antialiasing); backdrop.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        widecontent=QRectF(wx,65,max(1,w-wx-40),max(1,h-130))
        narrowcontent=QRectF(nx,ny,max(1,w-48),max(1,bottom-ny))
        for area,opacity in ((widecontent,1-v),(narrowcontent,v)):
            if opacity<.001: continue
            layer=QImage(self.size(),QImage.Format.Format_ARGB32_Premultiplied); layer.fill(Qt.GlobalColor.transparent)
            lp=QPainter(layer); lp.setRenderHint(QPainter.RenderHint.Antialiasing); lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if self.mode=='lyrics': self.lyrics(lp,area)
            else: self.listing(lp,area)
            lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            fade=QLinearGradient(0,area.bottom()-40,0,area.bottom()); fade.setColorAt(0,QColor(255,255,255,255)); fade.setColorAt(1,QColor(255,255,255,0)); lp.fillRect(QRectF(area.left(),area.bottom()-40,area.width(),40),fade); lp.end()
            backdrop.save(); backdrop.setOpacity(opacity); backdrop.drawImage(0,0,layer); backdrop.restore()
        backdrop.end(); p.drawImage(0,0,scene)
        p.save(); p.setOpacity(blend(1,self.header,v)); self.art(p,art,self.covers[self.color]); self.text(p,title,SONGS[self.track][0],blend(25,16,v),255,True); self.text(p,title.translated(0,blend(30,22,v)),SONGS[self.track][1],blend(16,12,v),155); p.restore()
        if v>.001 or self.variant=='A':
            narrowrect={k:QRectF(*r) for k,r in rects(self.variant,w,h).items()}
            if self.variant=='B' and self.mode=='lyrics':
                for k in ('previous','play','next'): narrowrect[k].translate(0,-64*(1-self.header))
            if self.variant=='A':
                island=mixrect(QRectF(65,h-69,max(120,w-135),48),QRectF(16,h-80,w-32,64),v); p.save(); p.setOpacity(v); self.glass(p,island,'island',scene,'capsule'); self.progress_line(p,QRectF(island.x()+14,island.y()+1,max(1,island.width()-28),2),False); p.restore()
            elif self.variant=='B': self.progress_line(p,QRectF(24,172-64*(1-self.header if self.mode=='lyrics' else 0),w-48,2),True)
            else: self.progress_line(p,QRectF(24,h-74,w-48,2),True)
        else: narrowrect={k:QRectF(*r) for k,r in rects(self.variant,w,h).items()}
        wide={'back':QRectF(16,12,36,36)}
        for i,k in enumerate(('audio','layout','mode','translation','window')): wide[k]=QRectF(w-204+i*38,12,32,32)
        for i,k in enumerate(('previous','play','next')): wide[k]=QRectF(70+i*95,h-66,36,36)
        wide.update(favorite=QRectF(w-142,h-56,32,32),playlist_add=QRectF(w-110,h-56,32,32),dj=QRectF(w-64,h-56,32,32))
        positions={k:mixrect(wide[k],narrowrect[k],v) for k in wide}
        for key in ('back','audio','layout','mode','translation','window','previous','play','next'): self.control(p,key,positions[key],scene)
        f=positions['favorite']; a=positions['playlist_add']; capsule=f.united(a).adjusted(-2,-2,2,2); self.glass(p,capsule,'collection',scene,'capsule'); self.hits['favorite']=f; self.hits['playlist_add']=a
        for key,rect in (('favorite',f),('playlist_add',a)):
            state=self.feedback.state(key)
            if state:
                rect.translate(state.offset); g=max(0,state.lift)*.14; rect.adjust(-rect.width()*g/2,-rect.height()*g/2,rect.width()*g/2,rect.height()*g/2)
                if state.glow: p.fillRect(rect,QColor(255,255,255,round(18*state.glow)))
        paint_music_source_icon(p,f.adjusted(6,6,-6,-6),'heart_filled' if self.favorite else 'heart_outline',tint=QColor('white')); paint_music_source_icon(p,a.adjusted(6,6,-6,-6),'playlist_add',tint=QColor('white')); self.control(p,'dj',positions['dj'],scene)
        self.renderer.end_frame()
        if self.hints: self.text(p,QRectF(8,h-13,w-16,11),'L 歌词  P 列表  Tab 切换  N/W 宽窄  ←/→ 调宽  1–4 配色  Space 暂停  H 提示  Esc 退出',7,130)
        p.end(); elapsed=(time.perf_counter()-start)*1000
        if self.first_paint is None: self.first_paint=elapsed
        elif getattr(self,'bench_pressing',False): self.press_samples.append(elapsed)
        elif abs(v-self.target)>.001: self.anim_samples.append(elapsed)
        else: self.samples.append(elapsed)
    def progress_line(self,p,r,times):
        p.fillRect(r,QColor(255,255,255,65)); p.fillRect(QRectF(r.x(),r.y(),r.width()*.33,r.height()),QColor(255,255,255,200))
        if times:
            self.text(p,QRectF(r.x(),r.y()-15,60,13),'0:42',11,155); self.text(p,QRectF(r.right()-60,r.y()-15,60,13),'−3:18',11,155,align=Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
    def lyrics(self,p,r):
        p.save(); p.setClipRect(r); idx=int(self.pos//4)%len(LYRICS); phase=(self.pos%4)/4; step=74; cy=r.top()+r.height()*.42; slide=max(0,(phase-.945)/.055)*step
        for offset in range(-3,5):
            y=cy+offset*step-slide; line=LYRICS[(idx+offset)%len(LYRICS)]; alpha=255 if offset==0 else 120 if abs(offset)==1 else 60
            edge=max(0,min(1,(y-r.top()+20)/35,(r.bottom()-y)/45)); alpha*=edge
            if offset==0:
                p.setFont(font(24,True)); p.setPen(QColor(255,255,255,round(alpha*.4))); p.drawText(QRectF(r.x(),y,r.width(),60),Qt.TextFlag.TextWordWrap|Qt.AlignmentFlag.AlignLeft,line); p.save(); p.setClipRect(QRectF(r.x(),y,r.width()*phase,60),Qt.ClipOperation.IntersectClip); p.setPen(QColor(255,255,255,round(alpha))); p.drawText(QRectF(r.x(),y,r.width(),60),Qt.TextFlag.TextWordWrap|Qt.AlignmentFlag.AlignLeft,line); p.restore()
            else:
                image=self.blurred_line(line, 2 if abs(offset)==1 else 5)
                p.save(); p.setOpacity(alpha/255); p.drawImage(QPointF(r.x()-8,y-4),image); p.restore()
        p.restore()
    def blurred_line(self,line,radius):
        key=(line,radius)
        if key not in self.lyric_cache:
            image=QImage(480,52,QImage.Format.Format_ARGB32_Premultiplied); image.fill(Qt.GlobalColor.transparent)
            painter=QPainter(image); painter.setRenderHint(QPainter.RenderHint.TextAntialiasing); painter.setFont(font(19,True)); painter.setPen(QColor('white')); painter.drawText(QRectF(8,4,460,36),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,line); painter.end()
            data=QByteArray(); buffer=QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer,'PNG'); buffer.close()
            blurred=Image.open(io.BytesIO(bytes(data))).filter(ImageFilter.GaussianBlur(radius)); out=io.BytesIO(); blurred.save(out,format='PNG'); self.lyric_cache[key]=QImage.fromData(out.getvalue(),'PNG')
        return self.lyric_cache[key]
    def listing(self,p,r):
        p.save(); p.setClipRect(r)
        for i,(name,artist) in enumerate(SONGS):
            y=r.y()+i*58-self.scroll
            if y+58<r.top() or y>r.bottom(): continue
            rr=QRectF(r.x(),y,r.width(),54); self.hits['row'+str(i)]=rr
            p.save(); p.setOpacity(max(0,min(1,(y-r.top()+48)/35,(r.bottom()-y)/40)))
            key=(i,round(r.width()),self.color,i==self.track)
            row=self.row_cache.get(key)
            if row is None:
                row=QImage(max(1,round(r.width())),54,QImage.Format.Format_ARGB32_Premultiplied); row.fill(Qt.GlobalColor.transparent); rp=QPainter(row); rp.setRenderHint(QPainter.RenderHint.Antialiasing); rp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                if i==self.track:
                    c=QColor(*COLORS[self.color]); c.setAlpha(56); rp.setPen(QPen(QColor(255,255,255,65),1)); rp.setBrush(c); rp.drawRoundedRect(QRectF(0,0,r.width(),54),14,14)
                self.art(rp,QRectF(6,7,40,40),self.covers[i%4]); self.text(rp,QRectF(56,7,r.width()-64,21),name,14,255,True); self.text(rp,QRectF(56,27,r.width()-64,18),artist,11,155); rp.end(); self.row_cache[key]=row
            p.drawImage(QPointF(r.x(),y),row); p.restore()
        p.restore()

def run(variant):
    parser=argparse.ArgumentParser(); parser.add_argument('--grab',type=Path); parser.add_argument('--seconds',type=float,default=0); parser.add_argument('--bench',type=int,default=0); args=parser.parse_args(); app=QApplication(sys.argv[:1]); win=Demo(variant,args.bench); win.show()
    if args.grab:
        args.grab.mkdir(parents=True,exist_ok=True); win.hints=False
        def save(name): win.grab().save(str(args.grab/f'narrow2_{variant}_{name}.png'))
        QTimer.singleShot(500,lambda:save('wide_lyrics')); QTimer.singleShot(600,lambda:win.resize(360,640)); QTimer.singleShot(1150,lambda:save('narrow_lyrics')); QTimer.singleShot(1200,lambda:setattr(win,'mode','list')); QTimer.singleShot(1450,lambda:save('narrow_list'))
        def collapse(): win.mode='lyrics'; win.last_activity=time.monotonic()-4; win.header=0
        QTimer.singleShot(1500,collapse); QTimer.singleShot(1750,lambda:save('narrow_lyrics_collapsed'))
        def reset(): win.mode='lyrics'; win.header=1; win.last_activity=time.monotonic(); win.resize(960,540)
        QTimer.singleShot(1800,reset); QTimer.singleShot(2400,lambda:win.resize(360,640))
        for i in range(5): QTimer.singleShot(2400+i*95,lambda i=i:save(f'transition_{i}'))
        def color_frame(i):
            win.mode='lyrics'; win.header=1.; win.last_activity=time.monotonic(); win.color=i; win.update()
        for i in range(4):
            QTimer.singleShot(3000+i*600,lambda i=i:color_frame(i))
            QTimer.singleShot(3500+i*600,lambda i=i:save(f'color{i+1}'))
        QTimer.singleShot(5500,app.quit)
    elif args.seconds>0:
        if args.bench:
            QTimer.singleShot(350,lambda:win.resize(360,640)); QTimer.singleShot(1100,lambda:win.resize(960,540))
            def press():
                win.bench_pressing=True
                r=win.hits.get('play',QRectF(100,100,30,30)); win.feedback.press('play',r.center(),time.monotonic())
            def release(): win.feedback.release(); win.bench_pressing=False
            QTimer.singleShot(2000,press); QTimer.singleShot(2500,release)
        QTimer.singleShot(round(args.seconds*1000),app.quit)
    app.exec()
    if args.bench:
        def stats(values):
            a=sorted(values); return '无样本' if not a else f'median={statistics.median(a):.3f} ms p95={a[math.ceil(.95*len(a))-1]:.3f} ms n={len(a)}'
        print(f'方案 {variant}: 首帧={win.first_paint:.3f} ms; 稳态 {stats(win.samples)}; 动画 {stats(win.anim_samples)}; 按压 {stats(win.press_samples)}',flush=True)
