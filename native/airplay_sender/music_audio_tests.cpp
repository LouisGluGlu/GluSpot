// Offline ownership/clock policy checks. These tests open no real audio stream,
// change no session/endpoint volume, and create no receiver connection.
#include "music_session_guard.h"
#include "local_audio_renderer.h"
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
    rendererRequiresExplicitOutput();
    std::cout << "Music session identity/ownership and local clock policy passed\n";
}
