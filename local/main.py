from __future__ import annotations

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

def main(argv: list[str] | None = None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--legacy-package-check" in arguments:
        if len(arguments) != 2 or arguments[0] != "--legacy-package-check":
            if sys.stderr is not None:
                sys.stderr.write("Usage: GluSpot --legacy-package-check REPORTFILE\n")
            return 2
        from local.legacy_package_check import run_legacy_package_check
        return run_legacy_package_check(arguments[1])
    if "--audio-package-check" in arguments:
        if len(arguments) != 2 or arguments[0] != "--audio-package-check":
            if sys.stderr is not None:
                sys.stderr.write("Usage: GluSpot --audio-package-check REPORTFILE\n")
            return 2
        # This must precede local.app: app import loads GUI/settings code and
        # app.main acquires the normal single-instance guard.
        from local.audio_package_check import run_audio_package_check
        return run_audio_package_check(arguments[1])
    if "--installer-handoff" in arguments:
        from local.installer_handoff import parse_handoff_request
        try:
            handoff, token = parse_handoff_request(arguments)
        except ValueError:
            return 2
        from local.app import main as run_app
        if token is not None:
            return run_app(installer_handoff=handoff, installer_sync=token)
        return run_app(installer_handoff=handoff)
    from local.app import main as run_app
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
