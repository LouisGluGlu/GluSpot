#include "timing_estimate.h"
#include "group_main.h"
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

    // Group display and Windows playback use the same total declared lead.
    // At a 48kHz capture rate the 250ms AP2 render lead still uses 44.1kHz.
    tracker = BufferTimingTracker{};
    s = {24000, 22050, 0, 0, 0, 960, 48000, groupPresentationFrames(66150, 11025), true};
    assert(!tracker.sample(s).valid);
    s.capturedFrames += 24000; s.audioFramesSent += 22050;
    estimate = tracker.sample(s);
    assert(estimate.valid && estimate.protocolBufferMs == 1750 && estimate.delayMs() == 1770);
    assert(groupPresentationFrames(88200, 11025) == 99225);
    for (auto invalid : {0U, 1U, 22050U}) {
        bool rejected = false;
        try { (void)groupPresentationFrames(66150, invalid); }
        catch (const std::invalid_argument&) { rejected = true; }
        assert(rejected); // Missing/different policies cannot silently arm a member.
    }
    return 0;
}
