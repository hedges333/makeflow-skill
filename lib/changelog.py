"""Target 90 changelog bullet formatter, parser, validator, and updater (GH-228).

Implements an extensible Strategy + Registry architecture for ticket-prefixed
CHANGELOG.md entries under Keep a Changelog's ``## [Unreleased]`` section.
Provides ``ChangelogScript(BaseScript)`` for hermetic CLI execution.
"""

from abc import ABC, abstractmethod
import argparse
from dataclasses import dataclass
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from lib.base_script import BaseScript, SilentArgumentParser

CANONICAL_SECTIONS: Tuple[str, ...] = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)

_CANONICAL_SECTION_MAP: Dict[str, str] = {
    section.lower(): section for section in CANONICAL_SECTIONS
}

_BULLET_CAPTURE_RE = re.compile(r"^\s*([-*])\s+([^\s:]+):\s+(\S.*)$")
_BULLET_LINE_RE = re.compile(r"^\s*([-*])\s+(.*)$")
_PREFIX_CAPTURE_RE = re.compile(r"^([^\s:]+):(.*)$")
_BARE_DIGITS_RE = re.compile(r"^([1-9][0-9]*)$")
_UNRELEASED_HEADING_RE = re.compile(r"^##\s+\[Unreleased\](?:\s|$)")
_H2_HEADING_RE = re.compile(r"^##\s+(?!#)")


@dataclass
class ChangelogBullet:
  """Structured representation of a ticket-prefixed changelog bullet line."""

  ticket_type: str
  ticket_id: str
  summary: str
  bullet_char: str = "-"
  raw: str = ""


class TicketTypeStrategy(ABC):
  """Strategy interface for normalizing and matching a ticket identifier family."""

  @property
  @abstractmethod
  def kind(self) -> str:
    """Canonical uppercase ticket type identifier."""

  @abstractmethod
  def normalize(
      self, raw_ticket: str, *, allow_bare_digits: bool = False
  ) -> Optional[str]:
    """Normalize a raw ticket reference into canonical bullet prefix form.

    Args:
      raw_ticket: Raw ticket identifier supplied by a caller or CLI flag.
      allow_bare_digits: Whether bare positive integers are accepted as this kind.

    Returns:
      Normalized canonical ticket identifier, or None if invalid for this kind.
    """

  def parse_bullet_ticket(self, token: str) -> Optional[str]:
    """Match a canonical ticket token at the start of a changelog bullet.

    Args:
      token: Candidate ticket token extracted before ``:`` in a bullet line.

    Returns:
      Canonical ticket identifier if the token matches canonical format, else None.
    """
    cleaned = (token or "").strip()
    if not cleaned:
      return None
    normalized = self.normalize(cleaned, allow_bare_digits=False)
    if normalized is not None and normalized == cleaned:
      return normalized
    return None


class GitHubTicketStrategy(TicketTypeStrategy):
  """Ticket strategy normalizing GitHub issues to ``GH-<N>``."""

  kind = "GITHUB"
  _EXPLICIT_RE = re.compile(r"^(?:GH-|#)([1-9][0-9]*)$", re.IGNORECASE)

  def normalize(
      self, raw_ticket: str, *, allow_bare_digits: bool = False
  ) -> Optional[str]:
    """Normalize ``GH-<N>``, ``gh-<N>``, ``#<N>``, or explicit ``<N>`` to ``GH-<N>``.

    Args:
      raw_ticket: Raw GitHub ticket identifier.
      allow_bare_digits: When True, accept bare positive integers as GitHub IDs.

    Returns:
      Canonical ``GH-<N>`` string, or None when invalid.
    """
    if not isinstance(raw_ticket, str):
      return None
    cleaned = raw_ticket.strip()
    match = self._EXPLICIT_RE.match(cleaned)
    if match:
      return f"GH-{int(match.group(1))}"
    if allow_bare_digits:
      digit_match = _BARE_DIGITS_RE.match(cleaned)
      if digit_match:
        return f"GH-{int(digit_match.group(1))}"
    return None


