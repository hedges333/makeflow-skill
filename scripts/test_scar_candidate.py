#!/usr/bin/env python3
"""Persistent test utility to validate synthetic scar candidate JSON files (SCAR-ARCH-05, SCAR-SH-01).

Validates synthetic scar candidate JSON files against Simple English rules, sentence
word count constraints, byte-for-byte LaTeX and code span preservation, and schema.

Usage:
    python3 scripts/test_scar_candidate.py <candidate_file.json> [--db-path <path>] [--json]
"""

import json
import os
import re
import sqlite3
import string
import sys
from typing import Any, Dict, List, NoReturn, Optional, TextIO

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)

from base_script import BaseScript
from emit_reflex import SilentArgumentParser
from sync_synthetic_scars import (
    extract_protected_spans,
    resolve_scars_db_path,
    _strip_protected_and_inline_code,
)

_PUNCTUATION_CHARS = string.punctuation + "«»“”‘’"


class ScarCandidateCliError(Exception):
  """Raised on invalid CLI arguments or invocations without calling sys.exit()."""

  exit_code = 2


class CandidateArgumentParser(SilentArgumentParser):
  """ArgumentParser subclass that raises ScarCandidateCliError with exit_code = 2."""

  def error(self, message: str) -> NoReturn:
    raise ScarCandidateCliError(message)

  def exit(self, status: int = 0, message: Optional[str] = None) -> NoReturn:
    raise ScarCandidateCliError(
        message or f"Argument parser exited with status {status}"
    )


def mask_code_and_math_spans(text: str) -> str:
  """Masks fenced code, inline code, display math, and inline math with atomic tokens.

  Prevents punctuation and periods inside code or math from splitting sentences.
  """
  content = text or ""
  masked = re.sub(r"```[\s\S]*?```", " __FENCED_CODE__ ", content)
  masked = re.sub(r"`[^`\n]+`", " __INLINE_CODE__ ", masked)
  masked = re.sub(r"\$\$[\s\S]+?\$\$", " __DISPLAY_MATH__ ", masked)
  masked = re.sub(
      r"(?<!\$)\$(?!\$)([^\$\n]+?)(?<!\$)\$(?!\$)", " __INLINE_MATH__ ", masked
  )
  return masked


def split_into_sentences(text: str) -> List[str]:
  """Tokenizes text into sentences on [.!?] followed by whitespace or end of string.

  Protects code and math spans before tokenization so periods/punctuation inside
  code and math do not split sentences prematurely.
  """
  if not text:
    return []
  masked = mask_code_and_math_spans(text)
  raw_sentences = re.split(r"(?<=[.!?])(?:\s+|$)", masked)
  return [s.strip() for s in raw_sentences if s.strip()]


def count_words_in_sentence(sentence: str) -> int:
  """Counts words in a sentence by splitting on whitespace and stripping punctuation."""
  words: List[str] = []
  for token in sentence.split():
    cleaned = token.strip(_PUNCTUATION_CHARS)
    if cleaned:
      words.append(cleaned)
  return len(words)


