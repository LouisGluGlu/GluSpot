# Music-focused settings and GLU identity

Date: 2026-10-10. Branch: `codex/owned-playback-sources`.
Version stays `6.412`; no new packaged executable is claimed.

## Product scope

The user confirmed removal of time/weather/agenda plugins and all notification
pages, popup styles and background collection. The application keeps music,
lyrics and output-device control. Settings have four pages: Basic, Media,
Fullscreen lyrics and About. MyDockFinder's path, selected process names and
detected status move to Media, using the existing listener and setters.

Theme/startup, shared animation frame rates/speed, program/system sources,
login, player profiles, lyric offsets, three background/style choices, reading
controls and secondary-content preferences remain. Song-page translation stays
functional as a session action. The beta/gold badge is absent; gold reading still
uses the existing gold ripple.

The app passes the current session's translation state through the existing
settings-getter boundary to source lyric parsing and its cache identity.
Persisted translation remains at the fixed startup default. Program playback
reparses the current source payload in a fenced background task when that
preference changes; it keeps the audio source, playback epoch and position.
Six parser preferences are captured on the Qt owner thread before submission.
Repeated changes coalesce into one pending parse; stale preferences, songs,
stops and terminal playback errors cannot publish old results. Only the current
immutable `LyricPayload` is retained, within the existing adapter's 2 Mi-character
per-field and 8 MiB bridge-response bounds. Song changes/stop/close/error clear it.
A rejected or failed first reparse ends the loading marker; failures after
lyrics are established keep the prior lyrics. No new lyric API request, stream
request, seek or audio-source change accompanies the translation action.

Island shell/layout, font and weight, placement/empty-bar controls, cover
geometry, background tuning/reactivity, bilingual settings toggle and secondary
font-size controls are removed. `FIXED_DEFAULT_SETTING_KEYS` in
`local/settings_store.py` is the authoritative compatibility-key list. Loading
old settings resets those values to `DEFAULT_SETTINGS`; later state export/set
calls cannot restore them. Mutable defaults are returned as copies. Account,
custom icon, reading and source settings, saved playback mode, MyDockFinder path
and selected process names are retained. Runtime drag interactions continue, but
an old saved placement does not alter the next launch's default.

## Runtime boundaries

`WindowsMediaProvider` uses an empty compatibility provider instead of
constructing the real plugin/message collectors. Real, owned, cached and
fallback snapshots publish empty message/plugin tuples. Weather HTTP refresh,
schedule loading and Windows notification/UIAutomation message polling do not
start, even if old settings enable them. The V2 island also fences retired
message/plugin ingestion and overlays from the live application path.

`LocalIslandApp` no longer imports/constructs `AudioDeviceNotice`, starts its
350 ms dispatch timer or routes arrival notices into the island. Music surfaces
and the output chooser keep their existing route polling and confirmed playback
membership feedback. Device discovery/control, audio transport, output clocks
and MyDockFinder polling remain active. Retired standalone modules and fixtures
remain inspectable for compatibility; they are not active product features.

## Default icon

The new GLU asset reuses the actual Impact lettering and compact letter spacing
from the installation-animation preview. Static vector outlines allow rendering
without distributing a font file. App, tray, settings preview and future Windows
packaging reference the same new icon family. Existing user-selected icons keep
their established precedence. Asset source/provenance and regeneration details
are in `assets/brand/README.md`.

## Verification

The frozen final full suite passed all 3728 tests in 180.449 seconds with no
skips or native crash (`logs/music-only-final-full-suite-v3.log`). Python
compilation, dependency consistency (`pip check`) and `git diff --check` also
passed. README records this integrated source baseline. Tests cover old-file
migration, ignored later visual exports, mutable
defaults, provider runtime retirement, surviving settings and retained playback
actions. The real Qt account/library smoke exercises app construction, startup,
actual muted decoding, clock/lyrics, list-loop EOF, song-page Back and all three
playback modes with isolated fixtures. Native Qt screenshots cover the four
settings pages, themes and display scaling.

The final settings-focused check passed 104 tests (11.420 seconds, no skips):
`tests.test_mac_preferences_shell`, `tests.test_playback_mode_settings`,
`tests.test_music_cache_settings` and `tests.test_music_recovery`. Retired-control
exposure tests were replaced with live four-page navigation, widget/search
absence, MyDockFinder relocation and persistence, fixed background values,
retained simple styles/preloading, general animation controls and default-font
checks. The same 104 tests passed on native Windows at DPR 2 (13.218 seconds,
no skips). Sync tests cover
refreshing the dialog after the removed controls are gone. The secondary-content
combo stays on Fullscreen lyrics and uses the existing save/refresh setter;
the dialog does not perform synchronous lyric lookup.

`tools/preview_simplified_settings.py` uses isolated offline UI fixtures and the
same global theme getter as the source app. Native `windows` Qt rendering was
checked at logical 688×496 and 900×650, in light/dark themes, at actual DPR 1 and
2. All 32 page-width checks passed; screenshots also cover expanded MyDockFinder,
the media and secondary-content sections, diagnostics and search. Inspection
found readable controls, no empty retained cards and no horizontal overflow.
Exact MyDockFinder/background searches returned their retained control; removed
notification/font-import queries returned no results. Preview images and JSON
evidence stay ignored under `.runtime/settings-simplification-preview/`.

The frozen owned translation/player/library/output focused suite passed 161
tests (9.743 seconds), including a real background worker and owner-thread
responsiveness during repeated changes. An independent review passed another
75 focused tests and checked paused/resumed playback plus first-fetch changes.
The final native `tools/smoke_music_library.py` run passed all 64 library/player
and 14 application-constructor checks (library run: 8.406 seconds). It proves
that current-song translation appears and disappears while actual muted Qt
playback continues, reuses the retained lyric payload, does not reload the
stream, and leaves the persisted default off. It also exercises Back navigation,
list-loop EOF and retained playback modes. The constructor confirms that retired
notice/timer objects are absent, plugins are inactive, idle translation refresh
still reaches the system source, and close hooks run. Reports remain ignored in
`.runtime/music-only-final-session-smoke-v2/` and the corresponding log.

The fractional output-menu repair contains the exact source circle by flooring
the widget origin and rounding up its right extent, keeping the painted shell
aligned without clipping. Focused panel/surface checks passed all 59 cases.

Preliminary full-suite runs exposed obsolete notification/style expectations,
which were updated to assert the retired live boundary while keeping explicit
standalone legacy fixtures. One preliminary run also terminated with a native
Qt access violation in `test_music_cache_settings.py` during `processEvents`;
its cause was not established. The next complete run finished all 3716 tests
without that native crash and identified one remaining obsolete message-prune
expectation in the music-surface tick test. The corrected focused surface and
session suite passed 275 tests. These preliminary results do not substitute for
the final full run recorded above; cyclic garbage collection remains enabled.

This source work does not certify a new frozen package, physical HomePod/group
listening or sustained real-account sessions. Those limits remain documented in
`docs/owned-playback-refinements.md`.

The final source/privacy review covered 69 candidate paths (67 text files and
two image files). No runtime/log/build/release/environment folders, credential
JSON, private keys, token literals or signed stream URLs enter the commit. The
only credential-URL-shaped fixture is the explicit `example.test` user/password
rejection case in `tests/test_owned_playback.py`. New GLU images have no metadata;
the SVG has no external resource references. Git identity and private access are
verified again by the explicit feature-branch synchronization gate. Original
history backups and account data stay outside this source commit.
