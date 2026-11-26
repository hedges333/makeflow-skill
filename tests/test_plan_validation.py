"""Hermetic unit tests for canonical 20_plan.md validation and check-plan gate (GH-270).

Covers canonical 8-section Markdown parsing, fenced code block immunity
(SCAR-LOCAL-DATA-0001), Section 3 required subheadings, Section 2 Given-When-Then
acceptance criteria, Section 7 Markdown table validation, workflow_gates.py
check-plan Vector 4 Reflex output, make approved-plan prerequisite enforcement,
and state_machine.py T15 -> T20 fail-closed validation (SCAR-PROC-160).
"""

from dataclasses import FrozenInstanceError
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)


def make_canonical_plan_markdown() -> str:
  """Builds a valid canonical 8-section 20_plan.md document for tests.

  Returns:
    Valid canonical Markdown text satisfying SCAR-PROC-160.
  """
  return (
      "# Target 20 Implementation Plan: GH-270\n\n"
      "## 1. Problem Statement and Root Cause Summary\n\n"
      "Planning artifacts lacked a deterministic schema, causing cross-model drift.\n\n"
      "## 2. BDD Discovery and Acceptance Criteria\n\n"
      "### Scenario 1: Valid Plan Passes Gate\n"
      "- **Given** a workspace with a canonical eight-section plan\n"
      "- **When** the agent runs check-plan\n"
      "- **Then** validation returns exit code 0.\n\n"
      "## 3. Target Subprojects, Planned File Edits, and Layer Posture\n\n"
      "- **Target Subproject**: Root Python plugin.\n"
      "- **Layer Posture (`SCAR-ARCH-08`)**: Adapt to clean existing layout.\n\n"
      "### Specific Edits to Existing Files\n\n"
      "| File Path | Layer Role | Planned Specific Edits |\n"
      "| :--- | :--- | :--- |\n"
      "| `lib/state_machine.py` | Library module | Validate plan on T20. |\n\n"
      "### New Files to Create\n\n"
      "| File Path | Layer Role | Purpose |\n"
      "| :--- | :--- | :--- |\n"
      "| `lib/plan_validation.py` | Library module | Canonical plan parser. |\n\n"
      "### Untouched Files and Scope Quarantine\n\n"
      "- `install.sh` remains untouched.\n\n"
      "## 4. OO Design Patterns, Module Cohesion, and Documentation Standard\n\n"
      "- Frozen `PlanValidationResult` dataclass and Google-style docstrings.\n\n"
      "## 5. Testing Strategy, Commands, and Burden-Balance Analysis\n\n"
      "- Targeted command: `python3 -m unittest -v -f tests/test_plan_validation.py`.\n"
      "- Runs hermetically in under 100 ms using `tempfile.TemporaryDirectory()`.\n\n"
      "## 6. Red-Green-Refactor TDD Plan\n\n"
      "1. Red: Write failing tests first.\n"
      "2. Green: Implement minimal validator.\n"
      "3. Refactor: Run static checks and verify-regression.\n\n"
      "## 7. Socratic Invariant Audit Table\n\n"
      "| Invariant / Boundary | Probing Socratic Inquiry | Pre-Implementation Verification / Proof |\n"
      "| :--- | :--- | :--- |\n"
      "| **Fail-Fast Targeted Loops** | Which command runs? | `python3 -m unittest -v -f` |\n\n"
      "## 8. Consulted Reference Manuals and Target 30 Scar Carry-Forward\n\n"
      "- `skills/makeflow/references/02_archaeology_and_planning.md`\n"
      "- `SCAR-LOCAL-DATA-0001`: Track fenced code block state.\n"
  )


