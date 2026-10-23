import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, Pause, Play } from "@phosphor-icons/react";
import { getTimedPhase, storyDuration } from "./storyTimeline.js";
import { useScrollStory } from "./useScrollStory.js";

const MODES = [
  { id: "free", name: "Free", description: "自由悬浮", lyric: "It's been the same old thing", x: 50, y: 50, w: 360, h: 46 },
  { id: "top", name: "Top", description: "贴合屏幕顶部", lyric: "Since it ended", x: 50, y: 0, w: 360, h: 62 },
  { id: "tasker", name: "Tasker Bar", description: "融入 Windows 任务栏", lyric: "Since you left me", x: 4, y: 100, w: 300, h: 40 },
  { id: "finder", name: "Finder Bar", description: "MyDockFinder 适配", lyric: "Since I moved on", x: 50, y: 0, w: 360, h: 30 },
];
const VIEWS = ["音乐岛", "窗口", "全屏"];

export function OpeningHero() {
  return <section className="opening-page" aria-labelledby="opening-title">
    <img className="headline-light" src="/assets/chace-background-still.webp" alt="" aria-hidden="true" />
    <h1 id="opening-title">让音乐<br />融入桌面</h1>
  </section>;
}

function StoryRail({ story, names, label }) {
  const active = getTimedPhase(story.progress, names.length).index;
  return <div className="story-rail" aria-label={label}>
    <button className="story-play" type="button" aria-label={story.completed ? `重播${label}` : story.playing ? `暂停${label}` : `播放${label}`}
      onClick={() => { if (story.completed) { story.seek(0); story.setPlaying(true); } else story.setPlaying(value => !value); }}>
      {story.playing && !story.completed ? <Pause size={15} weight="fill" /> : <Play size={15} weight="fill" />}
    </button>
    <div className="story-dots" role="group" aria-label={`${label}章节`}>
      {names.map((name, index) => <button key={name} type="button" className={active === index ? "active" : ""}
        aria-label={`${label}：${name}`} aria-current={active === index ? "step" : undefined}
        onClick={() => story.seek(index * 3500 / storyDuration(names.length))}>
        <span className="story-dot-track"><span style={{ transform: `scaleX(${active === index ? getTimedPhase(story.progress, names.length).holdProgress : active > index ? 1 : 0})` }} /></span>
      </button>)}
    </div>
    <input type="range" min="0" max="1000" step="1" value={Math.round(story.progress * 1000)}
      aria-label={`${label}进度`} onChange={event => story.seek(Number(event.target.value) / 1000)} />
    <span className="story-hint">{story.completed ? "继续向下探索" : "滚动，或让它自己播放"}</span>
  </div>;
}

