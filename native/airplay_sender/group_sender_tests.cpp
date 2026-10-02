// Independently decrypt the coordinated group's packets using the existing
// cryptographic AP2 fake receiver. Arrival coincidence cannot prove sync:
// these checks compare identical PCM and its RTP-to-NTP presentation mapping.
#define main upstreamCoreTestMain
#include "../../third_party/airplay2-sender-cpp/test/core_tests.cpp"
#undef main
#include "group_main.h"
#include <limits>

namespace {
uint64_t wire64(const std::string& packet, size_t offset) {
    return (uint64_t(be32(packet, offset)) << 32) | be32(packet, offset + 4);
}
uint64_t sampleNtp(uint64_t frame) {
    return ((frame / 44100) << 32) + ((frame % 44100) << 32) / 44100;
}
uint64_t millisecondNtp(uint64_t ms) {
    return ((ms / 1000) << 32) + (ms % 1000) * 0x100000000ULL / 1000;
}
std::vector<FakeIo::Datagram> packetsOf(Rig& rig, RaopUdp kind) {
    auto packets = rig.io.takeUdp();
    packets.erase(std::remove_if(packets.begin(), packets.end(),
        [kind](const auto& packet) { return packet.s != kind; }), packets.end());
    return packets;
}
void connectEvents(Rig& rig) {
    // Upstream's transient fixture initializes control keys only. Derive the
    // independently authenticated reverse event keys from full SRP K too.
    rig.rx.evOut = airplay::hkdfSha512("Events-Salt", "Events-Write-Encryption-Key", rig.rx.sharedSecret, 32);
    rig.rx.evIn = airplay::hkdfSha512("Events-Salt", "Events-Read-Encryption-Key", rig.rx.sharedSecret, 32);
    rig.sender->onTcpConnected(RaopTcp::Event, {rig.localIp, 50001}, {rig.peerIp, rig.rx.eventPort});
}
void timingRequest(Rig& rig) {
    std::string request(32, '\0');
    request[0] = char(0x80); request[1] = char(0xD2);
    for (size_t i = 24; i < 32; ++i) request[i] = char(i);
    rig.sender->onUdpDatagram(RaopUdp::Timing, spanOf(request), {rig.peerIp, 6002});
}

void testStaggeredReceiversSharePcmAndPresentationClock() {
    g_test = "GluSpot two independently paired receivers share PCM/RTP/NTP";
    Clock::time_point sharedNow{std::chrono::seconds(1000)};
    Rig a, b;
    for (auto* rig : {&a, &b}) {
        rig->sender->setClock([&sharedNow] { return sharedNow; });
        rig->sender->setCoordinatedPlayback(true);
        rig->sender->setStrictReceiverAuth(true);
        rig->sender->setPreserveReceiverVolume(true);
        rig->sender->setInputFormat(48000); // Core group API must never resample.
    }
    runAp2Handshake(a, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(a.launchedOk && *a.launchedOk, "first receiver is authenticated/ready");
    sharedNow += std::chrono::milliseconds(1234);
    a.sender->tick();
    runAp2Handshake(b, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(b.launchedOk && *b.launchedOk, "second receiver is ready 1.234 seconds later");
    REQUIRE(a.rx.audioKey != b.rx.audioKey, "receivers independently authenticate with different audio keys");
    CHECK(a.io.takeUdp().empty() && b.io.takeUdp().empty(),
          "staggered setup and pacer ticks emit neither audio nor sync before group barrier");
    a.fillRingRamp(1500, 7); b.fillRingRamp(900, 129);
    const auto aInput = a.ring.availableRead(), bInput = b.ring.availableRead();
    const auto start = sharedNow + std::chrono::milliseconds(150);
    const uint64_t anchor = 0xEC110234ABCDEF12ULL; // Deliberately unlike the real wall clock.
    for (auto* rig : {&a, &b}) {
        CHECK(rig->sender->armCoordinatedTimeline(start, anchor), "ready receiver accepts shared future anchor");
        const auto sync = packetsOf(*rig, RaopUdp::Control);
        REQUIRE(sync.size() == 1 && sync[0].data.size() == 20, "one 20-byte initial NTP sync");
        CHECK(uint8_t(sync[0].data[0]) == 0x90 && uint8_t(sync[0].data[1]) == 0xD4 &&
              be32(sync[0].data, 4) == 0 && wire64(sync[0].data, 8) == anchor &&
              be32(sync[0].data, 16) == 66150, "sync maps frame zero to common NTP with common 1.5 second presentation lead");
        CHECK(!rig->sender->armCoordinatedTimeline(start, anchor), "a live timeline cannot be independently rebased");
        const auto d = rig->sender->diagnostics();
        CHECK(d.coordinatedPlayback && d.coordinatedTimelineArmed && d.queuedInputFrames == 0 &&
              d.inputSampleRate == 44100, "group diagnostics expose fixed final PCM without per-sink input conditioning");
    }
    auto pcm = rampExpect(704, -321);
    CHECK(!a.sender->sendCoordinatedAudio(0, pcm) && !b.sender->sendCoordinatedAudio(0, pcm),
          "future barrier cannot send frame zero early");
    timingRequest(a); timingRequest(b);
    const auto earlyA = packetsOf(a, RaopUdp::Timing), earlyB = packetsOf(b, RaopUdp::Timing);
    REQUIRE(earlyA.size() == 1 && earlyB.size() == 1, "both receivers receive timing answers before frame zero");
    CHECK(earlyA[0].data == earlyB[0].data && wire64(earlyA[0].data, 16) == anchor - millisecondNtp(150),
          "timing answers already use the shared anchor minus future lead, not separate system clocks");

    sharedNow = start;
    uint16_t seqA = 0, seqB = 0;
    uint64_t decodedNonzero = 0;
    uint64_t decodedMeaningful = 0;
    for (uint64_t index : {0ULL, 352ULL, 704ULL, 1056ULL}) {
        if (index) {
            sharedNow += std::chrono::milliseconds(8);
            pcm = rampExpect(704, int16_t(index));
        }
        REQUIRE(a.sender->sendCoordinatedAudio(index, pcm) && b.sender->sendCoordinatedAudio(index, pcm),
                "one coordinator packet is accepted by both receiver sessions");
        const auto pa = packetsOf(a, RaopUdp::Audio), pb = packetsOf(b, RaopUdp::Audio);
        REQUIRE(pa.size() == 1 && pb.size() == 1, "exactly one encrypted packet reaches each transport");
        const auto decodedA = a.rx.decodeAudio(pa[0].data), decodedB = b.rx.decodeAudio(pb[0].data);
        CHECK(decodedA && decodedB && *decodedA == pcm && *decodedB == pcm,
              "independent receivers decrypt exactly the same final source PCM");
        CHECK(be32(pa[0].data, 4) == 66150 + index && be32(pb[0].data, 4) == 66150 + index,
              "shared source frame has identical presentation RTP in both sessions");
        if (index == 0) { seqA = be16(pa[0].data, 2); seqB = be16(pb[0].data, 2); }
        CHECK(be16(pa[0].data, 2) == uint16_t(seqA + index / 352) &&
              be16(pb[0].data, 2) == uint16_t(seqB + index / 352),
              "each independent RTP sequence remains contiguous");
        CHECK(uint8_t(pa[0].data[1]) == (index == 0 ? 0xE0 : 0x60) &&
              uint8_t(pb[0].data[1]) == (index == 0 ? 0xE0 : 0x60), "first packet alone carries the start marker");
        decodedNonzero += uint64_t(std::count_if(pcm.begin(), pcm.end(), [](int16_t v) { return v != 0; }));
        decodedMeaningful += uint64_t(std::count_if(pcm.begin(), pcm.end(), [](int16_t v) { return v < -1 || v > 1; }));
        CHECK(a.sender->diagnostics().nonzeroSamplesSent == decodedNonzero &&
              b.sender->diagnostics().nonzeroSamplesSent == decodedNonzero,
              "transport signal counts refer to final emitted shared PCM");
        CHECK(a.sender->diagnostics().meaningfulSamplesSent == decodedMeaningful &&
              b.sender->diagnostics().meaningfulSamplesSent == decodedMeaningful,
              "meaningful transport signal is counted from independently decoded final PCM beyond mixer dither");
        CHECK(wire64(pa[0].data, pa[0].data.size() - 8) == wire64(pb[0].data, pb[0].data.size() - 8),
              "invalid/rejected sends have not advanced one sink's audio nonce");
    }
    CHECK(a.ring.availableRead() == aInput && b.ring.availableRead() == bInput,
          "coordinated sending never consumes disparate local rings or independent resamplers");
    sharedNow = start + std::chrono::seconds(1);
    a.sender->tick(); b.sender->tick();
    const auto sa = packetsOf(a, RaopUdp::Control), sb = packetsOf(b, RaopUdp::Control);
    REQUIRE(sa.size() == 1 && sb.size() == 1, "each armed session emits periodic sync");
    CHECK(sa[0].data == sb[0].data && wire64(sa[0].data, 8) == anchor + sampleNtp(1408) &&
          be32(sa[0].data, 4) == 1408 && be32(sa[0].data, 16) == 66150 + 1408,
          "later sync preserves common PCM-frame/NTP mapping despite staggered handshakes");
    CHECK(a.sender->diagnostics().audioFramesSent == 1408 && b.sender->diagnostics().audioFramesSent == 1408,
          "tick does not invent per-receiver silence or consume extra PCM");
    timingRequest(a); timingRequest(b);
    const auto ta = packetsOf(a, RaopUdp::Timing), tb = packetsOf(b, RaopUdp::Timing);
    REQUIRE(ta.size() == 1 && tb.size() == 1, "shared clock answers timing at one second");
    CHECK(ta[0].data == tb[0].data && wire64(ta[0].data, 16) == anchor + 0x100000000ULL &&
          wire64(ta[0].data, 24) == anchor + 0x100000000ULL,
          "timing replies and sync remain anchored to the same monotonic domain, independent of wall time");
    a.sender->stop(); b.sender->stop();
}

void testRejectedPacketsDoNotChangeGroupTimeline() {
    g_test = "GluSpot coordinated packet grammar, deadline and reset";
    Rig rig;
    const auto pcm = rampExpect(704);
    CHECK(!rig.sender->armCoordinatedTimeline(rig.now, 0xD000000000000000ULL) &&
          !rig.sender->sendCoordinatedAudio(0, pcm), "idle ordinary sender rejects group operations");
    rig.sender->setCoordinatedPlayback(true);
    CHECK(!rig.sender->sendCoordinatedAudio(0, pcm), "group selection alone cannot send before authentication");
    rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "packet validation receiver is ready");
    CHECK(!rig.sender->sendCoordinatedAudio(0, pcm) && !rig.sender->armCoordinatedTimeline(rig.now, 1, 0) &&
          !rig.sender->armCoordinatedTimeline(rig.now, 1, 88201), "unarmed and out-of-negotiated-latency cases are rejected");
    const auto start = rig.now;
    REQUIRE(rig.sender->armCoordinatedTimeline(start, 0xD000000000000000ULL, 11025),
            "negotiated minimum latency is accepted explicitly");
    rig.io.takeUdp();
    rig.sender->setCoordinatedPlayback(false);
    CHECK(rig.sender->diagnostics().coordinatedPlayback, "live mode cannot change underneath a group");
    CHECK(!rig.sender->sendCoordinatedAudio(352, pcm) &&
          !rig.sender->sendCoordinatedAudio(std::numeric_limits<uint64_t>::max(), pcm) &&
          !rig.sender->sendCoordinatedAudio(0, std::span<const int16_t>(pcm.data(), 703)),
          "skipped/overflowed frame index and malformed PCM are rejected");
    CHECK(rig.io.takeUdp().empty() && rig.sender->diagnostics().audioFramesSent == 0,
          "invalid calls do not emit data or advance the presentation timeline");
    rig.now += std::chrono::milliseconds(35);
    REQUIRE(rig.sender->sendCoordinatedAudio(0, pcm), "35 ms scheduler stall still permits bounded catch-up before presentation");
    const auto first = packetsOf(rig, RaopUdp::Audio);
    REQUIRE(first.size() == 1, "valid initial packet sends exactly once");
    CHECK(!rig.sender->sendCoordinatedAudio(0, pcm), "duplicate frame cannot become a later audio packet");
    REQUIRE(rig.sender->sendCoordinatedAudio(352, pcm), "contiguous catch-up packet follows without dropping source PCM");
    const auto second = packetsOf(rig, RaopUdp::Audio);
    REQUIRE(second.size() == 1, "one contiguous catch-up packet");
    CHECK(be16(second[0].data, 2) == uint16_t(be16(first[0].data, 2) + 1) &&
          wire64(second[0].data, second[0].data.size() - 8) == 0x0100000000000000ULL &&
          be32(second[0].data, 4) == 11025 + 352,
          "failed calls do not create RTP gaps or move the source-frame mapping");
    rig.now = start + std::chrono::seconds(1);
    CHECK(!rig.sender->sendCoordinatedAudio(704, pcm) && rig.io.takeUdp().empty() &&
          rig.sender->diagnostics().audioFramesSent == 704,
          "PCM already beyond its 250 ms presentation deadline is rejected without timeline advance");

    std::string request(8, '\0'); request[0] = char(0x80); request[1] = char(0xD5);
    const uint16_t lost = be16(first[0].data, 2);
    request[4] = char(lost >> 8); request[5] = char(lost & 255); request[7] = 1;
    rig.sender->onUdpDatagram(RaopUdp::Control, spanOf(request), {rig.peerIp, 6101});
    const auto repeated = packetsOf(rig, RaopUdp::Control);
    REQUIRE(repeated.size() == 1, "receiver can request coordinated packet retransmission");
    CHECK(repeated[0].data.substr(4) == first[0].data,
          "retransmission preserves original ciphertext, nonce, PCM and presentation timestamp");
    rig.sender->stop(); rig.exchange();
    CHECK(!rig.sender->diagnostics().coordinatedTimelineArmed && !rig.sender->sendCoordinatedAudio(704, pcm),
          "stopped member cannot emit another source packet");
    rig.rx = FakeReceiver{};
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "new group session authenticates");
    CHECK(!rig.sender->diagnostics().coordinatedTimelineArmed && rig.sender->diagnostics().audioFramesSent == 0 &&
          rig.sender->diagnostics().protocolLatencyFrames == 66150 && rig.io.takeUdp().empty(),
          "new session resets old arm/frame index/custom latency and waits for a new group barrier");
    REQUIRE(rig.sender->armCoordinatedTimeline(rig.now, 0xD111111111111111ULL), "new session accepts a different group anchor");
    rig.io.takeUdp();
    REQUIRE(rig.sender->sendCoordinatedAudio(0, pcm), "new session begins again at source frame zero");
    const auto fresh = packetsOf(rig, RaopUdp::Audio);
    REQUIRE(fresh.size() == 1, "new session has one first audio packet");
    CHECK(uint8_t(fresh[0].data[1]) == 0xE0 && be32(fresh[0].data, 4) == 66150 &&
          std::all_of(fresh[0].data.end() - 8, fresh[0].data.end(), [](char byte) { return byte == 0; }),
          "new receiver has fresh first marker, common RTP start and nonce zero");
    rig.sender->stop();
}

void testReadyBarrierRetainsFeedbackAndEventAcknowledgements() {
    g_test = "GluSpot ready group barrier keeps encrypted control/event session alive";
    Rig rig;
    rig.sender->setCoordinatedPlayback(true);
    rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "receiver is setup-ready");
    connectEvents(rig);
    gluspot::GroupFeedbackWatchdog watchdog;
    watchdog.start(rig.now);
    for (int tick = 1; tick <= 65; ++tick) {
        rig.advance(1000); rig.exchange();
        const auto diagnostics = rig.sender->diagnostics();
        CHECK(watchdog.healthy(rig.now, diagnostics.feedbackReplies, diagnostics.feedbackRejected),
              "successful authenticated feedback keeps a 65-second ready barrier healthy");
        if (tick % 5 == 0) {
            const auto seq = std::to_string(tick);
            rig.sender->onTcpData(RaopTcp::Event,
                spanOf(rig.rx.pushEvent("POST /command RTSP/1.0\r\nCSeq: " + seq + "\r\nContent-Length: 0\r\n\r\n")));
            CHECK(rig.rx.readEventReplies(rig.io.takeTcp(RaopTcp::Event)) ==
                  "RTSP/1.0 200 OK\r\nServer: AirTunes/550.10\r\nCSeq: " + seq + "\r\n\r\n",
                  "ready group receiver continues getting a minimal encrypted event acknowledgement");
        }
        CHECK(rig.io.takeUdp().empty(), "waiting group barrier emits no independent silence/audio or NTP sync");
    }
    const auto d = rig.sender->diagnostics();
    CHECK(d.feedbackSent == 32 && d.feedbackReplies == 32 && d.eventAcks == 13 && d.audioFramesSent == 0 &&
          !d.coordinatedTimelineArmed && rig.sender->active(),
          "barrier readiness preserves keep-alive without claiming a playing/synchronized group");
    rig.sender->stop();
}

