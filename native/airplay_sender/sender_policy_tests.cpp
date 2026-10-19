// GluSpot policy checks reuse the upstream's real HAP fake receiver rather
// than implementing a second, potentially divergent cryptographic test peer.
#define main upstreamCoreTestMain
#include "../../third_party/airplay2-sender-cpp/test/core_tests.cpp"
#undef main
#include <cmath>
#include <fstream>
#include <limits>
#include <stdexcept>

namespace {
size_t volumeRequests(const FakeReceiver& receiver);

struct AlacFixture {
    bool coordinated = false;
    uint32_t profile = 0;
    std::vector<int16_t> pcm;
    std::string payload;
    size_t datagramBytes = 0;
};

// Obtain the real production encoder's output through its public sender API.
// Only authentication/decryption uses the fake peer; an external standard
// decoder can consume the returned payload without our test decoder.
std::vector<AlacFixture> actualAlacFixtures() {
    std::vector<AlacFixture> fixtures;
    for (const bool coordinated : {false, true}) {
        Rig rig;
        rig.sender->setPreserveReceiverVolume(true);
        rig.sender->setStrictReceiverAuth(true);
        rig.sender->setCoordinatedPlayback(coordinated);
        runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
        if (!rig.launchedOk || !*rig.launchedOk)
            throw std::runtime_error("Synthetic ALAC fixture authentication failed");
        if (coordinated && !rig.sender->armCoordinatedTimeline(rig.now, 0xEC110234ABCDEF12ULL))
            throw std::runtime_error("Synthetic ALAC fixture timeline failed");
        rig.io.takeUdp();
        for (uint32_t profile = 0; profile < 4; ++profile) {
            std::vector<int16_t> pcm(704, 0);
            for (size_t frame = 0; frame < 352; ++frame) {
                if (profile == 1) {
                    pcm[frame * 2] = frame % 2 ? 32767 : -32768;
                    pcm[frame * 2 + 1] = frame % 2 ? -32768 : 32767;
                } else if (profile == 2) {
                    pcm[frame * 2] = int16_t(int(frame) * 79 - 14000);
                    pcm[frame * 2 + 1] = int16_t(13000 - int(frame) * 73);
                } else if (profile == 3) {
                    const double phase = double(frame) * 6.2831853071795864769 / 44100.0;
                    pcm[frame * 2] = int16_t(std::lround(12000.0 * std::sin(phase * 997.0)));
                    pcm[frame * 2 + 1] = int16_t(std::lround(10000.0 * std::sin(phase * 631.0)));
                }
            }
            if (coordinated) {
                if (!rig.sender->sendCoordinatedAudio(uint64_t(profile) * 352, pcm))
                    throw std::runtime_error("Synthetic coordinated PCM was rejected");
            } else {
                if (!rig.ring.tryPush(pcm)) throw std::runtime_error("Synthetic PCM ring is full");
                rig.advance(8);
            }
            const auto datagrams = rig.io.takeUdp();
            const auto packet = std::find_if(datagrams.begin(), datagrams.end(),
                [](const auto& datagram) { return datagram.s == RaopUdp::Audio; });
            if (packet == datagrams.end() || std::count_if(datagrams.begin(), datagrams.end(),
                [](const auto& datagram) { return datagram.s == RaopUdp::Audio; }) != 1 || packet->data.size() < 36)
                throw std::runtime_error("Synthetic sender must emit exactly one encrypted packet");
            const auto& wire = packet->data;
            const Bytes aad(wire.begin() + 4, wire.begin() + 12);
            const Bytes nonce(wire.end() - 8, wire.end());
            const Bytes ciphertext(wire.begin() + 12, wire.end() - 8);
            const auto decrypted = airplay::chacha20Poly1305Decrypt(rig.rx.audioKey, nonce, ciphertext, aad);
            if (!decrypted) throw std::runtime_error("Synthetic real sender packet did not authenticate");
            fixtures.push_back({coordinated, profile, std::move(pcm), strOf(*decrypted), wire.size()});
        }
        rig.sender->stop();
        rig.exchange();
    }
    return fixtures;
}

void testExplicitAlacFrameLength() {
    g_test = "GluSpot ALAC explicit packet length independent of decoder cookie";
    const auto fixtures = actualAlacFixtures();
    REQUIRE(fixtures.size() == 8, "standalone and coordinated sender emit four synthetic profiles each");
    for (const auto& fixture : fixtures) {
        REQUIRE(fixture.payload.size() == 1416, "uncompressed packet adds exactly one 32-bit frame count");
        CHECK((uint8_t(fixture.payload[2]) & 0x10u) != 0,
              "real encoder sets hasSize instead of relying on receiver cookie default");
        CHECK(fixture.datagramBytes == 12 + 1416 + 16 + 8 && fixture.datagramBytes + 28 <= 1500,
              "encrypted RTP plus IPv4/UDP headers stays below the ordinary 1500-byte MTU");
        for (const int cookieFrames : {352, 4096}) {
            const auto decoded = decodeAlacUncompressed(fixture.payload, cookieFrames);
            CHECK(decoded && *decoded == fixture.pcm,
                  "explicit count overrides either 352 or 4096 cookie without changing signed stereo PCM");
        }
    }
}

void exportActualAlacFixtures(const char* path) {
    const auto fixtures = actualAlacFixtures();
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    if (!file) throw std::runtime_error("Cannot open the requested synthetic fixture path");
    file.write("GLUSPOT_ALAC_V1\n", 16);
    auto number = [&file](uint32_t value) {
        const char bytes[]{char(value >> 24), char(value >> 16), char(value >> 8), char(value)};
        file.write(bytes, 4);
    };
    number(uint32_t(fixtures.size()));
    for (const auto& fixture : fixtures) {
        number(fixture.coordinated ? 1u : 0u); number(fixture.profile);
        number(uint32_t(fixture.pcm.size())); number(uint32_t(fixture.payload.size()));
        number(uint32_t(fixture.datagramBytes));
        for (const auto sample : fixture.pcm) {
            const char bytes[]{char(uint16_t(sample)), char(uint16_t(sample) >> 8)};
            file.write(bytes, 2);
        }
        file.write(fixture.payload.data(), std::streamsize(fixture.payload.size()));
    }
    if (!file) throw std::runtime_error("Cannot write the synthetic ALAC fixture");
}

void testActualNonzeroAudioSendEvidence() {
    g_test = "GluSpot final nonzero PCM transport evidence";
    Rig rig;
    rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "nonzero evidence test launches AP2");
    rig.io.takeUdp();
    CHECK(rig.sender->diagnostics().nonzeroSamplesSent == 0,
          "authenticated handshake alone does not prove sound-bearing audio");
    uint64_t decodedNonzero = 0;
    auto checkPacket = [&] {
        const auto datagrams = rig.io.takeUdp();
        size_t audioPackets = 0;
        for (const auto& packet : datagrams) {
            if (packet.s != RaopUdp::Audio) continue;
            ++audioPackets;
            const auto pcm = rig.rx.decodeAudio(packet.data);
            REQUIRE(pcm.has_value(), "receiver independently decrypts ALAC audio");
            decodedNonzero += uint64_t(std::count_if(pcm->begin(), pcm->end(),
                [](int16_t sample) { return sample != 0; }));
        }
        CHECK(audioPackets == 1, "one pacer step hands one encrypted audio datagram to transport");
        CHECK(rig.sender->diagnostics().nonzeroSamplesSent == decodedNonzero,
              "sender evidence equals the independently decoded wire PCM");
    };
    rig.advance(8);
    checkPacket();
    CHECK(rig.sender->diagnostics().audioFramesSent == 352 && decodedNonzero == 0,
          "empty-ring padding advances timeline but cannot prove nonzero audio");

