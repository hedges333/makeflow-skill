#!/usr/bin/env python3
"""Environment Path Pre-Flight Validator CLI entrypoint (SCAR-SYS-06)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.env_preflight import (  # noqa: E402
    STANDARD_ENV_VARS,
    EnvPreflightScript,
    main,
    preflight_env_vars,
    sanitize_runtime_env,
    validate_runtime_path,
)

__all__ = [
    "EnvPreflightScript",
    "STANDARD_ENV_VARS",
    "validate_runtime_path",
    "preflight_env_vars",
    "sanitize_runtime_env",
    "main",
]

if __name__ == "__main__":
  sys.exit(EnvPreflightScript().execute())