class LocalTicketStrategy(TicketTypeStrategy):
  """Ticket strategy normalizing local session tasks to ``LOCAL-<N>``."""

  kind = "LOCAL"
  _EXPLICIT_RE = re.compile(r"^LOCAL-([1-9][0-9]*)$", re.IGNORECASE)

  def normalize(
      self, raw_ticket: str, *, allow_bare_digits: bool = False
  ) -> Optional[str]:
    """Normalize ``LOCAL-<N>``, ``local-<N>``, or explicit ``<N>`` to ``LOCAL-<N>``.

    Args:
      raw_ticket: Raw local ticket identifier.
      allow_bare_digits: When True, accept bare positive integers as local IDs.

    Returns:
      Canonical ``LOCAL-<N>`` string, or None when invalid.
    """
    if not isinstance(raw_ticket, str):
      return None
    cleaned = raw_ticket.strip()
    match = self._EXPLICIT_RE.match(cleaned)
    if match:
      return f"LOCAL-{int(match.group(1))}"
    if allow_bare_digits:
      digit_match = _BARE_DIGITS_RE.match(cleaned)
      if digit_match:
        return f"LOCAL-{int(digit_match.group(1))}"
    return None


class TicketTypeRegistry:
  """Registry mapping ticket type identifiers to ``TicketTypeStrategy`` instances."""

  def __init__(self) -> None:
    """Initialize an empty ticket strategy registry."""
    self._strategies: Dict[str, TicketTypeStrategy] = {}

  @classmethod
  def default_registry(cls) -> "TicketTypeRegistry":
    """Build a registry preloaded with built-in GitHub and Local strategies.

    Returns:
      Fresh ``TicketTypeRegistry`` containing ``GITHUB`` and ``LOCAL`` handlers.
    """
    registry = cls()
    registry.register(GitHubTicketStrategy())
    registry.register(LocalTicketStrategy())
    return registry

  def register(
      self, strategy: TicketTypeStrategy, *, replace: bool = False
  ) -> None:
    """Register a ticket strategy under its canonical ``kind``.

    Args:
      strategy: Concrete ``TicketTypeStrategy`` instance to register.
      replace: Whether to overwrite an existing strategy with the same ``kind``.

    Raises:
      TypeError: If ``strategy`` is not a ``TicketTypeStrategy``.
      ValueError: If ``strategy.kind`` is empty or already registered without ``replace=True``.
    """
    if not isinstance(strategy, TicketTypeStrategy):
      raise TypeError("strategy must inherit from TicketTypeStrategy.")
    kind_key = str(getattr(strategy, "kind", "") or "").strip().upper()
    if not kind_key:
      raise ValueError("Strategy kind must not be empty.")
    if kind_key in self._strategies and not replace:
      raise ValueError(f"Strategy for ticket type '{kind_key}' is already registered.")
    self._strategies[kind_key] = strategy

  def get(self, ticket_type: str) -> Optional[TicketTypeStrategy]:
    """Return the registered strategy for ``ticket_type``, or None if absent.

    Args:
      ticket_type: Case-insensitive ticket type key.

    Returns:
      Registered ``TicketTypeStrategy`` or None.
    """
    if not isinstance(ticket_type, str):
      return None
    return self._strategies.get(ticket_type.strip().upper())

  def kinds(self) -> Tuple[str, ...]:
    """Return registered ticket type names in registration order.

    Returns:
      Tuple of canonical uppercase ticket type names.
    """
    return tuple(self._strategies.keys())

  def __contains__(self, ticket_type: object) -> bool:
    """Return True when ``ticket_type`` is registered."""
    if not isinstance(ticket_type, str):
      return False
    return ticket_type.strip().upper() in self._strategies

  def try_resolve(
      self, raw_ticket: str, ticket_type: Optional[str] = None
  ) -> Optional[Tuple[str, str]]:
    """Resolve ``raw_ticket`` to ``(ticket_type, normalized_id)`` if valid.

    Args:
      raw_ticket: Raw ticket string.
      ticket_type: Optional explicit ticket type key.

    Returns:
      Tuple of ``(kind, normalized_id)`` or None when unrecognized.

    Raises:
      ValueError: If an explicit ``ticket_type`` is not registered.
    """
    if ticket_type is not None:
      key = ticket_type.strip().upper() if isinstance(ticket_type, str) else ""
      strategy = self._strategies.get(key)
      if strategy is None:
        raise ValueError(f"Unknown ticket type: {ticket_type!r}.")
      norm = strategy.normalize(raw_ticket, allow_bare_digits=True)
      return (key, norm) if norm is not None else None

    for kind_key, strategy in self._strategies.items():
      norm = strategy.normalize(raw_ticket, allow_bare_digits=False)
      if norm is not None:
        return (kind_key, norm)
    return None

  def resolve(
      self, raw_ticket: str, ticket_type: Optional[str] = None
  ) -> Tuple[str, str]:
    """Resolve ``raw_ticket`` or raise ``ValueError`` with a clear reason.

    Args:
      raw_ticket: Raw ticket string.
      ticket_type: Optional explicit ticket type key.

    Returns:
      Tuple of ``(kind, normalized_id)``.

    Raises:
      ValueError: If ``ticket_type`` is unknown, digits are ambiguous, or ticket is invalid.
    """
    resolved = self.try_resolve(raw_ticket, ticket_type=ticket_type)
    if resolved is not None:
      return resolved
    if (
        ticket_type is None
        and isinstance(raw_ticket, str)
        and _BARE_DIGITS_RE.match(raw_ticket.strip())
    ):
      raise ValueError(
          f"Bare ticket number {raw_ticket.strip()!r} requires an explicit ticket_type."
      )
    raise ValueError(f"Invalid or unrecognized ticket identifier: {raw_ticket!r}.")

  def normalize(
      self, raw_ticket: str, ticket_type: Optional[str] = None
  ) -> str:
    """Normalize ``raw_ticket`` to its canonical ticket identifier string.

    Args:
      raw_ticket: Raw ticket string.
      ticket_type: Optional explicit ticket type key.

    Returns:
      Canonical ticket identifier (for example ``GH-1234`` or ``LOCAL-1234``).

    Raises:
      ValueError: If resolution fails.
    """
    _, norm_id = self.resolve(raw_ticket, ticket_type=ticket_type)
    return norm_id

  def parse_bullet_token(self, token: str) -> Optional[Tuple[str, str]]:
    """Match a canonical changelog bullet prefix token against registered strategies.

    Args:
      token: Candidate prefix token before ``:``.

    Returns:
      Tuple of ``(kind, canonical_id)`` or None.
    """
    for kind_key, strategy in self._strategies.items():
      norm = strategy.parse_bullet_ticket(token)
      if norm is not None:
        return (kind_key, norm)
    return None


