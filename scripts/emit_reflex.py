#!/usr/bin/env python3
"""Standardized SCAR Reflex Emitter CLI entrypoint."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.base_script import ReflexArgumentError, SilentArgumentParser  # noqa: E402
from lib.reflex import (  # noqa: E402
    REFLEX_EXIT_CODE,
    EmitReflexScript,
    _HEAVY_BORDER,
    _LIGHT_BORDER,
    emit_reflex,
    normalize_scar_id,
)

__all__ = [
    "REFLEX_EXIT_CODE",
    "ReflexArgumentError",
    "SilentArgumentParser",
    "EmitReflexScript",
    "_HEAVY_BORDER",
    "_LIGHT_BORDER",
    "emit_reflex",
    "normalize_scar_id",
]

if __name__ == "__main__":
  sys.exit(EmitReflexScript().execute())
