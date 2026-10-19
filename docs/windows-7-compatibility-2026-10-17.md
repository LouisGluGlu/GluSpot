# Windows 7 compatibility — October 17, 2026

The screenshot's installer font failure is corrected. A separate **Windows 7 SP1 x64 compatibility candidate** uses CPython 3.8.10, PySide2 5.15.2.1 / Qt 5.15.2 and official Node 13.14.0. The user explicitly selected program playback only: system SMTC capture, publication and associated headset controls are unavailable. The modern package remains separate and targets Windows 10 1809 or later.

This is an internal compatibility test build, **not real Windows 7 acceptance**. The available host is Windows 11 x64. The reported computer's architecture is unknown; no 32-bit build is produced or claimed. Version remains 6.412.

## Root causes

The screenshot reports `Installer graphics: open UI font (14)`. The installer requested only `Microsoft YaHei UI`; GDI+ status 14 means `FontFamilyNotFound`. Microsoft's [Windows 7 font inventory](https://learn.microsoft.com/en-us/typography/fonts/windows_7_font_list) lists Microsoft YaHei; the UI family appears in the [Windows 8 inventory](https://learn.microsoft.com/en-us/typography/fonts/windows_8_font_list).

Fixing the font cannot repair the original application's Windows loader failures:

| Original component | Actual package evidence | Separate candidate |
| --- | --- | --- |
| Python | 3.12.10 imports `CopyFile2`, `PssCaptureSnapshot`, `SetWaitableTimerEx` and other unavailable APIs. | Official CPython 3.8.10 x64 in an isolated toolchain. |
| Qt | Qt6Core 6.12.0 imports `WaitOnAddress`, `SetThreadDescription` and modern WinRT/ICU dependencies. | Native Qt 5.15.2 with a build-only Qt 6 import facade. |
| Online runtime | Node 24.19.0 imports `GetSystemTimePreciseAsFileTime`; pinned adapters use newer JavaScript/Node APIs. | Unmodified official Node 13.14.0 with a separate compatibility mirror. |
| System media | SMTC session APIs require Windows 10 1809. | Program playback is locked on; system workers never start. |
| Multimedia | Production code uses Qt 6 player/audio/PCM interfaces. | Explicit Qt 5 player, private gain/mute and real QAudioProbe adapters. |