class ScarCandidateValidator:
  """Validates synthetic scar candidate fields against schema, spans, and Simple English rules."""

  def validate(
      self,
      candidate: Dict[str, Any],
      raw_data: Optional[Dict[str, Any]] = None,
  ) -> List[str]:
    """Validates candidate fields and returns list of actionable diagnostic error strings."""
    errors: List[str] = []

    scar_id = str(candidate.get("id") or "").strip()
    if not scar_id:
      errors.append("Candidate missing or empty 'id'.")
      scar_id = "<unknown>"

    # 1. Schema & Required non-empty string fields
    for req_field in ("wound", "trap", "reflex", "socratic_probe"):
      val = candidate.get(req_field)
      if not isinstance(val, str) or not val.strip():
        errors.append(
            f"Scar '{scar_id}' field '{req_field}' must be a non-empty string."
        )

    # 2. Circuit Breaker validation
    raw_source = raw_data if raw_data is not None else candidate
    raw_cb_raw = raw_source.get("raw_circuit_breaker")
    raw_cb_str = str(raw_cb_raw or "").strip() if raw_cb_raw is not None else ""
    trans_cb = candidate.get("circuit_breaker")

    if not raw_cb_str:
      # Raw circuit breaker is empty: transformed must be "" (not None or non-empty string)
      if trans_cb is None or trans_cb != "":
        errors.append(
            f"Scar '{scar_id}' field 'circuit_breaker' must be '' (not None or non-empty) when raw circuit breaker is empty."
        )
    else:
      # Raw circuit breaker is non-empty: transformed must be non-empty string
      if not isinstance(trans_cb, str) or not trans_cb.strip():
        errors.append(
            f"Scar '{scar_id}' field 'circuit_breaker' must be non-empty when raw circuit breaker is non-empty."
        )

    # 3. Span Preservation across code and math blocks
    field_pairs = (
        ("raw_wound", "wound"),
        ("raw_trap", "trap"),
        ("raw_circuit_breaker", "circuit_breaker"),
        ("raw_reflex", "reflex"),
    )
    for raw_key, trans_key in field_pairs:
      raw_val = raw_source.get(raw_key)
      raw_text = str(raw_val or "") if raw_val is not None else ""
      trans_val = candidate.get(trans_key)
      trans_text = str(trans_val or "") if trans_val is not None else ""

      raw_spans = extract_protected_spans(raw_text)
      trans_spans = extract_protected_spans(trans_text)

      for span_kind in ("fenced_code", "display_math", "inline_math"):
        if raw_spans[span_kind] != trans_spans[span_kind]:
          errors.append(
              f"Scar '{scar_id}' field '{trans_key}' {span_kind} mismatch: "
              f"expected {raw_spans[span_kind]}, got {trans_spans[span_kind]}."
          )

    # 4. Prose Punctuation Restrictions (zero semicolons or em-dashes outside code/math)
    for prose_field in (
        "wound",
        "trap",
        "circuit_breaker",
        "reflex",
        "socratic_probe",
    ):
      val_str = str(candidate.get(prose_field) or "")
      prose_only = _strip_protected_and_inline_code(val_str)
      if ";" in prose_only:
        errors.append(
            f"Scar '{scar_id}' field '{prose_field}' contains forbidden semicolon ';' in prose."
        )
      if "\u2014" in prose_only:
        errors.append(
            f"Scar '{scar_id}' field '{prose_field}' contains forbidden em-dash '—' in prose."
        )

    # 5. Simple English Sentence Word Count Limits
    # Descriptive sentences (wound, trap): <= 25 words per sentence
    for desc_field in ("wound", "trap"):
      desc_val = candidate.get(desc_field)
      if isinstance(desc_val, str) and desc_val.strip():
        for sentence in split_into_sentences(desc_val):
          wcount = count_words_in_sentence(sentence)
          if wcount > 25:
            errors.append(
                f"Scar '{scar_id}' field '{desc_field}' sentence exceeds word limit "
                f"({wcount} > 25 words): '{sentence}'."
            )

    # Procedural sentences (circuit_breaker, reflex): <= 20 words per sentence
    for proc_field in ("circuit_breaker", "reflex"):
      proc_val = candidate.get(proc_field)
      if isinstance(proc_val, str) and proc_val.strip():
        for sentence in split_into_sentences(proc_val):
          wcount = count_words_in_sentence(sentence)
          if wcount > 20:
            errors.append(
                f"Scar '{scar_id}' field '{proc_field}' sentence exceeds word limit "
                f"({wcount} > 20 words): '{sentence}'."
            )

    # Socratic probe questions: <= 20 words per question sentence, ending in ?, up to 3 questions
    probe_val = candidate.get("socratic_probe")
    if isinstance(probe_val, str) and probe_val.strip():
      probe_str = probe_val.strip()
      if not probe_str.endswith("?"):
        errors.append(
            f"Scar '{scar_id}' field 'socratic_probe' must end with '?'."
        )
      sentences = split_into_sentences(probe_str)
      if len(sentences) > 3:
        errors.append(
            f"Scar '{scar_id}' field 'socratic_probe' has {len(sentences)} question sentences "
            f"(maximum is 3)."
        )
      for idx, sentence in enumerate(sentences, start=1):
        if not sentence.endswith("?"):
          errors.append(
              f"Scar '{scar_id}' field 'socratic_probe' sentence {idx} must end with '?': '{sentence}'."
          )
        wcount = count_words_in_sentence(sentence)
        if wcount > 20:
          errors.append(
              f"Scar '{scar_id}' field 'socratic_probe' sentence {idx} exceeds word limit "
              f"({wcount} > 20 words): '{sentence}'."
          )

    return errors


