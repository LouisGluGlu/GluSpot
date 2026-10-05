#pragma once
#include <chrono>
#include <cstdint>
#include <stdexcept>

namespace gluspot {
// Optional measured compensation delays one Windows endpoint. Shared by CLI
// validation, the renderer and PCM retention; zero preserves the timeline.
inline constexpr uint32_t localMaxExtraDelayFrames = 22050;
inline constexpr uint32_t groupPcmHistoryFrames = 44100 * 3;
// The renderer may trail its target by 250 ms before its terminal phase guard.
// The network publisher can lead by one 352-frame packet; keep two lookahead
// frames as well, instead of allowing a compensated reader on the wrap edge.
inline constexpr uint32_t localHistoryMarginFrames = 11025 + 352 + 2;

inline bool localPresentationFitsHistory(uint32_t baseFrames, uint32_t extraFrames) noexcept {
    return baseFrames && baseFrames <= 99225 && extraFrames <= localMaxExtraDelayFrames &&
        uint64_t(baseFrames) + extraFrames <= groupPcmHistoryFrames - localHistoryMarginFrames;
}
inline uint32_t localPresentationFrames(uint32_t baseFrames, uint32_t extraFrames) {
    if (!localPresentationFitsHistory(baseFrames, extraFrames))
        throw std::invalid_argument("Local presentation delay exceeds shared history budget");
    return baseFrames + extraFrames;
}
inline std::chrono::nanoseconds localPresentationDelay(uint32_t baseFrames, uint32_t extraFrames) {
    return std::chrono::nanoseconds(uint64_t(localPresentationFrames(baseFrames, extraFrames)) *
                                    1000000000ULL / 44100);
}
} // namespace gluspot
