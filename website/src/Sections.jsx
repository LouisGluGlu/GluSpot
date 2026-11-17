import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown, ArrowUpRight, ArrowsLeftRight, Check, CheckCircle, ClockCounterClockwise, Code, CornersIn, Drop, EyeSlash, Headphones,
  Lightning, ListPlus, MagnifyingGlass, Magnet, MusicNotes, Package, Pause, Play, PlayCircle, Queue, SkipBack, SkipForward, Sparkle,
  SpeakerHigh, TextAa, Translate, Waveform, WindowsLogo,
} from "@phosphor-icons/react";
import { ASSET, SONGS } from "./data.js";
import { Ambience, Cover, LyricMedia } from "./PlayerScene.jsx";
import { onFrame, useAutoVideo, useInView, useMagnetic } from "./lib/motion.js";

function Head({ eyebrow, title, id, children, align = "center" }) {
  return <header className={`head ${align}`} data-rv>
    <span className="eyebrow">{eyebrow}</span>
    <h2 id={id}>{title}</h2>
    {children && <p>{children}</p>}
  </header>;
}

/* ---------- Hero ---------- */
export function Hero({ release, ready }) {
  const inner = useRef(null), light = useRef(null), host = useRef(null);
  const primary = useMagnetic(), ghost = useMagnetic(0.18);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const stop = onFrame(() => {
      const node = inner.current;
      if (!node) return false;
      const p = Math.min(1, window.scrollY / Math.max(1, window.innerHeight * 0.8));
      node.style.opacity = (1 - p * 1.15).toFixed(3);
      node.style.transform = `translate3d(0,${(-p * 70).toFixed(1)}px,0) scale(${(1 - p * 0.06).toFixed(4)})`;
      return false;
    });
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    const move = event => {
      const box = host.current.getBoundingClientRect();
      light.current.style.transform = `translate3d(${event.clientX - box.left}px,${event.clientY - box.top}px,0)`;
      light.current.style.opacity = "1";
    };
    if (fine) host.current.addEventListener("pointermove", move, { passive: true });
    return () => { stop(); host.current?.removeEventListener("pointermove", move); };
  }, []);
  return <section ref={host} className={`hero${ready ? " ready" : ""}`} aria-labelledby="hero-title">
    <div className="aurora" aria-hidden="true"><i /><i /><i /><b /></div>
    <div ref={light} className="cursor-light" aria-hidden="true" />
    <div ref={inner} className="hero-inner">
      <a className="badge" href={release.url} target="_blank" rel="noreferrer">
        <b>{release.version ? `v${release.version}` : "最新版"}</b><span>海平面光影 · Liquid Glass · 逐字歌词</span>
      </a>
      <h1 id="hero-title"><span className="line"><span>让音乐</span></span><span className="line"><span>融入桌面</span></span></h1>
      <p className="hero-sub">桌面上的一座音乐岛。小到一枚胶囊，大到整片海——歌词逐字读亮，光随鼓点起落，从耳机一路响到整个房间。</p>
      <div className="hero-actions">
        <a ref={primary} className="btn primary" href={release.url} target="_blank" rel="noreferrer"><WindowsLogo size={20} weight="fill" />免费下载<ArrowUpRight size={17} /></a>
        <a ref={ghost} className="btn ghost" href="#island">看看它如何工作<ArrowDown size={17} /></a>
      </div>
      <ul className="hero-facts" aria-label="要点">
        <li>开源免费 · MIT</li><li>Windows 10 1809+ / 11 x64</li><li>安装版与便携版</li>
      </ul>
    </div>
    <span className="hero-cue" aria-hidden="true">向下滚动，唤醒小岛</span>
  </section>;
}

