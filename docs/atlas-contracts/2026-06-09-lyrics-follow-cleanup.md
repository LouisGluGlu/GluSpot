# Atlas Contract: Lightweight Lyrics Follow Cleanup

Date: 2026-06-09

## Goal

- Keep the copied lightweight NetEase/QQ lyric-follow baseline as the only default follow path.
- Remove user-facing settings and diagnostics that belonged to the superseded forced progress-anchor design.
- Remove production interfaces that can accidentally restart the heavy path: progress polling settings, click debug snapshots, global mouse/pointer sampling, immediate screenshot anchors, and lyric-click hit-test helpers.

## Acceptance Criteria

- `v2_music_app_profiles` defaults contain only app path, sync enablement, lyric offset, and service-specific BetterNCM fields.
- The settings UI does not render or save `progress_poll_interval_seconds` and does not expose `lyric_click_debug`.
- `WindowsMediaProvider` has no progress-poll scheduler, no settings progress-anchor request API, no lyric-click retry state, no global pointer sampler/hook, and no lyric-click helper methods.
- `NeteasePositionReader.read(...)` stays asynchronous and lightweight: UIA progress text first, visible lyric rows as fallback. It has no `read_immediate`, no `read_progress_immediate`, no `force_progress_image`, and no lyric-click hit-test methods.
- Changing dual-line, secondary source, or Chinese conversion settings may still request an immediate media refresh, but it must not call a progress-anchor API.

## Out Of Scope

- Do not remove app-profile sync itself; configured player paths are still the supported way to enable NetEase/QQ/Qishui/Kugou attachments.
- Do not remove local seek projection used by GluSpot's own media controls.
- Do not change the copied lightweight follow timing model in this cleanup pass.
