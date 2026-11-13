#!/usr/bin/env python3
"""AST-Based Exception-Handling Anti-Pattern Scrubber CLI entrypoint (SCAR-ERR-05)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.ast_scrub import (  # noqa: E402
    DEFAULT_SCAN_DIRS,
    SCAR_ID,
    AstScrubScript,
    AstScrubberVisitor,
    Violation,
    collect_python_files,
    get_git_modified_files,
    is_test_file,
    scrub_file,
    scrub_source,
)

__all__ = [
    "DEFAULT_SCAN_DIRS",
    "SCAR_ID",
    "AstScrubScript",
    "AstScrubberVisitor",
    "Violation",
    "collect_python_files",
    "get_git_modified_files",
    "is_test_file",
    "scrub_file",
    "scrub_source",
]

if __name__ == "__main__":
  sys.exit(AstScrubScript().execute())
