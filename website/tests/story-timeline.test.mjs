import test from 'node:test';
import assert from 'node:assert/strict';
import { advanceStoryClock, advanceStoryScrub, applyStoryDelta, clampProgress, getTimedPhase, isEditableTarget, normalizeWheelDelta, queueStoryScrub, shouldReleaseGesture, storyDuration, storyKeyDelta } from '../src/storyTimeline.js';

test('four island styles hold three seconds and transition for half a second without wrapping', () => {
  const phase = (ms) => getTimedPhase(ms / storyDuration(4), 4);
  assert.equal(storyDuration(4), 13500);
  assert.equal(storyDuration(1), 3000);
  assert.equal(phase(2999).index, 0);
  assert.equal(phase(2999).transitioning, false);
  assert.equal(phase(3000).transitioning, true);
  assert.equal(phase(3250).mix, .5);
  assert.equal(phase(3500).index, 1);
  assert.equal(phase(6500).mix, 0);
  assert.equal(phase(6750).mix, .5);
  assert.equal(phase(7000).index, 2);
  assert.equal(phase(10500).index, 3);
  assert.equal(phase(13250).nextIndex, 3);
  assert.equal(phase(13250).mix, 0);
  assert.equal(phase(13500).index, 3);
  assert.equal(phase(13500).holdProgress, 1);
});

test('the three presentation stages use the same finite hold/transition boundaries', () => {
  assert.equal(storyDuration(3), 10000);
  assert.equal(getTimedPhase(3500 / storyDuration(3), 3).index, 1);
  assert.equal(getTimedPhase(7000 / storyDuration(3), 3).index, 2);
  assert.equal(getTimedPhase(1, 3).nextIndex, 2);
});

test('downward scrub advances, reversal rewinds immediately and both ends clamp', () => {
  const duration = storyDuration(4);
  const next = applyStoryDelta(.5, 100, duration);
  assert.ok(next > .5);
  assert.ok(Math.abs(applyStoryDelta(next, -100, duration) - .5) < 1e-12);
  assert.equal(applyStoryDelta(.98, 10000, duration), 1);
  assert.equal(applyStoryDelta(.02, -10000, duration), 0);
  assert.equal(applyStoryDelta(.5, NaN, duration), .5);
  assert.equal(clampProgress(Infinity), 0);
});

test('nine equal wheel deltas advance exactly the former single-delta distance', () => {
  const duration = storyDuration(4);
  let progress = .2;
  for (let index = 0; index < 9; index += 1) progress = applyStoryDelta(progress, 100, duration);
  assert.ok(Math.abs(progress - applyStoryDelta(.2, 100, duration, 12)) < 1e-12);
  assert.ok(Math.abs((applyStoryDelta(.2, 100, duration) - .2) * 9 - 1200 / duration) < 1e-12);
});

test('wheel targets accumulate with no immediate change to the rendered progress', () => {
  const duration = storyDuration(3);
  const first = queueStoryScrub(.3, .3, 100, duration);
  assert.equal(first.from, .3);
  assert.ok(first.to > .3);
  const firstFrame = advanceStoryScrub(first, 16);
  assert.ok(firstFrame.progress > .3 && firstFrame.progress < first.to);
  const accumulated = queueStoryScrub(firstFrame.progress, first.to, 100, duration);
  assert.equal(accumulated.from, firstFrame.progress);
  assert.ok(Math.abs(accumulated.to - applyStoryDelta(.3, 200, duration)) < 1e-12);
});

test('wheel reversal starts from actual progress and responds backward on the next frame', () => {
  const forward = queueStoryScrub(.5, .5, 500, storyDuration(3));
  const displayed = advanceStoryScrub(forward, 16).progress;
  const reversed = queueStoryScrub(displayed, forward.to, -30, storyDuration(3));
  assert.equal(reversed.from, displayed);
  assert.ok(reversed.to < displayed);
  assert.ok(advanceStoryScrub(reversed, 16).progress < displayed);
});

test('wheel settling is continuous, bounded, and reaches its exact target after 140ms', () => {
  let segment = queueStoryScrub(.5, .5, 100000, storyDuration(3));
  let previous = .5;
  for (const elapsed of [16, 16, 16, 16, 16, 16, 16, 16]) {
    segment = advanceStoryScrub(segment, elapsed);
    assert.ok(segment.progress > previous && segment.progress < 1);
    assert.equal(segment.complete, false);
    previous = segment.progress;
  }
  segment = advanceStoryScrub(segment, 12);
  assert.equal(segment.elapsedMs, 140);
  assert.equal(segment.progress, 1);
  assert.equal(segment.complete, true);
  const cadence = advanceStoryScrub(advanceStoryScrub(queueStoryScrub(.5, .5, 100000, storyDuration(3)), 70), 70);
  assert.equal(cadence.progress, segment.progress);
  assert.equal(advanceStoryScrub(segment, 16).progress, 1);
});

