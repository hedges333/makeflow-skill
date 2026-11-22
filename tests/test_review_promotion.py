#!/usr/bin/env python3
"""Hermetic unit and boundary tests for critic review validation and promotion (GH-221).

Verifies:
1. Strict positive ASCII integer round parsing.
2. Canonical Six-Pillar heading order, uniqueness, and non-empty prose body checks.
3. Machine-readable binary verdict grammar and soft-token rejection outside fences.
4. Byte-for-byte promotion via safe_copy_file with SCAR-SYS-05 EPERM resilience.
5. WorkflowGatesScript promote-review CLI text, JSON, and Vector 4 Reflex exit code 2.
6. Real GNU make promote-review boundary execution in tempfile.TemporaryDirectory().
"""

import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.review_promotion import (
    CANONICAL_SIX_PILLAR_HEADINGS,
    ReviewValidationResult,
    parse_round_number,
    promote_review_round,
    validate_critic_review_text,
)
from lib.workflow_gates import WorkflowGatesScript, generate_state_makefile


def _build_valid_review(
    verdict_line: str = "VERDICT: APPROVED (0 BLOCKERS)",
    extra_pillar_body: str = "",
) -> str:
  """Constructs a canonical Six-Pillar critic review for test fixtures.

  Args:
    verdict_line: Final verdict line appended outside code fences.
    extra_pillar_body: Optional extra markdown appended under Pillar 2.

  Returns:
    Complete markdown review document satisfying all Six-Pillar invariants.
  """
  sections = [
      "# Adversarial Code Review — Round 1\n",
      "### 1. Intent\nThe diff resolves the ticket cleanly within scoped files.\n",
      (
          "### 2. Verification\n"
          "Red tests failed before implementation and green boundary tests pass.\n"
          f"{extra_pillar_body}"
      ),
      "### 3. Architecture\nThe library module separates validation from CLI wrappers.\n",
      "### 4. Blast Radius\nSibling targets remain untouched with zero caller breaks.\n",
      "### 5. Reviewer Defense\nSimple English limits hold across updated manuals.\n",
      "### 6. Immune Defenses & Crash Antibodies\nSafe byte copy avoids metadata EPERM.\n",
      f"{verdict_line}\n",
  ]
  return "\n".join(sections)


class TestReviewRoundParsing(unittest.TestCase):
  """Pure in-memory unit tests for parse_round_number."""

  def test_accepts_positive_ascii_integers(self) -> None:
    """Valid positive integer strings and ints parse to their exact int value."""
    for raw, expected in (
        ("1", 1),
        ("2", 2),
        ("12", 12),
        (" 7 ", 7),
        (1, 1),
        (9, 9),
    ):
      with self.subTest(raw=raw):
        self.assertEqual(parse_round_number(raw), expected)

  def test_rejects_invalid_round_values(self) -> None:
    """Zero, negative, non-ASCII, traversal, empty, and boolean values fail."""
    invalid_values = (
        "",
        "   ",
        "0",
        "01",
        "-1",
        "+1",
        "abc",
        "../1",
        "1/2",
        "1.5",
        "\u0662",  # Arabic-Indic digit two
        "\uff12",  # Fullwidth digit two
        0,
        -3,
        True,
        False,
        None,
    )
    for raw in invalid_values:
      with self.subTest(raw=raw):
        with self.assertRaises(ValueError):
          parse_round_number(raw)


