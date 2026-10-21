# Output resource lifecycle profile — October 17, 2026

This follow-up investigates the report that selecting one device after multi-device playback feels slow, with a possible suspicion of unreleased memory. It records measurements of native retirement and the retained single-output coordinator. It does not establish the cause of physical-device latency or claim that the reported listening experience is fixed.

The product remains GluSpot **6.412**. This document adds evidence only; the profiling work did not alter production native code, the helper used by another process, system audio routing, endpoint volume/mute, or the running installed application.

## Findings and their scope

- A warm group reduced to one output intentionally retains its owned PCM helper and source timeline. The current implementation handles supported hot membership changes before the cold single-Windows-device route. Keeping that work alive is not, by itself, a leaked group.
- Three actual-helper runs against independent localhost AP2 receivers removed the departed member, kept the remaining receiver's packets continuous, and reaped the child and Python workers on stop. Handles decreased from 150 to 146; no continuing private-memory growth was observed in the measured final single-member interval.
- An offline native stress harness completed 1,000 local renderer retirements and 1,000 future-capture release checks. The surviving renderer advanced while retirement was deliberately blocked. Owner references were released, and handles did not accumulate.
- Separate Python and panel investigations found bounded stopped PCM retention and stale hover records respectively. Those have scoped cleanup fixes and their own evidence below. Neither result demonstrates that they were the cause of this user's hardware playback delay.

## Environment and reproducibility

Measurements were taken on native Windows 11 `10.0.26200`, with CPython `3.12.10`, in the existing `codex/ui-visual-alignment` worktree. Its Git HEAD was `0cde741f522530284d344e12ef85a19ce248de3d`, with the ongoing uncommitted branch changes present; that commit alone does not identify the full measured source tree.

The helper and receiver fixture were copied from `build/native-airplay/` into ignored `.runtime/` paths before profiling. Those snapshots prevented another build from changing the executables under this experiment. The native build cache selected Release and the MSYS2 UCRT64 compiler. The separate retirement harness was compiled with `c++.exe (MSYS2 Rev4) 16.2.0`, C++20, `-O2 -static -UNDEBUG`, and the existing Windows definitions/libraries.

| Executable snapshot | SHA-256 |
| --- | --- |
| `.runtime/profile-native-helper-2026-10-17.exe` | `15aac3f83face8332caf0fc77bf11acf9377bd1a72342443df3b7b53ee4e827e` |
| `.runtime/profile-native-fixture-2026-10-17.exe` | `dd85ba91a6ca14b2abbf4efeffe37542ebf289d34d7bb75fc764c6154f68f9f3` |
| `.runtime/native_retired_future_profile_2026_10_17.exe` | `0855bec3bda432b2f314d295023710c5a5ab4f3920f15c9961bcdc6fa36c4ab8` |

Generated programs, executables, raw samples and logs stay in ignored `.runtime/`; they are local verification artifacts rather than source-controlled runtime files. The source fixtures needed for an equivalent rerun are [the localhost receiver](../native/airplay_sender/owned_membership_receiver_fixture.cpp), [the existing membership smoke harness](../tools/smoke_owned_pcm_loopback_membership.py), and [the fake local renderer tests](../native/airplay_sender/group_local_render_tests.cpp).

Local experiment sources and results:

- `.runtime/profile_native_retirement_2026_10_17.py` → `.runtime/native-retirement-profile-2026-10-17.json`.
- `.runtime/native_retired_future_profile_2026_10_17.cpp` → `.runtime/native-retired-future-profile-2026-10-17.json`.
- `.runtime/profile_owned_pcm_lifecycle_2026_10_17.py` → `.runtime/owned-pcm-lifecycle-profile-2026-10-17.json`.
- `.runtime/profile_audio_panel_readonly.py` → `.runtime/audio-panel-readonly-profile-2026-10-17-before.json` and `-after.json`.

## Why a single warm output keeps the coordinator

[OwnedAudioOutputController._choose](../api/owned_audio_outputs.py) checks whether an authenticated, streaming group can accept `reconfigure_pcm` within the same media token and session epoch. When that succeeds, a single retained Windows endpoint follows this hot path as well. The direct Qt single-Windows-device selection is the later cold-route case.

The distinction preserves the existing PCM source position, presentation delay, renderer cursor and authenticated retained transports. Switching a warm singleton immediately to a fresh direct Qt output would replace its presentation clock and could advance or repeat the audible source. This investigation preserves that timing contract rather than introducing an unmeasured route change as a memory optimization.

The retained coordinator still does work: the native loop sleeps for 5 ms between iterations, publishes ordinary statistics at roughly 500 ms intervals, conditions shared PCM and maintains history. Python keeps its writer and native status readers. A retained Windows renderer also keeps its own WASAPI render worker. The localhost experiment below measures the actual coordinator/network path with one AP2 member; it does **not** measure that physical Windows renderer.

