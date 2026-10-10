// Offline test-only AP2 receiver over real loopback sockets. Reuse the
// independent upstream crypto/parser fixture; never open an audio endpoint.
#include <winsock2.h>
#include <ws2tcpip.h>
#define main upstreamCoreTestMain
#include "../../third_party/airplay2-sender-cpp/test/core_tests.cpp"
#undef main
#include <atomic>
#include <iostream>
#include <mutex>
#include <thread>

namespace {
SOCKET boundSocket(bool tcp) {
    const auto socket = ::socket(AF_INET, tcp ? SOCK_STREAM : SOCK_DGRAM, 0);
    sockaddr_in address{}; address.sin_family = AF_INET; address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (socket == INVALID_SOCKET || ::bind(socket, reinterpret_cast<sockaddr*>(&address), sizeof(address)) ||
        (tcp && ::listen(socket, 1))) throw std::runtime_error("Loopback fixture bind failed");
    return socket;
}
uint16_t fixturePort(SOCKET socket) {
    sockaddr_in address{}; int size = sizeof(address);
    if (::getsockname(socket, reinterpret_cast<sockaddr*>(&address), &size)) throw std::runtime_error("Fixture port failed");
    return ntohs(address.sin_port);
}
uint64_t fixtureBig64(const std::string& packet, size_t offset) {
    return (uint64_t(be32(packet, offset)) << 32) | be32(packet, offset + 4);
}
uint64_t fixtureLittle64(const std::string& packet, size_t offset) {
    uint64_t result = 0;
    for (size_t index = 0; index < 8; ++index) result |= uint64_t(uint8_t(packet[offset + index])) << (index * 8);
    return result;
}
void fixtureSend(SOCKET socket, const std::string& bytes) {
    for (size_t offset = 0; offset < bytes.size();) {
        const auto count = ::send(socket, bytes.data() + offset, int(bytes.size() - offset), 0);
        if (count <= 0) return;
        offset += size_t(count);
    }
}
struct AudioRecord { uint32_t rtp; uint64_t hash; };
class ReceiverFixture {
public:
    explicit ReceiverFixture(bool badProof = false) {
        controlListen_ = boundSocket(true); eventListen_ = boundSocket(true);
        audio_ = boundSocket(false); control_ = boundSocket(false); timing_ = boundSocket(false);
        rx_.mode = FakeReceiver::Mode::Ap2Transient; rx_.badSrpProof = badProof;
        rx_.eventPort = fixturePort(eventListen_); rx_.dataPort = fixturePort(audio_);
        rx_.ap2ControlPort = fixturePort(control_); rx_.timingPort = fixturePort(timing_);
        worker_ = std::thread([this] { run(); });
    }
    ~ReceiverFixture() {
        stopped_.store(true); worker_.join();
        for (const auto socket : {controlListen_, eventListen_, audio_, control_, timing_, controlClient_, eventClient_})
            if (socket != INVALID_SOCKET) ::closesocket(socket);
    }
    uint16_t port() const { return fixturePort(controlListen_); }
    void report() {
        std::lock_guard lock(mutex_);
        std::cout << "{\"packets\":" << records_.size() << ",\"anchor\":" << anchor_
            << ",\"decrypt_failures\":" << decryptFailures_ << ",\"continuity_failures\":" << continuityFailures_
            << ",\"teardown\":" << (rx_.sawTeardown ? "true" : "false")
            << ",\"control_decrypt_failed\":" << (rx_.controlDecryptFailed ? "true" : "false")
            << ",\"event_ack\":" << (eventAck_ ? "true" : "false") << ",\"records\":[";
        bool comma = false;
        for (const auto& record : records_) {
            if (comma) std::cout << ',';
            comma = true; std::cout << '[' << record.rtp << ',' << record.hash << ']';
        }
        std::cout << "]}";
    }
private:
    FakeReceiver rx_;
    SOCKET controlListen_ = INVALID_SOCKET, eventListen_ = INVALID_SOCKET, audio_ = INVALID_SOCKET;
    SOCKET control_ = INVALID_SOCKET, timing_ = INVALID_SOCKET, controlClient_ = INVALID_SOCKET, eventClient_ = INVALID_SOCKET;
    std::mutex mutex_;
    std::atomic<bool> stopped_{false};
    std::thread worker_;
    std::vector<AudioRecord> records_;
    uint64_t anchor_ = 0, lastNonce_ = 0;
    uint16_t lastSequence_ = 0;
    uint32_t lastRtp_ = 0;
    unsigned decryptFailures_ = 0, continuityFailures_ = 0;
    bool eventPushed_ = false, eventAck_ = false;
    void run() {
        while (!stopped_.load()) {
            fd_set ready; FD_ZERO(&ready);
            for (const auto socket : {controlListen_, eventListen_, audio_, control_, timing_, controlClient_, eventClient_})
                if (socket != INVALID_SOCKET) FD_SET(socket, &ready);
            timeval timeout{0, 5000};
            if (::select(0, &ready, nullptr, nullptr, &timeout) <= 0) continue;
            std::lock_guard lock(mutex_);
            if (FD_ISSET(controlListen_, &ready) && controlClient_ == INVALID_SOCKET)
                controlClient_ = ::accept(controlListen_, nullptr, nullptr);
            if (FD_ISSET(eventListen_, &ready) && eventClient_ == INVALID_SOCKET)
                eventClient_ = ::accept(eventListen_, nullptr, nullptr);
            if (controlClient_ != INVALID_SOCKET && FD_ISSET(controlClient_, &ready)) {
                char bytes[65536]; const auto count = ::recv(controlClient_, bytes, sizeof(bytes), 0);
                if (count > 0) fixtureSend(controlClient_, rx_.onControlBytes(std::string_view(bytes, size_t(count))));
                else { ::closesocket(controlClient_); controlClient_ = INVALID_SOCKET; }
            }
            if (eventClient_ != INVALID_SOCKET && !eventPushed_ && !rx_.sharedSecret.empty()) {
                rx_.evOut = airplay::hkdfSha512("Events-Salt", "Events-Write-Encryption-Key", rx_.sharedSecret, 32);
                rx_.evIn = airplay::hkdfSha512("Events-Salt", "Events-Read-Encryption-Key", rx_.sharedSecret, 32);
                fixtureSend(eventClient_, rx_.pushEvent("POST /feedback RTSP/1.0\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n"));
                eventPushed_ = true;
            }
            if (eventClient_ != INVALID_SOCKET && FD_ISSET(eventClient_, &ready)) {
                char bytes[4096]; const auto count = ::recv(eventClient_, bytes, sizeof(bytes), 0);
                if (count > 0) eventAck_ |= rx_.readEventReplies(std::string_view(bytes, size_t(count))).find("200 OK") != std::string::npos;
                else { ::closesocket(eventClient_); eventClient_ = INVALID_SOCKET; }
            }
            if (FD_ISSET(audio_, &ready)) {
                char bytes[4096]; const auto count = ::recvfrom(audio_, bytes, sizeof(bytes), 0, nullptr, nullptr);
                if (count > 0) {
                    const std::string packet(bytes, size_t(count));
                    const auto decoded = rx_.decodeAudio(packet);
                    if (!decoded) { ++decryptFailures_; continue; }
                    const auto sequence = be16(packet, 2); const auto rtp = be32(packet, 4);
                    const auto nonce = fixtureLittle64(packet, packet.size() - 8);
                    if (!records_.empty() && (sequence != uint16_t(lastSequence_ + 1) ||
                        rtp != lastRtp_ + 352 || nonce != lastNonce_ + 1)) ++continuityFailures_;
                    lastSequence_ = sequence; lastRtp_ = rtp; lastNonce_ = nonce;
                    uint64_t hash = 1469598103934665603ULL;
                    for (const auto sample : *decoded) {
                        hash = (hash ^ uint8_t(sample)) * 1099511628211ULL;
                        hash = (hash ^ uint8_t(uint16_t(sample) >> 8)) * 1099511628211ULL;
                    }
                    if (records_.size() < 2048) records_.push_back({rtp, hash});
                }
            }
            if (FD_ISSET(control_, &ready)) {
                char bytes[4096]; const auto count = ::recvfrom(control_, bytes, sizeof(bytes), 0, nullptr, nullptr);
                if (count == 20 && !anchor_) anchor_ = fixtureBig64(std::string(bytes, size_t(count)), 8);
            }
            if (FD_ISSET(timing_, &ready)) {
                char bytes[4096]; ::recvfrom(timing_, bytes, sizeof(bytes), 0, nullptr, nullptr);
            }
        }
    }
};
} // namespace

int main() {
    WSADATA data{}; if (WSAStartup(MAKEWORD(2, 2), &data)) return 2;
    try {
        ReceiverFixture a, b, bad(true);
        std::cout << "{\"ports\":[" << a.port() << ',' << b.port() << ',' << bad.port() << "]}\n" << std::flush;
        std::string command;
        while (std::getline(std::cin, command)) {
            if (command == "stop") break;
            if (command != "stats") continue;
            std::cout << "{\"receivers\":["; a.report(); std::cout << ','; b.report(); std::cout << ','; bad.report();
            std::cout << "]}\n" << std::flush;
        }
    } catch (...) { std::cout << "{\"fixture_error\":true}\n" << std::flush; return 1; }
    WSACleanup(); return 0;
}
