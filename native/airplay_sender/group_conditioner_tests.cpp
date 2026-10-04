// Audio quality checks use independently evaluated continuous waveforms, not
// another copy of the filter. All runs are offline and open no audio endpoint.
#include "group_main.h"
#include <cassert>
#include <chrono>
#include <cstdio>
#include <limits>

namespace {
constexpr double pi = 3.1415926535897932384626433832795;
struct Waveform {
    double frequency;
    bool chirp = false;
    double at(double frame, size_t channel) const {
        const double time = frame / 44100;
        const double phase = chirp ? 2 * pi * (500 * time + .5 * (17500.0 / 8) * time * time)
                                   : 2 * pi * frequency * time;
        return 12000 * std::sin(phase + (channel ? .713 : 0));
    }
    int16_t pcm(size_t frame, size_t channel) const {
        return int16_t(std::lround(at(double(frame), channel)));
    }
};

void quality(const Waveform& wave, int ppm) {
    gluspot::GroupPcmConditioner conditioner;
    std::array<int16_t, 704> output{};
    size_t fed = 0, measured = 0;
    double position = 0, maximum = 0, boundaryError = 0;
    std::array<double, 2> previousActual{}, previousIdeal{};
    long double square = 0, linearSquare = 0;
    const auto began = std::chrono::steady_clock::now();
    // Real capture arrives in 10 ms (441 frame) batches, while the shared
    // output consumes 352 frames. Positive and negative ppm exercise fractional
    // phase wraps and changing correction across thousands of packet borders.
    constexpr size_t packets = 1000;
    for (size_t packet = 0; packet < packets; ++packet) {
        const uint64_t elapsed = uint64_t(packet) * 352;
        const uint64_t captured = elapsed * uint64_t(1000000 + ppm) / 1000000;
        const size_t available = gluspot::GroupPcmConditioner::targetQueueFrames + size_t(captured / 441) * 441;
        std::vector<int16_t> input((available - fed) * 2);
        for (size_t index = fed; index < available; ++index)
            for (size_t channel = 0; channel < 2; ++channel)
                input[(index - fed) * 2 + channel] = wave.pcm(index, channel);
        assert(conditioner.feed(input)); fed = available;
        conditioner.packet(output);
        const double ratio = conditioner.ratio();
        assert(ratio >= .998 && ratio <= 1.002);
        assert(conditioner.queuedFrames() <= 44100 && conditioner.paddedFrames() == 0);
        for (size_t frame = 0; frame < 352; ++frame) {
            for (size_t channel = 0; channel < 2; ++channel) {
                const double ideal = wave.at(position, channel);
                const double actual = output[frame * 2 + channel];
                if (position >= 64) { // The finite source has no preceding waveform before frame zero.
                    const double error = actual - ideal;
                    square += error * error; maximum = std::max(maximum, std::abs(error));
                    ++measured;
                    const auto left = size_t(position);
                    const double fraction = position - left;
                    // Historical two-point interpolation evaluated directly at
                    // the same independent waveform positions, as a quality
                    // baseline; the acceptance bound below is absolute.
                    const double linear = std::round(wave.pcm(left, channel) +
                        (double(wave.pcm(left + 1, channel)) - wave.pcm(left, channel)) * fraction);
                    linearSquare += (linear - ideal) * (linear - ideal);
                    if (!frame && packet)
                        boundaryError = std::max(boundaryError, std::abs((actual - previousActual[channel]) -
                                                                      (ideal - previousIdeal[channel])));
                }
                previousActual[channel] = actual; previousIdeal[channel] = ideal;
            }
            position += ratio;
        }
    }
    const double rms = std::sqrt(double(square / measured));
    const double linearRms = std::sqrt(double(linearSquare / measured));
    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - began).count();
    std::printf("Conditioner %s %.0f Hz %+d ppm: linear RMS %.3f -> actual %.3f LSB; max %.3f, boundary %.3f; %.3f s for %.3f audio s\n",
        wave.chirp ? "chirp" : "tone", wave.frequency, ppm, linearRms, rms, maximum, boundaryError,
        seconds, double(packets * 352) / 44100);
    std::fflush(stdout);
    // These bounds are waveform error/SNR, not agreement with filter taps.
    // A phase reset or skipped stereo frame would fail the boundary limit.
    assert(rms < 8 && maximum < 20 && boundaryError < 40);
}

void dcAndExtremes() {
    for (const int16_t level : {int16_t(0), int16_t(1), int16_t(-1), int16_t(32767), int16_t(-32768)}) {
        gluspot::GroupPcmConditioner conditioner;
        std::vector<int16_t> lead(gluspot::GroupPcmConditioner::targetQueueFrames * 2, level);
        assert(conditioner.feed(lead));
        std::array<int16_t, 704> output{};
        for (size_t packet = 0; packet < 100; ++packet) {
            assert(conditioner.feed(std::vector<int16_t>(706, level)));
            conditioner.packet(output);
            assert(conditioner.paddedFrames() == 0);
            assert(std::all_of(output.begin(), output.end(), [level](int16_t value) { return value == level; }));
        }
    }
    // The band-limited transition legitimately overshoots a full-scale step.
    // Saturation must retain the plateau's sign instead of wrapping int16.
    gluspot::GroupPcmConditioner edge;
    constexpr size_t change = 7001;
    std::vector<int16_t> step(20000);
    for (size_t frame = 0; frame < step.size() / 2; ++frame) {
        step[frame * 2] = frame < change ? 32767 : -32768;
        step[frame * 2 + 1] = frame < change ? -32768 : 32767;
    }
    assert(edge.feed(step));
    std::array<int16_t, 704> output{};
    double position = 0;
    bool clampedPositive = false, clampedNegative = false;
    for (size_t packet = 0; packet < 24; ++packet) {
        edge.packet(output);
        assert(edge.paddedFrames() == 0);
        for (size_t frame = 0; frame < 352; ++frame) {
            if (position > change - 15 && position < change - 1) {
                assert(output[frame * 2] > 0 && output[frame * 2 + 1] < 0);
                clampedPositive = clampedPositive || output[frame * 2] == 32767;
                clampedNegative = clampedNegative || output[frame * 2 + 1] == -32768;
            }
            position += edge.ratio();
        }
    }
    assert(clampedPositive && clampedNegative);
}

void boundedFeedsAndStarvation() {
    gluspot::GroupPcmConditioner conditioner;
    std::array<int16_t, 704> output{};
    conditioner.packet(output);
    assert(conditioner.paddedFrames() == 352);
    assert(std::all_of(output.begin(), output.end(), [](int16_t v) { return v == 0; }));
    assert(!conditioner.feed(std::vector<int16_t>(3)));
    assert(!conditioner.feed(std::vector<int16_t>(88202)));
    assert(conditioner.feed(std::vector<int16_t>(88200, 700)));
    assert(!conditioner.feed(std::vector<int16_t>(2)));
    assert(conditioner.queuedFrames() == 44100);
    conditioner.packet(output);
    assert(std::all_of(output.begin(), output.end(), [](int16_t v) { return v == 700; }));
    bool rejected = false;
    try { conditioner.packet(std::span<int16_t>(output.data(), 703)); }
    catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
}
} // namespace

int main() {
    for (int ppm : {-2000, 2000}) {
        for (double frequency : {1000.0, 8000.0, 18000.0}) quality({frequency, false}, ppm);
        quality({0, true}, ppm);
    }
    dcAndExtremes(); boundedFeedsAndStarvation();
    std::puts("Shared conditioner waveform quality, DC, saturation and bounds passed");
}
