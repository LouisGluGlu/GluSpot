#include "pcm_convert.h"
#include <cassert>
#include <limits>

int main() {
    using namespace gluspot;
    std::vector<int16_t> out;
    PcmFormat f{48000, 2, 32, 8, true, 3}; validateFormat(f);
    const float values[] = {0.5f, -0.5f, 2.0f, -2.0f,
                            std::numeric_limits<float>::quiet_NaN(), 0.0f};
    convertStereo(reinterpret_cast<const uint8_t*>(values), 3, f, false, out);
    assert((out == std::vector<int16_t>{16384, -16384, 32767, -32768, 0, 0}));
    convertStereo(nullptr, 3, f, true, out);
    assert((out == std::vector<int16_t>(6, 0)));
    f = {44100, 1, 24, 3, false, 4}; validateFormat(f);
    const uint8_t packed[] = {0, 0, 0x80, 0xff, 0xff, 0x7f};
    convertStereo(packed, 2, f, false, out);
    assert((out == std::vector<int16_t>{-32768, -32768, 32767, 32767}));
    f = {48000, 6, 32, 24, true, 0x3f}; validateFormat(f);
    const float centre[] = {0, 0, 1, 0, 0, 0};
    convertStereo(reinterpret_cast<const uint8_t*>(centre), 1, f, false, out);
    assert(out[0] > 10000 && out[0] == out[1]);
    f.blockAlign = 1;
    bool rejected = false;
    try { validateFormat(f); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    return 0;
}
