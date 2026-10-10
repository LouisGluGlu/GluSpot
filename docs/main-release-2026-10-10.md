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

The complete project copy is planned at
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

Completion results, executable identity and archive integrity are recorded
after the build and final checks. Existing source-instance or historical
package verification is not used as evidence for the newly built executable.
No casting, test tones or output-volume changes are needed; the user's
Realtek-zero constraint remains in force.
