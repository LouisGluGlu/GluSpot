#pragma once
#include <array>
#include <cstdint>
#include <span>
#include <stdexcept>

namespace gluspot {
struct PcmPipeHeader { uint64_t epoch, sequence; uint32_t frames; };
inline PcmPipeHeader parsePcmPipeHeader(std::span<const uint8_t> bytes, uint64_t epoch, uint64_t sequence) {
    if (bytes.size() != 24 || bytes[0] != 'G' || bytes[1] != 'P' || bytes[2] != 'C' || bytes[3] != 'M')
        throw std::invalid_argument("Invalid direct PCM header");
    auto little = [&bytes](size_t offset, size_t count) {
        uint64_t value = 0;
        for (size_t index = 0; index < count; ++index) value |= uint64_t(bytes[offset + index]) << (index * 8);
        return value;
    };
    PcmPipeHeader result{little(4, 8), little(12, 8), uint32_t(little(20, 4))};
    if (!epoch || result.epoch != epoch || !sequence || result.sequence != sequence ||
        !result.frames || result.frames > 16384)
        throw std::invalid_argument("Direct PCM epoch, sequence or frame count changed");
    return result;
}
} // namespace gluspot
