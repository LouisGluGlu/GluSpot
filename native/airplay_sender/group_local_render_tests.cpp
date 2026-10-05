// Exercise the production multi-output barrier/ownership with offline device
// clocks. No WASAPI stream, endpoint mute or network receiver is opened.
#include "group_main.h"
#include "local_audio_renderer.h"
#include <cassert>
#include <iostream>

namespace {
using Clock = std::chrono::steady_clock;
struct State {
    std::string endpoint;
    gluspot::LocalRenderDiagnostics stats;
    Clock::time_point start{};
    Clock::time_point presentation{};
    uint32_t extraDelayFrames = 0;
    std::string error;
    bool prepareOk = true, armOk = true, stopped = false, destroyed = false;
    double cursor = 0;
};
class FakeRenderer {
public:
    using Clock = std::chrono::steady_clock;
    FakeRenderer(std::shared_ptr<State> state, std::vector<std::shared_ptr<State>>& peers,
        gluspot::GroupPcmHistory& history) : state_(std::move(state)), peers_(peers), history_(history) {}
    ~FakeRenderer() {
        // This catches sequential join-then-stop even on constructor/arm failure.
        for (const auto& peer : peers_) assert(peer->stopped);
        state_->destroyed = true;
    }
    bool waitReady() { return state_->stats.ready = state_->prepareOk && state_->error.empty(); }
    bool arm(Clock::time_point start, uint32_t frames) {
        if (!state_->stats.ready || state_->stats.armed || !state_->armOk || state_->stopped ||
            !gluspot::localPresentationFitsHistory(frames, state_->extraDelayFrames)) return false;
        state_->start = start; state_->stats.presentationLatencyFrames = frames;
        state_->presentation = start + gluspot::localPresentationDelay(frames, state_->extraDelayFrames);
        state_->stats.extraDelayFrames = state_->extraDelayFrames;
        state_->stats.localPresentationLatencyFrames =
            gluspot::localPresentationFrames(frames, state_->extraDelayFrames);
        state_->stats.armed = state_->stats.playing = true;
        return true;
    }
    std::string error() const { return state_->error; }
    gluspot::LocalRenderDiagnostics diagnostics() const { return state_->stats; }
    void requestStop() noexcept { state_->stopped = true; state_->stats.playing = false; }
    bool render(double desired, size_t frames) {
        if (state_->stopped || !state_->stats.armed) return false;
        const double phase = desired - state_->cursor;
        state_->stats.phaseErrorMs = phase * 1000 / 44100;
        state_->stats.resampleRatio = gluspot::local_render_detail::slewRatio(phase);
        const auto first = uint64_t(std::floor(state_->cursor));
        const double fractional = state_->cursor - first;
        const auto needed = size_t(std::ceil((frames - 1) * state_->stats.resampleRatio + fractional)) + 2;
        std::vector<int16_t> source(needed * 2);
        if (!history_.pull(first, source)) { state_->error = "Shared history unavailable"; return false; }
        for (size_t frame = 0; frame < frames; ++frame) {
            const double position = fractional + frame * state_->stats.resampleRatio;
            const auto left = size_t(std::floor(position));
            for (size_t channel = 0; channel < 2; ++channel) {
                const auto value = gluspot::local_render_detail::interpolate(
                    source[left * 2 + channel], source[(left + 1) * 2 + channel], position - left);
                if (value) ++state_->stats.nonzeroSamples;
                if (value < -1 || value > 1) ++state_->stats.meaningfulSamples;
            }
        }
        state_->cursor += frames * state_->stats.resampleRatio;
        state_->stats.writtenFrames += frames;
        state_->stats.sourceFrame = uint64_t(state_->cursor);
        return true;
    }
private:
    std::shared_ptr<State> state_;
    std::vector<std::shared_ptr<State>>& peers_;
    gluspot::GroupPcmHistory& history_;
};
using Outputs = gluspot::GroupLocalRendererSet<FakeRenderer>;

struct Rig {
    std::unique_ptr<gluspot::GroupPcmHistory> history = std::make_unique<gluspot::GroupPcmHistory>();
    std::vector<std::shared_ptr<State>> states;
    uint64_t head = 0;
    void publishUntil(uint64_t frame, int16_t sample = 3100) {
        const std::array<int16_t, 704> pcm = [sample] {
            std::array<int16_t, 704> packet{}; packet.fill(sample); return packet;
        }();
        while (head < frame) { assert(history->append(head, pcm)); head += 352; }
    }
    auto factory(bool secondPrepare = true, bool secondArm = true, bool throwSecond = false) {
        return [this, secondPrepare, secondArm, throwSecond](const std::string& endpoint) {
            if (throwSecond && !states.empty()) throw std::runtime_error("Fake activation failure");
            auto state = std::make_shared<State>(); state->endpoint = endpoint;
            if (!states.empty()) { state->prepareOk = secondPrepare; state->armOk = secondArm; }
            states.push_back(state);
            return std::make_unique<FakeRenderer>(state, states, *history);
        };
    }
};

void sharedTimelineIndependentDeviceClocks() {
    Rig rig;
    {
        Outputs outputs({"wired", "bluetooth"}, rig.factory());
        assert(outputs.prepare().empty());
        const auto epoch = Clock::time_point(std::chrono::seconds(100));
        const auto lead = gluspot::groupPresentationFrames(66150, 11025);
        assert(outputs.arm(epoch, lead).empty());
        assert(rig.states[0]->start == epoch && rig.states[1]->start == epoch);
        assert(rig.states[0]->stats.presentationLatencyFrames == lead &&
            rig.states[1]->stats.presentationLatencyFrames == lead);
        assert(!outputs.hasMeaningfulPcm());
        rig.publishUntil(10000);
        assert(outputs.outputs()[0].renderer->render(0, 441));
        assert(!outputs.hasMeaningfulPcm()); // The wired output cannot certify Bluetooth.
        assert(outputs.outputs()[1].renderer->render(0, 441));
        assert(outputs.hasMeaningfulPcm());
        // Both sinks continue over the same bounded immutable PCM history, with
        // +200/-150 ppm device clock models and private correction cursors.
        for (unsigned pass = 1; pass < 6000; ++pass) {
            rig.publishUntil(uint64_t((pass + 20) * 441));
            assert(outputs.outputs()[0].renderer->render(pass * 441 * 1.0002, 441));
            assert(outputs.outputs()[1].renderer->render(pass * 441 * .99985, 441));
        }
        assert(rig.states[0]->stats.resampleRatio > 1 && rig.states[1]->stats.resampleRatio < 1);
        assert(std::abs(rig.states[0]->cursor - 6000 * 441 * 1.0002) < 19);
        assert(std::abs(rig.states[1]->cursor - 6000 * 441 * .99985) < 14);
        assert(rig.states[0]->stats.sourceFrame > rig.states[1]->stats.sourceFrame);
        assert(rig.states[0]->stats.underrunFrames == 0 && rig.states[1]->stats.underrunFrames == 0);
        // A renderer read never consumes data, rewrites a packet, or advances
        // the other renderer's cursor. Repeated overlapping ranges stay exact.
        const auto before = rig.states[1]->cursor;
        std::array<int16_t, 130> a{}, b{};
        assert(rig.history->pull(rig.head - 175, a) && rig.history->pull(rig.head - 175, b));
        assert(a == b && std::all_of(a.begin(), a.end(), [](auto sample) { return sample == 3100; }));
        assert(rig.states[1]->cursor == before);
        outputs.clear();
        assert(!outputs.hasMeaningfulPcm() && !outputs.arm(epoch, lead).empty());
    }
    assert(rig.states[0]->destroyed && rig.states[1]->destroyed);
}

void everyInitializationAndArmFailureStopsPeers() {
    for (int failure = 0; failure < 3; ++failure) {
        Rig rig;
        try {
            Outputs outputs({"wired", "bluetooth"}, rig.factory(failure != 0, failure != 1, failure == 2));
            if (failure == 0) assert(!outputs.prepare().empty());
            else {
                assert(outputs.prepare().empty());
                assert(!outputs.arm(Clock::time_point(std::chrono::seconds(100)), 77175).empty());
            }
            for (const auto& state : rig.states) assert(state->stopped);
            assert(!outputs.hasMeaningfulPcm());
        } catch (const std::runtime_error&) { assert(failure == 2); }
        for (const auto& state : rig.states) assert(state->stopped && state->destroyed);
    }
}

void optionalCompensationPreservesOtherEndpointEpoch() {
    Rig rig;
    auto factory = rig.factory();
    Outputs outputs({"wired", "bluetooth"}, [&](const std::string& endpoint) {
        auto renderer = factory(endpoint);
        if (endpoint == "bluetooth") rig.states.back()->extraDelayFrames = 11025;
        return renderer;
    });
    assert(outputs.prepare().empty());
    const auto start = Clock::time_point(std::chrono::seconds(100));
    assert(outputs.arm(start, 77175).empty());
    assert(rig.states[0]->presentation == start + std::chrono::milliseconds(1750));
    assert(rig.states[1]->presentation == start + std::chrono::milliseconds(2000));
    assert(rig.states[0]->stats.presentationLatencyFrames == 77175 &&
           rig.states[1]->stats.presentationLatencyFrames == 77175);
    assert(rig.states[0]->stats.extraDelayFrames == 0 && rig.states[1]->stats.extraDelayFrames == 11025);
    assert(rig.states[1]->stats.localPresentationLatencyFrames == 88200);
    // The existing armed fence prevents any running-cursor epoch adjustment.
    assert(!outputs.arm(start, 77175).empty());
    assert(rig.states[0]->stopped && rig.states[1]->stopped);
    // A compensated endpoint that exceeds the joint retention budget refuses
    // arm normally, so the barrier fans out stop to an already armed peer.
    Rig limited;
    auto limitedFactory = limited.factory();
    Outputs outOfBudget({"wired", "bluetooth"}, [&](const std::string& endpoint) {
        auto renderer = limitedFactory(endpoint);
        if (endpoint == "bluetooth") limited.states.back()->extraDelayFrames = 22050;
        return renderer;
    });
    assert(outOfBudget.prepare().empty() && !outOfBudget.arm(start, 99225).empty());
    assert(limited.states[0]->stats.armed && !limited.states[1]->stats.armed);
    assert(limited.states[0]->stopped && limited.states[1]->stopped);
}

void compensationPolicyAndThreeSecondHistoryBudget() {
    static_assert(gluspot::GroupPcmHistory::capacityFrames == gluspot::groupPcmHistoryFrames);
    assert(gluspot::localPresentationDelay(77175, 0) == std::chrono::milliseconds(1750));
    assert(gluspot::localPresentationDelay(77175, 22050) == std::chrono::milliseconds(2250));
    constexpr uint32_t maximumExtra = gluspot::groupPcmHistoryFrames - gluspot::localHistoryMarginFrames - 99225;
    static_assert(maximumExtra == 21696);
    assert(gluspot::localPresentationDelay(99225, maximumExtra).count() == 2741972789);
    assert(gluspot::GroupPcmHistory::capacityFrames -
           gluspot::localPresentationFrames(99225, maximumExtra) == gluspot::localHistoryMarginFrames);
    for (const auto& [base, extra] : std::vector<std::pair<uint32_t, uint32_t>>{
            {0, 0}, {99226, 0}, {77175, 22051}, {77175, UINT32_MAX}, {UINT32_MAX, 0},
            {99225, maximumExtra + 1}, {99225, 22050}}) {
        bool rejected = false;
        try { (void)gluspot::localPresentationDelay(base, extra); }
        catch (const std::invalid_argument&) { rejected = true; }
        assert(rejected);
    }
    bool rejected = false;
    try {
        gluspot::LocalAudioRenderer renderer(L"not-opened",
            [](uint64_t, std::span<int16_t>) { return false; }, 22051);
    } catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected); // Out-of-budget construction cannot start a WASAPI worker.