Several PCM payload allocations have explicit bounds, independent of whether a group has just shrunk:

| Buffer | Verified payload bound |
| --- | --- |
| Python pending PCM queue | 352,800 bytes, from `OwnedPcmProcess.MAX_QUEUED_BYTES` |
| Native immutable PCM history | 529,200 bytes: 132,300 stereo frames of signed 16-bit PCM |
| Native pipe capture ring | 262,144 bytes: `RingBuffer<int16_t>(44100 * 2)` rounds the requested sample count up to 131,072 elements |

The ring's actual allocation follows [RingBuffer's power-of-two constructor](../third_party/airplay2-sender-cpp/src/ring_buffer.h); 176,400 bytes would describe only the unrounded request. These are selected payload bounds, not a total process-memory estimate. Encoders, network backlogs, conditioning storage, thread stacks, code and allocator retention add their own costs.

## Actual helper: one → two → one localhost receivers

The profiling script reuses `OwnedPcmProcess` and the independent strict-SRP AP2 receiver fixture. The fixture binds only to `127.0.0.1` ephemeral sockets and opens no audio endpoint. It receives and decrypts production packets and checks packet sequence, RTP-frame and nonce continuity. A third fixture receiver is created by the existing fixture but is never selected in this experiment.

Each of three fresh runs starts one receiver, feeds nonzero stereo 44.1 kHz / 16-bit PCM in 352-frame packets, waits three seconds after streaming, joins a second receiver, retains two members for five seconds, removes the original receiver, then retains the second receiver for eight seconds. The sample source is constant `(+2500, -2500)` PCM; this is not representative music or a codec-content benchmark.

The parent samples native process CPU time, handles, threads, private bytes and working set approximately every 250 ms using Windows process APIs and Toolhelp. Stage metrics discard the first second of samples after each stage transition. CPU percentage is process CPU seconds divided by wall time, expressed relative to **one logical core**. Measurements occurred alongside other development work, including compilation, so the short CPU windows should not be used to rank routes or promise a percentage on another machine.

| Run | Whole run | Observed removal confirmation | Initial / dual / retained-single handles | Steady native threads | Retained-single private bytes, min–max | Retained-single CPU, one core | CPU sample window | Stop |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 20.109 s | 0 ms | 145 / 150 / 146 | 6 | 5,156,864–5,156,864 | 1.157% | 6.750 s / 28 samples | 109 ms |
| 2 | 20.094 s | 0 ms | 145 / 150 / 146 | 6 | 4,935,680–4,952,064 | 0.477% | 6.547 s / 27 samples | 109 ms |
| 3 | 20.125 s | 16 ms | 145 / 150 / 146 | 6 | 5,230,592–5,230,592 | 0.231% | 6.750 s / 28 samples | 109 ms |

The 0 ms observations mean the request and acknowledgement were seen within the same parent clock/poll interval; they do not establish instantaneous native removal. Total run time was **60.328 seconds**, excluding fixture setup and final cleanup. Initial-single CPU samples were 12.500%, 0.000% and 5.157% over only 1.515–1.750 seconds; dual samples were 0.446%, 4.979% and 0.000% over 3.500–3.766 seconds. That variability further limits interpretation of the CPU figures.

All three runs recorded:

- The same helper process, PCM writer, status reader and stderr reader throughout the membership changes.
- One final confirmed receiver, no pending IDs, the same run-specific PCM epoch, and an advancing nonzero global timeline in native statistics. Final group statistics explicitly reported `local_renderer: false` and `acoustic_alignment_verified: false`.
- TEARDOWN on the removed receiver, with no TEARDOWN on the receiver still playing before the explicit final stop.
- Zero independent audio decryption failures, packet-continuity failures and control-decryption failures. Removed/retained receiver packet counts were 1,478/1,852, 1,479/1,851 and 1,478/1,852 respectively; authenticated event-channel acknowledgements were present.
- No native/pipe error, and the helper child, Python writer, both reader threads and receiver fixture all terminated after the final stop.

Memory remained above the initial single-receiver state after a second receiver had been used. That alone does not identify a leak: a process can retain allocator pages and bounded shared storage after releasing owners. The measured late single-member interval was flat in runs 1 and 3 and varied by 16,384 bytes in run 2. This is evidence about these short intervals, not a long-session leak certification or an attribution of every retained byte.

## Native retirement ownership and 1,000-cycle stress

In [group_main.cpp](../native/airplay_sender/group_main.cpp), a departed receiver is moved out of active membership into a `std::async` capture whose worker calls `sender->stop()`. A departed local endpoint first calls `GroupLocalRendererSet::remove`, which signals `requestStop()` immediately; its renderer is then destroyed on an asynchronous worker. The packet loop uses `wait_for(0ms)` and erases ready futures, rather than joining a retiring device synchronously on the packet path. Active plus retiring identities are limited to eight; a new request cannot reuse a still-retiring identity or physical target.

