// Explicit local group member. It renders the coordinator's shared PCM history,
// never reads a capture ring and never changes endpoint/session volume or mute.
// IAudioClock schedules the endpoint timeline; Bluetooth acoustic delay remains
// unknown because Windows stream latency is not a microphone measurement.
#pragma once
#include <windows.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <avrt.h>
#include <wrl/client.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <functional>
#include <limits>
#include <mutex>
#include <span>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace gluspot {
struct LocalRenderDiagnostics {
    bool ready = false, armed = false, playing = false;
    bool endpointClockValid = false, acousticDelayKnown = false;
    uint64_t writtenFrames = 0, nonzeroSamples = 0, meaningfulSamples = 0, underrunFrames = 0;
    uint64_t sourceFrame = 0, deviceClockFrames = 0;
    uint32_t presentationLatencyFrames = 0;
    uint64_t clockPosition = 0, clockFrequency = 0, clockQpc100ns = 0;
    uint64_t paddingFrames = 0, writeEstimateWrittenFrames = 0;
    bool clockQpcAgeKnown = false;
    double clockQpcAgeMs = 0, writeQueueDelayMs = 0, writePresentationOffsetMs = 0;
    double writePresentationEpochOffsetMs = 0;
    double streamLatencyMs = 0, bufferDurationMs = 0;
    double phaseErrorMs = 0, resampleRatio = 1;
};

namespace local_render_detail {
inline void checked(HRESULT hr, const char* action) {
    if (FAILED(hr)) {
        std::ostringstream message;
        message << action << " failed (HRESULT 0x" << std::hex << uint32_t(hr) << ')';
        throw std::runtime_error(message.str());
    }
}
inline double slewRatio(double phaseErrorFrames) {
    // Correct clock drift continuously; no frame-index jump in a live group.
    return 1.0 + std::clamp(phaseErrorFrames / (44100.0 * 2), -.002, .002);
}
inline int16_t interpolate(int16_t a, int16_t b, double fraction) {
    return int16_t(std::clamp(std::lround(double(a) + (double(b) - double(a)) * fraction), -32768L, 32767L));
}
} // namespace local_render_detail

class LocalAudioRenderer {
public:
    // Pull fills an arbitrary contiguous range of 44.1 kHz interleaved stereo
    // PCM, returning false if the coordinator's bounded history lacks it.
    using Pull = std::function<bool(uint64_t, std::span<int16_t>)>;
    using Clock = std::chrono::steady_clock;
    LocalAudioRenderer(std::wstring endpoint, Pull pull) : endpoint_(std::move(endpoint)), pull_(std::move(pull)) {
        if (endpoint_.empty() || !pull_) throw std::invalid_argument("Explicit render endpoint and PCM history required");
        stop_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        begin_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!stop_ || !begin_) {
            if (stop_) CloseHandle(stop_);
            if (begin_) CloseHandle(begin_);
            throw std::runtime_error("Cannot create local-render control events");
        }
        try { worker_ = std::thread([this] { run(); }); }
        catch (...) { CloseHandle(stop_); CloseHandle(begin_); throw; }
    }
    ~LocalAudioRenderer() {
        SetEvent(stop_);
        if (worker_.joinable()) worker_.join();
        CloseHandle(begin_); CloseHandle(stop_);
    }
    LocalAudioRenderer(const LocalAudioRenderer&) = delete;
    LocalAudioRenderer& operator=(const LocalAudioRenderer&) = delete;

    bool waitReady() {
        std::unique_lock lock(mutex_);
        return condition_.wait_for(lock, std::chrono::seconds(5),
            [this] { return stats_.ready || !error_.empty(); }) && stats_.ready && error_.empty();
    }
    bool arm(Clock::time_point sendStart, uint32_t latencyFrames) {
        std::lock_guard lock(mutex_);
        if (!stats_.ready || stats_.armed || !error_.empty() || !latencyFrames || latencyFrames > 99225) return false;
        const auto presentation = sendStart + std::chrono::nanoseconds(uint64_t(latencyFrames) * 1000000000ULL / 44100);
        if (presentation <= Clock::now()) return false;
        presentationEpoch_ = presentation;
        stats_.presentationLatencyFrames = latencyFrames;
        stats_.armed = true;
        SetEvent(begin_);
        return true;
    }
    std::string error() const { std::lock_guard lock(mutex_); return error_; }
    LocalRenderDiagnostics diagnostics() const { std::lock_guard lock(mutex_); return stats_; }

