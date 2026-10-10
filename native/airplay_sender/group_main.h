#pragma once
#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <deque>
#include <memory>
#include <optional>
#include <regex>
#include <set>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

namespace gluspot {
int runGroupHelper(int argc, wchar_t** argv);

// AP2 realtime/NTP keeps the RTP buffer and the requested receiver render
// lead separate on the wire. Local playback needs both once (OwnTone's
// network buffer B-250ms vs local B policy). This is protocol evidence,
// never a receiver-reported acoustic delay. Do not add sync flag 7 again.
inline uint32_t groupPresentationFrames(uint32_t rtpBuffer, uint32_t requestedMin) {
    if (rtpBuffer < 11025 || rtpBuffer > 88200 || requestedMin != 11025)
        throw std::invalid_argument("Unsupported group presentation latency policy");
    return rtpBuffer + requestedMin;
}

// Windows-only playback retains the established shared-history reserve. A
// 250 ms reserve is insufficient for the supported one-second WASAPI queue;
// this 1.75 s schedule is not a measured Bluetooth or acoustic correction.
inline constexpr uint32_t windowsGroupPresentationFrames = 77175;

// A setup-ready receiver must keep acknowledging authenticated /feedback,
// including while another member is still pairing. Local audio handoff,
// feedback requests/skips, encrypted events and rejected replies are not proof
// that this receiver still accepts the session. The failure is terminal for
// the current group; a late acknowledgement cannot resurrect its timeline.
class GroupFeedbackWatchdog {
public:
    using Clock = std::chrono::steady_clock;
    static constexpr auto timeout = std::chrono::seconds(10);
    void start(Clock::time_point now) {
        started_ = true; failed_ = false; successes_ = 0; lastSuccess_ = now;
    }
    bool healthy(Clock::time_point now, uint64_t replies, uint64_t rejected) {
        if (failed_) return false;
        if (!started_) return true;
        if (rejected > replies || replies - rejected < successes_ || now < lastSuccess_) {
            failed_ = true; return false;
        }
        const auto successful = replies - rejected;
        if (successful > successes_) {
            successes_ = successful; lastSuccess_ = now;
        }
        if (now - lastSuccess_ >= timeout) failed_ = true;
        return !failed_;
    }
private:
    bool started_ = false, failed_ = false;
    uint64_t successes_ = 0;
    Clock::time_point lastSuccess_{};
};

struct GroupReceiverArgument {
    std::string id, host, deviceId, name;
    uint16_t port = 0;
};
struct GroupArguments {
    uint32_t musicPid = 0;
    uint64_t musicCreatedAt = 0;
    std::string sourceEndpoint;
    std::vector<std::string> localEndpoints;
    std::string sourceId = "02:47:4C:55:53:50";
    std::vector<GroupReceiverArgument> receivers;
    std::string pcmPipe;
    uint32_t pcmParentPid = 0;
    uint64_t pcmParentCreatedAt = 0, pcmEpoch = 0;
    bool directPcm() const { return !pcmPipe.empty(); }
};
inline uint64_t groupUnsigned(const std::string& value, uint64_t maximum, const char* field) {
    uint64_t result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    if (value.empty() || parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() ||
        !result || result > maximum)
        throw std::invalid_argument(std::string("Invalid group ") + field);
    return result;
}
inline void groupText(const std::string& text, size_t maximum, const char* field) {
    if (text.empty() || text.size() > maximum ||
        std::any_of(text.begin(), text.end(), [](unsigned char c) { return c < 32 || c == 127; }))
        throw std::invalid_argument(std::string("Invalid group ") + field);
}
inline bool groupIpv4(const std::string& text) {
    unsigned parts[4]{};
    size_t cursor = 0;
    for (size_t part = 0; part < 4; ++part) {
        const size_t end = text.find('.', cursor);
        const size_t last = end == std::string::npos ? text.size() : end;
        if ((part < 3) != (end != std::string::npos) || last == cursor || last - cursor > 3 ||
            (last - cursor > 1 && text[cursor] == '0')) return false;
        const auto parsed = std::from_chars(text.data() + cursor, text.data() + last, parts[part]);
        if (parsed.ec != std::errc{} || parsed.ptr != text.data() + last || parts[part] > 255) return false;
        cursor = last + 1;
    }
    return (parts[0] || parts[1] || parts[2] || parts[3]) && parts[0] < 224;
}
inline bool groupSameEndpoint(const std::string& a, const std::string& b) {
    return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin(), [](unsigned char x, unsigned char y) {
        auto fold = [](unsigned char c) { return c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : int(c); };
        return fold(x) == fold(y);
    });
}
// Pure command-line validation precedes Windows identity/mute/network work.
// --receiver-id begins a member and each of its four fields is explicit.
inline GroupArguments parseGroupArguments(const std::vector<std::string>& arguments) {
    GroupArguments result;
    std::set<std::string> global, memberFields, ids, endpoints;
    auto finishMember = [&] {
        if (result.receivers.empty()) return;
        const auto& member = result.receivers.back();
        if (memberFields != std::set<std::string>{"--host", "--port", "--device-id", "--name"} ||
            !groupIpv4(member.host)) throw std::invalid_argument("Each HomePod requires unique ID, IPv4 host, port, device ID and name");
        groupText(member.id, 2048, "receiver ID");
        groupText(member.deviceId, 1024, "device ID");
        groupText(member.name, 1024, "receiver name");
        if (!ids.insert(member.id).second || !endpoints.insert(member.host + ':' + std::to_string(member.port)).second)
            throw std::invalid_argument("Duplicate HomePod group member");
    };
    for (size_t index = 0; index < arguments.size(); ++index) {
        const auto& key = arguments[index];
        if (key == "--group") {
            if (!global.insert(key).second) throw std::invalid_argument("Duplicate --group argument");
            continue;
        }
        if (index + 1 == arguments.size()) throw std::invalid_argument("Missing group option value");
        const auto& value = arguments[++index];
        if (key == "--receiver-id") {
            finishMember(); memberFields.clear();
            if (result.receivers.size() == 8) throw std::invalid_argument("At most eight group outputs are supported");
            result.receivers.push_back({value, {}, {}, {}, 0});
        } else if (key == "--host" || key == "--port" || key == "--device-id" || key == "--name") {
            if (result.receivers.empty() || !memberFields.insert(key).second)
                throw std::invalid_argument("Invalid or duplicate HomePod member option");
            auto& member = result.receivers.back();
            if (key == "--host") member.host = value;
            else if (key == "--port") member.port = uint16_t(groupUnsigned(value, 65535, "receiver port"));
            else if (key == "--device-id") member.deviceId = value;
            else member.name = value;
        } else if (key == "--local-endpoint") {
            groupText(value, 2048, "local output endpoint");
            if (result.localEndpoints.size() == 8)
                throw std::invalid_argument("At most eight group outputs are supported");
            result.localEndpoints.push_back(value);
        } else {
            if (!global.insert(key).second) throw std::invalid_argument("Duplicate group option");
            if (key == "--music-pid") result.musicPid = uint32_t(groupUnsigned(value, 0xffffffffULL, "music PID"));
            else if (key == "--music-created-at") result.musicCreatedAt = groupUnsigned(value, UINT64_MAX, "music creation token");
            else if (key == "--source-endpoint") result.sourceEndpoint = value;
            else if (key == "--source-id") result.sourceId = value;
            else if (key == "--pcm-pipe") result.pcmPipe = value;
            else if (key == "--pcm-parent-pid") result.pcmParentPid = uint32_t(groupUnsigned(value, 0xffffffffULL, "PCM parent PID"));
            else if (key == "--pcm-parent-created-at") result.pcmParentCreatedAt = groupUnsigned(value, UINT64_MAX, "PCM parent creation token");
            else if (key == "--pcm-epoch") result.pcmEpoch = groupUnsigned(value, UINT64_MAX, "PCM epoch");
            else throw std::invalid_argument("Unknown group option: " + key);
        }
    }
    finishMember();
    if (!global.count("--group")) throw std::invalid_argument("Missing group mode");
    if (result.directPcm()) {
        static const std::regex pipeName("^GluSpotPCM-[0-9a-f]{32}$");
        if (!std::regex_match(result.pcmPipe, pipeName) || !result.pcmParentPid ||
            !result.pcmParentCreatedAt || !result.pcmEpoch || global.count("--music-pid") ||
            global.count("--music-created-at") || global.count("--source-endpoint") ||
            (result.receivers.empty() && result.localEndpoints.empty()))
            throw std::invalid_argument("Direct PCM requires an authenticated parent, epoch and output, without process capture options");
    } else {
        if (global.count("--pcm-pipe") || global.count("--pcm-parent-pid") || global.count("--pcm-parent-created-at") || global.count("--pcm-epoch") ||
            !result.musicPid || !result.musicCreatedAt || (result.receivers.empty() && result.localEndpoints.size() < 2))
            throw std::invalid_argument("Group requires a verified music process, source endpoint and two outputs or AirPlay");
        groupText(result.sourceEndpoint, 2048, "source endpoint");
    }
    if (result.receivers.size() + result.localEndpoints.size() > 8)
        throw std::invalid_argument("At most eight group outputs are supported");
    for (size_t index = 0; index < result.localEndpoints.size(); ++index) {
        const auto& endpoint = result.localEndpoints[index];
        if ((!result.directPcm() && groupSameEndpoint(endpoint, result.sourceEndpoint)) ||
            std::any_of(result.localEndpoints.begin(), result.localEndpoints.begin() + index,
                [&endpoint](const auto& previous) { return groupSameEndpoint(endpoint, previous); }))
            throw std::invalid_argument("Local group outputs must be unique and differ from muted source");
    }
    static const std::regex identity("^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$");
    if (!std::regex_match(result.sourceId, identity)) throw std::invalid_argument("Invalid group source identity");
    return result;
}