/* ---------- Marquee ---------- */
const TAGS = ["Free", "Top", "任务栏嵌入", "Finder 栏", "逐字歌词", "翻译 · 音译 · 粤拼", "音乐高光", "海平面背景", "Liquid Glass", "窄版窗口", "四种版式", "多设备同步", "在线音乐库", "系统媒体抓取", "私人 DJ", "MIT 开源"];
export function Marquee() {
  return <div className="marquee" aria-hidden="true">
    <div>{[...TAGS, ...TAGS].map((tag, index) => <span key={index}>{tag}</span>)}</div>
  </div>;
}

/* ---------- Lyrics ---------- */
const LYRIC_POINTS = [
  { icon: TextAa, title: "逐字读亮", text: "有逐字时间，就一个字一个字亮起；长句换行后读到第二行，也不会弹跳。" },
  { icon: Translate, title: "次行：翻译、音译、粤拼", text: "译文与读音跟在原文下一行。所选内容缺失时自动改用另一项，粤语歌可以显示粤拼。" },
  { icon: ArrowsLeftRight, title: "繁体与简体", text: "歌词可在繁简之间转换，用字保持原意。" },
  { icon: Sparkle, title: "音乐高光", text: "慢唱的字被单独点亮、轻轻抬起，读歌词像在看现场。" },
];
export function Lyrics({ motion }) {
  const [tone, setTone] = useState(0);
  const [translated, setTranslated] = useState(true);
  const highlight = useAutoVideo(motion);
  const song = SONGS[tone];
  return <section className="section lyrics" id="lyrics" aria-labelledby="lyrics-title">
    <div className="shell">
      <Head eyebrow="歌词" id="lyrics-title" title={<>读到哪里，<br />光就走到哪里。</>}>生产环境的歌词绘制器原样导出：逐字抬升、发光，译文同步。</Head>
      <figure className="highlight-card spot" data-rv>
        <div className={`highlight-media${highlight.ready && motion ? " on" : ""}`}>
          <img src={`${ASSET}highlight/music-highlight.png`} alt="音乐高光，感知清晰！" width="960" height="300" loading="lazy" decoding="async" />
          <video ref={highlight.ref} src={`${ASSET}highlight/music-highlight.webm`} muted loop playsInline preload="none" aria-hidden="true"
            onLoadedData={() => highlight.setReady(true)} onError={() => highlight.setReady(false)} />
        </div>
        <figcaption>原生逐字高光 · 静音演示，文字时钟为单独编写</figcaption>
      </figure>
      <div className="lyric-grid">
        <div className={`lyric-card spot tone-${song.tone}`} data-rv>
          <Ambience songIndex={tone} motion={motion} />
          <div className="lyric-card-body"><LyricMedia song={song} translated={translated} motion={motion} /></div>
          <div className="lyric-card-bar">
            <div className="seg small" role="group" aria-label="歌词演示歌曲">
              {SONGS.filter(item => item.lyrics).map((item, index) => <button key={item.tone} type="button" aria-pressed={tone === index} onClick={() => setTone(index)}>{item.title}</button>)}
            </div>
            <button type="button" className="chip-toggle" aria-pressed={translated} onClick={() => setTranslated(value => !value)}><Translate size={16} />译文</button>
          </div>
        </div>
        <ul className="points">
          {LYRIC_POINTS.map((item, index) => <li key={item.title} data-rv style={{ "--d": `${index * 70}ms` }}>
            <span className="point-icon"><item.icon size={20} /></span>
            <div><h3>{item.title}</h3><p>{item.text}</p></div>
          </li>)}
        </ul>
      </div>
    </div>
  </section>;
}

