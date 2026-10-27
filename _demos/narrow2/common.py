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
        super().__init__(); self.variant=variant; self.bench=bench; self.samples=deque(maxlen=max(1,bench)); self.anim_samples=deque(maxlen=max(1,bench)); self.frames=deque(); self.mode='lyrics'; self.track=0; self.color=0; self.paused=False; self.dj=True; self.favorite=False; self.hints=True; self.pos=4.8; self.scroll=0; self.narrow=False; self.start=0.; self.target=0.; self.transition=0.; self.last=time.monotonic(); self.last_activity=self.last; self.header=1.; self.hovered=None; self.hits={}; self.feedback=ControlFeedback(); self.renderer=GlassControlRenderer(); self.covers=[cover(c) for c in COLORS]; self.backgrounds=[]; self.bg_cache={}; self.lyric_cache={}
        for image,rgb in zip(self.covers,COLORS):
            c=QColor(*rgb); palette=artwork_surface_palette(image,c,c.darker(150)); self.backgrounds.append(background_role_color(palette.primary))
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
        elif Qt.Key.Key_1<=k<=Qt.Key.Key_4: self.color=k-Qt.Key.Key_1; self.renderer.clear()
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
            elif k in ('previous','next'): self.track=(self.track+(-1 if k=='previous' else 1))%len(SONGS); self.color=self.track%4; self.renderer.clear()
            elif k.startswith('row'): self.track=int(k[3:]); self.color=self.track%4; self.renderer.clear()
        self.last_activity=time.monotonic(); self.update()
    def wheelEvent(self,e):
        if self.mode=='list': self.scroll=max(0,min(8*58-160,self.scroll-e.angleDelta().y()/120*58)); self.update()
    def background(self):
        key=(self.width(),self.height(),self.color)
        if key not in self.bg_cache:
            im=QImage(self.size(),QImage.Format.Format_ARGB32_Premultiplied); p=QPainter(im); c=self.backgrounds[self.color]; p.fillRect(im.rect(),c.darker(175)); g=QRadialGradient(self.width()*.72,self.height()*.18,max(self.width(),self.height())*.8); light=QColor(*COLORS[self.color]); light.setAlpha(155); g.setColorAt(0,light); light.setAlpha(0); g.setColorAt(1,light); p.fillRect(im.rect(),g); g=QRadialGradient(self.width()*.05,self.height()*.8,max(self.width(),self.height())*.65); light=QColor(*COLORS[(self.color+1)%4]); light.setAlpha(55); g.setColorAt(0,light); light.setAlpha(0); g.setColorAt(1,light); p.fillRect(im.rect(),g); p.end(); self.bg_cache={key:im}
        return self.bg_cache[key]
    def text(self,p,r,s,size,alpha=255,bold=False,align=Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft):
        p.setFont(font(size,bold)); p.setPen(QColor(255,255,255,round(alpha))); p.drawText(r,align,QFontMetrics(p.font()).elidedText(s,Qt.TextElideMode.ElideRight,max(0,int(r.width()))))
    def art(self,p,r,image):
        p.save(); path=QPainterPath(); path.addRoundedRect(r,min(12,r.width()*.16),min(12,r.width()*.16)); p.setClipPath(path); p.drawImage(r,image); p.restore()
    def glass(self,p,r,key,scene,kind='circle',path=None):
        state=self.feedback.state(key); hover=self.feedback.hover.get(key,0.); rr=QRectF(r)
        if state:
            rr.translate(state.offset); growth=max(0.,state.lift)*.14; rr.adjust(-rr.width()*growth/2,-rr.height()*growth/2,rr.width()*growth/2,rr.height()*growth/2)
        cached=self.renderer.patches.get(key)
        if cached and abs(self.progress()-self.target)>.001 and not (state and state.active(time.monotonic())):
            target=path.boundingRect().adjusted(-2,-2,2,2) if path is not None else rr
            p.drawImage(target,cached[3])
        else:
            self.renderer.paint(p,rr,scene=scene if key=='island' else self.background(),scene_bounds=QRectF(self.rect()),ink=QColor('white'),now=time.monotonic(),role=key,state=state,hover=hover,kind=kind,path=path)
        return rr
    def transport_path(self,r,key):
        # Adapted from MusicSurfaceWindow._paint_transport_icon.
        path=QPainterPath(); cx=r.center().x(); cy=r.center().y(); s=min(r.width(),r.height())*.78; hh=s*.60
        if key=='play' and not self.paused:
            for x in (cx-s*.48,cx+s*.21): path.addRoundedRect(QRectF(x,cy-hh,s*.27,hh*2),s*.081,s*.081)
        else:
            direction=-1 if key=='previous' else 1; tip=s*(.48 if key!='play' else .58); base=s*(.42 if key!='play' else .46); path.moveTo(cx+direction*tip,cy); path.lineTo(cx-direction*base,cy-hh); path.lineTo(cx-direction*base,cy+hh); path.closeSubpath()
            if key!='play': path.addRoundedRect(QRectF(cx+direction*s*.57-s*.11,cy-hh,s*.22,hh*2),s*.066,s*.066)
        return path
    def control(self,p,key,r,scene):
        self.hits[key]=r
        if key in ('previous','play','next'):
            path=self.transport_path(r,key); self.glass(p,r,key,scene,'path',path); return
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
            p.setPen(white); p.setFont(font(14,True)); p.drawText(icon,Qt.AlignmentFlag.AlignCenter,'文A')
        else: p.setPen(QPen(white,1.6)); p.drawText(icon,Qt.AlignmentFlag.AlignCenter,'∞')
    def paintEvent(self,e):
        start=time.perf_counter(); now=time.monotonic(); self.frames.append(now); p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform); scene=self.background(); p.drawImage(0,0,scene); self.hits={}; self.renderer.begin_frame(now); v=self.progress(); w=self.width(); h=self.height()
        wideart=QRectF(48,52,min(w*.34,h*.61),min(w*.34,h*.61)); small=QRectF(16,56,56,56); art=mixrect(wideart,small,v)
        p.save(); p.setOpacity(blend(1,self.header,v)); self.art(p,art,self.covers[self.color]); title=mixrect(QRectF(54,wideart.bottom()+14,wideart.width(),32),QRectF(84,59,max(20,w-100),24),v); self.text(p,title,SONGS[self.track][0],blend(25,16,v),255,True); self.text(p,title.translated(0,blend(30,22,v)),SONGS[self.track][1],blend(16,12,v),155); p.restore()
        nx=24; ny=188 if self.variant=='B' else 124; ny-= (1-self.header)*(64 if self.variant=='B' else 56) if self.mode=='lyrics' else 0; bottom=h-(76 if self.variant=='A' else 84 if self.variant=='C' else 0); wx=wideart.right()+80; content=QRectF(blend(wx,nx,v),blend(65,ny,v),max(1,blend(w-wx-40,w-48,v)),max(1,blend(h-100,bottom-ny,v)))
        # Sample the actual content under the floating island.
        scene=scene.copy(); backdrop=QPainter(scene); backdrop.setRenderHint(QPainter.RenderHint.Antialiasing); backdrop.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.mode=='lyrics': self.lyrics(backdrop,content)
        else: self.listing(backdrop,content)
        backdrop.end(); p.drawImage(0,0,scene)
        p.save(); p.setOpacity(blend(1,self.header,v)); self.art(p,art,self.covers[self.color]); self.text(p,title,SONGS[self.track][0],blend(25,16,v),255,True); self.text(p,title.translated(0,blend(30,22,v)),SONGS[self.track][1],blend(16,12,v),155); p.restore()
        if v>.001:
            narrowrect={k:QRectF(*r) for k,r in rects(self.variant,w,h).items()}
            if self.variant=='B' and self.mode=='lyrics':
                for k in ('previous','play','next'): narrowrect[k].translate(0,-64*(1-self.header))
            if self.variant=='A':
                island=QRectF(16,h-80,w-32,64); self.glass(p,island,'island',scene,'capsule'); self.progress_line(p,QRectF(30,h-79,w-60,2),False)
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
        paint_music_source_icon(p,f.adjusted(6,6,-6,-6),'heart_filled' if self.favorite else 'heart_outline',tint=QColor('white')); paint_music_source_icon(p,a.adjusted(6,6,-6,-6),'playlist_add',tint=QColor('white')); self.control(p,'dj',positions['dj'],scene)
        self.renderer.end_frame()
        if self.hints: self.text(p,QRectF(8,h-13,w-16,11),'L 歌词  P 列表  Tab 切换  N/W 宽窄  ←/→ 调宽  1–4 配色  Space 暂停  H 提示  Esc 退出',7,130)
        p.end(); elapsed=(time.perf_counter()-start)*1000; self.samples.append(elapsed)
        if abs(v-self.target)>.001: self.anim_samples.append(elapsed)
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
                self.text(p,QRectF(r.x(),y,r.width(),40),line,24,alpha*.4,True); p.save(); p.setClipRect(QRectF(r.x(),y,r.width()*phase,40),Qt.ClipOperation.IntersectClip); self.text(p,QRectF(r.x(),y,r.width(),40),line,24,alpha,True); p.restore()
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
            if i==self.track:
                c=QColor(*COLORS[self.color]); c.setAlpha(56); p.setPen(QPen(QColor(255,255,255,65),1)); p.setBrush(c); p.drawRoundedRect(rr,14,14)
            self.art(p,QRectF(r.x()+6,y+7,40,40),self.covers[i%4]); self.text(p,QRectF(r.x()+56,y+7,r.width()-64,21),name,14,255,True); self.text(p,QRectF(r.x()+56,y+27,r.width()-64,18),artist,11,155); p.restore()
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
        QTimer.singleShot(3000,app.quit)
    elif args.seconds>0:
        if args.bench: QTimer.singleShot(350,lambda:win.resize(360,640)); QTimer.singleShot(1100,lambda:win.resize(960,540))
        QTimer.singleShot(round(args.seconds*1000),app.quit)
    app.exec()
    if args.bench:
        def stats(values):
            a=sorted(values); return '无样本' if not a else f'median={statistics.median(a):.3f} ms p95={a[math.ceil(.95*len(a))-1]:.3f} ms n={len(a)}'
        print(f'方案 {variant}: paint {stats(win.samples)}; animation {stats(win.anim_samples)}',flush=True)
