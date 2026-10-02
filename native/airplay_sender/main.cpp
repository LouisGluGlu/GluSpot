// GluSpot: Windows endpoint loopback capture -> one authenticated AirPlay 2 sender.
#include "pcm_convert.h"
#include "endpoint_mute_guard.h"
#include "timing_estimate.h"
#include "helper_commands.h"
#include "process_loopback.h"
#include "capture_signal.h"
#include "group_main.h"
#include "raop_loop.h"
#include "raop_sender.h"
#include "ring_buffer.h"

#include <windows.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <avrt.h>
#include <ks.h>
#include <ksmedia.h>
#include <wrl/client.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

using Microsoft::WRL::ComPtr;
using namespace std::chrono_literals;

namespace {
std::atomic<bool> interrupted{false};
BOOL WINAPI consoleHandler(DWORD event) {
    if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT || event == CTRL_CLOSE_EVENT) {
        interrupted = true; return TRUE;
    }
    return FALSE;
}

std::string jsonString(const std::string& s) {
    std::string out = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += char(c); }
        else if (c < 32) {
            const char* hex = "0123456789abcdef";
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15];
        } else out += char(c);
    }
    return out + '"';
}
void status(const std::string& event, const std::string& message = "") {
    std::cout << "{\"event\":" << jsonString(event)
              << ",\"message\":" << jsonString(message) << "}\n" << std::flush;
}

void muteStatus(const std::string& phase, const std::string& endpoint, bool original, bool applied) {
    std::cout << "{\"event\":\"mute_state\",\"phase\":" << jsonString(phase)
              << ",\"endpoint_id\":" << jsonString(endpoint)
              << ",\"original_muted\":" << (original ? "true" : "false")
              << ",\"applied\":" << (applied ? "true" : "false") << "}\n" << std::flush;
}

std::string receiverVolumeJson(const fxchain::RaopDiagnostics& d) {
    std::ostringstream out;
    out << "{\"volume\":";
    if (d.receiverVolumeKnown) out << d.receiverVolumePct / 100.0;
    else out << "null";
    out << ",\"known\":" << (d.receiverVolumeKnown ? "true" : "false")
        << ",\"source\":" << jsonString(d.receiverVolumeSource)
        << ",\"operation\":" << jsonString(d.receiverVolumeOperation)
        << ",\"pending\":" << (d.receiverVolumePending ? "true" : "false")
        << ",\"last_code\":" << d.lastVolumeCode << '}';
    return out.str();
}

