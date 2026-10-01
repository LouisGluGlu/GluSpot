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

void testTransientSessionPastReceiverWatchdog() {
    g_test = "GluSpot transient event keep-alive past two 30-second windows";
    Rig rig;
    rig.sender->setStrictReceiverAuth(true);
    rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "strict transient handshake launches");
    // The simulated receiver independently derives the reverse event keys from
    // full 64-byte SRP K, matching pyatv's HapTransient.encryption_keys().
    REQUIRE(rig.rx.sharedSecret.size() == 64, "transient event HKDF uses the full SRP secret");
    rig.rx.evOut = airplay::hkdfSha512("Events-Salt", "Events-Write-Encryption-Key", rig.rx.sharedSecret, 32);
    rig.rx.evIn = airplay::hkdfSha512("Events-Salt", "Events-Read-Encryption-Key", rig.rx.sharedSecret, 32);
    rig.sender->onTcpConnected(RaopTcp::Event, {rig.localIp, 50001}, {rig.peerIp, 7001});
    rig.io.takeUdp();
    auto lastAck = rig.now;
    bool watchdogExpired = false;
    int eventAcks = 0;
    size_t audioPackets = 0;
    for (int elapsed = 100; elapsed <= 65000; elapsed += 100) {
        rig.fillRingRamp(4410);
        rig.advance(100);
        rig.exchange();
        auto datagrams = rig.io.takeUdp();
        for (const auto& datagram : datagrams) {
            if (datagram.s == RaopUdp::Audio) ++audioPackets;
        }
        if (elapsed % 5000 == 0) {
            const std::string sequence = std::to_string(elapsed / 5000);
            const std::string request = "POST /command RTSP/1.0\r\nCSeq: " + sequence
                + "\r\nContent-Length: 5\r\n\r\nhello";
            const std::string encrypted = rig.rx.pushEvent(request);
            // Both TCP framing and encrypted-message boundaries may split.
            rig.sender->onTcpData(RaopTcp::Event, spanOf(std::string_view(encrypted).substr(0, 7)));
            const bool premature = !rig.io.takeTcp(RaopTcp::Event).empty();
            rig.sender->onTcpData(RaopTcp::Event, spanOf(std::string_view(encrypted).substr(7)));
            const auto reply = rig.rx.readEventReplies(rig.io.takeTcp(RaopTcp::Event));
            if (!premature && reply == "RTSP/1.0 200 OK\r\nServer: AirTunes/550.10\r\nCSeq: "
                                      + sequence + "\r\n\r\n") {
                lastAck = rig.now;
                ++eventAcks;
            }
        }
        if (rig.now - lastAck >= std::chrono::seconds(30)) {
            watchdogExpired = true;
            rig.sender->onTcpClosed(RaopTcp::Control, "");
            break;
        }
    }
    CHECK(!watchdogExpired && rig.sender->active(), "receiver watchdog remains satisfied across 65 seconds");
    CHECK(eventAcks == 13, "all transient encrypted event pushes receive minimal 200 with correct CSeq/nonces");
    CHECK(audioPackets >= 8000, "audio pacing continues throughout both watchdog windows");
    const auto feedback = std::count_if(rig.rx.requests.begin(), rig.rx.requests.end(),
        [](const Request& request) { return request.method == "POST" && request.uri == "/feedback" && request.version == "RTSP/1.0"; });
    CHECK(feedback == 32, "32 valid encrypted feedback requests receive replies over 65 seconds");
    const auto diagnostics = rig.sender->diagnostics();
    CHECK(diagnostics.eventConnected && diagnostics.eventKeyed && diagnostics.eventAcks == 13
          && diagnostics.eventRequests == 13 && diagnostics.eventDecryptFailures == 0,
          "bounded event diagnostics agree with independently decrypted replies");
    CHECK(diagnostics.feedbackSent == 32 && diagnostics.feedbackReplies == 32
          && diagnostics.lastFeedbackCode == 200 && diagnostics.feedbackSkipped == 0,
          "feedback counters prove timer and receiver responses across both watchdog windows");
    CHECK(diagnostics.audioFramesSent == audioPackets * 352 && diagnostics.protocolLatencyFrames == 66150
          && diagnostics.receiverLatencyReported && diagnostics.receiverLatencyFrames == 11025,
          "sender timeline buffer and reported receiver minimum remain distinct from acoustic latency");
    CHECK(!rig.rx.controlDecryptFailed && volumeRequests(rig.rx) == 0,
          "event/control counters stay independent and current receiver volume is preserved");
    rig.sender->stop();
    rig.exchange();
    CHECK(rig.rx.count("TEARDOWN") == 1 && !rig.io.anyUdpOpen(), "long session still tears down cleanly");
}

void testUnansweredFeedbackIsBoundedAndRecoverable() {
    g_test = "GluSpot bounded unanswered feedback and rejection evidence";
    Rig rig;
    rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "transient handshake launches");
    rig.io.takeUdp();
    rig.advance(2000);
    // Decrypt the first feedback at the receiver but hold its reply for 60 s.
    const auto reply = rig.rx.onControlBytes(rig.io.takeTcp(RaopTcp::Control));
    REQUIRE(!reply.empty(), "valid feedback reaches the receiver");
    for (int i = 0; i < 30; ++i) {
        rig.advance(2000);
        rig.io.takeUdp();
    }
    auto d = rig.sender->diagnostics();
    CHECK(d.feedbackSent == 1 && d.feedbackSkipped == 30 && rig.io.takeTcp(RaopTcp::Control).empty(),
          "missing replies leave one pending feedback instead of an unbounded FIFO");
    rig.sender->onTcpData(RaopTcp::Control, spanOf(reply));
    rig.advance(2000);
    const auto ignoredReply = rig.rx.onControlBytes(rig.io.takeTcp(RaopTcp::Control));
    REQUIRE(!ignoredReply.empty(), "feedback resumes after the delayed reply");
    const Request* request = rig.rx.last("POST", "/feedback");
    REQUIRE(request, "resumed request is feedback");
    const std::string rejected = "RTSP/1.0 500 Error\r\nCSeq: " + request->h("cseq")
                                + "\r\nContent-Length: 0\r\n\r\n";
    // onControlBytes advanced its output nonce to build ignoredReply; roll it
    // back so this replacement response occupies the same encrypted frame.
    --rig.rx.ctrlOutCtr;
    rig.sender->onTcpData(RaopTcp::Control, spanOf(encryptFrames(rig.rx.ctrlOut, rig.rx.ctrlOutCtr, rejected)));
    d = rig.sender->diagnostics();
    CHECK(d.feedbackSent == 2 && d.feedbackReplies == 2 && d.feedbackRejected == 1
          && d.lastFeedbackCode == 500 && d.lastError.find("500") != std::string::npos,
          "rejected feedback stays visible rather than being silently discarded");
    rig.sender->stop();
    d = rig.sender->diagnostics();
    CHECK(!d.eventConnected && d.feedbackReplies == 2, "final diagnostics retain evidence after releasing sockets");
}

}  // namespace

int main() {
    testPreserveReceiverVolume();
    testExplicitVolumeDuringHandshake();
    testTransientSessionPastReceiverWatchdog();
    testUnansweredFeedbackIsBoundedAndRecoverable();
    std::printf("GluSpot sender policy: %d/%d checks passed\n", g_checks - g_failures, g_checks);
    return g_failures ? 1 : 0;
}