    const std::vector<int16_t> zeros(352 * 2, 0);
    REQUIRE(rig.ring.tryPush(zeros), "zero-valued source packet enters ring");
    rig.advance(8);
    checkPacket();
    CHECK(decodedNonzero == 0, "actual zero-valued source PCM does not count as sound");

    auto mixed = zeros;
    mixed[0] = -32768; mixed[1] = 32767; mixed[17] = 1; mixed[31] = -1;
    REQUIRE(rig.ring.tryPush(mixed), "nonzero source packet enters ring");
    CHECK(rig.sender->diagnostics().nonzeroSamplesSent == 0,
          "queued input does not prove it has been sent");
    rig.advance(8);
    checkPacket();
    CHECK(decodedNonzero == 4, "both full-scale and tiny final samples count without counting zeros");
    rig.advance(8);
    checkPacket();
    CHECK(decodedNonzero == 4, "later padding retains historical count without fabricating fresh sound");
    rig.sender->stop();
    rig.exchange();
    CHECK(rig.sender->diagnostics().nonzeroSamplesSent == 4,
          "stopping retains final bounded evidence for diagnostics");

    rig.rx = FakeReceiver{};
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "a new AP2 session launches");
    CHECK(rig.sender->diagnostics().nonzeroSamplesSent == 0,
          "new session cannot inherit another receiver's nonzero evidence");
    rig.io.takeUdp();
    decodedNonzero = 0;
    rig.advance(8);
    checkPacket();
    CHECK(decodedNonzero == 0, "restarted empty session remains explicitly silent");

    // 48 kHz input frame 1 has a one-LSB impulse. The 44.1 kHz lerp samples
    // frame 0 at phase 0 and frame 1 at ~1.088, truncating the latter to zero.
    // Input evidence is nonzero; the actual emitted packet must stay zero.
    Rig resampled;
    resampled.sender->setPreserveReceiverVolume(true);
    resampled.sender->setInputFormat(48000);
    runAp2Handshake(resampled, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(resampled.launchedOk && *resampled.launchedOk, "resampling evidence test launches");
    resampled.io.takeUdp();
    std::vector<int16_t> impulse(960 * 2, 0);
    impulse[2] = 1;
    REQUIRE(resampled.ring.tryPush(impulse), "tiny nonzero impulse enters the input ring");
    resampled.advance(8);
    const auto resampledPackets = resampled.io.takeUdp();
    size_t audioPackets = 0;
    for (const auto& packet : resampledPackets) {
        if (packet.s != RaopUdp::Audio) continue;
        ++audioPackets;
        const auto pcm = resampled.rx.decodeAudio(packet.data);
        REQUIRE(pcm.has_value(), "resampled wire packet independently decrypts");
        CHECK(std::all_of(pcm->begin(), pcm->end(), [](int16_t sample) { return sample == 0; }),
              "sub-LSB resampled impulse emits an entirely zero-valued packet");
    }
    CHECK(audioPackets == 1 && resampled.sender->diagnostics().nonzeroSamplesSent == 0,
          "nonzero sending evidence is measured after resampling, not from input count");
}
// The original fake receiver intentionally does not implement GET_PARAMETER.
// Replace only that reply using its independently derived encrypted channel.
void replyVolume(Rig& rig, int code, const std::string& body = {}, int overrideSeq = -1) {
    const auto oldRequests = rig.rx.requests.size();
    const auto ignoredReply = rig.rx.onControlBytes(rig.io.takeTcp(RaopTcp::Control));
    REQUIRE(!ignoredReply.empty() && rig.rx.requests.size() == oldRequests + 1,
            "one bounded encrypted volume request reaches the receiver");
    const auto& request = rig.rx.requests.back();
    const auto seq = overrideSeq >= 0 ? std::to_string(overrideSeq) : request.h("cseq");
    const auto reply = "RTSP/1.0 " + std::to_string(code) + " Result\r\nCSeq: " + seq
        + "\r\nContent-Type: text/parameters\r\nContent-Length: "
        + std::to_string(body.size()) + "\r\n\r\n" + body;
    --rig.rx.ctrlOutCtr;
    rig.sender->onTcpData(RaopTcp::Control,
        spanOf(encryptFrames(rig.rx.ctrlOut, rig.rx.ctrlOutCtr, reply)));
}

