// Offline ownership/clock policy checks. These tests open no real audio stream,
// change no session/endpoint volume, and create no receiver connection.
#include "music_session_guard.h"
#include "local_audio_renderer.h"
#include "group_main.h"
#include <cassert>
#include <iostream>
#include <limits>

namespace {
void sessionOwnership() {
    using gluspot::music_session_detail::SessionEvents;
    const GUID own{0x12345678, 0x1234, 0x5678, {1,2,3,4,5,6,7,8}};
    const GUID external{0x12345679, 0x1234, 0x5678, {1,2,3,4,5,6,7,8}};
    Microsoft::WRL::ComPtr<SessionEvents> events;
    events.Attach(new SessionEvents(own, false));
    assert(!events->externallyChanged() && !events->disconnected());
    assert(events->OnSimpleVolumeChanged(.8f, TRUE, &own) == S_OK);
    assert(!events->externallyChanged());
    // A volume-only user action must survive recovery without leaving an
    // owned mute stuck on. No SetMasterVolume is used in the guard.
    events->OnSimpleVolumeChanged(.5f, TRUE, &external);
    assert(!events->externallyChanged());
    events->OnSimpleVolumeChanged(.5f, FALSE, &external);
    assert(events->externallyChanged());
    events->OnSimpleVolumeChanged(.5f, TRUE, &external);
    events->OnSimpleVolumeChanged(.5f, FALSE, &own);
    assert(events->externallyChanged()); // Never reacquire after a user mute change.
    events->OnStateChanged(AudioSessionStateInactive);
    assert(!events->disconnected());
    events->OnSessionDisconnected(DisconnectReasonDeviceRemoval);
    assert(events->disconnected());
    void* identity = nullptr;
    assert(events->QueryInterface(__uuidof(IAudioSessionEvents), &identity) == S_OK && identity);
    static_cast<IAudioSessionEvents*>(identity)->Release();
    identity = reinterpret_cast<void*>(1);
    assert(events->QueryInterface(external, &identity) == E_NOINTERFACE && identity == nullptr);
    assert(events->QueryInterface(__uuidof(IUnknown), nullptr) == E_POINTER);
    events.Attach(new SessionEvents(own, false));
    events->OnSimpleVolumeChanged(.9f, TRUE, nullptr);
    assert(events->externallyChanged());
    events.Attach(new SessionEvents(own, true));
    events->OnStateChanged(AudioSessionStateExpired);
    assert(events->disconnected());
}

void creationFenceBeforeAudioAccess() {
    const auto self = gluspot::music_session_detail::openProcess(GetCurrentProcessId());
    bool rejected = false;
    try { gluspot::MusicSessionMuteGuard guard(self.pid, self.created + 1); }
    catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
    rejected = false;
    try { gluspot::MusicSessionMuteGuard guard(self.pid, self.created); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected); // A capture/render helper cannot be inside its source tree.
}

void continuousClockCorrection() {
    using namespace gluspot::local_render_detail;
    assert(slewRatio(0) == 1);
    assert(slewRatio(44100) == 1.002);
    assert(slewRatio(-44100) == .998);
    assert(slewRatio(std::numeric_limits<double>::max()) == 1.002);
    assert(slewRatio(-std::numeric_limits<double>::max()) == .998);
    // Simulate a one-minute device drift. The cursor advances continuously;
    // correction converges without dropping a packet or jumping frame index.
    double cursor = 0;
    const double deviceRatio = 1.00015;
    constexpr double framesPerPass = 441;
    for (unsigned pass = 1; pass <= 6000; ++pass) {
        const double expected = (pass - 1) * framesPerPass * deviceRatio;
        const double ratio = slewRatio(expected - cursor);
        assert(ratio >= .998 && ratio <= 1.002);
        const double previous = cursor;
        cursor += framesPerPass * ratio;
        assert(cursor > previous && cursor - previous <= framesPerPass * 1.002 + 1e-8);
    }
    assert(std::abs(cursor - 6000 * framesPerPass * deviceRatio) < 14);
    assert(interpolate(-32768, 32767, 0) == -32768);
    assert(interpolate(-32768, 32767, 1) == 32767);
    assert(interpolate(100, 200, .25) == 125);
    assert(interpolate(200, 100, .75) == 125);
    assert(interpolate(-32768, -32768, .5) == -32768);
    assert(interpolate(32767, 32767, .5) == 32767);
}

void writeClockUnitsAndInvalidObservations() {
    using namespace gluspot::local_render_detail;
    // Raw QPC uses a 3 MHz counter; GetPosition's QPC is already in 100 ns.
    // All four position/frequency units describe the same five-second stream.
    const WriteClockObservation base{S_OK, 5 * 176400, 176400,
        1000000000 - 50000, 100 * 3000000, 3000000};
    gluspot::LocalRenderDiagnostics stats;
    constexpr uint64_t written = 5 * 44100 + 882;
    for (uint64_t frequency : {44100ULL, 48000ULL, 176400ULL, 10000000ULL}) {
        auto observation = base;
        observation.frequency = frequency; observation.position = 5 * frequency;
        const auto offset = estimatedWriteOffset(observation, written, 882, 1000000, stats);
        assert(stats.endpointClockValid && stats.deviceClockFrames == 5 * 44100);
        assert(stats.clockQpcAgeKnown && std::abs(stats.clockQpcAgeMs - 5) < 1e-8);
        assert(std::abs(stats.writeQueueDelayMs - 20) < 1e-8);
        assert(std::abs(offset.count() - 15000000) <= 1);
        assert(std::abs(stats.writePresentationOffsetMs - 15) < 1e-6);
        // A valid device clock incorporates presentation lag once. The large
        // maximum stream latency above must not be added a second time.
        assert(stats.clockPosition == observation.position && stats.clockFrequency == frequency);
        assert(stats.writeEstimateWrittenFrames == written && stats.paddingFrames == 882);
    }
    auto fallback = [&](WriteClockObservation observation, uint64_t count = written) {
        stats.endpointClockValid = true;
        const auto offset = estimatedWriteOffset(observation, count, 882, 300000, stats);
        assert(!stats.endpointClockValid && offset.count() == 50000000);
        assert(std::abs(stats.writeQueueDelayMs - 20) < 1e-8);
        assert(std::abs(stats.writePresentationOffsetMs - 50) < 1e-8);
    };
    auto observation = base;
    observation.position = 0; fallback(observation); // Startup can report zero.
    observation = base; observation.result = S_FALSE; fallback(observation);
    observation.result = E_FAIL; fallback(observation);
    observation = base; observation.qpc100ns = 0; fallback(observation);
    assert(!stats.clockQpcAgeKnown);
    observation = base; observation.frequency = 0; fallback(observation);
    observation = base; observation.qpcFrequency = 0; fallback(observation);
    assert(!stats.clockQpcAgeKnown);
    observation.qpcFrequency = -1; fallback(observation);
    observation = base; observation.qpc100ns = 1000000001; fallback(observation);
    assert(stats.clockQpcAgeKnown && stats.clockQpcAgeMs < 0); // Future correlation.
    observation = base; observation.qpc100ns = 1000000000 - 10000000; fallback(observation);
    assert(stats.clockQpcAgeKnown && stats.clockQpcAgeMs == 1000); // Stale boundary.
    observation = base; observation.position = (written + 882 + 4411) * 4; fallback(observation);
    observation = base; fallback(observation, 6 * 44100); // One-second queue boundary.

    // If the observed clock has passed all written data, the existing queue
    // clamp produces a signed estimate in the past. Keep this edge explicit;
    // this seam does not silently invent a new underrun scheduling policy.
    observation = base;
    const auto drained = estimatedWriteOffset(observation, 5 * 44100 - 441, 0, 0, stats);
    assert(stats.endpointClockValid && stats.writeQueueDelayMs == 0);
    assert(std::abs(drained.count() + 5000000) <= 1);
}

void writeClockDownstreamLagAndHistory() {
    using namespace gluspot::local_render_detail;
    // These independent raw observations correspond to the current silent
    // endpoint probe and a previous flowing-M6 log. Padding is 20 ms in both;
    // downstream queue inferred from their position differs by 200 ms. Neither
    // fixture claims to measure either speaker's actual acoustic presentation.
    const WriteClockObservation silent{S_OK, 1300132, 176400, 999999983, 1000000000, 10000000};
    const WriteClockObservation music{S_OK, 16168888, 176400, 999999983, 1000000000, 10000000};
    gluspot::LocalRenderDiagnostics a, b;
    const auto silentOffset = estimatedWriteOffset(silent, 326781, 882, 0, a);
    const auto musicOffset = estimatedWriteOffset(music, 4052790, 882, 0, b);
    assert(a.endpointClockValid && b.endpointClockValid);
    assert(std::abs(a.writeQueueDelayMs - 39.6371882086) < 1e-6);
    assert(std::abs(b.writeQueueDelayMs - 239.6371882086) < 1e-6);
    assert(musicOffset - silentOffset == std::chrono::milliseconds(200));
    assert(std::abs(a.clockQpcAgeMs - .0017) < 1e-8 && std::abs(b.clockQpcAgeMs - .0017) < 1e-8);

    auto history = std::make_unique<gluspot::GroupPcmHistory>();
    for (uint64_t first = 0; first < 35200; first += 352) {
        std::array<int16_t, 704> pcm;
        for (size_t frame = 0; frame < 352; ++frame)
            pcm[frame * 2] = pcm[frame * 2 + 1] = int16_t((first + frame) % 30000 + 1);
        assert(history->append(first, pcm));
    }
    const auto now = std::chrono::steady_clock::time_point(std::chrono::seconds(2));
    const auto epoch = std::chrono::steady_clock::time_point(std::chrono::milliseconds(1750));
    const auto sourceFrame = [&](std::chrono::nanoseconds offset) {
        return std::chrono::duration<double>(now + offset - epoch).count() * 44100;
    };
    const double earlyModelFrame = sourceFrame(musicOffset), lateModelFrame = sourceFrame(silentOffset);
    assert(std::abs(earlyModelFrame - lateModelFrame - 8820) < 1e-6);
    std::array<int16_t, 4> earlier{}, later{}, repeated{};
    const auto first = uint64_t(std::floor(lateModelFrame)), second = uint64_t(std::floor(earlyModelFrame));
    assert(history->pull(first, earlier) && history->pull(second, later) && history->pull(first, repeated));
    assert(earlier == repeated && earlier[0] == int16_t(first % 30000 + 1));
    assert(later[0] == int16_t(second % 30000 + 1));
    // An overestimated write-to-speaker queue selects a later source frame,
    // which can make playback early. Padding alone is not interchangeable
    // with the position-derived downstream queue.
    assert(later[0] - earlier[0] == 8820);

    auto startup = silent; startup.position = 0;
    const auto startupOffset = estimatedWriteOffset(startup, 326781, 882, 0, a);
    assert(!a.endpointClockValid && startupOffset == std::chrono::milliseconds(20));
    double cursor = sourceFrame(startupOffset);
    const double phase = lateModelFrame - cursor;
    assert(phase > 0 && slewRatio(phase) == 1.002);
    const double before = cursor;
    cursor += 441 * slewRatio(phase);
    assert(cursor > before && cursor < lateModelFrame); // Continuous startup handoff, no index jump.
}

void rendererRequiresExplicitOutput() {
    bool rejected = false;
    try { gluspot::LocalAudioRenderer renderer(L"", [](uint64_t, std::span<int16_t>) { return false; }); }
    catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
    rejected = false;
    try { gluspot::LocalAudioRenderer renderer(L"not-opened", {}); }
    catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
}
} // namespace

int main() {
    sessionOwnership();
    creationFenceBeforeAudioAccess();
    continuousClockCorrection();
    writeClockUnitsAndInvalidObservations();
    writeClockDownstreamLagAndHistory();
    rendererRequiresExplicitOutput();
    std::cout << "Music session identity/ownership and local clock policy passed\n";
}
