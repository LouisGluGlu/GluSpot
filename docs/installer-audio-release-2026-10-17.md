# Installer, synchronized playback and output cleanup — October 17, 2026

GluSpot remains **6.412** on `codex/ui-visual-alignment`. This follow-up covers
the bottom installation progress bar, the installer/application transition,
early audio replay during synchronization preparation, and the reported stutter
after reducing multiple playback outputs to one.

## Resulting behavior

- Extraction progress uses a native antialiased maximum-radius capsule in the
  directory-selection row. Actual extraction drives its fill and percentage.
  The perimeter progress assets and their residual rim are removed. Completion
  fades the lower content and smoothly contracts the existing card.
- The installed application initializes during the existing completion
  storyboard. The installer reveals it at the top of the screen and retains its
  own island until a visible application surface acknowledges its first paint.
  Named-event waits are bounded and do not block Qt's event loop.
- Cold group setup and song/seek restarts hold the real decoder position and
  keep the private Qt output muted until the native PCM timeline can accept it.
  The beginning enters the delayed synchronized schedule once. User pause and
  stream-recovery intent remain authoritative; failures restore owned playback.
- Stopped PCM sessions immediately discard queued audio and active member
  references. Lock-protected stop/epoch checks prevent a waiting producer from
  repopulating a retired queue, and cleanup continues when pipe close fails.
- Destroyed device rows immediately release their hover records, bound
  callbacks and cached images even while the animation timer is idle. Removing
  a pressed control safely cancels its pointer capture.

An established group reduced to one output keeps its presentation clock to
avoid jumping ahead to the decoder position. Its remaining helper is active
playback infrastructure. Removed member transports are retired asynchronously;
the tested paths do not leave an additional old group running.

## Verification

The final native Windows command passed **5061 tests in 297.613 seconds**:

```powershell
$env:QT_SCALE_FACTOR='1'
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v
```

The log `logs/oct17-installer-audio-lifecycle-full.log` contains no skips,
Python tracebacks, fatal exceptions or Qt callback errors. All **105** frozen
implementation hashes were unchanged after the run; the snapshot is
`.runtime/oct17-installer-audio-lifecycle-source-freeze.json`.

Focused evidence includes:

| Area | Evidence |
| --- | --- |
| Native installer progress | 18 checks at DPR 1/2, real compositor pixel export |
| Installer/application protocol | 16 native cross-process checks, fast/slow/missing paint |
| Audio preparation | 287 related tests; 12 checks with actual muted Qt decoding |
| PCM lifecycle | 173 related tests; 200 repeated inert sessions with constant handles/threads and zero stopped PCM |
| Hover and output panel | 94 related tests; 120 device replacements retain 3 live hover records instead of 124 |
| Native member removal | Three actual-helper localhost AP2 runs; departed member TEARDOWN, continuous retained packets, handles 150 to 146 |
| Native retirement ownership | 1000 renderer owners and 1000 future captures released; retained renderer keeps advancing |

The panel profile demonstrates reference cleanup rather than a measured speed
improvement: open-cycle P95 was 12.163 ms before and 12.325 ms after. The native
single-member intervals showed a memory plateau, but these short measurements
do not establish a long-session memory ceiling or identify the cause of every
physical-device stutter.

Detailed implementation and reproduction evidence:

- [Installer progress and handoff](installer-progress-handoff-2026-10-17.md).
- [Cold synchronization and repeated-introduction correction](owned-output-preparation-2026-10-17.md).
- [Stopped PCM lifecycle](owned-output-lifecycle-2026-10-17.md).
- [Native and UI resource measurements](output-resource-profile-2026-10-17.md).

## Rebuilt artifacts

The final `build-exe.bat -NoPause` completed successfully, including all **12
native suites in 73.12 seconds**. The frozen EXE's diagnostic acceptance passed
without opening Qt, reading user settings or starting a receiver session. The
read-only package audit passed **246/246** checks over **100 source modules**,
the entrypoint and packaged resources. It found **8397** payload files before
adding the portable build receipt and usage instructions.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `release/GluSpot-6.412/GluSpot.exe` | 10,329,303 | `366685149efec4cb69f2c8e5bb56da37e35dafb57f178fc3d23d8746de16d0e4` |
| `release/GluSpot-6.412-2026-10-17-installer-audio-Setup-x64.exe` | 161,265,909 | `0c3d4d01f4007d0a521465f432944c4cf639771301fe606bce59d373c9d80e78` |
| `release/GluSpot-6.412-2026-10-17-installer-audio-windows-x64.zip` | 212,817,373 | `7a055febf2e8e9ff402babf6d528d647020749f7483f3fd752147f84aed358f8` |

The canonical `release/GluSpot-6.412-Setup-x64.exe` matches the dated Setup byte
for byte. The ZIP contains **8399** unique entries, passed its complete CRC
check and contains byte-identical `BUILD-INFO.json` and `使用说明.txt` receipts.
The canonical Setup, dated Setup and ZIP have matching SHA-256 sidecars. Keep the
portable EXE together with its `_internal` directory. Previous dated packages
and backups are retained. These remain unsigned internal Windows x64 builds.

Final local evidence:

- `logs/oct17-installer-audio-lifecycle-build.log`.
- `logs/oct17-installer-audio-lifecycle-setup-build.log`.
- `.runtime/release-audit-2026-10-17-installer-audio/result.json`.
- `logs/oct17-installer-audio-frozen-acceptance.json`.
- `logs/oct17-installer-audio-release-artifacts.json`.

## Scope limits

The audio experiments used inert processes, volume-zero Qt outputs and localhost
receivers. They did not operate the user's installed application, real HomePod,
M6/AirPort, Bluetooth or WASAPI output, or change a system audio setting.
Physical listening, wireless interruptions, long-session drift, real driver
teardown and complete interactive installer/display smoothness remain to be
validated. The corrections address demonstrated source defects; they do not
claim that every reported physical-device delay has been reproduced or fixed.

An earlier native Python process access violation remains unexplained. The
final full run passed; no crash-specific correction or claim is made here.
