# Atlas Contract: Lightweight Lyric Follow Recognition

Date: 2026-06-09

Status: supersedes the previous progress-image-anchor contract for the default NetEase/QQ follow path.

## Goal

- Match the preferred reference copy's lyric-follow behavior: lightweight player-position reads are the default; direct click recognition is not part of the normal refresh path.
- Keep progress dragging responsive by avoiding global pointer sampling, lyric hit testing, screenshot progress anchors, and immediate retry bursts during ordinary provider refresh.
- Only poll supported player readers when timed lyrics exist, because the lyric display has nothing useful to align without timed lyrics.

## Acceptance Criteria

- Provider default refresh must call `player_position_reader.read(...)` only when `timed_lyrics` is non-empty.
- Recent lyric clicks must not trigger UIA lyric-row hit tests, player-content hit tests, `read_immediate`, `read_progress_immediate`, media-session seek, or post-click retry scheduling in the default path.
- NetEase reader refresh must prefer visible UIA progress text; when unavailable, it may estimate from visible lyric rows. It must not enter screenshot progress-image fallback from normal `read(...)`.
- Fresh playing `*-uia-lyrics` samples are allowed to update the displayed position, matching the reference copy's fallback behavior.
- Global pointer sampler/hook startup must not be tied to creating the NetEase/QQ position reader.
- Foreground-title correction must not clear already loaded lyrics from cached visual data; only confirmed refresh data may change the lyric identity for a loaded track.

## Out Of Scope

- No direct player lyric-click recognition in this contract.
- No screenshot progress-image anchor in the default follow path.
- No BetterNCM-specific implementation.
- No word-level lyric timestamp model.
