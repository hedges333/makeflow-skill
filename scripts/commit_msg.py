#!/usr/bin/env python3
"""Commit Message Formatter and Validator CLI entrypoint."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.commit_msg import (  # noqa: E402
    CANONICAL_TYPES,
    CommitInvocation,
    CommitMsgScript,
    FileChange,
    HeadCommitCheckResult,
    HeaderInfo,
    ValidationResult,
    detect_commit_context,
    extract_commit_invocation,
    format_commit_message,
    normalize_commit_message,
    parse_commit_header,
    read_head_commit_message,
    validate_commit_message,
    validate_head_commit,
)

__all__ = [
    "CANONICAL_TYPES",
    "CommitInvocation",
    "CommitMsgScript",
    "FileChange",
    "HeadCommitCheckResult",
    "HeaderInfo",
    "ValidationResult",
    "detect_commit_context",
    "extract_commit_invocation",
    "format_commit_message",
    "normalize_commit_message",
    "parse_commit_header",
    "read_head_commit_message",
    "validate_commit_message",
    "validate_head_commit",
]

if __name__ == "__main__":
  sys.exit(CommitMsgScript().execute())
