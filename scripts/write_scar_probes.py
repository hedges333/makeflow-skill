#!/usr/bin/env python3
"""Thin CLI entrypoint for Socratic probe generation on synthetic scars (GH-209)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.write_scar_probes import WriteScarProbesScript  # noqa: E402

if __name__ == "__main__":
  sys.exit(WriteScarProbesScript().execute())
