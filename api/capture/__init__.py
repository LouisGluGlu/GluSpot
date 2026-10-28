"""Opt-in external-player capture components.

Only ``api.providers`` connects these components to the application. Importing
this package never imports WinRT, UI Automation, Qt, or starts a worker.
"""

FEATURE_SETTING = "v2_capture_position_enabled"


def capture_enabled(settings_getter) -> bool:
    """Require an explicit boolean opt-in; malformed saved values stay off."""
    return callable(settings_getter) and settings_getter(FEATURE_SETTING, False) is True
