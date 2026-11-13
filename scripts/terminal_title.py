#!/usr/bin/env python3
"""Dynamic Terminal Title Utility CLI entrypoint (GH-73)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.terminal_title import (  # noqa: E402
    StreamProxy,
    TerminalTitleScript,
    detect_issue_tag,
    detect_project_name,
    detect_terminal_capability,
    detect_ticket_id,
    format_terminal_title,
    generate_escape_sequence,
    is_temporary_directory_name,
    sanitize_terminal_title,
    set_terminal_title,
)

__all__ = [
    "StreamProxy",
    "TerminalTitleScript",
    "detect_issue_tag",
    "detect_project_name",
    "detect_terminal_capability",
    "detect_ticket_id",
    "format_terminal_title",
    "generate_escape_sequence",
    "is_temporary_directory_name",
    "sanitize_terminal_title",
    "set_terminal_title",
]

if __name__ == "__main__":
  sys.exit(TerminalTitleScript().execute())