void testGroupFeedbackBlackholeEndsEveryMember() {
    for (const bool armed : {false, true}) {
        g_test = armed ? "GluSpot armed group fails on silent feedback blackhole with TCP open"
                       : "GluSpot ready barrier fails on silent feedback blackhole with TCP open";
        Rig healthy, silent;
        gluspot::GroupFeedbackWatchdog healthyWatchdog, silentWatchdog;
        for (auto* rig : {&healthy, &silent}) {
            rig->sender->setCoordinatedPlayback(true);
            rig->sender->setPreserveReceiverVolume(true);
            runAp2Handshake(*rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
            REQUIRE(rig->launchedOk && *rig->launchedOk, "both group members authenticate independently");
            connectEvents(*rig);
        }
        healthyWatchdog.start(healthy.now); silentWatchdog.start(silent.now);
        for (auto* rig : {&healthy, &silent}) { rig->advance(2000); rig->exchange(); }
        CHECK(healthyWatchdog.healthy(healthy.now, healthy.sender->diagnostics().feedbackReplies, 0) &&
              silentWatchdog.healthy(silent.now, silent.sender->diagnostics().feedbackReplies, 0),
              "each member first acknowledges feedback before one becomes a blackhole");
        if (armed) {
            const auto pcm = rampExpect(704, 1123);
            for (auto* rig : {&healthy, &silent}) {
                REQUIRE(rig->sender->armCoordinatedTimeline(rig->now, 0xE011001100110011ULL) &&
                        rig->sender->sendCoordinatedAudio(0, pcm), "shared source PCM has reached both network transports");
                rig->io.takeUdp();
            }
        }
        bool failed = false;
        for (int second = 1; second <= 10; ++second) {
            healthy.advance(1000); healthy.exchange();
            silent.advance(1000);
            // The receiver consumes valid encrypted requests but its replies
            // never reach the sender. Neither control nor event TCP is closed.
            const auto output = silent.io.takeTcp(RaopTcp::Control);
            if (!output.empty()) (void)silent.rx.onControlBytes(output);
            silent.sender->onTcpData(RaopTcp::Event,
                spanOf(silent.rx.pushEvent("POST /command RTSP/1.0\r\nCSeq: " +
                    std::to_string(second) + "\r\nContent-Length: 0\r\n\r\n")));
            CHECK(!silent.rx.readEventReplies(silent.io.takeTcp(RaopTcp::Event)).empty(),
                  "encrypted event traffic still works while successful feedback is absent");
            const auto hd = healthy.sender->diagnostics(), sd = silent.sender->diagnostics();
            CHECK(healthyWatchdog.healthy(healthy.now, hd.feedbackReplies, hd.feedbackRejected),
                  "one healthy member cannot hide another member's failure");
            const auto alive = silentWatchdog.healthy(silent.now, sd.feedbackReplies, sd.feedbackRejected);
            CHECK(alive == (second < 10), "failure occurs at ten seconds since the last successful feedback");
            if (!alive) { failed = true; break; }
        }
        REQUIRE(failed && healthy.sender->active() && silent.sender->active() && silent.closedCount == 0,
                "bounded feedback detection works without a TCP close notification");
        const auto sd = silent.sender->diagnostics();
        CHECK(sd.feedbackReplies == 1 && sd.feedbackSent == 2 && sd.feedbackSkipped >= 4 && sd.eventAcks == 10,
              "requests, skipped requests and encrypted event acknowledgements never reset feedback liveness");
        CHECK(!armed || sd.meaningfulSamplesSent > 0, "previous meaningful PCM handoff never proves current receiver liveness");
        // The same all-member failure policy as the host closes every member,
        // rather than leaving a smaller group on an old presentation timeline.
        healthy.sender->stop(); silent.sender->stop();
        CHECK(!healthy.sender->active() && !silent.sender->active() &&
              !healthy.io.anyUdpOpen() && !silent.io.anyUdpOpen(),
              "one failed feedback watchdog releases all group transports");
        CHECK(!silentWatchdog.healthy(silent.now, sd.feedbackReplies + 1, sd.feedbackRejected),
              "late feedback cannot resurrect an already failed group");
    }
}

void testRejectedFeedbackCannotRefreshGroupWatchdog() {
    g_test = "GluSpot rejected feedback cannot keep a ready group member alive";
    Rig rig;
    rig.sender->setCoordinatedPlayback(true); rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "receiver is authenticated before rejected feedback");
    gluspot::GroupFeedbackWatchdog watchdog;
    watchdog.start(rig.now);
    for (int interval = 1; interval <= 5; ++interval) {
        rig.advance(2000);
        bool bad = false;
        auto stream = rig.io.takeTcp(RaopTcp::Control);
        auto plain = decryptFrames(rig.rx.ctrlIn, rig.rx.ctrlInCtr, stream, bad);
        const auto requests = parseRequests(plain);
        REQUIRE(!bad && requests.size() == 1 && requests.front().uri == "/feedback",
                "each authenticated feedback request reaches the fake receiver");
        const auto response = "RTSP/1.0 503 Service Unavailable\r\nCSeq: " + requests.front().h("cseq") +
            "\r\nContent-Length: 0\r\n\r\n";
        rig.sender->onTcpData(RaopTcp::Control, spanOf(encryptFrames(rig.rx.ctrlOut, rig.rx.ctrlOutCtr, response)));
        const auto diagnostics = rig.sender->diagnostics();
        CHECK(diagnostics.feedbackReplies == uint64_t(interval) && diagnostics.feedbackRejected == uint64_t(interval),
              "rejected replies are independently visible in protocol diagnostics");
        CHECK(watchdog.healthy(rig.now, diagnostics.feedbackReplies, diagnostics.feedbackRejected) == (interval < 5),
              "repeated rejected replies fail at ten seconds from setup readiness");
    }
    rig.sender->stop();
}