def _resolve_registry(
    registry: Optional[TicketTypeRegistry],
) -> TicketTypeRegistry:
  """Return ``registry`` when supplied, otherwise a fresh default registry."""
  return registry if registry is not None else TicketTypeRegistry.default_registry()


def normalize_section_name(section: str) -> str:
  """Validate and normalize a Keep a Changelog subsection heading.

  Args:
    section: Section title such as ``Added``, ``Changed``, or ``Fixed``.

  Returns:
    Canonical title-cased Keep a Changelog section name.

  Raises:
    ValueError: If ``section`` is empty, multiline, or not a canonical section.
  """
  if not isinstance(section, str) or "\n" in section or "\r" in section:
    raise ValueError("Changelog section name must be a single-line string.")
  cleaned = section.strip()
  canonical = _CANONICAL_SECTION_MAP.get(cleaned.lower())
  if canonical is None:
    allowed = ", ".join(CANONICAL_SECTIONS)
    raise ValueError(
        f"Unsupported changelog section {section!r}. Expected one of: {allowed}."
    )
  return canonical


def format_changelog_bullet(
    ticket_id: str,
    summary: str,
    ticket_type: Optional[str] = None,
    registry: Optional[TicketTypeRegistry] = None,
) -> str:
  """Format a single ticket-prefixed ``CHANGELOG.md`` bullet line.

  Args:
    ticket_id: Raw or normalized ticket identifier.
    summary: Single-line user-facing summary text.
    ticket_type: Optional explicit ticket type (required for bare digits).
    registry: Optional custom ``TicketTypeRegistry``.

  Returns:
    Formatted bullet line ``- <TICKET_ID>: <summary>``.

  Raises:
    ValueError: If ticket ID, summary, or embedded summary prefix is invalid.
  """
  reg = _resolve_registry(registry)
  _, norm_id = reg.resolve(ticket_id, ticket_type=ticket_type)

  if not isinstance(summary, str):
    raise ValueError("Changelog summary must be a string.")
  if "\n" in summary or "\r" in summary:
    raise ValueError("Changelog summary must be a single line.")

  cleaned = re.sub(r"^[-*]\s+", "", summary.lstrip(), count=1).strip()
  if not cleaned:
    raise ValueError("Changelog summary must not be empty.")

  prefix_match = _PREFIX_CAPTURE_RE.match(cleaned)
  if prefix_match:
    candidate_token = prefix_match.group(1)
    remainder = prefix_match.group(2)
    matched = reg.try_resolve(candidate_token)
    if matched is not None:
      _, matched_id = matched
      if matched_id != norm_id:
        raise ValueError(
            f"Summary prefix '{candidate_token}' conflicts with ticket '{norm_id}'."
        )
      cleaned = remainder.strip()
      if not cleaned:
        raise ValueError("Changelog summary must not be empty.")

  return f"- {norm_id}: {cleaned}"


