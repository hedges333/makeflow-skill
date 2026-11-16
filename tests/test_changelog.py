#!/usr/bin/env python3
"""Hermetic unit tests for Target 90 changelog ticket prefixing (GH-228).

Tests:
1. TicketTypeStrategy implementations (GitHubTicketStrategy, LocalTicketStrategy)
   and TicketTypeRegistry lookup, error handling, and replacement.
2. Strategy + Registry extensibility via a test-local JiraTicketStrategy without
   modifying built-in handlers.
3. Bullet formatting, parsing, and validation (prefix normalization, duplicate
   prefix prevention, multiline/empty/conflicting prefix rejections).
4. Idempotent insertion into CHANGELOG.md under ## [Unreleased] subsections
   without mutating historical release headings.
5. ChangelogScript(BaseScript) CLI subcommands (format, validate, add) via
   in-memory StringIO streams and temporary directories.
6. Makefile target wiring for test-changelog.
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from typing import Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.changelog import (
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


class JiraTicketStrategy(TicketTypeStrategy):
  """Test-local Jira ticket strategy verifying Registry extensibility."""

  kind = "JIRA"
  _PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*-[1-9][0-9]*)$")

  def normalize(
      self, raw_ticket: str, *, allow_bare_digits: bool = False
  ) -> Optional[str]:
    """Normalize canonical Jira issue keys such as AUTH-102."""
    del allow_bare_digits
    cleaned = (raw_ticket or "").strip()
    match = self._PATTERN.match(cleaned)
    return match.group(1) if match else None


class TestTicketStrategiesAndRegistry(unittest.TestCase):
  """Tests built-in ticket strategies, registry operations, and Jira extensibility."""

  def test_github_strategy_accepts_and_rejects(self):
    """GitHubTicketStrategy normalizes GH-/gh-/# and rejects invalid or local IDs."""
    strategy = GitHubTicketStrategy()
    self.assertEqual(strategy.kind, "GITHUB")

    for raw in ("GH-1234", "gh-1234", "  Gh-1234  ", "#1234"):
      self.assertEqual(
          strategy.normalize(raw),
          "GH-1234",
          f"Expected GH-1234 for {raw!r}",
      )

    self.assertIsNone(strategy.normalize("1234"))
    self.assertEqual(
        strategy.normalize("1234", allow_bare_digits=True),
        "GH-1234",
    )

    for bad in ("0", "-1", "GH-0", "GH-01", "#0", "abc", "", "   ", "LOCAL-1"):
      self.assertIsNone(
          strategy.normalize(bad, allow_bare_digits=True),
          f"Expected rejection for {bad!r}",
      )

  def test_local_strategy_accepts_and_rejects(self):
    """LocalTicketStrategy normalizes LOCAL-/local- and rejects invalid or GH IDs."""
    strategy = LocalTicketStrategy()
    self.assertEqual(strategy.kind, "LOCAL")

    for raw in ("LOCAL-1234", "local-1234", "  Local-1234  "):
      self.assertEqual(
          strategy.normalize(raw),
          "LOCAL-1234",
          f"Expected LOCAL-1234 for {raw!r}",
      )

    self.assertIsNone(strategy.normalize("1234"))
    self.assertEqual(
        strategy.normalize("1234", allow_bare_digits=True),
        "LOCAL-1234",
    )

    for bad in ("0", "LOCAL-0", "LOCAL-01", "-1", "#1234", "GH-1234", "", "   "):
      self.assertIsNone(
          strategy.normalize(bad, allow_bare_digits=True),
          f"Expected rejection for {bad!r}",
      )

  def test_default_registry_and_registration_invariants(self):
    """TicketTypeRegistry includes GITHUB and LOCAL and rejects bare digits or duplicates."""
    reg = TicketTypeRegistry.default_registry()
    self.assertIn("GITHUB", reg)
    self.assertIn("LOCAL", reg)
    self.assertEqual(reg.kinds(), ("GITHUB", "LOCAL"))

    self.assertEqual(reg.normalize("1234", ticket_type="github"), "GH-1234")
    self.assertEqual(reg.normalize("1234", ticket_type="local"), "LOCAL-1234")

    with self.assertRaises(ValueError):
      reg.normalize("1234")

    with self.assertRaises(ValueError):
      reg.normalize("AUTH-102", ticket_type="jira")

    with self.assertRaises(ValueError):
      reg.register(GitHubTicketStrategy())

    replacement = GitHubTicketStrategy()
    reg.register(replacement, replace=True)
    self.assertIs(reg.get("GITHUB"), replacement)

  def test_jira_strategy_extensibility_without_modifying_builtins(self):
    """Registering JiraTicketStrategy on a fresh TicketTypeRegistry supports format, parse, and validate."""
    reg = TicketTypeRegistry()
    reg.register(GitHubTicketStrategy())
    reg.register(LocalTicketStrategy())
    reg.register(JiraTicketStrategy())

    formatted = format_changelog_bullet(
        "AUTH-102",
        "Fixed JWT token refresh",
        registry=reg,
    )
    self.assertEqual(formatted, "- AUTH-102: Fixed JWT token refresh")

    parsed = parse_changelog_bullet(formatted, registry=reg)
    self.assertEqual(
        parsed,
        ChangelogBullet(
            ticket_type="JIRA",
            ticket_id="AUTH-102",
            summary="Fixed JWT token refresh",
            bullet_char="-",
            raw="- AUTH-102: Fixed JWT token refresh",
        ),
    )

    validated = validate_changelog_bullet(
        formatted,
        expected_ticket_id="AUTH-102",
        registry=reg,
    )
    self.assertEqual(validated.ticket_type, "JIRA")
    self.assertEqual(validated.ticket_id, "AUTH-102")
    self.assertEqual(validated.summary, "Fixed JWT token refresh")

    with self.assertRaises(ValueError):
      validate_changelog_bullet(formatted, expected_ticket_id="AUTH-999", registry=reg)

    with self.assertRaises(ValueError):
      format_changelog_bullet("auth-102", "Lowercase rejected", registry=reg)

  def test_registry_validation_and_membership_edge_cases(self):
    """TicketTypeRegistry rejects bad strategies/kinds and handles non-str or missing lookups (GH-228)."""
    reg = TicketTypeRegistry.default_registry()

    for bad_strategy in ("GITHUB", object(), None):
      with self.assertRaises(TypeError):
        reg.register(bad_strategy)

    class EmptyKindStrategy(TicketTypeStrategy):
      """Strategy stub with empty kind string."""

      kind = ""

      def normalize(
          self, raw_ticket: str, *, allow_bare_digits: bool = False
      ) -> Optional[str]:
        """Return None for empty-kind test stub."""
        del raw_ticket, allow_bare_digits
        return None

    class WhitespaceKindStrategy(TicketTypeStrategy):
      """Strategy stub with whitespace-only kind string."""

      kind = "   "

      def normalize(
          self, raw_ticket: str, *, allow_bare_digits: bool = False
      ) -> Optional[str]:
        """Return None for whitespace-kind test stub."""
        del raw_ticket, allow_bare_digits
        return None

    for bad_kind_strategy in (EmptyKindStrategy(), WhitespaceKindStrategy()):
      with self.assertRaises(ValueError):
        reg.register(bad_kind_strategy)

    for non_str in (None, 123, ["GITHUB"]):
      self.assertIsNone(reg.get(non_str))
      self.assertFalse(non_str in reg)

    self.assertFalse("JIRA" in reg)
    self.assertIsNone(reg.get("JIRA"))