test('pending wheel targets cannot unlock an endpoint before displayed progress settles', () => {
  const pending = advanceStoryScrub(queueStoryScrub(.99, .99, 1000, storyDuration(3)), 16);
  assert.equal(pending.to, 1);
  assert.ok(pending.progress < 1);
  assert.equal(shouldReleaseGesture({ progress: pending.progress, direction: 1, now: 5000, lastInputAt: 0, edgeAt: 0 }), false);
});

test('inertial input cannot escape the ending; a fresh gesture after edge grace can', () => {
  const edge = { progress: 1, direction: 1, edgeAt: 1000 };
  assert.equal(shouldReleaseGesture({ ...edge, now: 1010, lastInputAt: 990 }), false);
  assert.equal(shouldReleaseGesture({ ...edge, now: 1500, lastInputAt: 1480 }), false);
  assert.equal(shouldReleaseGesture({ ...edge, now: 1250, lastInputAt: 900 }), false);
  assert.equal(shouldReleaseGesture({ ...edge, now: 1600, lastInputAt: 1300 }), true);
  assert.equal(shouldReleaseGesture({ ...edge, direction: -1, now: 1600, lastInputAt: 1300 }), false);
  assert.equal(shouldReleaseGesture({ progress: 0, direction: -1, now: 2000, lastInputAt: 1500, edgeAt: 1600 }), true);
});

test('once endpoint exit unlocks the same-direction gesture stays released through a sticky runway', () => {
  const continuingExit = { progress: 1, direction: 1, now: 1620, lastInputAt: 1600, edgeAt: 1000, releasedDirection: 1 };
  assert.equal(shouldReleaseGesture(continuingExit), true);
  assert.equal(shouldReleaseGesture({ ...continuingExit, direction: -1 }), false);
  assert.equal(shouldReleaseGesture({ ...continuingExit, progress: 0, direction: -1, releasedDirection: -1 }), true);
});

test('reduced motion always permits ordinary document scrolling', () => {
  assert.equal(advanceStoryClock(.2, 0, storyDuration(4), { reducedMotion: true }), 1);
  assert.equal(shouldReleaseGesture({ progress: .2, direction: 1, now: 1, lastInputAt: 1, edgeAt: 1, reducedMotion: true }), true);
});

test('autoplay pauses outside the page/section and resumes without accruing hidden time', () => {
  const duration = storyDuration(4);
  assert.equal(advanceStoryClock(.2, 5000, duration, { visible: false }), .2);
  assert.equal(advanceStoryClock(.2, 5000, duration, { playing: false }), .2);
  assert.equal(advanceStoryClock(.2, -10, duration), .2);
  assert.equal(advanceStoryClock(.2, 5000, duration), .2 + 80 / duration);
  assert.equal(advanceStoryClock(.999, 80, duration), 1);
});

test('keyboard preserves form/interactive controls, modifiers, and supports both directions', () => {
  const editable = { closest: () => ({}) };
  const plain = { closest: () => null };
  assert.equal(isEditableTarget(editable), true);
  assert.equal(isEditableTarget(null), false);
  assert.equal(storyKeyDelta({ key: 'ArrowDown', target: editable }), 0);
  assert.equal(storyKeyDelta({ key: 'ArrowDown', target: plain, ctrlKey: true }), 0);
  assert.ok(storyKeyDelta({ key: 'PageDown', target: plain }) > 0);
  assert.equal(storyKeyDelta({ key: 'PageDown', target: plain }, 1000), 900);
  assert.equal(storyKeyDelta({ key: 'PageUp', target: plain }, 1000), -900);
  assert.ok(storyKeyDelta({ key: 'PageUp', target: plain }) < 0);
  assert.ok(storyKeyDelta({ key: ' ', shiftKey: true, target: plain }) < 0);
  assert.equal(storyKeyDelta({ key: 'Tab', target: plain }), 0);
});

test('wheel line and page units become consistent pixels for trackpads and mouse wheels', () => {
  assert.equal(normalizeWheelDelta({ deltaMode: 0, deltaY: 2.5 }), 2.5);
  assert.equal(normalizeWheelDelta({ deltaMode: 1, deltaY: 3 }), 48);
  assert.equal(normalizeWheelDelta({ deltaMode: 2, deltaY: -1 }, 900), -900);
});