uint64_t monotonicMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
void signalJson(const gluspot::CaptureSignalSnapshot& signal, bool processLoopback) {
    std::cout << "{\"mode\":\"" << (processLoopback ? "process_loopback" : "endpoint_loopback")
              << "\",\"scope\":\"" << (processLoopback ? "all_other_processes" : "selected_endpoint")
              << "\",\"nonzero_samples\":" << signal.nonzeroSamples << ",\"last_nonzero_age_ms\":";
    if (signal.lastNonzeroAgeMs) std::cout << *signal.lastNonzeroAgeMs;
    else std::cout << "null";
    std::cout << ",\"peak\":" << signal.peak << ",\"meaningful_samples\":" << signal.meaningfulSamples
              << ",\"last_meaningful_age_ms\":";
    if (signal.lastMeaningfulAgeMs) std::cout << *signal.lastMeaningfulAgeMs;
    else std::cout << "null";
    std::cout << '}';
}
void transportStats(const fxchain::RaopSender& sender, uint64_t captured, uint64_t dropped,
                    uint64_t discontinuities, gluspot::BufferTimingTracker& timing, bool streaming,
                    const gluspot::CaptureSignalSnapshot& signal, bool processLoopback) {
    const auto d = sender.diagnostics();
    const auto estimate = timing.sample({captured, d.audioFramesSent, d.silenceFrames,
        dropped, discontinuities, d.queuedInputFrames, d.inputSampleRate,
        d.protocolLatencyFrames, streaming});
    std::cout << "{\"event\":\"stats\",\"captured_frames\":" << captured
              << ",\"dropped_samples\":" << dropped << ",\"discontinuities\":" << discontinuities
              << ",\"protocol\":{\"feedback_sent\":" << d.feedbackSent
              << ",\"feedback_replies\":" << d.feedbackReplies << ",\"feedback_rejected\":" << d.feedbackRejected
              << ",\"feedback_skipped\":" << d.feedbackSkipped << ",\"last_feedback_code\":" << d.lastFeedbackCode
              << ",\"event_requests\":" << d.eventRequests << ",\"event_acks\":" << d.eventAcks
              << ",\"event_decrypt_failures\":" << d.eventDecryptFailures
              << ",\"event_connected\":" << (d.eventConnected ? "true" : "false")
              << ",\"event_keyed\":" << (d.eventKeyed ? "true" : "false")
              << ",\"audio_frames_sent\":" << d.audioFramesSent << ",\"silence_frames\":" << d.silenceFrames
              << ",\"nonzero_samples_sent\":" << d.nonzeroSamplesSent
              << ",\"meaningful_samples_sent\":" << d.meaningfulSamplesSent
              << ",\"queued_input_frames\":" << d.queuedInputFrames
              << ",\"input_sample_rate\":" << d.inputSampleRate
              << ",\"protocol_latency_frames\":" << d.protocolLatencyFrames
              << ",\"receiver_latency_frames\":" << d.receiverLatencyFrames
              << ",\"receiver_latency_reported\":" << (d.receiverLatencyReported ? "true" : "false")
              << ",\"last_error\":" << jsonString(d.lastError)
              << "},\"timing\":{\"method\":\"protocol_plus_input_queue\""
              << ",\"estimated_delay_ms\":" << estimate.delayMs()
              << ",\"protocol_buffer_ms\":" << estimate.protocolBufferMs
              << ",\"input_queue_ms\":" << estimate.inputQueueMs
              << ",\"valid\":" << (estimate.valid ? "true" : "false")
              << "},\"receiver_volume\":" << receiverVolumeJson(d) << ",\"capture_signal\":";
    signalJson(signal, processLoopback);
    std::cout << "}\n" << std::flush;
}

std::wstring wide(const std::string& text) {
    const int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), nullptr, 0);
    if (!n) throw std::runtime_error("Invalid UTF-8 endpoint ID");
    std::wstring result(size_t(n), L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), result.data(), n);
    return result;
}
std::string utf8(const wchar_t* value) {
    const int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, nullptr, 0, nullptr, nullptr);
    if (!n) throw std::runtime_error("Invalid Unicode command argument");
    std::string result(size_t(n), '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, result.data(), n, nullptr, nullptr);
    result.pop_back();
    return result;
}
void check(HRESULT hr, const char* action) {
    if (FAILED(hr)) {
        std::ostringstream out;
        out << action << " failed (HRESULT 0x" << std::hex << uint32_t(hr) << ')';
        throw std::runtime_error(out.str());
    }
}

class Capture {
public:
    explicit Capture(std::string endpoint, bool preferProcess = false, bool requireProcess = false)
        : endpoint_(std::move(endpoint)), preferProcess_(preferProcess), requireProcess_(requireProcess) {
        stopEvent_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!stopEvent_) throw std::runtime_error("Cannot create capture stop event");
        worker_ = std::thread([this] { run(); });
    }
    ~Capture() {
        SetEvent(stopEvent_);
        if (worker_.joinable()) worker_.join();
        CloseHandle(stopEvent_);
    }
    bool waitReady() {
        std::unique_lock lock(mutex_);
        return condition_.wait_for(lock, 5s, [this] { return ready_ || !error_.empty(); }) && ready_;
    }
    std::string error() const { std::lock_guard lock(mutex_); return error_; }
    const gluspot::PcmFormat& format() const { return format_; }
    fxchain::RingBuffer<int16_t>& ring() { return *ring_; }
    void start() { begin_ = true; }
    bool processLoopback() const { return processLoopback_; }
    gluspot::CaptureSignalSnapshot signal(bool resetPeak = false) {
        return signal_.snapshot(monotonicMs(), resetPeak);
    }
    std::atomic<uint64_t> frames{0}, droppedSamples{0}, discontinuities{0};