void testReceiverVolumeReadAckAndCoalescing() {
    g_test = "GluSpot actual receiver volume and coalesced acknowledged writes";
    Rig rig;
    rig.sender->setPreserveReceiverVolume(true);
    CHECK(!rig.sender->requestReceiverVolume(), "cannot query volume before connected");
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    REQUIRE(rig.launchedOk && *rig.launchedOk, "AP2 volume test launches");
    CHECK(!rig.sender->diagnostics().receiverVolumeKnown, "missing receiver read never invents 50 percent");
    CHECK(rig.sender->requestReceiverVolume() && !rig.sender->requestReceiverVolume(),
          "only one current volume query may occupy the response FIFO");
    replyVolume(rig, 200, "volume: -21.000000\r\n");
    const auto* get = rig.rx.last("GET_PARAMETER");
    REQUIRE(get, "volume read uses GET_PARAMETER");
    CHECK(get->body == "volume\r\n" && get->h("content-type") == "text/parameters",
          "volume query uses receiver text parameter grammar");
    auto d = rig.sender->diagnostics();
    CHECK(d.receiverVolumeKnown && std::abs(d.receiverVolumePct - 30.0) < 1e-8 &&
          d.receiverVolumeSource == "receiver_read" && !d.receiverVolumePending,
          "actual -21 dB receiver response maps to 30 percent");
    rig.sender->setVolume(55);
    for (int pct = 1; pct <= 100; ++pct) rig.sender->setVolume(pct);
    d = rig.sender->diagnostics();
    CHECK(d.receiverVolumePending && std::abs(d.receiverVolumePct - 30.0) < 1e-8,
          "sending a slider value does not fake a receiver acknowledgement");
    replyVolume(rig, 200);
    CHECK(rig.rx.last("SET_PARAMETER")->body == "volume: -13.500000",
          "first explicit 55 percent uses the existing dB mapping");
    d = rig.sender->diagnostics();
    CHECK(d.receiverVolumeKnown && std::abs(d.receiverVolumePct - 55.0) < 1e-8 && d.receiverVolumePending,
          "matching 200 confirms first value while newest queued request is pending");
    replyVolume(rig, 200);
    CHECK(rig.rx.last("SET_PARAMETER")->body == "volume: 0.000000" &&
          rig.rx.count("SET_PARAMETER") == 2 && !rig.sender->diagnostics().receiverVolumePending,
          "100 slider updates coalesce into only first and latest requests");
    rig.sender->setVolume(0);
    replyVolume(rig, 200);
    d = rig.sender->diagnostics();
    CHECK(d.receiverVolumeKnown && d.receiverVolumePct == 0 && d.receiverVolumeSource == "receiver_ack" &&
          rig.rx.last("SET_PARAMETER")->body == "volume: -144.000000",
          "zero uses the receiver mute sentinel and is confirmed only on reply");
    rig.sender->setVolume(75);
    replyVolume(rig, 500);
    d = rig.sender->diagnostics();
    CHECK(d.receiverVolumeKnown && d.receiverVolumePct == 0 && d.lastVolumeCode == 500,
          "receiver rejection retains prior confirmed mute instead of requested 75 percent");
    rig.sender->setVolume(12.3456789);
    const auto bare = "RTSP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n";
    rig.sender->onTcpData(RaopTcp::Control, spanOf(encryptFrames(rig.rx.ctrlOut, rig.rx.ctrlOutCtr, bare)));
    CHECK(rig.sender->diagnostics().receiverVolumePct == 0 && rig.sender->diagnostics().receiverVolumePending,
          "a bare 200 without matching CSeq never confirms receiver volume");
    replyVolume(rig, 200);
    CHECK(rig.rx.last("SET_PARAMETER")->body == "volume: -26.296296" &&
          std::abs(rig.sender->diagnostics().receiverVolumePct - 12.34568) < 1e-8,
          "decimal slider round-trip confirms the rounded dB level actually sent");
    rig.sender->setVolume(std::numeric_limits<double>::quiet_NaN());
    rig.sender->setVolume(std::numeric_limits<double>::infinity());
    CHECK(rig.io.takeTcp(RaopTcp::Control).empty(), "non-finite values send no command");
    rig.sender->stop();
    rig.exchange();
    CHECK(!rig.sender->requestReceiverVolume(), "stopped sender never reads a stale receiver");
    rig.rx = FakeReceiver{};
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    CHECK(!rig.sender->diagnostics().receiverVolumeKnown && volumeRequests(rig.rx) == 0,
          "new receiver never inherits prior slider state or previous actual volume");
}

