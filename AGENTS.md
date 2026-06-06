# Project Notes

Read `README.md` first. It is the canonical product, implementation, release-readiness, and pending-work document for this branch.

The public product name is GluSpot. Keep old settings paths only as migration aliases; new user-visible names and runtime namespaces should use GluSpot.

The runnable app is `local/main.py`.

Versioned Windows test builds use `build-exe.bat` and `GluSpot.spec`. Keep `local/branding.py`, `GluSpot.spec`, `packaging/windows/version_info.txt`, and `README.md` aligned when the version changes.

Keep fake and real data behind `api/providers.py`.

Do not reintroduce the old Pill UI or the removed live-media source tree unless it is rebuilt as a V2 provider.

Keep generated runtime files, logs, caches, and temporary reference projects out of source control.
