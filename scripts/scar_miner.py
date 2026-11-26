#!/usr/bin/env python3
"""Thin CLI entrypoint for Target 90 Rapid-Regret Miner SQLite workflows (GH-117, GH-304)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.scar_miner import (  # noqa: E402
    TIER_ID_PREFIXES,
    UPSTREAM_REPO,
    MinedScarRecord,
    ScarConfigStore,
    ScarMinerScript,
    SqliteScarStore,
    UpstreamIssuePublisher,
    validate_mined_scar,
    validate_scar_id,
)

__all__ = [
    "TIER_ID_PREFIXES",
    "UPSTREAM_REPO",
    "MinedScarRecord",
    "ScarConfigStore",
    "ScarMinerScript",
    "SqliteScarStore",
    "UpstreamIssuePublisher",
    "validate_mined_scar",
    "validate_scar_id",
]

if __name__ == "__main__":
  sys.exit(ScarMinerScript().execute())