void testGroupArgumentIdentityAndCommandIsolation() {
    g_test = "GluSpot group argument and member-volume destination isolation";
    const std::vector<std::string> valid{"--group", "--music-pid", "1234", "--music-created-at", "456789",
        "--source-endpoint", "{Windows-Source}", "--source-id", "02:47:4C:55:53:50",
        "--receiver-id", "homepod-one", "--host", "192.168.1.2", "--port", "7000",
        "--device-id", "D001", "--name", "Living Room",
        "--receiver-id", "homepod-two", "--host", "192.168.1.3", "--port", "7000",
        "--device-id", "D002", "--name", "Studio", "--local-endpoint", "{Bluetooth-Output}"};
    const auto parsed = gluspot::parseGroupArguments(valid);
    CHECK(parsed.musicPid == 1234 && parsed.musicCreatedAt == 456789 && parsed.receivers.size() == 2 &&
          parsed.receivers[1].id == "homepod-two" && parsed.localEndpoint == "{Bluetooth-Output}",
          "validated CLI preserves ordered receiver identities and exact process creation token");
    auto rejects = [](const std::vector<std::string>& arguments) {
        try { gluspot::parseGroupArguments(arguments); return false; }
        catch (const std::invalid_argument&) { return true; }
    };
    for (const auto& [key, badValue] : std::vector<std::pair<std::string, std::string>>{
            {"--music-pid", "0"}, {"--music-pid", "4294967296"}, {"--music-pid", "+1"},
            {"--music-created-at", "-1"}, {"--music-created-at", "18446744073709551616"},
            {"--host", "example.com"}, {"--host", "224.1.2.3"}, {"--host", "0.0.0.0"},
            {"--host", "192.168.01.2"}, {"--port", "70000"}, {"--port", "12suffix"},
            {"--source-id", "bad mac"}, {"--name", "injected\r\nRTSP"},
            {"--local-endpoint", "{windows-source}"}}) {
        auto args = valid;
        const auto field = std::find(args.begin(), args.end(), key);
        *(field + 1) = badValue;
        CHECK(rejects(args), "bad identity/address/text/volume endpoint rejects before any Windows or network work");
    }
    auto duplicated = valid; duplicated.insert(duplicated.end(), {"--music-pid", "1234"});
    CHECK(rejects(duplicated), "duplicate singleton option never changes verified process identity");
    auto duplicateHost = valid;
    const auto secondHost = std::find(std::find(duplicateHost.begin(), duplicateHost.end(), "--host") + 1,
                                     duplicateHost.end(), "--host");
    *(secondHost + 1) = "192.168.1.2";
    CHECK(rejects(duplicateHost), "same physical network endpoint cannot be two group outputs");
    auto absent = valid; absent.erase(absent.begin() + 13, absent.begin() + 15);
    CHECK(rejects(absent), "missing member field is not replaced with guessed sender defaults");
    CHECK(!gluspot::groupVolumeCommand("{\"command\":\"set_volume\",\"volume\":0.5}", 2),
          "legacy unnamed volume command cannot control an arbitrary group receiver");
    const auto command = gluspot::groupVolumeCommand("{\"command\":\"set_volume\",\"receiver\":1,\"volume\":3.5e-1}", 2);
    CHECK(command && command->receiver == 1 && std::abs(command->volume - .35) < 1e-9,
          "canonical member-index JSON explicitly targets its current receiver");
    for (const auto& line : {
            "{\"command\":\"set_volume\",\"receiver\":2,\"volume\":0.5}",
            "{\"command\":\"set_volume\",\"receiver\":-1,\"volume\":0.5}",
            "{\"command\":\"set_volume\",\"receiver\":1.0,\"volume\":0.5}",
            "{\"command\":\"set_volume\",\"receiver\":true,\"volume\":0.5}",
            "{\"command\":\"set_volume\",\"receiver\":1,\"receiver\":0,\"volume\":0.5}",
            "{\"command\":\"set_volume\",\"receiver\":0,\"volume\":NaN}",
            "{\"command\":\"set_volume\",\"receiver\":0,\"volume\":1.001}",
            "{\"command\":\"set_volume\",\"receiver\":0,\"volume\":1e999}",
            "{\"command\":\"set_volume\",\"receiver\":0,\"volume\":{\"level\":0.5}}"})
        CHECK(!gluspot::groupVolumeCommand(line, 2), "invalid destination/value/duplicate/nested grammar cannot become a receiver command");
    CHECK(!gluspot::groupVolumeCommand(std::string(1025, ' '), 2), "command parsing work remains bounded at 1024 bytes");
}