class TestPlanValidationModule(unittest.TestCase):
  """Direct unit tests for lib/plan_validation.py (GH-270, SCAR-PROC-160)."""

  def test_lib_plan_validation_has_no_shebang_and_frozen_result(self) -> None:
    """Verifies SCAR-ARCH-05 no-shebang rule and immutable PlanValidationResult."""
    from lib.plan_validation import (
        CANONICAL_PLAN_HEADINGS,
        REQUIRED_SECTION3_SUBHEADINGS,
        PlanValidationResult,
        validate_plan_markdown,
    )

    lib_path = os.path.join(REPO_ROOT, "lib", "plan_validation.py")
    with open(lib_path, "r", encoding="utf-8") as fh:
      self.assertFalse(fh.readline().startswith("#!"))

    self.assertEqual(len(CANONICAL_PLAN_HEADINGS), 8)
    self.assertEqual(len(REQUIRED_SECTION3_SUBHEADINGS), 3)

    res = validate_plan_markdown(make_canonical_plan_markdown())
    self.assertIsInstance(res, PlanValidationResult)
    self.assertTrue(res.valid, res.errors)
    self.assertEqual(res.errors, ())
    self.assertEqual(res.headings_found, CANONICAL_PLAN_HEADINGS)
    as_dict = res.to_dict()
    self.assertTrue(as_dict["valid"])
    self.assertEqual(as_dict["errors"], [])

    with self.assertRaises(FrozenInstanceError):
      res.valid = False  # type: ignore[misc]

  def test_rejects_empty_missing_duplicate_and_reordered_headings(self) -> None:
    """Verifies missing, duplicate, empty, and out-of-order canonical headings fail closed."""
    from lib.plan_validation import (
        CANONICAL_PLAN_HEADINGS,
        validate_plan_markdown,
    )

    for empty_text in ("", "   \n\t\n", "# Plan\n"):
      with self.subTest(case=repr(empty_text)):
        res = validate_plan_markdown(empty_text)
        self.assertFalse(res.valid)
        self.assertTrue(res.errors)

    base = make_canonical_plan_markdown()

    # Missing each required heading one by one
    for heading in CANONICAL_PLAN_HEADINGS:
      with self.subTest(missing=heading):
        mutated = base.replace(heading, "## X. Removed Heading")
        res = validate_plan_markdown(mutated)
        self.assertFalse(res.valid)
        self.assertTrue(
            any(heading in err for err in res.errors),
            f"Expected missing heading error for {heading!r}, got {res.errors}",
        )

    # Duplicate canonical heading
    dup = base + "\n## 1. Problem Statement and Root Cause Summary\n\nExtra.\n"
    dup_res = validate_plan_markdown(dup)
    self.assertFalse(dup_res.valid)
    self.assertTrue(any("Duplicate" in err for err in dup_res.errors), dup_res.errors)

    # Out-of-order headings (swap sections 1 and 2)
    sec1_marker = "## 1. Problem Statement and Root Cause Summary"
    sec2_marker = "## 2. BDD Discovery and Acceptance Criteria"
    sec3_marker = "## 3. Target Subprojects, Planned File Edits, and Layer Posture"
    pre, rest1 = base.split(sec1_marker, 1)
    sec1_body, rest2 = rest1.split(sec2_marker, 1)
    sec2_body, tail = rest2.split(sec3_marker, 1)
    reordered = pre + sec2_marker + sec2_body + sec1_marker + sec1_body + sec3_marker + tail
    reord_res = validate_plan_markdown(reordered)
    self.assertFalse(reord_res.valid)
    self.assertTrue(any("order" in err.lower() for err in reord_res.errors), reord_res.errors)

  def test_rejects_empty_section_body_and_missing_section3_subheadings(self) -> None:
    """Verifies empty section bodies and missing/empty Section 3 subheadings fail closed."""
    from lib.plan_validation import (
        REQUIRED_SECTION3_SUBHEADINGS,
        validate_plan_markdown,
    )

    base = make_canonical_plan_markdown()

    # Empty Section 4 body (only blank lines and horizontal rule)
    empty_sec4 = base.replace(
        "- Frozen `PlanValidationResult` dataclass and Google-style docstrings.",
        "---",
    )
    res4 = validate_plan_markdown(empty_sec4)
    self.assertFalse(res4.valid)
    self.assertTrue(
        any("4. OO Design Patterns" in err for err in res4.errors),
        res4.errors,
    )

    # Missing each required Section 3 subheading
    for sub in REQUIRED_SECTION3_SUBHEADINGS:
      with self.subTest(missing_sub=sub):
        mut = base.replace(sub, "### Other Subheading")
        res = validate_plan_markdown(mut)
        self.assertFalse(res.valid)
        self.assertTrue(
            any(sub in err for err in res.errors),
            f"Expected error for missing {sub!r}, got {res.errors}",
        )

    # Empty Section 3 subheading body
    empty_sub = base.replace("- `install.sh` remains untouched.", "   ")
    res_sub = validate_plan_markdown(empty_sub)
    self.assertFalse(res_sub.valid)
    self.assertTrue(
        any("Untouched Files" in err for err in res_sub.errors),
        res_sub.errors,
    )

    # Duplicate Section 3 required subheading fails closed
    dup_sub = base.replace(
        "### Untouched Files and Scope Quarantine\n\n- `install.sh` remains untouched.",
        (
            "### Untouched Files and Scope Quarantine\n\n"
            "- `install.sh` remains untouched.\n\n"
            "### Untouched Files and Scope Quarantine\n\n"
            "- Duplicate block."
        ),
    )
    res_dup_sub = validate_plan_markdown(dup_sub)
    self.assertFalse(res_dup_sub.valid)
    self.assertTrue(
        any("duplicate" in err.lower() and "Untouched Files" in err for err in res_dup_sub.errors),
        res_dup_sub.errors,
    )

  def test_section3_allows_nested_h4_subheadings_with_content_and_rejects_empty_h4(
      self,
  ) -> None:
    """GH-270 NBO-1: Section 3 allows #### subheadings with body content and rejects empty ####."""
    from lib.plan_validation import validate_plan_markdown

    base = make_canonical_plan_markdown()

    # Nested #### subheading with prose/table content beneath it MUST PASS
    with_h4_content = base.replace(
        "### Specific Edits to Existing Files\n\n"
        "| File Path | Layer Role | Planned Specific Edits |\n"
        "| :--- | :--- | :--- |\n"
        "| `lib/state_machine.py` | Library module | Validate plan on T20. |",
        (
            "### Specific Edits to Existing Files\n\n"
            "#### Library Layer\n\n"
            "| File Path | Layer Role | Planned Specific Edits |\n"
            "| :--- | :--- | :--- |\n"
            "| `lib/state_machine.py` | Library module | Validate plan on T20. |\n\n"
            "#### Test Layer\n\n"
            "- `tests/test_state_machine.py`: Update fixtures."
        ),
    )
    res_h4_ok = validate_plan_markdown(with_h4_content)
    self.assertTrue(res_h4_ok.valid, res_h4_ok.errors)

    # Nested #### subheading with NO body text beneath it MUST FAIL
    with_h4_empty = base.replace(
        "### Specific Edits to Existing Files\n\n"
        "| File Path | Layer Role | Planned Specific Edits |\n"
        "| :--- | :--- | :--- |\n"
        "| `lib/state_machine.py` | Library module | Validate plan on T20. |",
        "### Specific Edits to Existing Files\n\n#### Library Layer\n",
    )
    res_h4_empty = validate_plan_markdown(with_h4_empty)
    self.assertFalse(res_h4_empty.valid)
    self.assertTrue(
        any("Specific Edits to Existing Files" in err for err in res_h4_empty.errors),
        res_h4_empty.errors,
    )

  def test_requires_unfenced_bdd_given_when_then_and_section7_table(self) -> None:
    """Verifies Section 2 Given-When-Then and Section 7 Markdown table enforcement."""
    from lib.plan_validation import validate_plan_markdown

    base = make_canonical_plan_markdown()

    # Missing When clause in Section 2
    no_when = base.replace("- **When** the agent runs check-plan", "- Execute check-plan")
    res_when = validate_plan_markdown(no_when)
    self.assertFalse(res_when.valid)
    self.assertTrue(
        any("When" in err or "Given-When-Then" in err for err in res_when.errors),
        res_when.errors,
    )

    # Missing Markdown table in Section 7
    no_table = base.replace(
        "| Invariant / Boundary | Probing Socratic Inquiry | Pre-Implementation Verification / Proof |\n"
        "| :--- | :--- | :--- |\n"
        "| **Fail-Fast Targeted Loops** | Which command runs? | `python3 -m unittest -v -f` |",
        "- Checked all invariants in prose without a table.",
    )
    res_tbl = validate_plan_markdown(no_table)
    self.assertFalse(res_tbl.valid)
    self.assertTrue(
        any("table" in err.lower() for err in res_tbl.errors),
        res_tbl.errors,
    )

    # Header+separator-only Section 7 table without any data row MUST FAIL
    header_sep_only = base.replace(
        "| Invariant / Boundary | Probing Socratic Inquiry | Pre-Implementation Verification / Proof |\n"
        "| :--- | :--- | :--- |\n"
        "| **Fail-Fast Targeted Loops** | Which command runs? | `python3 -m unittest -v -f` |",
        "| Invariant / Boundary | Probing Socratic Inquiry | Pre-Implementation Verification / Proof |\n"
        "| :--- | :--- | :--- |",
    )
    res_hdr_only = validate_plan_markdown(header_sep_only)
    self.assertFalse(res_hdr_only.valid)
    self.assertTrue(
        any("table" in err.lower() for err in res_hdr_only.errors),
        res_hdr_only.errors,
    )

  def test_fenced_code_block_immunity_scar_local_data_0001(self) -> None:
    """SCAR-LOCAL-DATA-0001: ignores headings, subheadings, GWT, and tables inside fenced blocks."""
    from lib.plan_validation import validate_plan_markdown

    base = make_canonical_plan_markdown()

    # 1. Valid plan containing embedded fake headings inside triple-backtick and tilde fences MUST PASS
    with_fenced_examples = base.replace(
        "Planning artifacts lacked a deterministic schema, causing cross-model drift.",
        (
            "Planning artifacts lacked a deterministic schema, causing cross-model drift.\n\n"
            "```markdown\n"
            "## 1. Problem Statement and Root Cause Summary\n"
            "### Specific Edits to Existing Files\n"
            "---\n"
            "```\n\n"
            "~~~markdown\n"
            "## 8. Consulted Reference Manuals and Target 30 Scar Carry-Forward\n"
            "~~~"
        ),
    )
    res_ok = validate_plan_markdown(with_fenced_examples)
    self.assertTrue(res_ok.valid, f"Fenced example headings must not cause duplicates: {res_ok.errors}")

    # 2. Required heading placed ONLY inside a fenced code block MUST FAIL
    fenced_heading = base.replace(
        "## 6. Red-Green-Refactor TDD Plan",
        "```markdown\n## 6. Red-Green-Refactor TDD Plan\n```",
    )
    res_fh = validate_plan_markdown(fenced_heading)
    self.assertFalse(res_fh.valid)
    self.assertTrue(
        any("6. Red-Green-Refactor TDD Plan" in err for err in res_fh.errors),
        res_fh.errors,
    )

    # 3. Given-When-Then placed ONLY inside a tilde fenced block MUST FAIL
    fenced_gwt = base.replace(
        "- **Given** a workspace with a canonical eight-section plan\n"
        "- **When** the agent runs check-plan\n"
        "- **Then** validation returns exit code 0.",
        "Notes on discovery.\n\n~~~gherkin\nGiven a workspace\nWhen check-plan runs\nThen exit 0\n~~~",
    )
    res_fgwt = validate_plan_markdown(fenced_gwt)
    self.assertFalse(res_fgwt.valid)
    self.assertTrue(
        any("Given" in err or "BDD" in err for err in res_fgwt.errors),
        res_fgwt.errors,
    )

    # 4. Section 7 table placed ONLY inside a fenced code block MUST FAIL
    fenced_table = base.replace(
        "| Invariant / Boundary | Probing Socratic Inquiry | Pre-Implementation Verification / Proof |\n"
        "| :--- | :--- | :--- |\n"
        "| **Fail-Fast Targeted Loops** | Which command runs? | `python3 -m unittest -v -f` |",
        "Audit prose.\n\n```markdown\n| A | B | C |\n| :--- | :--- | :--- |\n| 1 | 2 | 3 |\n```",
    )
    res_ftbl = validate_plan_markdown(fenced_table)
    self.assertFalse(res_ftbl.valid)
    self.assertTrue(
        any("table" in err.lower() for err in res_ftbl.errors),
        res_ftbl.errors,
    )

    # 5. Unclosed code fence swallows subsequent required heading -> MUST FAIL
    unclosed_fence = base.replace(
        "## 8. Consulted Reference Manuals and Target 30 Scar Carry-Forward",
        "```markdown\n## 8. Consulted Reference Manuals and Target 30 Scar Carry-Forward",
    )
    res_unclosed = validate_plan_markdown(unclosed_fence)
    self.assertFalse(res_unclosed.valid)
    self.assertTrue(
        any("8. Consulted Reference Manuals" in err for err in res_unclosed.errors),
        res_unclosed.errors,
    )

    # 6. 4-backtick outer fence containing 3-backtick inner fence keeps inner duplicate heading fenced -> MUST PASS
    nested_four_tick = base.replace(
        "Planning artifacts lacked a deterministic schema, causing cross-model drift.",
        (
            "Planning artifacts lacked a deterministic schema, causing cross-model drift.\n\n"
            "````markdown\n"
            "```markdown\n"
            "## 1. Problem Statement and Root Cause Summary\n"
            "```\n"
            "## 2. BDD Discovery and Acceptance Criteria\n"
            "````"
        ),
    )
    res_nested = validate_plan_markdown(nested_four_tick)
    self.assertTrue(res_nested.valid, res_nested.errors)

    # 7. CommonMark 4.5: backtick info string containing another backtick is NOT a fence opener -> MUST PASS
    inline_backtick_info = base.replace(
        "Planning artifacts lacked a deterministic schema, causing cross-model drift.",
        (
            "Planning artifacts lacked a deterministic schema, causing cross-model drift.\n\n"
            "```inline`code``` remains ordinary prose."
        ),
    )
    res_inline = validate_plan_markdown(inline_backtick_info)
    self.assertTrue(res_inline.valid, res_inline.errors)

  def test_validate_plan_file_handles_missing_and_valid_files(self) -> None:
    """Verifies validate_plan_file on missing and valid temporary files."""
    from lib.plan_validation import validate_plan_file

    with tempfile.TemporaryDirectory() as tmpdir:
      missing_path = os.path.join(tmpdir, "20_plan.md")
      res_missing = validate_plan_file(missing_path)
      self.assertFalse(res_missing.valid)
      self.assertTrue(any("not found" in err.lower() for err in res_missing.errors))

      with open(missing_path, "w", encoding="utf-8") as fh:
        fh.write(make_canonical_plan_markdown())
      res_valid = validate_plan_file(missing_path)
      self.assertTrue(res_valid.valid, res_valid.errors)