/* ---------- Sea level ---------- */
export function SeaLevel({ motion }) {
  const [index, setIndex] = useState(0);
  const [previous, setPrevious] = useState(0);
  const [host, visible] = useInView();
  const pick = next => { if (next === index) return; setPrevious(index); setIndex(next); };
  const song = SONGS[index], before = SONGS[previous];
  const field = pair => ({ "--a": pair[0], "--b": pair[1] });
  return <section className="section sea-section" id="sea" aria-labelledby="sea-title">
    <div className="shell split">
      <div className="split-copy" data-rv>
        <span className="eyebrow">海平面背景</span>
        <h2 id="sea-title">颜色来自封面，<br />光随鼓点起落。</h2>
        <p>海平面保留封面自己的色相，按色彩距离与和谐度挑出一对搭配——不再只有固定的几种拼法。</p>
        <ul className="ticks">
          <li><Drop size={18} />从封面取色，一首歌一种海。</li>
          <li><Lightning size={18} />闪光以鼓点为先，人声门槛更高，光斑更小、不晃眼。</li>
          <li><Waveform size={18} />变色在高潮落定：先缓缓铺开，再一口气扫过，颜色落下时页面暗一瞬，新颜色破光而出。</li>
          <li><TextAa size={18} />未读的字带上背景的色调：深底是同色浅色，浅底是同色深色。</li>
        </ul>
      </div>
      <div ref={host} className={`sea spot${motion && visible ? " running" : ""}`} data-rv style={{ "--glow": song.glow }}>
        <div className="sea-field" style={field(before.sea)} />
        <div className="sea-field next" key={index} style={field(song.sea)} />
        <div className="sea-light" />
        <div className="sea-vignette" />
        <div className="sea-lyric"><LyricMedia song={song} translated motion={motion} /></div>
        <div className="sea-bar">
          <div className="song-picks" role="group" aria-label="切换封面，观察取色">
            {SONGS.map((item, i) => <button key={item.tone} type="button" aria-pressed={i === index} aria-label={`${item.artist} · ${item.title}`} onClick={() => pick(i)}><Cover song={item} alt="" /></button>)}
          </div>
          <span className="sea-swatch" aria-hidden="true"><i style={{ background: song.sea[0] }} /><i style={{ background: song.sea[1] }} /></span>
          <small>网页示意动画 · 取色自演示封面 · 固定节拍</small>
        </div>
      </div>
    </div>
  </section>;
}

/* ---------- Liquid Glass ---------- */
export function Glass({ songIndex, motion, setMotion }) {
  const [strength, setStrength] = useState(45);
  const song = SONGS[songIndex];
  const style = { "--g-blur": `${(4 + strength * 0.195).toFixed(1)}px`, "--g-tint": (0.15 + strength * 0.0045).toFixed(3) };
  return <section className="section glass-section" id="glass" aria-labelledby="glass-title">
    <div className="shell split reverse">
      <div className="split-copy" data-rv>
        <span className="eyebrow">Liquid Glass</span>
        <h2 id="glass-title">透光的控件，<br />跟着画面取色。</h2>
        <p>按钮、设备控制与“添加到歌单”等二级菜单是同一块玻璃：一个强度同时决定模糊与着色，边缘留一道亮线。</p>
        <ul className="ticks">
          <li><Drop size={18} />按钮底色跟随背后的画面。</li>
          <li><Queue size={18} />二级菜单行扁平排布，文字随主题取色。</li>
          <li><Sparkle size={18} />浅色与深色主题各自记住控件透明度。</li>
        </ul>
      </div>
      <div className="glass-demo spot" data-rv style={style}>
        <Ambience songIndex={songIndex} motion={motion} />
        <Cover song={song} className="glass-cover" alt="" />
        <div className="glass-menu lg" aria-hidden="true">
          <span><PlayCircle size={17} />下一首播放</span>
          <span><Lightning size={17} />插播</span>
          <span><ListPlus size={17} />添加到歌单</span>
        </div>
        <div className="glass-bar lg" aria-hidden="true"><SkipBack weight="fill" /><Pause weight="fill" /><SkipForward weight="fill" /></div>
        <div className="glass-window lg" role="group" aria-label="外观设置演示">
          <div className="glass-title"><span className="lights" aria-hidden="true"><i /><i /><i /></span><strong>外观设置</strong><small>交互演示</small></div>
          <label className="range-label" htmlFor="glass-strength"><span>玻璃材质强度</span><output>{strength}%</output></label>
          <input id="glass-strength" type="range" min="0" max="100" value={strength} style={{ "--fill": `${strength}%` }} onChange={event => setStrength(Number(event.target.value))} />
          <p>强度越高，模糊越强、着色越实。</p>
          <label className="switch-row">
            <span>动态效果<small>本页背景流动与歌词动画</small></span>
            <input type="checkbox" role="switch" checked={motion} onChange={event => setMotion(event.target.checked)} />
            <i aria-hidden="true" />
          </label>
        </div>
      </div>
    </div>
  </section>;
}

