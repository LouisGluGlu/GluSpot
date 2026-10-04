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
        if (!state_->stats.ready || state_->stats.armed || !state_->armOk || state_->stopped) return false;
        state_->start = start; state_->stats.presentationLatencyFrames = frames;
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
    disconnectedOutputCannotLeaveTheOtherPlaying();
    std::cout << "Multi-Windows renderer shared timeline, independent clocks and all-output lifecycle passed\n";
}
