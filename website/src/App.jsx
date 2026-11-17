import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUpRight, GithubLogo, WindowsLogo } from "@phosphor-icons/react";
import { ASSET } from "./data.js";
import { IslandScene } from "./IslandScene.jsx";
import { PlayerScene } from "./PlayerScene.jsx";
import { Download, Glass, Hero, Lyrics, Marquee, More, Room, SeaLevel, Sources } from "./Sections.jsx";
import { onFrame, useReducedMotion, useReveal, useSpotlight } from "./lib/motion.js";
import { REPO, RELEASE_FALLBACK, loadLatestRelease } from "./releases.js";

const NAV = [
  ["island", "音乐岛"], ["player", "播放"], ["lyrics", "歌词"], ["sea", "海平面"],
  ["devices", "多设备"], ["sources", "音乐源"], ["download", "下载"],
];
const INTRO_HOLD_MS = 2800;

/** Opening mark: holds briefly (or until the first gesture), then flies into the header. */
function IntroLogo({ onComplete }) {
  const logo = useRef(null), host = useRef(null);
  useLayoutEffect(() => {
    const node = logo.current;
    const target = document.querySelector(".site-header .brand img");
    if (!node || !target) { onComplete(); return; }
    let started = false, touchY = null, animation = null;
    const previousOverflow = document.documentElement.style.overflow;
    window.scrollTo({ top: 0, behavior: "instant" });
    document.documentElement.style.overflow = "hidden";
    host.current?.focus({ preventScroll: true });
    const begin = () => {
      if (started) return;
      started = true;
      const from = node.getBoundingClientRect(), to = target.getBoundingClientRect();
      host.current?.classList.add("leaving");
      animation = node.animate([
        { transform: "translate(0,0) scale(1)" },
        { transform: `translate(${to.left - from.left}px,${to.top - from.top}px) scale(${to.width / from.width})` },
      ], { duration: 900, easing: "cubic-bezier(.65,0,.22,1)", fill: "forwards" });
      animation.onfinish = onComplete;
    };
    const timer = window.setTimeout(begin, INTRO_HOLD_MS);
    const wheel = event => { if (event.deltaY > 0) begin(); };
    const touchStart = event => { touchY = event.touches[0]?.clientY ?? null; };
    const touchMove = event => { if (touchY !== null && event.touches[0]?.clientY < touchY - 4) begin(); };
    const key = event => { if (["ArrowDown", "PageDown", " ", "Enter", "Escape", "Tab"].includes(event.key)) { event.preventDefault(); begin(); } };
    document.addEventListener("wheel", wheel, { passive: true });
    document.addEventListener("touchstart", touchStart, { passive: true });
    document.addEventListener("touchmove", touchMove, { passive: true });
    document.addEventListener("keydown", key);
    document.addEventListener("pointerdown", begin);
    return () => {
      clearTimeout(timer);
      animation?.cancel();
      document.documentElement.style.overflow = previousOverflow;
      document.removeEventListener("wheel", wheel);
      document.removeEventListener("touchstart", touchStart);
      document.removeEventListener("touchmove", touchMove);
      document.removeEventListener("keydown", key);
      document.removeEventListener("pointerdown", begin);
    };
  }, [onComplete]);
  return <div ref={host} className="intro" role="dialog" aria-modal="true" tabIndex="-1" aria-label="GluSpot 欢迎画面，稍后自动进入，也可按回车继续">
    <div className="intro-veil" />
    <img ref={logo} src={`${ASSET}gluspot.svg`} alt="GluSpot" />
    <div className="intro-word"><strong>GluSpot</strong><span>让音乐，融入桌面</span></div>
  </div>;
}

