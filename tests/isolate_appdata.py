"""Keep a test process off the real %APPDATA% settings file.

Import this before any product module. settings_store captures its path at
import, and Windows always has LOCALAPPDATA, so a later patch is too late.
"""
from __future__ import annotations

import os
import tempfile

ROOT = tempfile.mkdtemp(prefix="gluspot-test-appdata-")
os.environ["APPDATA"] = ROOT
if os.environ.get("LOCALAPPDATA"):
    os.environ["LOCALAPPDATA"] = ROOT