void testSharedHistoryArbitraryRenderRangesAndRetention() {
    g_test = "GluSpot shared PCM history without a second capture-ring consumer";
    auto history = std::make_unique<gluspot::GroupPcmHistory>();
    const auto first = rampExpect(704, 17), next = rampExpect(704, 222);
    std::array<int16_t, 14> out{};
    CHECK(!history->pull(0, out), "unpublished timeline range is unavailable");
    REQUIRE(history->append(0, first) && history->append(352, next), "contiguous final PCM packets publish once");
    REQUIRE(history->pull(349, out), "renderer can pull an arbitrary unaligned range across packet boundaries");
    CHECK(std::equal(out.begin(), out.begin() + 6, first.end() - 6) &&
          std::equal(out.begin() + 6, out.end(), next.begin()),
          "render history preserves exact source samples across the network packet boundary");
    CHECK(!history->append(352, next) && !history->append(1056, next) &&
          !history->append(704, std::span<const int16_t>(next.data(), 703)),
          "duplicate/skipped/malformed publish cannot rewrite the source timeline");
    CHECK(!history->pull(702, out) && !history->pull(UINT64_MAX, out) &&
          !history->pull(0, std::span<int16_t>(out.data(), 13)), "partial/future/overflowed render reads fail boundedly");
    uint64_t head = 704;
    const auto packet = rampExpect(704, -721);
    while (head <= gluspot::GroupPcmHistory::capacityFrames + 704) {
        REQUIRE(history->append(head, packet), "history wraps with contiguous source packets");
        head += 352;
    }
    CHECK(!history->pull(0, out), "only the latest three seconds are retained, not unlimited music history");
    REQUIRE(history->pull(head - 352, out), "most recent source range remains available after wrap");
    CHECK(std::equal(out.begin(), out.end(), packet.begin()), "wrapped history still returns exact final source PCM");
}