    Rig rig;
    constexpr uint64_t queueFrames = 1764; // 40 ms clock-derived write delay.
    std::array<int16_t, 886> pcm{}, repeated{}; // A 10 ms write plus interpolation lookahead.
    for (uint64_t elapsed = 0; elapsed <= 60 * 44100; elapsed += 441) {
        rig.publishUntil(elapsed + 1); // Production nextFrame can lead dueFrame by at most 352.
        for (uint32_t base : {77175u, 99225u}) {
            for (uint32_t extra : {0u, 11025u, 22050u}) {
                if (!gluspot::localPresentationFitsHistory(base, extra)) continue;
                const auto delay = gluspot::localPresentationFrames(base, extra);
                // The production prefix emits silence while its own epoch is
                // still in the future; compensation never asks for future PCM.
                if (elapsed + queueFrames < delay) continue;
                const auto first = elapsed + queueFrames - delay;
                assert(first + pcm.size() / 2 <= rig.head);
                assert(rig.history->pull(first, pcm) && rig.history->pull(first, repeated));
                assert(pcm == repeated && std::all_of(pcm.begin(), pcm.end(), [](auto value) { return value == 3100; }));
            }
        }
        // Worst permitted base+extra with a cursor at the phase-guard limit,
        // and one publisher packet ahead still fits after each history wrap.
        const auto maximumDelay = gluspot::localPresentationFrames(99225, maximumExtra);
        if (elapsed >= maximumDelay + 11025) {
            const auto first = elapsed - maximumDelay - 11025;
            assert(rig.history->pull(first, pcm));
        }
    }
    assert(rig.head > gluspot::GroupPcmHistory::capacityFrames);
    assert(!rig.history->pull(0, pcm)); // Retention stays bounded at three seconds.
}