class TestCriticReviewValidation(unittest.TestCase):
  """Pure in-memory unit tests for validate_critic_review_text."""

  def test_accepts_valid_approved_and_rejected_reviews(self) -> None:
    """Valid approved and rejected reviews pass with parsed verdict and counts."""
    approved = validate_critic_review_text(_build_valid_review())
    self.assertIsInstance(approved, ReviewValidationResult)
    self.assertTrue(approved.valid, approved.errors)
    self.assertEqual(approved.verdict, "APPROVED")
    self.assertEqual(approved.blocker_count, 0)
    self.assertEqual(approved.errors, ())

    for line, expected_count in (
        ("VERDICT: REJECTED (1 BLOCKER)", 1),
        ("VERDICT: REJECTED (1 BLOCKERS)", 1),
        ("VERDICT: REJECTED (3 BLOCKERS)", 3),
    ):
      with self.subTest(verdict_line=line):
        rejected = validate_critic_review_text(_build_valid_review(line))
        self.assertTrue(rejected.valid, rejected.errors)
        self.assertEqual(rejected.verdict, "REJECTED")
        self.assertEqual(rejected.blocker_count, expected_count)
        self.assertEqual(rejected.errors, ())

  def test_ignores_fake_headings_verdicts_and_soft_tokens_inside_fenced_blocks(
      self,
  ) -> None:
    """Quoted diffs inside backtick or tilde code fences never break validation."""
    fenced_snippet = (
        "Tests verify rejection of `REQUEST_CHANGES` and `NEEDS_WORK` verdicts.\n"
        "```markdown\n"
        "### 1. Intent\n"
        "VERDICT: REJECTED (9 BLOCKERS)\n"
        "VERDICT: REQUEST_CHANGES\n"
        "```\n"
        "~~~text\n"
        "### 4. Blast Radius\n"
        "NEEDS_WORK\n"
        "~~~\n"
    )
    res = validate_critic_review_text(
        _build_valid_review(extra_pillar_body=fenced_snippet)
    )
    self.assertTrue(res.valid, res.errors)
    self.assertEqual(res.verdict, "APPROVED")
    self.assertEqual(res.blocker_count, 0)

  def test_rejects_empty_text(self) -> None:
    """Empty or whitespace-only documents fail validation cleanly."""
    for content in ("", "   \n\t\n"):
      res = validate_critic_review_text(content)
      self.assertFalse(res.valid)
      self.assertEqual(res.verdict, "")
      self.assertIsNone(res.blocker_count)
      self.assertTrue(res.errors)

  def test_rejects_each_missing_canonical_heading(self) -> None:
    """Omitting any of the six canonical headings produces a deterministic error."""
    base = _build_valid_review()
    for heading in CANONICAL_SIX_PILLAR_HEADINGS:
      with self.subTest(missing_heading=heading):
        mutated = base.replace(heading, "### X. Unrelated Heading")
        res = validate_critic_review_text(mutated)
        self.assertFalse(res.valid)
        self.assertTrue(
            any(heading in err for err in res.errors),
            f"Expected missing heading '{heading}' in {res.errors}",
        )

  def test_rejects_out_of_order_headings(self) -> None:
    """Swapping two canonical headings fails exact order validation."""
    base = _build_valid_review()
    swapped = base.replace("### 3. Architecture", "TEMP_H3").replace(
        "### 4. Blast Radius", "### 3. Architecture"
    ).replace("TEMP_H3", "### 4. Blast Radius")
    res = validate_critic_review_text(swapped)
    self.assertFalse(res.valid)
    self.assertTrue(any("order" in err.lower() for err in res.errors), res.errors)

  def test_rejects_duplicate_heading(self) -> None:
    """Duplicating a canonical heading fails validation."""
    dup = _build_valid_review(
        extra_pillar_body="\n### 2. Verification\nDuplicate pillar prose.\n"
    )
    res = validate_critic_review_text(dup)
    self.assertFalse(res.valid)
    self.assertTrue(
        any("duplicate" in err.lower() for err in res.errors), res.errors
    )

  def test_rejects_empty_or_stub_pillar_bodies(self) -> None:
    """Headings with empty, rule-only, or fence-only bodies fail validation."""
    base = _build_valid_review()
    stub_cases = (
        ("empty", "### 4. Blast Radius\nSibling targets remain untouched with zero caller breaks.", "### 4. Blast Radius\n"),
        ("rule_only", "### 4. Blast Radius\nSibling targets remain untouched with zero caller breaks.", "### 4. Blast Radius\n---\n"),
        (
            "fence_only",
            "### 4. Blast Radius\nSibling targets remain untouched with zero caller breaks.",
            "### 4. Blast Radius\n```text\nonly code\n```\n",
        ),
    )
    for label, old, new in stub_cases:
      with self.subTest(case=label):
        res = validate_critic_review_text(base.replace(old, new))
        self.assertFalse(res.valid)
        self.assertTrue(
            any("### 4. Blast Radius" in err for err in res.errors), res.errors
        )

  def test_rejects_missing_contradictory_multiple_and_soft_verdicts(self) -> None:
    """Missing, contradictory, duplicate, and prohibited soft verdicts are rejected."""
    invalid_verdicts = (
        ("missing", ""),
        ("approved_nonzero", "VERDICT: APPROVED (1 BLOCKERS)"),
        ("rejected_zero", "VERDICT: REJECTED (0 BLOCKERS)"),
        (
            "multiple_conflicting",
            "VERDICT: APPROVED (0 BLOCKERS)\nVERDICT: REJECTED (1 BLOCKERS)",
        ),
        ("soft_request_changes", "VERDICT: REQUEST_CHANGES"),
        ("soft_needs_work", "VERDICT: NEEDS_WORK (2 BLOCKERS)"),
        ("standalone_soft", "REQUEST_CHANGES"),
        ("standalone_needs_work", "NEEDS_WORK"),
        ("lowercase_verdict", "verdict: approved (0 blockers)"),
    )
    for label, verdict_block in invalid_verdicts:
      with self.subTest(case=label):
        res = validate_critic_review_text(_build_valid_review(verdict_block))
        self.assertFalse(res.valid, f"Expected failure for {label}: {res}")
        self.assertTrue(res.errors)

  def test_accepts_trailing_fenced_verdict_blocks(self) -> None:
    """Trailing verdict fences (plain, text, yaml, yml) after Pillar 6 pass (GH-221)."""
    approved_cases = (
        ("plain", "```\nVERDICT: APPROVED (0 BLOCKERS)\n```"),
        ("text", "```text\nVERDICT: APPROVED (0 BLOCKERS)\n```"),
        ("yaml", "```yaml\nVERDICT: APPROVED (0 BLOCKERS)\n```"),
        ("yml", "```yml\nVERDICT: APPROVED (0 BLOCKERS)\n```"),
        ("tilde_text", "~~~text\nVERDICT: APPROVED (0 BLOCKERS)\n~~~"),
        (
            "text_with_trailing_nbo",
            (
                "```text\n"
                "VERDICT: APPROVED (0 BLOCKERS)\n"
                "```\n\n"
                "### Non-Blocking Observations (SCAR-PROC-108)\n\n"
                "| # | Observation | Location | Scope Relationship | Recommendation |\n"
                "| :--- | :--- | :--- | :--- | :--- |\n"
                "| 1 | Sample note | `lib/a.py:1` | `Somewhat related` | `Skip` |\n"
                "```python\n"
                "# Example snippet in NBO section\n"
                "x = 1\n"
                "```\n"
            ),
        ),
    )
    for label, verdict_block in approved_cases:
      with self.subTest(case=label):
        res = validate_critic_review_text(_build_valid_review(verdict_block))
        self.assertTrue(res.valid, f"Expected valid for {label}: {res.errors}")
        self.assertEqual(res.verdict, "APPROVED")
        self.assertEqual(res.blocker_count, 0)

    rejected_fenced = (
        "```text\n"
        "VERDICT: REJECTED (2 BLOCKERS)\n"
        "BLOCKERS:\n"
        "  - ID: BLOCKER-1\n"
        "    PILLAR: Architecture\n"
        "    LOCATION: lib/review_promotion.py:128\n"
        "    WOUND: Fenced verdict rejected.\n"
        "    TRAP: Discarded all fences.\n"
        "    DIAGNOSTIC_INVARIANT: Accept trailing verdict fence.\n"
        "  - ID: BLOCKER-2\n"
        "    PILLAR: Architecture\n"
        "    LOCATION: lib/review_promotion.py:164\n"
        "    WOUND: Subheading broke prose scan.\n"
        "    TRAP: Matched h4-h6 as section boundary.\n"
        "    DIAGNOSTIC_INVARIANT: Skip h4-h6 subheadings.\n"
        "```\n"
    )
    res_rej = validate_critic_review_text(_build_valid_review(rejected_fenced))
    self.assertTrue(res_rej.valid, res_rej.errors)
    self.assertEqual(res_rej.verdict, "REJECTED")
    self.assertEqual(res_rej.blocker_count, 2)

    # Non-verdict prose fences in Pillar 1-6 (including Pillar 6 quoting VERDICT)
    # remain ignored when paired with a valid trailing fenced verdict.
    base = _build_valid_review("```text\nVERDICT: APPROVED (0 BLOCKERS)\n```")
    pillar6_with_quote = base.replace(
        "### 6. Immune Defenses & Crash Antibodies\nSafe byte copy avoids metadata EPERM.",
        (
            "### 6. Immune Defenses & Crash Antibodies\n"
            "Safe byte copy avoids metadata EPERM.\n"
            "```markdown\n"
            "### 1. Intent\n"
            "VERDICT: REJECTED (9 BLOCKERS)\n"
            "```\n"
            "```text\n"
            "### 1. Intent\n"
            "VERDICT: REJECTED (4 BLOCKERS)\n"
            "```\n"
        ),
    )
    res_quote = validate_critic_review_text(pillar6_with_quote)
    self.assertTrue(res_quote.valid, res_quote.errors)
    self.assertEqual(res_quote.verdict, "APPROVED")
    self.assertEqual(res_quote.blocker_count, 0)

  def test_rejects_invalid_duplicate_and_mixed_fenced_verdict_blocks(
      self,
  ) -> None:
    """Soft tokens, contradictory counts, duplicates, and wrong info strings fail."""
    invalid_fenced = (
        ("fenced_soft_request_changes", "```text\nVERDICT: REQUEST_CHANGES\n```"),
        ("fenced_soft_needs_work", "```yaml\nVERDICT: NEEDS_WORK (1 BLOCKER)\n```"),
        ("fenced_standalone_soft", "```text\nREQUEST_CHANGES\n```"),
        ("fenced_standalone_needs_work", "```\nNEEDS_WORK\n```"),
        ("fenced_approved_nonzero", "```text\nVERDICT: APPROVED (1 BLOCKERS)\n```"),
        ("fenced_rejected_zero", "```text\nVERDICT: REJECTED (0 BLOCKERS)\n```"),
        (
            "fenced_duplicate_in_block",
            "```text\nVERDICT: APPROVED (0 BLOCKERS)\nVERDICT: REJECTED (1 BLOCKER)\n```",
        ),
        (
            "two_fenced_blocks",
            "```text\nVERDICT: APPROVED (0 BLOCKERS)\n```\n\n```yaml\nVERDICT: APPROVED (0 BLOCKERS)\n```",
        ),
        (
            "mixed_unfenced_and_fenced",
            "VERDICT: APPROVED (0 BLOCKERS)\n\n```text\nVERDICT: APPROVED (0 BLOCKERS)\n```",
        ),
        (
            "unsupported_info_python",
            "```python\nVERDICT: APPROVED (0 BLOCKERS)\n```",
        ),
        (
            "unsupported_info_markdown",
            "```markdown\nVERDICT: APPROVED (0 BLOCKERS)\n```",
        ),
    )
    for label, verdict_block in invalid_fenced:
      with self.subTest(case=label):
        res = validate_critic_review_text(_build_valid_review(verdict_block))
        self.assertFalse(res.valid, f"Expected failure for {label}: {res}")
        self.assertTrue(res.errors)

    # Fenced verdict appearing BEFORE Pillar 6 (inside Pillar 2) does not satisfy
    # the mandatory trailing verdict requirement.
    fenced_in_p2_only = _build_valid_review(
        verdict_line="",
        extra_pillar_body="```text\nVERDICT: APPROVED (0 BLOCKERS)\n```\n",
    )
    res_p2 = validate_critic_review_text(fenced_in_p2_only)
    self.assertFalse(res_p2.valid)
    self.assertTrue(
        any("missing mandatory binary verdict" in e.lower() for e in res_p2.errors),
        res_p2.errors,
    )

  def test_rejects_empty_pillar_six_before_fenced_verdict_and_nbo_table(
      self,
  ) -> None:
    """Trailing NBO tables after a fenced verdict cannot satisfy an empty Pillar 6."""
    nbo_table = (
        "| # | Observation | Location | Scope Relationship | Recommendation |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| 1 | Minor cleanup | `lib/x.py:1` | `Somewhat related` | `Skip` |\n"
    )
    for label, trailing in (
        ("bare_table_after_fence", f"```text\nVERDICT: APPROVED (0 BLOCKERS)\n```\n\n{nbo_table}"),
        (
            "headed_table_after_fence",
            f"```text\nVERDICT: APPROVED (0 BLOCKERS)\n```\n\n### Non-Blocking Observations\n{nbo_table}",
        ),
    ):
      with self.subTest(case=label):
        base = _build_valid_review(trailing)
        empty_p6 = base.replace(
            "### 6. Immune Defenses & Crash Antibodies\nSafe byte copy avoids metadata EPERM.",
            "### 6. Immune Defenses & Crash Antibodies\n",
        )
        res = validate_critic_review_text(empty_p6)
        self.assertFalse(res.valid, f"Expected empty Pillar 6 failure for {label}: {res}")
        self.assertTrue(
            any("### 6. Immune Defenses & Crash Antibodies" in e for e in res.errors),
            res.errors,
        )

  def test_accepts_deeper_subheadings_inside_pillars_with_prose(self) -> None:
    """Level 4-6 subheadings under a canonical level-3 pillar pass when followed by prose."""
    base = _build_valid_review("```text\nVERDICT: APPROVED (0 BLOCKERS)\n```")
    with_subheadings = base.replace(
        "### 1. Intent\nThe diff resolves the ticket cleanly within scoped files.",
        (
            "### 1. Intent\n"
            "#### Scope Alignment\n"
            "The diff resolves the ticket cleanly within scoped files.\n"
        ),
    ).replace(
        "### 6. Immune Defenses & Crash Antibodies\nSafe byte copy avoids metadata EPERM.",
        (
            "### 6. Immune Defenses & Crash Antibodies\n"
            "#### Crash Boundaries\n"
            "##### Filesystem Promotion\n"
            "Safe byte copy avoids metadata EPERM.\n"
        ),
    )
    res = validate_critic_review_text(with_subheadings)
    self.assertTrue(res.valid, res.errors)
    self.assertEqual(res.verdict, "APPROVED")
    self.assertEqual(res.blocker_count, 0)

  def test_rejects_subheading_only_pillar_stubs(self) -> None:
    """Level 4-6 subheadings without prose still fail empty-pillar validation."""
    base = _build_valid_review("```text\nVERDICT: APPROVED (0 BLOCKERS)\n```")
    stub_cases = (
        (
            "h4_only_in_p1",
            "### 1. Intent",
            "### 1. Intent\nThe diff resolves the ticket cleanly within scoped files.",
            "### 1. Intent\n#### Scope Alignment\n",
        ),
        (
            "h4_and_h5_with_fence_only",
            "### 3. Architecture",
            "### 3. Architecture\nThe library module separates validation from CLI wrappers.",
            "### 3. Architecture\n#### Layering\n##### Notes\n```text\nonly code\n```\n",
        ),
        (
            "h4_only_in_p6_before_fenced_verdict_and_nbo",
            "### 6. Immune Defenses & Crash Antibodies",
            "### 6. Immune Defenses & Crash Antibodies\nSafe byte copy avoids metadata EPERM.",
            "### 6. Immune Defenses & Crash Antibodies\n#### Runtime Safety\n",
        ),
    )
    for label, expected_heading, old, new in stub_cases:
      with self.subTest(case=label):
        doc = base.replace(old, new)
        if label == "h4_only_in_p6_before_fenced_verdict_and_nbo":
          doc += "\n| # | Note |\n| 1 | Trailing NBO row |\n"
        res = validate_critic_review_text(doc)
        self.assertFalse(res.valid, f"Expected failure for {label}: {res}")
        self.assertTrue(
            any(expected_heading in e for e in res.errors),
            f"Expected '{expected_heading}' error in {res.errors}",
        )

  def test_inline_triple_backtick_span_does_not_open_code_fence(self) -> None:
    """CommonMark 4.5: backticks inside a backtick info string do not open a fence."""
    inline_span_prose = (
        "```text``` fences wrap trailing verdicts cleanly without swallowing sections.\n"
    )
    res = validate_critic_review_text(
        _build_valid_review(extra_pillar_body=inline_span_prose)
    )
    self.assertTrue(res.valid, res.errors)
    self.assertEqual(res.verdict, "APPROVED")
    self.assertEqual(res.blocker_count, 0)


