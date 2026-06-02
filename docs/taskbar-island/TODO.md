# Taskbar Island TODO

Status: waiting list for the taskbar fusion branch. Do not treat this as code-ready execution order yet.

## Product Decisions

- [ ] Confirm Settings wording for `Finder融合` and `任务栏融合`.
- [ ] Decide whether `任务栏融合` is automatic on placement or has its own Settings toggle.
- [ ] Define taskbar placement recognition thresholds.
- [ ] Decide behavior when Windows taskbar is auto-hidden.
- [ ] Decide whether taskbar fusion supports top/left/right taskbars in V1 or only bottom taskbar.

## Design

- [ ] Produce compact music taskbar layout reference based on Soda Music placement logic.
- [ ] Design gpt-island-specific compact plugin badges/chips/rail.
- [ ] Design expanded taskbar panel above the Windows taskbar.
- [ ] Define compact corner-radius tokens for album-cover-like rounding.
- [ ] Define taskbar height/inset sizing tokens.
- [ ] Define empty media and no-lyrics states for taskbar size.

## Implementation Planning

- [ ] Map taskbar fusion state into settings and persisted state.
- [ ] Map taskbar placement recognition into drag/boundary handling.
- [ ] Map compact taskbar rendering into the V2 island visual path.
- [ ] Map plugin taskbar compact rendering without reusing old Pill UI.
- [ ] Map expanded taskbar panel rendering without Finder material assumptions.
- [ ] Add focused tests and visual checks to the implementation plan.

## Guardrails

- [ ] Keep fake and real data behind `api/providers.py`.
- [ ] Keep the current V2 island.
- [ ] Do not restore old Pill UI.
- [ ] Do not restore removed live-media source trees unless rebuilt as a V2 provider.
- [ ] Do not add a visible progress bar as the lyric solution.
