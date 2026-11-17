import { useEffect, useRef, useState } from "react";
import { approach, pinProgress } from "../storyTimeline.js";

/** One shared animation frame for every scroll-linked effect. */
const listeners = new Set();
let frame = 0, last = 0;
function run(now) {
  frame = 0;
  const elapsed = last ? now - last : 16;
  last = now;
  let again = false;
  listeners.forEach(listener => { if (listener(elapsed) === true) again = true; });
  if (again) requestTick(); else last = 0;
}
export function requestTick() {
  if (!frame && typeof window !== "undefined") frame = requestAnimationFrame(run);
}
if (typeof window !== "undefined") {
  window.addEventListener("scroll", requestTick, { passive: true });
  window.addEventListener("resize", requestTick);
}
/** Subscribe to frames. Return true from the listener to ask for another frame. */
export function onFrame(listener) {
  listeners.add(listener);
  requestTick();
  return () => listeners.delete(listener);
}

export function useReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => setReduced(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  return reduced;
}

export function useInView(options) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), options);
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, visible];
}

/**
 * Native-scroll pinned scene. `section` is the tall runway, `sticky` its pinned child.
 * `render(progress)` receives eased progress whenever it changes. With `enabled`
 * false (reduced motion) the scene is static and `seek` paints directly.
 */
export function usePinnedScene({ render, enabled = true }) {
  const section = useRef(null), sticky = useRef(null);
  const shown = useRef(0), paint = useRef(render);
  paint.current = render;
  useEffect(() => {
    if (!enabled) { paint.current(shown.current); return; }
    let first = true;
    return onFrame(elapsed => {
      const node = section.current, pin = sticky.current;
      if (!node || !pin) return false;
      const box = node.getBoundingClientRect();
      const target = pinProgress(box.top, box.height, pin.offsetHeight);
      const next = first ? target : approach(shown.current, target, elapsed);
      const moved = first || next !== shown.current;
      first = false;
      shown.current = next;
      if (moved) paint.current(next);
      return next !== target;
    });
  }, [enabled]);
  const seek = progress => {
    const node = section.current, pin = sticky.current;
    if (!enabled || !node || !pin) { shown.current = progress; paint.current(progress); requestTick(); return; }
    const top = node.getBoundingClientRect().top + window.scrollY;
    window.scrollTo({ top: top + (node.offsetHeight - pin.offsetHeight) * progress, behavior: "smooth" });
  };
  const repaint = () => { paint.current(shown.current); requestTick(); };
  return { section, sticky, seek, repaint, progress: shown };
}

/** Fade/rise elements marked with data-rv once they enter the viewport. */
export function useReveal() {
  useEffect(() => {
    const nodes = document.querySelectorAll("[data-rv]");
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.dataset.rv = "in";
      observer.unobserve(entry.target);
    }), { threshold: 0.1, rootMargin: "0px 0px -5% 0px" });
    nodes.forEach(node => observer.observe(node));
    return () => observer.disconnect();
  }, []);
}

const finePointer = () => window.matchMedia("(hover: hover) and (pointer: fine)").matches;
const stillMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Buttons lean a few pixels towards a fine pointer. */
export function useMagnetic(strength = 0.25) {
  const ref = useRef(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || !finePointer() || stillMotion()) return;
    const move = event => {
      const box = node.getBoundingClientRect();
      node.style.translate = `${(event.clientX - box.left - box.width / 2) * strength}px ${(event.clientY - box.top - box.height / 2) * strength}px`;
    };
    const leave = () => { node.style.translate = ""; };
    node.addEventListener("pointermove", move);
    node.addEventListener("pointerleave", leave);
    return () => { node.removeEventListener("pointermove", move); node.removeEventListener("pointerleave", leave); };
  }, [strength]);
  return ref;
}

/** Cards get a pointer-tracked spotlight (CSS vars --mx/--my) and an optional slight tilt. */
export function useSpotlight(selector = ".spot") {
  useEffect(() => {
    if (!finePointer()) return;
    const still = stillMotion();
    let pending = null, queued = 0;
    const apply = () => {
      queued = 0;
      if (!pending) return;
      const { card, x, y } = pending;
      const box = card.getBoundingClientRect();
      const px = (x - box.left) / box.width, py = (y - box.top) / box.height;
      card.style.setProperty("--mx", `${px * 100}%`);
      card.style.setProperty("--my", `${py * 100}%`);
      if (!still && card.dataset.tilt !== undefined) {
        card.style.setProperty("--rx", `${(0.5 - py) * 4}deg`);
        card.style.setProperty("--ry", `${(px - 0.5) * 5}deg`);
      }
    };
    const move = event => {
      const card = event.target.closest?.(selector);
      if (!card) return;
      pending = { card, x: event.clientX, y: event.clientY };
      if (!queued) queued = requestAnimationFrame(apply);
    };
    const out = event => {
      const card = event.target.closest?.(selector);
      if (!card || card.contains(event.relatedTarget)) return;
      pending = null;
      card.style.setProperty("--rx", "0deg");
      card.style.setProperty("--ry", "0deg");
    };
    document.addEventListener("pointermove", move, { passive: true });
    document.addEventListener("pointerout", out, { passive: true });
    return () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerout", out); cancelAnimationFrame(queued); };
  }, [selector]);
}

/** Play a muted video only while it is visible and motion is allowed. */
export function useAutoVideo(active = true, key = "") {
  const [ref, visible] = useInView({ rootMargin: "100px" });
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(false), [key]);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (active && visible) node.play().catch(() => {});
    else node.pause();
  }, [active, visible, key, ready]);
  return { ref, visible, ready, setReady };
}
