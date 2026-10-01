#pragma once

#include <cstddef>
#include <cstdint>

namespace gluspot {

// The sender's RTP clock is always 44.1 kHz, even for a 48/96 kHz source.
inline constexpr uint32_t airplaySampleRate = 44100;

struct BufferTimingSample {
    uint64_t capturedFrames = 0, audioFramesSent = 0, silenceFrames = 0;
    uint64_t droppedSamples = 0, discontinuities = 0;
    size_t queuedInputFrames = 0;
    uint32_t inputSampleRate = 0, protocolLatencyFrames = 0;
    bool streaming = false;
};

struct BufferTimingEstimate {
    double protocolBufferMs = 0, inputQueueMs = 0;
    bool valid = false;
    double delayMs() const { return protocolBufferMs + inputQueueMs; }
};

// Estimates queued sender time only. There is no receiver playhead or acoustic
// measurement here. A whole clean, flowing interval is required after startup,
// starvation, capture loss or a discontinuity before publishing a valid value.
class BufferTimingTracker {
public:
    BufferTimingEstimate sample(const BufferTimingSample& current) {
        BufferTimingEstimate result;
        if (current.protocolLatencyFrames)
            result.protocolBufferMs = 1000.0 * current.protocolLatencyFrames / airplaySampleRate;
        if (current.inputSampleRate)
            result.inputQueueMs = 1000.0 * current.queuedInputFrames / current.inputSampleRate;
        result.valid = primed_ && current.streaming && current.inputSampleRate &&
            current.protocolLatencyFrames &&
            current.capturedFrames > previous_.capturedFrames &&
            current.audioFramesSent > previous_.audioFramesSent &&
            current.silenceFrames <= current.audioFramesSent &&
            current.silenceFrames == previous_.silenceFrames &&
            current.droppedSamples == previous_.droppedSamples &&
            current.discontinuities == previous_.discontinuities;
        previous_ = current;
        primed_ = current.streaming;
        return result;
    }

private:
    BufferTimingSample previous_;
    bool primed_ = false;
};

}  // namespace gluspot