class TestPromoteReviewRound(unittest.TestCase):
  """Hermetic filesystem tests for promote_review_round and SCAR-SYS-05 resilience."""

  def test_promotes_valid_round_byte_for_byte_with_eperm_resilience(self) -> None:
    """Promotion copies exact bytes via safe_copy_file even if chmod/utime raise EPERM."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      state_dir = os.path.join(tmp_dir, ".workflow", "state")
      os.makedirs(state_dir)
      round_path = os.path.join(state_dir, "40_critic_review_round1.md")
      expected_text = _build_valid_review()
      with open(round_path, "w", encoding="utf-8") as f:
        f.write(expected_text)

      eperm = PermissionError(errno.EPERM, "Operation not permitted")
      with (
          mock.patch("os.chmod", side_effect=eperm),
          mock.patch("os.utime", side_effect=eperm),
      ):
        outcome = promote_review_round(tmp_dir, "1")

      self.assertTrue(outcome.promoted)
      self.assertEqual(outcome.round_number, 1)
      self.assertEqual(outcome.validation.verdict, "APPROVED")
      self.assertEqual(outcome.validation.blocker_count, 0)
      canon_path = os.path.join(state_dir, "40_critic_review.md")
      self.assertTrue(os.path.isfile(canon_path))
      with open(canon_path, "rb") as src_f, open(round_path, "rb") as dst_f:
        self.assertEqual(src_f.read(), dst_f.read())

  def test_missing_round_file_raises_file_not_found(self) -> None:
    """Missing round file raises FileNotFoundError and does not create canonical file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      state_dir = os.path.join(tmp_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with self.assertRaises(FileNotFoundError):
        promote_review_round(tmp_dir, "2")
      self.assertFalse(
          os.path.exists(os.path.join(state_dir, "40_critic_review.md"))
      )

  def test_invalid_round_preserves_existing_canonical_review(self) -> None:
    """Validation failure leaves pre-existing 40_critic_review.md untouched."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      state_dir = os.path.join(tmp_dir, ".workflow", "state")
      os.makedirs(state_dir)
      canon_path = os.path.join(state_dir, "40_critic_review.md")
      original_canon = _build_valid_review("VERDICT: REJECTED (1 BLOCKER)")
      with open(canon_path, "w", encoding="utf-8") as f:
        f.write(original_canon)

      bad_round_path = os.path.join(state_dir, "40_critic_review_round2.md")
      with open(bad_round_path, "w", encoding="utf-8") as f:
        f.write("### 1. Intent\nIncomplete review.\nVERDICT: REQUEST_CHANGES\n")

      outcome = promote_review_round(tmp_dir, "2")
      self.assertFalse(outcome.promoted)
      self.assertFalse(outcome.validation.valid)
      with open(canon_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), original_canon)


class TestWorkflowGatesPromoteReviewCli(unittest.TestCase):
  """In-memory BaseScript CLI tests for WorkflowGatesScript promote-review."""

  def test_cli_promotes_valid_round_text_and_json(self) -> None:
    """promote-review exits 0 for text and --json modes on valid round cards."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      state_dir = os.path.join(tmp_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "40_critic_review_round1.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write(_build_valid_review())

      out_txt, err_txt = io.StringIO(), io.StringIO()
      rc_txt = WorkflowGatesScript().execute(
          args=["--repo-root", tmp_dir, "promote-review", "--round", "1"],
          stdout=out_txt,
          stderr=err_txt,
      )
      self.assertEqual(rc_txt, 0, err_txt.getvalue())
      self.assertIn("40_critic_review.md", out_txt.getvalue())
      self.assertEqual(err_txt.getvalue(), "")

      out_json, err_json = io.StringIO(), io.StringIO()
      rc_json = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmp_dir,
              "promote-review",
              "--round",
              "1",
              "--json",
          ],
          stdout=out_json,
          stderr=err_json,
      )
      self.assertEqual(rc_json, 0, err_json.getvalue())
      payload = json.loads(out_json.getvalue())
      self.assertTrue(payload["promoted"])
      self.assertEqual(payload["round_number"], 1)
      self.assertEqual(payload["verdict"], "APPROVED")
      self.assertEqual(payload["blocker_count"], 0)

  def test_cli_returns_vector4_reflex_exit_two_on_errors(self) -> None:
    """Invalid round, missing file, and invalid content emit Vector 4 Reflex exit 2."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      state_dir = os.path.join(tmp_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "40_critic_review_round3.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("### 1. Intent\nBroken review\nVERDICT: NEEDS_WORK\n")

      cases = (
          ("bad_round", "", "INVALID_REVIEW_ROUND"),
          ("traversal_round", "../1", "INVALID_REVIEW_ROUND"),
          ("missing_file", "2", "MISSING_REVIEW_ROUND_FILE"),
          ("invalid_review", "3", "INVALID_CRITIC_REVIEW"),
      )
      for label, round_arg, expected_code in cases:
        for as_json in (False, True):
          with self.subTest(case=label, as_json=as_json):
            args = [
                "--repo-root",
                tmp_dir,
                "promote-review",
                "--round",
                round_arg,
            ]
            if as_json:
              args.append("--json")
            out, err = io.StringIO(), io.StringIO()
            rc = WorkflowGatesScript().execute(args=args, stdout=out, stderr=err)
            self.assertEqual(rc, 2)
            self.assertEqual(out.getvalue(), "")
            if as_json:
              err_obj = json.loads(err.getvalue())
              self.assertEqual(err_obj["violation_code"], expected_code)
              self.assertEqual(err_obj["exit_code"], 2)
              self.assertTrue(err_obj["socratic_probe"])
            else:
              self.assertIn(expected_code, err.getvalue())
              self.assertIn("SOCRATIC PROBE:", err.getvalue())

  def test_cli_returns_vector4_reflex_exit_two_on_os_error(self) -> None:
    """Mocked OSError during safe_copy_file emits REVIEW_PROMOTION_IO_ERROR exit 2."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      state_dir = os.path.join(tmp_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "40_critic_review_round1.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write(_build_valid_review())

      io_err = PermissionError(errno.EACCES, "Permission denied")
      for as_json in (False, True):
        with self.subTest(as_json=as_json):
          args = [
              "--repo-root",
              tmp_dir,
              "promote-review",
              "--round",
              "1",
          ]
          if as_json:
            args.append("--json")
          out, err = io.StringIO(), io.StringIO()
          with mock.patch(
              "lib.review_promotion.safe_copy_file", side_effect=io_err
          ):
            rc = WorkflowGatesScript().execute(
                args=args, stdout=out, stderr=err
            )
          self.assertEqual(rc, 2)
          self.assertEqual(out.getvalue(), "")
          if as_json:
            err_obj = json.loads(err.getvalue())
            self.assertEqual(
                err_obj["violation_code"], "REVIEW_PROMOTION_IO_ERROR"
            )
            self.assertEqual(err_obj["scar_id"], "SCAR-SYS-05")
            self.assertEqual(err_obj["exit_code"], 2)
            self.assertTrue(err_obj["socratic_probe"])
          else:
            self.assertIn("REVIEW_PROMOTION_IO_ERROR", err.getvalue())
            self.assertIn("[SCAR-ID: SYS-05]", err.getvalue())
            self.assertIn("SOCRATIC PROBE:", err.getvalue())


