/** Shared timing and input rules for the finite, reversible product chapters. */
export const STORY_HOLD_MS = 3000;
export const STORY_TRANSITION_MS = 500;
export const GESTURE_IDLE_MS = 240;
export const EDGE_GRACE_MS = 320;
export const WHEEL_MS_PER_PIXEL = 12 / 9;
export const SCRUB_SETTLE_MS = 140;

export function clampProgress(value) {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
}

export function storyDuration(count, holdMs = STORY_HOLD_MS, transitionMs = STORY_TRANSITION_MS) {
  const length = Math.max(1, Math.floor(count) || 1);
  return length * Math.max(0, holdMs) + (length - 1) * Math.max(0, transitionMs);
}

export function getTimedPhase(progress, count, holdMs = STORY_HOLD_MS, transitionMs = STORY_TRANSITION_MS) {
  const length = Math.max(1, Math.floor(count) || 1);
  const hold = Math.max(0, holdMs);
  const transition = Math.max(0, transitionMs);
  const step = hold + transition || 1;
  const elapsedMs = clampProgress(progress) * storyDuration(length, hold, transition);
  const index = Math.min(length - 1, Math.floor(elapsedMs / step));
  const localMs = Math.min(step, elapsedMs - index * step);
  const nextIndex = Math.min(length - 1, index + 1);
  const mix = transition ? clampProgress((localMs - hold) / transition) : 0;
  return {
    index,
    nextIndex,
    mix: nextIndex === index ? 0 : mix,
    holdProgress: hold ? clampProgress(localMs / hold) : 1,
    transitioning: nextIndex !== index && localMs >= hold,
    elapsedMs,
  };
}

export function applyStoryDelta(progress, deltaPixels, durationMs, millisecondsPerPixel = WHEEL_MS_PER_PIXEL) {
  if (!Number.isFinite(deltaPixels) || !(durationMs > 0)) return clampProgress(progress);
  return clampProgress(clampProgress(progress) + deltaPixels * millisecondsPerPixel / durationMs);
}

/** Accumulate one target; reverse from the rendered position, without forward backlog. */
export function queueStoryScrub(progress, pendingTarget, deltaPixels, durationMs) {
  const current = clampProgress(progress);
  const target = clampProgress(pendingTarget);
  const base = Math.sign(deltaPixels) === Math.sign(target - current) ? target : current;
  return { from: current, to: applyStoryDelta(base, deltaPixels, durationMs), elapsedMs: 0 };
}

/** Time-based interpolation settles exactly; no independent animation timer is needed. */
export function advanceStoryScrub(segment, elapsedMs, settleMs = SCRUB_SETTLE_MS) {
  const duration = Math.max(1, settleMs);
  const delta = Number.isFinite(elapsedMs) ? Math.max(0, Math.min(80, elapsedMs)) : 0;
  const elapsed = Math.min(duration, segment.elapsedMs + delta);
  const t = elapsed / duration;
  const eased = 1 - (1 - t) ** 3;
  return {
    ...segment,
    elapsedMs: elapsed,
    progress: t === 1 ? segment.to : clampProgress(segment.from + (segment.to - segment.from) * eased),
    complete: t === 1,
  };
}

/** Background tabs never accrue a hidden backlog or jump across a chapter. */
export function advanceStoryClock(progress, elapsedMs, durationMs, { playing = true, visible = true, reducedMotion = false } = {}) {
  if (reducedMotion) return 1;
  if (!playing || !visible || !Number.isFinite(elapsedMs) || !(durationMs > 0)) return clampProgress(progress);
  return clampProgress(clampProgress(progress) + Math.max(0, Math.min(80, elapsedMs)) / durationMs);
}

/** Reaching an edge consumes the current gesture; only a fresh gesture exits. */
export function shouldReleaseGesture({ progress, direction, now, lastInputAt, edgeAt, reducedMotion = false, releasedDirection = 0 }) {
  if (reducedMotion) return true;
  const atEdge = direction > 0 ? progress >= 1 : direction < 0 && progress <= 0;
  return Boolean(atEdge && (direction === releasedDirection || (now - lastInputAt >= GESTURE_IDLE_MS && now - edgeAt >= EDGE_GRACE_MS)));
}

export function normalizeWheelDelta(event, viewportHeight = 800) {
  const pixels = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaMode === 2 ? event.deltaY * viewportHeight : event.deltaY;
  return Number.isFinite(pixels) ? pixels : 0;
}

export function isEditableTarget(target, includeLinksAndButtons = true) {
  const selector = 'input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="slider"],[role="textbox"]';
  return Boolean(target?.closest?.(includeLinksAndButtons ? `${selector},button,a` : selector));
}

export function storyKeyDelta(event, viewportHeight = 800) {
  if (event.altKey || event.ctrlKey || event.metaKey || isEditableTarget(event.target)) return 0;
  if (event.key === 'ArrowDown') return 50;
  if (event.key === 'ArrowUp') return -50;
  const pageStep = Math.max(240, viewportHeight * .9);
  if (event.key === 'PageDown' || (event.key === ' ' && !event.shiftKey)) return pageStep;
  if (event.key === 'PageUp' || (event.key === ' ' && event.shiftKey)) return -pageStep;
  return 0;
}

/** Scroll-pinned scenes: native scrolling drives the timeline; nothing is intercepted. */
export const SCENE_HOLD = 1000;
export const SCENE_TRANSITION = 720;

/** Progress of a tall section whose sticky child is `pinHeight` tall. */
export function pinProgress(top, height, pinHeight) {
  const travel = height - pinHeight;
  if (!(travel > 0) || !Number.isFinite(top)) return 0;
  return clampProgress(-top / travel);
}

/** Progress at the middle of a hold, used to scroll a pinned scene to that step. */
export function phaseCenter(index, count, holdMs = SCENE_HOLD, transitionMs = SCENE_TRANSITION) {
  const length = Math.max(1, Math.floor(count) || 1);
  const step = Math.max(0, Math.min(length - 1, Math.floor(index) || 0));
  const total = storyDuration(length, holdMs, transitionMs);
  if (!(total > 0)) return 0;
  return clampProgress((step * (holdMs + transitionMs) + holdMs / 2) / total);
}

/** Frame-rate independent easing towards a target; snaps when close enough. */
export function approach(current, target, elapsedMs, halfLifeMs = 70, epsilon = 0.0004) {
  if (!Number.isFinite(current)) return target;
  const delta = target - current;
  if (Math.abs(delta) <= epsilon || !(halfLifeMs > 0)) return target;
  const dt = Number.isFinite(elapsedMs) ? Math.max(0, Math.min(80, elapsedMs)) : 16;
  return current + delta * (1 - Math.pow(0.5, dt / halfLifeMs));
}

export function smoothstep(value) {
  const t = clampProgress(value);
  return t * t * (3 - 2 * t);
}
