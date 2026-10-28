# Capture timeline replay fixtures

These JSON files are deterministic **mock traces** of known SMTC behaviors.
They are not recordings captured from Windows or from live players. No such
recordings are available in this Linux development environment. Player names
describe the behavior being simulated, not a verified player version.

`test_capture_position.PositionModelTests.test_recorded_timeline_replay` loads
every JSON file in this directory. Each event has an explicit monotonic `now`
and an independently specified expected position in seconds. Events containing
`sample` supply a complete `TimelineSample`; events without a sample only read
the existing prediction. `wall_time` and `updated_at` are synthetic Unix seconds,
and `observed_at` is monotonic seconds. Every trace starts with a fresh model.

- `netease_stale_anchor.json`: an infrequently updated anchor, repeated polls,
  pause/resume with the old anchor, forward/backward seeks, and paused seeking.
- `generic_regular_updates.json`: regular updates with small measurement drift,
  playback-rate changes, pause/resume, and clamping at the song end.
- `clock_jump_and_track_replacement.json`: wall-clock jumps in both directions,
  a delayed out-of-order callback, track A → B → A, and a session replacement.

The fixtures exercise projection rules, not native WinRT delivery, UIA access,
or live-player accuracy. Real Windows recordings and the PRD's hardware matrix
remain acceptance work; add provenance and capture details when those traces
become available.