void testSharedCaptureClockConditionerContinuityAndDrift() {
    g_test = "GluSpot once-per-group source clock correction";
    gluspot::GroupPcmConditioner conditioner;
    std::vector<int16_t> initial(14000);
    for (size_t frame = 0; frame < initial.size() / 2; ++frame) {
        initial[frame * 2] = int16_t(frame);
        initial[frame * 2 + 1] = int16_t(-int(frame));
    }
    REQUIRE(conditioner.feed(initial), "shared conditioner accepts one startup source lead");
    std::array<int16_t, 704> output{};
    conditioner.packet(output);
    CHECK(output[0] == 0 && output[2] == 1 && output[4] == 2 && output[703] < 0,
          "shared clock correction interpolates the real source continuously in both channels");
    CHECK(conditioner.ratio() > 1 && conditioner.ratio() <= 1.00001 && conditioner.paddedFrames() == 0,
          "initial correction slews gently without frame dropping or padding");
    const auto firstPacketEnd = output[702];
    conditioner.packet(output);
    CHECK(output[0] >= firstPacketEnd && output[0] - firstPacketEnd <= 2,
          "subsequent packet continues the same source phase rather than resetting resampling per receiver");
    CHECK(!conditioner.feed(std::span<const int16_t>(initial.data(), 3)) &&
          !conditioner.feed(std::vector<int16_t>(88200)), "misaligned or unbounded source feeds fail closed");

    gluspot::GroupPcmConditioner drifting;
    const std::vector<int16_t> seed(gluspot::GroupPcmConditioner::targetQueueFrames * 2, 1200);
    REQUIRE(drifting.feed(seed), "drift model primes the common target queue");
    uint64_t extraFrames = 0;
    // Model a source about 0.057% faster than the group clock. One shared correction
    // must consume that rate without growing an unbounded FIFO or losing data.
    for (size_t packet = 0; packet < 14000; ++packet) {
        const auto extra = packet % 5 == 0 ? 1u : 0u;
        extraFrames += extra;
        REQUIRE(drifting.feed(std::vector<int16_t>((352 + extra) * 2, 1200)), "drifting source remains within bounded conditioner");
        drifting.packet(output);
    }
    CHECK(extraFrames == 2800 && drifting.queuedFrames() < seed.size() / 2 + 1400 &&
          drifting.queuedFrames() > seed.size() / 2 && drifting.paddedFrames() == 0,
          "shared clock correction absorbs sustained faster source without whole-frame jumps, starvation or unbounded queue growth");
    CHECK(drifting.ratio() > 1 && drifting.ratio() <= 1.002 &&
          std::all_of(output.begin(), output.end(), [](int16_t value) { return value == 1200; }),
          "bounded correction preserves a constant audio source exactly for every output member");
    gluspot::GroupPcmConditioner empty;
    empty.packet(output);
    CHECK(empty.paddedFrames() == 352 && std::all_of(output.begin(), output.end(), [](int16_t value) { return value == 0; }),
          "a paused source creates one common explicit silent packet, not independent receiver silence");
    REQUIRE(empty.feed(std::vector<int16_t>(2000, -2000)), "source can resume after paused capture");
    empty.packet(output);
    CHECK(std::all_of(output.begin(), output.end(), [](int16_t value) { return value == -2000; }),
          "resume reads new source PCM continuously without old audio or synthetic timeline seek");
}