export function IslandStory({ backdrop: DesktopBackdrop, motion = true, opening = false }) {
  const stage = useRef(null);
  const vessel = useRef(null);
  const slot = useRef(null), latestProgress = useRef(0), openingState = useRef(opening), liveConnect = useRef(null);
  openingState.current = opening;
  const connect = () => {
    const node = vessel.current, canvas = stage.current;
    if (!node || !canvas || !slot.current) return;
    const box = canvas.getBoundingClientRect();
    const entryScroll = slot.current.getBoundingClientRect().top + window.scrollY - 64;
    const amount = openingState.current ? 0 : Math.min(1, Math.max(0, window.scrollY / Math.max(1, entryScroll)));
    const blend = amount * amount * (3 - 2 * amount);
    const phase = getTimedPhase(latestProgress.current, 4), a = MODES[phase.index], b = MODES[phase.nextIndex];
    const mix = phase.mix * phase.mix * (3 - 2 * phase.mix);
    const targetW = Math.min(box.width - 24, a.w + (b.w - a.w) * mix);
    const targetH = a.h + (b.h - a.h) * mix;
    const y = (a.y + (b.y - a.y) * mix) / 100;
    const x = (a.x + (b.x - a.x) * mix) / 100;
    const targetCenter = box.left + (box.width - targetW) * x + targetW / 2;
    const targetTop = box.top + (box.height - targetH) * y;
    const width = 136 + (targetW - 136) * blend, height = 8 + (targetH - 8) * blend;
    node.style.width = `${width}px`;
    node.style.height = `${height}px`;
    node.style.left = `${window.innerWidth / 2 + (targetCenter - window.innerWidth / 2) * blend - width / 2}px`;
    // One reversible path: lift ahead of the page, then settle into the desktop.
    const travel = 1 - Math.pow(1 - amount, 3);
    const lift = Math.sin(Math.PI * amount) * Math.min(320, window.innerHeight * .45);
    node.style.top = `${window.innerHeight - 33 + (targetTop - window.innerHeight + 33) * travel - lift}px`;
    node.style.setProperty("--connection", String(blend));
    node.style.setProperty("--island-mix", String(mix));
    node.style.visibility = box.bottom < 64 ? "hidden" : "visible";
  };
  liveConnect.current = connect;
  const story = useScrollStory({ durationMs: storyDuration(4), autoPlay: motion, onProgress: progress => {
    latestProgress.current = progress;
    if (!stage.current) return;
    const phase = getTimedPhase(progress, 4), a = MODES[phase.index], b = MODES[phase.nextIndex];
    const mix = phase.mix * phase.mix * (3 - 2 * phase.mix);
    stage.current.style.setProperty("--island-y", `${a.y + (b.y - a.y) * mix}%`);
    stage.current.style.setProperty("--island-w", `${a.w + (b.w - a.w) * mix}px`);
    stage.current.style.setProperty("--island-h", `${a.h + (b.h - a.h) * mix}px`);
    stage.current.style.setProperty("--island-mix", String(mix));
    connect();
  } });
  const phase = getTimedPhase(story.progress, 4);
  useEffect(() => {
    const update = () => liveConnect.current?.();
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    const observer = new ResizeObserver(update);
    observer.observe(stage.current);
    return () => { window.removeEventListener("scroll", update); window.removeEventListener("resize", update); observer.disconnect(); };
  }, []);
  useEffect(() => { liveConnect.current?.(); }, [opening]);
  return <div ref={slot} className="story-slot">
    <section ref={story.ref} className="story-chapter island-story" id="island" tabIndex="0" aria-label="四种音乐岛形态演示" data-active={story.active}>
      <div className="chapter-heading"><span>一座岛，融入每一种桌面。</span><span>01 / 03</span></div>
      <div ref={stage} className={`island-story-stage form-${MODES[phase.index].id}`} data-current={phase.index} data-next={phase.nextIndex}>
        <DesktopBackdrop />
        <div className="story-finder-strip" aria-hidden="true"><span>GluSpot</span><span>Finder</span></div>
        {createPortal(<div ref={vessel} className="island-vessel shared-island" aria-hidden="true">
          {MODES.map((mode, index) => <img key={mode.id} src={`/assets/island-story/chace-${mode.id}.png?style=${index > 1 ? "transparent" : "solid"}`}
            alt={`GluSpot ${mode.name} 原生音乐岛，${mode.description}`}
            aria-hidden={index !== phase.index}
            className={`${index === phase.index ? "current" : ""} ${index === phase.nextIndex && index !== phase.index ? "next" : ""}`}
            width={mode.w * 2} height={mode.h * 2} />)}
        </div>, document.body)}
      </div>
      <div className="island-story-caption" aria-live="polite"><strong>{MODES[phase.index].name}</strong><span>{MODES[phase.index].description}</span><span className="visually-hidden">{MODES[phase.index].lyric}</span></div>
      <StoryRail story={story} names={MODES.map(mode => mode.name)} label="音乐岛演示" />
    </section>
  </div>;
}