void testReceiverVolumeUnknownTimeoutAndLateResponses() {
    g_test = "GluSpot volume unknown and bounded timeout isolation";
    Rig rig;
    rig.sender->setPreserveReceiverVolume(true);
    runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
    rig.sender->requestReceiverVolume();
    replyVolume(rig, 501);
    CHECK(!rig.sender->diagnostics().receiverVolumeKnown && rig.sender->active(),
          "unsupported read stays unknown and does not interrupt audio");
    for (const auto& body : {"volume: nan\r\n", "volume: -50\r\n", "volume: 2\r\n",
                            "volume: -15garbage\r\n", "no-volume: -15\r\n"}) {
        rig.sender->requestReceiverVolume();
        replyVolume(rig, 200, body);
        CHECK(!rig.sender->diagnostics().receiverVolumeKnown,
              "malformed or out-of-range receiver values never become slider defaults");
    }
    rig.sender->requestReceiverVolume();
    replyVolume(rig, 200, "volume: -144\r\n");
    CHECK(rig.sender->diagnostics().receiverVolumeKnown && rig.sender->diagnostics().receiverVolumePct == 0,
          "receiver-reported mute is an actual zero value");
    rig.sender->setVolume(20);
    const auto ignoredReply = rig.rx.onControlBytes(rig.io.takeTcp(RaopTcp::Control));
    REQUIRE(!ignoredReply.empty(), "write reaches receiver but its response is withheld");
    const auto oldSeq = rig.rx.last("SET_PARAMETER")->h("cseq");
    --rig.rx.ctrlOutCtr;
    rig.advance(3000);
    auto d = rig.sender->diagnostics();
    CHECK(d.receiverVolumeKnown && d.receiverVolumePct == 0 && d.lastVolumeCode == 0 &&
          !d.receiverVolumePending && rig.sender->active(),
          "unanswered write retains prior confirmed mute after three seconds without stopping playback");
    rig.exchange();  // Keep-alive remains independently matched after timeout.
    rig.sender->setVolume(80);
    const auto newRequest = rig.io.takeTcp(RaopTcp::Control);
    const auto reply = rig.rx.onControlBytes(newRequest);
    REQUIRE(!reply.empty(), "new write may proceed after retired timeout");
    --rig.rx.ctrlOutCtr;
    const auto late = "RTSP/1.0 200 OK\r\nCSeq: " + oldSeq + "\r\nContent-Length: 0\r\n\r\n";
    rig.sender->onTcpData(RaopTcp::Control, spanOf(encryptFrames(rig.rx.ctrlOut, rig.rx.ctrlOutCtr, late)));
    CHECK(rig.sender->diagnostics().receiverVolumeKnown && rig.sender->diagnostics().receiverVolumePct == 0 &&
          rig.sender->diagnostics().receiverVolumePending,
          "late acknowledgement retains prior confirmed mute and never confirms a different newer slider command");
    const auto matched = "RTSP/1.0 200 OK\r\nCSeq: " + rig.rx.last("SET_PARAMETER")->h("cseq")
        + "\r\nContent-Length: 0\r\n\r\n";
    rig.sender->onTcpData(RaopTcp::Control, spanOf(encryptFrames(rig.rx.ctrlOut, rig.rx.ctrlOutCtr, matched)));
    CHECK(rig.sender->diagnostics().receiverVolumeKnown &&
          std::abs(rig.sender->diagnostics().receiverVolumePct - 80.0) < 1e-8,
          "only current CSeq 200 confirms the newer value");
}

