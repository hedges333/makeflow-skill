#!/usr/bin/env python3
"""Thin CLI entrypoint for upstream synthetic scars synchronization (GH-209)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.sync_synthetic_scars import SyncSyntheticScarsScript  # noqa: E402

if __name__ == "__main__":
  sys.exit(SyncSyntheticScarsScript().execute())
