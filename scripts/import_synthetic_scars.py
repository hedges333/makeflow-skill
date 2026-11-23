#!/usr/bin/env python3
"""Thin CLI entrypoint for Simple English synthetic scar transformation (GH-209)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.import_synthetic_scars import ImportSyntheticScarsScript  # noqa: E402

if __name__ == "__main__":
  sys.exit(ImportSyntheticScarsScript().execute())
