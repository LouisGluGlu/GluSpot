// GluSpot synchronized group host: one exact music-process capture, one final
// PCM timeline, independently authenticated AirPlay receivers and optional
// Windows render output. No receiver owns an independent capture/pacer.
#include "group_main.h"
#include "group_capture.h"
#include "endpoint_mute_guard.h"
#include "timing_estimate.h"
#include "raop_loop.h"
#include "raop_sender.h"
#include <windows.h>
#include <mmdeviceapi.h>
#include <wrl/client.h>
#include <atomic>
#include <chrono>
#include <iostream>
#include <memory>
#include <thread>

#if __has_include("local_audio_renderer.h")
#include "local_audio_renderer.h"
#define GLUSPOT_HAS_LOCAL_RENDERER 1
#else
#define GLUSPOT_HAS_LOCAL_RENDERER 0
#endif

namespace gluspot {
namespace {
using Clock = fxchain::RaopSender::Clock;
using Microsoft::WRL::ComPtr;
using namespace std::chrono_literals;
std::atomic<bool> groupInterrupted{false};
BOOL WINAPI groupConsoleHandler(DWORD control) {
    if (control == CTRL_C_EVENT || control == CTRL_BREAK_EVENT || control == CTRL_CLOSE_EVENT) {
        groupInterrupted = true; return TRUE;
    }
    return FALSE;
}
std::string quoted(const std::string& text) {
    std::string output = "\"";
    const char* hex = "0123456789abcdef";
    for (const unsigned char byte : text) {
        if (byte == '"' || byte == '\\') { output += '\\'; output += char(byte); }
        else if (byte < 32) { output += "\\u00"; output += hex[byte >> 4]; output += hex[byte & 15]; }
        else output += char(byte);
    }
    return output + '"';
}
std::string utf8(const wchar_t* text) {
    const int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text, -1, nullptr, 0, nullptr, nullptr);
    if (!count) throw std::invalid_argument("Invalid Unicode group argument");
    std::string output(size_t(count), '\0');
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text, -1, output.data(), count, nullptr, nullptr))
        throw std::invalid_argument("Cannot convert group argument to UTF-8");
    output.pop_back(); return output;
}
std::wstring wide(const std::string& text) {
    const int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), nullptr, 0);
    if (!count) throw std::invalid_argument("Invalid UTF-8 group endpoint");
    std::wstring output(size_t(count), L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), output.data(), count))
        throw std::invalid_argument("Cannot convert group endpoint");
    return output;
}
void status(const std::string& event, const std::string& message = {}) {
    std::cout << "{\"event\":" << quoted(event) << ",\"message\":" << quoted(message) << "}\n" << std::flush;
}
void muteStatus(const char* phase, const GroupArguments& args, const EndpointMuteGuard& guard, bool applied) {
    std::cout << "{\"event\":\"mute_state\",\"phase\":" << quoted(phase)
              << ",\"scope\":\"selected_endpoint\",\"endpoint_id\":" << quoted(args.sourceEndpoint)
              << ",\"original_muted\":" << (guard.originalMuted() ? "true" : "false")
              << ",\"applied\":" << (applied ? "true" : "false") << "}\n" << std::flush;
}
uint64_t ntpWallNow() {
    const auto us = uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
    return ((us / 1000000ULL + 0x83AA7E80ULL) << 32) + (us % 1000000ULL) * 0x100000000ULL / 1000000ULL;
}
struct Apartment {
    Apartment() { audioChecked(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "Group host COM"); }
    ~Apartment() { CoUninitialize(); }
};
class SourceEndpointGuard {
public:
    explicit SourceEndpointGuard(const std::string& endpoint) {
        audioChecked(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_INPROC_SERVER,
                                     IID_PPV_ARGS(&enumerator_)), "Group source enumeration");
        audioChecked(enumerator_->GetDevice(wide(endpoint).c_str(), &device_), "Group source endpoint");
        ComPtr<IMMEndpoint> direction; EDataFlow flow{};
        audioChecked(device_.As(&direction), "Group source direction");
        audioChecked(direction->GetDataFlow(&flow), "Group source direction");
        if (flow != eRender) throw std::invalid_argument("Group source must be an active render endpoint");
        originalDefault_ = currentDefault(); check();
    }
    void check() const {
        DWORD state = 0;
        audioChecked(device_->GetState(&state), "Group source endpoint state");
        if (state != DEVICE_STATE_ACTIVE) throw std::runtime_error("Music source endpoint disconnected; group stopped");
        if (currentDefault() != originalDefault_)
            throw std::runtime_error("Windows default output changed; group stopped");
    }
