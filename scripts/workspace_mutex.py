#!/usr/bin/env python3
"""Atomic Directory-Based Mutual Exclusion Lock CLI entrypoint (SCAR-SYS-08)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.workspace_mutex import (  # noqa: E402
    WorkspaceMutex,
    WorkspaceMutexScript,
)

__all__ = [
    "WorkspaceMutex",
    "WorkspaceMutexScript",
]

if __name__ == "__main__":
  sys.exit(WorkspaceMutexScript().execute())