private:
    std::wstring endpoint_;
    Pull pull_;
    HANDLE stop_ = nullptr, begin_ = nullptr;
    std::thread worker_;
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    LocalRenderDiagnostics stats_;
    std::string error_;
    Clock::time_point presentationEpoch_{};

    static Clock::time_point estimatedWriteTime(IAudioClock* clock, uint64_t frequency,
        uint64_t writtenFrames, UINT32 padding, REFERENCE_TIME latency, LocalRenderDiagnostics& stats) {
        UINT64 position = 0, qpc = 0;
        const auto hr = clock ? clock->GetPosition(&position, &qpc) : E_NOINTERFACE;
        LARGE_INTEGER qpcNow{}, qpcFrequency{};
        QueryPerformanceCounter(&qpcNow);
        QueryPerformanceFrequency(&qpcFrequency);
        const auto now = Clock::now();
        // Keep the correlated raw observation together with the pre-write
        // count: writtenFrames in the published stats includes this pass's
        // later buffer release and cannot reconstruct the estimate alone.
        stats.clockPosition = position; stats.clockFrequency = frequency; stats.clockQpc100ns = qpc;
        stats.paddingFrames = padding; stats.writeEstimateWrittenFrames = writtenFrames;
        stats.clockQpcAgeKnown = false; stats.clockQpcAgeMs = 0;
        if (qpc && qpcFrequency.QuadPart > 0) {
            const double ageMs = double((static_cast<long double>(qpcNow.QuadPart) * 10000000 /
                                        qpcFrequency.QuadPart - qpc) / 10000);
            if (std::isfinite(ageMs)) { stats.clockQpcAgeMs = ageMs; stats.clockQpcAgeKnown = true; }
        }
        const long double deviceFrames = frequency ? static_cast<long double>(position) * 44100 / frequency : 0;
        if (hr == S_OK && position && qpc && frequency && qpcFrequency.QuadPart > 0 &&
            deviceFrames <= static_cast<long double>(writtenFrames) + padding + 4410) {
            // GetPosition's correlated QPC is already in 100 ns units. It
            // observes endpoint presentation; adding GetStreamLatency again
            // here would count the device pipeline twice.
            const long double current100ns = static_cast<long double>(qpcNow.QuadPart) * 10000000 / qpcFrequency.QuadPart;
            const long double ageNs = (current100ns - qpc) * 100;
            const long double queueNs = std::max(0.0L, static_cast<long double>(writtenFrames) - deviceFrames) * 1000000000 / 44100;
            if (ageNs >= 0 && ageNs < 1000000000 && queueNs < 1000000000) {
                stats.endpointClockValid = true;
                stats.deviceClockFrames = uint64_t(deviceFrames);
                stats.writeQueueDelayMs = double(queueNs / 1000000);
                stats.writePresentationOffsetMs = double(int64_t(queueNs - ageNs)) / 1000000;
                return now + std::chrono::nanoseconds(int64_t(queueNs - ageNs));
            }
        }
        stats.endpointClockValid = false;
        // The new stream's clock can stay zero until data reaches the device.
        // This fallback is a conservative estimate, never acoustic evidence.
        stats.writeQueueDelayMs = double(padding) * 1000 / 44100;
        stats.writePresentationOffsetMs = double(uint64_t(padding) * 1000000000ULL / 44100 + uint64_t(latency) * 100) / 1000000;
        return now + std::chrono::nanoseconds(uint64_t(padding) * 1000000000ULL / 44100 + uint64_t(latency) * 100);
    }

    void run() {
        const auto com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        HANDLE task = nullptr;
        LocalRenderDiagnostics stats;
        try {
            using namespace local_render_detail;
            checked(com, "Local renderer COM initialization");
            {
                Microsoft::WRL::ComPtr<IMMDeviceEnumerator> enumerator;
                checked(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_INPROC_SERVER,
                                         IID_PPV_ARGS(&enumerator)), "Local renderer endpoint enumeration");
                Microsoft::WRL::ComPtr<IMMDevice> device;
                checked(enumerator->GetDevice(endpoint_.c_str(), &device), "Local renderer explicit endpoint");
                Microsoft::WRL::ComPtr<IMMEndpoint> direction;
                checked(device.As(&direction), "Local renderer endpoint direction");
                EDataFlow flow;
                DWORD state = 0;
                checked(direction->GetDataFlow(&flow), "Local renderer endpoint direction");
                checked(device->GetState(&state), "Local renderer endpoint state");
                if (flow != eRender || state != DEVICE_STATE_ACTIVE)
                    throw std::runtime_error("Local group output must be an active render endpoint");
                struct EventOwner { HANDLE value = nullptr; ~EventOwner() { if (value) CloseHandle(value); } } samples;
                Microsoft::WRL::ComPtr<IAudioClient> client;
                checked(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                    reinterpret_cast<void**>(client.GetAddressOf())), "Local renderer WASAPI activation");
                WAVEFORMATEX format{WAVE_FORMAT_PCM, 2, 44100, 176400, 4, 16, 0};
                checked(client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                    AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM |
                    AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY | AUDCLNT_STREAMFLAGS_NOPERSIST,
                    300000, 0, &format, nullptr), "Local renderer initialization");
                samples.value = CreateEventW(nullptr, FALSE, FALSE, nullptr);
                if (!samples.value) throw std::runtime_error("Cannot create local render sample event");
                checked(client->SetEventHandle(samples.value), "Local renderer sample event");
                UINT32 capacity = 0;
                REFERENCE_TIME latency = 0;
                checked(client->GetBufferSize(&capacity), "Local renderer buffer capacity");
                checked(client->GetStreamLatency(&latency), "Local renderer maximum stream latency");
                if (!capacity || capacity > 44100 || latency < 0 || latency > 10000000)
                    throw std::runtime_error("Local renderer returned invalid buffer/latency");
                Microsoft::WRL::ComPtr<IAudioRenderClient> render;
                checked(client->GetService(IID_PPV_ARGS(&render)), "Local renderer buffer service");
                Microsoft::WRL::ComPtr<IAudioClock> clock;
                uint64_t frequency = 0;
                if (SUCCEEDED(client->GetService(IID_PPV_ARGS(&clock)))) {
                    if (FAILED(clock->GetFrequency(&frequency))) frequency = 0;
                }
                stats.ready = true;
                stats.streamLatencyMs = double(latency) / 10000;
                stats.bufferDurationMs = double(capacity) * 1000 / 44100;
                { std::lock_guard lock(mutex_); stats_ = stats; }
                condition_.notify_all();
                HANDLE controls[]{stop_, begin_};
                const auto begin = WaitForMultipleObjects(2, controls, FALSE, INFINITE);
                if (begin != WAIT_OBJECT_0 && begin != WAIT_OBJECT_0 + 1)
                    throw std::runtime_error("Local renderer control wait failed");
                if (begin == WAIT_OBJECT_0 + 1) {
                    Clock::time_point epoch;
                    { std::lock_guard lock(mutex_); epoch = presentationEpoch_; }
                    stats.armed = true;
                    { std::lock_guard lock(mutex_); stats.presentationLatencyFrames = stats_.presentationLatencyFrames; }
                    // Prime the device before Start; a future group barrier
                    // begins with silence, not an uninitialized render buffer.
                    BYTE* initial = nullptr;
                    checked(render->GetBuffer(capacity, &initial), "Local renderer priming buffer");
                    checked(render->ReleaseBuffer(capacity, AUDCLNT_BUFFERFLAGS_SILENT), "Local renderer priming release");
                    stats.writtenFrames = capacity;
                    DWORD taskIndex = 0;
                    task = AvSetMmThreadCharacteristicsW(L"Audio", &taskIndex);
                    checked(client->Start(), "Local renderer start");
                    struct ClientStop { IAudioClient* client; ~ClientStop() { client->Stop(); } } clientStop{client.Get()};
                    stats.playing = true;
                    bool cursorStarted = false;
                    double cursor = 0;
                    unsigned missingIntervals = 0;
                    std::vector<int16_t> source;
                    source.reserve((size_t(capacity) + 16) * 2);
                    HANDLE events[]{stop_, samples.value};
                    while (true) {
                        const auto wait = WaitForMultipleObjects(2, events, FALSE, 100);
                        if (wait == WAIT_OBJECT_0) break;
                        if (wait != WAIT_OBJECT_0 + 1 && wait != WAIT_TIMEOUT)
                            throw std::runtime_error("Local renderer sample wait failed");
                        UINT32 padding = 0;
                        checked(client->GetCurrentPadding(&padding), "Local renderer queued frames");
                        if (padding > capacity) throw std::runtime_error("Local renderer padding exceeds buffer");
                        const UINT32 available = capacity - padding;
                        if (!available) continue;
                        const auto presentation = estimatedWriteTime(clock.Get(), frequency, stats.writtenFrames,
                                                                    padding, latency, stats);
                        stats.writePresentationEpochOffsetMs = std::chrono::duration<double, std::milli>(presentation - epoch).count();
                        double desired = std::chrono::duration<double>(presentation - epoch).count() * 44100;
                        const UINT32 prefix = desired < 0 ? UINT32(std::min<double>(available, std::ceil(-desired))) : 0;
                        const UINT32 audible = available - prefix;
                        desired += prefix;
                        if (audible) {
                            if (!cursorStarted) { cursor = std::max(0.0, desired); cursorStarted = true; }
                            const auto phase = desired - cursor;
                            stats.phaseErrorMs = phase * 1000 / 44100;
                            if (stats.endpointClockValid && std::abs(stats.phaseErrorMs) > 250)
                                throw std::runtime_error("Local group clock lost synchronization");
                            stats.resampleRatio = slewRatio(phase);
                            if (!std::isfinite(cursor) || cursor > double(std::numeric_limits<uint64_t>::max() - 88202))
                                throw std::runtime_error("Local group source frame exceeds timeline");
                            const auto firstFrame = uint64_t(std::floor(cursor));
                            const auto neededFrames = size_t(std::ceil((audible - 1) * stats.resampleRatio + cursor - firstFrame)) + 2;
                            source.assign(neededFrames * 2, 0);
                            if (!pull_(firstFrame, source)) {
                                std::fill(source.begin(), source.end(), int16_t(0));
                                stats.underrunFrames += audible;
                                if (++missingIntervals >= 2)
                                    throw std::runtime_error("Shared music history is unavailable for local group output");
                            } else missingIntervals = 0;
                        }
                        BYTE* output = nullptr;
                        checked(render->GetBuffer(available, &output), "Local renderer write buffer");
                        if (!output) { render->ReleaseBuffer(available, AUDCLNT_BUFFERFLAGS_SILENT); throw std::runtime_error("Local renderer returned no output buffer"); }
                        auto* pcm = reinterpret_cast<int16_t*>(output);
                        std::fill_n(pcm, size_t(prefix) * 2, int16_t(0));
                        if (audible) {
                            const double fractional = cursor - std::floor(cursor);
                            for (UINT32 frame = 0; frame < audible; ++frame) {
                                const double position = fractional + frame * stats.resampleRatio;
                                const auto left = size_t(std::floor(position));
                                const auto part = position - left;
                                for (size_t channel = 0; channel < 2; ++channel) {
                                    const auto value = interpolate(source[left * 2 + channel], source[(left + 1) * 2 + channel], part);
                                    pcm[(size_t(prefix) + frame) * 2 + channel] = value;
                                    if (value) ++stats.nonzeroSamples;
                                    if (value < -1 || value > 1) ++stats.meaningfulSamples;
                                }
                            }
                            cursor += audible * stats.resampleRatio;
                            stats.sourceFrame = uint64_t(cursor);
                        }
                        checked(render->ReleaseBuffer(available, 0), "Local renderer write release");
                        stats.writtenFrames += available;
                        { std::lock_guard lock(mutex_); stats_ = stats; }
                    }
                    stats.playing = false;
                    { std::lock_guard lock(mutex_); stats_ = stats; }
                }
            }
        } catch (const std::exception& error) {
            stats.playing = false;
            std::lock_guard lock(mutex_); error_ = error.what(); stats_ = stats;
            condition_.notify_all();
        }
        if (task) AvRevertMmThreadCharacteristics(task);
        if (SUCCEEDED(com)) CoUninitialize();
    }
};
} // namespace gluspot