def parse_changelog_bullet(
    line: str,
    registry: Optional[TicketTypeRegistry] = None,
) -> Optional[ChangelogBullet]:
  """Parse a ticket-prefixed changelog bullet line if it matches canonical syntax.

  Args:
    line: Single line from a changelog file.
    registry: Optional custom ``TicketTypeRegistry``.

  Returns:
    ``ChangelogBullet`` on match, or None for unprefixed, malformed, or non-bullet lines.
  """
  if not isinstance(line, str):
    return None
  clean = line.rstrip("\r\n")
  if "\n" in clean or "\r" in clean:
    return None

  match = _BULLET_CAPTURE_RE.match(clean)
  if not match:
    return None

  bullet_char, token, summary = match.group(1), match.group(2), match.group(3).strip()
  if not summary:
    return None

  reg = _resolve_registry(registry)
  matched = reg.parse_bullet_token(token)
  if matched is None:
    return None

  kind_key, canonical_id = matched
  return ChangelogBullet(
      ticket_type=kind_key,
      ticket_id=canonical_id,
      summary=summary,
      bullet_char=bullet_char,
      raw=clean,
  )


def validate_changelog_bullet(
    line: str,
    expected_ticket_id: Optional[str] = None,
    ticket_type: Optional[str] = None,
    registry: Optional[TicketTypeRegistry] = None,
) -> ChangelogBullet:
  """Validate a single changelog bullet line and optionally verify its ticket ID.

  Args:
    line: Candidate changelog bullet line.
    expected_ticket_id: Optional expected ticket ID to enforce against the bullet.
    ticket_type: Optional explicit ticket type for normalizing ``expected_ticket_id``.
    registry: Optional custom ``TicketTypeRegistry``.

  Returns:
    Validated ``ChangelogBullet`` instance.

  Raises:
    ValueError: If the line is malformed, lacks a canonical ticket prefix, or mismatches ``expected_ticket_id``.
  """
  if not isinstance(line, str) or not line.strip():
    raise ValueError("Changelog bullet must not be empty.")
  clean = line.rstrip("\r\n")
  if "\n" in clean or "\r" in clean:
    raise ValueError("Changelog bullet must be a single line.")

  bullet_match = _BULLET_LINE_RE.match(clean)
  if not bullet_match:
    raise ValueError(f"Line is not a Markdown bullet: {clean!r}.")

  bullet_char = bullet_match.group(1)
  body = bullet_match.group(2)
  prefix_match = _PREFIX_CAPTURE_RE.match(body)
  if not prefix_match:
    raise ValueError(
        f"Changelog bullet is missing '<TICKET_ID>: ' prefix: {clean!r}."
    )

  token = prefix_match.group(1)
  rest = prefix_match.group(2)
  reg = _resolve_registry(registry)
  matched = reg.parse_bullet_token(token)
  if matched is None:
    raise ValueError(
        f"Invalid or unregistered ticket prefix {token!r} in bullet: {clean!r}."
    )

  if not rest or not rest[0].isspace():
    raise ValueError(
        f"Changelog bullet must include a space after '{token}:': {clean!r}."
    )

  summary = rest.strip()
  if not summary:
    raise ValueError(f"Changelog bullet summary must not be empty: {clean!r}.")

  kind_key, canonical_id = matched
  parsed = ChangelogBullet(
      ticket_type=kind_key,
      ticket_id=canonical_id,
      summary=summary,
      bullet_char=bullet_char,
      raw=clean,
  )

  if expected_ticket_id is not None:
    _, expected_norm = reg.resolve(expected_ticket_id, ticket_type=ticket_type)
    if parsed.ticket_id != expected_norm:
      raise ValueError(
          f"Expected ticket '{expected_norm}' but found '{parsed.ticket_id}'."
      )

  return parsed


