#!/usr/bin/env python3
"""Synthetic Scar Registry Sync and FTS5 Query CLI entrypoint (GH-93)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.scars_db import (  # noqa: E402
    DEFAULT_REGISTRY_URL,
    ScriptArgumentError,
    ScriptArgumentParser,
    SyncSyntheticScarsScript,
    _format_scar_row,
    _sanitize_fts_query,
    _strip_protected_and_inline_code,
    extract_protected_spans,
    query_scars,
    resolve_scars_db_path,
    verify_scar_transformation,
)

__all__ = [
    "DEFAULT_REGISTRY_URL",
    "ScriptArgumentError",
    "ScriptArgumentParser",
    "SyncSyntheticScarsScript",
    "_format_scar_row",
    "_sanitize_fts_query",
    "_strip_protected_and_inline_code",
    "extract_protected_spans",
    "query_scars",
    "resolve_scars_db_path",
    "verify_scar_transformation",
]

if __name__ == "__main__":
  sys.exit(SyncSyntheticScarsScript().execute())
