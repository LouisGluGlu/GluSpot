// Exact music-process session mute ownership. The caller owns an MTA apartment.
// Windows ABI checked against audiopolicy.idl and audioclient.h. Muting a session
// is not evidence that process loopback still contains audio: probe that first.
#pragma once

#include <windows.h>
#include <audiopolicy.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <tlhelp32.h>
#include <wrl/client.h>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace gluspot {
namespace music_session_detail {
inline void checked(HRESULT hr, const char* action) {
    if (FAILED(hr)) {
        std::ostringstream message;
        message << action << " failed (HRESULT 0x" << std::hex << uint32_t(hr) << ')';
        throw std::runtime_error(message.str());
    }
}
class Handle {
public:
    explicit Handle(HANDLE value = nullptr) : value_(value) {}
    ~Handle() { if (value_ && value_ != INVALID_HANDLE_VALUE) CloseHandle(value_); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    HANDLE get() const { return value_; }
private:
    HANDLE value_;
};
inline uint64_t creationTime(HANDLE process) {
    FILETIME created{}, exited{}, kernel{}, user{};
    if (!GetProcessTimes(process, &created, &exited, &kernel, &user))
        throw std::runtime_error("Cannot read music process creation time");
    return uint64_t(created.dwLowDateTime) | (uint64_t(created.dwHighDateTime) << 32);
}
struct ProcessProof {
    DWORD pid;
    uint64_t created;
    std::shared_ptr<Handle> handle;
};
inline ProcessProof openProcess(DWORD pid) {
    auto handle = std::make_shared<Handle>(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                                                     FALSE, pid));
    if (!handle->get()) throw std::runtime_error("Cannot verify music process identity");
    if (WaitForSingleObject(handle->get(), 0) != WAIT_TIMEOUT)
        throw std::runtime_error("Music process is no longer alive");
    return {pid, creationTime(handle->get()), std::move(handle)};
}
inline std::wstring instanceId(IAudioSessionControl2* control) {
    LPWSTR raw = nullptr;
    checked(control->GetSessionInstanceIdentifier(&raw), "Music session instance identifier");
    std::unique_ptr<wchar_t, decltype(&CoTaskMemFree)> owner(raw, CoTaskMemFree);
    if (!raw || !*raw) throw std::runtime_error("Music session has no instance identity");
    return raw;
}

class SessionEvents final : public IAudioSessionEvents {
public:
    SessionEvents(const GUID& context, bool originallyMuted) : context_(context), lastMuted_(originallyMuted) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (!IsEqualIID(iid, __uuidof(IUnknown)) && !IsEqualIID(iid, __uuidof(IAudioSessionEvents)))
            return E_NOINTERFACE;
        *out = static_cast<IAudioSessionEvents*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto count = --references_;
        if (!count) delete this;
        return count;
    }
    HRESULT STDMETHODCALLTYPE OnDisplayNameChanged(LPCWSTR, LPCGUID) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE OnIconPathChanged(LPCWSTR, LPCGUID) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE OnSimpleVolumeChanged(float, BOOL muted, LPCGUID context) override {
        const bool changed = lastMuted_.exchange(muted != FALSE) != (muted != FALSE);
        if (changed && (!context || !IsEqualGUID(*context, context_)))
            externalMuteChange_.store(true);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE OnChannelVolumeChanged(DWORD, float[], DWORD, LPCGUID) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE OnGroupingParamChanged(LPCGUID, LPCGUID) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE OnStateChanged(AudioSessionState state) override {
        if (state == AudioSessionStateExpired) disconnected_.store(true);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE OnSessionDisconnected(AudioSessionDisconnectReason) override {
        disconnected_.store(true);
        return S_OK;
    }
    bool externallyChanged() const { return externalMuteChange_.load(); }
    bool disconnected() const { return disconnected_.load(); }
private:
    ~SessionEvents() = default;
    std::atomic<ULONG> references_{1};
    GUID context_;
    std::atomic<bool> lastMuted_, externalMuteChange_{false}, disconnected_{false};
};
} // namespace music_session_detail

struct MusicSessionMuteState {
    std::wstring endpoint;
    std::wstring instance;
    DWORD pid = 0;
    uint64_t createdAt = 0;
    bool originalMuted = false;
    bool muted = false;
    bool owned = false;
    bool externalMuteChange = false;
    bool disconnected = false;
    float originalVolume = 0;
    float volume = 0;
};

class MusicSessionMuteGuard {
public:
    // Reads identity/session state and registers notifications, without muting.
    MusicSessionMuteGuard(DWORD targetPid, uint64_t createdAt)
        : target_(music_session_detail::openProcess(targetPid)) {
        if (!targetPid || !createdAt || target_.created != createdAt)
            throw std::invalid_argument("Music process creation identity does not match");
        music_session_detail::checked(CoCreateGuid(&context_), "Music session event context");
        prepare();
    }
    MusicSessionMuteGuard(const MusicSessionMuteGuard&) = delete;
    MusicSessionMuteGuard& operator=(const MusicSessionMuteGuard&) = delete;
    ~MusicSessionMuteGuard() noexcept {
        try { restore(); } catch (...) {}
    }