class TestPromoteReviewMakefileBoundary(unittest.TestCase):
  """Real GNU make boundary tests for .workflow/state/Makefile promote-review."""

  def test_generate_state_makefile_declares_unexported_round_and_phony_target(
      self,
  ) -> None:
    """generate_state_makefile declares unexported ROUND ?= and PHONY promote-review."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      mk_text = generate_state_makefile(tmp_dir)
      self.assertIn("ROUND ?=", mk_text)
      self.assertIn("promote-review", mk_text)
      self.assertIn('promote-review --round "$(ROUND)"', mk_text)
      for line in mk_text.splitlines():
        if line.startswith("export "):
          self.assertNotIn("ROUND", line.split())

  def test_gnu_make_promote_review_promotes_valid_and_rejects_invalid(
      self,
  ) -> None:
    """Real GNU make promotes valid round 1 and rejects missing ROUND or invalid content."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      out, err = io.StringIO(), io.StringIO()
      init_rc = WorkflowGatesScript().execute(
          args=["--repo-root", tmp_dir, "init-makefile"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(init_rc, 0, err.getvalue())

      state_dir = os.path.join(tmp_dir, ".workflow", "state")
      mk_path = os.path.join(state_dir, "Makefile")
      round1_path = os.path.join(state_dir, "40_critic_review_round1.md")
      round2_path = os.path.join(state_dir, "40_critic_review_round2.md")
      canon_path = os.path.join(state_dir, "40_critic_review.md")

      valid_text = _build_valid_review()
      with open(round1_path, "w", encoding="utf-8") as f:
        f.write(valid_text)
      with open(round2_path, "w", encoding="utf-8") as f:
        f.write("### 1. Intent\nStub only.\nVERDICT: APPROVED (1 BLOCKERS)\n")

      # 1. Valid promotion via real GNU make
      ok_proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              mk_path,
              "promote-review",
              "ROUND=1",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmp_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(ok_proc.returncode, 0, ok_proc.stderr)
      with open(canon_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), valid_text)

      # 2. Missing ROUND fails with Vector 4 Reflex
      missing_round_proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              mk_path,
              "promote-review",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmp_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertNotEqual(missing_round_proc.returncode, 0)
      self.assertIn("INVALID_REVIEW_ROUND", missing_round_proc.stderr)

      # 3. Invalid content fails with Vector 4 Reflex and preserves round 1 canonical file
      bad_proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              mk_path,
              "promote-review",
              "ROUND=2",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmp_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertNotEqual(bad_proc.returncode, 0)
      self.assertIn("INVALID_CRITIC_REVIEW", bad_proc.stderr)
      with open(canon_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), valid_text)


if __name__ == "__main__":
  unittest.main()