void disconnectedOutputCannotLeaveTheOtherPlaying() {
    Rig rig;
    Outputs outputs({"wired", "bluetooth"}, rig.factory());
    assert(outputs.prepare().empty() && outputs.arm(Clock::time_point(std::chrono::seconds(100)), 77175).empty());
    rig.publishUntil(1000);
    assert(outputs.outputs()[0].renderer->render(0, 441));
    assert(outputs.outputs()[1].renderer->render(0, 441));
    assert(outputs.hasMeaningfulPcm());
    rig.states[1]->error = "Device disconnected";
    rig.states[1]->stats.playing = false;
    assert(outputs.error() == "bluetooth: Device disconnected" && !outputs.hasMeaningfulPcm());
    // This is the same terminal action the production host performs on error.
    outputs.stop();
    assert(rig.states[0]->stopped && rig.states[1]->stopped);
    assert(!outputs.outputs()[0].renderer->render(441, 441));
    assert(!outputs.arm(Clock::time_point(std::chrono::seconds(200)), 77175).empty());
}
} // namespace

int main() {
    sharedTimelineIndependentDeviceClocks();
    everyInitializationAndArmFailureStopsPeers();
    optionalCompensationPreservesOtherEndpointEpoch();
    compensationPolicyAndThreeSecondHistoryBudget();
    disconnectedOutputCannotLeaveTheOtherPlaying();
    std::cout << "Multi-Windows renderer shared timeline, independent clocks and all-output lifecycle passed\n";
}
