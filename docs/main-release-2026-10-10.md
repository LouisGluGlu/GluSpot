# Main consolidation and Windows test build, October 10, 2026

The user requested that the current integrated version become the latest
`main`, that its executable be built under `release`, and that the entire
project folder be copied to a dated directory under
`E:/Glusopt_waitingforlaunch`. Branch deletion requires a separate user
confirmation after preservation and verification.

## Source and history

The source directory was already on `main`, at old commit `4d080daa`, with
the reviewed work from subsequent tasks integrated as uncommitted files.
There were 17 local branches, no remote branches or configured remote, and
six registered worktrees. The five other worktrees were clean.

All 16 feature-branch tips are ancestors of `codex/lyric-ripple-fluidity`
(`fb78bfa`). The current product is preserved as it exists in the main
directory; the latest feature branch also carries older experimental
Bluetooth calibration that had deliberately not been integrated. A history
merge retains the feature ancestry while preserving the validated main
tree. It does not reintroduce those experimental files.

The current source, assets, tests, native sender, vendored source and license
metadata are committed. Existing deleted generated lyric files stay deleted;
their earlier contents remain recoverable in Git history. Runtime logs,
caches, environments and build products remain ignored. An all-refs bundle
is retained at `logs/branches-before-main-2026-10-10.bundle`.

## Build and archive

The new test-build version is **6.412**. Runtime branding, PyInstaller spec,
Windows version metadata, build script, README and branding regression are
aligned. The normal `build-exe.bat` entry configures and rebuilds the native
sender, executes its CTest suites, prepares dependency license records, and
builds the PyInstaller onedir application.

The deliverable is the complete `release/GluSpot-6.412` directory. Its
`GluSpot.exe` requires the adjacent `_internal` folder. This is an unsigned
internal Windows test build, with the public-release limits already listed
in README.

The complete project copy is prepared at
`E:/Glusopt_waitingforlaunch/gpt-island_26_10_10_01`. It includes `.git`,
`.venv`, build tools and products, runtime folders, existing logs and the
new release. No source-folder subtree is intentionally excluded. External
C-drive Git worktree checkouts are separate; their commits are preserved
in the main history and all-refs bundle, but the folder copy does not copy
their external working directories. Branches and worktrees are retained
until the user confirms cleanup.

## Verification

The first newly built package exposed a stale acceptance expectation: the
group probe still matched the former HomePod-required parser message. The
current native contract permits two Windows outputs without AirPlay and
rejects incomplete arguments with its corresponding message. The Python
check now matches that exact contract and rejects the former parser message;
it still requires one error event, exit code 2, and the separate multiple-
output collision probe. The package is rebuilt with this corrected check.

Final verification passed:

- All **3408 Python tests**, 97.056 seconds, with the final acceptance fix.
- All **11 native CTest suites**, 45.67 seconds, during the final build.
- The newly rebuilt executable exits its explicit frozen acceptance with
  code 0 and `ok=true`; version is 6.412 and native probes use the bundled
  `_internal/native/GluSpotAirPlay.exe`. It enumerates devices, browses mDNS,
  captures 250 ms of loopback without saving, and checks invalid parser
  arguments without connecting to a receiver or changing Windows state.
- All 70 collected `local/api` modules match compiled current source, as
  do the CArchive main entry and Qt DLL hook. All 61 application-data,
  artwork/font/icon and license/source-archive files match by SHA256.
- Windows FileVersion and ProductVersion are both `6.412.0.0`.
- EXE SHA256:
  `174bf4abb67982488f954d0d30f8a7d5cc2590bb80202c6b688fc6c85c6cbf5d`.
- Rebuilt source and packaged native helper SHA256:
  `1ed5c7fde780298aa009bc4b46b6558cc734f40664316ff63fe61bd2008debfb`.

Logs are retained under the ignored `logs/release-6.412-*` paths. The complete
folder's file/directory inventory and paired SHA256 verification are written
outside both source and destination at
`E:/Glusopt_waitingforlaunch/_verification_26_10_10_01/complete-copy-sha256.json`.
An additional frozen acceptance is run from the archived executable, with
its report in that same verification directory.

Historical 6.411 package reports are not used to certify this package.
No casts, test tones, recordings saved to disk or output-volume writes ran.
Realtek remains at 0. Full GUI desktop, real receiver playback and long-session
audio synchronization are outside this packaging check and retain the
existing limitations documented in README.
