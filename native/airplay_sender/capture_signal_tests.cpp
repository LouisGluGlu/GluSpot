#include "capture_signal.h"
#include "../../third_party/airplay2-sender-cpp/src/ring_buffer.h"
#include <array>
#include <cassert>
#include <cmath>
#include <limits>

int main() {
    using gluspot::CaptureSignalCounter;
    using gluspot::hasStreamingSignal;
    CaptureSignalCounter counter;
    const std::array<int16_t, 4> silence{};
    auto value = counter.snapshot(1000);
    assert(value.nonzeroSamples == 0 && !value.lastNonzeroAgeMs && value.peak == 0);
    counter.recordAccepted({}, 1000);
    counter.recordAccepted(silence, 1000);
    value = counter.snapshot(1500, true);
    assert(value.nonzeroSamples == 0 && !value.lastNonzeroAgeMs && value.peak == 0);

    // Session mute can leave one-LSB dither: diagnostic evidence is retained,
    // but it must not acknowledge music playback.
    const std::array<int16_t, 4> quiet{0, 1, -1, 0};
    counter.recordAccepted(quiet, 2000);
    value = counter.snapshot(2123);
    assert(value.nonzeroSamples == 2 && value.lastNonzeroAgeMs == 123);
    assert(value.peak == 1.0 / 32768.0);
    counter.recordAccepted(silence, 2500);
    value = counter.snapshot(3000);
    assert(value.nonzeroSamples == 2 && value.lastNonzeroAgeMs == 1000);
    assert(!hasStreamingSignal(value, 0)); // RTP padded silence never proves sound.
    assert(!hasStreamingSignal(value, 1));
    assert(value.meaningfulSamples == 0 && !value.lastMeaningfulAgeMs);
    assert(!hasStreamingSignal(counter.snapshot(3001), 1)); // Old nonzero cannot certify startup.
    assert(!hasStreamingSignal({0, 0, 0}, 1));
    assert(!hasStreamingSignal({1, std::nullopt, 0}, 1));

    // Promote before negating INT16_MIN; both full-scale polarities stay finite.
    const std::array<int16_t, 4> fullScale{0, 32767, -32768, -16384};
    counter.recordAccepted(fullScale, 4000);
    value = counter.snapshot(4050, true);
    assert(value.nonzeroSamples == 5 && value.lastNonzeroAgeMs == 50);
    assert(value.meaningfulSamples == 3 && value.lastMeaningfulAgeMs == 50);
    assert(!hasStreamingSignal(value, 0)); // Capture before transport is insufficient.
    assert(hasStreamingSignal(value, 1));
    assert(std::isfinite(value.peak) && value.peak == 1.0);
    value = counter.snapshot(4500);
    assert(value.nonzeroSamples == 5 && value.lastNonzeroAgeMs == 500 && value.peak == 0);
    counter.recordAccepted(quiet, 5000);
    value = counter.snapshot(4999);
    assert(value.lastNonzeroAgeMs == 0 && value.peak == 1.0 / 32768.0);
    assert(value.lastMeaningfulAgeMs == 999 && hasStreamingSignal(value, 3));
    assert(!hasStreamingSignal(counter.snapshot(5001), 3)); // Dither cannot refresh meaningful age.
    value = counter.snapshot(std::numeric_limits<uint64_t>::max());
    assert(value.lastNonzeroAgeMs == std::numeric_limits<uint64_t>::max() - 5000);

    // A full sender ring rejects a packet. Its signal must never reach the
    // accepted-packet counter, otherwise dropped sound falsely proves flow.
    fxchain::RingBuffer<int16_t> ring(4);
    CaptureSignalCounter delivered;
    assert(ring.tryPush(quiet));
    delivered.recordAccepted(quiet, 100);
    if (ring.tryPush(fullScale)) delivered.recordAccepted(fullScale, 200);
    value = delivered.snapshot(250);
    assert(value.nonzeroSamples == 2 && value.lastNonzeroAgeMs == 150);
    assert(value.peak == 1.0 / 32768.0);
    assert(value.meaningfulSamples == 0 && !hasStreamingSignal(value, 3));
    std::array<int16_t, 4> consumed{};
    assert(ring.tryPop(consumed) && consumed == quiet);
    assert(ring.tryPush(fullScale));
    delivered.recordAccepted(fullScale, 300);
    value = delivered.snapshot(350);
    assert(value.nonzeroSamples == 5 && value.lastNonzeroAgeMs == 50 && value.peak == 1.0);
    assert(value.meaningfulSamples == 3 && hasStreamingSignal(value, 3));

    // Long sessions accumulate count without narrowing to packet/frame width;
    // peak remains bounded and reads never consume the cumulative evidence.
    CaptureSignalCounter sustained;
    const std::array<int16_t, 4> steady{1, -1, 2, -2};
    for (uint64_t packet = 0; packet < 20000; ++packet)
        sustained.recordAccepted(steady, packet);
    value = sustained.snapshot(21000, true);
    assert(value.nonzeroSamples == 80000 && value.lastNonzeroAgeMs == 1001);
    assert(value.meaningfulSamples == 40000 && value.lastMeaningfulAgeMs == 1001);
    assert(!hasStreamingSignal(value, 40000));
    assert(value.peak == 2.0 / 32768.0);
    value = sustained.snapshot(22000);
    assert(value.nonzeroSamples == 80000 && value.lastNonzeroAgeMs == 2001 && value.peak == 0);
    return 0;
}