class TestChangelogBulletFormattingAndValidation(unittest.TestCase):
  """Tests formatting, parsing, and validation of changelog bullet lines."""

  def test_format_github_and_local_bullets(self):
    """format_changelog_bullet formats canonical GH- and LOCAL- bullets."""
    self.assertEqual(
        format_changelog_bullet(
            "GH-1234",
            "Added frobnostication to reticulator dongle",
        ),
        "- GH-1234: Added frobnostication to reticulator dongle",
    )
    self.assertEqual(
        format_changelog_bullet(
            "#1234",
            "Added frobnostication to reticulator dongle",
        ),
        "- GH-1234: Added frobnostication to reticulator dongle",
    )
    self.assertEqual(
        format_changelog_bullet(
            "1234",
            "Added frobnostication to reticulator dongle",
            ticket_type="github",
        ),
        "- GH-1234: Added frobnostication to reticulator dongle",
    )
    self.assertEqual(
        format_changelog_bullet(
            "LOCAL-1234",
            "Added frobnostication to reticulator dongle",
        ),
        "- LOCAL-1234: Added frobnostication to reticulator dongle",
    )
    self.assertEqual(
        format_changelog_bullet(
            "1234",
            "Added frobnostication to reticulator dongle",
            ticket_type="local",
        ),
        "- LOCAL-1234: Added frobnostication to reticulator dongle",
    )

  def test_format_strips_bullet_markers_and_avoids_double_prefixing(self):
    """Leading bullet markers and identical ticket prefixes are stripped cleanly."""
    self.assertEqual(
        format_changelog_bullet("GH-1234", "- Added feature"),
        "- GH-1234: Added feature",
    )
    self.assertEqual(
        format_changelog_bullet("GH-1234", "* Added feature"),
        "- GH-1234: Added feature",
    )
    self.assertEqual(
        format_changelog_bullet(
            "GH-1234",
            "GH-1234: Added frobnostication to reticulator dongle",
        ),
        "- GH-1234: Added frobnostication to reticulator dongle",
    )
    self.assertEqual(
        format_changelog_bullet("GH-1234", "- gh-1234: Added feature"),
        "- GH-1234: Added feature",
    )
    self.assertEqual(
        format_changelog_bullet("GH-1234", "#1234: Added feature"),
        "- GH-1234: Added feature",
    )
    self.assertEqual(
        format_changelog_bullet("LOCAL-1234", "LOCAL-1234: Added feature"),
        "- LOCAL-1234: Added feature",
    )
    self.assertEqual(
        format_changelog_bullet("GH-1234", "Note: keep existing behavior"),
        "- GH-1234: Note: keep existing behavior",
    )

  def test_format_rejects_empty_multiline_and_conflicting_prefixes(self):
    """format_changelog_bullet raises ValueError on bad summaries or conflicting ticket IDs."""
    for bad_summary in ("", "   ", "-   ", "* ", "GH-1234:   "):
      with self.assertRaises(ValueError, msg=f"Should reject {bad_summary!r}"):
        format_changelog_bullet("GH-1234", bad_summary)

    for multiline in ("First line\nSecond line", "First line\rSecond line"):
      with self.assertRaises(ValueError):
        format_changelog_bullet("GH-1234", multiline)

    for tid, summary in (
        ("GH-1234", "GH-999: Wrong ticket"),
        ("GH-1234", "LOCAL-1: Wrong ticket type"),
        ("LOCAL-1", "GH-1234: Wrong ticket type"),
    ):
      with self.assertRaises(ValueError, msg=f"Should reject {(tid, summary)!r}"):
        format_changelog_bullet(tid, summary)

  def test_parse_changelog_bullet(self):
    """parse_changelog_bullet parses valid bullets and returns None for unprefixed or non-bullet lines."""
    gh_bullet = parse_changelog_bullet("- GH-1234: Added frobnostication")
    self.assertEqual(
        gh_bullet,
        ChangelogBullet(
            ticket_type="GITHUB",
            ticket_id="GH-1234",
            summary="Added frobnostication",
            bullet_char="-",
            raw="- GH-1234: Added frobnostication",
        ),
    )

    local_bullet = parse_changelog_bullet("* LOCAL-1234: Fixed task: edge case\n")
    self.assertEqual(
        local_bullet,
        ChangelogBullet(
            ticket_type="LOCAL",
            ticket_id="LOCAL-1234",
            summary="Fixed task: edge case",
            bullet_char="*",
            raw="* LOCAL-1234: Fixed task: edge case",
        ),
    )

    for non_matching in (
        "- Legacy note.",
        "- Note: legacy note without ticket ID.",
        "## [Unreleased]",
        "Plain paragraph line.",
        "- gh-1234: Lowercase prefix.",
        "- #1234: Hash prefix.",
        "- 1234: Bare number.",
        "- GH-1234:Missing space.",
        "- GH-1234:   ",
        "",
    ):
      self.assertIsNone(
          parse_changelog_bullet(non_matching),
          f"Expected None for {non_matching!r}",
      )

  def test_validate_changelog_bullet(self):
    """validate_changelog_bullet enforces valid prefix, non-empty summary, and expected_ticket_id."""
    parsed = validate_changelog_bullet(
        "- GH-1234: Added feature",
        expected_ticket_id="#1234",
    )
    self.assertEqual(parsed.ticket_id, "GH-1234")
    self.assertEqual(parsed.ticket_type, "GITHUB")

    local_parsed = validate_changelog_bullet(
        "* LOCAL-42: Added task",
        expected_ticket_id="42",
        ticket_type="local",
    )
    self.assertEqual(local_parsed.ticket_id, "LOCAL-42")
    self.assertEqual(local_parsed.ticket_type, "LOCAL")

    for bad_line in (
        "",
        "   ",
        "GH-1234: Missing bullet marker",
        "- Legacy note without prefix.",
        "- #1234: Non-canonical hash prefix",
        "- gh-1234: Lowercase prefix",
        "- GH-1234:Missing space",
        "- GH-1234:   ",
        "- GH-1234: Line one\nLine two",
    ):
      with self.assertRaises(ValueError, msg=f"Should reject {bad_line!r}"):
        validate_changelog_bullet(bad_line)

    with self.assertRaises(ValueError):
      validate_changelog_bullet(
          "- GH-1234: Added feature",
          expected_ticket_id="GH-999",
      )

    with self.assertRaises(ValueError):
      validate_changelog_bullet(
          "- GH-1234: Added feature",
          expected_ticket_id="LOCAL-1234",
      )

  def test_normalize_section_name_canonical_and_rejections(self):
    """normalize_section_name handles all 6 canonical sections and rejects invalid values (GH-228)."""
    self.assertEqual(
        CANONICAL_SECTIONS,
        ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"),
    )
    for canonical in CANONICAL_SECTIONS:
      self.assertEqual(normalize_section_name(canonical), canonical)
      self.assertEqual(normalize_section_name(canonical.lower()), canonical)
      self.assertEqual(
          normalize_section_name(f"  {canonical.swapcase()}  "),
          canonical,
      )

    for bad_section in (
        "",
        "   ",
        "Added\nFixed",
        "Added\rFixed",
        "Misc",
        None,
        123,
    ):
      with self.assertRaises(ValueError, msg=f"Should reject {bad_section!r}"):
        normalize_section_name(bad_section)

  def test_validate_unreleased_case_insensitive_malformed_prefix(self):
    """validate_changelog_unreleased surfaces specific malformed-bullet errors over missing-ticket fallback (GH-228)."""
    cases = (
        (
            "GH-228",
            "- gh-228: Lowercase prefix.",
            "Invalid or unregistered ticket prefix 'gh-228'",
        ),
        (
            "GH-228",
            "- GH-228:Missing space.",
            "must include a space after 'GH-228:'",
        ),
        (
            "GH-228",
            "- GH-228 missing colon.",
            "missing '<TICKET_ID>: ' prefix",
        ),
        (
            "LOCAL-7",
            "- local-7: Lowercase local.",
            "Invalid or unregistered ticket prefix 'local-7'",
        ),
    )
    for ticket_id, malformed_line, expected_fragment in cases:
      changelog = (
          "# Changelog\n\n"
          "## [Unreleased]\n\n"
          "### Added\n\n"
          "- Legacy note without ticket ID.\n"
          f"{malformed_line}\n"
      )
      with self.subTest(ticket_id=ticket_id, line=malformed_line):
        with self.assertRaises(ValueError) as ctx:
          validate_changelog_unreleased(changelog, ticket_id=ticket_id)
        msg = str(ctx.exception)
        self.assertIn(expected_fragment, msg)
        self.assertNotIn("Missing valid changelog bullet", msg)


