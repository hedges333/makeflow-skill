#!/usr/bin/env python3
"""SessionStart hook CLI entrypoint for Antigravity (SCAR-ARCH-05)."""

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.config import (  # noqa: E402
    find_state_dir,
    get_workflow_config,
    is_plugin_dir,
    is_ticket_active,
    load_default_config,
)
from lib.session_start import (  # noqa: E402
    SessionStartScript,
    main,
    set_dynamic_terminal_title,
)
from lib.ticket_state import TICKET_JSON_FILENAME  # noqa: E402
from lib.workflow_gates import refresh_state_makefile_if_ignored  # noqa: E402

# Built-in technical English specifications live in skills/makeflow/references/resources/language/english/

__all__ = [
    "SessionStartScript",
    "TICKET_JSON_FILENAME",
    "find_state_dir",
    "get_workflow_config",
    "is_plugin_dir",
    "is_ticket_active",
    "json",
    "load_default_config",
    "main",
    "refresh_state_makefile_if_ignored",
    "set_dynamic_terminal_title",
]

if __name__ == "__main__":
  sys.exit(SessionStartScript().execute())
