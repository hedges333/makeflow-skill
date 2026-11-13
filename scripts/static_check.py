#!/usr/bin/env python3
"""Pre-flight structural static analysis runner CLI entrypoint (SCAR-TEST-10)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.static_check import (  # noqa: E402
    REPO_ROOT,
    collect_python_files,
    find_linter,
    main,
    run_ast_fallback,
    run_external_linter,
)

__all__ = [
    "REPO_ROOT",
    "collect_python_files",
    "find_linter",
    "main",
    "run_ast_fallback",
    "run_external_linter",
]

if __name__ == "__main__":
  main()
