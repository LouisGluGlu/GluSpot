import { useEffect, useRef, useState } from "react";
import { ArrowsInSimple, CornersOut, Disc, MusicNotes, Pause, Play, Queue, SkipBack, SkipForward, TextAlignLeft, Translate, Waveform } from "@phosphor-icons/react";
import { ASSET, SONGS, coverAlt } from "./data.js";
import { DesktopBackdrop } from "./IslandScene.jsx";
import { useAutoVideo, usePinnedScene } from "./lib/motion.js";
import { SCENE_HOLD, SCENE_TRANSITION, getTimedPhase, phaseCenter, smoothstep } from "./storyTimeline.js";

const VIEWS = [
  { id: "island", name: "音乐岛" },
  { id: "window", name: "窗口" },
  { id: "fullscreen", name: "全屏" },
];
export const LAYOUTS = [
  { id: "album", label: "专辑封面", icon: Disc },
  { id: "inside", label: "封面内歌词", icon: MusicNotes },
  { id: "outside", label: "封面外歌词", icon: TextAlignLeft },
  { id: "queue", label: "播放列表", icon: Queue },
];
const phaseOf = progress => getTimedPhase(progress, VIEWS.length, SCENE_HOLD, SCENE_TRANSITION);

export function Cover({ song, className = "", ...props }) {
  return <img className={`cover ${className}`} src={`${ASSET}${song.cover}`} alt={coverAlt(song)} width="440" height="440" decoding="async" {...props} />;
}

/** Production album-flow background: a still per song, the loop only for the active one. */
export function Ambience({ songIndex, motion = true }) {
  const song = SONGS[songIndex];
  const video = useAutoVideo(motion, song.tone);
  return <div className="ambience" aria-hidden="true">
    {SONGS.map((item, index) => <img key={item.tone} className={index === songIndex ? "on" : ""} src={`${ASSET}${item.tone}-background-still.webp`} alt="" loading="lazy" decoding="async" />)}
    <video key={song.tone} ref={video.ref} className={video.ready && motion ? "on" : ""} src={`${ASSET}${song.tone}-background.mp4`}
      muted loop playsInline preload="none" onLoadedData={() => video.setReady(true)} onError={() => video.setReady(false)} />
  </div>;
}

/** Production lyric painter export (alpha video over a still). */
export function LyricMedia({ song, translated, playing = true, motion = true }) {
  const name = `${song.tone}-${translated ? "translated" : "original"}`;
  const video = useAutoVideo(playing && motion, name);
  if (!song.lyrics) return <div className="lyric-plain"><strong>{song.title}</strong><span>{song.artist}</span><small>此曲演示不展示歌词</small></div>;
  return <div className="lyric-media">
    <img src={`${ASSET}lyrics/${name}.png`} alt={`${song.title} 逐字歌词${translated ? "与译文" : ""}演示画面`} width="480" height="480" loading="lazy" decoding="async" />
    <video key={name} ref={video.ref} className={video.ready && motion ? "on" : ""} src={`${ASSET}lyrics/${name}.webm`} muted loop playsInline preload="none" aria-hidden="true"
      onLoadedData={() => video.setReady(true)} onError={() => video.setReady(false)} />
  </div>;
}