class ScarCandidateTestScript(BaseScript):
  """CLI test script validating synthetic scar candidate JSON files (SCAR-ARCH-05)."""

  def __init__(self) -> None:
    super().__init__()
    self.candidate_file: str = ""
    self.db_path: Optional[str] = None
    self.json_output: bool = False
    self.validator = ScarCandidateValidator()

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = CandidateArgumentParser(
        prog="test_scar_candidate.py",
        description="Validate synthetic scar candidate JSON files against Simple English rules and spans.",
    )
    parser.add_argument(
        "candidate_file",
        help="Path to synthetic scar candidate JSON file.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional path to SQLite scars registry database for raw field fallback.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output validation results as machine-readable JSON.",
    )

    parsed = parser.parse_args(self.raw_args)
    self.candidate_file = parsed.candidate_file
    self.db_path = parsed.db_path
    self.json_output = parsed.json_output

  def _lookup_raw_fields_from_db(
      self, scar_id: str, db_path: str
  ) -> Optional[Dict[str, Any]]:
    """Looks up raw fields from SQLite database if file exists."""
    if not os.path.isfile(db_path):
      return None

    conn = None
    try:
      conn = sqlite3.connect(db_path)
      cur = conn.cursor()
      cur.execute(
          """
          SELECT raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
                 wound, trap, circuit_breaker, reflex
          FROM scars
          WHERE id = ?
          """,
          (scar_id,),
      )
      row = cur.fetchone()
      if not row:
        return None
      return {
          "raw_wound": row[0] if row[0] is not None else (row[4] or ""),
          "raw_trap": row[1] if row[1] is not None else (row[5] or ""),
          "raw_circuit_breaker": (
              row[2] if row[2] is not None else (row[6] or "")
          ),
          "raw_reflex": row[3] if row[3] is not None else (row[7] or ""),
      }
    except sqlite3.Error:
      return None
    finally:
      if conn:
        conn.close()

  def run(self) -> int:
    if not self.candidate_file or not os.path.isfile(self.candidate_file):
      self.stderr.write(
          f"Error: Candidate file not found: '{self.candidate_file}'.\n"
      )
      return 2

    try:
      with open(self.candidate_file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    except (OSError, ValueError) as exc:
      self.stderr.write(
          f"Error: Failed to parse JSON in '{self.candidate_file}': {exc}\n"
      )
      return 2

    items = payload if isinstance(payload, list) else [payload]
    if not items or not all(isinstance(x, dict) for x in items):
      self.stderr.write(
          f"Error: Candidate JSON in '{self.candidate_file}' must be an object or list of objects.\n"
      )
      return 2

    resolved_db = resolve_scars_db_path(db_path=self.db_path)
    all_errors: List[str] = []
    validated_ids: List[str] = []

    for item in items:
      scar_id = str(item.get("id") or "").strip()
      if not scar_id:
        all_errors.append("Candidate item is missing 'id'.")
        continue

      validated_ids.append(scar_id)

      # Check if raw fields are embedded in candidate JSON
      has_embedded_raw = any(
          k in item
          for k in (
              "raw_wound",
              "raw_trap",
              "raw_circuit_breaker",
              "raw_reflex",
          )
      )

      raw_data: Optional[Dict[str, Any]] = None
      if has_embedded_raw:
        raw_data = item
      else:
        # Fallback to SQLite database lookup
        raw_data = self._lookup_raw_fields_from_db(scar_id, resolved_db)
        if raw_data is None:
          all_errors.append(
              f"Scar '{scar_id}' not found in database '{resolved_db}' and no raw fields provided in candidate JSON."
          )
          continue

      errors = self.validator.validate(item, raw_data=raw_data)
      all_errors.extend(errors)

    if self.json_output:
      result = {
          "valid": len(all_errors) == 0,
          "scar_ids": validated_ids,
          "errors": all_errors,
      }
      self.stdout.write(json.dumps(result, indent=2) + "\n")
      return 0 if len(all_errors) == 0 else 1

    if all_errors:
      self.stderr.write(
          f"Validation failed with {len(all_errors)} error(s) for candidate '{self.candidate_file}':\n"
      )
      for err in all_errors:
        self.stderr.write(f"  ✖ {err}\n")
      return 1

    if len(validated_ids) == 1:
      self.stdout.write(
          f"✔ Scar candidate '{validated_ids[0]}' passed all validations.\n"
      )
    else:
      self.stdout.write(
          f"✔ Verified {len(validated_ids)} candidate scar(s) with 0 errors.\n"
      )
    return 0


if __name__ == "__main__":
  sys.exit(ScarCandidateTestScript().execute())
