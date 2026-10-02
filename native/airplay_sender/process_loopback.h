// Windows process-loopback activation. Interface/ABI checked against Microsoft
// ApplicationLoopback and audioclientactivationparams.h; no PCM is stored here.
#pragma once

#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <wrl/client.h>
#include <atomic>
#include <cstdint>

namespace gluspot {

// LLVM-MinGW currently omits audioclientactivationparams.h. These are the exact
// SDK fields: ActivationType, then the union's ProcessLoopbackParams (PID/mode).
struct ProcessActivationParams {
    int32_t activationType = 1; // AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    DWORD targetProcessId = 0;
    int32_t processLoopbackMode = 1; // EXCLUDE_TARGET_PROCESS_TREE
};
static_assert(sizeof(ProcessActivationParams) == 12);

class ProcessActivation final : public IActivateAudioInterfaceCompletionHandler, public IAgileObject {
public:
    ProcessActivation(DWORD targetProcessId = 0, bool includeProcessTree = false) {
        event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        params_.targetProcessId = targetProcessId ? targetProcessId : GetCurrentProcessId();
        params_.processLoopbackMode = includeProcessTree ? 0 : 1;
        property_.vt = VT_BLOB;
        property_.blob.cbSize = sizeof(params_);
        property_.blob.pBlobData = reinterpret_cast<BYTE*>(&params_);
    }
    bool valid() const { return event_ != nullptr; }
    PROPVARIANT* property() { return &property_; }
    HANDLE event() const { return event_; }
    HRESULT result() const { return result_; }
    Microsoft::WRL::ComPtr<IAudioClient> client() const { return client_; }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (IsEqualIID(iid, __uuidof(IUnknown)) ||
            IsEqualIID(iid, __uuidof(IActivateAudioInterfaceCompletionHandler)))
            *out = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
        else if (IsEqualIID(iid, __uuidof(IAgileObject)))
            *out = static_cast<IAgileObject*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto count = --references_;
        if (!count) delete this;
        return count;
    }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT activation = E_FAIL;
        Microsoft::WRL::ComPtr<IUnknown> activated;
        result_ = operation->GetActivateResult(&activation, &activated);
        if (SUCCEEDED(result_)) result_ = activation;
        if (SUCCEEDED(result_)) result_ = activated.As(&client_);
        SetEvent(event_);
        // A self-reference covers timeout/cancellation: a late callback never
        // touches stack parameters, a released handler or a closed event.
        Release();
        return S_OK;
    }

private:
    ~ProcessActivation() { if (event_) CloseHandle(event_); }
    std::atomic<ULONG> references_{1};
    HANDLE event_ = nullptr;
    ProcessActivationParams params_;
    PROPVARIANT property_{};
    HRESULT result_ = E_PENDING;
    Microsoft::WRL::ComPtr<IAudioClient> client_;
};

inline HRESULT activateProcessLoopback(HANDLE stopEvent, Microsoft::WRL::ComPtr<IAudioClient>& client,
                                      DWORD targetProcessId = 0, bool includeProcessTree = false) {
    if (includeProcessTree && !targetProcessId) return E_INVALIDARG;
    // Load only the OS library. Dynamic lookup keeps the ordinary endpoint path
    // usable on systems without process-loopback support.
    static HMODULE audioLibrary = LoadLibraryExW(L"mmdevapi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!audioLibrary) return HRESULT_FROM_WIN32(GetLastError());
    using Activate = HRESULT (WINAPI*)(LPCWSTR, REFIID, PROPVARIANT*,
        IActivateAudioInterfaceCompletionHandler*, IActivateAudioInterfaceAsyncOperation**);
    const auto activate = reinterpret_cast<Activate>(GetProcAddress(audioLibrary, "ActivateAudioInterfaceAsync"));
    if (!activate) return E_NOTIMPL;
    Microsoft::WRL::ComPtr<ProcessActivation> handler;
    handler.Attach(new ProcessActivation(targetProcessId, includeProcessTree));
    if (!handler->valid()) return HRESULT_FROM_WIN32(GetLastError());
    Microsoft::WRL::ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
    handler->AddRef(); // Owned until callback or synchronous activation failure.
    const auto started = activate(L"VAD\\Process_Loopback", __uuidof(IAudioClient),
                                 handler->property(), handler.Get(), &operation);
    if (FAILED(started)) { handler->Release(); return started; }
    HANDLE events[]{stopEvent, handler->event()};
    const auto waited = WaitForMultipleObjects(2, events, FALSE, 4000);
    if (waited == WAIT_OBJECT_0) return HRESULT_FROM_WIN32(ERROR_CANCELLED);
    if (waited == WAIT_TIMEOUT) return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
    if (waited != WAIT_OBJECT_0 + 1) return HRESULT_FROM_WIN32(GetLastError());
    if (FAILED(handler->result())) return handler->result();
    client = handler->client();
    return S_OK;
}
} // namespace gluspot