export function PlayerScene({ songIndex, setSongIndex, motion = true, reduced = false }) {
  const stage = useRef(null), surface = useRef(null);
  const size = useRef({ width: 900, height: 520 });
  const [view, setView] = useState(reduced ? 1 : 0);
  const viewRef = useRef(view);
  const [layout, setLayout] = useState("outside");
  const [playing, setPlaying] = useState(true);
  const [translated, setTranslated] = useState(true);
  const song = SONGS[songIndex];

  const scene = usePinnedScene({
    enabled: !reduced,
    render: progress => {
      const node = surface.current;
      if (!node) return;
      const phase = phaseOf(progress), { width, height } = size.current;
      const wide = width / height > 1.1;
      const k = Math.max(0.78, Math.min(1.3, width / 900));
      const islandW = Math.min(width - 24, 360 * k);
      const windowW = wide ? Math.min(width * 0.76, (height - 40) * 700 / 380, 880) : Math.min(width - 28, 400);
      const sizes = [
        { w: islandW, h: islandW * 46 / 360, r: islandW * 23 / 360, open: 0 },
        { w: windowW, h: wide ? windowW * 380 / 700 : Math.min(height - 36, windowW * 1.5), r: 26, open: 1 },
        { w: width, h: height, r: 0, open: 1 },
      ];
      const a = sizes[phase.index], b = sizes[phase.nextIndex], mix = smoothstep(phase.mix);
      const between = key => a[key] + (b[key] - a[key]) * mix;
      node.style.setProperty("--w", `${between("w").toFixed(2)}px`);
      node.style.setProperty("--h", `${between("h").toFixed(2)}px`);
      node.style.setProperty("--r", `${between("r").toFixed(2)}px`);
      node.style.setProperty("--open", between("open").toFixed(3));
      const index = phase.transitioning && phase.mix > 0.5 ? phase.nextIndex : phase.index;
      if (index !== viewRef.current) { viewRef.current = index; setView(index); }
    },
  });
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      size.current = { width: entry.contentRect.width, height: entry.contentRect.height };
      scene.repaint();
    });
    observer.observe(stage.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => { if (reduced) scene.seek(phaseCenter(Math.max(1, viewRef.current), VIEWS.length)); }, [reduced]);

  const go = index => scene.seek(phaseCenter(index, VIEWS.length));
  const pickLayout = id => { setLayout(id); if (viewRef.current === 0) go(1); };
  const step = delta => setSongIndex((songIndex + delta + SONGS.length) % SONGS.length);
  const closed = view === 0;
  const lyric = <LyricMedia song={song} translated={translated} playing={playing} motion={motion} />;

  return <section ref={scene.section} className={`pin player-scene${reduced ? " still" : ""}`} id="player" aria-labelledby="player-title" style={{ "--steps": VIEWS.length }}>
    <div ref={scene.sticky} className="pin-sticky">
      <div className="player-layout shell">
        <header className="scene-head">
          <span className="eyebrow">窗口与全屏 · 四种版式</span>
          <h2 id="player-title">点开小岛，便是一整片海。</h2>
        </header>
        <div ref={stage} className="theater">
          <DesktopBackdrop />
          <article ref={surface} className="surface" data-layout={layout} data-view={VIEWS[view].id} aria-label="GluSpot 互动播放器演示（无音频）">
            <button type="button" className="surface-island" tabIndex={closed ? 0 : -1} aria-hidden={!closed} aria-label="点击音乐岛展开播放界面" onClick={() => go(1)}>
              <img src={`${ASSET}islands/${song.tone}-free.png`} alt="" width="720" height="92" />
            </button>
            <Ambience songIndex={songIndex} motion={motion && !closed} />
            <div className="surface-ui" inert={closed}>
              <div className="p-top">
                <span className="p-tag">交互演示 · 无音频</span>
                <div className="p-tools">
                  <button type="button" className="glass-btn" aria-label={translated ? "隐藏译文" : "显示译文"} aria-pressed={translated} onClick={() => setTranslated(value => !value)}><Translate /></button>
                  <button type="button" className="glass-btn" aria-label="收拢为音乐岛" onClick={() => go(0)}><ArrowsInSimple /></button>
                  <button type="button" className="glass-btn" aria-label={view === 2 ? "返回窗口" : "进入全屏"} onClick={() => go(view === 2 ? 1 : 2)}><CornersOut /></button>
                </div>
              </div>
              <div className="p-body" key={layout}>
                <div className="p-album">
                  <div className="p-cover">
                    {SONGS.map((item, index) => <Cover key={item.tone} song={item} className={index === songIndex ? "on" : ""} aria-hidden={index !== songIndex} />)}
                    {layout === "inside" && <div className="p-inside">{lyric}</div>}
                  </div>
                  <div className="p-meta">
                    <div className="p-title"><h3>{song.title}</h3><p>{song.artist}</p></div>
                    <div className="p-transport">
                      <button type="button" aria-label="上一首演示歌曲" onClick={() => step(-1)}><SkipBack weight="fill" /></button>
                      <button type="button" aria-label={playing ? "暂停歌词演示" : "播放歌词演示"} aria-pressed={playing} onClick={() => setPlaying(value => !value)}>
                        {playing ? <Pause weight="fill" /> : <Play weight="fill" />}
                      </button>
                      <button type="button" aria-label="下一首演示歌曲" onClick={() => step(1)}><SkipForward weight="fill" /></button>
                    </div>
                  </div>
                </div>
                {layout === "outside" && <div className="p-side">{lyric}</div>}
                {layout === "queue" && <div className="p-side p-queue">
                  <p>播放列表 · 3 首演示曲目</p>
                  {SONGS.map((item, index) => <button key={item.tone} type="button" className={index === songIndex ? "on" : ""} aria-label={`切换到 ${item.artist} ${item.title}`} onClick={() => setSongIndex(index)}>
                    <Cover song={item} alt="" />
                    <span><strong>{item.title}</strong><small>{item.artist}</small></span>
                    {index === songIndex ? <Waveform /> : <Play weight="fill" />}
                  </button>)}
                </div>}
              </div>
            </div>
          </article>
          <span className={`stage-hint${closed ? " on" : ""}`} aria-hidden="true">点击小岛，或继续滚动</span>
        </div>
        <div className="scene-controls">
          <div className="seg" role="group" aria-label="展示形态">
            {VIEWS.map((item, index) => <button key={item.id} type="button" aria-pressed={view === index} onClick={() => go(index)}>{item.name}</button>)}
          </div>
          <div className="seg layouts" role="group" aria-label="播放版式">
            {LAYOUTS.map(item => <button key={item.id} type="button" aria-pressed={layout === item.id} aria-label={item.label} title={item.label} onClick={() => pickLayout(item.id)}>
              <item.icon size={18} /><span>{item.label}</span>
            </button>)}
          </div>
          <div className="song-picks" role="group" aria-label="演示歌曲">
            {SONGS.map((item, index) => <button key={item.tone} type="button" aria-pressed={index === songIndex} aria-label={`${item.artist} · ${item.title}`} title={`${item.artist} · ${item.title}`} onClick={() => setSongIndex(index)}>
              <Cover song={item} alt="" />
            </button>)}
          </div>
        </div>
      </div>
    </div>
  </section>;
}