private:
    ComPtr<IMMDeviceEnumerator> enumerator_;
    ComPtr<IMMDevice> device_;
    std::wstring originalDefault_;
    std::wstring currentDefault() const {
        ComPtr<IMMDevice> device;
        audioChecked(enumerator_->GetDefaultAudioEndpoint(eRender, eMultimedia, &device), "Group default output");
        LPWSTR raw = nullptr;
        audioChecked(device->GetId(&raw), "Group default output identity");
        std::unique_ptr<wchar_t, decltype(&CoTaskMemFree)> owner(raw, CoTaskMemFree);
        if (!raw) throw std::runtime_error("Windows default output has no identity");
        return raw;
    }
};
struct Member {
    GroupReceiverArgument args;
    fxchain::RaopLoop loop;
    std::unique_ptr<fxchain::RaopSender> sender;
    bool ready = false;
    std::string state = "connecting";
    uint64_t volumeRevision = UINT64_MAX;
    GroupFeedbackWatchdog feedback;
};
void receiverVolume(std::ostream& out, const fxchain::RaopDiagnostics& d) {
    out << "{\"volume\":";
    if (d.receiverVolumeKnown) out << d.receiverVolumePct / 100.0; else out << "null";
    out << ",\"known\":" << (d.receiverVolumeKnown ? "true" : "false")
        << ",\"source\":" << quoted(d.receiverVolumeSource) << ",\"operation\":" << quoted(d.receiverVolumeOperation)
        << ",\"pending\":" << (d.receiverVolumePending ? "true" : "false") << ",\"last_code\":" << d.lastVolumeCode << '}';
}
void protocol(std::ostream& out, const fxchain::RaopDiagnostics& d, uint64_t silence = 0, size_t queued = 0) {
    out << "{\"feedback_sent\":" << d.feedbackSent << ",\"feedback_replies\":" << d.feedbackReplies
        << ",\"feedback_rejected\":" << d.feedbackRejected << ",\"feedback_skipped\":" << d.feedbackSkipped
        << ",\"last_feedback_code\":" << d.lastFeedbackCode << ",\"event_requests\":" << d.eventRequests
        << ",\"event_acks\":" << d.eventAcks << ",\"event_decrypt_failures\":" << d.eventDecryptFailures
        << ",\"event_connected\":" << (d.eventConnected ? "true" : "false")
        << ",\"event_keyed\":" << (d.eventKeyed ? "true" : "false")
        << ",\"audio_frames_sent\":" << d.audioFramesSent << ",\"silence_frames\":" << silence
        << ",\"nonzero_samples_sent\":" << d.nonzeroSamplesSent << ",\"queued_input_frames\":" << queued
        << ",\"meaningful_samples_sent\":" << d.meaningfulSamplesSent
        << ",\"input_sample_rate\":44100,\"protocol_latency_frames\":" << d.protocolLatencyFrames
        << ",\"receiver_latency_frames\":" << d.receiverLatencyFrames
        << ",\"receiver_latency_reported\":" << (d.receiverLatencyReported ? "true" : "false")
        << ",\"coordinated_playback\":" << (d.coordinatedPlayback ? "true" : "false")
        << ",\"timeline_armed\":" << (d.coordinatedTimelineArmed ? "true" : "false")
        << ",\"last_error\":" << quoted(d.lastError) << '}';
}
void memberStates(std::ostream& out, const std::vector<std::unique_ptr<Member>>& members) {
    out << '[';
    for (size_t index = 0; index < members.size(); ++index) {
        if (index) out << ',';
        const auto& member = *members[index];
        const auto d = member.sender->diagnostics();
        out << "{\"receiver_id\":" << quoted(member.args.id) << ",\"state\":" << quoted(member.state)
            << ",\"receiver_volume\":"; receiverVolume(out, d);
        out << ",\"protocol\":"; protocol(out, d);
        out << '}';
    }
    out << ']';
}
bool stdinCommands(std::string& pending, std::vector<std::unique_ptr<Member>>& members) {
    const HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (!input || input == INVALID_HANDLE_VALUE || GetFileType(input) != FILE_TYPE_PIPE) return false;
    DWORD available = 0;
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) return true;
    std::array<std::optional<double>, 8> latest;
    DWORD budget = 4096;
    while (available && budget) {
        char bytes[256]; DWORD count = 0;
        if (!ReadFile(input, bytes, std::min<DWORD>({available, DWORD(sizeof(bytes)), budget}), &count, nullptr) || !count)
            return true;
        pending.append(bytes, count); budget -= count;
        size_t newline = 0;
        while ((newline = pending.find('\n')) != std::string::npos) {
            const auto line = pending.substr(0, newline); pending.erase(0, newline + 1);
            if (line == "stop" || line == "stop\r") return true;
            if (const auto command = groupVolumeCommand(line, members.size())) latest[command->receiver] = command->volume;
        }
        if (pending.size() > 1024) return true;
        if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) return true;
    }
    for (size_t index = 0; index < members.size(); ++index)
        if (latest[index] && members[index]->ready) members[index]->sender->setVolume(*latest[index] * 100.0);
    return false;
}
void groupStats(GroupCapture& capture, const std::vector<std::unique_ptr<Member>>& members,
                BufferTimingTracker& timing, bool flowing, uint64_t paddedFrames,
                const GroupArguments& args, uint64_t timelineFrame, size_t conditionedFrames,
                double captureRatio
#if GLUSPOT_HAS_LOCAL_RENDERER
                , const LocalAudioRenderer* local
#endif
                ) {
    fxchain::RaopDiagnostics aggregate;
    aggregate.eventConnected = aggregate.eventKeyed = aggregate.coordinatedPlayback = aggregate.coordinatedTimelineArmed = true;
    aggregate.audioFramesSent = aggregate.nonzeroSamplesSent = aggregate.meaningfulSamplesSent = UINT64_MAX;
    for (const auto& member : members) {
        const auto d = member->sender->diagnostics();
        aggregate.feedbackSent += d.feedbackSent; aggregate.feedbackReplies += d.feedbackReplies;
        aggregate.feedbackRejected += d.feedbackRejected; aggregate.feedbackSkipped += d.feedbackSkipped;
        aggregate.eventRequests += d.eventRequests; aggregate.eventAcks += d.eventAcks;
        aggregate.eventDecryptFailures += d.eventDecryptFailures;
        aggregate.eventConnected &= d.eventConnected; aggregate.eventKeyed &= d.eventKeyed;
        aggregate.coordinatedTimelineArmed &= d.coordinatedTimelineArmed;
        aggregate.audioFramesSent = std::min(aggregate.audioFramesSent, d.audioFramesSent);
        aggregate.nonzeroSamplesSent = std::min(aggregate.nonzeroSamplesSent, d.nonzeroSamplesSent);
        aggregate.meaningfulSamplesSent = std::min(aggregate.meaningfulSamplesSent, d.meaningfulSamplesSent);
        aggregate.protocolLatencyFrames = d.protocolLatencyFrames;
        if (!d.lastError.empty()) aggregate.lastError = d.lastError;
    }
    const auto queued = capture.ring().availableRead() / 2 + conditionedFrames;
    const auto estimate = timing.sample({capture.frames.load(), aggregate.audioFramesSent, paddedFrames,
        capture.droppedSamples.load(), capture.discontinuities.load(), queued, 44100, aggregate.protocolLatencyFrames, flowing});
    const auto signal = capture.signal(true);
    std::cout << "{\"event\":\"stats\",\"captured_frames\":" << capture.frames.load()
              << ",\"dropped_samples\":" << capture.droppedSamples.load() << ",\"discontinuities\":" << capture.discontinuities.load()
              << ",\"protocol\":"; protocol(std::cout, aggregate, paddedFrames, queued);
    std::cout << ",\"timing\":{\"method\":\"protocol_plus_input_queue\",\"estimated_delay_ms\":" << estimate.delayMs()
              << ",\"protocol_buffer_ms\":" << estimate.protocolBufferMs << ",\"input_queue_ms\":" << estimate.inputQueueMs
              << ",\"valid\":" << (estimate.valid ? "true" : "false") << '}'
              << ",\"capture_signal\":{\"mode\":\"process_loopback\",\"scope\":\"music_process_tree\",\"nonzero_samples\":" << signal.nonzeroSamples
              << ",\"meaningful_samples\":" << signal.meaningfulSamples
              << ",\"last_nonzero_age_ms\":";
    if (signal.lastNonzeroAgeMs) std::cout << *signal.lastNonzeroAgeMs; else std::cout << "null";
    std::cout << ",\"last_meaningful_age_ms\":";
    if (signal.lastMeaningfulAgeMs) std::cout << *signal.lastMeaningfulAgeMs; else std::cout << "null";
    std::cout << ",\"peak\":" << signal.peak << "},\"group\":{\"method\":\"shared_pcm_rtp_ntp\",\"members\":" << members.size()
              << ",\"timeline_frame\":" << timelineFrame << ",\"music_pid\":" << args.musicPid
              << ",\"music_created_at\":" << args.musicCreatedAt << ",\"local_endpoint\":" << quoted(args.localEndpoint)
              << ",\"local_renderer\":" << (!args.localEndpoint.empty() ? "true" : "false")
              << ",\"capture_resample_ratio\":" << captureRatio
              << ",\"acoustic_alignment_verified\":false},\"members\":";
    memberStates(std::cout, members);
#if GLUSPOT_HAS_LOCAL_RENDERER
    if (local) {
        const auto d = local->diagnostics();
        std::cout << ",\"local_render\":{\"ready\":" << (d.ready ? "true" : "false")
                  << ",\"armed\":" << (d.armed ? "true" : "false") << ",\"playing\":" << (d.playing ? "true" : "false")
                  << ",\"endpoint_clock_valid\":" << (d.endpointClockValid ? "true" : "false")
                  << ",\"acoustic_delay_known\":" << (d.acousticDelayKnown ? "true" : "false")
                  << ",\"written_frames\":" << d.writtenFrames << ",\"nonzero_samples\":" << d.nonzeroSamples
                  << ",\"meaningful_samples\":" << d.meaningfulSamples << ",\"underrun_frames\":" << d.underrunFrames
                  << ",\"source_frame\":" << d.sourceFrame << ",\"device_clock_frames\":" << d.deviceClockFrames
                  << ",\"stream_latency_ms\":" << d.streamLatencyMs << ",\"buffer_duration_ms\":" << d.bufferDurationMs
                  << ",\"phase_error_ms\":" << d.phaseErrorMs << ",\"resample_ratio\":" << d.resampleRatio << '}';
    }
#endif
    std::cout << "}\n" << std::flush;
}
} // namespace

