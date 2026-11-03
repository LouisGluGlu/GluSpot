# Library sidebar A-refined

The selectable `sidebar` library now displays the selected real catalog group (`for_you`, `recommended`, `personal`) or the existing platform search. The library default remains `classic`.

The content area has no extra glass pane or per-row glass card. Wide windows show a sparse cover grid, narrow windows show collection rows. Titles are `LibraryCollection.title`; secondary text is `song.artist` or a short kind label, with duplicate title/kind text omitted. Slogans, generic marketing subtitles and decorative cover generators are removed. Missing covers use the existing platform fallback; only URL-backed covers enter the artwork skeleton/request path.

The rail reuses `login_variant_layout("sidebar", ...)`: a 208 px labeled rail at 960×540, and a 64 px vertical icon rail at 433×550 / 391×508. Platform controls use A2's existing source icons and connection states. A 90 ms clock invalidates only connected status dots; signed-out/error/unsupported states stop it. Top-right platform/logout/close share one glass capsule with 44 px targets. Both the mini bar and its cover emit the existing `nowPlayingRequested` action.

Section filtering also applies to catalog loading and pagination. `moreRequested` remains the signal; `load_more()` uses the selected sidebar group to call the existing recommended/personal fetch method. `set_has_more(..., groups=...)` carries those existing controller flags to the view. Search and classic retain aggregate paging behavior. No independent liked/recent/local navigation or unconnected browse action is exposed.

## Shared artwork background

Library and `MusicSurfaceWindow._art_palette()` use `artwork_surface_palette` / its shared cache, with `ambient_colors` and `ambient_weights` passed to `AlbumFlowBackgroundRenderer`. Library now publishes prepared roles through `backgroundPaletteChanged`; `MusicLibraryController` connects that to login's `set_background_palette()` before applying the current track. Login freezes the same extracted scene at t=0 / intensity=0 and retains its readability veil.

When library is hidden or closed and login is visible, the existing single palette worker can still finish the current artwork. Opening login starts a pending extraction; when both consumers are hidden, extraction stays deferred. Existing generation/key checks reject stale results. No duplicate login extractor, network call, pixel timer or private login palette is added.

Without a track, library and login call `idle_surface_palette()`. Its dark source tones `(44,44,44)` and `(14,14,14)` come from `idle_surface_colors()`, also used by the main surface owner's `_no_audio_surface_colors()`. The main surface's existing light-system-theme tones `(242,242,242)` / `(214,214,214)` remain available through that helper. The first-run mode-choice animation retains its separate onboarding palette. Classic's underlying AlbumFlow painter is unchanged; refined chrome and the shared no-track palette intentionally change complete page pixels from 767eecc.

## Evidence

- `/workspace/gluspot/handoff/library-variants/variant-A-refined.png`: labeled 960×540 / 433×550 / 391×508, each loaded/loading/empty; loaded cells show the three real groups. All cells use LouisHerm and the Trippin / Kara Marni now-playing snapshot.
- `/workspace/gluspot/handoff/backdrop-variants/backdrop-{baseline,1,2,3}.png`: refreshed classic/flow/frosted/noir sheets, A-refined library and connected login A2, two sizes and warm/red extracted palette fixtures.
- `/workspace/gluspot/handoff/library-variants/FEATURE_INVENTORY.md`: current control-to-signal/controller mapping with source lines.
- `tools/render_library_variants.py --refined` and `tools/render_backdrop_variants.py`: actual Qt windows, offscreen, private temporary settings/cache directories, no provider/controller/network construction.

Review covers reuse tracked `_demos/narrow3/covers/{sunset_city,night_sea,pink_forest,clip}.jpg`. They are offline fixtures, not claimed to be official artwork for the displayed tracks/playlists. Backdrop comparison retains photographic detail under controlled warm/red duotones before running the real extractor. No new image asset or redistribution claim is added.

Focused verification uses `/workspace/gluspot/qtvenv/bin/python`, separate processes per module, and private temporary settings. All 257 discovered checks complete successfully, with two existing skips: library variants (16), backdrop variants (11), library background (9), login variants (11), library controller (83), surface palette (43), idle palette (6), library window (45), header (8), search (12), horizontal catalog (9), navigation (4). The two skips require local reference screenshots that are unavailable. Log: `/workspace/gluspot/handoff/library-variants/verification-A-refined.log`. Compilation and `git diff --check` pass.

A preliminary combined process completed assertions with an obsolete platform-tint expectation, then crashed during Qt teardown. That expectation was corrected for the requested shared neutral fallback. Final module-isolated runs exit normally; ordinary whole-suite/native teardown stability is not claimed. Full discovery was not run, as requested. Offscreen evidence does not establish Windows compositor/live-account acceptance.

Process score: 100/100. Product edits were written by the primary agent; delegated work was read-only inspection. No version/build/push, credential access, live account call or player action was performed.
