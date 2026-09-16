# Atlas Contract: Top/Stick Lyrics And Progress Follow

Date: 2026-06-09

## Goal

- Keep Gelv Songscatter behavior consistent between top and stick edge-notch modes.
- Make top standard and spacious modes show the lower Gelv row; spacious only adds more room.
- Apply the former top height rules to both top and stick music notch heights.
- Make top and stick reuse the same Gelv line-switch motion path as free and tasker.
- Keep post-track-change progress alignment on the lightweight provider path, with a delayed first read so very early song positions are not trusted too soon.

## Acceptance Criteria

- Top/stick compact keeps Gelv single-row.
- Top/stick standard and spacious show the current Gelv row plus the next lower row when no explicit dual line is active.
- Top/stick line switching exposes previous/current/next rows during transition.
- Top and stick return matching music notch heights for compact, standard, and spacious.
- Track changes keep the delayed progress retry due until about five seconds after the change.

## Out Of Scope

- No release packaging in this change.
- No provider rewrite for players that do not yet expose a progress reader.

## Superseded

- The per-player progress polling setting and forced progress-image reader described in the earlier draft are superseded by `2026-06-09-lyrics-follow-cleanup.md`.
