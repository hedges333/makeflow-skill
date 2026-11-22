#!/usr/bin/env python3
"""Hermetic unit tests for lib/scar_candidate.py (GH-146, GH-209).

Validates synthetic scar prose against Simple English rules, contraction bans,
sentence word count limits, standalone math/code sentence splitting, byte-for-byte
span preservation, and Socratic probe rules. Zero socket or external daemon
dependencies (SCAR-TEST-08, SCAR-TEST-11).
"""

import os
import sys
import time
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

import lib.scar_candidate as scar_candidate_mod
from lib.scar_candidate import (
    _CONTRACTION_RE,
    ScarCandidateValidator,
    count_words_in_sentence,
    mask_code_and_math_spans,
    split_into_sentences,
    validate_socratic_probe,
)


def _make_valid_candidate(scar_id: str = "SCAR-PROC-99") -> dict:
  """Build a valid synthetic scar candidate dictionary for testing."""
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
  """Unit tests for ScarCandidateValidator and sentence tokenization helpers."""

  def setUp(self) -> None:
    self.validator = ScarCandidateValidator()

  def test_obsolete_cli_classes_removed(self) -> None:
    """Verify deleted CLI wrapper classes are absent from lib.scar_candidate."""
    for removed_name in (
        "ScarCandidateCliError",
        "CandidateArgumentParser",
        "ScarCandidateTestScript",
        "ValidateScarCandidateScript",
    ):
      self.assertFalse(
          hasattr(scar_candidate_mod, removed_name),
          f"Removed symbol '{removed_name}' should not exist in lib.scar_candidate",
      )
    self.assertIsNotNone(_CONTRACTION_RE)

  def test_schema_required_fields(self) -> None:
    """Verify id, wound, trap, reflex must be non-empty while socratic_probe is optional."""
    valid = _make_valid_candidate()
    self.assertEqual(self.validator.validate(valid), [])

    bad_id = dict(valid, id="")
    errs = self.validator.validate(bad_id)
    self.assertTrue(any("id" in e for e in errs), f"Expected 'id' error in {errs}")

    no_id = dict(valid)
    del no_id["id"]
    errs = self.validator.validate(no_id)
    self.assertTrue(any("id" in e for e in errs), f"Expected 'id' error in {errs}")

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

    self.assertEqual(self.validator.validate(dict(valid, socratic_probe=None)), [])
    self.assertEqual(self.validator.validate(dict(valid, socratic_probe="")), [])
    no_probe = dict(valid)
    del no_probe["socratic_probe"]
    self.assertEqual(self.validator.validate(no_probe), [])

  def test_circuit_breaker_empty_and_non_empty(self) -> None:
    """Verify circuit_breaker matches presence of raw_circuit_breaker."""
    no_cb_candidate = _make_valid_candidate("SCAR-NOCB-01")
    no_cb_candidate["raw_circuit_breaker"] = ""
    no_cb_candidate["circuit_breaker"] = ""
    self.assertEqual(self.validator.validate(no_cb_candidate), [])

    bad_none = dict(no_cb_candidate, circuit_breaker=None)
    errs = self.validator.validate(bad_none)
    self.assertTrue(
        any("circuit_breaker" in e for e in errs),
        f"Expected circuit_breaker error in {errs}",
    )

    bad_extra = dict(no_cb_candidate, circuit_breaker="Stop now.")
    errs = self.validator.validate(bad_extra)
    self.assertTrue(
        any("circuit_breaker" in e for e in errs),
        f"Expected circuit_breaker error in {errs}",
    )

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

  def test_omitted_circuit_breaker_defaults_to_empty_when_raw_empty(self) -> None:
    """Verify omitting circuit_breaker is accepted when raw_circuit_breaker is empty."""
    no_cb_candidate = _make_valid_candidate("SCAR-NOCB-02")
    no_cb_candidate["raw_circuit_breaker"] = ""
    del no_cb_candidate["circuit_breaker"]
    del no_cb_candidate["socratic_probe"]
    self.assertEqual(self.validator.validate(no_cb_candidate), [])

  def test_span_preservation(self) -> None:
    """Verify byte-for-byte span preservation across math and code blocks."""
    base = _make_valid_candidate()

    bad_inline = dict(base, wound="Simple wound without formula.")
    errs = self.validator.validate(bad_inline)
    self.assertTrue(
        any("inline_math mismatch" in e for e in errs),
        f"Expected inline_math mismatch in {errs}",
    )

    bad_display = dict(base, trap="Simple trap with $$E = mc^3$$ equation.")
    errs = self.validator.validate(bad_display)
    self.assertTrue(
        any("display_math mismatch" in e for e in errs),
        f"Expected display_math mismatch in {errs}",
    )

    bad_fenced = dict(
        base, reflex="Run ```python\nx = 99\n``` in the test."
    )
    errs = self.validator.validate(bad_fenced)
    self.assertTrue(
        any("fenced_code mismatch" in e for e in errs),
        f"Expected fenced_code mismatch in {errs}",
    )

  def test_prose_punctuation_and_contraction_restrictions(self) -> None:
    """Verify zero semicolons, em-dashes, and contractions outside code/math."""
    base = _make_valid_candidate()

    bad_semi = dict(base, wound="Simple wound; bad semicolon.")
    errs = self.validator.validate(bad_semi)
    self.assertTrue(
        any("semicolon" in e for e in errs),
        f"Expected semicolon error in {errs}",
    )

    bad_emdash = dict(base, trap="Simple trap \u2014 with em-dash.")
    errs = self.validator.validate(bad_emdash)
    self.assertTrue(
        any("em-dash" in e for e in errs),
        f"Expected em-dash error in {errs}",
    )

    # Contractions in prose across all prose fields must be rejected (GH-209)
    for field, bad_text in (
        ("wound", "The worker doesn't check $a + b = c$ or `$ENV_VAR`."),
        ("trap", "It's easy to misread $$E = mc^2$$ during review."),
        ("circuit_breaker", "Don't run `x` when inputs fail."),
        ("reflex", "We won't skip ```python\nx = 1; y = 2\n``` in tests."),
        ("socratic_probe", "Isn't $a + b = c$ verified before the test?"),
    ):
      with self.subTest(field=field):
        bad_contraction = dict(base, **{field: bad_text})
        errs = self.validator.validate(bad_contraction)
        self.assertTrue(
            any("contraction" in e.lower() and field in e for e in errs),
            f"Expected contraction error for '{field}' in {errs}",
        )

    # Semicolons and contractions inside inline code or fenced code are allowed
    good_code = dict(
        base,
        circuit_breaker="Run `don't_fail();` before proceeding.",
        raw_circuit_breaker="Run `don't_fail();` before proceeding.",
        reflex="Run ```python\nmsg = \"don't fail\"\nx = 1; y = 2\n``` in the test.",
        raw_reflex="Run ```python\nmsg = \"don't fail\"\nx = 1; y = 2\n``` in test.",
    )
    self.assertEqual(self.validator.validate(good_code), [])

  def test_simple_english_sentence_word_count_limits(self) -> None:
    """Verify word count limits for descriptive, procedural, and probe fields."""
    base = _make_valid_candidate()

    desc_25 = (
        "One two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty-one "
        "twenty-two twenty-three twenty-four twenty-five."
    )
    base_25 = dict(base, wound=desc_25, raw_wound=desc_25)
    self.assertEqual(self.validator.validate(base_25), [])

    desc_26 = desc_25[:-1] + " twenty-six."
    base_26 = dict(base, wound=desc_26, raw_wound=desc_26)
    errs = self.validator.validate(base_26)
    self.assertTrue(
        any("wound" in e and "26 > 25" in e for e in errs),
        f"Expected 26 > 25 error in {errs}",
    )

    proc_20 = (
        "One two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty."
    )
    base_proc_20 = dict(
        base,
        circuit_breaker=proc_20,
        raw_circuit_breaker=proc_20,
    )
    self.assertEqual(self.validator.validate(base_proc_20), [])

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

    probe_2 = "Did you run the test? Did you check the code?"
    self.assertEqual(
        self.validator.validate(dict(base, socratic_probe=probe_2)), []
    )
    probe_3 = "Did you check? Did you run? Did you log?"
    self.assertEqual(
        self.validator.validate(dict(base, socratic_probe=probe_3)), []
    )

    probe_4 = "Did you check? Did you run? Did you log? Did you review?"
    errs = self.validator.validate(dict(base, socratic_probe=probe_4))
    self.assertTrue(
        any("socratic_probe" in e and "3" in e for e in errs),
        f"Expected question count error in {errs}",
    )

    probe_stmt = "Check the code before running. Did you test?"
    errs = self.validator.validate(dict(base, socratic_probe=probe_stmt))
    self.assertTrue(
        any("socratic_probe" in e and "?" in e for e in errs),
        f"Expected probe question mark error in {errs}",
    )

    probe_21 = (
        "Did you verify that every single component in the entire layout tree "
        "is completely and thoroughly validated before starting execution now?"
    )
    errs = self.validator.validate(dict(base, socratic_probe=probe_21))
    self.assertTrue(
        any("socratic_probe" in e and "21 > 20" in e for e in errs),
        f"Expected probe word count error in {errs}",
    )

  def test_code_and_math_spans_do_not_split_sentences(self) -> None:
    """Verify inline code and inline display math do not split sentences prematurely."""
    base = _make_valid_candidate()
    reflex_code = "Run `import os.path` before continuing. Check `file.py` now."
    candidate = dict(base, reflex=reflex_code, raw_reflex=reflex_code)
    self.assertEqual(self.validator.validate(candidate), [])

    inline_math_sentences = split_into_sentences(
        "Simple trap with $$E = mc^2$$ equation."
    )
    self.assertEqual(len(inline_math_sentences), 1)
    self.assertIn("__DISPLAY_MATH__", inline_math_sentences[0])

    inline_code_sentences = split_into_sentences(
        "Run ```python\nx = 1; y = 2\n``` in the test."
    )
    self.assertEqual(len(inline_code_sentences), 1)
    self.assertIn("__FENCED_CODE__", inline_code_sentences[0])

  def test_split_into_sentences_splits_across_standalone_math_and_code_blocks(
      self,
  ) -> None:
    """Verify standalone newline-delimited $$...$$ and fenced code act as sentence boundaries (GH-209)."""
    math_text = (
        "Verify the invariant before committing:\n"
        "$$a + b = c$$\n"
        "Then run the targeted unit test suite."
    )
    math_sentences = split_into_sentences(math_text)
    self.assertEqual(
        math_sentences,
        [
            "Verify the invariant before committing:",
            "Then run the targeted unit test suite.",
        ],
    )

    code_text = (
        "Run this safe verification script:\n"
        "```python\n"
        "print('ok')\n"
        "```\n"
        "Check the exit status before returning."
    )
    code_sentences = split_into_sentences(code_text)
    self.assertEqual(
        code_sentences,
        [
            "Run this safe verification script:",
            "Check the exit status before returning.",
        ],
    )

    # Two 15-word procedural sentences separated by a standalone math block
    # must pass the 20-word procedural limit (GH-209)
    proc_before = (
        "Run the schema migration check before applying the upstream update "
        "to the local database:"
    )
    proc_after = (
        "Verify that every row in the registry table preserves its original "
        "identifier after updates."
    )
    self.assertLessEqual(count_words_in_sentence(proc_before), 20)
    self.assertLessEqual(count_words_in_sentence(proc_after), 20)

    reflex_with_math = f"{proc_before}\n$$N_{{\\text{{before}}}} = N_{{\\text{{after}}}}$$\n{proc_after}"
    candidate = _make_valid_candidate("SCAR-MATH-01")
    candidate["raw_reflex"] = reflex_with_math
    candidate["reflex"] = reflex_with_math
    self.assertEqual(self.validator.validate(candidate), [])

    reflex_with_code = f"{proc_before}\n```bash\nmake test\n```\n{proc_after}"
    candidate_code = _make_valid_candidate("SCAR-CODE-01")
    candidate_code["raw_reflex"] = reflex_with_code
    candidate_code["reflex"] = reflex_with_code
    self.assertEqual(self.validator.validate(candidate_code), [])

    # Verify mask_code_and_math_spans remains exported and intact
    masked = mask_code_and_math_spans("Check `x` and $y$.")
    self.assertIn("__INLINE_CODE__", masked)
    self.assertIn("__INLINE_MATH__", masked)

  def test_actionable_diagnostics_formatting(self) -> None:
    """Verify error messages cite scar ID, field name, limits, and mismatched spans."""
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
    self.assertGreaterEqual(len(errs), 2)
    for err in errs:
      self.assertIn(scar_id, err)

    wound_err = [e for e in errs if "wound" in e][0]
    self.assertIn("26 > 25", wound_err)

    reflex_err = [e for e in errs if "reflex" in e][0]
    self.assertIn("fenced_code mismatch", reflex_err)

  def test_validate_socratic_probe_helper(self) -> None:
    """Verify standalone validate_socratic_probe() rules."""
    self.assertEqual(
        validate_socratic_probe(
            "Did you inspect `$ENV_VAR` and $x = 1$ before editing?"
        ),
        [],
    )
    errs_empty = validate_socratic_probe("")
    self.assertTrue(any("socratic_probe" in e for e in errs_empty))

    errs_no_q = validate_socratic_probe("Check the schema first.")
    self.assertTrue(any("?" in e for e in errs_no_q))

    errs_contraction = validate_socratic_probe("Don't you test first?")
    self.assertTrue(any("contraction" in e.lower() for e in errs_contraction))

    errs_semi = validate_socratic_probe(
        "Did you check the formula; did it pass?"
    )
    self.assertTrue(any("semicolon" in e.lower() for e in errs_semi))

    errs_long = validate_socratic_probe(
        "Did you check every single generated helper file and migration table "
        "in the temporary directory before running the commit verification step?"
    )
    self.assertTrue(any("20" in e for e in errs_long))

    errs_many = validate_socratic_probe("One? Two? Three? Four?")
    self.assertTrue(any("maximum is 3" in e for e in errs_many))

  def test_execution_time_budget(self) -> None:
    """Verify 10 candidate validations finish well under 100 ms (SCAR-TEST-11)."""
    candidate = _make_valid_candidate()
    start = time.perf_counter()
    for _ in range(10):
      self.validator.validate(candidate)
    elapsed = time.perf_counter() - start
    self.assertLess(
        elapsed,
        0.10,
        f"Validation of 10 candidates took {elapsed:.4f}s (budget: 0.10s)",
    )


if __name__ == "__main__":
  unittest.main()
