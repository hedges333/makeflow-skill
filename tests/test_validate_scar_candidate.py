#!/usr/bin/env python3
"""Hermetic unit tests for scripts/validate_scar_candidate.py.

Validates synthetic scar candidate JSON files against Simple English rules,
sentence word count limits, span preservation, schema, and BaseScript execution.
Zero socket or external daemon dependencies (SCAR-TEST-08, SCAR-TEST-11).
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
  if _p not in sys.path:
    sys.path.insert(0, _p)

from lib.base_script import BaseScript
from lib.scar_candidate import (
    ScarCandidateTestScript,
    ScarCandidateValidator,
    ValidateScarCandidateScript,
    count_words_in_sentence,
    split_into_sentences,
)


def _make_valid_candidate(scar_id: str = "SCAR-PROC-99") -> dict:
  return {
      "id": scar_id,
      "raw_wound": "Raw wound with $a + b = c$ and `$ENV_VAR`.",
      "raw_trap": "Raw trap with $$E = mc^2$$ equation.",
      "raw_circuit_breaker": "Stop if `x` fails.",
      "raw_reflex": "Run ```python\nx = 1; y = 2\n``` in test.",
      "wound": "Simple wound with $a + b = c$ and `$ENV_VAR`.",
      "trap": "Simple trap with $$E = mc^2$$ equation.",
      "circuit_breaker": "If `x` fails, stop the build.",
      "reflex": "Run ```python\nx = 1; y = 2\n``` in the test.",
      "socratic_probe": "Did you verify $a + b = c$ before running the test?",
  }


class TestScarCandidateValidator(unittest.TestCase):
  """Unit tests for ScarCandidateValidator."""

  def setUp(self):
    self.validator = ScarCandidateValidator()

  def test_schema_required_fields(self):
    """Test that id, wound, trap, reflex must be non-empty strings while socratic_probe is optional."""
    valid = _make_valid_candidate()
    errors = self.validator.validate(valid)
    self.assertEqual(errors, [])

    # Missing or empty 'id'
    bad_id = dict(valid, id="")
    errs = self.validator.validate(bad_id)
    self.assertTrue(any("id" in e for e in errs), f"Expected 'id' error in {errs}")

    no_id = dict(valid)
    del no_id["id"]
    errs = self.validator.validate(no_id)
    self.assertTrue(any("id" in e for e in errs), f"Expected 'id' error in {errs}")

    # Missing or empty required fields
    for field in ("wound", "trap", "reflex"):
      bad_field = dict(valid, **{field: ""})
      errs = self.validator.validate(bad_field)
      self.assertTrue(
          any(field in e for e in errs),
          f"Expected '{field}' error for empty string in {errs}",
      )

      missing_field = dict(valid)
      del missing_field[field]
      errs = self.validator.validate(missing_field)
      self.assertTrue(
          any(field in e for e in errs),
          f"Expected '{field}' error for missing field in {errs}",
      )

    # socratic_probe is optional (None, empty, or omitted passes validation)
    self.assertEqual(self.validator.validate(dict(valid, socratic_probe=None)), [])
    self.assertEqual(self.validator.validate(dict(valid, socratic_probe="")), [])
    no_probe = dict(valid)
    del no_probe["socratic_probe"]
    self.assertEqual(self.validator.validate(no_probe), [])

  def test_circuit_breaker_empty_and_non_empty(self):
    """Test circuit breaker validation against raw circuit breaker."""
    # When raw circuit breaker is empty, circuit_breaker must be "" (not None or non-empty)
    no_cb_candidate = _make_valid_candidate("SCAR-NOCB-01")
    no_cb_candidate["raw_circuit_breaker"] = ""
    no_cb_candidate["circuit_breaker"] = ""
    errs = self.validator.validate(no_cb_candidate)
    self.assertEqual(errs, [])

    # None when raw is empty -> rejected
    bad_none = dict(no_cb_candidate, circuit_breaker=None)
    errs = self.validator.validate(bad_none)
    self.assertTrue(
        any("circuit_breaker" in e for e in errs),
        f"Expected circuit_breaker error in {errs}",
    )

    # Non-empty when raw is empty -> rejected
    bad_extra = dict(no_cb_candidate, circuit_breaker="Stop now.")
    errs = self.validator.validate(bad_extra)
    self.assertTrue(
        any("circuit_breaker" in e for e in errs),
        f"Expected circuit_breaker error in {errs}",
    )

    # When raw circuit breaker is non-empty, circuit_breaker must be non-empty
    has_cb = _make_valid_candidate("SCAR-HASCB-01")
    has_cb["raw_circuit_breaker"] = "Raw stop rule."
    has_cb["circuit_breaker"] = ""
    errs = self.validator.validate(has_cb)
    self.assertTrue(
        any("circuit_breaker" in e for e in errs),
        f"Expected circuit_breaker error in {errs}",
    )

    has_cb["circuit_breaker"] = None
    errs = self.validator.validate(has_cb)
    self.assertTrue(
        any("circuit_breaker" in e for e in errs),
        f"Expected circuit_breaker error in {errs}",
    )

  def test_omitted_circuit_breaker_defaults_to_empty_when_raw_empty(self):
    """Test that omitting circuit_breaker key is accepted when raw_circuit_breaker is empty."""
    no_cb_candidate = _make_valid_candidate("SCAR-NOCB-02")
    no_cb_candidate["raw_circuit_breaker"] = ""
    del no_cb_candidate["circuit_breaker"]
    del no_cb_candidate["socratic_probe"]
    errs = self.validator.validate(no_cb_candidate)
    self.assertEqual(errs, [])

  def test_span_preservation(self):
    """Test byte-for-byte span preservation across math and code blocks."""
    base = _make_valid_candidate()

    # Drop inline math -> error
    bad_inline = dict(base, wound="Simple wound without formula.")
    errs = self.validator.validate(bad_inline)
    self.assertTrue(
        any("inline_math mismatch" in e for e in errs),
        f"Expected inline_math mismatch in {errs}",
    )

    # Modify display math -> error
    bad_display = dict(base, trap="Simple trap with $$E = mc^3$$ equation.")
    errs = self.validator.validate(bad_display)
    self.assertTrue(
        any("display_math mismatch" in e for e in errs),
        f"Expected display_math mismatch in {errs}",
    )

    # Modify fenced code block -> error
    bad_fenced = dict(
        base, reflex="Run ```python\nx = 99\n``` in the test."
    )
    errs = self.validator.validate(bad_fenced)
    self.assertTrue(
        any("fenced_code mismatch" in e for e in errs),
        f"Expected fenced_code mismatch in {errs}",
    )

  def test_prose_punctuation_restrictions(self):
    """Test zero semicolons and em-dashes outside protected code/math."""
    base = _make_valid_candidate()

    # Semicolon in prose -> rejected
    bad_semi = dict(base, wound="Simple wound; bad semicolon.")
    errs = self.validator.validate(bad_semi)
    self.assertTrue(
        any("semicolon" in e for e in errs),
        f"Expected semicolon error in {errs}",
    )

    # Em-dash in prose -> rejected
    bad_emdash = dict(base, trap="Simple trap — with em-dash.")
    errs = self.validator.validate(bad_emdash)
    self.assertTrue(
        any("em-dash" in e for e in errs),
        f"Expected em-dash error in {errs}",
    )

    # Semicolon inside code span or fenced code -> allowed!
    good_semi_code = dict(
        base,
        circuit_breaker="Run `var a = 1;` before proceeding.",
        raw_circuit_breaker="Run `var a = 1;` before proceeding.",
    )
    errs = self.validator.validate(good_semi_code)
    self.assertEqual(errs, [])

  def test_simple_english_sentence_word_count_limits(self):
    """Test sentence word count limits for descriptive, procedural, and probe fields."""
    base = _make_valid_candidate()

    # Descriptive limit: <= 25 words (wound, trap)
    # Exactly 25 words -> valid
    desc_25 = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty-one twenty-two twenty-three twenty-four twenty-five."
    base_25 = dict(base, wound=desc_25, raw_wound=desc_25)
    self.assertEqual(self.validator.validate(base_25), [])

    # 26 words -> invalid
    desc_26 = desc_25[:-1] + " twenty-six."
    base_26 = dict(base, wound=desc_26, raw_wound=desc_26)
    errs = self.validator.validate(base_26)
    self.assertTrue(
        any("wound" in e and "26 > 25" in e for e in errs),
        f"Expected 26 > 25 error in {errs}",
    )

    # Procedural limit: <= 20 words (circuit_breaker, reflex)
    # Exactly 20 words -> valid
    proc_20 = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty."
    base_proc_20 = dict(
        base,
        circuit_breaker=proc_20,
        raw_circuit_breaker=proc_20,
    )
    self.assertEqual(self.validator.validate(base_proc_20), [])

    # 21 words -> invalid
    proc_21 = proc_20[:-1] + " twenty-one."
    base_proc_21 = dict(
        base,
        circuit_breaker=proc_21,
        raw_circuit_breaker=proc_21,
    )
    errs = self.validator.validate(base_proc_21)
    self.assertTrue(
        any("circuit_breaker" in e and "21 > 20" in e for e in errs),
        f"Expected 21 > 20 error in {errs}",
    )

    # Socratic probe questions: <= 20 words per question sentence, ending in ?, up to 3 questions
    probe_2_questions = "Did you run the test? Did you check the code?"
    base_probe_2 = dict(base, socratic_probe=probe_2_questions)
    self.assertEqual(self.validator.validate(base_probe_2), [])

    probe_3_questions = "Did you check? Did you run? Did you log?"
    base_probe_3 = dict(base, socratic_probe=probe_3_questions)
    self.assertEqual(self.validator.validate(base_probe_3), [])

    # 4 questions -> invalid
    probe_4_questions = "Did you check? Did you run? Did you log? Did you review?"
    base_probe_4 = dict(base, socratic_probe=probe_4_questions)
    errs = self.validator.validate(base_probe_4)
    self.assertTrue(
        any("socratic_probe" in e and ("3" in e or "exceeds" in e) for e in errs),
        f"Expected question count error in {errs}",
    )

    # Probe question ending with . instead of ? -> invalid
    probe_statement = "Check the code before running. Did you test?"
    base_probe_stmt = dict(base, socratic_probe=probe_statement)
    errs = self.validator.validate(base_probe_stmt)
    self.assertTrue(
        any("socratic_probe" in e and "?" in e for e in errs),
        f"Expected probe question mark error in {errs}",
    )

    # Probe sentence exceeding 20 words -> invalid
    probe_21 = "Did you verify that every single component in the entire layout tree is completely and thoroughly validated before starting execution now?"
    base_probe_21 = dict(base, socratic_probe=probe_21)
    errs = self.validator.validate(base_probe_21)
    self.assertTrue(
        any("socratic_probe" in e and "21 > 20" in e for e in errs),
        f"Expected probe word count error in {errs}",
    )

  def test_code_and_math_spans_do_not_split_sentences(self):
    """Test that punctuation inside inline code or math does not split sentences."""
    base = _make_valid_candidate()
    reflex_code = "Run `import os.path` before continuing. Check `file.py` now."
    candidate = dict(
        base,
        reflex=reflex_code,
        raw_reflex=reflex_code,
    )
    errs = self.validator.validate(candidate)
    self.assertEqual(errs, [])

  def test_actionable_diagnostics_formatting(self):
    """Test that error messages clearly cite scar ID, field name, word counts, limits, or mismatched spans."""
    scar_id = "SCAR-DIAG-01"
    candidate = _make_valid_candidate(scar_id)
    candidate["wound"] = (
        "One two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty-one "
        "twenty-two twenty-three twenty-four twenty-five twenty-six."
    )
    candidate["raw_wound"] = candidate["wound"]
    candidate["reflex"] = "Run ```python\nx = 999\n``` in test."

    errs = self.validator.validate(candidate)
    self.assertTrue(len(errs) >= 2)

    # Check scar ID cited in errors
    for err in errs:
      self.assertIn(scar_id, err)

    # Check field name and word count cited
    wound_err = [e for e in errs if "wound" in e][0]
    self.assertIn("26 > 25", wound_err)

    # Check span mismatch cited
    reflex_err = [e for e in errs if "reflex" in e][0]
    self.assertIn("fenced_code mismatch", reflex_err)


class TestScarCandidateCLI(unittest.TestCase):
  """Unit tests for ScarCandidateTestScript CLI and BaseScript lifecycle."""

  def test_inherits_basescript(self):
    """Test that ScarCandidateTestScript inherits from BaseScript (SCAR-ARCH-05)."""
    self.assertTrue(issubclass(ScarCandidateTestScript, BaseScript))

  def test_valid_candidate_file_exits_0(self):
    """Test that valid candidate JSON file returns exit code 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
      cand_path = os.path.join(tmpdir, "candidate.json")
      with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(_make_valid_candidate(), f)

      out = io.StringIO()
      err = io.StringIO()
      script = ScarCandidateTestScript()
      code = script.execute([cand_path], stdout=out, stderr=err)
      self.assertEqual(code, 0)
      self.assertIn("passed", out.getvalue().lower())

  def test_validation_failure_exits_1(self):
    """Test that validation failures return exit code 1."""
    with tempfile.TemporaryDirectory() as tmpdir:
      cand_path = os.path.join(tmpdir, "invalid.json")
      bad = _make_valid_candidate()
      bad["wound"] = "Simple wound with forbidden semicolon; here."
      with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(bad, f)

      out = io.StringIO()
      err = io.StringIO()
      script = ScarCandidateTestScript()
      code = script.execute([cand_path], stdout=out, stderr=err)
      self.assertEqual(code, 1)
      output = out.getvalue() + err.getvalue()
      self.assertIn("semicolon", output)

  def test_file_not_found_exits_2(self):
    """Test that non-existent candidate file returns exit code 2."""
    out = io.StringIO()
    err = io.StringIO()
    script = ScarCandidateTestScript()
    code = script.execute(["/non/existent/candidate.json"], stdout=out, stderr=err)
    self.assertEqual(code, 2)
    self.assertIn("not found", err.getvalue().lower())

  def test_unparseable_json_exits_2(self):
    """Test that malformed JSON returns exit code 2."""
    with tempfile.TemporaryDirectory() as tmpdir:
      bad_json_path = os.path.join(tmpdir, "bad.json")
      with open(bad_json_path, "w", encoding="utf-8") as f:
        f.write("{invalid: json,")

      out = io.StringIO()
      err = io.StringIO()
      script = ScarCandidateTestScript()
      code = script.execute([bad_json_path], stdout=out, stderr=err)
      self.assertEqual(code, 2)
      self.assertIn("failed to parse json", err.getvalue().lower())

  def test_cli_argument_errors_exit_2(self):
    """Test that missing arguments or invalid flags return exit code 2."""
    out = io.StringIO()
    err = io.StringIO()
    script = ScarCandidateTestScript()
    # Missing candidate file argument
    code = script.execute([], stdout=out, stderr=err)
    self.assertEqual(code, 2)

    # Unknown flag
    code_bad_flag = script.execute(["--unknown-flag"], stdout=out, stderr=err)
    self.assertEqual(code_bad_flag, 2)

  def test_sqlite_fallback_resolution(self):
    """Test resolving raw fields from an isolated temporary SQLite database."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "test_scars.db")
      conn = sqlite3.connect(db_path)
      cur = conn.cursor()
      cur.execute(
          """
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              raw_wound TEXT,
              raw_trap TEXT,
              raw_circuit_breaker TEXT,
              raw_reflex TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT
          )
          """
      )
      cur.execute(
          """
          INSERT INTO scars (id, raw_wound, raw_trap, raw_circuit_breaker, raw_reflex)
          VALUES (?, ?, ?, ?, ?)
          """,
          (
              "SCAR-SQL-01",
              "Raw wound with formula $x = 1$.",
              "Raw trap text.",
              "Stop if error occurs.",
              "Run ```bash\nmake test\n``` now.",
          ),
      )
      conn.commit()
      conn.close()

      # Candidate JSON with only transformed fields (no raw_* fields embedded)
      cand_path = os.path.join(tmpdir, "candidate.json")
      candidate = {
          "id": "SCAR-SQL-01",
          "wound": "Transformed wound with formula $x = 1$.",
          "trap": "Transformed trap text.",
          "circuit_breaker": "Stop if error occurs.",
          "reflex": "Run ```bash\nmake test\n``` now.",
          "socratic_probe": "Did you run the test before making changes?",
      }
      with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(candidate, f)

      # Run with --db-path pointing to the temporary database
      out = io.StringIO()
      err = io.StringIO()
      script = ScarCandidateTestScript()
      code = script.execute(
          [cand_path, "--db-path", db_path], stdout=out, stderr=err
      )
      self.assertEqual(code, 0, f"Expected 0, got {code}. err: {err.getvalue()}")

      # If candidate alters a code/math span compared to DB -> validation error (exit code 1)
      candidate_altered = dict(
          candidate, wound="Transformed wound without formula."
      )
      with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(candidate_altered, f)

      out = io.StringIO()
      err = io.StringIO()
      code_altered = script.execute(
          [cand_path, "--db-path", db_path], stdout=out, stderr=err
      )
      self.assertEqual(code_altered, 1)
      self.assertIn("inline_math mismatch", out.getvalue() + err.getvalue())

      # If scar ID is not in DB -> validation error (exit code 1)
      candidate_missing_id = dict(candidate, id="SCAR-NONEXISTENT")
      with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(candidate_missing_id, f)

      out = io.StringIO()
      err = io.StringIO()
      code_missing = script.execute(
          [cand_path, "--db-path", db_path], stdout=out, stderr=err
      )
      self.assertEqual(code_missing, 1)
      self.assertIn("not found in database", out.getvalue() + err.getvalue())

  def test_json_output_mode(self):
    """Test --json flag emits machine-readable JSON on stdout."""
    with tempfile.TemporaryDirectory() as tmpdir:
      cand_path = os.path.join(tmpdir, "candidate.json")
      with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(_make_valid_candidate(), f)

      out = io.StringIO()
      err = io.StringIO()
      script = ScarCandidateTestScript()
      code = script.execute([cand_path, "--json"], stdout=out, stderr=err)
      self.assertEqual(code, 0)
      payload = json.loads(out.getvalue())
      self.assertTrue(payload.get("valid"))
      self.assertEqual(payload.get("errors"), [])

  def test_execution_time_budget(self):
    """Verify entire test candidate validation executes well under 100 ms (SCAR-TEST-11)."""
    validator = ScarCandidateValidator()
    candidate = _make_valid_candidate()

    start = time.perf_counter()
    for _ in range(10):
      validator.validate(candidate)
    elapsed = time.perf_counter() - start

    # 10 validations should comfortably finish within 100 ms (0.10s)
    self.assertLess(
        elapsed,
        0.10,
        f"Validation of 10 candidates took {elapsed:.4f}s (budget: 0.10s)",
    )


if __name__ == "__main__":
  unittest.main()
