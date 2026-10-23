// Authenticated, bounded direct PCM from the owning Qt player. This path never
// captures an endpoint or process tree and never owns a Windows mute state.
#pragma once
#include "group_capture.h"
#include "group_main.h"
#include "pcm_pipe_protocol.h"

namespace gluspot {
class GroupPcmPipeCapture {
public:
    GroupPcmPipeCapture(const std::string& suffix, DWORD parentPid, uint64_t creation, uint64_t epoch)
        : process_(parentPid, creation), epoch_(epoch), ring_(44100 * 2) {
        const std::wstring name = L"\\\\.\\pipe\\" + std::wstring(suffix.begin(), suffix.end());
        pipe_ = CreateNamedPipeW(name.c_str(), PIPE_ACCESS_INBOUND | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 0, 65536, 0, nullptr);
        stop_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        begin_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (pipe_ == INVALID_HANDLE_VALUE || !stop_ || !begin_) {
            closeHandles(); throw std::runtime_error("Cannot create direct PCM channel");
        }
        try { worker_ = std::thread([this] { run(); }); }
        catch (...) { closeHandles(); throw; }
    }
    ~GroupPcmPipeCapture() {
        SetEvent(stop_); CancelIoEx(pipe_, nullptr);
        if (worker_.joinable()) worker_.join();
        closeHandles();
    }
    bool waitReady() const { return pipe_ != INVALID_HANDLE_VALUE; }
    void start() { SetEvent(begin_); }
    std::string error() const { std::lock_guard lock(mutex_); return error_; }
    bool sourceAlive() const { return process_.alive(); }
    fxchain::RingBuffer<int16_t>& ring() { return ring_; }
    CaptureSignalSnapshot signal(bool resetPeak = false) { return signal_.snapshot(captureNowMs(), resetPeak); }
    std::atomic<uint64_t> frames{0}, droppedSamples{0}, discontinuities{0};
private:
    VerifiedMusicProcess process_;
    uint64_t epoch_;
    fxchain::RingBuffer<int16_t> ring_;
    CaptureSignalCounter signal_;
    HANDLE pipe_ = INVALID_HANDLE_VALUE, stop_ = nullptr, begin_ = nullptr;
    std::thread worker_;
    mutable std::mutex mutex_;
    std::string error_;
    void closeHandles() {
        if (pipe_ != INVALID_HANDLE_VALUE) CloseHandle(pipe_);
        if (stop_) CloseHandle(stop_);
        if (begin_) CloseHandle(begin_);
        pipe_ = INVALID_HANDLE_VALUE; stop_ = begin_ = nullptr;
    }
    bool finish(OVERLAPPED& pending, DWORD& count) {
        HANDLE handles[]{stop_, pending.hEvent};
        while (true) {
            const auto result = WaitForMultipleObjects(2, handles, FALSE, 100);
            if (result == WAIT_OBJECT_0) {
                CancelIoEx(pipe_, &pending);
                // Drain cancellation before releasing the OVERLAPPED storage.
                GetOverlappedResult(pipe_, &pending, &count, TRUE);
                return false;
            }
            if (result == WAIT_OBJECT_0 + 1) {
                if (!GetOverlappedResult(pipe_, &pending, &count, FALSE))
                    throw std::runtime_error("Direct PCM channel ended");
                return true;
            }
            if (result != WAIT_TIMEOUT) {
                CancelIoEx(pipe_, &pending);
                GetOverlappedResult(pipe_, &pending, &count, TRUE);
                throw std::runtime_error("Direct PCM channel wait failed");
            }
            if (!process_.alive()) {
                CancelIoEx(pipe_, &pending);
                GetOverlappedResult(pipe_, &pending, &count, TRUE);
                throw std::runtime_error("Direct PCM parent ended");
            }
        }
    }
    bool readExact(std::span<uint8_t> bytes) {
        size_t offset = 0;
        while (offset < bytes.size()) {
            if (WaitForSingleObject(stop_, 0) == WAIT_OBJECT_0) return false;
            OVERLAPPED pending{};
            pending.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
            if (!pending.hEvent) throw std::runtime_error("Cannot create PCM read event");
            struct Owner { HANDLE event; ~Owner() { CloseHandle(event); } } owner{pending.hEvent};
            DWORD count = 0;
            if (!ReadFile(pipe_, bytes.data() + offset, DWORD(bytes.size() - offset), &count, &pending)) {
                if (GetLastError() != ERROR_IO_PENDING) throw std::runtime_error("Direct PCM channel ended");
                if (!finish(pending, count)) return false;
            }
            if (!count) throw std::runtime_error("Direct PCM channel ended");
            offset += count;
        }
        return true;
    }
    void run() {
        try {
            OVERLAPPED pending{};
            pending.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
            if (!pending.hEvent) throw std::runtime_error("Cannot create PCM connection event");
            struct Owner { HANDLE event; ~Owner() { CloseHandle(event); } } owner{pending.hEvent};
            if (!ConnectNamedPipe(pipe_, &pending)) {
                const auto code = GetLastError();
                DWORD count = 0;
                if (code == ERROR_IO_PENDING) {
                    if (!finish(pending, count)) return;
                } else if (code != ERROR_PIPE_CONNECTED) {
                    throw std::runtime_error("Cannot connect direct PCM channel");
                }
            }
            ULONG client = 0;
            if (!GetNamedPipeClientProcessId(pipe_, &client) || client != process_.pid() || !process_.alive())
                throw std::runtime_error("Direct PCM client identity mismatch");
            HANDLE handles[]{stop_, begin_};
            while (true) {
                const auto ready = WaitForMultipleObjects(2, handles, FALSE, 100);
                if (ready == WAIT_OBJECT_0) return;
                if (ready == WAIT_OBJECT_0 + 1) break;
                if (ready != WAIT_TIMEOUT || !process_.alive()) throw std::runtime_error("Direct PCM parent ended before startup");
            }
            uint64_t sequence = 1;
            while (WaitForSingleObject(stop_, 0) != WAIT_OBJECT_0) {
                std::array<uint8_t, 24> bytes{};
                if (!readExact(bytes)) return;
                const auto header = parsePcmPipeHeader(bytes, epoch_, sequence);
                std::vector<int16_t> pcm(header.frames * 2);
                if (!readExact({reinterpret_cast<uint8_t*>(pcm.data()), pcm.size() * sizeof(int16_t)})) return;
                // Qt can deliver a burst after its GUI thread was stalled.
                // Keep this packet intact and let the pipe apply pressure to
                // its writer while the coordinator drains the bounded ring.
                // Stop/parent death must still interrupt a full-ring wait.
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
                while (!ring_.tryPush(pcm)) {
                    if (WaitForSingleObject(stop_, 2) == WAIT_OBJECT_0) return;
                    if (!process_.alive()) throw std::runtime_error("Direct PCM parent ended");
                    if (std::chrono::steady_clock::now() >= deadline) {
                        droppedSamples += pcm.size(); throw std::runtime_error("Direct PCM queue overran");
                    }
                }
                frames += header.frames;
                signal_.recordAccepted(pcm, captureNowMs());
                if (sequence == UINT64_MAX) throw std::runtime_error("Direct PCM sequence exhausted");
                ++sequence;
            }
        } catch (const std::exception& exception) {
            if (WaitForSingleObject(stop_, 0) != WAIT_OBJECT_0) {
                std::lock_guard lock(mutex_); error_ = exception.what();
            }
        }
    }
};

class GroupInput {
    std::unique_ptr<GroupCapture> process_;
    std::unique_ptr<GroupPcmPipeCapture> pcm_;
public:
    explicit GroupInput(const GroupArguments& args)
        : process_(args.directPcm() ? nullptr : std::make_unique<GroupCapture>(args.musicPid, args.musicCreatedAt)),
          pcm_(args.directPcm() ? std::make_unique<GroupPcmPipeCapture>(args.pcmPipe, args.pcmParentPid, args.pcmParentCreatedAt, args.pcmEpoch) : nullptr),
          frames(pcm_ ? pcm_->frames : process_->frames),
          droppedSamples(pcm_ ? pcm_->droppedSamples : process_->droppedSamples),
          discontinuities(pcm_ ? pcm_->discontinuities : process_->discontinuities) {}
    std::atomic<uint64_t>& frames;
    std::atomic<uint64_t>& droppedSamples;
    std::atomic<uint64_t>& discontinuities;
    bool waitReady() { return pcm_ ? pcm_->waitReady() : process_->waitReady(); }
    void start() { if (pcm_) pcm_->start(); else process_->start(); }
    std::string error() const { return pcm_ ? pcm_->error() : process_->error(); }
    bool sourceAlive() const { return pcm_ ? pcm_->sourceAlive() : process_->sourceAlive(); }
    fxchain::RingBuffer<int16_t>& ring() { return pcm_ ? pcm_->ring() : process_->ring(); }
    CaptureSignalSnapshot signal(bool reset = false) { return pcm_ ? pcm_->signal(reset) : process_->signal(reset); }
};
} // namespace gluspot