int runGroupHelper(int argc, wchar_t** argv) {
    GroupArguments args;
    try {
        std::vector<std::string> arguments;
        for (int index = 1; index < argc; ++index) arguments.push_back(utf8(argv[index]));
        args = parseGroupArguments(arguments);
    } catch (const std::exception& error) { status("error", error.what()); return 2; }
    groupInterrupted = false;
    if (!SetConsoleCtrlHandler(groupConsoleHandler, TRUE)) { status("error", "Cannot register group cancellation"); return 1; }
    struct ConsoleOwner { ~ConsoleOwner() { SetConsoleCtrlHandler(groupConsoleHandler, FALSE); } } consoleOwner;
    try {
        Apartment apartment;
        SourceEndpointGuard source(args.sourceEndpoint);
        EndpointMuteGuard mute(wide(args.sourceEndpoint));
        muteStatus("prepared", args, mute, false);
        GroupCapture capture(args.musicPid, args.musicCreatedAt);
        if (!capture.waitReady()) throw std::runtime_error(capture.error().empty() ? "Music capture initialization timed out" : capture.error());
        auto history = std::make_unique<GroupPcmHistory>();
#if GLUSPOT_HAS_LOCAL_RENDERER
        std::unique_ptr<LocalAudioRenderer> local;
        if (!args.localEndpoint.empty()) {
            local = std::make_unique<LocalAudioRenderer>(wide(args.localEndpoint),
                [&history](uint64_t frame, std::span<int16_t> output) { return history->pull(frame, output); });
            if (!local->waitReady()) throw std::runtime_error(local->error().empty() ? "Local group renderer initialization timed out" : local->error());
        }
#else
        if (!args.localEndpoint.empty()) throw std::runtime_error("Local synchronized renderer is not included in this build");
#endif
        bool failed = false, groupArmed = false, streaming = false;
        auto fail = [&](const std::string& message) {
            if (failed) return;
            failed = true; status("error", message);
        };
        std::vector<std::unique_ptr<Member>> members;
        for (const auto& receiver : args.receivers) {
            auto member = std::make_unique<Member>(); member->args = receiver;
            Member* sink = member.get();
            fxchain::RaopEvents events;
            events.launched = [sink, &fail](bool ok, const std::string& error) {
                if (!ok) { sink->state = "error"; fail(sink->args.name + ": " + error); }
                else {
                    sink->ready = true; sink->state = "waiting_audio";
                    sink->feedback.start(Clock::now());
                }
            };
            events.closed = [sink, &fail] { sink->state = "error"; fail(sink->args.name + ": receiver ended the synchronized group session"); };
            events.pinRequired = [sink, &fail](const std::string&) {
                sink->state = "error"; fail(sink->args.name + ": this build requires open HomePod transient pairing");
            };
            member->sender = std::make_unique<fxchain::RaopSender>(member->loop, std::move(events),
                [id = receiver.id](fxchain::RaopLogLevel, const std::string& line) { std::cerr << '[' << id << "] " << line << '\n'; });
            member->sender->setStrictReceiverAuth(true); member->sender->setPreserveReceiverVolume(true);
            member->sender->setCoordinatedPlayback(true);
            member->sender->setIdentity({"GluSpot", args.sourceId, "iPhone14,3"});
            member->sender->setAuth(fxchain::RaopDeviceInfo::Auth::HapTransient, true, receiver.deviceId, "", "");
            members.push_back(std::move(member));
        }
        std::string pending;
        if (groupInterrupted || stdinCommands(pending, members)) { status("stopped"); return 0; }
        status("connecting");
        for (auto& member : members) member->sender->start(member->args.host, member->args.port, member->args.name);
        const auto connectDeadline = Clock::now() + 20s;
        auto statsDeadline = Clock::now() + 500ms, guardDeadline = Clock::now(), volumeDeadline = Clock::now();
        Clock::time_point sendStart{};
        uint64_t nextFrame = 0;
        GroupPcmConditioner conditioner;
        BufferTimingTracker timing;
        while (!failed && !groupInterrupted && !stdinCommands(pending, members)) {
            for (auto& member : members) {
                member->loop.pump(*member->sender, 0ms);
                if (failed) break;
            }
            if (failed) break;
            const auto now = Clock::now();
            for (auto& member : members) {
                if (!member->ready) continue;
                const auto diagnostics = member->sender->diagnostics();
                if (!member->feedback.healthy(now, diagnostics.feedbackReplies, diagnostics.feedbackRejected)) {
                    member->state = "error";
                    fail(member->args.name + ": no successful receiver feedback for 10 seconds; synchronized group stopped");
                    break;
                }
            }
            if (failed) break;
            if (!capture.error().empty()) { fail(capture.error()); break; }
            if (!capture.sourceAlive()) { fail("Music process ended; synchronized group stopped"); break; }
            if (capture.droppedSamples.load()) { fail("Music capture overran; synchronized group stopped before source frames diverged"); break; }
#if GLUSPOT_HAS_LOCAL_RENDERER
            if (local && !local->error().empty()) { fail(local->error()); break; }
#endif
            if (now >= guardDeadline) {
                try { source.check(); }
                catch (const std::exception& error) { fail(error.what()); break; }
                if (groupArmed && !mute.isStillMuted()) { fail("Source output was unmuted externally; synchronized group stopped"); break; }
                guardDeadline = now + 250ms;
            }
            const bool allReady = std::all_of(members.begin(), members.end(), [](const auto& member) { return member->ready; });
            if (!groupArmed && !allReady && now >= connectDeadline) { fail("A group receiver timed out; check access and inbound UDP firewall rules"); break; }
            if (!groupArmed && allReady) {
                mute.mute(); muteStatus("muted", args, mute, true);
                // One startup lead: source frame zero enters capture now and
                // is transmitted 150 ms later, then plays at the protocol's
                // common presentation time. Queue evidence includes that lead.
                const auto anchorClock = Clock::now();
                const auto anchorNtp = ntpWallNow();
                sendStart = anchorClock + 150ms;
                const auto firstFrameNtp = anchorNtp + 150ULL * 0x100000000ULL / 1000;
                for (auto& member : members)
                    if (!member->sender->armCoordinatedTimeline(sendStart, firstFrameNtp)) {
                        fail("Cannot arm every receiver on the shared group timeline"); break;
                    }
                if (failed) break;
#if GLUSPOT_HAS_LOCAL_RENDERER
                if (local && !local->arm(sendStart, 66150)) { fail("Cannot arm local output on shared group timeline"); break; }
#endif
                capture.start(); groupArmed = true;
                status("waiting_audio", "Synchronized group connected; waiting for music-process audio");
            }
            if (groupArmed && now >= sendStart) {
                const auto ns = uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(now - sendStart).count());
                const uint64_t dueFrame = (ns / 1000000000ULL) * 44100 + (ns % 1000000000ULL) * 44100 / 1000000000ULL;
                int burst = 0;
                while (nextFrame <= dueFrame && burst++ < 8) {
                    std::array<int16_t, 704> pcm{};
                    const auto take = std::min<size_t>(44100 * 2, capture.ring().availableRead() & ~size_t(1));
                    if (take) {
                        std::vector<int16_t> captured(take);
                        if (!capture.ring().tryPop(captured) || !conditioner.feed(captured)) {
                            fail("Music source queue exceeded the bounded shared conditioner"); break;
                        }
                    }
                    conditioner.packet(pcm);
                    if (!history->append(nextFrame, pcm)) { fail("Shared PCM timeline rejected a source frame"); break; }
                    for (auto& member : members)
                        if (!member->sender->sendCoordinatedAudio(nextFrame, pcm)) {
                            fail(member->args.name + ": source packet missed shared presentation deadline"); break;
                        }
                    if (failed) break;
                    nextFrame += 352;
                }
            }
            if (failed) break;
            if (allReady && now >= volumeDeadline) {
                for (auto& member : members) member->sender->requestReceiverVolume();
                volumeDeadline = now + 5s;
            }
            bool revised = false;
            for (auto& member : members) {
                const auto revision = member->sender->diagnostics().receiverVolumeRevision;
                if (member->ready && revision != member->volumeRevision) { member->volumeRevision = revision; revised = true; }
            }
            if (revised) {
                std::cout << "{\"event\":\"receiver_volume\",\"members\":"; memberStates(std::cout, members);
                std::cout << "}\n" << std::flush;
            }
            const auto signal = capture.signal();
            bool localSignalReady = true;
#if GLUSPOT_HAS_LOCAL_RENDERER
            if (local) localSignalReady = local->diagnostics().meaningfulSamples > 0;
#endif
            if (groupArmed && localSignalReady && !streaming && std::all_of(members.begin(), members.end(), [&signal](const auto& member) {
                    return hasStreamingSignal(signal, member->sender->diagnostics().meaningfulSamplesSent);
                })) {
                streaming = true;
                for (auto& member : members) member->state = "streaming";
                status("streaming");
            }
            if (now >= statsDeadline) {
                groupStats(capture, members, timing, groupArmed, conditioner.paddedFrames(), args, nextFrame,
                           conditioner.queuedFrames(), conditioner.ratio()
#if GLUSPOT_HAS_LOCAL_RENDERER
                           , local.get()
#endif
                           );
                statsDeadline = now + 500ms;
            }
            std::this_thread::sleep_for(5ms);
        }
        groupStats(capture, members, timing, false, conditioner.paddedFrames(), args, nextFrame,
                   conditioner.queuedFrames(), conditioner.ratio()
#if GLUSPOT_HAS_LOCAL_RENDERER
                   , local.get()
#endif
                   );
        for (auto& member : members) { member->sender->stop(); member->state = "stopped"; }
#if GLUSPOT_HAS_LOCAL_RENDERER
        local.reset(); // No delayed music may continue after source mute is restored.
#endif
        try { mute.restore(); muteStatus("restored", args, mute, false); }
        catch (const std::exception& error) { fail(error.what()); }
        status("stopped"); return failed ? 1 : 0;
    } catch (const std::exception& error) { status("error", error.what()); status("stopped"); return 1; }
}
} // namespace gluspot