// An owned helper accepts only the parent's canonical, versioned grammar.
// Output arguments are UTF-8 bytes encoded as hex and separated by NUL;
// the established CLI validator remains the authority for every member.
struct GroupMembershipCommand {
    uint64_t epoch = 0, revision = 0;
    GroupArguments outputs;
};
inline std::optional<GroupMembershipCommand> groupMembershipCommand(
        const std::string& line, const GroupArguments& session) {
    if (!session.directPcm() || line.size() > 131072) return std::nullopt;
    const std::string prefix = "{\"command\":\"reconfigure_pcm\",\"epoch\":";
    const std::string middle = ",\"revision\":";
    const std::string tail = ",\"outputs_hex\":\"";
    if (!line.starts_with(prefix) || !line.ends_with("\"}")) return std::nullopt;
    const auto revisionAt = line.find(middle, prefix.size());
    const auto outputsAt = revisionAt == std::string::npos ? revisionAt : line.find(tail, revisionAt + middle.size());
    if (outputsAt == std::string::npos) return std::nullopt;
    try {
        GroupMembershipCommand result;
        const auto epochText = line.substr(prefix.size(), revisionAt - prefix.size());
        const auto revisionText = line.substr(revisionAt + middle.size(), outputsAt - revisionAt - middle.size());
        if (epochText.empty() || revisionText.empty() || epochText.front() == '0' || revisionText.front() == '0') return std::nullopt;
        result.epoch = groupUnsigned(epochText, UINT64_MAX, "membership epoch");
        result.revision = groupUnsigned(revisionText, UINT64_MAX, "membership revision");
        if (result.epoch != session.pcmEpoch) return std::nullopt;
        const auto encoded = line.substr(outputsAt + tail.size(), line.size() - outputsAt - tail.size() - 2);
        if (encoded.empty() || encoded.size() % 2 || std::any_of(encoded.begin(), encoded.end(), [](char c) {
                return !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f'); })) return std::nullopt;
        std::string bytes; bytes.reserve(encoded.size() / 2);
        auto digit = [](char c) { return c <= '9' ? c - '0' : c - 'a' + 10; };
        for (size_t index = 0; index < encoded.size(); index += 2)
            bytes += char(digit(encoded[index]) * 16 + digit(encoded[index + 1]));
        std::vector<std::string> arguments{"--group", "--pcm-pipe", session.pcmPipe,
            "--pcm-parent-pid", std::to_string(session.pcmParentPid),
            "--pcm-parent-created-at", std::to_string(session.pcmParentCreatedAt),
            "--pcm-epoch", std::to_string(session.pcmEpoch), "--source-id", session.sourceId};
        size_t cursor = 0, count = 0;
        while (cursor < bytes.size()) {
            const auto end = bytes.find('\0', cursor);
            const auto part = bytes.substr(cursor, end == std::string::npos ? end : end - cursor);
            if (part.empty() || ++count > 80) return std::nullopt;
            arguments.push_back(part);
            if (end == std::string::npos) break;
            cursor = end + 1;
            if (cursor == bytes.size()) return std::nullopt;
        }
        for (size_t index = 11; index < arguments.size(); index += 2)
            if (arguments[index] != "--local-endpoint" && arguments[index] != "--receiver-id" &&
                arguments[index] != "--host" && arguments[index] != "--port" &&
                arguments[index] != "--device-id" && arguments[index] != "--name") return std::nullopt;
        result.outputs = parseGroupArguments(arguments);
        std::set<std::string> identities;
        for (const auto& endpoint : result.outputs.localEndpoints) identities.insert(endpoint);
        for (const auto& receiver : result.outputs.receivers)
            if (!identities.insert(receiver.id).second) return std::nullopt;
        return result;
    } catch (const std::exception&) { return std::nullopt; }
}

inline uint64_t groupJoinFrame(uint64_t nextFrame, uint64_t leadFrames = 6615) {
    if (nextFrame > UINT64_MAX - leadFrames - 351) throw std::overflow_error("Group join frame overflow");
    return ((nextFrame + leadFrames + 351) / 352) * 352;
}
inline std::chrono::nanoseconds groupFrameTime(uint64_t frame) {
    if (frame / 44100 > uint64_t(INT64_MAX / 1000000000)) throw std::overflow_error("Group frame time overflow");
    return std::chrono::nanoseconds((frame / 44100) * 1000000000ULL + (frame % 44100) * 1000000000ULL / 44100);
}
inline uint64_t groupFrameNtp(uint64_t frame) {
    return ((frame / 44100) << 32) + ((frame % 44100) << 32) / 44100;
}

// The coordinator owns independent endpoint renderers as one lifecycle. All
// workers receive the same pull callback and presentation epoch, while their
// device clocks remain private. Stop signals fan out before any destructor
// joins: one endpoint must never keep playing during another endpoint's exit.
// The renderer contract is also exercised with offline fake device clocks.
template<class Renderer>
class GroupLocalRendererSet {
public:
    struct Progress {
        bool ready = false;
        uint64_t writtenFrames = 0, nonzeroSamples = 0, meaningfulSamples = 0;
    };
    struct Output {
        std::string endpoint;
        std::unique_ptr<Renderer> renderer;
        std::shared_ptr<uint64_t> sourceBase;
    };
    template<class Factory>
    GroupLocalRendererSet(const std::vector<std::string>& endpoints, Factory factory) {
        try {
            outputs_.reserve(endpoints.size());
            for (const auto& endpoint : endpoints) {
                auto renderer = factory(endpoint);
                if (!renderer) throw std::runtime_error("Local group renderer factory returned no output");
                outputs_.push_back({endpoint, std::move(renderer), std::make_shared<uint64_t>(0)});
            }
        } catch (...) { stop(); throw; }
    }
    ~GroupLocalRendererSet() { stop(); }
    GroupLocalRendererSet(const GroupLocalRendererSet&) = delete;
    GroupLocalRendererSet& operator=(const GroupLocalRendererSet&) = delete;

    std::string prepare() {
        if (stopped_) return "Local synchronized outputs already stopped";
        for (const auto& output : outputs_) {
            if (!output.renderer->waitReady()) {
                const auto detail = output.renderer->error();
                stop();
                return output.endpoint + ": " + (detail.empty() ? "Local group renderer initialization timed out" : detail);
            }
        }
        return {};
    }
    std::string arm(typename Renderer::Clock::time_point start, uint32_t presentationFrames) {
        if (stopped_) return "Local synchronized outputs already stopped";
        for (const auto& output : outputs_) {
            if (!output.renderer->arm(start, presentationFrames)) {
                stop();
                return output.endpoint + ": Cannot arm local output on shared group timeline";
            }
        }
        return {};
    }
    std::string error() const {
        for (const auto& output : outputs_)
            if (const auto detail = output.renderer->error(); !detail.empty()) return output.endpoint + ": " + detail;
        return {};
    }
    bool hasMeaningfulPcm(const std::set<std::string>& excluded = {}) const {
        if (stopped_) return false;
        return std::all_of(outputs_.begin(), outputs_.end(), [&excluded](const auto& output) {
            if (excluded.count(output.endpoint)) return true;
            const auto stats = output.renderer->diagnostics();
            return stats.ready && stats.armed && stats.playing && stats.meaningfulSamples > 0;
        });
    }
    Progress progress(const std::set<std::string>& excluded = {}) const {
        Progress result;
        if (stopped_ || outputs_.empty()) return result;
        result.ready = true;
        result.writtenFrames = result.nonzeroSamples = result.meaningfulSamples = UINT64_MAX;
        bool included = false;
        for (const auto& output : outputs_) {
            if (excluded.count(output.endpoint)) continue;
            included = true;
            const auto stats = output.renderer->diagnostics();
            result.ready = result.ready && stats.ready && stats.armed && stats.playing;
            result.writtenFrames = std::min(result.writtenFrames, *output.sourceBase + stats.writtenFrames);
            result.nonzeroSamples = std::min(result.nonzeroSamples, stats.nonzeroSamples);
            result.meaningfulSamples = std::min(result.meaningfulSamples, stats.meaningfulSamples);
        }
        if (!included) return {};
        return result;
    }
    const std::vector<Output>& outputs() const { return outputs_; }
    bool add(const std::string& endpoint, std::unique_ptr<Renderer> renderer,
             std::shared_ptr<uint64_t> sourceBase = std::make_shared<uint64_t>(0)) {
        if (stopped_ || !renderer || outputs_.size() >= 8 || std::any_of(outputs_.begin(), outputs_.end(),
                [&endpoint](const auto& output) { return groupSameEndpoint(output.endpoint, endpoint); })) {
            if (renderer) renderer->requestStop();
            return false;
        }
        if (!sourceBase) { renderer->requestStop(); return false; }
        outputs_.push_back({endpoint, std::move(renderer), std::move(sourceBase)});
        return true;
    }
    std::unique_ptr<Renderer> remove(const std::string& endpoint) {
        const auto item = std::find_if(outputs_.begin(), outputs_.end(),
            [&endpoint](const auto& output) { return output.endpoint == endpoint; });
        if (item == outputs_.end()) return {};
        item->renderer->requestStop();
        auto renderer = std::move(item->renderer);
        outputs_.erase(item);
        return renderer;
    }
    void stop() noexcept {
        stopped_ = true;
        for (const auto& output : outputs_) output.renderer->requestStop();
    }
    void clear() { stop(); outputs_.clear(); }
private:
    std::vector<Output> outputs_;
    bool stopped_ = false;
};

struct GroupVolumeCommand { size_t receiver = 0; double volume = 0; };
inline std::optional<GroupVolumeCommand> groupVolumeCommand(const std::string& line, size_t members) {
    if (line.size() > 1024) return std::nullopt;
    static const std::regex grammar(R"(^\s*\{\s*"command"\s*:\s*"set_volume"\s*,\s*"receiver"\s*:\s*(0|[1-9][0-9]*)\s*,\s*"volume"\s*:\s*(-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*\}\s*$)");
    std::smatch match;
    if (!std::regex_match(line, match, grammar)) return std::nullopt;
    GroupVolumeCommand result;
    const auto receiver = match[1].str(), volume = match[2].str();
    const auto parsedReceiver = std::from_chars(receiver.data(), receiver.data() + receiver.size(), result.receiver);
    const auto parsedVolume = std::from_chars(volume.data(), volume.data() + volume.size(), result.volume);
    if (parsedReceiver.ec != std::errc{} || parsedReceiver.ptr != receiver.data() + receiver.size() || result.receiver >= members ||
        parsedVolume.ec != std::errc{} || parsedVolume.ptr != volume.data() + volume.size() ||
        !std::isfinite(result.volume) || result.volume < 0 || result.volume > 1) return std::nullopt;
    return result;
}

struct OwnedGroupVolumeCommand { std::string receiver; double volume = 0; };
inline std::optional<OwnedGroupVolumeCommand> ownedGroupVolumeCommand(const std::string& line, uint64_t epoch) {
    if (line.size() > 8192) return std::nullopt;
    const std::string prefix = "{\"command\":\"set_owned_volume\",\"epoch\":";
    const std::string receiverKey = ",\"receiver_hex\":\"", volumeKey = "\",\"volume\":";
    if (!line.starts_with(prefix) || !line.ends_with('}')) return std::nullopt;
    const auto receiverAt = line.find(receiverKey, prefix.size());
    const auto volumeAt = receiverAt == std::string::npos ? receiverAt : line.find(volumeKey, receiverAt + receiverKey.size());
    if (volumeAt == std::string::npos) return std::nullopt;
    try {
        if (groupUnsigned(line.substr(prefix.size(), receiverAt - prefix.size()), UINT64_MAX, "volume epoch") != epoch) return std::nullopt;
        const auto hex = line.substr(receiverAt + receiverKey.size(), volumeAt - receiverAt - receiverKey.size());
        if (hex.empty() || hex.size() > 4096 || hex.size() % 2) return std::nullopt;
        OwnedGroupVolumeCommand result;
        auto digit = [](char c) { return c <= '9' ? c - '0' : c - 'a' + 10; };
        for (size_t index = 0; index < hex.size(); index += 2) {
            for (size_t offset = 0; offset < 2; ++offset)
                if (!(hex[index + offset] >= '0' && hex[index + offset] <= '9') &&
                    !(hex[index + offset] >= 'a' && hex[index + offset] <= 'f')) return std::nullopt;
            result.receiver += char(digit(hex[index]) * 16 + digit(hex[index + 1]));
        }
        groupText(result.receiver, 2048, "volume receiver identity");
        const auto volume = line.substr(volumeAt + volumeKey.size(), line.size() - volumeAt - volumeKey.size() - 1);
        const auto parsed = groupVolumeCommand("{\"command\":\"set_volume\",\"receiver\":0,\"volume\":" + volume + "}", 1);
        if (!parsed) return std::nullopt;
        result.volume = parsed->volume;
        return result;
    } catch (...) { return std::nullopt; }
}

// One immutable final-PCM timeline shared by network fan-out and the WASAPI
// render clock. Bounded history permits the delayed renderer to read source
// frame positions without becoming another consumer of the capture ring.
class GroupPcmHistory {
public:
    static constexpr size_t capacityFrames = 44100 * 3;
    bool append(uint64_t index, std::span<const int16_t> pcm) {
        std::lock_guard lock(mutex_);
        if (pcm.size() != 704 || index != head_ || index > UINT64_MAX - 352) return false;
        for (size_t frame = 0; frame < 352; ++frame) {
            const size_t offset = size_t((index + frame) % capacityFrames) * 2;
            data_[offset] = pcm[frame * 2]; data_[offset + 1] = pcm[frame * 2 + 1];
        }
        head_ += 352;
        return true;
    }
    bool pull(uint64_t index, std::span<int16_t> pcm) const {
        std::lock_guard lock(mutex_);
        if (pcm.empty() || pcm.size() % 2 || index > UINT64_MAX - pcm.size() / 2 ||
            index + pcm.size() / 2 > head_ || (head_ > capacityFrames && index < head_ - capacityFrames)) return false;
        for (size_t frame = 0; frame < pcm.size() / 2; ++frame) {
            const size_t offset = size_t((index + frame) % capacityFrames) * 2;
            pcm[frame * 2] = data_[offset]; pcm[frame * 2 + 1] = data_[offset + 1];
        }
        return true;
    }
private:
    mutable std::mutex mutex_;
    std::array<int16_t, capacityFrames * 2> data_{};
    uint64_t head_ = 0;
};

// Capture and speaker clocks each have independent ppm error. Condition once
// before all-sink fan-out: a gently varying consumption ratio keeps the shared
// 150 ms input lead bounded without dropping whole source frames or giving each
// receiver its own resampler. The clock correction is bounded to 0.2 percent.
class GroupPcmConditioner {
public:
    static constexpr size_t targetQueueFrames = 6615;
    GroupPcmConditioner() { (void)kernels(); }
    bool feed(std::span<const int16_t> pcm) {
        const auto frames = pcm.size() / 2;
        if (pcm.size() % 2 || frames > 44100 || queuedFrames() > 44100 - frames) return false;
        data_.insert(data_.end(), pcm.begin(), pcm.end());
        return true;
    }
    size_t queuedFrames() const { return data_.size() / 2; }
    double ratio() const { return ratio_; }
    uint64_t paddedFrames() const { return paddedFrames_; }
    void packet(std::span<int16_t> output) {
        if (output.size() != 704) throw std::invalid_argument("Group conditioner requires one stereo source packet");
        const double desired = 1.0 + std::clamp(
            (double(queuedFrames()) - targetQueueFrames) / (44100.0 * 20.0), -.002, .002);
        ratio_ += std::clamp(desired - ratio_, -.00001, .00001);
        size_t produced = 0;
        for (; produced < 352; ++produced) {
            const auto left = size_t(phase_);
            if (left + radius >= queuedFrames()) break;
            const auto fraction = phase_ - left;
            // Fractional phase never resets at a packet boundary. A short
            // symmetric windowed-sinc retains treble without the phase-varying
            // gain of two-point interpolation; all receivers share this PCM.
            const double tablePhase = fraction * phaseBins;
            const auto bin = std::min(size_t(tablePhase), phaseBins - 1);
            const double blend = tablePhase - bin;
            const auto& a = kernels()[bin];
            const auto& b = kernels()[bin + 1];
            std::array<double, 2> value{};
            for (size_t tap = 0; tap < taps; ++tap) {
                const auto offset = int64_t(left) + int64_t(tap) - int64_t(radius - 1);
                const double weight = a[tap] + (b[tap] - a[tap]) * blend;
                for (size_t channel = 0; channel < 2; ++channel)
                    value[channel] += sample(offset, channel) * weight;
            }
            for (size_t channel = 0; channel < 2; ++channel)
                output[produced * 2 + channel] = int16_t(std::lround(
                    std::clamp(value[channel], -32768.0, 32767.0)));
            phase_ += ratio_;
        }
        std::fill(output.begin() + produced * 2, output.end(), int16_t(0));
        paddedFrames_ += 352 - produced;
        const auto consumed = std::min(size_t(phase_), queuedFrames());
        for (size_t frame = 0; frame < consumed; ++frame) {
            for (size_t channel = 0; channel < 2; ++channel) {
                history_[historyWrite_ * 2 + channel] = data_.front();
                data_.pop_front();
            }
            historyWrite_ = (historyWrite_ + 1) % (radius - 1);
            historyFrames_ = std::min(historyFrames_ + 1, radius - 1);
        }
        phase_ -= consumed;
        if (!produced && data_.empty()) phase_ = 0;
    }
private:
    static constexpr size_t radius = 16, taps = radius * 2, phaseBins = 1024;
    using KernelTable = std::array<std::array<double, taps>, phaseBins + 1>;
    static const KernelTable& kernels() {
        static const auto table = [] {
            KernelTable result{};
            constexpr double pi = 3.1415926535897932384626433832795;
            for (size_t phase = 0; phase <= phaseBins; ++phase) {
                const double fraction = double(phase) / phaseBins;
                double total = 0;
                for (size_t tap = 0; tap < taps; ++tap) {
                    const double distance = fraction - (double(tap) - (radius - 1));
                    const double sinc = distance == 0 ? 1 : std::sin(pi * distance) / (pi * distance);
                    const double window = std::abs(distance) < radius
                        ? .42 + .5 * std::cos(pi * distance / radius) + .08 * std::cos(2 * pi * distance / radius) : 0;
                    total += result[phase][tap] = sinc * window;
                }
                for (auto& weight : result[phase]) weight /= total; // Exact DC gain, including near source boundaries.
            }
            // Integer phases are the original sample exactly, without trig
            // roundoff. The end phase is the next sample's matching kernel.
            result.front().fill(0); result.front()[radius - 1] = 1;
            result.back().fill(0); result.back()[radius] = 1;
            return result;
        }();
        return table;
    }
    double sample(int64_t offset, size_t channel) const {
        if (offset >= 0) return data_[size_t(offset) * 2 + channel];
        const auto past = size_t(-offset);
        if (past <= historyFrames_)
            return history_[((historyWrite_ + radius - 1 - past) % (radius - 1)) * 2 + channel];
        // Extend the first real sample only at the beginning of the source.
        return historyFrames_ ? history_[((historyWrite_ + radius - 1 - historyFrames_) % (radius - 1)) * 2 + channel]
                              : data_[channel];
    }
    std::deque<int16_t> data_;
    std::array<int16_t, (radius - 1) * 2> history_{};
    size_t historyWrite_ = 0, historyFrames_ = 0;
    double phase_ = 0, ratio_ = 1;
    uint64_t paddedFrames_ = 0;
};
} // namespace gluspot