export function SurfaceStory({ children, onPresentation, controller, motion = true, enabled = true }) {
  const notify = useRef(onPresentation);
  notify.current = onPresentation;
  const geometry = useRef({ width: 900, height: 450 }), host = useRef(null);
  const applyFrame = progress => {
    const node = host.current;
    if (!node) return;
    const phase = getTimedPhase(progress, 3), { width, height } = geometry.current;
    const sizes = [
      { w: Math.min(360, width - 24), h: 46, radius: 24, open: 0 },
      { w: Math.min(700, width * .88), h: Math.min(380, height - 24), radius: 27, open: 1 },
      { w: width, h: height, radius: 12, open: 1 },
    ];
    const a = sizes[phase.index], b = sizes[phase.nextIndex];
    const mix = phase.mix * phase.mix * (3 - 2 * phase.mix);
    const between = key => a[key] + (b[key] - a[key]) * mix;
    node.style.setProperty("--surface-w", `${between("w")}px`);
    node.style.setProperty("--surface-h", `${between("h")}px`);
    node.style.setProperty("--surface-radius", `${between("radius")}px`);
    node.style.setProperty("--surface-open", `${between("open")}`);
  };
  const latestProgress = useRef(0);
  const story = useScrollStory({ durationMs: storyDuration(3), autoPlay: motion, enabled, onProgress: progress => {
    latestProgress.current = progress;
    applyFrame(progress);
  } });
  const phase = getTimedPhase(story.progress, 3);
  const current = useRef(-1);
  useEffect(() => {
    const stage = host.current?.querySelector(".player-stage");
    if (!stage) return;
    const observer = new ResizeObserver(([entry]) => {
      geometry.current = { width: entry.contentRect.width, height: entry.contentRect.height };
      applyFrame(latestProgress.current);
    });
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    controller.current = { seek: story.seek, setPlaying: story.setPlaying };
    return () => { controller.current = null; };
  }, [controller, story.seek, story.setPlaying]);
  useEffect(() => {
    const target = phase.transitioning && phase.mix > 0 ? phase.nextIndex : phase.index;
    if (target === current.current) return;
    current.current = target;
    notify.current(["island", "window", "fullscreen"][target]);
  }, [phase.index, phase.nextIndex, phase.transitioning, phase.mix]);
  return <div className="story-slot">
    <section ref={node => { story.ref.current = node; host.current = node; }} className="story-chapter surface-story" id="player-story" tabIndex="0" aria-label="音乐岛、窗口与全屏连续演示">
      <div className="chapter-heading"><span>从一座岛，到整个屏幕。</span><span>02 / 03</span></div>
      {children}
      <StoryRail story={story} names={VIEWS} label="播放界面演示" />
    </section>
  </div>;
}

export function HighlightStory({ motion = true }) {
  const player = useRef(null), host = useRef(null);
  const [visible, setVisible] = useState(false), [ready, setReady] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (motion && visible) player.current?.play().catch(() => setReady(false));
    else player.current?.pause();
  }, [motion, visible]);
  return <section ref={host} className="highlight-story section-shell" aria-labelledby="highlight-title">
    <div className="chapter-heading"><span>让每个字，都被看见。</span><span>03 / 03</span></div>
    <h2 id="highlight-title" className="visually-hidden">音乐高光，感知清晰！</h2>
    <div className={`highlight-media ${ready && motion ? "ready" : ""}`}>
      <img src="/assets/highlight/music-highlight.png" alt="音乐高光，感知清晰！" width="960" height="300" />
      <video ref={player} src="/assets/highlight/music-highlight.webm" muted loop playsInline preload="metadata" aria-hidden="true"
        onLoadedData={() => setReady(true)} onError={() => setReady(false)} />
    </div>
    <p>原生逐字抬升与高光。<br />读到哪里，光就走到哪里。</p>
    <ArrowDown size={20} className="highlight-down" aria-hidden="true" />
  </section>;
}
