#!/usr/bin/env python3
"""Local Ticket Management CLI entrypoint (GH-124)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.local_tickets import (  # noqa: E402
    LocalTicketScript,
    LocalTicketStore,
)

__all__ = [
    "LocalTicketScript",
    "LocalTicketStore",
]

if __name__ == "__main__":
  sys.exit(LocalTicketScript().execute())
