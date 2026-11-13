#!/usr/bin/env python3
"""Cryptographic Red-Green TDD Guard CLI entrypoint (SCAR-TEST-12)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.tdd_guard import (  # noqa: E402
    AUTOLOAD_HOOK_FILENAMES,
    CORE_GOVERNANCE_FILES,
    DEFAULT_RECEIPT_REL,
    DEFAULT_TIMEOUT_SECONDS,
    IGNORED_CACHE_DIRS,
    IGNORED_SUFFIXES,
    TddGuardScript,
    _compute_receipt_seal,
    _diff_manifests,
    _hash_file_sha256,
    _hash_symlink_target,
    _resolve_test_dir,
    compute_test_tree_hash,
    compute_toolchain_hash,
)

__all__ = [
    "AUTOLOAD_HOOK_FILENAMES",
    "CORE_GOVERNANCE_FILES",
    "DEFAULT_RECEIPT_REL",
    "DEFAULT_TIMEOUT_SECONDS",
    "IGNORED_CACHE_DIRS",
    "IGNORED_SUFFIXES",
    "TddGuardScript",
    "_compute_receipt_seal",
    "_diff_manifests",
    "_hash_file_sha256",
    "_hash_symlink_target",
    "_resolve_test_dir",
    "compute_test_tree_hash",
    "compute_toolchain_hash",
]

if __name__ == "__main__":
  sys.exit(TddGuardScript().execute())
