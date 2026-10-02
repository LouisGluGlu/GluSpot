// Non-content capture evidence. Only samples accepted by the sender ring count.
#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <optional>
#include <span>

namespace gluspot {
struct CaptureSignalSnapshot {
    uint64_t nonzeroSamples = 0;
    std::optional<uint64_t> lastNonzeroAgeMs;
    double peak = 0;
    uint64_t meaningfulSamples = 0;
    std::optional<uint64_t> lastMeaningfulAgeMs;
};

// A live session-mute probe produced +/-1 PCM16 LSB of dither with the music
// suppressed. Keep those samples in diagnostics, but require fresh larger PCM
// and the same evidence handed to transport before declaring startup complete.
inline bool hasStreamingSignal(const CaptureSignalSnapshot& signal, uint64_t meaningfulSamplesSent) {
    return signal.meaningfulSamples > 0 && signal.lastMeaningfulAgeMs &&
           *signal.lastMeaningfulAgeMs <= 1000 && meaningfulSamplesSent > 0;
}

class CaptureSignalCounter {
public:
    void recordAccepted(std::span<const int16_t> samples, uint64_t nowMs) {
        uint64_t nonzero = 0, meaningful = 0;
        int peak = 0;
        for (const auto sample : samples) {
            if (sample != 0) ++nonzero;
            const int magnitude = sample < 0 ? -int(sample) : int(sample);
            if (magnitude > 1) ++meaningful;
            peak = std::max(peak, magnitude);
        }
        if (!nonzero) return;
        if (meaningful) {
            lastMeaningfulMs_.store(nowMs, std::memory_order_relaxed);
            meaningfulSamples_.fetch_add(meaningful, std::memory_order_release);
        }
        lastNonzeroMs_.store(nowMs, std::memory_order_relaxed);
        nonzeroSamples_.fetch_add(nonzero, std::memory_order_release);
        int previous = peak_.load(std::memory_order_relaxed);
        while (previous < peak && !peak_.compare_exchange_weak(previous, peak, std::memory_order_relaxed)) {}
    }

    CaptureSignalSnapshot snapshot(uint64_t nowMs, bool resetPeak = false) {
        CaptureSignalSnapshot value;
        value.nonzeroSamples = nonzeroSamples_.load(std::memory_order_acquire);
        if (value.nonzeroSamples) {
            const auto last = lastNonzeroMs_.load(std::memory_order_relaxed);
            value.lastNonzeroAgeMs = nowMs >= last ? nowMs - last : 0;
        }
        value.meaningfulSamples = meaningfulSamples_.load(std::memory_order_acquire);
        if (value.meaningfulSamples) {
            const auto last = lastMeaningfulMs_.load(std::memory_order_relaxed);
            value.lastMeaningfulAgeMs = nowMs >= last ? nowMs - last : 0;
        }
        value.peak = (resetPeak ? peak_.exchange(0, std::memory_order_relaxed)
                               : peak_.load(std::memory_order_relaxed)) / 32768.0;
        return value;
    }

private:
    std::atomic<uint64_t> nonzeroSamples_{0}, lastNonzeroMs_{0};
    std::atomic<uint64_t> meaningfulSamples_{0}, lastMeaningfulMs_{0};
    std::atomic<int> peak_{0};
};
} // namespace gluspot
