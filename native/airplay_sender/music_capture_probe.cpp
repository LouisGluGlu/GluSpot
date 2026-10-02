// Standalone exact-process signal diagnostic. It opens no microphone/network
// connection and retains no PCM. Session mute requires a separate explicit flag.
#include "music_session_guard.h"
#include "process_loopback.h"
#include "capture_signal.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <limits>
#include <span>
#include <string>

using Microsoft::WRL::ComPtr;
namespace {
HANDLE stopEvent = nullptr;
BOOL WINAPI onConsoleControl(DWORD control) {
    if (control == CTRL_C_EVENT || control == CTRL_BREAK_EVENT || control == CTRL_CLOSE_EVENT) {
        if (stopEvent) SetEvent(stopEvent);
        return TRUE;
    }
    return FALSE;
}
uint64_t nowMs() {
    return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}
std::string utf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), int(value.size()),
                                          nullptr, 0, nullptr, nullptr);
    if (!length) throw std::runtime_error("Invalid Unicode diagnostic argument");
    std::string result(size_t(length), '\0');
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), int(value.size()),
                            result.data(), length, nullptr, nullptr))
        throw std::runtime_error("Cannot convert diagnostic Unicode");
    return result;
}
std::string json(const std::string& value) {
    std::string output = "\"";
    const char* hex = "0123456789abcdef";
    for (const auto byte : value) {
        const auto c = static_cast<unsigned char>(byte);
        if (c == '"' || c == '\\') { output += '\\'; output += char(c); }
        else if (c < 32) { output += "\\u00"; output += hex[c >> 4]; output += hex[c & 15]; }
        else output += char(c);
    }
    return output + '"';
}
uint64_t unsignedArgument(const std::wstring& text) {
    if (text.empty() || !std::all_of(text.begin(), text.end(), [](wchar_t c) { return c >= L'0' && c <= L'9'; }))
        throw std::invalid_argument("Process identity requires decimal unsigned integers");
    size_t consumed = 0;
    const auto value = std::stoull(text, &consumed);
    if (consumed != text.size() || !value) throw std::invalid_argument("Process identity cannot be zero");
    return value;
}
void sessionState(const char* phase, const gluspot::MusicSessionMuteGuard& guard) {
    std::cout << "{\"event\":\"music_sessions\",\"phase\":" << json(phase) << ",\"sessions\":[";
    bool first = true;
    for (const auto& state : guard.snapshot()) {
        if (!first) std::cout << ',';
        first = false;
        std::cout << "{\"endpoint\":" << json(utf8(state.endpoint)) << ",\"instance\":" << json(utf8(state.instance))
                  << ",\"pid\":" << state.pid << ",\"created_at\":" << state.createdAt
                  << ",\"original_muted\":" << (state.originalMuted ? "true" : "false")
                  << ",\"muted\":" << (state.muted ? "true" : "false")
                  << ",\"owned\":" << (state.owned ? "true" : "false")
                  << ",\"external_mute_change\":" << (state.externalMuteChange ? "true" : "false")
                  << ",\"disconnected\":" << (state.disconnected ? "true" : "false")
                  << ",\"original_volume\":" << state.originalVolume << ",\"volume\":" << state.volume << '}';
    }
    std::cout << "]}\n" << std::flush;
}
struct CaptureMetrics {
    uint64_t frames = 0, nonzero = 0, silentFrames = 0, discontinuities = 0;
    uint64_t aboveOneLsb = 0, aboveFourLsb = 0, sumSquares = 0;
    int peak = 0;
};
CaptureMetrics collect(IAudioCaptureClient* capture, uint64_t durationMs,
                       gluspot::MusicSessionMuteGuard& guard, bool requireMuted) {
    CaptureMetrics result;
    const auto start = nowMs();
    uint64_t nextGuardCheck = start;
    while (nowMs() - start < durationMs) {
        if (WaitForSingleObject(stopEvent, 5) != WAIT_TIMEOUT) throw std::runtime_error("Music capture probe cancelled");
        if (nowMs() >= nextGuardCheck) {
            if (!guard.targetAlive()) throw std::runtime_error("Music process ended during probe");
            if (requireMuted && !guard.isStillMuted()) throw std::runtime_error("Music session mute changed during probe");
            nextGuardCheck = nowMs() + 100;
        }
        UINT32 frames = 0;
        gluspot::music_session_detail::checked(capture->GetNextPacketSize(&frames), "Music probe next packet");
        while (frames) {
            BYTE* bytes = nullptr;
            UINT32 count = 0;
            DWORD flags = 0;
            gluspot::music_session_detail::checked(capture->GetBuffer(&bytes, &count, &flags, nullptr, nullptr),
                                                 "Music probe capture buffer");
            // Read metrics only while the driver owns the buffer. Release on
            // every branch, including a malformed or oversized packet.
            try {
                if (count > 88200) throw std::runtime_error("Oversized music capture packet");
                result.frames += count;
                if (flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) ++result.discontinuities;
                if (flags & AUDCLNT_BUFFERFLAGS_SILENT) result.silentFrames += count;
                else {
                    if (!bytes && count) throw std::runtime_error("Music capture returned no PCM buffer");
                    const auto samples = std::span<const int16_t>(reinterpret_cast<const int16_t*>(bytes), size_t(count) * 2);
                    for (const auto sample : samples) {
                        if (sample) ++result.nonzero;
                        const auto magnitude = sample < 0 ? -int(sample) : int(sample);
                        if (magnitude > 1) ++result.aboveOneLsb;
                        if (magnitude > 4) ++result.aboveFourLsb;
                        result.sumSquares += uint64_t(int64_t(sample) * int64_t(sample));
                        result.peak = std::max(result.peak, magnitude);
                    }
                }
            } catch (...) { capture->ReleaseBuffer(count); throw; }
            gluspot::music_session_detail::checked(capture->ReleaseBuffer(count), "Music probe release buffer");
            gluspot::music_session_detail::checked(capture->GetNextPacketSize(&frames), "Music probe next packet");
        }
    }
    return result;
}
void metrics(const char* phase, const CaptureMetrics& value, uint64_t duration) {
    std::cout << "{\"event\":\"music_capture_probe\",\"phase\":" << json(phase)
              << ",\"mode\":\"process_loopback\",\"scope\":\"music_process_tree\",\"duration_ms\":" << duration
              << ",\"frames\":" << value.frames << ",\"nonzero_samples\":" << value.nonzero
              << ",\"samples_above_one_lsb\":" << value.aboveOneLsb
              << ",\"samples_above_four_lsb\":" << value.aboveFourLsb
              << ",\"rms\":" << (value.frames ? std::sqrt(double(value.sumSquares) / (double(value.frames) * 2)) / 32768.0 : 0)
              << ",\"silent_frames\":" << value.silentFrames << ",\"discontinuities\":" << value.discontinuities
              << ",\"peak\":" << value.peak / 32768.0 << "}\n" << std::flush;
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    bool comOwned = false;
    try {
        DWORD pid = 0;
        uint64_t createdAt = 0;
        std::wstring endpoint;
        bool muteProbe = false;
        std::set<std::wstring> seen;
        for (int index = 1; index < argc; ++index) {
            const std::wstring arg = argv[index];
            if (!seen.insert(arg).second) throw std::invalid_argument("Duplicate diagnostic argument");
            if (arg == L"--session-mute-probe") { muteProbe = true; continue; }
            if ((arg != L"--pid" && arg != L"--created-at" && arg != L"--endpoint") || index + 1 >= argc)
                throw std::invalid_argument("Expected --pid, --created-at and --endpoint; optional --session-mute-probe");
            const std::wstring value = argv[++index];
            if (arg == L"--endpoint") endpoint = value;
            else if (arg == L"--created-at") createdAt = unsignedArgument(value);
            else {
                const auto parsed = unsignedArgument(value);
                if (parsed > std::numeric_limits<DWORD>::max()) throw std::invalid_argument("Process ID exceeds DWORD");
                pid = DWORD(parsed);
            }
        }
        if (!pid || !createdAt || endpoint.empty()) throw std::invalid_argument("Explicit process identity and endpoint required");
        gluspot::music_session_detail::checked(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "Music probe COM initialization");
        comOwned = true;
        {
            gluspot::music_session_detail::Handle stopOwner(CreateEventW(nullptr, TRUE, FALSE, nullptr));
            if (!stopOwner.get()) throw std::runtime_error("Cannot create music probe stop event");
            stopEvent = stopOwner.get();
            if (!SetConsoleCtrlHandler(onConsoleControl, TRUE)) throw std::runtime_error("Cannot register music probe cancellation");
            struct ConsoleOwner { ~ConsoleOwner() { SetConsoleCtrlHandler(onConsoleControl, FALSE); stopEvent = nullptr; } } consoleOwner;
            ComPtr<IMMDeviceEnumerator> enumerator;
            gluspot::music_session_detail::checked(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&enumerator)), "Music probe endpoint enumeration");
            ComPtr<IMMDevice> device;
            gluspot::music_session_detail::checked(enumerator->GetDevice(endpoint.c_str(), &device), "Music probe explicit endpoint");
            ComPtr<IMMEndpoint> direction;
            gluspot::music_session_detail::checked(device.As(&direction), "Music probe endpoint direction");
            EDataFlow flow;
            DWORD deviceState = 0;
            gluspot::music_session_detail::checked(direction->GetDataFlow(&flow), "Music probe endpoint direction");
            gluspot::music_session_detail::checked(device->GetState(&deviceState), "Music probe endpoint state");
            if (flow != eRender || deviceState != DEVICE_STATE_ACTIVE)
                throw std::invalid_argument("Music probe endpoint must be an active render output");

            gluspot::MusicSessionMuteGuard guard(pid, createdAt);
            sessionState("prepared", guard);
            std::cout << "{\"event\":\"probe_policy\",\"session_mute_requested\":" << (muteProbe ? "true" : "false")
                      << ",\"endpoint_mute_requested\":false,\"network\":false,\"saved_pcm\":false}\n" << std::flush;
            struct EventOwner { HANDLE handle = nullptr; ~EventOwner() { if (handle) CloseHandle(handle); } } sampleOwner;
            ComPtr<IAudioClient> client;
            gluspot::music_session_detail::checked(gluspot::activateProcessLoopback(stopEvent, client, pid, true),
                                                 "Music process loopback activation");
            WAVEFORMATEX requested{};
            requested.wFormatTag = WAVE_FORMAT_PCM;
            requested.nChannels = 2;
            requested.nSamplesPerSec = 44100;
            requested.wBitsPerSample = 16;
            requested.nBlockAlign = 4;
            requested.nAvgBytesPerSec = 176400;
            gluspot::music_session_detail::checked(client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
                0, 0, &requested, nullptr), "Music process loopback initialization");
            sampleOwner.handle = CreateEventW(nullptr, FALSE, FALSE, nullptr);
            if (!sampleOwner.handle) throw std::runtime_error("Cannot create music sample event");
            gluspot::music_session_detail::checked(client->SetEventHandle(sampleOwner.handle), "Music process sample event");
            ComPtr<IAudioCaptureClient> capture;
            gluspot::music_session_detail::checked(client->GetService(IID_PPV_ARGS(&capture)), "Music capture service");
            gluspot::music_session_detail::checked(client->Start(), "Music capture start");
            struct ClientOwner { IAudioClient* client; ~ClientOwner() { client->Stop(); } } clientOwner{client.Get()};
            // A baseline and post-mute settling interval avoid proving success
            // with queued samples from before the mute bit actually changed.
            collect(capture.Get(), 300, guard, false);
            metrics("baseline", collect(capture.Get(), 1000, guard, false), 1000);
            if (muteProbe) {
                guard.mute();
                sessionState("muted", guard);
                collect(capture.Get(), 300, guard, true);
                metrics("muted", collect(capture.Get(), 4700, guard, true), 4700);
                guard.restore();
                sessionState("restored", guard);
                collect(capture.Get(), 300, guard, false);
                metrics("restored", collect(capture.Get(), 1000, guard, false), 1000);
            }
            gluspot::music_session_detail::checked(client->Stop(), "Music capture stop");
            std::cout << "{\"event\":\"stopped\"}\n" << std::flush;
        }
        CoUninitialize();
        return 0;
    } catch (const std::exception& error) {
        if (comOwned) CoUninitialize();
        std::cout << "{\"event\":\"error\",\"message\":" << json(error.what()) << "}\n" << std::flush;
        return 1;
    }
}
