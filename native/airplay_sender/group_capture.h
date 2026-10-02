// One verified music-process capture feeds every group member. No endpoint
// fallback: unresolved process capture must never forward other applications.
#pragma once
#include "process_loopback.h"
#include "capture_signal.h"
#include "pcm_convert.h"
#include "ring_buffer.h"
#include <avrt.h>
#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>

namespace gluspot {
inline void audioChecked(HRESULT hr, const char* operation) {
    if (FAILED(hr)) {
        std::ostringstream error;
        error << operation << " failed (HRESULT 0x" << std::hex << uint32_t(hr) << ')';
        throw std::runtime_error(error.str());
    }
}
inline uint64_t captureNowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
class VerifiedMusicProcess {
public:
    VerifiedMusicProcess(DWORD pid, uint64_t creation) : pid_(pid) {
        if (!pid || pid == GetCurrentProcessId() || !creation)
            throw std::invalid_argument("A verified music process identity is required");
        handle_ = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid);
        if (!handle_) throw std::runtime_error("Music process is no longer accessible");
        FILETIME created{}, exited{}, kernel{}, user{};
        if (!GetProcessTimes(handle_, &created, &exited, &kernel, &user) ||
            (uint64_t(created.dwHighDateTime) << 32 | created.dwLowDateTime) != creation ||
            WaitForSingleObject(handle_, 0) != WAIT_TIMEOUT) {
            CloseHandle(handle_); handle_ = nullptr;
            throw std::runtime_error("Music process identity changed before capture");
        }
    }
    ~VerifiedMusicProcess() { if (handle_) CloseHandle(handle_); }
    VerifiedMusicProcess(const VerifiedMusicProcess&) = delete;
    VerifiedMusicProcess& operator=(const VerifiedMusicProcess&) = delete;
    bool alive() const { return WaitForSingleObject(handle_, 0) == WAIT_TIMEOUT; }
    DWORD pid() const { return pid_; }
private:
    DWORD pid_;
    HANDLE handle_ = nullptr;
};

class GroupCapture {
public:
    GroupCapture(DWORD pid, uint64_t creation) : process_(pid, creation), ring_(44100 * 2) {
        stop_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        begin_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!stop_ || !begin_) {
            if (stop_) CloseHandle(stop_);
            if (begin_) CloseHandle(begin_);
            throw std::runtime_error("Cannot create music capture control events");
        }
        try { worker_ = std::thread([this] { run(); }); }
        catch (...) { CloseHandle(stop_); CloseHandle(begin_); throw; }
    }
    ~GroupCapture() {
        SetEvent(stop_);
        if (worker_.joinable()) worker_.join();
        CloseHandle(begin_); CloseHandle(stop_);
    }
    bool waitReady() {
        std::unique_lock lock(mutex_);
        return readyCondition_.wait_for(lock, std::chrono::seconds(5),
            [this] { return ready_ || !error_.empty(); }) && ready_;
    }
    void start() { SetEvent(begin_); }
    std::string error() const { std::lock_guard lock(mutex_); return error_; }
    bool sourceAlive() const { return process_.alive(); }
    fxchain::RingBuffer<int16_t>& ring() { return ring_; }
    CaptureSignalSnapshot signal(bool resetPeak = false) { return signal_.snapshot(captureNowMs(), resetPeak); }
    std::atomic<uint64_t> frames{0}, droppedSamples{0}, discontinuities{0};
private:
    VerifiedMusicProcess process_;
    fxchain::RingBuffer<int16_t> ring_;
    CaptureSignalCounter signal_;
    HANDLE stop_ = nullptr, begin_ = nullptr;
    std::thread worker_;
    mutable std::mutex mutex_;
    std::condition_variable readyCondition_;
    bool ready_ = false;
    std::string error_;
    void run() {
        const auto com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        struct ApartmentOwner {
            HRESULT initialized;
            ~ApartmentOwner() { if (SUCCEEDED(initialized)) CoUninitialize(); }
        } apartment{com};
        HANDLE task = nullptr;
        try {
            audioChecked(com, "Music capture COM");
            struct EventOwner { HANDLE handle = nullptr; ~EventOwner() { if (handle) CloseHandle(handle); } } samples;
            Microsoft::WRL::ComPtr<IAudioClient> client;
            audioChecked(activateProcessLoopback(stop_, client, process_.pid(), true), "Music process loopback activation");
            WAVEFORMATEX format{WAVE_FORMAT_PCM, 2, 44100, 176400, 4, 16, 0};
            audioChecked(client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
                0, 0, &format, nullptr), "Music process loopback initialization");
            samples.handle = CreateEventW(nullptr, FALSE, FALSE, nullptr);
            if (!samples.handle) throw std::runtime_error("Cannot create music capture notification event");
            audioChecked(client->SetEventHandle(samples.handle), "Music capture event");
            Microsoft::WRL::ComPtr<IAudioCaptureClient> capture;
            audioChecked(client->GetService(IID_PPV_ARGS(&capture)), "Music capture buffer service");
            { std::lock_guard lock(mutex_); ready_ = true; }
            readyCondition_.notify_all();
            HANDLE controls[]{stop_, begin_};
            const auto beginResult = WaitForMultipleObjects(2, controls, FALSE, INFINITE);
            if (beginResult == WAIT_OBJECT_0) return;
            if (beginResult != WAIT_OBJECT_0 + 1)
                throw std::runtime_error("Music capture start wait failed");
            DWORD index = 0;
            task = AvSetMmThreadCharacteristicsW(L"Audio", &index);
            audioChecked(client->Start(), "Music capture start");
            struct StreamOwner {
                IAudioClient* client;
                ~StreamOwner() { client->Stop(); }
            } stream{client.Get()};
            HANDLE events[]{stop_, samples.handle};
            while (true) {
                const auto sampleResult = WaitForMultipleObjects(2, events, FALSE, 100);
                if (sampleResult == WAIT_OBJECT_0) break;
                if (sampleResult != WAIT_TIMEOUT && sampleResult != WAIT_OBJECT_0 + 1)
                    throw std::runtime_error("Music capture sample wait failed");
                if (!process_.alive()) throw std::runtime_error("Music process exited; group stopped");
                UINT32 available = 0;
                audioChecked(capture->GetNextPacketSize(&available), "Music capture next packet");
                while (available && WaitForSingleObject(stop_, 0) == WAIT_TIMEOUT) {
                    BYTE* data = nullptr; UINT32 count = 0; DWORD flags = 0;
                    audioChecked(capture->GetBuffer(&data, &count, &flags, nullptr, nullptr), "Music capture buffer");
                    try {
                        if (count > 88200) throw std::runtime_error("Oversized music capture packet");
                        std::vector<int16_t> pcm;
                        const PcmFormat description{44100, 2, 16, 4, false, 0};
                        convertStereo(data, count, description, flags & AUDCLNT_BUFFERFLAGS_SILENT, pcm);
                        if (ring_.tryPush(pcm)) {
                            frames += count;
                            signal_.recordAccepted(pcm, captureNowMs());
                        } else droppedSamples += pcm.size();
                        if (flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) ++discontinuities;
                    } catch (...) { capture->ReleaseBuffer(count); throw; }
                    audioChecked(capture->ReleaseBuffer(count), "Music capture release");
                    audioChecked(capture->GetNextPacketSize(&available), "Music capture next packet");
                }
            }
        } catch (const std::exception& error) {
            std::lock_guard lock(mutex_); error_ = error.what();
            readyCondition_.notify_all();
        }
        if (task) AvRevertMmThreadCharacteristics(task);
    }
};
} // namespace gluspot