void testThreeHourSharedCaptureClockDrift() {
    g_test = "GluSpot three simulated hours of shared capture-clock drift";
    gluspot::GroupPcmConditioner conditioner;
    const std::vector<int16_t> lead(gluspot::GroupPcmConditioner::targetQueueFrames * 2, 1800);
    REQUIRE(conditioner.feed(lead), "long-running actual conditioner starts with one bounded capture lead");
    std::array<int16_t, 706> input;
    input.fill(1800);
    std::array<int16_t, 704> output{};
    constexpr uint64_t hourFrames = 44100ULL * 3600;
    constexpr uint64_t totalFrames = hourFrames * 3;
    int64_t captureFraction = 0;
    uint64_t frameIndex = 0, acceptedFrames = lead.size() / 2;
    size_t minimumQueue = lead.size() / 2, maximumQueue = minimumQueue;
    bool fed = true, slewBounded = true, rateBounded = true, audioPreserved = true;
    double previousRatio = conditioner.ratio();
    const auto began = Clock::now();
    for (; frameIndex + 352 <= totalFrames; frameIndex += 352) {
        // Exact integer-clock simulation, rather than rounding each packet's
        // ppm error away: one hour +1700 ppm, one -1700, then one +1000.
        const auto hour = frameIndex / hourFrames;
        const int ppm = hour == 0 ? 1700 : hour == 1 ? -1700 : 1000;
        captureFraction += int64_t(352) * ppm;
        const int64_t extra = captureFraction / 1000000;
        captureFraction -= extra * 1000000;
        const size_t sourceFrames = size_t(352 + extra);
        if (!conditioner.feed(std::span<const int16_t>(input.data(), sourceFrames * 2))) { fed = false; break; }
        acceptedFrames += sourceFrames;
        conditioner.packet(output);
        const auto ratio = conditioner.ratio();
        const auto queued = conditioner.queuedFrames();
        minimumQueue = std::min(minimumQueue, queued); maximumQueue = std::max(maximumQueue, queued);
        slewBounded = slewBounded && std::abs(ratio - previousRatio) <= .00001000001;
        rateBounded = rateBounded && ratio >= .998 && ratio <= 1.002;
        audioPreserved = audioPreserved && output.front() == 1800 && output.back() == 1800;
        previousRatio = ratio;
        if (frameIndex % (352 * 7517ULL) == 0 && frameIndex % hourFrames > 44100ULL * 300) {
            CHECK(std::abs(ratio - (1.0 + double(ppm) / 1000000)) < .000005,
                  "one shared conditioner converges to the actual capture clock in either drift direction");
        }
    }
    CHECK(fed && frameIndex >= totalFrames - 352 && acceptedFrames > frameIndex,
          "three hours consume actual PCM packets with exact positive/negative source-clock error");
    CHECK(minimumQueue > 4000 && maximumQueue < 9000 && conditioner.paddedFrames() == 0,
          "multi-hour rate reversals retain bounded real source lead without starvation or whole-frame skips");
    CHECK(slewBounded && rateBounded && audioPreserved,
          "clock correction stays continuous and within 2000 ppm while preserving the actual constant source");
    std::printf("Three-hour shared conditioner: queue %zu..%zu frames; wall %.3f s\n", minimumQueue, maximumQueue,
        std::chrono::duration<double>(Clock::now() - began).count());
}

