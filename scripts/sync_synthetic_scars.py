#!/usr/bin/env python3
"""Synthetic Scar Registry Sync and FTS5 Query CLI entrypoint (GH-93)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.scar_batch import (  # noqa: E402
    SIMPLE_ENGLISH_JSON_SCHEMA,
    SOCRATIC_PROBE_JSON_SCHEMA,
    run_agy_scar_batch,
)
from lib.scar_ontology import (  # noqa: E402
    CATEGORY_SYNONYM_MAP,
    DOMAIN_CATEGORY_ONTOLOGY,
    TARGET_INT_TO_PHASE_LABEL,
    UPSTREAM_CATEGORY_MAP,
    compute_canonical_scar_id,
    compute_makeflow_targets,
    normalize_domain_category,
    parse_upstream_targets,
    resolve_category_filter_tokens,
    select_primary_language,
    shift_left_targets,
)
from lib.scars_db import (  # noqa: E402
    DEFAULT_REGISTRY_URL,
    EnrichScarsIndexScript,
    ScriptArgumentError,
    ScriptArgumentParser,
    SyncSyntheticScarsScript,
    _format_scar_row,
    _sanitize_fts_query,
    _strip_protected_and_inline_code,
    enrich_scars_database,
    extract_protected_spans,
    extract_scar_metadata,
    migrate_scars_ontology,
    query_scars,
    resolve_scars_db_path,
    verify_scar_transformation,
)

__all__ = [
    "CATEGORY_SYNONYM_MAP",
    "DEFAULT_REGISTRY_URL",
    "DOMAIN_CATEGORY_ONTOLOGY",
    "EnrichScarsIndexScript",
    "SIMPLE_ENGLISH_JSON_SCHEMA",
    "SOCRATIC_PROBE_JSON_SCHEMA",
    "ScriptArgumentError",
    "ScriptArgumentParser",
    "SyncSyntheticScarsScript",
    "TARGET_INT_TO_PHASE_LABEL",
    "UPSTREAM_CATEGORY_MAP",
    "_format_scar_row",
    "_sanitize_fts_query",
    "_strip_protected_and_inline_code",
    "compute_canonical_scar_id",
    "compute_makeflow_targets",
    "enrich_scars_database",
    "extract_protected_spans",
    "extract_scar_metadata",
    "migrate_scars_ontology",
    "normalize_domain_category",
    "parse_upstream_targets",
    "query_scars",
    "resolve_category_filter_tokens",
    "resolve_scars_db_path",
    "run_agy_scar_batch",
    "select_primary_language",
    "shift_left_targets",
    "verify_scar_transformation",
]

if __name__ == "__main__":
  sys.exit(SyncSyntheticScarsScript().execute())
