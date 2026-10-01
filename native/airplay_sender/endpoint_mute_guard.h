// GluSpot owns one explicit Windows render endpoint's mute during a cast.
// SDK ABI: endpointvolume.h / Microsoft's Endpoint Volume Controls sample.
// This changes no master/channel volume and never changes a default endpoint.
#pragma once
#include <windows.h>
#include <endpointvolume.h>
#include <mmdeviceapi.h>
#include <wrl/client.h>
#include <cstdint>
#include <sstream>
#include <stdexcept>
#include <string>

namespace gluspot {
class EndpointMuteGuard {
public:
    // Construction reads only. mute() belongs after receiver authentication.
    explicit EndpointMuteGuard(const std::wstring& endpoint) {
        if (endpoint.empty()) throw std::invalid_argument("An explicit render endpoint is required");
        const HRESULT initialized = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        if (initialized != RPC_E_CHANGED_MODE) {
            checked(initialized, "Mute guard COM initialization");
            comOwned_ = true;
        }
        try {
            Microsoft::WRL::ComPtr<IMMDeviceEnumerator> enumerator;
            checked(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_INPROC_SERVER,
                                     IID_PPV_ARGS(&enumerator)), "Mute guard endpoint enumeration");
            Microsoft::WRL::ComPtr<IMMDevice> device;
            checked(enumerator->GetDevice(endpoint.c_str(), &device), "Mute guard source endpoint");
            Microsoft::WRL::ComPtr<IMMEndpoint> direction;
            checked(device.As(&direction), "Mute guard endpoint direction");
            EDataFlow flow;
            checked(direction->GetDataFlow(&flow), "Mute guard endpoint direction");
            if (flow != eRender) throw std::invalid_argument("Mute guard requires a render endpoint");
            checked(device->Activate(__uuidof(IAudioEndpointVolume), CLSCTX_ALL, nullptr,
                                     reinterpret_cast<void**>(volume_.GetAddressOf())), "Mute guard activation");
            originalMuted_ = readMute();
            checked(CoCreateGuid(&context_), "Mute guard event context");
        } catch (...) {
            volume_.Reset();
            if (comOwned_) CoUninitialize();
            throw;
        }
    }
    EndpointMuteGuard(const EndpointMuteGuard&) = delete;
    EndpointMuteGuard& operator=(const EndpointMuteGuard&) = delete;
    EndpointMuteGuard(EndpointMuteGuard&&) = delete;
    EndpointMuteGuard& operator=(EndpointMuteGuard&&) = delete;

    ~EndpointMuteGuard() noexcept {
        try { restore(); } catch (...) {
            // The Python owner has the original explicit endpoint/mute state
            // and must run its recovery after failed/forced helper teardown.
        }
        volume_.Reset();
        if (comOwned_) CoUninitialize();
    }

    bool originalMuted() const noexcept { return originalMuted_; }

    void mute() {
        if (applied_) return;
        if (readMute() != originalMuted_)
            throw std::runtime_error("Source endpoint mute changed before HomePod was ready");
        if (!originalMuted_) {
            checked(volume_->SetMute(TRUE, &context_), "Mute source endpoint");
            // Record ownership before readback, so readback failure still
            // restores a successful SetMute during unwinding.
            changed_ = true;
        }
        if (!readMute()) throw std::runtime_error("Windows did not confirm source endpoint mute");
        applied_ = true;
    }

    bool isStillMuted() const {
        // An external unmute is a routing conflict. The caller should end the
        // cast instead of repeatedly overriding the user's hardware controls.
        return readMute();
    }

    void restore() {
        if (!changed_) return;
        checked(volume_->SetMute(originalMuted_ ? TRUE : FALSE, &context_), "Restore source endpoint mute");
        if (readMute() != originalMuted_)
            throw std::runtime_error("Windows did not confirm restored endpoint mute");
        changed_ = false;
        applied_ = false;
    }

private:
    Microsoft::WRL::ComPtr<IAudioEndpointVolume> volume_;
    GUID context_{};
    bool comOwned_ = false;
    bool originalMuted_ = false;
    bool changed_ = false;
    bool applied_ = false;

    bool readMute() const {
        BOOL muted = FALSE;
        checked(volume_->GetMute(&muted), "Read source endpoint mute");
        return muted != FALSE;
    }
    static void checked(HRESULT result, const char* action) {
        if (FAILED(result)) {
            std::ostringstream message;
            message << action << " failed (HRESULT 0x" << std::hex << uint32_t(result) << ')';
            throw std::runtime_error(message.str());
        }
    }
};
}  // namespace gluspot
