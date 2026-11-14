#!/usr/bin/env python3
"""Multi-dimensional Synthetic Scars metadata enrichment CLI entrypoint (GH-145)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.scars_db import (  # noqa: E402
    EnrichScarsIndexScript,
    enrich_scars_database,
    extract_scar_metadata,
)

__all__ = [
    "EnrichScarsIndexScript",
    "enrich_scars_database",
    "extract_scar_metadata",
]

if __name__ == "__main__":
  sys.exit(EnrichScarsIndexScript().execute())