class TestAddUnreleasedEntry(unittest.TestCase):
  """Tests idempotent insertion into CHANGELOG.md ## [Unreleased]."""

  def test_inserts_under_existing_added_and_preserves_releases(self):
    """add_unreleased_entry appends to existing ### Added without altering ## [1.0.0]."""
    initial = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- Legacy unreleased note.\n\n"
        "### Fixed\n\n"
        "- Legacy bug fix.\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- GH-1234: Historical bullet in released section.\n"
    )
    updated, changed = add_unreleased_entry(
        initial,
        "GH-1234",
        "Added frobnostication to reticulator dongle",
        section="Added",
    )
    self.assertTrue(changed)
    expected = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- Legacy unreleased note.\n"
        "- GH-1234: Added frobnostication to reticulator dongle\n\n"
        "### Fixed\n\n"
        "- Legacy bug fix.\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- GH-1234: Historical bullet in released section.\n"
    )
    self.assertEqual(updated, expected)

  def test_creates_missing_subsection_under_unreleased(self):
    """add_unreleased_entry creates ### Added / ### Fixed / ### Changed when absent."""
    empty_unreleased = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- Initial release.\n"
    )
    step1, changed1 = add_unreleased_entry(
        empty_unreleased,
        "GH-10",
        "Added new endpoint",
        section="Added",
    )
    self.assertTrue(changed1)
    self.assertIn("### Added\n\n- GH-10: Added new endpoint\n", step1)
    self.assertIn("## [1.0.0] - 2026-01-01\n\n### Added\n\n- Initial release.\n", step1)

    step2, changed2 = add_unreleased_entry(
        step1,
        "LOCAL-2",
        "Fixed edge case",
        section="Fixed",
    )
    self.assertTrue(changed2)
    self.assertIn("### Fixed\n\n- LOCAL-2: Fixed edge case\n", step2)

    step3, changed3 = add_unreleased_entry(
        step2,
        "GH-11",
        "Changed output format",
        section="Changed",
    )
    self.assertTrue(changed3)
    self.assertIn("### Changed\n\n- GH-11: Changed output format\n", step3)

  def test_idempotent_when_ticket_already_present_in_unreleased(self):
    """add_unreleased_entry returns (original_text, False) if ticket already appears under ## [Unreleased]."""
    existing = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Fixed\n\n"
        "- GH-1234: Fixed existing bug\n\n"
        "## [1.0.0] - 2026-01-01\n"
    )
    updated, changed = add_unreleased_entry(
        existing,
        "#1234",
        "Duplicate summary ignored",
        section="Added",
    )
    self.assertFalse(changed)
    self.assertEqual(updated, existing)

  def test_raises_when_unreleased_missing_or_invalid_section(self):
    """add_unreleased_entry raises ValueError if ## [Unreleased] is absent or section is invalid."""
    with self.assertRaises(ValueError):
      add_unreleased_entry(
          "# Changelog\n\n## [1.0.0] - 2026-01-01\n",
          "GH-1234",
          "Missing unreleased heading",
      )

    with self.assertRaises(ValueError):
      add_unreleased_entry(
          "# Changelog\n\n## [Unreleased]\n",
          "GH-1234",
          "Bad section",
          section="UnknownSection",
      )

  def test_inserts_into_empty_existing_subsection_variants(self):
    """add_unreleased_entry inserts cleanly into empty ### Added with/without leading blanks before ### Changed or EOF (GH-228)."""
    cases = (
        (
            "leading_blank_before_next_section",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n### Changed\n\n- GH-1: Existing change.\n",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- GH-228: Added item.\n\n### Changed\n\n- GH-1: Existing change.\n",
        ),
        (
            "leading_blank_at_eof",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- GH-228: Added item.\n",
        ),
        (
            "no_leading_blank_before_next_section",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n### Changed\n\n- GH-1: Existing change.\n",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- GH-228: Added item.\n\n### Changed\n\n- GH-1: Existing change.\n",
        ),
        (
            "no_leading_blank_at_eof",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n",
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- GH-228: Added item.\n",
        ),
    )
    for name, initial, expected in cases:
      with self.subTest(case=name):
        updated, changed = add_unreleased_entry(
            initial,
            "GH-228",
            "Added item.",
            section="Added",
        )
        self.assertTrue(changed)
        self.assertEqual(updated, expected)

  def test_fenced_block_before_unreleased_does_not_hijack_bounds(self):
    """Triple-backtick block before ## [Unreleased] containing decoy ## [Unreleased] does not hijack bounds (GH-228)."""
    changelog = (
        "# Changelog\n\n"
        "Format example:\n\n"
        "```markdown\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- GH-999: Fenced decoy before unreleased.\n"
        "```\n\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- GH-228: Real unreleased entry.\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- GH-1: Released entry.\n"
    )
    bullets = validate_changelog_unreleased(
        changelog,
        ticket_id="GH-228",
        strict_all=True,
    )
    self.assertEqual(len(bullets), 1)
    self.assertEqual(bullets[0].ticket_id, "GH-228")

    with self.assertRaises(ValueError):
      validate_changelog_unreleased(changelog, ticket_id="GH-999")

    updated, changed = add_unreleased_entry(
        changelog,
        "GH-999",
        "Real entry added after fence.",
        section="Added",
    )
    self.assertTrue(changed)
    expected = (
        "# Changelog\n\n"
        "Format example:\n\n"
        "```markdown\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- GH-999: Fenced decoy before unreleased.\n"
        "```\n\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- GH-228: Real unreleased entry.\n"
        "- GH-999: Real entry added after fence.\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- GH-1: Released entry.\n"
    )
    self.assertEqual(updated, expected)

  def test_fenced_block_inside_unreleased_is_skipped(self):
    """Triple-backtick block inside ## [Unreleased] with decoy headings and bullets is skipped cleanly (GH-228)."""
    changelog = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "Example usage:\n\n"
        "```markdown\n"
        "## [9.9.9] - 2026-02-01\n\n"
        "### Added\n\n"
        "- GH-999: Fenced example.\n"
        "- unprefixed example\n"
        "```\n\n"
        "### Added\n\n"
        "- GH-228: Real unreleased entry.\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- GH-1: Released entry.\n"
    )
    strict_bullets = validate_changelog_unreleased(changelog, strict_all=True)
    self.assertEqual(len(strict_bullets), 1)
    self.assertEqual(strict_bullets[0].ticket_id, "GH-228")

    with self.assertRaises(ValueError) as ctx:
      validate_changelog_unreleased(changelog, ticket_id="GH-999")
    self.assertIn("Missing valid changelog bullet for 'GH-999'", str(ctx.exception))

    updated, changed = add_unreleased_entry(
        changelog,
        "GH-999",
        "Real entry outside fence.",
        section="Added",
    )
    self.assertTrue(changed)
    expected = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "Example usage:\n\n"
        "```markdown\n"
        "## [9.9.9] - 2026-02-01\n\n"
        "### Added\n\n"
        "- GH-999: Fenced example.\n"
        "- unprefixed example\n"
        "```\n\n"
        "### Added\n\n"
        "- GH-228: Real unreleased entry.\n"
        "- GH-999: Real entry outside fence.\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- GH-1: Released entry.\n"
    )
    self.assertEqual(updated, expected)