def _find_unreleased_bounds(lines: List[str]) -> Tuple[int, int]:
  """Locate the line bounds ``(start_idx, end_idx)`` of ``## [Unreleased]``."""
  unrel_start: Optional[int] = None
  in_fence = False
  for idx, raw_line in enumerate(lines):
    stripped = raw_line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
      continue
    if in_fence:
      continue
    if unrel_start is None:
      if _UNRELEASED_HEADING_RE.match(stripped):
        unrel_start = idx
    elif _H2_HEADING_RE.match(stripped):
      return unrel_start, idx

  if unrel_start is None:
    raise ValueError("Missing '## [Unreleased]' heading in changelog.")
  return unrel_start, len(lines)


def _insert_into_existing_section(
    lines: List[str], sec_idx: int, sec_end: int, bullet: str
) -> List[str]:
  """Insert ``bullet`` into an existing ``### <Section>`` slice cleanly."""
  last_content_idx = sec_idx
  for idx in range(sec_idx + 1, sec_end):
    if lines[idx].strip():
      last_content_idx = idx

  if last_content_idx > sec_idx:
    insert_at = last_content_idx + 1
    return lines[:insert_at] + [bullet] + lines[insert_at:]

  has_leading_blank = sec_idx + 1 < sec_end and not lines[sec_idx + 1].strip()
  if has_leading_blank:
    insert_at = sec_idx + 2
    inserted = [bullet]
    if insert_at == sec_end and sec_end < len(lines):
      inserted.append("")
    return lines[:insert_at] + inserted + lines[insert_at:]

  inserted = ["", bullet]
  if sec_end < len(lines):
    inserted.append("")
  return lines[: sec_idx + 1] + inserted + lines[sec_idx + 1 :]


def _insert_new_section(
    lines: List[str],
    unrel_start: int,
    unrel_end: int,
    canonical_section: str,
    bullet: str,
) -> List[str]:
  """Append a new ``### <Section>`` block inside ``## [Unreleased]`` cleanly."""
  last_content_idx = unrel_start
  for idx in range(unrel_start + 1, unrel_end):
    if lines[idx].strip():
      last_content_idx = idx

  next_idx = last_content_idx + 1
  if next_idx < unrel_end and not lines[next_idx].strip():
    insert_at = next_idx + 1
    block = [f"### {canonical_section}", "", bullet]
    if insert_at == unrel_end and unrel_end < len(lines):
      block.append("")
    return lines[:insert_at] + block + lines[insert_at:]

  block = ["", f"### {canonical_section}", "", bullet]
  if next_idx == unrel_end and unrel_end < len(lines):
    block.append("")
  return lines[:next_idx] + block + lines[next_idx:]