/* ---------- Multi-device room ---------- */
const DEVICES = [
  { id: "small", name: "Wi-Fi 音箱 · 01", icon: "wifi-small.svg" },
  { id: "large", name: "Wi-Fi 音箱 · 02", icon: "wifi-large.svg" },
  { id: "headphones", name: "耳机" },
];
export function Room({ motion }) {
  const [selected, setSelected] = useState(["small", "large"]);
  const [volumes, setVolumes] = useState({ small: 65, large: 45, headphones: 60 });
  const [host, visible] = useInView();
  const lit = id => selected.includes(id) && volumes[id] > 0;
  const toggle = id => setSelected(ids => ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id]);
  return <section ref={host} className={`section room${motion && visible ? " running" : ""}`} id="devices" aria-labelledby="room-title">
    <div className="shell">
      <div className="room-card">
        <div className="room-visual" aria-hidden="true">
          <div className="room-scene">
            <img className="room-image" src={`${ASSET}room-devices-off.webp`} alt="" width="1774" height="887" loading="lazy" decoding="async" />
            {DEVICES.map(({ id }) => <div key={id} className={`device-sound sound-${id}${lit(id) ? " active" : ""}`} style={{ "--gain": volumes[id] / 100 }}>
              <img className="soundwave" src={`${ASSET}speaker-waves.webp`} alt="" loading="lazy" />
              <img className="soundwave second" src={`${ASSET}speaker-waves.webp`} alt="" loading="lazy" />
            </div>)}
            {["small", "large"].map(id => <img key={id} className={`playback-light light-${id}${lit(id) ? " active" : ""}`} src={`${ASSET}speaker-playback-light.webp`} alt="" loading="lazy" style={{ "--gain": volumes[id] / 100 }} />)}
          </div>
        </div>
        <div className="room-copy" data-rv>
          <span className="eyebrow">多设备播放</span>
          <h2 id="room-title">从耳机，<br />到整个房间。</h2>
          <p>本机、有线与蓝牙耳机、HomePod 和其他兼容 AirPlay 的 Wi-Fi 音箱，可以单独响起，也可以一起合唱——每一台各有自己的音量。</p>
          <div className="tags"><span><Headphones size={16} />本机 / 有线 / 蓝牙</span><span><SpeakerHigh size={16} />兼容 AirPlay 的 Wi-Fi 音箱</span></div>
          <p className="fine">单设备播放时，新连上的蓝牙耳机会自动接管。HomePod 投放组件随安装包与便携包提供；同步效果因设备组合与网络环境而异。</p>
        </div>
        <div className="device-panel lg" data-rv>
          <div className="device-head"><strong>一起播放</strong><small>交互演示 · 无音频</small></div>
          {DEVICES.map(device => {
            const on = selected.includes(device.id);
            return <div key={device.id} className={`device-row${on ? " on" : ""}`}>
              <button type="button" aria-pressed={on} aria-label={`${on ? "移出" : "加入"}同步：${device.name}`} onClick={() => toggle(device.id)}>
                {device.icon ? <img src={`${ASSET}${device.icon}`} alt="" width="24" height="24" /> : <Headphones size={24} />}
                <span>{device.name}</span>
                <i aria-hidden="true">{on && <Check size={13} weight="bold" />}</i>
              </button>
              <input type="range" min="0" max="100" value={volumes[device.id]} disabled={!on} aria-label={`${device.name}音量`} style={{ "--fill": `${volumes[device.id]}%` }}
                onChange={event => setVolumes(current => ({ ...current, [device.id]: Number(event.target.value) }))} />
            </div>;
          })}
          <p role="status">{selected.length > 1 ? `${selected.length} 台设备，正在一起播放` : selected.length === 1 ? "1 台设备已选择" : "选择设备，加入同步演示"}</p>
        </div>
      </div>
    </div>
  </section>;
}

