import { useCallback, useEffect, useRef, useState } from 'react';
import { advanceStoryClock, advanceStoryScrub, applyStoryDelta, clampProgress, isEditableTarget, normalizeWheelDelta, queueStoryScrub, shouldReleaseGesture, storyKeyDelta } from './storyTimeline.js';

/** Finite chapter playback. The section must fit the viewport below pinOffset. */
export function useScrollStory({ durationMs, enabled = true, autoPlay = true, onProgress, pinOffset = 64 }) {
  const ref = useRef(null);
  const value = useRef(0);
  const pendingScrub = useRef(null);
  const notify = useRef(onProgress);
  const seekImpl = useRef(null);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(autoPlay);
  const [active, setActive] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(() => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  notify.current = onProgress;
  const seek = useCallback((next) => seekImpl.current?.(next), []);

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const changed = () => setReducedMotion(query.matches);
    query.addEventListener('change', changed);
    changed();
    return () => query.removeEventListener('change', changed);
  }, []);

  useEffect(() => setPlaying(autoPlay), [autoPlay]);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    let frame = 0, previous = null, lastPublish = -Infinity;
    let inView = false, pinned = false, disposed = false;
    let lastInputAt = -Infinity, edgeAt = -Infinity, lastTouchY = null;
    let releasedDirection = 0;
    let manualUntil = 0;
    const now = () => performance.now();
    const publish = (next, force = false) => {
      const before = value.current;
      value.current = clampProgress(next);
      if ((before < 1 && value.current === 1) || (before > 0 && value.current === 0)) edgeAt = now();
      node.style.setProperty('--story-progress', `${value.current}`);
      notify.current?.(value.current);
      const timestamp = now();
      if (force || value.current === 0 || value.current === 1 || timestamp - lastPublish >= 1000 / 30) {
        lastPublish = timestamp;
        setProgress(value.current);
      }
    };
    const canRun = () => enabled && !reducedMotion && !document.hidden && inView && pinned && !releasedDirection && (pendingScrub.current !== null || (playing && value.current < 1));
    const stop = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      previous = null;
    };
    const tick = (timestamp) => {
      frame = 0;
      if (disposed || !canRun()) { previous = null; return; }
      if (previous !== null) {
        if (pendingScrub.current) {
          const step = advanceStoryScrub(pendingScrub.current, timestamp - previous);
          pendingScrub.current = step.complete ? null : step;
          publish(step.progress, step.complete);
        } else if (playing && timestamp >= manualUntil) {
          publish(advanceStoryClock(value.current, timestamp - previous, durationMs));
        }
      }
      previous = timestamp;
      if (canRun()) frame = requestAnimationFrame(tick);
    };
    const synchronizeClock = () => {
      if (canRun()) { if (!frame) frame = requestAnimationFrame(tick); }
      else stop();
    };
    const measure = () => {
      const box = node.getBoundingClientRect();
      inView = box.bottom > pinOffset && box.top < window.innerHeight;
      pinned = Math.abs(box.top - pinOffset) <= 14 && box.bottom <= window.innerHeight + 16;
      setActive(inView && pinned && enabled);
      synchronizeClock();
    };
    seekImpl.current = (next) => {
      releasedDirection = 0;
      pendingScrub.current = null;
      publish(typeof next === 'function' ? next(value.current) : next, true);
      manualUntil = now() + 200;
      synchronizeClock();
    };
    const consume = (event, delta, allowEntry = false) => {
      if (!enabled || reducedMotion || !delta || document.hidden || isEditableTarget(event.target, event.type === 'keydown')) return;
      const box = node.getBoundingClientRect();
      const entryDistance = box.top - pinOffset;
      // Acquire only at this section's entry, never by intercepting the whole page.
      const crossesEntry = delta > 0
        ? entryDistance > 0 && entryDistance <= delta
        : entryDistance < 0 && entryDistance >= delta;
      const reachesEntry = allowEntry && crossesEntry && box.height <= window.innerHeight - pinOffset + 16;
      const aligned = Math.abs(entryDistance) <= 14 && box.bottom <= window.innerHeight + 16;
      if (!aligned && !reachesEntry) return;
      const timestamp = now();
      const direction = Math.sign(delta);
      if (direction !== releasedDirection) releasedDirection = 0;
      if (shouldReleaseGesture({ progress: value.current, direction, now: timestamp, lastInputAt, edgeAt, releasedDirection })) {
        releasedDirection = direction;
        lastInputAt = timestamp;
        synchronizeClock();
        return;
      }
      if (!event.cancelable) return;
      event.preventDefault();
      if (reachesEntry) window.scrollBy({ top: entryDistance, behavior: 'instant' });
      lastInputAt = timestamp;
      manualUntil = timestamp + 650;
      const storyDelta = reachesEntry ? delta - entryDistance : delta;
      if (event.type === 'wheel') {
        pendingScrub.current = queueStoryScrub(value.current, pendingScrub.current?.to ?? value.current, storyDelta, durationMs);
      } else {
        // Keyboard and direct touch retain their established distance and immediate response.
        pendingScrub.current = null;
        publish(applyStoryDelta(value.current, storyDelta, durationMs, 12));
      }
      measure();
    };
    const wheel = (event) => {
      if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      consume(event, normalizeWheelDelta(event, window.innerHeight), true);
    };
    const keydown = (event) => {
      if (event.defaultPrevented) return;
      if ((event.key === 'Home' || event.key === 'End') && pinned && enabled && !reducedMotion && !isEditableTarget(event.target) && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        seekImpl.current(event.key === 'Home' ? 0 : 1);
        return;
      }
      consume(event, storyKeyDelta(event, window.innerHeight), true);
    };
    const touchStart = (event) => { lastTouchY = event.touches.length === 1 ? event.touches[0].clientY : null; };
    const touchMove = (event) => {
      if (lastTouchY === null || event.touches.length !== 1) return;
      const nextY = event.touches[0].clientY;
      consume(event, lastTouchY - nextY, true);
      lastTouchY = nextY;
    };
    const touchEnd = () => { lastTouchY = null; };
    const observer = new IntersectionObserver(measure, { threshold: [0, .65, .9, 1] });
    observer.observe(node);
    node.addEventListener('wheel', wheel, { passive: false });
    node.addEventListener('touchstart', touchStart, { passive: true });
    node.addEventListener('touchmove', touchMove, { passive: false });
    node.addEventListener('touchend', touchEnd, { passive: true });
    node.addEventListener('touchcancel', touchEnd, { passive: true });
    document.addEventListener('keydown', keydown);
    document.addEventListener('visibilitychange', synchronizeClock);
    window.addEventListener('scroll', measure, { passive: true });
    window.addEventListener('resize', measure);
    if (reducedMotion) {
      pendingScrub.current = null;
      publish(advanceStoryClock(value.current, 0, durationMs, { reducedMotion }), true);
    }
    else publish(value.current, true);
    measure();
    return () => {
      disposed = true;
      stop();
      observer.disconnect();
      seekImpl.current = null;
      node.removeEventListener('wheel', wheel);
      node.removeEventListener('touchstart', touchStart);
      node.removeEventListener('touchmove', touchMove);
      node.removeEventListener('touchend', touchEnd);
      node.removeEventListener('touchcancel', touchEnd);
      document.removeEventListener('keydown', keydown);
      document.removeEventListener('visibilitychange', synchronizeClock);
      window.removeEventListener('scroll', measure);
      window.removeEventListener('resize', measure);
    };
  }, [durationMs, enabled, playing, reducedMotion, pinOffset]);

  return { ref, progress, playing, setPlaying, seek, completed: progress >= 1, reducedMotion, active };
}
