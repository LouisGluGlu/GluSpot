import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle,
  CornersOut,
  Disc,
  GearSix,
  GithubLogo,
  Headphones,
  Minus,
  MusicNotes,
  Pause,
  Play,
  Queue,
  SkipBack,
  SkipForward,
  SlidersHorizontal,
  SpeakerHigh,
  TextAlignLeft,
  Translate,
  Waveform,
  WindowsLogo,
  X,
} from "@phosphor-icons/react";
import { OpeningHero, IslandStory, SurfaceStory, HighlightStory } from "./StoryExperience.jsx";
import { storyDuration } from "./storyTimeline.js";

const ASSET = "/assets/";
const REPO = "https://github.com/LouisGluGlu/GluSpot";
const RELEASE = `${REPO}/releases/tag/v6.417`;
const SONGS = [
  {
    title: "The Rain Song",
    artist: "Chace",
    album: "Belated Suffocation",
    cover: "chace-belated-suffocation.jpg",
    lyric: "It’s been the same old thing",
    translation: "一成不变",
    tone: "chace",
  },
  {
    title: "Ibuprofen",
    artist: "Alicks",
    album: "Unwritten",
    cover: "alicks-unwritten.jpg",
    lyric: "I get high and I get low",
    translation: "我的意识在天空中翱翔",
    tone: "alicks",
  },
  {
    title: "Airport Bar",
    artist: "goodboy noah",
    album: "Airport Bar",
    cover: "goodboy-noah-airport-bar.jpg",
    lyric: "Airport Bar",
    translation: "goodboy noah",
    tone: "noah",
  },
];
const LAYOUTS = [
  { id: "album", label: "专辑封面", icon: Disc },
  { id: "inside", label: "封面内歌词", icon: MusicNotes },
  { id: "outside", label: "封面外歌词", icon: TextAlignLeft },
  { id: "queue", label: "播放列表", icon: Queue },
];
function IconButton({ label, children, className = "", ...props }) {
  return (
    <button
      className={`icon-button glass ${className}`}
      type="button"
      aria-label={label}
      title={label}
      {...props}
    >
      {children}
    </button>
  );
}
function Cover({ song, className = "", ...props }) {
  return (
    <img
      className={`album-image ${className}`}
      src={`${ASSET}${song.cover}`}
      alt={`${song.artist}《${song.album}》专辑封面`}
      width="440"
      height="440"
      {...props}
    />
  );
}
function BackgroundLayer({ song, active, motion }) {
  const video = useRef(null);
  const container = useRef(null);
  const [visible, setVisible] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const node = video.current;
    if (!node) return;
    if (active && motion && visible) node.play().catch(() => setReady(false));
    else node.pause();
  }, [active, motion, visible]);
  return (
    <div ref={container} className={`background-layer ${active ? "visible" : ""}`}>
      <img src={`${ASSET}${song.tone}-background-still.webp`} alt="" />
      <video
        ref={video}
        className={ready && motion ? "ready" : ""}
        src={active ? `${ASSET}${song.tone}-background.mp4` : undefined}
        muted loop playsInline preload={active ? "auto" : "none"}
        onLoadedData={() => setReady(true)}
        onEmptied={() => setReady(false)}
        onError={() => setReady(false)}
      />
    </div>
  );
}
function Ambience({ songIndex, motion = true, className = "" }) {
  return (
    <div className={`ambience ${className}`} aria-hidden="true">
      {SONGS.map((song, i) => (
        <BackgroundLayer
          key={song.title}
          song={song} active={i === songIndex} motion={motion}
        />
      ))}
    </div>
  );
}
function IntroLogo({ onComplete }) {
  const logo = useRef(null), host = useRef(null);
  useLayoutEffect(() => {
    const node = logo.current;
    const target = document.querySelector(".site-header .brand img");
    if (!node || !target) { onComplete(); return; }
    let started = false, touchY = null, animations = [];
    const previousOverflow = document.body.style.overflow;
    window.scrollTo({ top: 0, behavior: "instant" });
    document.body.style.overflow = "hidden";
    host.current?.focus({ preventScroll: true });
    const begin = () => {
      if (started) return;
      started = true;
      window.scrollTo({ top: 0, behavior: "instant" });
      const from = node.getBoundingClientRect(), to = target.getBoundingClientRect();
      host.current?.classList.add("leaving");
      const animation = node.animate([
        { transform: "translate(0,0) scale(1)" },
        { transform: `translate(${to.left-from.left}px,${to.top-from.top}px) scale(${to.width/from.width})` },
      ], { duration: 1000, easing: "cubic-bezier(.65,0,.22,1)", fill: "forwards" });
      animations.push(animation);
      animation.onfinish = onComplete;
    };
    const timer = window.setTimeout(begin, 5000);
    const wheel = event => { if (event.deltaY > 0) { event.preventDefault(); begin(); } };
    const touchStart = event => { touchY = event.touches[0]?.clientY; };
    const touchMove = event => { if (touchY !== null && event.touches[0]?.clientY < touchY) { event.preventDefault(); begin(); } };
    const key = event => { if (event.key === "Tab") event.preventDefault(); if (["ArrowDown", "PageDown", " ", "Enter", "Escape"].includes(event.key)) { event.preventDefault(); begin(); } };
    document.addEventListener("wheel", wheel, { passive: false });
    document.addEventListener("touchstart", touchStart, { passive: true });
    document.addEventListener("touchmove", touchMove, { passive: false });
    document.addEventListener("keydown", key);
    return () => {
      clearTimeout(timer);
      animations.forEach(animation => animation.cancel());
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("wheel", wheel);
      document.removeEventListener("touchstart", touchStart);
      document.removeEventListener("touchmove", touchMove);
      document.removeEventListener("keydown", key);
    };
  }, [onComplete]);
  return <div ref={host} className="intro-logo" role="dialog" aria-modal="true" tabIndex="-1" aria-label="GluSpot 欢迎画面，5 秒后进入，也可按回车或 Escape 继续"><div className="intro-veil" /><img ref={logo} src={`${ASSET}gluspot.svg`} alt="GluSpot" /><div className="intro-wordmark"><strong>GluSpot</strong><span>让音乐，融入桌面</span></div></div>;
}
function Lyric({ song, playing, translated, motion = true, clock }) {
  const player = useRef(null);
  const [ready, setReady] = useState(false);
  const [visible, setVisible] = useState(false);
  const element = useRef(null);
  const name = `${song.tone}-${translated ? "translated" : "original"}`;
  useEffect(() => setReady(false), [name]);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    if (element.current) observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const node = player.current;
    if (!node) return;
    if (playing && motion && visible) node.play().catch(() => setReady(false));
    else node.pause();
  }, [playing, motion, visible, name]);
  if (song.tone !== "noah") return (
    <div ref={element} className="native-lyric-demo">
      <img src={`${ASSET}lyrics/${name}.png`} alt={song.tone === "chace" ? "The Rain Song 多行歌词与译文演示" : "Ibuprofen 多行歌词与译文演示"} />
      <video key={name} ref={player} src={`${ASSET}lyrics/${name}.webm`} muted loop playsInline preload="auto"
        className={ready && motion ? "ready" : ""} aria-hidden="true"
        onLoadedMetadata={(e) => { if (clock?.current.tone === song.tone) e.currentTarget.currentTime = clock.current.time; }}
        onTimeUpdate={(e) => { if (clock?.current.tone === song.tone) clock.current.time = e.currentTarget.currentTime; }}
        onLoadedData={() => { setReady(true); if (playing && motion && visible) player.current?.play().catch(() => setReady(false)); }}
        onError={() => setReady(false)} />
    </div>
  );
  return (
    <div
      className={`demo-lyric reading ${playing ? "" : "paused"}`}
      key={song.title}
    >
      <p className="lyric-main">
        <span>{song.lyric}</span>
      </p>
      {translated && <p className="lyric-translation">{song.translation}</p>}
    </div>
  );
}
function Settings({
  state,
  setState,
  strength,
  setStrength,
  motion,
  setMotion,
}) {
  const panel = useRef(null),
    restore = useRef(null),
    previous = useRef(null);
  useEffect(() => {
    if (state !== "open" && state !== "expanded") return;
    previous.current = document.activeElement;
    panel.current?.querySelector("button")?.focus({ preventScroll: true });
    const key = (e) => {
      if (e.key === "Escape") setState("closed");
      if (e.key === "Tab") {
        const controls = [...panel.current.querySelectorAll("button,input")];
        const first = controls[0],
          last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      (previous.current?.isConnected && previous.current !== document.body
        ? previous.current
        : document.querySelector('button[aria-label="外观设置"]')
      )?.focus({ preventScroll: true });
    };
  }, [state, setState]);
  useEffect(() => {
    if (state === "minimized") restore.current?.focus({ preventScroll: true });
  }, [state]);
  if (state === "closed") return null;
  if (state === "minimized")
    return (
      <button
        ref={restore}
        className="settings-restore glass"
        onClick={() => setState("open")}
      >
        <GearSix size={18} />
        恢复外观设置
        <ArrowUpRight size={16} />
      </button>
    );
  return (
    <div className="settings-backdrop" onClick={() => setState("closed")}>
      <section
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-heading"
        className={`settings-panel glass ${state === "expanded" ? "expanded" : ""}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="settings-titlebar">
          <div className="traffic-lights">
            <button
              className="traffic red"
              aria-label="关闭设置"
              onClick={() => setState("closed")}
            >
              <X size={10} weight="bold" />
            </button>
            <button
              className="traffic yellow"
              aria-label="收起设置"
              onClick={() => setState("minimized")}
            >
              <Minus size={10} weight="bold" />
            </button>
            <button
              className="traffic green"
              aria-label={state === "expanded" ? "还原设置大小" : "展开设置"}
              onClick={() =>
                setState(state === "expanded" ? "open" : "expanded")
              }
            >
              <CornersOut size={10} weight="bold" />
            </button>
          </div>
          <h2 id="settings-heading">外观设置</h2>
          <span className="settings-version">GluSpot</span>
        </div>
        <div className="settings-body">
          <div className="settings-preview">
            <Cover song={SONGS[0]} />
            <div>
              <span>The Rain Song</span>
              <small>Liquid Glass · 交互演示</small>
            </div>
            <Pause size={23} weight="fill" />
          </div>
          <label className="range-label" htmlFor="glass-strength">
            <span>玻璃材质强度</span>
            <output>{strength}%</output>
          </label>
          <input
            id="glass-strength"
            type="range"
            min="0"
            max="100"
            value={strength}
            onChange={(e) => setStrength(Number(e.target.value))}
          />
          <p className="setting-help">强度越高，材质越不透明，背景模糊越强。</p>
          <label className="switch-row">
            <span>
              动态效果<small>背景流动与自动演示</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              checked={motion}
              onChange={(e) => setMotion(e.target.checked)}
            />
            <span className="switch-face" aria-hidden="true" />
          </label>
          <p className="settings-footnote">让桌面的光，柔和一点。</p>
        </div>
      </section>
    </div>
  );
}
function DeviceDemo({ selected, volumes, onToggle, onVolume }) {
  const devices = [
    { id: "small", name: "Wi-Fi 音箱 · 01", icon: "wifi-small.svg" },
    { id: "large", name: "Wi-Fi 音箱 · 02", icon: "wifi-large.svg" },
    { id: "headphones", name: "耳机" },
  ];
  return (
    <div className="device-demo glass">
      <div className="device-demo-title">
        <span>一起播放</span>
        <small>交互演示 · 无音频</small>
      </div>
      <div className="device-demo-rows">
        {devices.map((device) => (
          <div
            key={device.id}
            className={`device-demo-row ${selected.includes(device.id) ? "selected" : ""}`}
          >
            <button
              aria-label={`${selected.includes(device.id) ? "移出" : "加入"}同步：${device.name}`}
              aria-pressed={selected.includes(device.id)}
              onClick={() => onToggle(device.id)}
            >
              {device.icon ? (
                <img
                  src={`${ASSET}${device.icon}`}
                  alt=""
                  width="25"
                  height="25"
                />
              ) : (
                <Headphones size={25} />
              )}
              <span>{device.name}</span>
              {selected.includes(device.id) && <Check size={15} />}
            </button>
            <input
              aria-label={`${device.name}音量`}
              type="range"
              min="0"
              max="100"
              value={volumes[device.id]}
              disabled={!selected.includes(device.id)}
              onChange={(e) => onVolume(device.id, Number(e.target.value))}
            />
          </div>
        ))}
      </div>
      <p>
        {selected.length > 1
          ? `${selected.length} 台设备，正在一起播放`
          : selected.length === 1
            ? "1 台设备已选择"
            : "选择设备，加入同步演示"}
      </p>
    </div>
  );
}
function RoomExperience({ motion }) {
  const [selected, setSelected] = useState(["small", "large"]);
  const [volumes, setVolumes] = useState({
    small: 65,
    large: 45,
    headphones: 60,
  });
  const [visible, setVisible] = useState(false);
  const section = useRef(null);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) =>
      setVisible(entry.isIntersecting),
    );
    observer.observe(section.current);
    return () => observer.disconnect();
  }, []);
  const lit = (id) => selected.includes(id) && volumes[id] > 0;
  const scene = lit("small")
    ? lit("large")
      ? "room-devices-on"
      : "room-small-on"
    : lit("large")
      ? "room-large-on"
      : "room-devices-off";
  return (
    <section
      ref={section}
      className={`room-section ${motion && visible ? "waves-running" : "waves-paused"}`}
      id="experience"
      aria-labelledby="room-title"
    >
      <div className="room-visual" aria-hidden="true">
        <div className="room-scene" data-scene={scene}>
            <img
              className="room-image shown"
              src={`${ASSET}room-devices-off.webp`}
              alt=""
              width="1774"
              height="887"
              loading="lazy"
            />
          {["small", "large", "headphones"].map((id) => (
            <div
              key={id}
              className={`device-sound sound-${id} ${lit(id) ? "active" : ""}`}
              style={{ "--gain": volumes[id] / 100 }}
              data-volume={volumes[id]}
              data-active={lit(id)}
            >
              <img
                className="soundwave first"
                src={`${ASSET}speaker-waves.webp`}
                alt=""
              />
              <img
                className="soundwave second"
                src={`${ASSET}speaker-waves.webp`}
                alt=""
              />
            </div>
          ))}
          {["small", "large"].map((id) => (
            <img key={id} className={`playback-light light-${id} ${lit(id) ? "active" : ""}`}
              src={`${ASSET}speaker-playback-light.webp`} alt=""
              style={{ "--gain": volumes[id] / 100 }} />
          ))}
        </div>
      </div>
      <div className="room-copy">
        <span className="eyebrow">让好音乐，多响一会儿</span>
        <h2 id="room-title">
          从耳机，
          <br />
          到整个房间。
        </h2>
        <p>
          独自沉浸，也能与空间共鸣。
          <br />
          让你喜欢的声音，在多台设备间一起响起。
        </p>
        <div className="device-tags">
          <span>
            <Headphones size={17} />
            本机 / 有线 / 蓝牙
          </span>
          <span>
            <SpeakerHigh size={17} />
            Wi-Fi 音频设备
          </span>
        </div>
        <p className="fine-print">
          支持兼容 AirPlay 的设备。同步效果因设备组合与网络环境而异。
        </p>
      </div>
      <DeviceDemo
        selected={selected}
        volumes={volumes}
        onToggle={(id) =>
          setSelected((ids) =>
            ids.includes(id)
              ? ids.filter((value) => value !== id)
              : [...ids, id],
          )
        }
        onVolume={(id, value) =>
          setVolumes((previous) => ({ ...previous, [id]: value }))
        }
      />
    </section>
  );
}
function DesktopBackdrop() {
  return (
    <div className="simulated-desktop" aria-hidden="true">
      <img
        className="desktop-wallpaper"
        src={`${ASSET}desktop-wallpaper.webp`}
        alt=""
        width="1586"
        height="992"
      />
      <div className="desktop-shortcuts">
        <span>
          <WindowsLogo size={25} />
          此电脑
        </span>
        <span>
          <MusicNotes size={25} />
          音乐
        </span>
        <span>
          <img src={`${ASSET}gluspot.svg`} alt="" />
          GluSpot
        </span>
      </div>
      <div className="simulated-taskbar">
        <WindowsLogo size={19} weight="fill" />
        <MusicNotes size={20} />
        <img src={`${ASSET}gluspot.svg`} alt="" />
        <span className="taskbar-time">21:08</span>
      </div>
    </div>
  );
}
function PersonalDjDemo({ song, motion, onNext }) {
  const [speaking, setSpeaking] = useState(false);
  const card = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    observer.observe(card.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!speaking || !motion || !visible) return;
    const timer = setTimeout(() => setSpeaking(false), 6500);
    return () => clearTimeout(timer);
  }, [speaking, motion, visible, song.tone]);
  const state = speaking ? "speaking" : "ready";
  return (
    <div ref={card} className={`dj-card glass dj-state-${state}`}>
      <span className="dj-eyebrow">
        <img src={`${ASSET}personal-fm.svg`} alt="" width="25" height="25" />
        私人漫游 · PRIVATE DJ
      </span>
      <div className="dj-art">
        <Cover song={song} />
        <button
          className="native-dj"
          onClick={() => setSpeaking((value) => !value)}
          aria-label={
            speaking ? "暂停私人 DJ 播报演示" : "开始私人 DJ 播报演示"
          }
          aria-pressed={speaking}
        >
          <img
            src={`${ASSET}dj/${song.tone}-${state}${motion && visible ? "" : "-still"}.webp`}
            alt="GluSpot 原生私人 DJ 五环动画"
            width="240"
            height="240"
          />
        </button>
      </div>
      <h3>
        下一首惊喜，
        <br />
        交给你的私人 DJ。
      </h3>
      <p>
        私人漫游与专属播报，
        <br />
        让歌曲与播报，自然衔接。
      </p>
      <div className="dj-track"><strong>{song.title}</strong><span>{song.artist}</span></div>
      <div className="dj-actions">
        <button className="dj-start glass" aria-pressed={speaking} onClick={() => setSpeaking(value => !value)}>
          {speaking ? <Pause size={17} weight="fill" /> : <Play size={17} weight="fill" />}
          {speaking ? "暂停播报" : "体验私人 DJ"}
        </button>
        <IconButton label="私人 DJ 下一首演示" onClick={() => { onNext(); setSpeaking(true); }}><SkipForward size={19} weight="fill" /></IconButton>
      </div>
      <div className="dj-status" role="status"><CheckCircle size={14} weight="fill" />{speaking ? "正在播报" : "下一首播报已准备好"}</div>
      <small className="dj-silent-note">原生动效 · 静音演示</small>
    </div>
  );
}
function NativeIsland({ song, mode = "free", className = "", onClick, tabIndex = 0 }) {
  return (
    <button
      className={`native-island ${className}`}
      onClick={onClick}
      aria-label="点击音乐岛展开播放界面"
      title="点击展开播放界面"
      tabIndex={tabIndex}
    >
      <img
        src={`${ASSET}islands/${song.tone}-${mode}.png`}
        alt={`GluSpot ${mode === "tasker" ? "Tasker" : mode} 实际岛态：${song.lyric}`}
        width={mode === "tasker" ? 600 : 720}
        height={
          mode === "top"
            ? 124
            : mode === "finder"
              ? 60
              : mode === "tasker"
                ? 80
                : 92
        }
      />
    </button>
  );
}
export function App() {
  const [introDone, setIntroDone] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches || !["", "#top"].includes(window.location.hash));
  const finishIntro = useCallback(() => setIntroDone(true), []);
  const [songIndex, setSongIndex] = useState(0),
    [layout, setLayout] = useState("outside"),
    [playing, setPlaying] = useState(true),
    [translated, setTranslated] = useState(true),
    [settings, setSettings] = useState("closed"),
    [strength, setStrength] = useState(45),
    [motion, setMotion] = useState(
      () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
    [presentation, setPresentation] = useState("island"),
    [pageVisible, setPageVisible] = useState(!document.hidden);
  const playerRef = useRef(null),
    surfaceController = useRef(null);
  const lyricClock = useRef({ tone: SONGS[0].tone, time: 0 });
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash || hash === "#top") return;
    const frame = requestAnimationFrame(() => document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "instant", block: "start" }));
    return () => cancelAnimationFrame(frame);
  }, []);
  const manualView = (next) => {
    const index = next === "island" ? 0 : next === "fullscreen" ? 2 : 1;
    surfaceController.current?.seek(index * 3500 / storyDuration(3));
    setPresentation(next);
  };
  const manualLayout = (next) => {
    const current = playerRef.current?.querySelector(".native-lyric-demo video");
    if (current) lyricClock.current.time = current.currentTime;
    setLayout(next);
    if (presentation === "island") manualView("window");
  };
  const manualSong = (next) => {
    if (next !== songIndex) lyricClock.current = { tone: SONGS[next].tone, time: 0 };
    setSongIndex(next);
  };
  useEffect(() => {
    const visibility = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);
  useEffect(() => {
    const nodes = document.querySelectorAll(
      ".room-copy,.section-heading,.source-copy,.dj-card,.final-download",
    );
    const observer = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("revealed");
            observer.unobserve(e.target);
          }
        }),
      { threshold: 0.1 },
    );
    nodes.forEach((node) => {
      node.classList.add("scroll-reveal");
      observer.observe(node);
    });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const active = settings === "open" || settings === "expanded";
    if (!active) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [settings]);
  const song = SONGS[songIndex],
    changeSong = () => manualSong((songIndex + 1) % SONGS.length);
  const currentLayout = LAYOUTS.find((item) => item.id === layout);
  const LayoutIcon = currentLayout.icon;
  const glassStyle = {
    "--glass-alpha": `${0.08 + strength * 0.002}`,
    "--glass-blur": `${8 + strength * 0.28}px`,
  };
  return (
    <div
      id="top"
      className={`site ${song.tone} ${motion ? "motion-on" : "motion-off"} ${introDone ? "intro-done" : "intro-playing"}`}
      style={glassStyle}
    >
      {!introDone && <IntroLogo onComplete={finishIntro} />}
      <a className="skip-link" href="#player-story" inert={!introDone}>
        跳到互动演示
      </a>
      <header
        className="site-header"
        inert={!introDone || settings === "open" || settings === "expanded"}
      >
        <a className="brand" href="#top" aria-label="GluSpot 首页">
          <img src={`${ASSET}gluspot.svg`} alt="" width="50" height="50" />
          <span>GluSpot</span>
        </a>
        <nav aria-label="主导航">
          <a href="#island">音乐岛</a>
          <a href="#player-story">播放体验</a>
          <a href="#download">下载</a>
        </nav>
        <a
          className="github-link"
          aria-label="GitHub 开源项目"
          href={REPO}
          target="_blank"
          rel="noreferrer"
        >
          <GithubLogo size={25} weight="fill" />
          <span>GitHub</span>
          <ArrowUpRight className="github-arrow" size={14} />
        </a>
      </header>
      <main inert={!introDone || settings === "open" || settings === "expanded"}>
        <OpeningHero />
        <IslandStory backdrop={DesktopBackdrop} motion={motion} opening={!introDone} />
        <SurfaceStory onPresentation={setPresentation} controller={surfaceController} motion={motion} enabled={settings === "closed"}>
          <div
            className={`player-wrap presentation-${presentation}`}
            id="player"
          >
            <div className="player-stage">
              <DesktopBackdrop />
              <article
                ref={playerRef}
                className={`player glass surface-morph layout-${layout} view-window`}
                data-presentation={presentation}
                aria-label="GluSpot 互动播放器演示"
              >
                <NativeIsland
                  song={song}
                  className="hero-native-island"
                  tabIndex={presentation === "island" ? 0 : -1}
                  onClick={() => {
                    manualView("window");
                  }}
                />
                <Ambience
                  songIndex={songIndex}
                  motion={motion && pageVisible && presentation !== "island"}
                  className="player-ambience"
                />
                <div className="player-topline" inert={presentation === "island"}>
                  <span className="demo-tag">
                    GLUSPOT <span>／ 交互演示 · 无音频</span>
                  </span>
                  <div className="player-tools">
                    <IconButton
                      label={`切换播放布局：当前${currentLayout.label}`}
                      onClick={() =>
                        manualLayout(
                          LAYOUTS[
                            (LAYOUTS.findIndex((item) => item.id === layout) +
                              1) %
                              LAYOUTS.length
                          ].id,
                        )
                      }
                    >
                      <LayoutIcon size={19} />
                    </IconButton>
                    <IconButton
                      label="收拢为音乐岛"
                      onClick={() => {
                        manualView("island");
                      }}
                    >
                      <Minus size={18} />
                    </IconButton>
                    <IconButton
                      label={
                        presentation === "fullscreen"
                          ? "返回窗口展示"
                          : "切换到全屏展示"
                      }
                      onClick={() => {
                        manualView(
                          presentation === "fullscreen"
                            ? "window"
                            : "fullscreen",
                        );
                      }}
                    >
                      <CornersOut size={18} />
                    </IconButton>
                    <IconButton
                      label={translated ? "隐藏译文" : "显示译文"}
                      aria-pressed={translated}
                      onClick={() => setTranslated((x) => !x)}
                    >
                      <Translate size={19} />
                    </IconButton>
                    <IconButton
                      label="外观设置"
                      onClick={() => setSettings("open")}
                    >
                      <SlidersHorizontal size={18} />
                    </IconButton>
                  </div>
                </div>
                <div className="player-content" inert={presentation === "island"}>
                  <div className="album-block">
                    <div className="cover-frame">
                      <div className="cover-stack">
                        {SONGS.map((item, i) => (
                          <Cover
                            key={item.title}
                            song={item}
                            className={i === songIndex ? "current" : ""}
                            aria-hidden={i !== songIndex}
                          />
                        ))}
                      </div>
                      {layout === "inside" && (
                        <div className="inside-lyric">
                          <Lyric
                            song={song}
                            translated={translated}
                            playing={playing}
                            motion={motion && pageVisible}
                            clock={lyricClock}
                          />
                        </div>
                      )}
                    </div>
                    <div className="track-meta">
                      <div>
                        <h2>{song.title}</h2>
                        <p>{song.artist}</p>
                      </div>
                      <div className="transport">
                        <button
                          aria-label="上一首演示歌曲"
                          onClick={() =>
                            manualSong(
                              (songIndex - 1 + SONGS.length) % SONGS.length,
                            )
                          }
                        >
                          <SkipBack size={27} weight="fill" />
                        </button>
                        <button
                          className="play-button"
                          aria-label={playing ? "暂停歌词演示" : "播放歌词演示"}
                          aria-pressed={playing}
                          onClick={() => setPlaying((x) => !x)}
                        >
                          {playing ? (
                            <Pause size={31} weight="fill" />
                          ) : (
                            <Play size={31} weight="fill" />
                          )}
                        </button>
                        <button
                          aria-label="下一首演示歌曲"
                          onClick={changeSong}
                        >
                          <SkipForward size={27} weight="fill" />
                        </button>
                      </div>
                    </div>
                  </div>
                  <div
                    className="outside-panel"
                    aria-hidden={layout !== "outside"}
                  >
                    {layout === "outside" && (
                      <Lyric
                        song={song}
                        playing={playing}
                        translated={translated}
                        motion={motion && pageVisible}
                        clock={lyricClock}
                      />
                    )}
                  </div>
                  <div className="queue-panel" aria-hidden={layout !== "queue"}>
                    {layout === "queue" && (
                      <>
                        <p className="queue-label">
                          正在播放 <span>03 首演示曲目</span>
                        </p>
                        {SONGS.map((item, i) => (
                          <button
                            key={item.title}
                            className={`queue-row ${i === songIndex ? "selected" : ""}`}
                            aria-label={`切换到 ${item.artist} ${item.title}`}
                            onClick={() => manualSong(i)}
                          >
                            <Cover song={item} />
                            <span>
                              <strong>{item.title}</strong>
                              <small>{item.artist}</small>
                            </span>
                            {i === songIndex ? (
                              <Waveform size={21} />
                            ) : (
                              <Play size={18} weight="fill" />
                            )}
                          </button>
                        ))}
                      </>
                    )}
                  </div>
                </div>
                <span className="layout-state">
                  {currentLayout.label}
                </span>
              </article>
              <span
                className={`island-open-hint ${presentation === "island" ? "shown" : ""}`}
              >
                点击音乐岛，展开播放界面
              </span>
            </div>
            <div className="song-switch" role="group" aria-label="演示歌曲">
              {SONGS.map((item, i) => (
                <button
                  key={item.title}
                  aria-label={`${item.artist} · ${item.title}`}
                  aria-pressed={i === songIndex}
                  onClick={() => manualSong(i)}
                >
                  <span className="song-dot" />
                  <span>
                    {item.artist}
                    <span className="song-title-small"> · {item.title}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </SurfaceStory>
        <HighlightStory motion={motion && pageVisible} />
        <RoomExperience motion={motion && pageVisible} />
        <section
          className="sources-section section-shell"
          aria-labelledby="sources-title"
        >
          <div className="source-copy">
            <span className="eyebrow">从熟悉的地方，开始听</span>
            <h2 id="sources-title">
              你熟悉的音乐，
              <br />
              都在这里。
            </h2>
            <p>
              在 GluSpot 登录，浏览音乐库、搜索并播放。
              <br />
              也可以继续使用原来的播放器，让小岛跟随系统音频。
            </p>
            <div className="online-options">
              <span>
                <MusicNotes size={21} />
                在线音乐
              </span>
              <span>
                <Waveform size={21} />
                系统音频抓取
              </span>
            </div>
            <p className="fine-print">
              在线音乐服务持续适配中。系统抓取依赖外部播放器的媒体支持，体验略逊于程序内播放。
            </p>
          </div>
          <PersonalDjDemo song={song} motion={motion && pageVisible} onNext={changeSong} />
        </section>
        <section
          className="final-download section-shell"
          id="download"
          aria-labelledby="download-title"
        >
          <img
            className="installer-loop"
            src={`${ASSET}${motion ? "installer-loop.gif" : "gluspot.svg"}`}
            width="700"
            height="112"
            alt="GluSpot 安装循环动画"
            loading="lazy"
          />
          <span className="eyebrow">THIS IS TRUE MUSIC</span>
          <h2 id="download-title">音乐，随心而动。</h2>
          <p>一座岛，一整个音乐世界。</p>
          <a
            className="primary-button"
            href={RELEASE}
            target="_blank"
            rel="noreferrer"
          >
            <WindowsLogo size={24} weight="fill" />
            <span>下载 GluSpot</span>
            <ArrowUpRight size={21} />
          </a>
          <div className="release-details">
            <a href={RELEASE} target="_blank" rel="noreferrer">
              6.417 未签名测试版 <ArrowUpRight size={12} />
            </a>
            <span>安装版 & 便携版</span>
            <span>Windows 10 1809+ / 11 x64</span>
          </div>
        </section>
      </main>
      <footer
        className="site-footer section-shell"
        inert={!introDone || settings === "open" || settings === "expanded"}
      >
        <div className="footer-main">
          <a className="brand" href="#top">
            <img src={`${ASSET}gluspot.svg`} width="40" height="40" alt="" />
            <span>GluSpot</span>
          </a>
          <span>让音乐融入桌面。</span>
          <a href={REPO} target="_blank" rel="noreferrer">
            GitHub <ArrowUpRight size={14} />
          </a>
          <a
            href={`${REPO}/blob/main/LICENSE`}
            target="_blank"
            rel="noreferrer"
          >
            MIT 开源
          </a>
        </div>
        <div className="credits">
          <p>
            演示音乐：
            <a
              href="https://chace.lnk.to/BelatedSuffocation"
              target="_blank"
              rel="noreferrer"
            >
              Chace · The Rain Song
            </a>{" "}
            /{" "}
            <a
              href="https://alicks14.bandcamp.com/track/ibuprofen"
              target="_blank"
              rel="noreferrer"
            >
              Alicks · Ibuprofen
            </a>
            /{" "}
            <a
              href="https://soundcloud.com/goodboynoah/airport-bar-prod-by-evil-needle"
              target="_blank"
              rel="noreferrer"
            >
              goodboy noah · Airport Bar
            </a>
            。专辑封面版权归各自权利人所有。网页演示不播放音频。
          </p>
          <p>空间场景为生成示意图，不代表特定设备或兼容性承诺。</p>
        </div>
      </footer>
      <Settings
        state={settings}
        setState={setSettings}
        strength={strength}
        setStrength={setStrength}
        motion={motion}
        setMotion={setMotion}
      />
    </div>
  );
}