/* ---------- Sources: library, system capture, Personal DJ ---------- */
function fuzzy(query, text) {
  const needle = query.trim().toLowerCase().replace(/\s+/g, "");
  if (!needle) return true;
  let at = 0;
  for (const char of text.toLowerCase()) if (char === needle[at]) at += 1;
  return at >= needle.length;
}
function LibraryDemo({ onPlay }) {
  const [query, setQuery] = useState("");
  const [history, setHistory] = useState(["rain", "alks", "airport"]);
  const [note, setNote] = useState("试试漏打几个字母，例如 “alks”");
  const results = useMemo(() => SONGS.filter(song => fuzzy(query, `${song.title}${song.artist}${song.album}`)), [query]);
  const remember = () => { const value = query.trim(); if (value) setHistory(items => [value, ...items.filter(item => item !== value)].slice(0, 4)); };
  return <div className="library" role="group" aria-label="音乐库搜索演示">
    <form className="search" role="search" onSubmit={event => { event.preventDefault(); remember(); }}>
      <MagnifyingGlass size={18} aria-hidden="true" />
      <input type="search" value={query} placeholder="搜索三首演示曲目" aria-label="搜索演示曲目" autoComplete="off" onChange={event => setQuery(event.target.value)} onBlur={remember} />
    </form>
    <div className="history" aria-label="搜索历史">
      <ClockCounterClockwise size={15} aria-hidden="true" />
      {history.map(item => <button key={item} type="button" onClick={() => setQuery(item)}>{item}</button>)}
    </div>
    <ul className="results">
      {results.map(song => <li key={song.tone}>
        <Cover song={song} alt="" />
        <span><strong>{song.title}</strong><small>{song.artist} · {song.album}</small></span>
        <button type="button" aria-label={`插播 ${song.title}`} title="插播" onClick={() => { onPlay(SONGS.indexOf(song)); setNote(`已插播《${song.title}》（演示）`); }}><Lightning size={17} /></button>
        <button type="button" aria-label={`下一首播放 ${song.title}`} title="下一首播放" onClick={() => setNote(`《${song.title}》将在下一首播放（演示）`)}><PlayCircle size={17} /></button>
        <button type="button" aria-label={`把 ${song.title} 加入歌单`} title="加入歌单" onClick={() => setNote(`《${song.title}》已加入歌单（演示）`)}><ListPlus size={17} /></button>
      </li>)}
      {!results.length && <li className="empty">演示曲库里没有匹配的歌</li>}
    </ul>
    <p className="library-note" role="status">{note}</p>
  </div>;
}
const DEMO_SECONDS = 180;
const clock = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
function SyncDemo({ motion }) {
  const [position, setPosition] = useState(42);
  const [source, setSource] = useState("external");
  const [host, visible] = useInView();
  useEffect(() => {
    if (!motion || !visible) return;
    const timer = setInterval(() => setPosition(value => (value + 1) % DEMO_SECONDS), 1000);
    return () => clearInterval(timer);
  }, [motion, visible]);
  const bar = (id, label) => <label className={`sync-row${source === id ? " lead" : ""}`}>
    <span>{label}<small>{clock(position)} / {clock(DEMO_SECONDS)}</small></span>
    <input type="range" min="0" max={DEMO_SECONDS} value={position} aria-label={`${label}进度`} style={{ "--fill": `${position / DEMO_SECONDS * 100}%` }}
      onChange={event => { setSource(id); setPosition(Number(event.target.value)); }} />
  </label>;
  return <div ref={host} className="sync" role="group" aria-label="双向进度跟随演示">
    {bar("external", "外部播放器")}
    <span className="sync-link" aria-hidden="true"><ArrowsLeftRight size={18} /></span>
    {bar("gluspot", "GluSpot")}
    <p role="status">拖动任意一条，另一条随之跟上 · 演示时长</p>
  </div>;
}
function DjDemo({ song, motion, onNext }) {
  const [speaking, setSpeaking] = useState(false);
  const [host, visible] = useInView();
  useEffect(() => {
    if (!speaking) return;
    const timer = setTimeout(() => setSpeaking(false), 6500);
    return () => clearTimeout(timer);
  }, [speaking, song.tone]);
  const state = speaking ? "speaking" : "ready";
  return <div ref={host} className={`dj dj-is-${state}`}>
    <div className="dj-art">
      <Cover song={song} />
      <button type="button" className="dj-rings" aria-pressed={speaking} aria-label={speaking ? "暂停私人 DJ 播报演示" : "开始私人 DJ 播报演示"} onClick={() => setSpeaking(value => !value)}>
        <img src={`${ASSET}dj/${song.tone}-${state}${motion && visible ? "" : "-still"}.webp`} alt="GluSpot 原生私人 DJ 五环动画" width="240" height="240" loading="lazy" />
      </button>
    </div>
    <div className="dj-side">
      <div className="dj-track"><strong>{song.title}</strong><span>{song.artist}</span></div>
      <div className="dj-actions">
        <button type="button" className="btn small" aria-pressed={speaking} onClick={() => setSpeaking(value => !value)}>{speaking ? <Pause size={16} weight="fill" /> : <Play size={16} weight="fill" />}{speaking ? "暂停播报" : "体验播报"}</button>
        <button type="button" className="btn small ghost" onClick={() => { onNext(); setSpeaking(true); }}><SkipForward size={16} weight="fill" />下一首</button>
      </div>
      <p className="dj-status" role="status"><CheckCircle size={15} weight="fill" />{speaking ? "正在播报" : "下一首播报已准备好"}</p>
    </div>
  </div>;
}
export function Sources({ songIndex, setSongIndex, motion }) {
  const song = SONGS[songIndex];
  return <section className="section sources" id="sources" aria-labelledby="sources-title">
    <div className="shell">
      <Head eyebrow="音乐源" id="sources-title" title={<>在岛上听，<br />也听你习惯的播放器。</>}>登录在线音乐，浏览、搜索、收藏与播放；或者什么都不改，让小岛跟随系统里正在响的那首歌。</Head>
      <div className="bento">
        <article className="card spot span-7" data-rv data-tilt>
          <div className="card-text"><span className="kicker"><MusicNotes size={16} />在线音乐库</span><h3>歌单、模糊搜索与搜索历史</h3>
            <p>歌单详情页可以插播、下一首播放、加入歌单；搜索容得下错字，结果有缓存，历史随手再搜。</p></div>
          <LibraryDemo onPlay={setSongIndex} />
        </article>
        <article className="card spot span-5" data-rv data-tilt style={{ "--d": "80ms" }}>
          <div className="card-text"><span className="kicker"><Waveform size={16} />系统媒体</span><h3>外部播放器，也有封面、歌词与进度</h3>
            <p>GluSpot 跟随系统媒体会话。支持精确进度的播放器，还能按歌曲 ID 取到官方歌词与原图封面，两边拖动进度互相跟随。</p></div>
          <SyncDemo motion={motion} />
          <p className="fine">系统抓取依赖外部播放器的媒体支持，体验略逊于程序内播放；精确进度仅部分播放器可用。</p>
        </article>
        <article className="card spot span-12 dj-card" id="dj" data-rv>
          <div className="card-text"><span className="kicker"><img src={`${ASSET}personal-fm.svg`} alt="" width="18" height="18" />私人 DJ</span><h3>歌与歌之间，有人轻声说话。</h3>
            <p>私人漫游里，播报在两首歌之间自然衔接。播报样式可选小脸表情或五环；这里展示的是原生五环动画，环色取自封面。</p>
            <p className="fine">原生动效 · 静音演示，不播放语音。</p></div>
          <DjDemo song={song} motion={motion} onNext={() => setSongIndex((songIndex + 1) % SONGS.length)} />
        </article>
      </div>
    </div>
  </section>;
}

