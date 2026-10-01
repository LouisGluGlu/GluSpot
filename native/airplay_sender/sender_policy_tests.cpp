// GluSpot policy checks reuse the upstream's real HAP fake receiver rather
// than implementing a second, potentially divergent cryptographic test peer.
#define main upstreamCoreTestMain
#include "../../third_party/airplay2-sender-cpp/test/core_tests.cpp"
#undef main

namespace {
size_t volumeRequests(const FakeReceiver& receiver) {
    return size_t(std::count_if(receiver.requests.begin(), receiver.requests.end(),
        [](const Request& request) {
            return request.method == "SET_PARAMETER" && request.body.starts_with("volume: ");
        }));
}

void testPreserveReceiverVolume() {
    g_test = "GluSpot preserves receiver volume";
    Rig rig;
    rig.sender->setStrictReceiverAuth(true);
    rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "strict authenticated AP2 launch succeeds");
    CHECK(volumeRequests(rig.rx) == 0, "no automatic volume SET_PARAMETER at launch");
    rig.fillRingRamp(352 * 8);
    rig.advance(8);
    auto datagrams = rig.io.takeUdp();
    CHECK(std::any_of(datagrams.begin(), datagrams.end(),
        [](const FakeIo::Datagram& datagram) { return datagram.s == RaopUdp::Audio; }),
        "preserving volume does not suppress normal encrypted audio");
    rig.sender->setVolume(50);
    rig.exchange();
    CHECK(volumeRequests(rig.rx) == 1, "explicit setVolume still sends one volume request");
    const Request* volume = rig.rx.last("SET_PARAMETER");
    CHECK(volume && volume->body == "volume: -15.000000", "explicit 50 percent uses normal dB mapping");
    rig.sender->stop();
    rig.exchange();
    CHECK(rig.rx.count("TEARDOWN") == 1 && !rig.io.anyUdpOpen(), "normal stop tears down and releases UDP sockets");

    // An explicit volume in one session must not carry into a new receiver.
    rig.rx = FakeReceiver{};
    rig.launchedOk.reset();
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "the next authenticated session also launches");
    CHECK(volumeRequests(rig.rx) == 0, "preserve policy survives reconnect without carrying old volume");
}

void testExplicitVolumeDuringHandshake() {
    g_test = "GluSpot explicit volume during handshake";
    Rig rig;
    rig.sender->setPreserveReceiverVolume(true);
    rig.sender->setAuth(RaopDeviceInfo::Auth::HapTransient, true, "atv-1", "", "");
    rig.rx.mode = FakeReceiver::Mode::Ap2Transient;
    rig.sender->start(rig.peerIp, 7000, "Living Room");
    rig.sender->setVolume(25);
    rig.connectControl();
    rig.exchange();
    REQUIRE(rig.launchedOk && *rig.launchedOk, "AP2 launch succeeds with an explicit pending volume");
    CHECK(volumeRequests(rig.rx) == 1, "explicit pending volume is sent at launch");
    const Request* volume = rig.rx.last("SET_PARAMETER");
    CHECK(volume && volume->body == "volume: -22.500000", "pending 25 percent uses normal dB mapping");
}
}  // namespace

int main() {
    testPreserveReceiverVolume();
    testExplicitVolumeDuringHandshake();
    std::printf("GluSpot sender policy: %d/%d checks passed\n", g_checks - g_failures, g_checks);
    return g_failures ? 1 : 0;
}
