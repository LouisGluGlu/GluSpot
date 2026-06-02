# Lyric Flow And No-Lyrics Album Island PRD

Status: branch direction for the new lyric-flow work. This document covers the `格律宋散` lyric style, its dedicated `逐字染色` effect, and the no-lyrics album island.

## Goal

Add a second text-flow style named `格律宋散` beside the current default `波光逐流`. `格律宋散` should feel like a Song-serif lyric object: two visible lyric rows, strong hierarchy, smooth bottom-to-top line replacement, expanding word/character spacing, and a dedicated black-background word-coloring effect.

Also add a no-lyrics album island for instrumental tracks, missing lyrics, and user-selected no-lyrics display. This mode centers the current album cover and unfolds neighboring covers or listening-history context horizontally.

## Naming

- Current default text-flow style: `波光逐流`.
- New text-flow style: `格律宋散`.
- New `格律宋散`-only effect: `逐字染色`.
- `格律宋散` applies to both normal music island and taskbar fusion. Taskbar fusion may reduce sizes and spacing intensity.

## Typography

- The app's general default UI font should become `PingFang SC`.
- The old user-facing global font selector is no longer meaningful and should be removed or hidden during settings cleanup.
- `格律宋散` must use `Source Han Serif SC` / `思源宋体` with no fallback for this style.
- The font asset should be installed for local development and later bundled into the installer/package.
- Official source for the font asset should be Adobe's Source Han Serif release repository: [adobe-fonts/source-han-serif](https://github.com/adobe-fonts/source-han-serif).

## Core Lyric Layout

- `格律宋散` always displays two lyric rows in normal lyric mode.
- Upper row is the current lyric.
- Lower row is the next lyric.
- Upper row uses the existing `波光逐流` default lyric font size as its maximum/base size.
- Lower row is exactly 10 px smaller than the upper row.
- Lower row removes bold styling.
- The 10 px difference is a hard visual requirement.
- The island must allocate enough vertical space for the two-row layout.
- In taskbar fusion, the same style can be reduced proportionally, but the upper/lower hierarchy must remain visually obvious.

## Line Motion

- Lyrics replace each other bottom-to-top.
- As the next lyric becomes current, it moves upward into the upper row.
- The outgoing upper row fades out with a subtle scale-up and blur. The blur should be restrained and not become a visible smear.
- The transition should feel connected, smooth, and lightly held.
- Timing must follow the real lyric line duration from the timeline. Fixed decorative timing must not desync from the song.
- The line should have a held/settled middle portion when the line duration allows.

## Spacing Motion

- The main animation is expanding spacing between lyric units, not changing the font size.
- Chinese/Japanese/Korean text expands between characters.
- English and alphanumeric text expands between words, not between letters.
- Punctuation attaches to the previous character/word and moves with it.
- Mixed-language text follows the same chunking rule.
- The maximum island width is calculated from the expanded spacing width for the current rendered layout.

## Long Lyric Handling

Long lyrics use the split-line strategy by default.

- Trigger by lyric text length/chunk count, not by scrolling width.
- Do not scroll long lyrics in `格律宋散`.
- Do not split inside an English word.
- For Chinese-like text, split by character/chunk count.
- For English-like text, split by whole-word chunks.
- If the split count is odd, the upper split line gets the extra unit.
- Split lines are centered and visually tight.
- When the current lyric is split into two large lines, the small lower next-lyric row is hidden.
- When the next lyric returns to normal length, the two-row current/next layout returns.

## Relationship To Existing Dual-Line Lyrics

- `格律宋散` is a text-flow style, not the same feature as the existing explicit two-line lyric setting.
- When `格律宋散` is selected, the old `允许双行歌词` option must be unavailable.
- Dual-singer/duet lyric recognition is cancelled for this branch.
- Existing provider data contracts should not be changed for duet detection.
- Settings should be reclassified so obsolete typography options are removed, hidden, or scoped to the style that still uses them.

## `逐字染色` Effect

`逐字染色` is only available when `格律宋散` is selected.

- When enabled, the music island background becomes pure black.
- The normal album-color/background style is temporarily overridden.
- Turning the effect off restores the previous background style.
- Text is filled left-to-right by lyric progress.
- Coloring is per lyric chunk: per character for CJK, per word for English/alphanumeric.
- If only line-level LRC timing is available, distribute fill progress evenly across chunks during the line duration.
- The fill color is the album theme color increased to a high-saturation version.
- If the derived album color is black, white, gray, or otherwise too neutral, use the user-selected fallback color.
- Default fallback color: `#00D7FF`.
- The effect color applies to the text fill only unless a later design pass explicitly expands it.

## No-Lyrics Album Island

The no-lyrics album island appears when:

- The current song has no lyrics.
- The current song is instrumental/pure music.
- The user disables lyric display and selects album island mode.

The user should be able to choose the no-lyrics display behavior:

- `显示特定文字`.
- `专辑岛模式`.

When lyrics are enabled by default, the album island option should not replace available lyrics unless the user explicitly chooses the no-lyrics/album mode.

## Album Island Layout

- The current album cover is centered.
- Side covers unfold horizontally.
- Center cover is brightest, clearest, and visually dominant.
- Side covers gradually weaken by opacity, scale, blur, or darkness.
- User chooses album cover count from odd numbers only: 3, 5, or 7.
- Default cover count is 5.
- Island length adapts to cover count.
- The island should not exceed 80% of the current screen width.
- Taskbar fusion also supports this mode with reduced cover size and tighter spacing.

## Queue And History Logic

- If playback queue/recommendation data is available, use it to place neighboring covers.
- If queue data is unavailable:
  - Put previously played album covers on the left.
  - Put a text block on the right: `已经听歌 X 小时`.
  - `X` uses actual listening data when available and is rounded to no decimal places.
- On track change with unknown queue:
  - The new album emerges from the right-side text area.
  - The old current album shifts left into history.
  - Layering must keep the center cover dominant.

## Acceptance Criteria

- Settings expose text-flow style with `波光逐流` as default and `格律宋散` as the new option.
- Selecting `格律宋散` disables/hides the old dual-line lyric option.
- `格律宋散` renders current lyric above next lyric with exactly 10 px size difference.
- `格律宋散` animates spacing expansion by real lyric-line duration.
- Long lyrics split into two centered large lines and hide the lower next-lyric row.
- `逐字染色` is only visible/enabled for `格律宋散`.
- `逐字染色` forces pure black background and restores the previous background when disabled.
- Neutral album colors use `#00D7FF` unless the user chooses another fallback color.
- No-lyrics behavior offers `显示特定文字` and `专辑岛模式`.
- Album island supports 3/5/7 covers with default 5 and adaptive width capped at 80% screen width.
- Queue-unavailable mode uses left-side history plus right-side listened-hours text.
- Fake and real data remain behind `api/providers.py`.
- Old Pill UI and removed live-media source trees are not restored.