private:
    std::string endpoint_;
    bool preferProcess_ = false, requireProcess_ = false, processLoopback_ = false;
    gluspot::CaptureSignalCounter signal_;
    HANDLE stopEvent_ = nullptr;
    std::thread worker_;
    std::atomic<bool> begin_{false};
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    bool ready_ = false;
    std::string error_;
    gluspot::PcmFormat format_;
    std::unique_ptr<fxchain::RingBuffer<int16_t>> ring_;

    void run() {
        struct StopRequested {};
        const HRESULT com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        HANDLE task = nullptr;
        try {
            check(com, "COM initialization");
            ComPtr<IMMDeviceEnumerator> enumerator;
            check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                   IID_PPV_ARGS(&enumerator)), "Audio endpoint enumeration");
            ComPtr<IMMDevice> device;
            if (endpoint_.empty())
                check(enumerator->GetDefaultAudioEndpoint(eRender, eMultimedia, &device), "Default endpoint");
            else
                check(enumerator->GetDevice(wide(endpoint_).c_str(), &device), "Selected endpoint");
            ComPtr<IMMEndpoint> direction;
            check(device.As(&direction), "Endpoint direction");
            EDataFlow flow;
            check(direction->GetDataFlow(&flow), "Endpoint direction");
            if (flow != eRender) throw std::runtime_error("Capture source must be a render endpoint");
            DWORD state;
            check(device->GetState(&state), "Endpoint state");
            if (state != DEVICE_STATE_ACTIVE) throw std::runtime_error("Selected source endpoint is not active");
            struct EventOwner { HANDLE handle = nullptr; ~EventOwner() { if (handle) CloseHandle(handle); } } sampleOwner;
            // Release the client before closing its notification event, even
            // when initialization/capture exits through an exception.
            ComPtr<IAudioClient> client;
            WAVEFORMATEX* mix = nullptr;
            HANDLE sampleEvent = nullptr;
            if (preferProcess_) {
                const auto activation = gluspot::activateProcessLoopback(stopEvent_, client);
                if (WaitForSingleObject(stopEvent_, 0) == WAIT_OBJECT_0) throw StopRequested{};
                if (SUCCEEDED(activation)) {
                    // The virtual mixer need not implement GetMixFormat. The
                    // Microsoft sample uses explicit 44.1 kHz stereo PCM16.
                    WAVEFORMATEX requested{};
                    requested.wFormatTag = WAVE_FORMAT_PCM;
                    requested.nChannels = 2;
                    requested.nSamplesPerSec = 44100;
                    requested.wBitsPerSample = 16;
                    requested.nBlockAlign = 4;
                    requested.nAvgBytesPerSec = 176400;
                    const auto initialized = client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                        AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
                        AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM, 0, 0, &requested, nullptr);
                    if (SUCCEEDED(initialized)) {
                        sampleEvent = sampleOwner.handle = CreateEventW(nullptr, FALSE, FALSE, nullptr);
                        if (!sampleEvent) throw std::runtime_error("Cannot create process capture event");
                        check(client->SetEventHandle(sampleEvent), "Process loopback event");
                        mix = static_cast<WAVEFORMATEX*>(CoTaskMemAlloc(sizeof(requested)));
                        if (!mix) throw std::bad_alloc();
                        *mix = requested;
                        processLoopback_ = true;
                    } else if (requireProcess_) check(initialized, "Process loopback initialization");
                } else if (requireProcess_) check(activation, "Process loopback activation");
                if (!processLoopback_) {
                    std::cerr << "Process loopback unavailable; using selected endpoint loopback\n";
                    client.Reset();
                }
            }
            if (!processLoopback_) {
                check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                                       reinterpret_cast<void**>(client.GetAddressOf())), "WASAPI activation");
                check(client->GetMixFormat(&mix), "WASAPI mix format");
            }
            std::unique_ptr<WAVEFORMATEX, decltype(&CoTaskMemFree)> mixOwner(mix, CoTaskMemFree);
            bool floating = mix->wFormatTag == WAVE_FORMAT_IEEE_FLOAT;
            uint32_t mask = 0;
            if (mix->wFormatTag == WAVE_FORMAT_EXTENSIBLE && mix->cbSize >= 22) {
                const auto* ext = reinterpret_cast<const WAVEFORMATEXTENSIBLE*>(mix);
                mask = ext->dwChannelMask;
                floating = IsEqualGUID(ext->SubFormat, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT);
                if (!floating && !IsEqualGUID(ext->SubFormat, KSDATAFORMAT_SUBTYPE_PCM))
                    throw std::runtime_error("Unsupported WASAPI encoding");
            } else if (!floating && mix->wFormatTag != WAVE_FORMAT_PCM)
                throw std::runtime_error("Unsupported WASAPI encoding");
            format_ = {mix->nSamplesPerSec, mix->nChannels, mix->wBitsPerSample,
                       mix->nBlockAlign, floating, mask};
            gluspot::validateFormat(format_);
            if (!processLoopback_)
                check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK,
                                         1000000, 0, mix, nullptr), "WASAPI loopback initialization");
            ComPtr<IAudioCaptureClient> capture;
            check(client->GetService(IID_PPV_ARGS(&capture)), "WASAPI capture service");
            // About 170 ms at 48 kHz; bounded so a stalled sender never builds
            // an unbounded backlog. The ring has one producer and one consumer.
            ring_ = std::make_unique<fxchain::RingBuffer<int16_t>>(format_.rate / 3);
            {
                std::lock_guard lock(mutex_); ready_ = true;
            }
            condition_.notify_all();
            while (!begin_ && WaitForSingleObject(stopEvent_, 10) == WAIT_TIMEOUT) {}
            if (WaitForSingleObject(stopEvent_, 0) == WAIT_OBJECT_0) throw StopRequested{};
            DWORD taskIndex = 0;
            task = AvSetMmThreadCharacteristicsW(L"Audio", &taskIndex);
            check(client->Start(), "WASAPI capture start");
            std::vector<int16_t> converted;
            while (WaitForSingleObject(stopEvent_, 5) == WAIT_TIMEOUT) {
                UINT32 packet = 0;
                check(capture->GetNextPacketSize(&packet), "WASAPI next packet");
                while (packet && WaitForSingleObject(stopEvent_, 0) == WAIT_TIMEOUT) {
                    BYTE* data = nullptr;
                    UINT32 count = 0;
                    DWORD flags = 0;
                    check(capture->GetBuffer(&data, &count, &flags, nullptr, nullptr), "WASAPI capture buffer");
                    // Always release the borrowed driver buffer, including errors.
                    try {
                        if (count > format_.rate * 2)
                            throw std::runtime_error("WASAPI returned an oversized capture packet");
                        gluspot::convertStereo(data, count, format_,
                                               flags & AUDCLNT_BUFFERFLAGS_SILENT, converted);
                        if (!ring_->tryPush(std::span<const int16_t>(converted)))
                            droppedSamples += converted.size();
                        else {
                            frames += count;
                            signal_.recordAccepted(std::span<const int16_t>(converted), monotonicMs());
                        }
                        if (flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) ++discontinuities;
                    } catch (...) { capture->ReleaseBuffer(count); throw; }
                    check(capture->ReleaseBuffer(count), "WASAPI capture release");
                    check(capture->GetNextPacketSize(&packet), "WASAPI next packet");
                }
            }
            client->Stop();
        } catch (const StopRequested&) {
            // Initialization finished, but the parent cancelled before Start.
        } catch (const std::exception& ex) {
            std::lock_guard lock(mutex_); error_ = ex.what();
            condition_.notify_all();
        }
        if (task) AvRevertMmThreadCharacteristics(task);
        if (SUCCEEDED(com)) CoUninitialize();
    }
};