The stress harness includes the existing fake-renderer test source and invokes the production `GroupLocalRendererSet` template. For each of 1,000 cycles it adds and removes a fake endpoint while keeping one renderer active. A gate deliberately blocks the removed renderer's destructor. The surviving renderer must render another packet while that retirement future is still not ready. Releasing the gate allows the same zero-wait/erase pattern as production to reap it; destruction and expiration of its weak state owner are asserted.

A second check per cycle moves a unique owner into an asynchronous callable, performs its stop operation, erases the ready future and asserts that its weak ownership token expires. This isolates future-capture lifetime semantics. It is a model of the network capture pattern, not an execution of 1,000 real `RaopSender` teardowns; actual network retirement is covered by the three localhost runs above.

| Checkpoint | Handles | Threads at snapshot | Private bytes |
| --- | ---: | ---: | ---: |
| Before cycles | 64 | 2 | 1,540,096 |
| 200 | 65 | 2 | 1,540,096 |
| 400 | 65 | 2 | 1,544,192 |
| 600 | 65 | 2 | 1,544,192 |
| 800 | 65 | 2 | 1,544,192 |
| 1,000 | 65 | 1 | 1,511,424 |
| After loop and 200 ms settle | 64 | 1 | 1,511,424 |

All **1,000 renderer owners**, **1,000 future captures** and **1,000 retained-renderer progress assertions** passed. Checkpoint snapshots can include a worker finishing its thread epilogue; settled resources did not increase. The profiler's original resource verdict required exact thread equality and therefore rejected a decrease from two to one despite passing all ownership assertions. The final harness accepts no resource increase, preserves the raw counts above and passed. No production change resulted from that instrumentation correction.

## Related scoped cleanup evidence

### Stopped Python PCM state

The separate cleanup in [owned_pcm_process.py](../api/owned_pcm_process.py) drains queued audio under the PCM lock, resets pending-byte accounting and releases active member/evidence references after stop, while preserving terminal diagnostics. The submit path rechecks stop, epoch and process state inside that same lock so a waiting submitter cannot repopulate the retired queue. Pipe disposal and reference cleanup execute through `finally` even if close raises.

The inert lifecycle regression first reproduced 327,680 retained PCM bytes before the change. The post-change 200-cycle profile used mocked/inert processes, not a native child: handles remained 186 and threads remained one at the baseline and at cycles 50/100/150/200. Each checkpoint had zero pending PCM bytes/packets and no active process, PCM writer, pipe or reader references. Traced live Python allocation was 15,608–17,152 bytes, with a 685,198-byte traced peak. See `.runtime/owned-pcm-lifecycle-profile-2026-10-17.json`.

An independent run of `python -m unittest tests.test_owned_pcm_lifecycle tests.test_owned_pcm_membership -v` passed **13 tests in 1.032 seconds**. Source review found start/stop lifecycle calls serialized by the routing worker; the existing stopped/epoch/queue checks and pipe-object/epoch cleanup guard preserve the later pipe when an old constructor finishes late. This review does not assert that arbitrary concurrent calls to the public start/stop methods form a separately supported interface.

### Idle output-panel hover records

The panel experiment used a fake provider and offscreen Qt. It performed warm-up, 120 open/select/dismiss cycles, cached snapshot reads, and then 120 changes of device identity with deferred widget deletion and garbage collection. With three device rows, ordinary repeated opening retained four hover records before and after the fix. Changing identities exposed the defect: stale records increased **4 → 124** even as live row count decreased **3 → 2**. After the scoped [control_hover.py](../local/control_hover.py) destruction/pruning fix, the same sequence ended **4 → 3** records, matching the remaining two rows plus the other live control.

Before/after open-cycle median was 10.161/10.257 ms and P95 was 12.163/12.325 ms. These numbers establish no speed improvement; the demonstrated correction is stale callback/record retention while idle. All seven observed panel timers were stopped after hiding. Process RSS was unavailable in this harness (`null`), so its Python/QObject counts must not be presented as measured process memory. The existing focused hover/panel run passed **94 tests in 2.044 seconds**, recorded in `.runtime/hover-lifecycle-focused-2026-10-17.log`. No additional Qt run was launched for this document.

## Remaining limits

The experiments opened no physical HomePod, Bluetooth headset, speaker or WASAPI render endpoint and changed no system audio setting. They do not measure receiver acoustic buffering, Wi-Fi congestion, actual driver stop latency, sleep/resume, a long music session, multi-monitor composition, or the installed application's complete UI under the user's workload.

The evidence supports bounded ownership and cleanup in the tested paths. It leaves the reported real-device responsiveness and the two-second replay/synchronization experience to separate physical-device validation. A repeat on that path needs timestamps for UI selection, membership request/confirmation, renderer/receiver presentation and audible onset; process memory alone cannot establish the cause.