class TestChangelogScriptCLI(unittest.TestCase):
  """Tests ChangelogScript(BaseScript) in memory via StringIO and TemporaryDirectory."""

  def _run_cli(self, args: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = ChangelogScript().execute(args=args, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()

  def test_format_subcommand_github_local_and_errors(self):
    """format subcommand prints normalized bullet and rejects ambiguous bare digits."""
    code, out, err = self._run_cli([
        "format",
        "--ticket",
        "GH-1234",
        "--summary",
        "Added frobnostication to reticulator dongle",
    ])
    self.assertEqual(code, 0, err)
    self.assertEqual(out, "- GH-1234: Added frobnostication to reticulator dongle\n")
    self.assertEqual(err, "")

    code_gh_num, out_gh_num, err_gh_num = self._run_cli([
        "format",
        "--type",
        "github",
        "--ticket",
        "1234",
        "--summary",
        "Added frobnostication to reticulator dongle",
    ])
    self.assertEqual(code_gh_num, 0, err_gh_num)
    self.assertEqual(
        out_gh_num,
        "- GH-1234: Added frobnostication to reticulator dongle\n",
    )

    code_local, out_local, err_local = self._run_cli([
        "format",
        "--ticket",
        "LOCAL-1234",
        "--summary",
        "Added frobnostication to reticulator dongle",
    ])
    self.assertEqual(code_local, 0, err_local)
    self.assertEqual(
        out_local,
        "- LOCAL-1234: Added frobnostication to reticulator dongle\n",
    )

    code_bad, out_bad, err_bad = self._run_cli([
        "format",
        "--ticket",
        "1234",
        "--summary",
        "Ambiguous ticket",
    ])
    self.assertEqual(code_bad, 1)
    self.assertEqual(out_bad, "")
    self.assertIn("Error:", err_bad)

  def test_validate_subcommand_bullet_and_file_modes(self):
    """validate subcommand supports --bullet, --file --ticket, and --strict-all."""
    code_ok, out_ok, err_ok = self._run_cli([
        "validate",
        "--bullet",
        "- GH-1234: Added feature",
        "--json",
    ])
    self.assertEqual(code_ok, 0, err_ok)
    payload = json.loads(out_ok)
    self.assertTrue(payload["valid"])
    self.assertEqual(payload["ticket_id"], "GH-1234")
    self.assertEqual(payload["ticket_type"], "GITHUB")

    code_bad_bullet, _, err_bad_bullet = self._run_cli([
        "validate",
        "--bullet",
        "- Unprefixed bullet",
    ])
    self.assertEqual(code_bad_bullet, 1)
    self.assertIn("Error:", err_bad_bullet)

    with tempfile.TemporaryDirectory() as tmpdir:
      mixed_path = os.path.join(tmpdir, "CHANGELOG.md")
      with open(mixed_path, "w", encoding="utf-8") as f:
        f.write(
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Added\n\n"
            "- Legacy bullet without ticket ID.\n"
            "- GH-228: Enforce ticket prefix on changelog bullets.\n"
        )

      code_ticket_ok, _, err_ticket_ok = self._run_cli([
          "validate",
          "--file",
          mixed_path,
          "--ticket",
          "GH-228",
      ])
      self.assertEqual(code_ticket_ok, 0, err_ticket_ok)

      code_ticket_miss, _, err_ticket_miss = self._run_cli([
          "validate",
          "--file",
          mixed_path,
          "--ticket",
          "GH-999",
      ])
      self.assertEqual(code_ticket_miss, 1)
      self.assertIn("GH-999", err_ticket_miss)

      code_strict_fail, _, err_strict_fail = self._run_cli([
          "validate",
          "--file",
          mixed_path,
          "--strict-all",
      ])
      self.assertEqual(code_strict_fail, 1)
      self.assertIn("Error:", err_strict_fail)

      strict_path = os.path.join(tmpdir, "STRICT.md")
      with open(strict_path, "w", encoding="utf-8") as f:
        f.write(
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Added\n\n"
            "- GH-228: Enforce ticket prefix.\n"
            "- LOCAL-7: Add strict mode.\n"
        )
      code_strict_ok, _, err_strict_ok = self._run_cli([
          "validate",
          "--file",
          strict_path,
      ])
      self.assertEqual(code_strict_ok, 0, err_strict_ok)

  def test_add_subcommand_updates_once_and_handles_errors_without_sys_exit(self):
    """add subcommand inserts once, stays idempotent, and returns 1 on missing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
      cl_path = os.path.join(tmpdir, "CHANGELOG.md")
      with open(cl_path, "w", encoding="utf-8") as f:
        f.write("# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Legacy item.\n")

      code1, out1, err1 = self._run_cli([
          "add",
          "--file",
          cl_path,
          "--ticket",
          "GH-228",
          "--section",
          "Added",
          "--summary",
          "Enforce ticket prefixes on Target 90 changelog bullets.",
      ])
      self.assertEqual(code1, 0, err1)
      self.assertIn("GH-228", out1)

      with open(cl_path, "r", encoding="utf-8") as f:
        after_first = f.read()
      self.assertIn(
          "- GH-228: Enforce ticket prefixes on Target 90 changelog bullets.\n",
          after_first,
      )

      code2, _, err2 = self._run_cli([
          "add",
          "--file",
          cl_path,
          "--ticket",
          "GH-228",
          "--section",
          "Added",
          "--summary",
          "Enforce ticket prefixes on Target 90 changelog bullets.",
      ])
      self.assertEqual(code2, 0, err2)
      with open(cl_path, "r", encoding="utf-8") as f:
        after_second = f.read()
      self.assertEqual(after_second, after_first)

      missing_path = os.path.join(tmpdir, "MISSING.md")
      code_missing, _, err_missing = self._run_cli([
          "add",
          "--file",
          missing_path,
          "--ticket",
          "GH-228",
          "--summary",
          "Should fail gracefully",
      ])
      self.assertEqual(code_missing, 1)
      self.assertIn("Error:", err_missing)

  def test_validate_cli_reports_malformed_prefix_for_expected_ticket(self):
    """validate --file --ticket GH-228 surfaces specific malformed-bullet diagnostics on stderr (GH-228)."""
    cases = (
        (
            "- gh-228: Lowercase prefix.",
            "Invalid or unregistered ticket prefix 'gh-228'",
        ),
        (
            "- GH-228:Missing space.",
            "must include a space after 'GH-228:'",
        ),
        (
            "- GH-228 missing colon.",
            "missing '<TICKET_ID>: ' prefix",
        ),
    )
    with tempfile.TemporaryDirectory() as tmpdir:
      for idx, (malformed_line, expected_fragment) in enumerate(cases):
        cl_path = os.path.join(tmpdir, f"CHANGELOG_{idx}.md")
        with open(cl_path, "w", encoding="utf-8") as f:
          f.write(
              "# Changelog\n\n"
              "## [Unreleased]\n\n"
              "### Added\n\n"
              f"{malformed_line}\n"
          )
        with self.subTest(line=malformed_line):
          code, out, err = self._run_cli([
              "validate",
              "--file",
              cl_path,
              "--ticket",
              "GH-228",
          ])
          self.assertEqual(code, 1)
          self.assertEqual(out, "")
          self.assertIn("Error:", err)
          self.assertIn(expected_fragment, err)
          self.assertNotIn("Missing valid changelog bullet", err)

  def test_cli_parser_edge_errors(self):
    """ChangelogScript rejects missing subcommand, conflicting or absent validate targets, and empty --bullet (GH-228)."""
    bad_arg_vectors = (
        ["validate", "--bullet", "- GH-1: x", "--file", "dummy.md"],
        ["validate"],
        ["validate", "--bullet", ""],
        [],
    )
    for argv in bad_arg_vectors:
      with self.subTest(argv=argv):
        code, out, err = self._run_cli(argv)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("Error:", err)

  def test_help_flag_and_script_exports(self):
    """--help prints usage cleanly and scripts/changelog.py re-exports all 13 public symbols (GH-228)."""
    code_help, out_help, err_help = self._run_cli(["--help"])
    self.assertEqual(code_help, 0, err_help)
    self.assertIn("changelog.py", out_help)

    import scripts.changelog as cli_mod

    expected_exports = {
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
    }
    self.assertEqual(set(cli_mod.__all__), expected_exports)
    self.assertEqual(len(cli_mod.__all__), len(expected_exports))
    for sym in sorted(expected_exports):
      self.assertTrue(
          hasattr(cli_mod, sym),
          f"Missing export {sym} in scripts/changelog.py",
      )


class TestChangelogMakefileWiring(unittest.TestCase):
  """Verifies Makefile exposes test-changelog target wired to tests/test_changelog.py."""

  def test_makefile_declares_test_changelog_target(self):
    """Makefile .PHONY, help, and recipe invoke tests/test_changelog.py via $(PYTHON)."""
    makefile_path = os.path.join(_REPO_ROOT, "Makefile")
    with open(makefile_path, "r", encoding="utf-8") as f:
      mk_text = f.read()

    phony_line = next(
        (ln for ln in mk_text.splitlines() if ln.startswith(".PHONY:")),
        "",
    )
    self.assertIn("test-changelog", phony_line.split())
    self.assertIn("make test-changelog", mk_text)
    self.assertRegex(mk_text, r"(?m)^test-changelog:")
    recipe_match = re.search(
        r"(?ms)^test-changelog:.*?(?=^\S|\Z)",
        mk_text,
    )
    self.assertIsNotNone(recipe_match)
    recipe = recipe_match.group(0)
    self.assertIn("$(PYTHON)", recipe)
    self.assertIn("test_changelog.py", recipe)


if __name__ == "__main__":
  unittest.main()
