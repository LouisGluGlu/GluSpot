# Project Notes

Read `README.md` first. It is the canonical product, implementation, release-readiness, and pending-work document for this branch.

The public product name is GluSpot. Keep old settings paths only as migration aliases; new user-visible names and runtime namespaces should use GluSpot.

The runnable app is `local/main.py`.

Versioned Windows test builds use `build-exe.bat` and `GluSpot.spec`. Keep `local/branding.py`, `GluSpot.spec`, `packaging/windows/version_info.txt`, and `README.md` aligned when the version changes.

Keep fake and real data behind `api/providers.py`.

Do not reintroduce the old Pill UI or the removed live-media source tree unless it is rebuilt as a V2 provider.

Keep generated runtime files, logs, caches, and temporary reference projects out of source control.

## Open Source Maturity Discipline

GluSpot is moving toward an open source project. Treat every change as something a future maintainer must be able to audit from evidence, not from guesswork.

Start each task from a 100 point process score. Deduct points for the failures below before claiming the work is ready:

| Failure | Deduction | Required correction |
| --- | ---: | --- |
| Guessing an interface instead of checking it | -20 | Read the existing code, docs, tests, and call sites before using or changing an interface. |
| Executing an ambiguous request without confirmation | -15 | Ask for human confirmation when scope, desired behavior, or release impact is unclear. |
| Inventing product behavior without authority | -15 | Use `README.md`, current implementation, tests, or explicit human confirmation as the source of truth. |
| Creating a new interface where an existing one fits | -15 | Reuse existing providers, models, settings, UI helpers, and boundaries before adding new ones. |
| Skipping verification | -20 | Run focused tests, the relevant full test command, or document exactly why verification could not run. |
| Breaking project architecture | -20 | Preserve the provider boundary, V2 island surface, GluSpot naming, packaging alignment, and runtime namespace rules. |
| Pretending to understand uncertain code or behavior | -10 | State the unknown clearly, then investigate or ask. |
| Making broad or blind changes | -10 | Prefer small, reviewable edits; justify refactors with evidence and keep them scoped to the task. |

Readiness thresholds:

- 90-100: Ready to hand off, assuming the requested verification passed.
- 80-89: Not ready until missing evidence, tests, or risk notes are added.
- Below 80: Not ready; revise the work before handoff.

Hard stops override the numeric score. If a change guesses an interface, skips verification, or breaks architecture, stop and correct it before presenting the work as ready.

Working standard:

- Be proud of careful lookup, not interface guessing.
- Be proud of confirmation, not vague execution.
- Be proud of human-validated product behavior, not imagined business rules.
- Be proud of reuse, not unnecessary interface creation.
- Be proud of active testing, not skipped verification.
- Be proud of project conventions, not architectural shortcuts.
- Be proud of honest uncertainty, not performed understanding.
- Be proud of cautious refactoring, not blind modification.
