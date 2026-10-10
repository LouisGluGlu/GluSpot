// Exercise the production multi-output barrier/ownership with offline device
// clocks. No WASAPI stream, endpoint mute or network receiver is opened.
#include "group_main.h"
#include "local_audio_renderer.h"
#include <cassert>
#include <iostream>
#include <future>

namespace {
using Clock = std::chrono::steady_clock;
struct State {
    std::string endpoint;
    gluspot::LocalRenderDiagnostics stats;
    Clock::time_point start{};
    std::string error;
    bool prepareOk = true, armOk = true, stopped = false, destroyed = false;
    bool wholeGroupTeardown = true;
    double cursor = 0;
    std::vector<int16_t> lastPcm;
    std::shared_future<void> retireGate;
};
class FakeRenderer {
public:
    using Clock = std::chrono::steady_clock;
    FakeRenderer(std::shared_ptr<State> state, std::vector<std::shared_ptr<State>>& peers,
        gluspot::GroupPcmHistory& history, uint64_t base = 0)
        : state_(std::move(state)), peers_(peers), history_(history), base_(base) {}
    ~FakeRenderer() {
        if (state_->retireGate.valid()) state_->retireGate.wait();
        // This catches sequential join-then-stop even on constructor/arm failure.
        if (state_->wholeGroupTeardown) for (const auto& peer : peers_) assert(peer->stopped);
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
        if (!history_.pull(base_ + first, source)) { state_->error = "Shared history unavailable"; return false; }
        state_->lastPcm.clear();
        for (size_t frame = 0; frame < frames; ++frame) {
            const double position = fractional + frame * state_->stats.resampleRatio;
            const auto left = size_t(std::floor(position));
            for (size_t channel = 0; channel < 2; ++channel) {
                const auto value = gluspot::local_render_detail::interpolate(
                    source[left * 2 + channel], source[(left + 1) * 2 + channel], position - left);
                state_->lastPcm.push_back(value);
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
    uint64_t base_ = 0;
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

void windowsOnlyBarrierAndProgressWithoutNetworkEvidence() {
    Rig rig;
    Outputs outputs({"bluetooth-a", "bluetooth-b"}, rig.factory());
    assert(!outputs.progress().ready && outputs.progress().meaningfulSamples == 0);
    assert(outputs.prepare().empty());
    const auto start = Clock::time_point(std::chrono::seconds(100));
    const auto reserve = gluspot::windowsGroupPresentationFrames;
    assert(reserve == gluspot::groupPresentationFrames(66150, 11025));
    assert(outputs.arm(start, reserve).empty());
    assert(rig.states[0]->start == start && rig.states[1]->start == start);
    assert(rig.states[0]->stats.presentationLatencyFrames == reserve);
    assert(rig.states[1]->stats.presentationLatencyFrames == reserve);
    assert(outputs.progress().ready && outputs.progress().writtenFrames == 0);
    rig.publishUntil(1000, 0);
    for (const auto& output : outputs.outputs()) assert(output.renderer->render(0, 441));
    assert(outputs.progress().writtenFrames == 441 && outputs.progress().meaningfulSamples == 0);
    assert(!outputs.hasMeaningfulPcm()); // Silence never confirms the local-only group.
    rig.publishUntil(3000, 3100);
    for (const auto& output : outputs.outputs()) assert(output.renderer->render(441, 441));
    for (const auto& output : outputs.outputs()) assert(output.renderer->render(882, 441));
    assert(outputs.progress().meaningfulSamples > 0 && outputs.hasMeaningfulPcm());
    assert(outputs.progress().writtenFrames == 1323);
    rig.states[1]->stats.playing = false;
    assert(!outputs.progress().ready && !outputs.hasMeaningfulPcm());
    outputs.stop();
    assert(!outputs.progress().ready && outputs.progress().writtenFrames == 0);
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

void hotJoinAndSubsetSoloKeepHistoryEpochAndOldClock() {
    Rig rig;
    Outputs outputs({"wired"}, rig.factory());
    rig.states[0]->wholeGroupTeardown = false;
    assert(outputs.prepare().empty());
    const auto start = Clock::time_point(std::chrono::seconds(100));
    assert(outputs.arm(start, 77175).empty());
    auto* retained = outputs.outputs()[0].renderer.get();
    auto base = std::make_shared<uint64_t>(gluspot::groupJoinFrame(352));
    while (rig.head < *base + 3000) {
        std::array<int16_t, 704> pcm{};
        for (size_t index = 0; index < pcm.size(); ++index) pcm[index] = int16_t(2000 + (rig.head + index / 2) % 12000);
        assert(rig.history->append(rig.head, pcm)); rig.head += 352;
    }
    assert(retained->render(0, 352));
    const auto oldCursor = rig.states[0]->cursor;
    const auto oldStart = rig.states[0]->start;
    const auto oldCount = rig.states[0]->stats.writtenFrames;
    auto state = std::make_shared<State>(); state->endpoint = "bluetooth"; state->wholeGroupTeardown = false;
    rig.states.push_back(state);
    auto incoming = std::make_unique<FakeRenderer>(state, rig.states, *rig.history, *base);
    auto* joined = incoming.get();
    assert(outputs.add("bluetooth", std::move(incoming), base));
    assert(outputs.outputs()[0].renderer.get() == retained && !rig.states[0]->stopped &&
        rig.states[0]->cursor == oldCursor && rig.states[0]->start == oldStart);
    assert(outputs.progress({"bluetooth"}).ready && outputs.progress({"bluetooth"}).writtenFrames == oldCount);
    assert(joined->waitReady() && joined->arm(start + gluspot::groupFrameTime(*base), 77175));
    assert(retained->render(352, size_t(*base - 352)) && retained->render(double(*base), 352));
    assert(joined->render(0, 352));
    assert(rig.states[0]->lastPcm == state->lastPcm);
    assert(outputs.progress().writtenFrames == *base + 352 && outputs.hasMeaningfulPcm());
    assert(state->start == oldStart + gluspot::groupFrameTime(*base));
    const auto joinedCursor = state->cursor;
    std::promise<void> retireGate;
    rig.states[0]->retireGate = retireGate.get_future().share();
    auto retired = outputs.remove("wired");
    assert(rig.states[0]->stopped && !rig.states[0]->destroyed && !state->stopped &&
        state->cursor == joinedCursor && outputs.outputs()[0].renderer.get() == joined);
    auto cleanup = std::async(std::launch::async, [retired = std::move(retired)]() mutable { retired.reset(); });
    assert(joined->render(352, 352) && outputs.progress().writtenFrames == *base + 704);
    assert(cleanup.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready && !state->stopped);
    retireGate.set_value(); cleanup.get();
    assert(rig.states[0]->destroyed);
    auto failure = std::make_shared<State>(); failure->prepareOk = false; failure->wholeGroupTeardown = false;
    rig.states.push_back(failure);
    auto failing = std::make_unique<FakeRenderer>(failure, rig.states, *rig.history);
    auto* failingPtr = failing.get();
    const auto beforeFailure = state->cursor;
    assert(outputs.add("bad-endpoint", std::move(failing)) && !failingPtr->waitReady());
    outputs.remove("bad-endpoint");
    assert(failure->stopped && failure->destroyed && !state->stopped && state->cursor == beforeFailure);
    auto duplicate = std::make_shared<State>(); duplicate->wholeGroupTeardown = false; rig.states.push_back(duplicate);
    assert(!outputs.add("BLUETOOTH", std::make_unique<FakeRenderer>(duplicate, rig.states, *rig.history)));
    assert(duplicate->stopped && duplicate->destroyed && !state->stopped);
    assert(joined->render(704, 352) && outputs.progress().writtenFrames == *base + 1056);
    outputs.stop();
    assert(state->stopped);
}
} // namespace

int main() {
    sharedTimelineIndependentDeviceClocks();
    everyInitializationAndArmFailureStopsPeers();
    disconnectedOutputCannotLeaveTheOtherPlaying();
    windowsOnlyBarrierAndProgressWithoutNetworkEvidence();
    hotJoinAndSubsetSoloKeepHistoryEpochAndOldClock();
    std::cout << "Multi-Windows renderer shared timeline, independent clocks and all-output lifecycle passed\n";
}