// The Python parent owns a pipe. Poll it instead of keeping a blocked input
// thread alive after a network error. EOF also ends the casting session.
bool commandsFromStdin(std::string& pending, fxchain::RaopSender& sender, bool authenticated) {
    const HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (!input || input == INVALID_HANDLE_VALUE || GetFileType(input) != FILE_TYPE_PIPE) return false;
    DWORD count = 0;
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &count, nullptr))
        return GetLastError() == ERROR_BROKEN_PIPE;
    std::optional<double> latestVolume;
    DWORD budget = 4096;  // A slider flood must yield to the audio pacer.
    while (count && budget) {
        char buffer[256]; DWORD read = 0;
        if (!ReadFile(input, buffer, std::min<DWORD>({count, sizeof(buffer), budget}), &read, nullptr) || !read)
            return true;
        budget -= read;
        pending.append(buffer, read);
        if (pending.size() > 1024) return true;
        size_t newline;
        while ((newline = pending.find('\n')) != std::string::npos) {
            std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            if (line == "stop" || line == "stop\r") return true;
            if (auto volume = gluspot::volumeCommand(line)) latestVolume = volume;
        }
        if (!PeekNamedPipe(input, nullptr, 0, nullptr, &count, nullptr)) return true;
    }
    if (latestVolume && authenticated) sender.setVolume(*latestVolume * 100.0);
    return false;
}
}  // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc > 1 && std::wstring(argv[1]) == L"--group")
        return gluspot::runGroupHelper(argc, argv);
    SetConsoleCtrlHandler(consoleHandler, TRUE);
    std::string host, endpoint, deviceId, name = "HomePod", sourceId = "02:47:4C:55:53:50";
    uint16_t port = 7000;
    bool probe = false, exclusiveOutput = false, processProbe = false;
    try {
        for (int i = 1; i < argc; ++i) {
            const std::string arg = utf8(argv[i]);
            if (arg == "--probe-capture") { probe = true; continue; }
            if (arg == "--process-loopback") { processProbe = true; continue; }
            if (arg == "--exclusive-output") { exclusiveOutput = true; continue; }
            if (i + 1 >= argc) throw std::runtime_error("Missing option value");
            const std::string value = utf8(argv[++i]);
            if (arg == "--host") host = value;
            else if (arg == "--endpoint") endpoint = value;
            else if (arg == "--device-id") deviceId = value;
            else if (arg == "--source-id") sourceId = value;
            else if (arg == "--name") name = value;
            else if (arg == "--port") {
                size_t used = 0; const auto parsed = std::stoul(value, &used);
                if (used != value.size() || parsed == 0 || parsed > 65535) throw std::runtime_error("Invalid port");
                port = uint16_t(parsed);
            } else throw std::runtime_error("Unknown option: " + arg);
        }
        // Numeric IPv4 only: avoid synchronous DNS in the host's tcpConnect.
        unsigned a, b, c, d; char extra;
        if (!probe && (endpoint.empty() || std::sscanf(host.c_str(), "%u.%u.%u.%u%c", &a, &b, &c, &d, &extra) != 4 ||
                       a > 255 || b > 255 || c > 255 || d > 255))
            throw std::runtime_error("Casting requires --host IPv4 and --endpoint render ID");
        if (probe && exclusiveOutput)
            throw std::runtime_error("Capture probe does not support --exclusive-output");
        if (processProbe && !probe)
            throw std::runtime_error("--process-loopback is only supported with --probe-capture");
    } catch (const std::exception& ex) { status("error", ex.what()); return 2; }

    try {
        // Constructing the guard reads state only. Muting begins after the
        // receiver authenticates; the parent gets the original state first.
        std::unique_ptr<gluspot::EndpointMuteGuard> muteGuard;
        if (exclusiveOutput) {
            muteGuard = std::make_unique<gluspot::EndpointMuteGuard>(wide(endpoint));
            muteStatus("prepared", endpoint, muteGuard->originalMuted(), false);
        }
        Capture capture(endpoint, exclusiveOutput || processProbe, processProbe);
        if (!capture.waitReady()) {
            const auto why = capture.error();
            status("error", why.empty() ? "Capture initialization timed out" : why); return 1;
        }
        if (probe) {
            std::cout << "{\"event\":\"capture_ready\",\"sample_rate\":" << capture.format().rate
                      << ",\"channels\":" << capture.format().channels << ",\"bits\":" << capture.format().bits
                      << "}\n" << std::flush;
            capture.start();
            std::this_thread::sleep_for(250ms);
            std::cout << "{\"event\":\"stats\",\"captured_frames\":" << capture.frames.load() << ",\"capture_signal\":";
            signalJson(capture.signal(), capture.processLoopback());
            std::cout << "}\n" << std::flush;
            if (!capture.error().empty()) { status("error", capture.error()); return 1; }
            status("stopped"); return 0;
        }
        fxchain::RaopLoop loop;
        bool authenticated = false, failed = false, reportedStreaming = false;
        fxchain::RaopEvents events;
        events.launched = [&](bool ok, const std::string& error) {
            if (!ok) { status("error", error); failed = true; }
            else {
                try {
                    if (muteGuard) {
                        muteGuard->mute();
                        muteStatus("muted", endpoint, muteGuard->originalMuted(), true);
                    }
                    // Begin sampling only after mute succeeds: a driver's
                    // final pre-mute buffer must not certify exclusive output.
                    capture.start();
                    authenticated = true;
                    status("waiting_audio", "AirPlay connected; waiting for source audio");
                } catch (const std::exception& ex) {
                    status("error", ex.what()); failed = true;
                }
            }
        };
        events.closed = [&] {
            if (!failed) status("error", "Receiver ended the AirPlay session");
            failed = true;
        };
        events.pinRequired = [&](const std::string&) {
            status("error", "This receiver requires PIN pairing; this build supports open HomePod transient pairing");
            failed = true;
        };
        fxchain::RaopSender sender(loop, std::move(events),
            [](fxchain::RaopLogLevel, const std::string& line) { std::cerr << line << '\n'; });
        sender.setStrictReceiverAuth(true);
        sender.setPreserveReceiverVolume(true);
        sender.attachRing(&capture.ring());
        sender.setInputFormat(capture.format().rate);
        sender.setIdentity({"GluSpot", sourceId, "iPhone14,3"});
        sender.setAuth(fxchain::RaopDeviceInfo::Auth::HapTransient, true, deviceId, "", "");
        std::string pending;
        // Initialization is bounded but can outlast parent cancellation. Do
        // not start a receiver session after an already closed/stopped pipe.
        if (interrupted || commandsFromStdin(pending, sender, false)) {
            status("stopped"); return 0;
        }
        status("connecting");
        sender.start(host, port, name);
        const auto connectDeadline = std::chrono::steady_clock::now() + 20s;
        auto statsDeadline = std::chrono::steady_clock::now() + 500ms;
        gluspot::BufferTimingTracker timing;
        auto muteDeadline = std::chrono::steady_clock::now() + 250ms;
        auto volumeDeadline = std::chrono::steady_clock::now();
        uint64_t volumeRevision = ~uint64_t(0);
        while (!failed && !interrupted && !commandsFromStdin(pending, sender, authenticated)) {
            loop.pump(sender, 20ms);
            if (failed) break;
            const auto error = capture.error();
            if (!error.empty()) { status("error", error); failed = true; break; }
            if (!authenticated && std::chrono::steady_clock::now() >= connectDeadline) {
                status("error", "AirPlay connection timed out; check receiver access and inbound UDP firewall rules");
                failed = true; break;
            }
            if (authenticated && muteGuard && std::chrono::steady_clock::now() >= muteDeadline) {
                if (!muteGuard->isStillMuted()) {
                    status("error", "Local output was unmuted externally; HomePod casting stopped");
                    failed = true; break;
                }
                muteDeadline = std::chrono::steady_clock::now() + 250ms;
            }
            const auto signal = capture.signal();
            if (authenticated && gluspot::hasStreamingSignal(signal, sender.diagnostics().meaningfulSamplesSent)
                && !reportedStreaming) {
                reportedStreaming = true; status("streaming");
            }
            if (authenticated && std::chrono::steady_clock::now() >= volumeDeadline) {
                sender.requestReceiverVolume();
                volumeDeadline = std::chrono::steady_clock::now() + 5s;
            }
            const auto d = sender.diagnostics();
            if (authenticated && d.receiverVolumeRevision != volumeRevision) {
                volumeRevision = d.receiverVolumeRevision;
                std::cout << "{\"event\":\"receiver_volume\",\"receiver_volume\":"
                          << receiverVolumeJson(d) << "}\n" << std::flush;
            }
            if (authenticated && std::chrono::steady_clock::now() >= statsDeadline) {
                transportStats(sender, capture.frames.load(), capture.droppedSamples.load(),
                               capture.discontinuities.load(), timing, true,
                               capture.signal(true), capture.processLoopback());
                statsDeadline = std::chrono::steady_clock::now() + 500ms;
            }
        }
        // One final bounded snapshot preserves the close/failure evidence.
        transportStats(sender, capture.frames.load(), capture.droppedSamples.load(),
                       capture.discontinuities.load(), timing, false,
                       capture.signal(), capture.processLoopback());
        sender.stop();
        if (muteGuard) {
            try {
                muteGuard->restore();
                muteStatus("restored", endpoint, muteGuard->originalMuted(), false);
            } catch (const std::exception& ex) {
                status("error", ex.what()); failed = true;
            }
        }
        status("stopped");
        return failed ? 1 : 0;
    } catch (const std::exception& ex) { status("error", ex.what()); return 1; }
}
