import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MusicNotes, WindowsLogo } from "@phosphor-icons/react";
import { ASSET } from "./data.js";
import { onFrame, usePinnedScene } from "./lib/motion.js";
import { SCENE_HOLD, SCENE_TRANSITION, getTimedPhase, phaseCenter, smoothstep } from "./storyTimeline.js";

// Sizes are the native exports at 1x; the stage scales them with --k.
const MODES = [
  { id: "free", name: "Free", tag: "自由悬浮", w: 360, h: 46,
    text: "随手拖动，停在顺手的角落。靠近吸附位、重合过半才会吸附；按住一秒，它就留在原地。" },
  { id: "top", name: "Top", tag: "贴住屏幕顶端", w: 360, h: 62,
    text: "贴着顶边展开，抬眼就是歌词，还能沿顶边横向移动。全屏且没有任何栏时，自动换成超矮的 Top。" },
  { id: "tasker", name: "任务栏", tag: "嵌进任务栏的空隙", w: 300, h: 40,
    text: "识别任务栏上的图标，自动收缩到空隙里，从不挡住它们。无底色的栏样式，让封面与歌词像长在任务栏上。" },
  { id: "finder", name: "Finder 栏", tag: "MyDockFinder 适配", w: 360, h: 30,
    text: "靠上 MyDockFinder 的 Finder 栏，透明无底色；保留可点击区域并始终置顶，不会点穿到栏上。" },
];
const COUNT = MODES.length;
const phaseOf = progress => getTimedPhase(progress, COUNT, SCENE_HOLD, SCENE_TRANSITION);
const stageScale = width => Math.max(0.74, Math.min(1.4, width / 780));

export function DesktopBackdrop({ finder = false }) {
  return <div className="desk" aria-hidden="true">
    <img className="desk-wall" src={`${ASSET}desktop-wallpaper.webp`} alt="" width="1586" height="992" decoding="async" />
    <div className="desk-icons">
      <span><WindowsLogo size={22} />此电脑</span>
      <span><MusicNotes size={22} />音乐</span>
      <span><img src={`${ASSET}gluspot.svg`} alt="" />GluSpot</span>
    </div>
    {finder && <div className="desk-finder"><b>GluSpot</b><span>文件</span><span>显示</span><i>Finder</i></div>}
    <div className="desk-taskbar">
      <div className="desk-taskbar-icons">
        <WindowsLogo weight="fill" /><MusicNotes /><img src={`${ASSET}gluspot.svg`} alt="" />
      </div>
      <span className="desk-clock">21:08</span>
    </div>
  </div>;
}

function modeRect(mode, box, k) {
  const width = Math.min(box.width - 20, mode.w * k);
  const height = mode.h * k * (width / (mode.w * k));
  const centre = box.left + box.width / 2;
  if (mode.id === "free") return { cx: centre, top: box.top + box.height * 0.42 - height / 2, width, height };
  if (mode.id === "tasker") {
    const bar = 46 * k;
    return { cx: box.left + 10 * k + width / 2, top: box.bottom - bar + (bar - height) / 2, width, height };
  }
  return { cx: centre, top: box.top, width, height };
}

