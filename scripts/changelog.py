#!/usr/bin/env python3
"""Target 90 Changelog Formatter and Validator CLI entrypoint (GH-228)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.changelog import (  # noqa: E402
    CANONICAL_SECTIONS,
    ChangelogBullet,
    ChangelogScript,
    GitHubTicketStrategy,
    LocalTicketStrategy,
    TicketTypeRegistry,
    TicketTypeStrategy,
    add_unreleased_entry,
    format_changelog_bullet,
    normalize_section_name,
    parse_changelog_bullet,
    validate_changelog_bullet,
    validate_changelog_unreleased,
)

__all__ = [
    "CANONICAL_SECTIONS",
    "ChangelogBullet",
    "ChangelogScript",
    "GitHubTicketStrategy",
    "LocalTicketStrategy",
    "TicketTypeRegistry",
    "TicketTypeStrategy",
    "add_unreleased_entry",
    "format_changelog_bullet",
    "normalize_section_name",
    "parse_changelog_bullet",
    "validate_changelog_bullet",
    "validate_changelog_unreleased",
]

if __name__ == "__main__":
  sys.exit(ChangelogScript().execute())