void testReceiverInitialVolumeInfo() {
    g_test = "GluSpot preserves and reads initialVolume capability";
    Rig rig;
    rig.sender->setPreserveReceiverVolume(true);
    rig.rx.mode = FakeReceiver::Mode::Ap2Transient;
    rig.sender->setAuth(RaopDeviceInfo::Auth::HapTransient, true, "atv-1", "", "");
    rig.sender->start(rig.peerIp, 7000, "Living Room");
    rig.connectControl();
    for (int i = 0; i < 16; ++i) {
        auto out = rig.io.takeTcp(RaopTcp::Control);
        if (out.empty()) break;
        auto reply = rig.rx.onControlBytes(out);
        const auto& request = rig.rx.requests.back();
        if (request.method == "GET" && request.uri == "/info") {
            airplay::bplist::Dict info;
            info.emplace_back("initialVolume", airplay::bplist::Value::real(-18));
            const auto body = strOf(airplay::bplist::encode(airplay::bplist::Value::object(std::move(info))));
            const auto plain = "RTSP/1.0 200 OK\r\nCSeq: " + request.h("cseq")
                + "\r\nContent-Type: application/x-apple-binary-plist\r\nContent-Length: "
                + std::to_string(body.size()) + "\r\n\r\n" + body;
            --rig.rx.ctrlOutCtr;
            reply = encryptFrames(rig.rx.ctrlOut, rig.rx.ctrlOutCtr, plain);
        }
        rig.sender->onTcpData(RaopTcp::Control, spanOf(reply));
    }
    const auto d = rig.sender->diagnostics();
    CHECK(rig.launchedOk && *rig.launchedOk && d.receiverVolumeKnown &&
          std::abs(d.receiverVolumePct - 40.0) < 1e-8 && d.receiverVolumeSource == "receiver_info",
          "receiver capability initialVolume -18 dB supplies actual 40 percent");
    CHECK(volumeRequests(rig.rx) == 0, "reading initial volume never overwrites receiver on connection");
    rig.sender->requestReceiverVolume();
    replyVolume(rig, 501);
    CHECK(rig.sender->diagnostics().receiverVolumeKnown &&
          std::abs(rig.sender->diagnostics().receiverVolumePct - 40.0) < 1e-8,
          "unsupported GET preserves actual capability evidence instead of making up a level");

    rig.rx.evOut = airplay::hkdfSha512("Events-Salt", "Events-Write-Encryption-Key", rig.rx.sharedSecret, 32);
    rig.rx.evIn = airplay::hkdfSha512("Events-Salt", "Events-Read-Encryption-Key", rig.rx.sharedSecret, 32);
    rig.sender->onTcpConnected(RaopTcp::Event, {rig.localIp, 50001}, {rig.peerIp, 7001});
    airplay::bplist::Dict value;
    value.emplace_back("initialVolume", airplay::bplist::Value::real(-24));
    airplay::bplist::Dict event;
    event.emplace_back("type", airplay::bplist::Value::str("updateInfo"));
    event.emplace_back("value", airplay::bplist::Value::object(std::move(value)));
    const auto body = strOf(airplay::bplist::encode(airplay::bplist::Value::object(std::move(event))));
    const auto request = "POST /command RTSP/1.0\r\nCSeq: 91\r\nContent-Type: application/x-apple-binary-plist\r\nContent-Length: "
        + std::to_string(body.size()) + "\r\n\r\n" + body;
    rig.sender->onTcpData(RaopTcp::Event, spanOf(rig.rx.pushEvent(request)));
    CHECK(rig.sender->diagnostics().receiverVolumeKnown &&
          std::abs(rig.sender->diagnostics().receiverVolumePct - 20.0) < 1e-8,
          "authenticated updateInfo reports external receiver changes without an implicit write");
    CHECK(rig.rx.readEventReplies(rig.io.takeTcp(RaopTcp::Event)) ==
          "RTSP/1.0 200 OK\r\nServer: AirTunes/550.10\r\nCSeq: 91\r\n\r\n",
          "receiver volume event retains the independently decrypted minimal event acknowledgement");
}

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