export function IslandScene({ opening = false, reduced = false }) {
  const stage = useRef(null), vessel = useRef(null), images = useRef([]);
  const intro = useRef(opening);
  intro.current = opening;
  const [step, setStep] = useState(0);
  const stepRef = useRef(0);
  const scene = usePinnedScene({
    enabled: !reduced,
    render: progress => {
      const index = phaseOf(progress).index;
      if (index !== stepRef.current) { stepRef.current = index; setStep(index); }
    },
  });

  useEffect(() => {
    const canvas = stage.current;
    const resize = new ResizeObserver(([entry]) => canvas.style.setProperty("--k", stageScale(entry.contentRect.width).toFixed(3)));
    resize.observe(canvas);
    const stop = onFrame(() => {
      const node = vessel.current;
      if (!node) return false;
      const box = canvas.getBoundingClientRect();
      if (box.bottom < 40 || box.width === 0) { node.style.visibility = "hidden"; return false; }
      const vh = window.innerHeight, vw = document.documentElement.clientWidth;
      const k = stageScale(box.width);
      const phase = phaseOf(scene.progress.current);
      const mix = smoothstep(phase.mix);
      const a = modeRect(MODES[phase.index], box, k), b = modeRect(MODES[phase.nextIndex], box, k);
      const lerp = key => a[key] + (b[key] - a[key]) * mix;
      // Flight from the bottom pill: lift ahead of the page, then settle into the desktop.
      const entry = scene.sticky.current.getBoundingClientRect().top + 0;
      const runway = Math.max(1, window.scrollY + entry);
      const amount = intro.current ? 0 : Math.min(1, Math.max(0, window.scrollY / runway));
      const blend = smoothstep(amount);
      const width = 136 + (lerp("width") - 136) * blend, height = 8 + (lerp("height") - 8) * blend;
      const travel = 1 - Math.pow(1 - amount, 3);
      const lift = Math.sin(Math.PI * amount) * Math.min(300, vh * 0.4);
      const x = vw / 2 + (lerp("cx") - vw / 2) * blend - width / 2;
      const y = vh - 34 + (lerp("top") - vh + 34) * travel - lift;
      node.style.visibility = "visible";
      node.style.width = `${width.toFixed(2)}px`;
      node.style.height = `${height.toFixed(2)}px`;
      node.style.transform = `translate3d(${x.toFixed(2)}px,${y.toFixed(2)}px,0)`;
      node.style.setProperty("--c", blend.toFixed(3));
      const shown = smoothstep((blend - 0.45) / 0.55);
      images.current.forEach((image, index) => {
        if (!image) return;
        const weight = index === phase.index ? (phase.nextIndex === index ? 1 : 1 - mix) : index === phase.nextIndex ? mix : 0;
        image.style.opacity = (weight * shown).toFixed(3);
      });
      canvas.style.setProperty("--finder", (phase.index === 3 ? 1 : phase.nextIndex === 3 ? mix : 0).toFixed(3));
      canvas.style.setProperty("--bar", (phase.index === 2 ? Math.max(1 - mix, phase.nextIndex === 2 ? 1 : 0) : phase.nextIndex === 2 ? mix : 0).toFixed(3));
      return false;
    });
    return () => { stop(); resize.disconnect(); };
  }, []);
  useEffect(() => { scene.repaint(); }, [opening, reduced]);

  const mode = MODES[step];
  return <section ref={scene.section} className={`pin island-scene${reduced ? " still" : ""}`} id="island" aria-labelledby="island-title" style={{ "--steps": COUNT }}>
    <div ref={scene.sticky} className="pin-sticky">
      <div className="island-layout shell">
        <div className="island-copy">
          <span className="eyebrow">音乐岛 · 四种岛态</span>
          <h2 id="island-title">一座岛，<br />停靠在每一种桌面。</h2>
          <div className="mode-tabs" role="group" aria-label="音乐岛形态">
            {MODES.map((item, index) => <button key={item.id} type="button" className={index === step ? "active" : ""}
              aria-pressed={index === step} onClick={() => scene.seek(phaseCenter(index, COUNT))}>
              <b>{item.name}</b><small>{item.tag}</small>
            </button>)}
          </div>
          <p className="mode-desc" key={mode.id} aria-live="polite">{mode.text}</p>
        </div>
        <div ref={stage} className="island-stage" data-mode={mode.id}>
          <DesktopBackdrop finder />
          <span className="stage-note">原生岛态导出 · 模拟桌面为示意图</span>
        </div>
      </div>
      {!reduced && <span className="scroll-cue" aria-hidden="true">继续滚动，切换岛态</span>}
    </div>
    {createPortal(<div ref={vessel} className="vessel" aria-hidden="true">
      {MODES.map((item, index) => <img key={item.id} ref={node => { images.current[index] = node; }}
        src={`${ASSET}island-story/chace-${item.id}.png`} alt="" width={item.w * 2} height={item.h * 2} />)}
    </div>, document.body)}
    <ul className="visually-hidden">
      {MODES.map(item => <li key={item.id}>{item.name}：{item.tag}。{item.text}</li>)}
    </ul>
  </section>;
}
