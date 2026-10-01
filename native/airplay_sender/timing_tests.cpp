#include "timing_estimate.h"
#include <cassert>
#include <cmath>

int main() {
    using namespace gluspot;
    BufferTimingTracker tracker;
    BufferTimingSample s{24000, 22050, 352, 0, 0, 960, 48000, 66150, true};
    auto estimate = tracker.sample(s);
    assert(!estimate.valid);  // First observation does not prove continuous flow.
    s.capturedFrames += 24000; s.audioFramesSent += 22050;
    estimate = tracker.sample(s);
    assert(estimate.valid && estimate.protocolBufferMs == 1500.0);
    assert(estimate.inputQueueMs == 20.0 && estimate.delayMs() == 1520.0);
    // Both source rates use the same RTP buffer and their own queue units.
    s.inputSampleRate = 44100; s.queuedInputFrames = 882;
    s.capturedFrames += 22050; s.audioFramesSent += 22050;
    estimate = tracker.sample(s);
    assert(estimate.valid && estimate.delayMs() == 1520.0);

    // Recent starvation/loss invalidates one full sampling interval; previous
    // historical counters do not prevent recovery to a fresh estimate.
    s.capturedFrames += 22050; s.audioFramesSent += 22050; s.silenceFrames += 352;
    assert(!tracker.sample(s).valid);
    s.capturedFrames += 22050; s.audioFramesSent += 22050;
    assert(tracker.sample(s).valid);
    s.capturedFrames += 22050; s.audioFramesSent += 22050; s.droppedSamples += 960;
    assert(!tracker.sample(s).valid);
    s.capturedFrames += 22050; s.audioFramesSent += 22050;
    assert(tracker.sample(s).valid);
    s.capturedFrames += 22050; s.audioFramesSent += 22050; ++s.discontinuities;
    assert(!tracker.sample(s).valid);
    s.capturedFrames += 22050; s.audioFramesSent += 22050;
    assert(tracker.sample(s).valid);
    assert(!tracker.sample(s).valid);  // No new capture/audio, e.g. paused source.

    s.streaming = false;
    assert(!tracker.sample(s).valid);
    s = {24000, 22050, 0, 0, 0, 960, 48000, 66150, true};
    assert(!tracker.sample(s).valid);  // Restart cannot reuse the previous clock.
    s.capturedFrames += 24000; s.audioFramesSent += 22050;
    assert(tracker.sample(s).valid);
    s.inputSampleRate = 0;
    s.capturedFrames += 24000; s.audioFramesSent += 22050;
    estimate = tracker.sample(s);
    assert(!estimate.valid && std::isfinite(estimate.delayMs()));
    s.inputSampleRate = 48000; s.protocolLatencyFrames = 0;
    s.capturedFrames += 24000; s.audioFramesSent += 22050;
    assert(!tracker.sample(s).valid);
    return 0;
}