class TestPlanValidationGatesAndStateMachine(unittest.TestCase):
  """Tests check-plan CLI, make approved-plan prerequisite, and state_machine T15 -> T20 gate."""

  def test_workflow_gates_check_plan_pass_and_reflex_fail(self) -> None:
    """Verifies WorkflowGatesScript check-plan returns 0 on valid plan and exit 2 reflex on invalid."""
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmpdir:
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir)
      plan_path = os.path.join(state_dir, "20_plan.md")

      # Invalid stub plan -> exit 2 with SCAR-PROC-160 INVALID_PLAN_SPEC
      with open(plan_path, "w", encoding="utf-8") as fh:
        fh.write("# Plan\n\nJust a stub.\n")
      out_fail, err_fail = io.StringIO(), io.StringIO()
      rc_fail = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "check-plan"],
          stdout=out_fail,
          stderr=err_fail,
      )
      self.assertEqual(rc_fail, 2)
      self.assertIn("[SCAR-ID: PROC-160]", err_fail.getvalue())
      self.assertIn("INVALID_PLAN_SPEC", err_fail.getvalue())

      # Valid canonical plan -> exit 0 and valid JSON payload with --json
      with open(plan_path, "w", encoding="utf-8") as fh:
        fh.write(make_canonical_plan_markdown())
      out_ok, err_ok = io.StringIO(), io.StringIO()
      rc_ok = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "check-plan", "--json"],
          stdout=out_ok,
          stderr=err_ok,
      )
      self.assertEqual(rc_ok, 0, err_ok.getvalue())
      parsed = json.loads(out_ok.getvalue())
      self.assertTrue(parsed["valid"])
      self.assertEqual(parsed["errors"], [])

  def test_state_machine_and_makefile_approved_plan_reject_invalid_plan(self) -> None:
    """Verifies both advance_workflow_target and make approved-plan fail closed on invalid plans."""
    from lib.state_machine import advance_workflow_target
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmpdir:
      subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True, capture_output=True)
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(os.path.join(state_dir, "15_archaeology_receipt.md"), "w", encoding="utf-8") as fh:
        fh.write("# Archaeology\n")
      with open(os.path.join(state_dir, "20_plan.md"), "w", encoding="utf-8") as fh:
        fh.write("# Plan\n")

      # Direct state_machine transition T15 -> T20 fails closed
      ok_sm, msg_sm = advance_workflow_target(
          "T15",
          "T20",
          evidence={"skipMainFetch": True},
          workspace_dir=tmpdir,
          state_dir=state_dir,
      )
      self.assertFalse(ok_sm)
      self.assertIn("SCAR-PROC-160", msg_sm)
      self.assertIn("INVALID_PLAN_SPEC", msg_sm)

      # Synthesize Makefile and run `make approved-plan` -> fails with exit 2 on stub plan
      rc_init = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(rc_init, 0)
      mk_path = os.path.join(state_dir, "Makefile")
      proc_bad = subprocess.run(
          ["make", "-s", "-f", mk_path, "approved-plan", "FROM=T15", "NO_FETCH=1", f"PYTHON={sys.executable}"],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc_bad.returncode, 2, proc_bad.stdout + proc_bad.stderr)
      self.assertIn("PROC-160", proc_bad.stderr)
      self.assertIn("INVALID_PLAN_SPEC", proc_bad.stderr)

      # Replace with valid canonical plan -> `make approved-plan` succeeds with exit 0
      with open(os.path.join(state_dir, "20_plan.md"), "w", encoding="utf-8") as fh:
        fh.write(make_canonical_plan_markdown())
      proc_ok = subprocess.run(
          ["make", "-s", "-f", mk_path, "approved-plan", "FROM=T15", "NO_FETCH=1", f"PYTHON={sys.executable}"],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc_ok.returncode, 0, proc_ok.stdout + proc_ok.stderr)


if __name__ == "__main__":
  unittest.main()
