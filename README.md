# gpt-island

Windows floating music island built with Python and PySide6.

The current branch focuses on one product direction: a real media island that uses album colors, live playback state, cached/online lyrics, and audio-reactive ambience. The target lyric experience is not a visible progress bar. It should feel like Apple Music style live lyrics inside a Dynamic Island: the active line follows the song position, breathes with the vocal moment, and drives subtle background motion.

The app entry point uses the real Windows media provider. If Windows has no active media session, the island shows an empty media state instead of sample songs. Fake data is only for standalone visual preview code.

## Run

Double-click `launch.bat`.

If dependencies are missing, run `setup-env.bat` once, then launch again. The setup script creates a project-local `.venv`, installs the required packages there, and verifies `PySide6`, `winsdk`, `soundcard`, `numpy`, and `pywinauto`. Startup errors are written to `logs/gpt-island.log`.

## Key Files

- `local/main.py`: app entry point.
- `local/app.py`: tray app, settings wiring, real provider wiring.
- `local/island_stack_visual.py`: island layout, music ambience, lyrics, animation.
- `local/settings_window.py`: settings UI.
- `local/settings_store.py`: persisted settings.
- `api/models.py`: shared data contracts.
- `api/providers.py`: Windows media provider implementation.
- `api/audio_analyzer.py`: real loopback audio capture diagnostics and audio bands.
- `api/lyrics.py`: local and online lyric resolution.
- `api/lyric_timeline.py`: lyric active-line, line phase, and sync-state calculation.
- `api/netease_position.py`: NetEase Cloud Music lyric-page anchor used when Windows exposes title but not playback position.
- `lyrics/`: local `.lrc` cache used by the lyric island.
- `tests/`: focused behavior tests for timeline and sync logic.

## Product Docs

- `docs/PRD.md`
- `docs/ISSUES.md`
