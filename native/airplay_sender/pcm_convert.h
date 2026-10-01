// GluSpot WASAPI input adapter. This file is independent of the AirPlay protocol.
#pragma once

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace gluspot {

struct PcmFormat {
    uint32_t rate = 0;
    uint16_t channels = 0;
    uint16_t bits = 0;
    uint16_t blockAlign = 0;
    bool floating = false;
    uint32_t channelMask = 0;
};

inline void validateFormat(const PcmFormat& f) {
    if (f.rate < 8000 || f.rate > 192000 || f.channels < 1 || f.channels > 8 ||
        f.blockAlign != f.channels * (f.bits / 8) ||
        (f.floating ? (f.bits != 32 && f.bits != 64)
                    : (f.bits != 8 && f.bits != 16 && f.bits != 24 && f.bits != 32)))
        throw std::runtime_error("Unsupported WASAPI mix format");
    if (f.channels > 2 && f.channelMask == 0)
        throw std::runtime_error("Multichannel endpoint has no speaker channel mask");
    if (f.channelMask && std::popcount(f.channelMask) != f.channels)
        throw std::runtime_error("WASAPI speaker channel mask does not match its channel count");
}

inline double pcmSample(const uint8_t* p, const PcmFormat& f) {
    if (f.floating) {
        double v;
        if (f.bits == 32) { float x; std::memcpy(&x, p, 4); v = x; }
        else std::memcpy(&v, p, 8);
        return std::isfinite(v) ? v : 0.0;
    }
    if (f.bits == 8) return (int(p[0]) - 128) / 128.0;
    if (f.bits == 16) { int16_t x; std::memcpy(&x, p, 2); return x / 32768.0; }
    if (f.bits == 24) {
        int32_t x = int32_t(p[0]) | (int32_t(p[1]) << 8) | (int32_t(p[2]) << 16);
        if (x & 0x800000) x |= int32_t(0xff000000);
        return x / 8388608.0;
    }
    int32_t x; std::memcpy(&x, p, 4); return x / 2147483648.0;
}

inline int16_t toS16(double v) {
    const double clipped = std::clamp(v, -1.0, 32767.0 / 32768.0);
    return int16_t(std::lrint(clipped * 32768.0));
}

inline void convertStereo(const uint8_t* data, size_t frames, const PcmFormat& f,
                          bool silent, std::vector<int16_t>& out) {
    out.resize(frames * 2);
    if (silent) { std::fill(out.begin(), out.end(), 0); return; }
    const size_t bytes = f.bits / 8;
    for (size_t n = 0; n < frames; ++n) {
        const auto* p = data + n * f.blockAlign;
        if (f.channels <= 2) {
            out[n * 2] = toS16(pcmSample(p, f));
            out[n * 2 + 1] = f.channels == 1 ? out[n * 2] : toS16(pcmSample(p + bytes, f));
            continue;
        }
        // WAVEFORMATEXTENSIBLE channels are ordered by speaker-mask bits.
        // Fold centre/surround into both or the matching side; omit LFE.
        double left = 0, right = 0, lWeight = 0, rWeight = 0;
        size_t ch = 0;
        for (uint32_t bit = 1; bit && ch < f.channels; bit <<= 1) {
            if (!(f.channelMask & bit)) continue;
            double l = 0, r = 0;
            if (bit == 1) l = 1;                     // front left
            else if (bit == 2) r = 1;                // front right
            else if (bit == 4) l = r = 0.70710678;   // front centre
            else if (bit == 16 || bit == 64 || bit == 512) l = 0.5;
            else if (bit == 32 || bit == 128 || bit == 1024) r = 0.5;
            else if (bit != 8) l = r = 0.5;
            const double v = pcmSample(p + ch++ * bytes, f);
            left += l * v; right += r * v; lWeight += l; rWeight += r;
        }
        out[n * 2] = toS16(left / std::max(1.0, lWeight));
        out[n * 2 + 1] = toS16(right / std::max(1.0, rWeight));
    }
}
}  // namespace gluspot