    bool targetAlive() const {
        return WaitForSingleObject(target_.handle->get(), 0) == WAIT_TIMEOUT &&
               music_session_detail::creationTime(target_.handle->get()) == target_.created;
    }
    size_t size() const { return entries_.size(); }

    void mute() {
        if (applied_) return;
        if (!targetAlive()) throw std::runtime_error("Music process ended before session mute");
        try {
            for (auto& entry : entries_) {
                verify(*entry);
                if (entry->events->externallyChanged() || readMute(*entry) != entry->originalMuted)
                    throw std::runtime_error("Music session mute changed before probe");
                if (!entry->originalMuted) {
                    music_session_detail::checked(entry->volume->SetMute(TRUE, &context_), "Mute music session");
                    // Record successful ownership before a possibly failing readback.
                    entry->owned = true;
                }
                if (!readMute(*entry)) throw std::runtime_error("Windows did not confirm music session mute");
            }
            applied_ = true;
        } catch (...) {
            try { restore(); } catch (...) {}
            throw;
        }
    }

    bool isStillMuted() {
        if (!targetAlive()) return false;
        for (auto& entry : entries_) {
            if (entry->events->externallyChanged() || entry->events->disconnected()) return false;
            verify(*entry);
            if (!readMute(*entry)) return false;
        }
        return !entries_.empty();
    }

    void restore() {
        std::string failure;
        for (auto& entry : entries_) {
            if (!entry->owned) continue;
            // External mute changes own the subsequent state. Scalar changes
            // remain untouched and do not relinquish our separate mute bit.
            if (entry->events->externallyChanged() || entry->events->disconnected()) {
                entry->owned = false;
                continue;
            }
            try {
                verify(*entry);
                if (readMute(*entry)) {
                    music_session_detail::checked(entry->volume->SetMute(FALSE, &context_), "Restore music session mute");
                    if (readMute(*entry)) throw std::runtime_error("Windows did not confirm restored music session mute");
                }
                entry->owned = false;
            } catch (const std::exception& error) {
                if (failure.empty()) failure = error.what();
            }
        }
        applied_ = false;
        if (!failure.empty()) throw std::runtime_error(failure);
    }

    std::vector<MusicSessionMuteState> snapshot() const {
        std::vector<MusicSessionMuteState> result;
        for (const auto& entry : entries_) {
            MusicSessionMuteState state{entry->endpoint, entry->instance, entry->process.pid,
                entry->process.created, entry->originalMuted, entry->originalMuted, entry->owned,
                entry->events->externallyChanged(), entry->events->disconnected(),
                entry->originalVolume, entry->originalVolume};
            if (!state.disconnected) {
                verify(*entry);
                state.muted = readMute(*entry);
                music_session_detail::checked(entry->volume->GetMasterVolume(&state.volume), "Read music session volume");
            }
            result.push_back(std::move(state));
        }
        return result;
    }

private:
    using ComPtrControl = Microsoft::WRL::ComPtr<IAudioSessionControl2>;
    struct Entry {
        std::wstring endpoint, instance;
        music_session_detail::ProcessProof process;
        ComPtrControl control;
        Microsoft::WRL::ComPtr<ISimpleAudioVolume> volume;
        Microsoft::WRL::ComPtr<music_session_detail::SessionEvents> events;
        bool originalMuted = false, owned = false, registered = false;
        float originalVolume = 0;
        // Any partially constructed entry unregisters before releasing callback.
        ~Entry() { if (registered) control->UnregisterAudioSessionNotification(events.Get()); }
    };
    music_session_detail::ProcessProof target_;
    GUID context_{};
    std::vector<std::unique_ptr<Entry>> entries_;
    bool applied_ = false;

    static bool readMute(const Entry& entry) {
        BOOL muted = FALSE;
        music_session_detail::checked(entry.volume->GetMute(&muted), "Read music session mute");
        return muted != FALSE;
    }
    static void verify(const Entry& entry) {
        if (WaitForSingleObject(entry.process.handle->get(), 0) != WAIT_TIMEOUT ||
            music_session_detail::creationTime(entry.process.handle->get()) != entry.process.created)
            throw std::runtime_error("Music session process identity changed");
        DWORD pid = 0;
        const auto hr = entry.control->GetProcessId(&pid);
        if (hr != S_OK || pid != entry.process.pid ||
            music_session_detail::instanceId(entry.control.Get()) != entry.instance)
            throw std::runtime_error("Music session instance identity changed");
    }