References: [Python 3.8](https://docs.python.org/3.8/using/windows.html), [Qt 5.15 platforms](https://doc.qt.io/archives/qt-5.15/supported-platforms.html), [Qt 6 requirements](https://doc.qt.io/qt-6/supported-platforms.html), [Node 13.14 platforms](https://github.com/nodejs/node/blob/v13.14.0/BUILDING.md#platform-list), [SMTC minimum](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager).

## Implemented changes

The installer resolves Microsoft YaHei UI, Microsoft YaHei, SimSun, Segoe UI, Arial, then GDI+ generic sans-serif. Missing bold falls back to regular. Failed candidates release their native handles; directory/wizard typography shares the resolved family. Existing animation/layout remains intact. The legacy installer requires SP1/x64 and checks DLL-loader/UCRT prerequisites before replacing files. The modern installer now rejects systems below its actual Windows 10 1809 floor.

`local/runtime_platform.py` detects the real Windows version without Qt/WinRT imports. Settings, first-use choices, controller and provider boundaries enforce owned-only operation, including stale system-only preferences. The system provider starts no asynchronous loop and imports no WinSDK; the SMTC bridge creates no worker or active timer. The retired per-player scraping tree is not restored.

`packaging/windows/legacy_qt/` is only on the legacy build path. Native PySide2 objects provide rendering, input coordinates, fonts, event loops and multimedia. Source corrections replace Python APIs unavailable in 3.8, including prefix/suffix helpers, runtime generic aliases and bisect's newer `key` argument. The timeline replacement preserves rightmost-equal behavior and logarithmic complexity.

Qt 5 supplies actual PCM through QAudioProbe and independent music/DJ mute/gain. It cannot map the current explicit WASAPI endpoint IDs through Qt 6's device API. **Local playback uses Windows' default output; choose another local output in Windows itself.** Legacy snapshots disable unsupported local route actions and explain the restriction. Default-output tracking remains stable across refreshes/system changes. AirPlay, mixed output synchronization and HomePod listening have not been accepted on Win7.

The online mirror retains NetEase 4.41.1, QQ 3.1.4, Kugou 1.6.0 and the existing provider/stdio protocol. CommonJS syntax is lowered for Node 13.14, with bounded language, abort, crypto and filesystem compatibility. Original manifests/licenses remain unchanged. ESM entrypoints are retained as source evidence outside the verified path. TLS verification is unchanged. See [recipe and limits](../packaging/windows/legacy-online/README.md). Node 13 and Python 3.8 are end of life; this selected legacy variant does not imply current upstream support.

## Build and prerequisites

Use Windows 7 **SP1 x64** with DLL-loader updates providing `AddDllDirectory` (KB2533623 or a superseding update), Universal C Runtime (KB2999226 or a superseding update), appropriate graphics/audio drivers and media components. N/KN editions may need their Media Feature Pack. Actual target patch level, codecs and TLS remain to be checked. A portable archive cannot bypass these OS requirements.

Prepare a dedicated official CPython 3.8 x64 environment, then run from the project root:

```powershell
& <python38> -m pip install -r packaging/windows/requirements-win7.txt
& <python38> packaging/windows/prepare-win7-licenses.py
& packaging/windows/legacy-online/prepare-runtime.ps1
& packaging/windows/build-win7.ps1 -PythonPath <python38> -Installer -ReuseAssets
```

The native helper and installer assets must already be prepared using their established recipes. `GluSpot-win7.spec` refuses a modern interpreter, missing legacy provenance or missing license materials. It reuses the replaceable zeroconf hook, excludes unused WinRT audio/position/Bluetooth binaries and retains Qt5Qml, a real dependency of the PySide2 wheel. Exact legacy library notices and checked upstream sources accompany the package.

Independent outputs are `release/GluSpot-6.412-Win7-x64/` and `release/GluSpot-6.412-Win7-Setup-x64.exe`. Keep the complete portable folder together. Existing installation-directory, version-confirmation, safe overwrite, state preservation and uninstall rules apply.

## Verification evidence

All checks ran on Windows 11. Native UI/installer checks are serialized. Fixtures use temporary state; decoding is privately muted at volume zero. No receiver is contacted and the user's output/active application is not changed.

- Modern regression: **5183 tests passed in 367.200 seconds**, no failures/skips (`logs/win7-modern-full-2026-10-17.log`). This full run preceded the final legacy endpoint guards and diagnostic CLI. Subsequent focused verification passed **79 routing/reconfiguration tests / 5.545 seconds** and **12 diagnostic/static-audit tests / 0.121 seconds** (`logs/win7-legacy-owned-output-focused-2026-10-17.log`, `logs/win7-audit-cli-final-2026-10-17.log`).
- Qt 5 UI: **50 modules / 396 interfaces** imported; DPR 1/2 rendered island, entry, library and song page without callback exceptions. Evidence: `.runtime/win7-qt-ui-2026-10-17/result.json` and `...-dpr2/result.json`.
- Qt 5 multimedia contract: **40 assertions**, two real temporary WAVs, pause/seek/resume/EOF/next and actual PCM (`.runtime/win7-media-contract/evidence.json`).
- Production source diagnostic: **58 checks / 6.532 seconds**. Actual `OwnedMusicPlayer`, stream cache and bridge exercised offline preparation, pause-clock stability, seek/resume, next and EOF continuation. It received 29 production PCM buffers and all eight new workers exited after close. Hidden first-use restrictions, four song-page layouts, absent system/SMTC workers and WinSDK imports also passed (`.runtime/win7-source-package-check-2026-10-17.json`).
- Actual Node 13 mirror: **nine offline checks**, including RFC 5869 HKDF, QQ encryption/session integrity, QR/module behavior, loopback HTTP redirects/cookies/cancellation and three stdio preloads. All 11780 output hashes rechecked (`build/online-music-win7/OFFLINE-ACCEPTANCE.json`, `SOURCE.json`, `.runtime/win7-online-production-integrity-20261017.json`).
- Original modern payload audit confirmed seven Python, nine Qt and one Node known direct-import blockers (`.runtime/win7-runtime-static-audit-2026-10-17-final.json`). Static absence of a known blocker never certifies compatibility.

The final frozen executable ran from a temporary working directory with source/Python/Qt path overrides removed. At **DPR 1 and DPR 2, all 58 checks passed in 6.704 and 6.109 seconds**, respectively. The reported runtime is genuinely frozen CPython 3.8.10 / PySide2 5.15.2.1 / Qt 5.15.2 using the Windows platform plugin. Each run received 29 production PCM buffers; all eight new workers exited, with zero callback exceptions. Reports: `.runtime/win7-delivery-frozen-check-dpr{1,2}-2026-10-17.json`.

The final native audit read **119 EXE/DLL/PYD files**, including direct and delay imports, without a known blocker in the limited mapping. It confirmed the sole native Qt runtime hook is PySide2; Qt 6, Python 3.12, host UCRT/API-set DLLs, unused WinRT audio and optional pywin32/CFFI binaries are absent. Provenance and version evidence: `.runtime/win7-delivery-component-audit-2026-10-17.json`, `.runtime/win7-delivery-native-import-audit-2026-10-17.json`. This remains static evidence, not full dependency/OS compatibility certification.

The final online runtime matches staging for **11780 SOURCE hashes and eight entrypoint/receipt files**. Three-platform offline preloading completed in 4.115 seconds; all nine original offline checks passed, including the unchanged 15-second stdio-preload limit. An initial aggregate-preload assertion failed without recording an error code, so its precise cause remains unknown; it was not reproduced and the test's failure message now records status, signal and error details. No production timeout was raised. Evidence: `.runtime/win7-online-delivery-diagnostic-2026-10-17.json`, `.runtime/win7-delivery-online-acceptance-repeat-2026-10-17.json`.

On the final installer source, native font verification passed **30 checks / five scenarios** and prerequisite verification passed **13 checks / three scenarios**. Both record normalized source SHA-256 `a9b84290efe2e3534e5fecadee0717734227e2154a0dee7862c52a3c421a3d16`. Fixtures exited before real payload installation or user registration. Reports: `.runtime/win7-delivery-installer-{fonts,prerequisites}-2026-10-17/evidence.json`. Earlier unchanged progress/handoff implementation passed 18/16 checks, and installer replacement/state/uninstall logic passed ten isolated scenarios (`logs/win7-installer-final-smoke-2026-10-17.log`). The final legacy installer also describes its owned-only first-use behavior.

The bundled legacy notices/sources cover **10 pinned packages, 45 notices and seven source archives**; all 55 files match the prepared license tree and every manifest hash is valid. Optional packaging/setuptools modules retain their matching notices. Evidence: `.runtime/win7-legacy-licenses-final-2026-10-17.json`. `_internal/README-Win7.txt` explains prerequisites, restrictions and the offline diagnostic command.

## Test artifacts

The independent application folder is `release/GluSpot-6.412-Win7-x64/`. Neither artifact contains test accounts or saved user settings.

| Artifact under `release/` | Bytes | SHA-256 |
| --- | ---: | --- |
| `GluSpot-6.412-2026-10-17-Win7-SP1-Setup-x64.exe` | 213091687 | `bf6ac9d61ed5ea2ec8cebd2ecb809359593ab13458b152dbf6f4de2033baddba` |
| `GluSpot-6.412-2026-10-17-Win7-SP1-portable-x64.zip` | 257242796 | `b1810f1bd6338f0dfd208a3c43f948feddc42a78b9d438889098417d6775414b` |

The ZIP contains **12224 files / 501594939 uncompressed bytes**, with every entry passing CRC, no duplicate/parent-traversal entries, and matching executable/readme/runtime manifest hashes. The dated Setup is byte-identical to the canonical `GluSpot-6.412-Win7-Setup-x64.exe`. Checksums also accompany the artifacts in `GluSpot-6.412-2026-10-17-Win7-SP1-SHA256.txt`.

Build/integrity evidence: `logs/win7-pyinstaller-delivery-2026-10-17.log`, `logs/win7-delivery-installer-build-2026-10-17.log`, `.runtime/win7-delivery-{zip,installer}-integrity-2026-10-17.json`. The original modern Dock64/HomePod Setup and ZIP retain their recorded SHA-256 values; they were not overwritten or relabeled.

## Required real Win7 acceptance

Before an accepted release, test installation/cold start on real Win7 SP1 x64 hardware or a VM with stated prerequisites, then actual login/TLS, music decoding, lyrics, transitions, window/island behavior, upgrade/state preservation and uninstall. Test drivers and physical devices separately. No Win7 VM/hardware was available in this task; 32-bit Windows remains unsupported.
