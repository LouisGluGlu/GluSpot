// Offline direct-PCM protocol and real Windows pipe lifecycle checks. Child
// processes host the actual capture; no endpoint, receiver or volume is opened.
#include "pcm_pipe_capture.h"
#include <cstdio>
#include <iostream>

namespace {
using Clock = std::chrono::steady_clock;
constexpr uint64_t epoch = 0x123456789abcdef0ULL;

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template<class Action> void rejected(Action action) {
    bool failed = false;
    try { action(); } catch (const std::invalid_argument&) { failed = true; }
    require(failed, "Malformed protocol or CLI was accepted");
}
std::array<uint8_t, 24> header(uint64_t token, uint64_t sequence, uint32_t frames) {
    std::array<uint8_t, 24> bytes{'G', 'P', 'C', 'M'};
    auto put = [&bytes](size_t offset, uint64_t value, size_t count) {
        for (size_t index = 0; index < count; ++index) bytes[offset + index] = uint8_t(value >> (index * 8));
    };
    put(4, token, 8); put(12, sequence, 8); put(20, frames, 4);
    return bytes;
}
void fixedHeaderBoundsAndIdentity() {
    const auto bytes = header(epoch, 0xfedcba9876543210ULL, 16384);
    const auto parsed = gluspot::parsePcmPipeHeader(bytes, epoch, 0xfedcba9876543210ULL);
    require(parsed.epoch == epoch && parsed.sequence == 0xfedcba9876543210ULL && parsed.frames == 16384,
        "Little-endian header fields changed");
    rejected([&] { gluspot::parsePcmPipeHeader(std::span(bytes).first(23), epoch, parsed.sequence); });
    std::array<uint8_t, 25> oversized{};
    std::copy(bytes.begin(), bytes.end(), oversized.begin());
    rejected([&] { gluspot::parsePcmPipeHeader(oversized, epoch, parsed.sequence); });
    for (size_t index = 0; index < 4; ++index) {
        auto invalid = bytes; invalid[index] ^= 1;
        rejected([&] { gluspot::parsePcmPipeHeader(invalid, epoch, parsed.sequence); });
    }
    for (uint32_t frames : {0U, 16385U, UINT32_MAX}) {
        const auto invalid = header(epoch, 1, frames);
        rejected([&] { gluspot::parsePcmPipeHeader(invalid, epoch, 1); });
    }
    rejected([&] { gluspot::parsePcmPipeHeader(bytes, 0, parsed.sequence); });
    rejected([&] { gluspot::parsePcmPipeHeader(bytes, epoch + 1, parsed.sequence); });
    rejected([&] { gluspot::parsePcmPipeHeader(bytes, epoch, 0); });
    rejected([&] { gluspot::parsePcmPipeHeader(bytes, epoch, parsed.sequence - 1); });
    const auto minimum = header(1, 1, 1);
    require(gluspot::parsePcmPipeHeader(minimum, 1, 1).frames == 1, "Minimum packet rejected");
}

std::vector<std::string> pcmArguments() {
    return {"--group", "--pcm-pipe", "GluSpotPCM-0123456789abcdef0123456789abcdef",
        "--pcm-parent-pid", "123", "--pcm-parent-created-at", "456", "--pcm-epoch", "789",
        "--local-endpoint", "output-a"};
}
void strictCaptureModeArguments() {
    const auto valid = pcmArguments();
    auto parsed = gluspot::parseGroupArguments(valid);
    require(parsed.directPcm() && parsed.musicPid == 0 && parsed.sourceEndpoint.empty() &&
        parsed.localEndpoints.size() == 1 && parsed.pcmEpoch == 789, "Owned one-output mode changed");
    auto airplay = valid;
    airplay.resize(9);
    airplay.insert(airplay.end(), {"--receiver-id", "receiver", "--host", "192.168.1.20", "--port", "7000",
        "--device-id", "device", "--name", "Test receiver"});
    require(gluspot::parseGroupArguments(airplay).receivers.size() == 1, "Owned single-AirPlay mode rejected");
    for (size_t index : {size_t(1), size_t(3), size_t(5), size_t(7), size_t(9)}) {
        auto invalid = valid; invalid.erase(invalid.begin() + index, invalid.begin() + index + 2);
        rejected([&] { gluspot::parseGroupArguments(invalid); });
    }
    for (const std::string& name : {std::string(""), std::string("GluSpotPCM-0123456789abcdef0123456789abcde"),
         std::string("GluSpotPCM-0123456789abcdef0123456789abcdef0"),
         std::string("GluSpotPCM-0123456789ABCDEF0123456789ABCDEF"), std::string("\\\\server\\pipe\\audio")}) {
        auto invalid = valid; invalid[2] = name;
        rejected([&] { gluspot::parseGroupArguments(invalid); });
    }
    for (size_t index : {size_t(4), size_t(6), size_t(8)}) {
        for (const std::string& value : {std::string("0"), std::string("-1"), std::string("1x"), std::string("18446744073709551616")}) {
            auto invalid = valid; invalid[index] = value;
            rejected([&] { gluspot::parseGroupArguments(invalid); });
        }
    }
    for (const std::string& option : {std::string("--music-pid"), std::string("--music-created-at"),
         std::string("--source-endpoint"), std::string("--unknown")}) {
        auto invalid = valid; invalid.insert(invalid.end(), {option, "123"});
        rejected([&] { gluspot::parseGroupArguments(invalid); });
    }
    auto duplicate = valid; duplicate.insert(duplicate.end(), {"--pcm-epoch", "789"});
    rejected([&] { gluspot::parseGroupArguments(duplicate); });
    auto sameOutput = valid; sameOutput.insert(sameOutput.end(), {"--local-endpoint", "OUTPUT-A"});
    rejected([&] { gluspot::parseGroupArguments(sameOutput); });
    auto noGroup = valid; noGroup.erase(noGroup.begin());
    rejected([&] { gluspot::parseGroupArguments(noGroup); });
    auto missingValue = valid; missingValue.push_back("--pcm-epoch");
    rejected([&] { gluspot::parseGroupArguments(missingValue); });
    auto tooMany = valid;
    for (unsigned index = 1; index < 9; ++index) tooMany.insert(tooMany.end(), {"--local-endpoint", "output-" + std::to_string(index)});
    rejected([&] { gluspot::parseGroupArguments(tooMany); });
    const std::vector<std::string> process{"--group", "--music-pid", "123", "--music-created-at", "456",
        "--source-endpoint", "source", "--local-endpoint", "output-a", "--local-endpoint", "output-b"};
    require(!gluspot::parseGroupArguments(process).directPcm(), "Process capture mode changed");
    for (const std::string& option : {std::string("--pcm-pipe"), std::string("--pcm-parent-pid"),
         std::string("--pcm-parent-created-at"), std::string("--pcm-epoch")}) {
        auto invalid = process; invalid.insert(invalid.end(), {option, option == "--pcm-pipe" ? "" : "123"});
        rejected([&] { gluspot::parseGroupArguments(invalid); });
    }
}

struct Handle {
    HANDLE value = nullptr;
    explicit Handle(HANDLE raw = nullptr) : value(raw) {}
    ~Handle() { reset(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    void reset(HANDLE raw = nullptr) {
        if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value);
        value = raw;
    }
};
uint64_t creationToken(HANDLE process) {
    FILETIME created{}, exited{}, kernel{}, user{};
    require(GetProcessTimes(process, &created, &exited, &kernel, &user), "Cannot read test process identity");
    return uint64_t(created.dwHighDateTime) << 32 | created.dwLowDateTime;
}
std::wstring executablePath() {
    std::array<wchar_t, 32768> path{};
    const auto size = GetModuleFileNameW(nullptr, path.data(), DWORD(path.size()));
    require(size && size < path.size(), "Cannot locate test executable");
    return {path.data(), size};
}
void writeBytes(HANDLE pipe, std::span<const uint8_t> bytes) {
    size_t offset = 0;
    while (offset < bytes.size()) {
        DWORD written = 0;
        require(WriteFile(pipe, bytes.data() + offset, DWORD(bytes.size() - offset), &written, nullptr) && written,
            "Test pipe write failed");
        offset += written;
    }
}
class Child {
    Handle process_, input_, output_;
public:
    explicit Child(const std::wstring& arguments) {
        SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
        HANDLE read = nullptr, write = nullptr;
        require(CreatePipe(&read, &write, &security, 0), "Cannot create test stdout pipe");
        output_.reset(read); Handle childOutput(write);
        require(SetHandleInformation(output_.value, HANDLE_FLAG_INHERIT, 0), "Cannot fence test stdout inheritance");
        require(CreatePipe(&read, &write, &security, 0), "Cannot create test stdin pipe");
        Handle childInput(read); input_.reset(write);
        require(SetHandleInformation(input_.value, HANDLE_FLAG_INHERIT, 0), "Cannot fence test stdin inheritance");
        STARTUPINFOW startup{}; startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = childInput.value; startup.hStdOutput = startup.hStdError = childOutput.value;
        PROCESS_INFORMATION info{};
        std::wstring command = L"\"" + executablePath() + L"\" " + arguments;
        require(CreateProcessW(nullptr, command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW,
            nullptr, nullptr, &startup, &info), "Cannot start test child");
        process_.reset(info.hProcess); CloseHandle(info.hThread);
    }
    ~Child() {
        if (process_.value && WaitForSingleObject(process_.value, 0) == WAIT_TIMEOUT) {
            TerminateProcess(process_.value, 99); WaitForSingleObject(process_.value, 2000);
        }
    }
    void command(const std::string& text) {
        const auto line = text + '\n';
        writeBytes(input_.value, {reinterpret_cast<const uint8_t*>(line.data()), line.size()});
    }
    std::string line() {
        const auto deadline = Clock::now() + std::chrono::seconds(8);
        std::string result;
        while (Clock::now() < deadline) {
            DWORD available = 0;
            if (!PeekNamedPipe(output_.value, nullptr, 0, nullptr, &available, nullptr)) break;
            if (available) {
                char byte = 0; DWORD count = 0;
                require(ReadFile(output_.value, &byte, 1, &count, nullptr) && count, "Cannot read test child");
                if (byte == '\n') return result;
                if (byte != '\r') result += byte;
                require(result.size() < 4096, "Unbounded child status");
            } else Sleep(2);
        }
        throw std::runtime_error("Test child status timed out: " + result);
    }
    void ready() { require(line() == "READY", "Test child was not ready"); }
    void finish() {
        const auto result = line();
        require(result == "OK", result.c_str());
        require(WaitForSingleObject(process_.value, 5000) == WAIT_OBJECT_0, "Test child failed to exit");
        DWORD code = 99;
        require(GetExitCodeProcess(process_.value, &code) && code == 0, "Test child failed");
    }
};
std::string newPipeName() {
    static uint64_t serial = 0;
    char bytes[33]{};
    std::snprintf(bytes, sizeof(bytes), "%016llx%016llx", static_cast<unsigned long long>(GetCurrentProcessId()),
        static_cast<unsigned long long>(++serial));
    return "GluSpotPCM-" + std::string(bytes);
}
std::wstring wide(const std::string& value) { return {value.begin(), value.end()}; }
std::wstring hostArguments(const std::string& name, const std::string& scenario, uint64_t creation) {
    return L"--capture-child " + wide(name) + L" " + std::to_wstring(GetCurrentProcessId()) + L" " +
        std::to_wstring(creation) + L" " + std::to_wstring(epoch) + L" " + wide(scenario);
}
void connectPipe(Handle& pipe, const std::string& suffix) {
    const auto name = L"\\\\.\\pipe\\" + wide(suffix);
    const auto deadline = Clock::now() + std::chrono::seconds(5);
    while (Clock::now() < deadline) {
        pipe.reset(CreateFileW(name.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr));
        if (pipe.value != INVALID_HANDLE_VALUE) return;
        Sleep(2);
    }
    throw std::runtime_error("Cannot connect test PCM pipe");
}
std::vector<uint8_t> packet(uint64_t token, uint64_t sequence, uint32_t frames, int16_t sample) {
    const auto prefix = header(token, sequence, frames);
    std::vector<uint8_t> bytes(prefix.begin(), prefix.end());
    for (size_t index = 0; index < size_t(frames) * 2; ++index) {
        const auto value = uint16_t(index % 2 ? -sample : sample);
        bytes.push_back(uint8_t(value)); bytes.push_back(uint8_t(value >> 8));
    }
    return bytes;
}
template<class Condition> void eventually(Condition condition, const char* message) {
    const auto deadline = Clock::now() + std::chrono::seconds(5);
    while (!condition() && Clock::now() < deadline) Sleep(2);
    require(condition(), message);
}
int captureChild(int argc, char** argv) {
    require(argc == 7, "Invalid capture-child test arguments");
    const std::string scenario = argv[6];
    const auto pid = DWORD(std::stoul(argv[3]));
    const auto creation = std::stoull(argv[4]), token = std::stoull(argv[5]);
    if (scenario == "bad-identity") {
        bool failed = false;
        try { gluspot::GroupPcmPipeCapture capture(argv[2], pid, creation, token); }
        catch (const std::runtime_error&) { failed = true; }
        require(failed, "Changed creation token was accepted");
        std::cout << "OK\n"; return 0;
    }
    auto capture = std::make_unique<gluspot::GroupPcmPipeCapture>(argv[2], pid, creation, token);
    require(capture->waitReady(), "PCM pipe was not created");
    capture->start(); std::cout << "READY\n";
    std::string command; require(bool(std::getline(std::cin, command)), "Missing test command");
    if (scenario == "fragments") {
        require(command == "verify", "Wrong fragmented test command");
        eventually([&] { return (capture->frames.load() == 369 && capture->signal().meaningfulSamples == 738) ||
            !capture->error().empty(); }, "Fragmented packets timed out");
        require(capture->error().empty() && capture->frames == 369, "Fragmented stream failed");
        std::vector<int16_t> pcm(738);
        require(capture->ring().tryPop(pcm) && capture->ring().availableRead() == 0, "Fragmented PCM count changed");
        for (size_t index = 0; index < pcm.size(); ++index) {
            const int16_t sample = index < 704 ? 3000 : 9000;
            require(pcm[index] == (index % 2 ? -sample : sample), "Fragmented PCM order changed");
        }
        require(capture->signal().meaningfulSamples == pcm.size(), "Only accepted PCM may prove meaningful signal");
    } else if (scenario == "overrun") {
        eventually([&] { return !capture->error().empty(); }, "Bounded queue did not fail");
        require(capture->error() == "Direct PCM queue overran" && capture->frames == 65536 &&
            capture->droppedSamples == 32768 && capture->ring().availableRead() == capture->ring().capacity(),
            "Queue overrun did not preserve the accepted bounded stream");
        require(capture->signal().meaningfulSamples == 131072, "Rejected PCM counted as accepted signal");
    } else if (scenario == "foreign") {
        eventually([&] { return !capture->error().empty(); }, "Foreign pipe client was not rejected");
        require(capture->error() == "Direct PCM client identity mismatch" && capture->frames == 0 &&
            capture->ring().availableRead() == 0, "Foreign pipe client supplied accepted PCM");
    } else if (scenario == "cancel") {
        require(command == "cancel" && capture->frames == 0 && capture->error().empty(), "Incomplete stream failed before cancellation");
    } else throw std::runtime_error("Unknown capture child scenario");
    const auto start = Clock::now(); capture.reset();
    require(Clock::now() - start < std::chrono::milliseconds(1500), "Pending pipe I/O did not cancel promptly");
    std::cout << "OK\n"; return 0;
}
int foreignChild(int argc, char** argv) {
    require(argc == 3, "Invalid foreign-client arguments");
    Handle pipe; connectPipe(pipe, argv[2]); std::cout << "READY\n";
    std::string command; require(bool(std::getline(std::cin, command)), "Missing foreign-client stop");
    std::cout << "OK\n"; return 0;
}
void realPipeLifecycle() {
    const auto created = creationToken(GetCurrentProcess());
    {
        Child bad(hostArguments(newPipeName(), "bad-identity", created + 1)); bad.finish();
    }
    // Cancellation is exercised while connect, header and payload reads each
    // have a real overlapped operation pending, with the verified parent alive.
    for (unsigned stage = 0; stage < 3; ++stage) {
        const auto name = newPipeName(); Child child(hostArguments(name, "cancel", created)); child.ready();
        Handle pipe;
        if (stage) {
            connectPipe(pipe, name);
            const auto bytes = packet(epoch, 1, 352, 3000);
            writeBytes(pipe.value, std::span(bytes).first(stage == 1 ? 7 : 26));
        }
        Sleep(75); child.command("cancel"); child.finish();
    }
    {
        const auto name = newPipeName(); Child child(hostArguments(name, "fragments", created)); child.ready();
        Handle pipe; connectPipe(pipe, name);
        const auto first = packet(epoch, 1, 352, 3000), second = packet(epoch, 2, 17, 9000);
        for (const auto& bytes : {first, second}) {
            size_t offset = 0;
            for (size_t count : {size_t(1), size_t(3), size_t(2), size_t(7), size_t(11), size_t(5), size_t(19)}) {
                writeBytes(pipe.value, std::span(bytes).subspan(offset, count)); offset += count; Sleep(3);
            }
            writeBytes(pipe.value, std::span(bytes).subspan(offset));
        }
        child.command("verify"); child.finish();
    }
    {
        const auto name = newPipeName(); Child child(hostArguments(name, "overrun", created)); child.ready();
        Handle pipe; connectPipe(pipe, name);
        for (uint64_t sequence = 1; sequence <= 5; ++sequence) writeBytes(pipe.value, packet(epoch, sequence, 16384, 3000));
        child.command("verify"); child.finish();
    }
    {
        const auto name = newPipeName(); Child child(hostArguments(name, "foreign", created)); child.ready();
        Child foreign(L"--foreign-client " + wide(name)); foreign.ready();
        child.command("verify"); child.finish(); foreign.command("stop"); foreign.finish();
    }
}
} // namespace

int main(int argc, char** argv) {
    std::cout << std::unitbuf;
    try {
        if (argc > 1 && std::string(argv[1]) == "--capture-child") return captureChild(argc, argv);
        if (argc > 1 && std::string(argv[1]) == "--foreign-client") return foreignChild(argc, argv);
        fixedHeaderBoundsAndIdentity(); strictCaptureModeArguments(); realPipeLifecycle();
        std::cout << "Direct PCM strict framing/CLI, fragmented stream, bounded queue, identity and cancellation passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n'; return 1;
    }
}