    std::map<DWORD, music_session_detail::ProcessProof> verifiedTree() {
        using namespace music_session_detail;
        Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
        if (snapshot.get() == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot enumerate music process tree");
        std::map<DWORD, DWORD> parents;
        PROCESSENTRY32W process{};
        process.dwSize = sizeof(process);
        if (!Process32FirstW(snapshot.get(), &process)) throw std::runtime_error("Cannot read music process tree");
        do { parents.emplace(process.th32ProcessID, process.th32ParentProcessID); }
        while (Process32NextW(snapshot.get(), &process));
        if (GetLastError() != ERROR_NO_MORE_FILES) throw std::runtime_error("Incomplete music process tree");
        std::map<DWORD, ProcessProof> tree{{target_.pid, target_}};
        bool added;
        do {
            added = false;
            for (const auto& [pid, parent] : parents) {
                if (tree.contains(pid) || !tree.contains(parent)) continue;
                // An exited child need not own a live session. Inaccessible
                // descendants cannot be accepted as mute candidates.
                try {
                    auto proof = openProcess(pid);
                    if (proof.created < tree.at(parent).created) continue;
                    tree.emplace(pid, std::move(proof));
                    added = true;
                } catch (const std::exception&) {}
            }
        } while (added);
        if (tree.contains(GetCurrentProcessId()))
            throw std::runtime_error("Music process tree includes capture/render helper");
        if (!targetAlive()) throw std::runtime_error("Music process ended during identity verification");
        return tree;
    }

    void prepare() {
        using namespace music_session_detail;
        const auto tree = verifiedTree();
        Microsoft::WRL::ComPtr<IMMDeviceEnumerator> enumerator;
        checked(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_INPROC_SERVER,
                                 IID_PPV_ARGS(&enumerator)), "Music session endpoint enumeration");
        Microsoft::WRL::ComPtr<IMMDeviceCollection> devices;
        checked(enumerator->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &devices), "Music session render endpoints");
        UINT deviceCount = 0;
        checked(devices->GetCount(&deviceCount), "Music session endpoint count");
        for (UINT index = 0; index < deviceCount; ++index) {
            Microsoft::WRL::ComPtr<IMMDevice> device;
            checked(devices->Item(index, &device), "Music session endpoint");
            LPWSTR endpointRaw = nullptr;
            checked(device->GetId(&endpointRaw), "Music session endpoint identity");
            std::unique_ptr<wchar_t, decltype(&CoTaskMemFree)> endpointOwner(endpointRaw, CoTaskMemFree);
            const std::wstring endpoint = endpointRaw ? endpointRaw : L"";
            Microsoft::WRL::ComPtr<IAudioSessionManager2> manager;
            checked(device->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL, nullptr,
                        reinterpret_cast<void**>(manager.GetAddressOf())), "Music session manager");
            Microsoft::WRL::ComPtr<IAudioSessionEnumerator> sessions;
            checked(manager->GetSessionEnumerator(&sessions), "Music session enumeration");
            int count = 0;
            checked(sessions->GetCount(&count), "Music session count");
            for (int slot = 0; slot < count; ++slot) {
                Microsoft::WRL::ComPtr<IAudioSessionControl> base;
                checked(sessions->GetSession(slot, &base), "Music session control");
                ComPtrControl control;
                checked(base.As(&control), "Music session extended control");
                AudioSessionState state;
                checked(control->GetState(&state), "Music session state");
                // This short guard tests the currently rendering streams.
                // Old inactive cross-process sessions do not prove anything
                // about today's selected source and must not be mutated.
                if (state != AudioSessionStateActive) continue;
                DWORD pid = 0;
                const auto pidResult = control->GetProcessId(&pid);
                if (FAILED(pidResult)) continue; // A disappearing unrelated session is irrelevant.
                if (!pid || !tree.contains(pid)) continue;
                if (pidResult != S_OK) {
                    std::ostringstream reason;
                    reason << "Active music session PID " << pid << " spans unverified processes (HRESULT 0x"
                           << std::hex << uint32_t(pidResult) << ')';
                    throw std::runtime_error(reason.str());
                }
                if (control->IsSystemSoundsSession() == S_OK)
                    throw std::runtime_error("System sounds session cannot belong to music route");
                auto entry = std::make_unique<Entry>();
                entry->endpoint = endpoint;
                entry->instance = instanceId(control.Get());
                entry->process = tree.at(pid);
                entry->control = std::move(control);
                checked(entry->control.As(&entry->volume), "Music session simple volume");
                entry->originalMuted = readMute(*entry);
                checked(entry->volume->GetMasterVolume(&entry->originalVolume), "Music session original volume");
                if (!std::isfinite(entry->originalVolume) || entry->originalVolume < 0 || entry->originalVolume > 1)
                    throw std::runtime_error("Music session returned invalid volume");
                entry->events.Attach(new SessionEvents(context_, entry->originalMuted));
                checked(entry->control->RegisterAudioSessionNotification(entry->events.Get()), "Music session notifications");
                entry->registered = true;
                verify(*entry);
                entries_.push_back(std::move(entry));
            }
        }
        if (entries_.empty()) throw std::runtime_error("Verified music process has no audio sessions");
    }
};
} // namespace gluspot