def add_unreleased_entry(
    changelog_text: str,
    ticket_id: str,
    summary: str,
    section: str = "Added",
    ticket_type: Optional[str] = None,
    registry: Optional[TicketTypeRegistry] = None,
) -> Tuple[str, bool]:
  """Insert a ticket-prefixed bullet under ``## [Unreleased]`` idempotently.

  Args:
    changelog_text: Full contents of ``CHANGELOG.md``.
    ticket_id: Raw or normalized ticket identifier.
    summary: Single-line user-facing summary text.
    section: Keep a Changelog subsection name (defaults to ``Added``).
    ticket_type: Optional explicit ticket type for normalizing ``ticket_id``.
    registry: Optional custom ``TicketTypeRegistry``.

  Returns:
    Tuple ``(updated_text, changed)`` where ``changed`` is False if ``ticket_id``
    already exists under ``## [Unreleased]``.

  Raises:
    ValueError: If ``## [Unreleased]`` is missing, or ticket/summary/section is invalid.
  """
  if not isinstance(changelog_text, str):
    raise ValueError("Changelog content must be a string.")
  reg = _resolve_registry(registry)
  canonical_section = normalize_section_name(section)
  _, norm_id = reg.resolve(ticket_id, ticket_type=ticket_type)
  bullet = format_changelog_bullet(
      norm_id,
      summary,
      ticket_type=None,
      registry=reg,
  )

  lines = changelog_text.splitlines()
  unrel_start, unrel_end = _find_unreleased_bounds(lines)

  in_fence = False
  for line in lines[unrel_start + 1 : unrel_end]:
    if line.strip().startswith("```"):
      in_fence = not in_fence
      continue
    if in_fence:
      continue
    parsed = parse_changelog_bullet(line, registry=reg)
    if parsed is not None and parsed.ticket_id == norm_id:
      return changelog_text, False

  sec_pattern = re.compile(
      rf"^###\s+{re.escape(canonical_section)}\s*$", re.IGNORECASE
  )
  sec_idx: Optional[int] = None
  sec_end = unrel_end
  in_fence = False
  for idx in range(unrel_start + 1, unrel_end):
    stripped = lines[idx].strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
      continue
    if in_fence:
      continue
    if sec_idx is None:
      if sec_pattern.match(stripped):
        sec_idx = idx
    elif stripped.startswith("### "):
      sec_end = idx
      break

  if sec_idx is not None:
    new_lines = _insert_into_existing_section(lines, sec_idx, sec_end, bullet)
  else:
    new_lines = _insert_new_section(
        lines, unrel_start, unrel_end, canonical_section, bullet
    )

  return "\n".join(new_lines) + "\n", True


def validate_changelog_unreleased(
    changelog_text: str,
    ticket_id: Optional[str] = None,
    ticket_type: Optional[str] = None,
    strict_all: bool = False,
    registry: Optional[TicketTypeRegistry] = None,
) -> List[ChangelogBullet]:
  """Validate bullets under ``## [Unreleased]`` in a ``CHANGELOG.md`` string.

  Args:
    changelog_text: Full contents of ``CHANGELOG.md``.
    ticket_id: Optional ticket ID required to appear under ``## [Unreleased]``.
    ticket_type: Optional explicit ticket type for normalizing ``ticket_id``.
    strict_all: Require every bullet under ``## [Unreleased]`` to be ticket-prefixed.
    registry: Optional custom ``TicketTypeRegistry``.

  Returns:
    List of validated ``ChangelogBullet`` objects found under ``## [Unreleased]``.

  Raises:
    ValueError: If ``## [Unreleased]`` is missing, required ticket is absent, or bullets are invalid.
  """
  if not isinstance(changelog_text, str):
    raise ValueError("Changelog content must be a string.")
  reg = _resolve_registry(registry)
  lines = changelog_text.splitlines()
  unrel_start, unrel_end = _find_unreleased_bounds(lines)

  expected_norm: Optional[str] = None
  if ticket_id is not None:
    _, expected_norm = reg.resolve(ticket_id, ticket_type=ticket_type)

  enforce_all = strict_all or (expected_norm is None)
  valid_bullets: List[ChangelogBullet] = []
  found_expected = False
  in_fence = False

  for line in lines[unrel_start + 1 : unrel_end]:
    stripped = line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
      continue
    if in_fence or not _BULLET_LINE_RE.match(line):
      continue

    if enforce_all:
      bullet = validate_changelog_bullet(line, registry=reg)
      valid_bullets.append(bullet)
      if expected_norm is not None and bullet.ticket_id == expected_norm:
        found_expected = True
      continue

    parsed = parse_changelog_bullet(line, registry=reg)
    if parsed is not None:
      valid_bullets.append(parsed)
      if parsed.ticket_id == expected_norm:
        found_expected = True
    elif expected_norm is not None and re.match(
        rf"^\s*[-*]\s+{re.escape(expected_norm)}(?::|\s|$)",
        line,
        re.IGNORECASE,
    ):
      validate_changelog_bullet(
          line, expected_ticket_id=expected_norm, registry=reg
      )

  if expected_norm is not None and not found_expected:
    raise ValueError(
        f"Missing valid changelog bullet for '{expected_norm}' under '## [Unreleased]'."
    )
  return valid_bullets