/* ---------- Everything else ---------- */
const MORE = [
  { icon: CornersIn, title: "窄版窗口", text: "更大的平滑圆角，自带进度条；隐藏控件时封面与歌名缩小而不消失。" },
  { icon: Waveform, title: "顺滑形变", text: "岛、窗口与全屏在固定画布内变形，不再逐帧缩放原生窗口。" },
  { icon: Magnet, title: "吸附有分寸", text: "岛体至少一半与吸附位重合才吸附；按住一秒，就停在当前位置。" },
  { icon: EyeSlash, title: "自动隐藏的栏", text: "任务栏或 Finder 栏自动隐藏、且处于全屏时，自动切换为超矮 Top。" },
  { icon: SpeakerHigh, title: "输出状态一目了然", text: "设置 → 播放 → 音频输出可查看 HomePod 投放组件的状态。" },
  { icon: Translate, title: "粤语歌更贴心", text: "可选“显示粤拼”或“跟随次行内容”，缺哪项自动补另一项。" },
  { icon: Package, title: "安装版与便携版", text: "安装版原目录覆盖升级；便携版解压即用，配置留在程序旁。" },
  { icon: Code, title: "MIT 开源", text: "自有代码与文档以 MIT 许可公开，欢迎查看、提问与参与。" },
];
export function More() {
  return <section className="section more" id="more" aria-labelledby="more-title">
    <div className="shell">
      <Head eyebrow="还有这些" id="more-title" title="细节，都为听歌而生。" />
      <ul className="more-grid">
        {MORE.map((item, index) => <li key={item.title} className="spot" data-rv style={{ "--d": `${(index % 4) * 60}ms` }}>
          <span className="point-icon"><item.icon size={20} /></span><h3>{item.title}</h3><p>{item.text}</p>
        </li>)}
      </ul>
    </div>
  </section>;
}

