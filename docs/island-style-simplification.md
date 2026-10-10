# Island style simplification

The October 9 island request is implemented on `codex/island-style-simplification`, based on `44dd462`. This is a scoped source change; the test-build version remains 6.411 and the packaged executable is unchanged.

## Behavior

The background chooser contains Sea Level, Solid Background and System. Full-island duotone is removed. Saved duotone choices migrate to Sea Level; saved black/white choices migrate to System. The settings store normalizes both loaded files and later updates, preserving unrelated preferences and canonical per-background values.

System reads Windows `AppsUseLightTheme` independently of GluSpot's app theme overrides or schedule. The existing light/dark painters supply matching text and SVG controls. Qt theme events refresh immediately and the existing update loop polls at most once every two seconds. An explicit System selection clears the old automatic-follow restoration marker. The optional app/taskbar-follow path retains its previous-background restoration; while it selects System, Windows remains the source of its actual color.

Solid Background uses the dominant source RGB retained during the existing artwork scan. This preserves bright, dark and low-saturation source colors without changing the large music surface's established palette roles. It fills the whole standard or dual-panel island without darkening, sheen, reactive tint or transition wash. Missing artwork uses the configured fallback. Neutral ink is chosen by contrast; the shared lyric renderer still supplies its usual reading opacity and lift.

Ember is removed from the chooser and live rendering dispatch. Saved Ember selections migrate to Wave. Gelv uses Wave's compact/expanded presentation, wrapped-line springs, timed-unit reading, low-perception whole-line reading, bilingual display, cover option and shared settings. Only the typeface differs: Noto Serif SC ExtraBold instead of Alibaba PuHuiTi. The serif asset has a fixed weight, so the Wave Bold/Heavy selector is disabled with an explanatory tooltip in Gelv. Retired effect knobs and helper definitions remain inactive compatibility details.

Album island offers only perspective; old flat-row selections migrate to it. A projective transform maps the complete artwork, rounded corners and border onto the same restrained side-cover plane. Smooth sampling preserves corners rather than cropping them away. Existing count, queue/history order, spacing, transition and central-cover interaction remain in place. The existing scaled-image cache holds at most 24 items and a 2 MiB pixel budget, enough for seven covers at compact/expanded sizes. Cache hits skip accounting; unusually large aspect-fill results remain transient.

## Verification

The final isolated full suite passed all 3392 tests (`python -m unittest discover tests -v`); the scoped main-directory integration passed all 3363 tests. Compilation and scoped whitespace checks also passed. The 29-test difference is the pre-existing isolated diagnostic/clock investigation coverage, rather than skipped main tests. Native Qt fixtures render both system themes, white/black/cream/dark-gray/magenta/blue covers and the old/new album perspective at fractional display scaling. These use fake provider data and do not play sound, cast or record.

Focused coverage includes persisted migration round trips, actual settings controls and search entries, independent Windows theme updates, flat solid pixels in both island layouts, typography-held-constant Wave/Gelv output, mixed timed/untimed songs, seek/reversal spring parity and full-image perspective at DPR 1/1.5/2.

A native Qt seven-cover fixture with a 1200-pixel current image and 96-pixel configured expanded covers measured projective draw median about 4.12 ms before cache repair and 3.27 ms afterward. The original clip-only renderer measured about 2.57 ms; complete projection has a quality cost. This controlled fixture is not a desktop FPS or long-session performance claim.

The static solid-color fixture's opaque ink contrast ranges from 4.825:1 to 21:1. That does not describe every faded lyric layer or an accessibility certification. Preview files and test logs remain ignored under `logs/`.

Realtek is kept at zero. Audio routing, playback, native helper and version metadata are outside this change. A running source instance needs a normal exit and restart to load the edited modules. Desktop animation quality over a long music session still requires viewing the restarted app.