class ChangelogScript(BaseScript):
  """BaseScript runner for formatting, validating, and updating ``CHANGELOG.md``."""

  def __init__(self, registry: Optional[TicketTypeRegistry] = None) -> None:
    """Initialize the script runner with an optional ticket type registry."""
    super().__init__()
    self.registry: TicketTypeRegistry = _resolve_registry(registry)
    self.subcommand: str = ""
    self.as_json: bool = False
    self.ticket: Optional[str] = None
    self.ticket_type: Optional[str] = None
    self.summary: str = ""
    self.section: str = "Added"
    self.bullet: Optional[str] = None
    self.file_path: Optional[str] = None
    self.strict_all: bool = False

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parse CLI arguments into script attributes without calling ``sys.exit()``.

    Args:
      args: Optional explicit argument vector for hermetic unit tests.
    """
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="changelog.py",
        description="Format, validate, and insert ticket-prefixed CHANGELOG.md bullets.",
        epilog=(
            "Examples:\n"
            "  python3 scripts/changelog.py format --ticket GH-1234 --summary 'Added feature'\n"
            "  python3 scripts/changelog.py validate --bullet '- GH-1234: Added feature'\n"
            "  python3 scripts/changelog.py add --file CHANGELOG.md --ticket GH-228 --section Added --summary 'Added prefix gate'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit output as JSON.",
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        parser_class=SilentArgumentParser,
        help="Subcommand to execute (format, validate, or add).",
    )

    p_fmt = subparsers.add_parser(
        "format",
        help="Format a normalized ticket-prefixed changelog bullet.",
    )
    p_fmt.add_argument("--ticket", required=True, help="Ticket ID (GH-1234, LOCAL-1, or bare digits with --type).")
    p_fmt.add_argument("--summary", required=True, help="Single-line changelog summary.")
    p_fmt.add_argument("--type", default=None, dest="ticket_type", help="Optional explicit ticket type (github or local).")
    p_fmt.add_argument("--json", action="store_true", dest="sub_json", help="Emit output as JSON.")

    p_val = subparsers.add_parser(
        "validate",
        help="Validate a changelog bullet string or CHANGELOG.md file.",
    )
    p_val.add_argument("--bullet", default=None, help="Single changelog bullet line to validate.")
    p_val.add_argument("--file", default=None, help="Path to CHANGELOG.md file to validate.")
    p_val.add_argument("--ticket", default=None, help="Expected ticket ID to enforce.")
    p_val.add_argument("--type", default=None, dest="ticket_type", help="Optional explicit ticket type (github or local).")
    p_val.add_argument(
        "--strict-all",
        action="store_true",
        default=False,
        help="Require every bullet under ## [Unreleased] to have a valid ticket prefix.",
    )
    p_val.add_argument("--json", action="store_true", dest="sub_json", help="Emit output as JSON.")

    p_add = subparsers.add_parser(
        "add",
        help="Insert a ticket-prefixed bullet under ## [Unreleased] idempotently.",
    )
    p_add.add_argument("--file", default="CHANGELOG.md", help="Path to CHANGELOG.md (defaults to CHANGELOG.md).")
    p_add.add_argument("--ticket", required=True, help="Ticket ID (GH-1234, LOCAL-1, or bare digits with --type).")
    p_add.add_argument("--summary", required=True, help="Single-line changelog summary.")
    p_add.add_argument("--section", default="Added", help="Keep a Changelog subsection (Added, Changed, Fixed, etc.).")
    p_add.add_argument("--type", default=None, dest="ticket_type", help="Optional explicit ticket type (github or local).")
    p_add.add_argument("--json", action="store_true", dest="sub_json", help="Emit output as JSON.")

    ns = parser.parse_args(self.raw_args)
    if not ns.subcommand:
      parser.error("Specify a subcommand: format, validate, or add.")

    self.subcommand = ns.subcommand
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))

    if self.subcommand == "format":
      self.ticket = ns.ticket
      self.summary = ns.summary
      self.ticket_type = ns.ticket_type
    elif self.subcommand == "validate":
      if bool(ns.bullet is not None) == bool(ns.file is not None):
        parser.error("Specify exactly one of --bullet or --file.")
      self.bullet = ns.bullet
      self.file_path = ns.file
      self.ticket = ns.ticket
      self.ticket_type = ns.ticket_type
      self.strict_all = bool(ns.strict_all)
    elif self.subcommand == "add":
      self.file_path = ns.file
      self.ticket = ns.ticket
      self.summary = ns.summary
      self.section = ns.section
      self.ticket_type = ns.ticket_type

  def _run_format(self) -> int:
    """Execute the ``format`` subcommand."""
    assert self.ticket is not None
    kind_key, norm_id = self.registry.resolve(
        self.ticket, ticket_type=self.ticket_type
    )
    bullet = format_changelog_bullet(
        norm_id,
        self.summary,
        registry=self.registry,
    )
    if self.as_json:
      self.stdout.write(
          json.dumps({
              "bullet": bullet,
              "ticket_id": norm_id,
              "ticket_type": kind_key,
          })
          + "\n"
      )
    else:
      self.stdout.write(f"{bullet}\n")
    return 0

  def _run_validate(self) -> int:
    """Execute the ``validate`` subcommand."""
    if self.bullet is not None:
      parsed = validate_changelog_bullet(
          self.bullet,
          expected_ticket_id=self.ticket,
          ticket_type=self.ticket_type,
          registry=self.registry,
      )
      if self.as_json:
        self.stdout.write(
            json.dumps({
                "valid": True,
                "ticket_id": parsed.ticket_id,
                "ticket_type": parsed.ticket_type,
                "summary": parsed.summary,
            })
            + "\n"
        )
      else:
        self.stdout.write(f"OK: {parsed.ticket_id}\n")
      return 0

    assert self.file_path is not None
    if not os.path.isfile(self.file_path):
      raise FileNotFoundError(f"Changelog file not found: {self.file_path}")
    with open(self.file_path, "r", encoding="utf-8") as f:
      content = f.read()

    bullets = validate_changelog_unreleased(
        content,
        ticket_id=self.ticket,
        ticket_type=self.ticket_type,
        strict_all=self.strict_all,
        registry=self.registry,
    )
    if self.as_json:
      self.stdout.write(
          json.dumps({
              "valid": True,
              "file": self.file_path,
              "count": len(bullets),
          })
          + "\n"
      )
    else:
      self.stdout.write(f"OK: validated {len(bullets)} bullet(s) in {self.file_path}\n")
    return 0

  def _run_add(self) -> int:
    """Execute the ``add`` subcommand."""
    assert self.file_path is not None
    assert self.ticket is not None
    if not os.path.isfile(self.file_path):
      raise FileNotFoundError(f"Changelog file not found: {self.file_path}")
    with open(self.file_path, "r", encoding="utf-8") as f:
      content = f.read()

    _, norm_id = self.registry.resolve(
        self.ticket, ticket_type=self.ticket_type
    )
    updated, changed = add_unreleased_entry(
        content,
        norm_id,
        self.summary,
        section=self.section,
        registry=self.registry,
    )
    if changed:
      with open(self.file_path, "w", encoding="utf-8") as f:
        f.write(updated)

    if self.as_json:
      self.stdout.write(
          json.dumps({
              "changed": changed,
              "file": self.file_path,
              "ticket_id": norm_id,
              "section": normalize_section_name(self.section),
          })
          + "\n"
      )
    elif changed:
      self.stdout.write(f"Added {norm_id} entry to {self.file_path}\n")
    else:
      self.stdout.write(f"Already present: {norm_id} in {self.file_path}\n")
    return 0

  def run(self) -> int:
    """Dispatch the selected changelog subcommand.

    Returns:
      Process exit code (0 on success).

    Raises:
      ValueError: If an unsupported subcommand is set.
    """
    if self.subcommand == "format":
      return self._run_format()
    if self.subcommand == "validate":
      return self._run_validate()
    if self.subcommand == "add":
      return self._run_add()
    raise ValueError(f"Unsupported subcommand: {self.subcommand}")