/* ---------- Download ---------- */
export function Download({ release, motion }) {
  const button = useMagnetic();
  const [host, visible] = useInView({ rootMargin: "200px" });
  return <section ref={host} className="section download" id="download" aria-labelledby="download-title">
    <div className="shell">
      <div className="download-card" data-rv>
        <div className="aurora small" aria-hidden="true"><i /><i /></div>
        <img className="installer" src={`${ASSET}${motion && visible ? "installer-loop.gif" : "gluspot.svg"}`} width="700" height="112" alt="GluSpot 安装循环动画" loading="lazy" />
        <span className="eyebrow">This is true music</span>
        <h2 id="download-title">音乐，随心而动。</h2>
        <p>一座岛，一整个音乐世界。</p>
        <a ref={button} className="btn primary large" href={release.url} target="_blank" rel="noreferrer"><WindowsLogo size={22} weight="fill" />下载 GluSpot{release.version ? ` ${release.version}` : ""}<ArrowUpRight size={19} /></a>
        <ul className="release-facts">
          <li>安装版 &amp; 便携版</li><li>Windows 10 1809+ / 11 x64</li><li>开源 · MIT</li>
        </ul>
        <p className="fine">当前为未签名测试版，首次运行可能需要在 SmartScreen 中选择“仍要运行”。下载将前往 GitHub Release 页面。</p>
      </div>
    </div>
  </section>;
}