void testTwoReceiversPauseSilenceResumeOnOneTimeline() {
    for (const bool captureStarves : {false, true}) {
        g_test = captureStarves ? "GluSpot capture starvation resumes on the same two-receiver timeline"
                               : "GluSpot captured pause silence resumes on the same two-receiver timeline";
        Clock::time_point now{std::chrono::seconds(2000)};
        Rig a, b;
        for (auto* rig : {&a, &b}) {
            rig->sender->setClock([&now] { return now; });
            rig->sender->setCoordinatedPlayback(true); rig->sender->setPreserveReceiverVolume(true);
            runAp2Handshake(*rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
            REQUIRE(rig->launchedOk && *rig->launchedOk, "pause/resume receivers each authenticate once");
            rig->fillRingRamp(2500, rig == &a ? 27 : 991);
        }
        const auto ringA = a.ring.availableRead(), ringB = b.ring.availableRead();
        const auto start = now;
        const uint64_t anchor = 0xE033221144556677ULL;
        for (auto* rig : {&a, &b}) {
            REQUIRE(rig->sender->armCoordinatedTimeline(start, anchor), "one shared timeline is armed once before music");
            rig->io.takeUdp();
        }
        gluspot::GroupPcmConditioner conditioner;
        const std::vector<int16_t> lead(gluspot::GroupPcmConditioner::targetQueueFrames * 2, 1200);
        REQUIRE(conditioner.feed(lead), "the shared music source has its initial capture lead");
        auto history = std::make_unique<gluspot::GroupPcmHistory>();
        std::array<int16_t, 704> input{}, finalPcm{}, localPcm{};
        uint16_t firstA = 0, firstB = 0;
        size_t silentPackets = 0, resumedPackets = 0;
        constexpr size_t musicPackets = 125, pausePackets = 250, totalPackets = 500;
        for (size_t packet = 0; packet < totalPackets; ++packet) {
            const uint64_t frameIndex = packet * 352;
            now = start + std::chrono::nanoseconds(frameIndex * 1000000000ULL / 44100);
            for (auto* rig : {&a, &b}) { rig->sender->tick(); rig->exchange(); }
            auto clockPacketsA = a.io.takeUdp(), clockPacketsB = b.io.takeUdp();
            CHECK(std::none_of(clockPacketsA.begin(), clockPacketsA.end(), [](const auto& datagram) { return datagram.s == RaopUdp::Audio; }) &&
                  std::none_of(clockPacketsB.begin(), clockPacketsB.end(), [](const auto& datagram) { return datagram.s == RaopUdp::Audio; }),
                  "independent receiver ticks cannot invent PCM during music, pause or resume");
            clockPacketsA.erase(std::remove_if(clockPacketsA.begin(), clockPacketsA.end(),
                [](const auto& datagram) { return datagram.s != RaopUdp::Control; }), clockPacketsA.end());
            clockPacketsB.erase(std::remove_if(clockPacketsB.begin(), clockPacketsB.end(),
                [](const auto& datagram) { return datagram.s != RaopUdp::Control; }), clockPacketsB.end());
            CHECK(clockPacketsA.size() == clockPacketsB.size(), "both paused/resumed receivers retain the same sync cadence");
            if (!clockPacketsA.empty()) {
                REQUIRE(clockPacketsA.size() == 1, "one periodic shared sync is emitted at a due host tick");
                CHECK(clockPacketsA.front().data == clockPacketsB.front().data &&
                      wire64(clockPacketsA.front().data, 8) == anchor + sampleNtp(frameIndex) &&
                      be32(clockPacketsA.front().data, 4) == frameIndex,
                      "periodic sync maps the same continuous music/silence/resume source index to the original NTP origin");
            }
            const bool paused = packet >= musicPackets && packet < musicPackets + pausePackets;
            input.fill(paused ? 0 : packet < musicPackets ? 1200 : 3200);
            if (!paused || !captureStarves)
                REQUIRE(conditioner.feed(input), "bounded shared capture accepts the actual source packet or captured silence");
            conditioner.packet(finalPcm);
            REQUIRE(history->append(frameIndex, finalPcm) && history->pull(frameIndex, localPcm),
                    "local render history receives the same single conditioned PCM packet");
            REQUIRE(a.sender->sendCoordinatedAudio(frameIndex, finalPcm) && b.sender->sendCoordinatedAudio(frameIndex, finalPcm),
                    "each output receives a contiguous source index including pause silence");
            const auto pa = packetsOf(a, RaopUdp::Audio), pb = packetsOf(b, RaopUdp::Audio);
            REQUIRE(pa.size() == 1 && pb.size() == 1, "pause/resume fan-out emits exactly one packet per receiver");
            const auto decodedA = a.rx.decodeAudio(pa.front().data), decodedB = b.rx.decodeAudio(pb.front().data);
            REQUIRE(decodedA && decodedB, "both differently keyed receivers independently decode emitted PCM");
            CHECK(*decodedA == *decodedB && std::equal(decodedA->begin(), decodedA->end(), localPcm.begin()),
                  "both network sessions and local history contain the identical final PCM on pause/resume");
            if (!packet) { firstA = be16(pa.front().data, 2); firstB = be16(pb.front().data, 2); }
            CHECK(be16(pa.front().data, 2) == uint16_t(firstA + packet) && be16(pb.front().data, 2) == uint16_t(firstB + packet) &&
                  be32(pa.front().data, 4) == 66150 + frameIndex && be32(pb.front().data, 4) == 66150 + frameIndex,
                  "pause/resume preserves continuous sequence and the original shared presentation mapping");
            CHECK(uint8_t(pa.front().data[1]) == (packet ? 0x60 : 0xE0) && uint8_t(pb.front().data[1]) == (packet ? 0x60 : 0xE0),
                  "resume does not re-mark a session or invent a new RTP origin");
            if (packet >= musicPackets + 30 && packet < musicPackets + pausePackets) {
                CHECK(std::all_of(decodedA->begin(), decodedA->end(), [](int16_t value) { return value == 0; }),
                      "after draining the bounded music lead both physical packet payloads are actual silence");
                ++silentPackets;
            }
            if (packet >= musicPackets + pausePackets + 30) {
                CHECK(std::all_of(decodedA->begin(), decodedA->end(), [](int16_t value) { return value == 3200; }),
                      "resumed music reaches every output without reconnecting or replaying pre-pause music");
                ++resumedPackets;
            }
        }
        CHECK(silentPackets == 220 && resumedPackets == 95 && a.launchedCount == 1 && b.launchedCount == 1 &&
              a.sender->active() && b.sender->active() && a.ring.availableRead() == ringA && b.ring.availableRead() == ringB,
              "one original session survives music/silence/resume without per-receiver ring consumption");
        CHECK(captureStarves ? conditioner.paddedFrames() > 0 : conditioner.paddedFrames() == 0,
              "explicit shared padding is distinguishable from genuinely captured pause silence");
        a.sender->stop(); b.sender->stop();
    }
}

void testTwoReceiverBoundedHostCatchupPreservesSourcePackets() {
    g_test = "GluSpot bounded host catch-up preserves both receiver PCM timelines";
    Clock::time_point now{std::chrono::seconds(3000)};
    Rig a, b;
    for (auto* rig : {&a, &b}) {
        rig->sender->setClock([&now] { return now; });
        rig->sender->setCoordinatedPlayback(true); rig->sender->setPreserveReceiverVolume(true);
        runAp2Handshake(*rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
        REQUIRE(rig->launchedOk && *rig->launchedOk && rig->sender->armCoordinatedTimeline(now, 0xE044556677889900ULL),
                "catch-up receivers start on one authenticated timeline");
        rig->io.takeUdp();
    }
    const auto start = now;
    gluspot::GroupPcmConditioner conditioner;
    const auto lead = rampExpect(gluspot::GroupPcmConditioner::targetQueueFrames * 2, 24);
    REQUIRE(conditioner.feed(lead), "actual bounded shared PCM queue is primed before the host stall");
    uint64_t nextFrame = 0;
    size_t packets = 0;
    uint16_t firstA = 0, firstB = 0;
    auto sendNext = [&] {
        std::array<int16_t, 704> pcm{};
        conditioner.packet(pcm);
        REQUIRE(a.sender->sendCoordinatedAudio(nextFrame, pcm) && b.sender->sendCoordinatedAudio(nextFrame, pcm),
                "late but not expired source packet reaches every member contiguously");
        const auto pa = packetsOf(a, RaopUdp::Audio), pb = packetsOf(b, RaopUdp::Audio);
        REQUIRE(pa.size() == 1 && pb.size() == 1, "catch-up still emits exactly one packet per source index per member");
        const auto decodedA = a.rx.decodeAudio(pa.front().data), decodedB = b.rx.decodeAudio(pb.front().data);
        CHECK(decodedA && decodedB && *decodedA == *decodedB && std::equal(decodedA->begin(), decodedA->end(), pcm.begin()),
              "catch-up shares the actual conditioned source packet, without independent per-member resampling");
        if (!packets) { firstA = be16(pa.front().data, 2); firstB = be16(pb.front().data, 2); }
        CHECK(be16(pa.front().data, 2) == uint16_t(firstA + packets) && be16(pb.front().data, 2) == uint16_t(firstB + packets) &&
              be32(pa.front().data, 4) == 66150 + nextFrame && be32(pb.front().data, 4) == 66150 + nextFrame,
              "same-time catch-up burst preserves original sequential packet and presentation indices");
        ++packets; nextFrame += 352;
    };
    sendNext();
    now += std::chrono::milliseconds(35);
    for (auto* rig : {&a, &b}) { rig->sender->tick(); rig->exchange(); }
    CHECK(a.io.takeUdp().empty() && b.io.takeUdp().empty(), "a 35 ms host stall cannot trigger any independent sender pacer");
    const uint64_t dueFrame = 35ULL * 44100 / 1000;
    REQUIRE(conditioner.feed(std::vector<int16_t>(size_t(dueFrame) * 2, 3300)),
            "capture accumulated during the stall stays within the shared queue bound");
    int burst = 0;
    while (nextFrame <= dueFrame && burst++ < 8) sendNext();
    CHECK(packets == 5 && burst == 4 && nextFrame > dueFrame && conditioner.paddedFrames() == 0,
          "four retained source packets catch up within the host's eight-packet budget without skips or padding");
    const auto beforeA = a.sender->diagnostics().audioFramesSent, beforeB = b.sender->diagnostics().audioFramesSent;
    now = start + std::chrono::seconds(2);
    const auto pcm = rampExpect(704, 311);
    CHECK(!a.sender->sendCoordinatedAudio(nextFrame, pcm) && !b.sender->sendCoordinatedAudio(nextFrame, pcm) &&
          a.sender->diagnostics().audioFramesSent == beforeA && b.sender->diagnostics().audioFramesSent == beforeB &&
          a.io.takeUdp().empty() && b.io.takeUdp().empty(),
          "a source packet already past presentation is rejected for the entire group without rewriting the timeline");
    a.sender->stop(); b.sender->stop();
}

void testDitherTransportNeverCertifiesMusic() {
    g_test = "GluSpot receiver transport distinguishes music from mixer dither";
    Rig rig;
    rig.sender->setCoordinatedPlayback(true); rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk && rig.sender->armCoordinatedTimeline(rig.now, 0xE000000000000000ULL),
            "dither receiver authenticates and receives a timeline");
    rig.io.takeUdp();
    std::array<int16_t, 704> dither;
    for (size_t index = 0; index < dither.size(); ++index) dither[index] = index % 2 ? -1 : 1;
    REQUIRE(rig.sender->sendCoordinatedAudio(0, dither), "one-LSB mixer dither is valid PCM, not an invented silence claim");
    auto packets = packetsOf(rig, RaopUdp::Audio);
    REQUIRE(packets.size() == 1, "dither is independently encrypted and sent");
    auto pcm = rig.rx.decodeAudio(packets[0].data);
    CHECK(pcm && std::equal(pcm->begin(), pcm->end(), dither.begin()) &&
          rig.sender->diagnostics().nonzeroSamplesSent == 704 && rig.sender->diagnostics().meaningfulSamplesSent == 0,
          "wire PCM has nonzero dither while meaningful transport evidence remains zero");
    dither[0] = -32768; dither[1] = 32767; dither[2] = 2; dither[3] = -2;
    rig.now += std::chrono::milliseconds(8);
    REQUIRE(rig.sender->sendCoordinatedAudio(352, dither), "music-bearing samples share the same source timeline");
    packets = packetsOf(rig, RaopUdp::Audio);
    REQUIRE(packets.size() == 1, "second actual packet sends once");
    pcm = rig.rx.decodeAudio(packets[0].data);
    CHECK(pcm && uint64_t(std::count_if(pcm->begin(), pcm->end(), [](int16_t value) { return value < -1 || value > 1; })) == 4 &&
          rig.sender->diagnostics().meaningfulSamplesSent == 4, "all actual meaningful final samples count once without counting mixer dither");
    rig.sender->stop(); rig.exchange(); rig.rx = FakeReceiver{};
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    CHECK(rig.sender->diagnostics().meaningfulSamplesSent == 0, "new receiver session cannot inherit music evidence");
    rig.sender->stop();
}
} // namespace

int main() {
    testStaggeredReceiversSharePcmAndPresentationClock();
    testRejectedPacketsDoNotChangeGroupTimeline();
    testReadyBarrierRetainsFeedbackAndEventAcknowledgements();
    testGroupFeedbackBlackholeEndsEveryMember();
    testRejectedFeedbackCannotRefreshGroupWatchdog();
    testGroupArgumentIdentityAndCommandIsolation();
    testSharedHistoryArbitraryRenderRangesAndRetention();
    testSharedCaptureClockConditionerContinuityAndDrift();
    testThreeHourSharedCaptureClockDrift();
    testTwoReceiversPauseSilenceResumeOnOneTimeline();
    testTwoReceiverBoundedHostCatchupPreservesSourcePackets();
    testDitherTransportNeverCertifiesMusic();
    std::printf("GluSpot group sender: %d/%d checks passed\n", g_checks - g_failures, g_checks);
    return g_failures ? 1 : 0;
}
