# Lyric Flow TODO

Status: waiting list for `格律宋散`, `逐字染色`, and no-lyrics album island.

## Spec

- [ ] Confirm final display label for `逐字染色`.
- [ ] Confirm whether the fallback color picker lives under media playback or appearance.
- [ ] Confirm final long-lyric split threshold after first screenshot pass.

## Fonts

- [ ] Download Source Han Serif SC from Adobe's official release repository.
- [ ] Install Source Han Serif SC locally for development.
- [ ] Add a package/bundle step for Source Han Serif SC.
- [ ] Remove or hide the global font selector.
- [ ] Keep `PingFang SC` as the general default UI font.

## Settings

- [ ] Add text-flow style selector: `波光逐流` / `格律宋散`.
- [ ] Add `格律宋散`-only `逐字染色` toggle.
- [ ] Add `逐字染色` fallback color selector with default `#00D7FF`.
- [ ] Add no-lyrics behavior selector: `显示特定文字` / `专辑岛模式`.
- [ ] Add album island cover-count selector: `3` / `5` / `7`, default `5`.
- [ ] Reclassify lyric settings and hide incompatible controls.

## Rendering

- [ ] Add lyric chunk utility tests for CJK, English, punctuation, and mixed text.
- [ ] Add long-lyric split utility tests.
- [ ] Add `格律宋散` layout metrics and width reservation.
- [ ] Add bottom-to-top current/next lyric transition.
- [ ] Add line-duration-based spacing expansion.
- [ ] Add `逐字染色` pure-black background and fill renderer.
- [ ] Add no-lyrics album island layout and queue/history fallback.

## Verification

- [ ] Run focused unit tests for settings, layout, and rendering helpers.
- [ ] Render screenshots for normal island, taskbar fusion, long lyric split, `逐字染色`, no-lyrics text mode, and no-lyrics album island mode.
- [ ] Run full test discovery.
- [ ] Run `git diff --check`.