void testAuthenticatedReceiverPlayPause() {
    g_test = "GluSpot authenticated receiver play/pause after encrypted ACK";
    Rig rig;
    std::vector<std::string> controls;
    std::vector<uint64_t> acksAtCallback;
    RaopEvents events;
    events.launched = [&](bool ok, const std::string& error) { rig.launchedOk = ok; rig.launchedErr = error; };
    events.receiverPlayPause = [&](const std::string& cseq) {
        controls.push_back(cseq);
        acksAtCallback.push_back(rig.sender->diagnostics().eventAcks);
    };
    rig.sender = std::make_unique<RaopSender>(rig.io, std::move(events));
    rig.sender->setClock([&] { return rig.now; });
    rig.sender->attachRing(&rig.ring);
    rig.sender->setInputFormat(44100);
    rig.sender->setPreserveReceiverVolume(true);
    rig.sender->setStrictReceiverAuth(true);
    auto connect = [&] {
        // Each connection has an independently fresh fake receiver's control
        // framing, keys and counters, just as the real reconnect does.
        rig.io.takeTcp(RaopTcp::Control); rig.io.takeTcp(RaopTcp::Event);
        rig.rx = FakeReceiver{};
        rig.launchedOk.reset();
        runAp2Handshake(rig, FakeReceiver::Mode::Ap2Transient, RaopDeviceInfo::Auth::HapTransient);
        REQUIRE(rig.launchedOk && *rig.launchedOk, "synthetic receiver authenticates before controls");
        rig.rx.evOut = airplay::hkdfSha512("Events-Salt", "Events-Write-Encryption-Key", rig.rx.sharedSecret, 32);
        rig.rx.evIn = airplay::hkdfSha512("Events-Salt", "Events-Read-Encryption-Key", rig.rx.sharedSecret, 32);
        rig.sender->onTcpConnected(RaopTcp::Event, {rig.localIp, 50001}, {rig.peerIp, 7001});
    };
    connect();
    auto request = [](const std::string& cseq, const std::string& command, const std::string& type = "sendMediaRemoteCommand") {
        using airplay::bplist::Value;
        const auto body = airplay::bplist::encode(Value::object({{"type", Value::str(type)}, {"value", Value::str(command)}}));
        return "POST /command RTSP/1.0\r\n" + (cseq.empty() ? "" : "CSeq: " + cseq + "\r\n")
            + "Content-Type: application/x-apple-binary-plist\r\nContent-Length: " + std::to_string(body.size())
            + "\r\n\r\n" + std::string(reinterpret_cast<const char*>(body.data()), body.size());
    };
    auto send = [&](const std::string& raw, const std::string& cseq, bool fragment = false) {
        const auto encrypted = rig.rx.pushEvent(raw);
        if (fragment) {
            rig.sender->onTcpData(RaopTcp::Event, spanOf(std::string_view(encrypted).substr(0, 9)));
            CHECK(rig.io.takeTcp(RaopTcp::Event).empty() && controls.empty(), "fragment cannot trigger a control or premature ACK");
        }
        rig.sender->onTcpData(RaopTcp::Event, spanOf(std::string_view(encrypted).substr(fragment ? 9 : 0)));
        const auto reply = rig.rx.readEventReplies(rig.io.takeTcp(RaopTcp::Event));
        CHECK(reply == "RTSP/1.0 200 OK\r\nServer: AirTunes/550.10\r\n"
            + (cseq.empty() ? "" : "CSeq: " + cseq + "\r\n") + "\r\n", "controls and ignored messages retain independently decrypted minimal ACK");
    };
    send(request("7", "paus"), "7", true);
    send(request("8", "play"), "8");
    CHECK(controls == std::vector<std::string>({"7", "8"}) && acksAtCallback == std::vector<uint64_t>({1, 2}),
        "both exact physical button names produce one toggle only after ACK");
    send(request("7", "play"), "7");
    send(request("0007", "paus"), "0007");
    send(request("9", "pause"), "9");
    send(request("10", "nitm"), "10");
    send(request("11", "pitm"), "11");
    send(request("12", "paus", "unknown"), "12");
    send(request("", "paus"), "");
    send(request("-13", "paus"), "-13");
    send(request("12345678901234567", "paus"), "12345678901234567");
    auto wrongMethod = request("14", "play"); wrongMethod.replace(0, 4, "GET ");
    send(wrongMethod, "14");
    auto wrongValue = request("15", "play");
    const auto valueAt = wrongValue.rfind("play"); REQUIRE(valueAt != std::string::npos, "test plist contains string value");
    wrongValue[valueAt - 1] = char(0x10); // Int marker replaces the known short string marker.
    send(wrongValue, "15");
    CHECK(controls.size() == 2, "duplicate, malformed and unsupported messages never become playback actions");
    const auto both = rig.rx.pushEvent(request("16", "paus") + request("17", "play"));
    rig.sender->onTcpData(RaopTcp::Event, spanOf(both));
    const auto replies = rig.rx.readEventReplies(rig.io.takeTcp(RaopTcp::Event));
    CHECK(controls == std::vector<std::string>({"7", "8", "16", "17"}) && replies.find("CSeq: 16") != std::string::npos
        && replies.find("CSeq: 17") != std::string::npos, "coalesced encrypted requests are each ACKed and dispatched once");
    const auto volume = rig.sender->diagnostics();
    CHECK(volumeRequests(rig.rx) == 0 && !rig.rx.controlDecryptFailed && volume.eventDecryptFailures == 0,
        "receiver taps preserve audio session and volume policy");
    rig.sender->stop();
    rig.io.takeTcp(RaopTcp::Event);
    rig.sender->onTcpData(RaopTcp::Event, spanOf(rig.rx.pushEvent(request("18", "paus"))));
    CHECK(controls.size() == 4 && rig.io.takeTcp(RaopTcp::Event).empty(), "a stopped session cannot dispatch or acknowledge taps");
    connect();
    send(request("7", "paus"), "7");
    CHECK(controls.size() == 5 && controls.back() == "7", "a fresh authenticated session resets receiver CSeq dedup");
    auto corrupt = rig.rx.pushEvent(request("19", "play")); corrupt.back() ^= 1;
    rig.sender->onTcpData(RaopTcp::Event, spanOf(corrupt));
    CHECK(controls.size() == 5 && rig.io.takeTcp(RaopTcp::Event).empty()
        && rig.sender->diagnostics().eventDecryptFailures == 1, "unauthenticated ciphertext cannot control or claim an ACK");
    rig.sender->stop();
}

}  // namespace

int main(int argc, char** argv) {
    // Optional offline evidence export. This creates only synthetic samples;
    // there is no socket, device, microphone, capture or persisted key.
    if (argc == 3 && std::string_view(argv[1]) == "--export-alac-fixture") {
        try { exportActualAlacFixtures(argv[2]); return 0; }
        catch (const std::exception& error) { std::fprintf(stderr, "%s\n", error.what()); return 1; }
    }
    if (argc != 1) { std::fprintf(stderr, "Usage: gluspot_sender_policy_tests [--export-alac-fixture path]\n"); return 2; }
    testExplicitAlacFrameLength();
    testActualNonzeroAudioSendEvidence();
    testReceiverVolumeReadAckAndCoalescing();
    testReceiverVolumeUnknownTimeoutAndLateResponses();
    testReceiverInitialVolumeInfo();
    testPreserveReceiverVolume();
    testExplicitVolumeDuringHandshake();
    testTransientSessionPastReceiverWatchdog();
    testUnansweredFeedbackIsBoundedAndRecoverable();
    testAuthenticatedReceiverPlayPause();
    std::printf("GluSpot sender policy: %d/%d checks passed\n", g_checks - g_failures, g_checks);
    return g_failures ? 1 : 0;
}