export function App() {
  const [release, setRelease] = useState(RELEASE_FALLBACK);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    loadLatestRelease({ signal: controller.signal }).then(latest => {
      clearTimeout(timer);
      if (!controller.signal.aborted) setRelease(latest);
    });
    return () => { clearTimeout(timer); controller.abort(); };
  }, []);

  const reduced = useReducedMotion();
  const [introDone, setIntroDone] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches || !["", "#top"].includes(window.location.hash));
  const finishIntro = useCallback(() => setIntroDone(true), []);
  const [songIndex, setSongIndex] = useState(0);
  const [motionPref, setMotion] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const [current, setCurrent] = useState("");
  const [scrolled, setScrolled] = useState(false);
  const meter = useRef(null);
  const motion = motionPref && pageVisible;

  useReveal();
  useSpotlight();
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash || hash === "#top") return;
    const frame = requestAnimationFrame(() => document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "instant", block: "start" }));
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => {
    const visibility = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", visibility);
    return () => document.removeEventListener("visibilitychange", visibility);
  }, []);
  // Reading progress hairline and header state.
  useEffect(() => onFrame(() => {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    if (meter.current) meter.current.style.transform = `scaleX(${max > 0 ? Math.min(1, window.scrollY / max).toFixed(4) : 0})`;
    setScrolled(window.scrollY > 24);
    return false;
  }), []);
  // Scroll spy for the navigation.
  useEffect(() => {
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) setCurrent(entry.target.id);
    }), { rootMargin: "-45% 0px -50% 0px" });
    NAV.forEach(([id]) => { const node = document.getElementById(id); if (node) observer.observe(node); });
    return () => observer.disconnect();
  }, []);

  const tone = ["chace", "alicks", "noah"][songIndex];
  return <div id="top" className={`site tone-${tone} ${motionPref ? "motion-on" : "motion-off"} ${introDone ? "intro-done" : "intro-playing"}`}>
    {!introDone && <IntroLogo onComplete={finishIntro} />}
    <a className="skip-link" href="#island" inert={!introDone}>跳到产品演示</a>
    <header className={`site-header${scrolled ? " scrolled" : ""}`} inert={!introDone}>
      <a className="brand" href="#top" aria-label="GluSpot 首页"><img src={`${ASSET}gluspot.svg`} alt="" width="34" height="34" /><span>GluSpot</span></a>
      <nav aria-label="主导航">
        {NAV.map(([id, label]) => <a key={id} href={`#${id}`} aria-current={current === id ? "true" : undefined}>{label}</a>)}
      </nav>
      <div className="header-actions">
        <a className="github" href={REPO} target="_blank" rel="noreferrer" aria-label="GitHub 开源项目"><GithubLogo size={20} weight="fill" /><span>GitHub</span></a>
        <a className="header-download" href={release.url} target="_blank" rel="noreferrer"><WindowsLogo size={15} weight="fill" />下载</a>
      </div>
      <span ref={meter} className="read-meter" aria-hidden="true" />
    </header>
    <main inert={!introDone}>
      <Hero release={release} ready={introDone} />
      <IslandScene opening={!introDone} reduced={reduced} />
      <PlayerScene songIndex={songIndex} setSongIndex={setSongIndex} motion={motion} reduced={reduced} />
      <Marquee />
      <Lyrics motion={motion} />
      <SeaLevel motion={motion} />
      <Glass songIndex={songIndex} motion={motion} setMotion={setMotion} />
      <Room motion={motion} />
      <Sources songIndex={songIndex} setSongIndex={setSongIndex} motion={motion} />
      <More />
      <Download release={release} motion={motion} />
    </main>
    <footer className="site-footer" inert={!introDone}>
      <div className="shell">
        <div className="footer-main">
          <a className="brand" href="#top"><img src={`${ASSET}gluspot.svg`} width="30" height="30" alt="" /><span>GluSpot</span></a>
          <span>让音乐融入桌面。</span>
          <a href={REPO} target="_blank" rel="noreferrer">GitHub <ArrowUpRight size={13} /></a>
          <a href={`${REPO}/releases`} target="_blank" rel="noreferrer">发行记录 <ArrowUpRight size={13} /></a>
          <a href={`${REPO}/blob/main/LICENSE`} target="_blank" rel="noreferrer">MIT 开源</a>
        </div>
        <div className="credits">
          <p>演示音乐：
            <a href="https://chace.lnk.to/BelatedSuffocation" target="_blank" rel="noreferrer">Chace · The Rain Song</a> /{" "}
            <a href="https://alicks14.bandcamp.com/track/ibuprofen" target="_blank" rel="noreferrer">Alicks · Ibuprofen</a> /{" "}
            <a href="https://soundcloud.com/goodboynoah/airport-bar-prod-by-evil-needle" target="_blank" rel="noreferrer">goodboy noah · Airport Bar</a>
            。专辑封面版权归各自权利人所有。网页演示不播放音频。</p>
          <p>岛态、歌词、高光与私人 DJ 画面由 GluSpot 的生产绘制代码导出；模拟桌面与空间场景为生成示意图，海平面与玻璃演示为网页示意，均不代表特定设备或兼容性承诺。</p>
        </div>
      </div>
    </footer>
  </div>;
}
