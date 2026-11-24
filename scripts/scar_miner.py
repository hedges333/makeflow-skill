#!/usr/bin/env python3
"""Thin CLI entrypoint for Target 90 Rapid-Regret Miner workflows (GH-117)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.scar_miner import (  # noqa: E402
    CATEGORY_SPOKE_FILES,
    TIER_ID_PREFIXES,
    UPSTREAM_REPO,
    MarkdownScarStore,
    MinedScarRecord,
    ScarConfigStore,
    ScarMinerScript,
    UpstreamIssuePublisher,
    category_to_filename,
    validate_mined_scar,
)

__all__ = [
    "CATEGORY_SPOKE_FILES",
    "TIER_ID_PREFIXES",
    "UPSTREAM_REPO",
    "MarkdownScarStore",
    "MinedScarRecord",
    "ScarConfigStore",
    "ScarMinerScript",
    "UpstreamIssuePublisher",
    "category_to_filename",
    "validate_mined_scar",
]

if __name__ == "__main__":
  sys.exit(ScarMinerScript().execute())
