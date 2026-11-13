#!/usr/bin/env python3
"""Workflow Gate Validator CLI entrypoint (GH-64, GH-97)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.workflow_gates import (  # noqa: E402
    DEFAULT_SKILL_MAX_BYTES,
    REQUIRED_PLUGIN_FILES,
    WorkflowGatesScript,
    generate_state_makefile,
)

__all__ = [
    "DEFAULT_SKILL_MAX_BYTES",
    "REQUIRED_PLUGIN_FILES",
    "WorkflowGatesScript",
    "generate_state_makefile",
]

if __name__ == "__main__":
  sys.exit(WorkflowGatesScript().execute())
