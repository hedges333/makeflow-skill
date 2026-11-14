#!/usr/bin/env python3
"""POSIX Signal Trapping & Graceful Teardown CLI entrypoint (SCAR-SYS-07)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.signal_trap import (  # noqa: E402
    DEFAULT_SIGNALS,
    SignalTrap,
    SignalTrapScript,
    logger,
    main,
)

__all__ = [
    "DEFAULT_SIGNALS",
    "SignalTrap",
    "SignalTrapScript",
    "logger",
    "main",
]

if __name__ == "__main__":
  sys.exit(SignalTrapScript().execute())
