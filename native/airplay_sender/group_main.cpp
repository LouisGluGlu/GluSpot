// GluSpot synchronized group host: one exact music-process capture, one final
// PCM timeline, independently authenticated AirPlay receivers and optional
// Windows render outputs. No receiver owns an independent capture/pacer.
#include "group_main.h"
#include "pcm_pipe_capture.h"
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
#include <future>
#include <map>
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
    GroupReceiverControls controls;
    bool committed = true, armed = false, retiring = false;
    uint64_t sourceBase = 0;
    std::string failure;
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
        << ",\"requested_min_latency_frames\":" << d.requestedMinLatencyFrames
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
bool stdinCommands(std::string& pending, std::vector<std::unique_ptr<Member>>& members,
                   const GroupArguments& args, std::optional<GroupMembershipCommand>& membership) {
    const HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (!input || input == INVALID_HANDLE_VALUE || GetFileType(input) != FILE_TYPE_PIPE) return false;
    DWORD available = 0;
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) return true;
    std::array<std::optional<double>, 8> latest;
    DWORD budget = 32768;
    while (available && budget) {
        char bytes[256]; DWORD count = 0;
        if (!ReadFile(input, bytes, std::min<DWORD>({available, DWORD(sizeof(bytes)), budget}), &count, nullptr) || !count)
            return true;
        pending.append(bytes, count); budget -= count;
        size_t newline = 0;
        while ((newline = pending.find('\n')) != std::string::npos) {
            auto line = pending.substr(0, newline); pending.erase(0, newline + 1);
            if (!line.empty() && line.back() == '\r') line.pop_back(); // Windows text-pipe CRLF is transport framing.
            if (line == "stop" || line == "stop\r") return true;
            if (!args.directPcm()) if (const auto command = groupVolumeCommand(line, members.size()))
                latest[command->receiver] = command->volume;
            if (const auto command = groupMembershipCommand(line, args)) membership = *command;
            if (args.directPcm()) if (const auto command = ownedGroupVolumeCommand(line, args.pcmEpoch))
                for (size_t index = 0; index < members.size(); ++index)
                    if (members[index]->args.id == command->receiver && members[index]->committed)
                        latest[index] = command->volume;
        }
        if (pending.size() > 131072) return true;
        if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) return true;
    }
    for (size_t index = 0; index < members.size(); ++index)
        if (latest[index] && members[index]->ready) members[index]->sender->setVolume(*latest[index] * 100.0);
    return false;
}
#if GLUSPOT_HAS_LOCAL_RENDERER
void localRenderStats(std::ostream& out, const LocalRenderDiagnostics& d, const std::string* endpoint = nullptr) {
    out << '{';
    if (endpoint) out << "\"endpoint_id\":" << quoted(*endpoint) << ',';
    out << "\"ready\":" << (d.ready ? "true" : "false")
        << ",\"armed\":" << (d.armed ? "true" : "false") << ",\"playing\":" << (d.playing ? "true" : "false")
        << ",\"endpoint_clock_valid\":" << (d.endpointClockValid ? "true" : "false")
        << ",\"acoustic_delay_known\":" << (d.acousticDelayKnown ? "true" : "false")
        << ",\"written_frames\":" << d.writtenFrames << ",\"nonzero_samples\":" << d.nonzeroSamples
        << ",\"meaningful_samples\":" << d.meaningfulSamples << ",\"underrun_frames\":" << d.underrunFrames
        << ",\"source_frame\":" << d.sourceFrame << ",\"device_clock_frames\":" << d.deviceClockFrames
        << ",\"presentation_latency_frames\":" << d.presentationLatencyFrames
        << ",\"clock_position\":" << d.clockPosition << ",\"clock_frequency\":" << d.clockFrequency
        << ",\"clock_qpc_100ns\":" << d.clockQpc100ns << ",\"clock_qpc_age_known\":" << (d.clockQpcAgeKnown ? "true" : "false")
        << ",\"clock_qpc_age_ms\":" << d.clockQpcAgeMs << ",\"padding_frames\":" << d.paddingFrames
        << ",\"write_estimate_written_frames\":" << d.writeEstimateWrittenFrames
        << ",\"write_queue_delay_ms\":" << d.writeQueueDelayMs
        << ",\"write_presentation_offset_ms\":" << d.writePresentationOffsetMs
        << ",\"write_presentation_epoch_offset_ms\":" << d.writePresentationEpochOffsetMs
        << ",\"stream_latency_ms\":" << d.streamLatencyMs << ",\"buffer_duration_ms\":" << d.bufferDurationMs
        << ",\"phase_error_ms\":" << d.phaseErrorMs << ",\"resample_ratio\":" << d.resampleRatio << '}';
}
#endif
void groupStats(GroupInput& capture, const std::vector<std::unique_ptr<Member>>& members,
                BufferTimingTracker& timing, bool flowing, uint64_t paddedFrames,
                const GroupArguments& args, uint64_t timelineFrame, size_t conditionedFrames,
                double captureRatio
#if GLUSPOT_HAS_LOCAL_RENDERER
                , const GroupLocalRendererSet<LocalAudioRenderer>& locals
                , const std::set<std::string>& stagedLocals
#endif
                ) {
    fxchain::RaopDiagnostics aggregate;
    aggregate.eventConnected = aggregate.eventKeyed = aggregate.coordinatedPlayback = aggregate.coordinatedTimelineArmed = true;
    aggregate.audioFramesSent = aggregate.nonzeroSamplesSent = aggregate.meaningfulSamplesSent = UINT64_MAX;
    size_t committedReceivers = 0;
    for (const auto& member : members) {
        if (!member->committed) continue;
        ++committedReceivers;
        const auto d = member->sender->diagnostics();
        aggregate.feedbackSent += d.feedbackSent; aggregate.feedbackReplies += d.feedbackReplies;
        aggregate.feedbackRejected += d.feedbackRejected; aggregate.feedbackSkipped += d.feedbackSkipped;
        aggregate.eventRequests += d.eventRequests; aggregate.eventAcks += d.eventAcks;
        aggregate.eventDecryptFailures += d.eventDecryptFailures;
        aggregate.eventConnected &= d.eventConnected; aggregate.eventKeyed &= d.eventKeyed;
        aggregate.coordinatedTimelineArmed &= d.coordinatedTimelineArmed;
        aggregate.audioFramesSent = std::min(aggregate.audioFramesSent, member->sourceBase + d.audioFramesSent);
        aggregate.nonzeroSamplesSent = std::min(aggregate.nonzeroSamplesSent, d.nonzeroSamplesSent);
        aggregate.meaningfulSamplesSent = std::min(aggregate.meaningfulSamplesSent, d.meaningfulSamplesSent);
        aggregate.protocolLatencyFrames = d.protocolLatencyFrames;
        aggregate.requestedMinLatencyFrames = d.requestedMinLatencyFrames;
        if (!d.lastError.empty()) aggregate.lastError = d.lastError;
    }
    uint64_t timingFrames = aggregate.audioFramesSent;
    uint32_t presentationFrames = aggregate.protocolLatencyFrames + aggregate.requestedMinLatencyFrames;
    if (!committedReceivers) {
        // Empty network membership is not UINT64_MAX transport success. The
        // local renderers own playback evidence, separately from network stats.
        aggregate.audioFramesSent = aggregate.nonzeroSamplesSent = aggregate.meaningfulSamplesSent = 0;
        aggregate.eventConnected = aggregate.eventKeyed = false;
        aggregate.protocolLatencyFrames = aggregate.requestedMinLatencyFrames = 0;
        aggregate.coordinatedTimelineArmed = false;
        presentationFrames = windowsGroupPresentationFrames;
#if GLUSPOT_HAS_LOCAL_RENDERER
        const auto progress = locals.progress(stagedLocals);
        timingFrames = progress.writtenFrames;
        aggregate.coordinatedTimelineArmed = progress.ready;
#else
        timingFrames = 0;
#endif
    }
    const auto queued = capture.ring().availableRead() / 2 + conditionedFrames;
#if GLUSPOT_HAS_LOCAL_RENDERER
    flowing = flowing && locals.hasMeaningfulPcm(stagedLocals);
#endif
    const auto estimate = timing.sample({capture.frames.load(), timingFrames, paddedFrames,
        capture.droppedSamples.load(), capture.discontinuities.load(), queued, 44100,
        presentationFrames, flowing});
    const auto signal = capture.signal(true);
    std::cout << "{\"event\":\"stats\",\"captured_frames\":" << capture.frames.load()
              << ",\"dropped_samples\":" << capture.droppedSamples.load() << ",\"discontinuities\":" << capture.discontinuities.load()
              << ",\"protocol\":"; protocol(std::cout, aggregate, paddedFrames, queued);
    std::cout << ",\"timing\":{\"method\":" << quoted(!committedReceivers ? "windows_shared_pcm_clock" : "protocol_plus_input_queue")
              << ",\"estimated_delay_ms\":" << estimate.delayMs()
              << (!committedReceivers ? ",\"presentation_buffer_ms\":" : ",\"protocol_buffer_ms\":") << estimate.protocolBufferMs
              << ",\"input_queue_ms\":" << estimate.inputQueueMs
              << ",\"rtp_buffer_ms\":" << 1000.0 * aggregate.protocolLatencyFrames / 44100
              << ",\"requested_min_latency_ms\":" << 1000.0 * aggregate.requestedMinLatencyFrames / 44100
              << ",\"valid\":" << (estimate.valid ? "true" : "false") << '}'
              << ",\"capture_signal\":{\"mode\":" << quoted(args.directPcm() ? "direct_pcm" : "process_loopback")
              << ",\"scope\":" << quoted(args.directPcm() ? "owned-player" : "music_process_tree")
              << ",\"nonzero_samples\":" << signal.nonzeroSamples
              << ",\"meaningful_samples\":" << signal.meaningfulSamples
              << ",\"last_nonzero_age_ms\":";
    if (signal.lastNonzeroAgeMs) std::cout << *signal.lastNonzeroAgeMs; else std::cout << "null";
    std::cout << ",\"last_meaningful_age_ms\":";
    if (signal.lastMeaningfulAgeMs) std::cout << *signal.lastMeaningfulAgeMs; else std::cout << "null";
    std::cout << ",\"peak\":" << signal.peak << "},\"group\":{\"method\":"
              << quoted(members.empty() ? "shared_pcm_local_clock" : "shared_pcm_rtp_ntp")
              << ",\"members\":" << members.size() + args.localEndpoints.size()
              << ",\"airplay_members\":" << members.size()
              << ",\"timeline_frame\":" << timelineFrame << ",\"music_pid\":" << args.musicPid
              << ",\"music_created_at\":" << args.musicCreatedAt;
    if (args.directPcm()) std::cout << ",\"pcm_epoch\":" << args.pcmEpoch;
    std::cout << ",\"local_endpoints\":[";
    for (size_t index = 0; index < args.localEndpoints.size(); ++index) {
        if (index) std::cout << ',';
        std::cout << quoted(args.localEndpoints[index]);
    }
    std::cout << ']';
    if (args.localEndpoints.size() == 1) std::cout << ",\"local_endpoint\":" << quoted(args.localEndpoints.front());
    std::cout << ",\"local_renderer\":" << (!args.localEndpoints.empty() ? "true" : "false")
              << ",\"capture_resample_ratio\":" << captureRatio
              << ",\"acoustic_alignment_verified\":false},\"members\":";
    memberStates(std::cout, members);
#if GLUSPOT_HAS_LOCAL_RENDERER
    std::cout << ",\"local_renders\":[";
    std::vector<LocalRenderDiagnostics> observations;
    observations.reserve(locals.outputs().size());
    for (const auto& output : locals.outputs()) {
        if (!observations.empty()) std::cout << ',';
        observations.push_back(output.renderer->diagnostics());
        localRenderStats(std::cout, observations.back(), &output.endpoint);
    }
    std::cout << ']';
    if (observations.size() == 1) {
        std::cout << ",\"local_render\":";
        localRenderStats(std::cout, observations.front());
    }
#else
    std::cout << ",\"local_renders\":[]";
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
        std::unique_ptr<SourceEndpointGuard> source;
        std::unique_ptr<EndpointMuteGuard> mute;
        if (!args.directPcm()) {
            source = std::make_unique<SourceEndpointGuard>(args.sourceEndpoint);
            mute = std::make_unique<EndpointMuteGuard>(wide(args.sourceEndpoint));
            muteStatus("prepared", args, *mute, false);
        }
        GroupInput capture(args);
        if (!capture.waitReady()) throw std::runtime_error(capture.error().empty() ? "Music capture initialization timed out" : capture.error());
        auto history = std::make_shared<GroupPcmHistory>();
#if GLUSPOT_HAS_LOCAL_RENDERER
        GroupLocalRendererSet<LocalAudioRenderer> locals(args.localEndpoints, [history](const auto& endpoint) {
            return std::make_unique<LocalAudioRenderer>(wide(endpoint),
                [history](uint64_t frame, std::span<int16_t> output) { return history->pull(frame, output); });
        });
        if (const auto error = locals.prepare(); !error.empty()) throw std::runtime_error(error);
#else
        if (!args.localEndpoints.empty()) throw std::runtime_error("Local synchronized renderer is not included in this build");
#endif
        bool failed = false, groupArmed = false, streaming = false;
        auto fail = [&](const std::string& message) {
            if (failed) return;
            failed = true;
#if GLUSPOT_HAS_LOCAL_RENDERER
            locals.stop();
#endif
            status("error", message);
        };
        std::vector<std::unique_ptr<Member>> members;
        auto makeMember = [&](const GroupReceiverArgument& receiver, bool committed) {
            auto member = std::make_unique<Member>(); member->args = receiver;
            member->committed = committed;
            Member* sink = member.get();
            fxchain::RaopEvents events;
            events.launched = [sink](bool ok, const std::string& error) {
                if (sink->retiring) return;
                if (!ok) { sink->state = "error"; sink->failure = error; }
                else {
                    sink->ready = true; sink->state = "waiting_audio";
                    sink->feedback.start(Clock::now());
                }
            };
            events.closed = [sink] {
                if (!sink->retiring) { sink->state = "error"; sink->failure = "Receiver ended the synchronized session"; }
            };
            events.pinRequired = [sink](const std::string&) {
                if (!sink->retiring) { sink->state = "error"; sink->failure = "Receiver requires interactive pairing"; }
            };
            events.receiverPlayPause = [&, sink](const std::string& cseq) {
                if (args.directPcm() && groupArmed && !failed && sink->committed && sink->ready && !sink->retiring)
                    sink->controls.push(cseq, Clock::now());
            };
            member->sender = std::make_unique<fxchain::RaopSender>(member->loop, std::move(events),
                [id = receiver.id](fxchain::RaopLogLevel, const std::string& line) { std::cerr << '[' << id << "] " << line << '\n'; });
            member->sender->setStrictReceiverAuth(true); member->sender->setPreserveReceiverVolume(true);
            member->sender->setCoordinatedPlayback(true);
            member->sender->setIdentity({"GluSpot", args.sourceId, "iPhone14,3"});
            member->sender->setAuth(fxchain::RaopDeviceInfo::Auth::HapTransient, true, receiver.deviceId, "", "");
            return member;
        };
        for (const auto& receiver : args.receivers) members.push_back(makeMember(receiver, true));
        std::string pending;
        std::optional<GroupMembershipCommand> membershipCommand;
        if (groupInterrupted || stdinCommands(pending, members, args, membershipCommand)) { status("stopped"); return 0; }
        if (args.directPcm()) std::cout << "{\"event\":\"capabilities\",\"pcm_epoch\":" << args.pcmEpoch
            << ",\"reconfigure_pcm\":1,\"owned_presentation\":1}\n" << std::flush;
        status("connecting");
        for (auto& member : members) member->sender->start(member->args.host, member->args.port, member->args.name);
        const auto connectDeadline = Clock::now() + 20s;
        auto statsDeadline = Clock::now() + 500ms, guardDeadline = Clock::now(), volumeDeadline = Clock::now();
        Clock::time_point sendStart{};
        uint64_t anchorNtp = 0;
        uint64_t nextFrame = 0;
        uint32_t presentationFrames = windowsGroupPresentationFrames;
        uint32_t rtpFrames = 66150;
        uint64_t membershipRevision = 0;
        std::optional<GroupMembershipCommand> staged;
        Clock::time_point stagedDeadline{};
        std::set<std::string> stagedLocals;
        struct JoiningLocal { std::shared_ptr<uint64_t> base; bool armed = false; };
        std::map<std::string, JoiningLocal> joiningLocals;
        struct RetiredTransport { std::string id, target; std::future<void> cleanup; };
        std::vector<RetiredTransport> retired;
        auto retireMember = [&](std::unique_ptr<Member> member) {
            member->retiring = true;
            const auto id = member->args.id, target = member->args.host + ':' + std::to_string(member->args.port);
            retired.push_back({id, target, std::async(std::launch::async,
                [member = std::move(member)] { member->sender->stop(); })});
        };
#if GLUSPOT_HAS_LOCAL_RENDERER
        auto retireLocal = [&](const std::string& endpoint) {
            auto renderer = locals.remove(endpoint); // Only signal here; never join on the PCM packet thread.
            if (renderer) retired.push_back({endpoint, "", std::async(std::launch::async,
                [renderer = std::move(renderer)]() mutable { renderer.reset(); })});
        };
#endif
        auto syncArguments = [&] {
            args.receivers.clear();
            for (const auto& member : members) args.receivers.push_back(member->args);
#if GLUSPOT_HAS_LOCAL_RENDERER
            args.localEndpoints.clear();
            for (const auto& output : locals.outputs()) args.localEndpoints.push_back(output.endpoint);
#endif
        };
        auto membershipEvent = [&](const char* state, uint64_t revision) {
            std::cout << "{\"event\":\"membership\",\"pcm_epoch\":" << args.pcmEpoch
                << ",\"revision\":" << revision << ",\"state\":" << quoted(state) << ",\"confirmed_ids\":[";
            bool comma = false;
            for (const auto& member : members) if (member->committed) {
                if (comma) std::cout << ','; comma = true; std::cout << quoted(member->args.id);
            }
#if GLUSPOT_HAS_LOCAL_RENDERER
            for (const auto& output : locals.outputs()) if (!stagedLocals.count(output.endpoint)) {
                if (comma) std::cout << ','; comma = true; std::cout << quoted(output.endpoint);
            }
#endif
            std::cout << "],\"pending_ids\":["; comma = false;
            for (const auto& member : members) if (!member->committed) {
                if (comma) std::cout << ','; comma = true; std::cout << quoted(member->args.id);
            }
            for (const auto& endpoint : stagedLocals) {
                if (comma) std::cout << ','; comma = true; std::cout << quoted(endpoint);
            }
            std::cout << "]}\n" << std::flush;
        };
        auto rollbackMembership = [&] {
            if (!staged) return;
            const auto revision = staged->revision;
            for (auto item = members.begin(); item != members.end();) {
                if (!(*item)->committed) { retireMember(std::move(*item)); item = members.erase(item); }
                else ++item;
            }
#if GLUSPOT_HAS_LOCAL_RENDERER
            for (const auto& endpoint : stagedLocals) retireLocal(endpoint);
#endif
            stagedLocals.clear(); joiningLocals.clear(); staged.reset(); syncArguments();
            membershipEvent("rejected", revision);
        };
        GroupPcmConditioner conditioner;
        BufferTimingTracker timing;
        while (!failed && !groupInterrupted && !stdinCommands(pending, members, args, membershipCommand)) {
            retired.erase(std::remove_if(retired.begin(), retired.end(), [](auto& transport) {
                return transport.cleanup.wait_for(0ms) == std::future_status::ready;
            }), retired.end());
            if (membershipCommand) {
                const auto command = *membershipCommand; membershipCommand.reset();
                if (command.revision > membershipRevision) {
                    rollbackMembership(); membershipRevision = command.revision;
                    bool valid = groupArmed;
                    std::set<std::string> unionIds;
                    std::set<std::string> physicalReceivers;
                    for (const auto& member : members) unionIds.insert(member->args.id);
                    for (const auto& member : members) physicalReceivers.insert(member->args.host + ':' + std::to_string(member->args.port));
                    for (const auto& endpoint : args.localEndpoints) unionIds.insert(endpoint);
                    for (const auto& receiver : command.outputs.receivers) {
                        const bool retained = unionIds.count(receiver.id);
                        unionIds.insert(receiver.id);
                        if (!retained) valid &= physicalReceivers.insert(receiver.host + ':' + std::to_string(receiver.port)).second;
                        for (const auto& member : members) if (member->args.id == receiver.id)
                            valid &= member->args.host == receiver.host && member->args.port == receiver.port &&
                                member->args.deviceId == receiver.deviceId;
                        for (const auto& endpoint : args.localEndpoints) valid &= endpoint != receiver.id;
                    }
                    for (const auto& endpoint : command.outputs.localEndpoints) {
                        unionIds.insert(endpoint);
                        for (const auto& member : members) valid &= member->args.id != endpoint;
                    }
                    valid &= unionIds.size() + retired.size() <= 8;
                    for (const auto& transport : retired) {
                        for (const auto& receiver : command.outputs.receivers)
                            valid &= transport.id != receiver.id && transport.target != receiver.host + ':' + std::to_string(receiver.port);
                        for (const auto& endpoint : command.outputs.localEndpoints)
                            valid &= !groupSameEndpoint(transport.id, endpoint);
                    }
                    if (!valid) membershipEvent("rejected", command.revision);
                    else {
                        staged = command; stagedDeadline = Clock::now() + 20s;
                        try {
                            for (const auto& receiver : command.outputs.receivers) {
                                if (std::any_of(members.begin(), members.end(), [&receiver](const auto& member) {
                                        return member->args.id == receiver.id; })) continue;
                                auto member = makeMember(receiver, false);
                                member->sender->start(receiver.host, receiver.port, receiver.name);
                                members.push_back(std::move(member));
                            }
#if GLUSPOT_HAS_LOCAL_RENDERER
                            for (const auto& endpoint : command.outputs.localEndpoints) {
                                if (std::find(args.localEndpoints.begin(), args.localEndpoints.end(), endpoint) != args.localEndpoints.end()) continue;
                                auto base = std::make_shared<uint64_t>(0);
                                auto renderer = std::make_unique<LocalAudioRenderer>(wide(endpoint),
                                    [history, base](uint64_t frame, std::span<int16_t> output) {
                                        return frame <= UINT64_MAX - *base && history->pull(*base + frame, output);
                                    });
                                if (!locals.add(endpoint, std::move(renderer), base)) throw std::runtime_error("Cannot add output");
                                stagedLocals.insert(endpoint); joiningLocals.emplace(endpoint, JoiningLocal{base});
                            }
#endif
                            syncArguments(); membershipEvent("pending", command.revision);
                        } catch (...) { rollbackMembership(); }
                    }
                }
            }
            for (auto& member : members) {
                member->loop.pump(*member->sender, 0ms);
                if (failed) break;
            }
            bool joinFailed = false;
            for (const auto& member : members) if (!member->failure.empty()) {
                if (member->committed) fail(member->args.name + ": " + member->failure);
                else joinFailed = true;
            }
            if (joinFailed) rollbackMembership();
            if (failed) break;
            const auto now = Clock::now();
            for (auto& member : members) {
                if (!member->ready) continue;
                const auto diagnostics = member->sender->diagnostics();
                if (!member->feedback.healthy(now, diagnostics.feedbackReplies, diagnostics.feedbackRejected)) {
                    member->state = "error";
                    if (member->committed) fail(member->args.name + ": no successful receiver feedback for 10 seconds; synchronized group stopped");
                    else joinFailed = true;
                }
            }
            if (joinFailed) rollbackMembership();
            if (failed) break;
            if (!capture.error().empty()) { fail(capture.error()); break; }
            if (!capture.sourceAlive()) { fail("Music process ended; synchronized group stopped"); break; }
            if (capture.droppedSamples.load()) { fail("Music capture overran; synchronized group stopped before source frames diverged"); break; }
            for (auto& member : members) {
                const bool eligible = args.directPcm() && groupArmed && !staged && member->committed && member->ready && !member->retiring;
                for (const auto& [cseq, age] : member->controls.take(eligible, now))
                    std::cout << "{\"event\":\"receiver_play_pause\",\"pcm_epoch\":" << args.pcmEpoch
                        << ",\"receiver_id\":" << quoted(member->args.id) << ",\"revision\":" << membershipRevision
                        << ",\"cseq\":" << quoted(cseq)
                        << ",\"age_ms\":" << age << "}\n" << std::flush;
            }
#if GLUSPOT_HAS_LOCAL_RENDERER
            for (const auto& output : locals.outputs()) if (const auto error = output.renderer->error(); !error.empty()) {
                if (stagedLocals.count(output.endpoint)) joinFailed = true;
                else fail(output.endpoint + ": " + error);
            }
            if (joinFailed) rollbackMembership();
            if (failed) break;
#endif
            if (now >= guardDeadline) {
                try { if (source) source->check(); }
                catch (const std::exception& error) { fail(error.what()); break; }
                if (groupArmed && mute && !mute->isStillMuted()) { fail("Source output was unmuted externally; synchronized group stopped"); break; }
                guardDeadline = now + 250ms;
            }
            const bool allReady = std::all_of(members.begin(), members.end(), [](const auto& member) { return member->ready; });
            if (!groupArmed && !allReady && now >= connectDeadline) { fail("A group receiver timed out; check access and inbound UDP firewall rules"); break; }
            if (!groupArmed && allReady) {
                if (mute) { mute->mute(); muteStatus("muted", args, *mute, true); }
                // One startup lead: source frame zero enters capture now and
                // is transmitted 150 ms later, then plays at the protocol's
                // common presentation time. The AP2 requested render lead
                // follows the RTP buffer; local playback must include both.
                // Queue evidence also includes the capture startup lead.
                const auto anchorClock = Clock::now();
                anchorNtp = ntpWallNow();
                sendStart = anchorClock + 150ms;
                const auto firstFrameNtp = anchorNtp + 150ULL * 0x100000000ULL / 1000;
                for (auto& member : members)
                    if (!member->sender->armCoordinatedTimeline(sendStart, firstFrameNtp)) {
                        fail("Cannot arm every receiver on the shared group timeline"); break;
                    } else member->armed = true;
                if (failed) break;
                anchorNtp = firstFrameNtp;
                if (!members.empty()) {
                    const auto policy = members.front()->sender->diagnostics();
                    rtpFrames = policy.protocolLatencyFrames;
                    presentationFrames = groupPresentationFrames(policy.protocolLatencyFrames, policy.requestedMinLatencyFrames);
                    if (!std::all_of(members.begin(), members.end(), [&policy](const auto& member) {
                        const auto d = member->sender->diagnostics();
                        return d.protocolLatencyFrames == policy.protocolLatencyFrames &&
                               d.requestedMinLatencyFrames == policy.requestedMinLatencyFrames;
                    })) { fail("Receivers do not share the same presentation latency policy"); break; }
                }
#if GLUSPOT_HAS_LOCAL_RENDERER
                if (const auto error = locals.arm(sendStart, presentationFrames); !error.empty()) { fail(error); break; }
#endif
                capture.start(); groupArmed = true;
                status("waiting_audio", args.directPcm() ? "Synchronized group connected; waiting for program-player PCM" :
                    "Synchronized group connected; waiting for music-process audio");
            }
            if (staged) {
                for (auto& member : members) if (!member->committed && member->ready && !member->armed) {
                    const auto policy = member->sender->diagnostics();
                    if (policy.protocolLatencyFrames != rtpFrames ||
                        groupPresentationFrames(policy.protocolLatencyFrames, policy.requestedMinLatencyFrames) != presentationFrames) {
                        joinFailed = true; break;
                    }
                    member->sourceBase = groupJoinFrame(nextFrame);
                    member->armed = member->sender->armCoordinatedTimeline(
                        sendStart + groupFrameTime(member->sourceBase),
                        anchorNtp + groupFrameNtp(member->sourceBase), rtpFrames);
                    if (!member->armed) { joinFailed = true; break; }
                }
#if GLUSPOT_HAS_LOCAL_RENDERER
                for (const auto& output : locals.outputs()) {
                    const auto item = joiningLocals.find(output.endpoint);
                    if (item == joiningLocals.end() || item->second.armed || !output.renderer->diagnostics().ready) continue;
                    *item->second.base = groupJoinFrame(nextFrame);
                    item->second.armed = output.renderer->arm(
                        sendStart + groupFrameTime(*item->second.base), presentationFrames);
                    if (!item->second.armed) { joinFailed = true; break; }
                }
#endif
                if (now >= stagedDeadline || joinFailed) rollbackMembership();
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
                        if (member->armed && nextFrame >= member->sourceBase &&
                            !member->sender->sendCoordinatedAudio(nextFrame - member->sourceBase, pcm)) {
                            if (member->committed) fail(member->args.name + ": source packet missed shared presentation deadline");
                            else joinFailed = true;
                            if (failed) break;
                        }
                    if (failed) break;
                    nextFrame += 352;
                }
            }
            if (joinFailed) rollbackMembership();
            if (staged) {
                bool joined = true;
                for (const auto& member : members) if (!member->committed)
                    joined &= member->armed && member->sender->diagnostics().meaningfulSamplesSent > 0 &&
                        now >= sendStart + groupFrameTime(member->sourceBase + presentationFrames);
#if GLUSPOT_HAS_LOCAL_RENDERER
                for (const auto& output : locals.outputs()) if (stagedLocals.count(output.endpoint)) {
                    const auto& item = joiningLocals.at(output.endpoint);
                    joined &= item.armed && output.renderer->diagnostics().meaningfulSamples > 0 &&
                        now >= sendStart + groupFrameTime(*item.base + presentationFrames);
                }
#endif
                if (joined) {
                    const auto revision = staged->revision;
                    for (auto item = members.begin(); item != members.end();) {
                        if (std::none_of(staged->outputs.receivers.begin(), staged->outputs.receivers.end(),
                                [&item](const auto& receiver) { return receiver.id == (*item)->args.id; })) {
                            retireMember(std::move(*item)); item = members.erase(item);
                        } else { (*item)->committed = true; (*item)->state = "streaming"; ++item; }
                    }
#if GLUSPOT_HAS_LOCAL_RENDERER
                    std::vector<std::string> removed;
                    for (const auto& output : locals.outputs())
                        if (std::find(staged->outputs.localEndpoints.begin(), staged->outputs.localEndpoints.end(), output.endpoint)
                                == staged->outputs.localEndpoints.end()) removed.push_back(output.endpoint);
                    for (const auto& endpoint : removed) retireLocal(endpoint);
#endif
                    staged.reset(); stagedLocals.clear(); joiningLocals.clear(); syncArguments();
                    membershipEvent("applied", revision);
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
            localSignalReady = locals.hasMeaningfulPcm();
#endif
            bool sourceSignalReady = !members.empty();
#if GLUSPOT_HAS_LOCAL_RENDERER
            if (members.empty()) sourceSignalReady = hasStreamingSignal(signal, locals.progress().meaningfulSamples);
#endif
            const bool presentationDue = !args.directPcm() || now >= sendStart + groupFrameTime(presentationFrames);
            if (groupArmed && presentationDue && sourceSignalReady && localSignalReady && !streaming && std::all_of(members.begin(), members.end(), [&signal](const auto& member) {
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
                           , locals, stagedLocals
#endif
                           );
                statsDeadline = now + 500ms;
            }
            std::this_thread::sleep_for(5ms);
        }
        groupStats(capture, members, timing, false, conditioner.paddedFrames(), args, nextFrame,
                   conditioner.queuedFrames(), conditioner.ratio()
#if GLUSPOT_HAS_LOCAL_RENDERER
                   , locals, stagedLocals
#endif
                   );
#if GLUSPOT_HAS_LOCAL_RENDERER
        locals.stop(); // Fan out before potentially slow receiver teardown.
#endif
        for (auto& member : members) { member->sender->stop(); member->state = "stopped"; }
#if GLUSPOT_HAS_LOCAL_RENDERER
        locals.clear(); // Stop every delayed output before source mute is restored.
#endif
        try { if (mute) { mute->restore(); muteStatus("restored", args, *mute, false); } }
        catch (const std::exception& error) { fail(error.what()); }
        status("stopped"); return failed ? 1 : 0;
    } catch (const std::exception& error) { status("error", error.what()); status("stopped"); return 1; }
}
} // namespace gluspot
