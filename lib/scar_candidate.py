"""Validation library for synthetic scar prose and Socratic probes (GH-146, GH-209).

Validates synthetic scar fields against Simple English rules, contraction bans,
sentence word count limits, standalone display math and fenced code sentence
boundaries, byte-for-byte LaTeX and code span preservation, and Socratic probe
rules.
"""

import re
import string
from typing import Any, Dict, List, Optional

from lib.scars_db import (
    _strip_protected_and_inline_code,
    extract_protected_spans,
)

_PUNCTUATION_CHARS = string.punctuation + "«»“”‘’"

_STANDALONE_MASKED_BLOCK_RE = re.compile(
    r"(?:^|\r?\n)[ \t]*(?:__FENCED_CODE__|__DISPLAY_MATH__)[ \t]*(?=\r?\n|$)"
)

_CONTRACTION_RE = re.compile(
    r"\b(?:don't|doesn't|didn't|won't|can't|cannot't|isn't|aren't|wasn't|weren't|"
    r"haven't|hasn't|hadn't|wouldn't|shouldn't|couldn't|mustn't|"
    r"i'm|you're|we're|they're|i've|you've|we've|they've|"
    r"i'll|you'll|we'll|they'll|i'd|you'd|we'd|they'd|"
    r"it's|that's|there's|here's|what's|who's|let's)\b",
    re.IGNORECASE,
)


def mask_code_and_math_spans(text: str) -> str:
  """Mask fenced code, inline code, display math, and inline math with atomic tokens.

  Prevents punctuation and periods inside code or math from splitting sentences.

  Args:
    text: Input prose string containing optional code or math spans.

  Returns:
    Masked string where code and math spans are replaced by placeholder tokens.
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
  """Tokenize text into sentences across punctuation and standalone math/code blocks.

  Protects inline code, inline math, inline display math, and inline fenced code
  before tokenization. Splits sentences across standalone newline-delimited
  ``$$...$$`` display math blocks and standalone fenced code blocks so lead-in
  lines ending in ``:`` or ``.`` are measured separately from following prose.

  Args:
    text: Input prose string.

  Returns:
    Ordered list of non-empty masked sentence strings.
  """
  if not text:
    return []
  masked = mask_code_and_math_spans(text)
  segments = _STANDALONE_MASKED_BLOCK_RE.split(masked)
  sentences: List[str] = []
  for segment in segments:
    stripped_seg = segment.strip()
    if not stripped_seg:
      continue
    for raw_sentence in re.split(r"(?<=[.!?])(?:\s+|$)", stripped_seg):
      cleaned = raw_sentence.strip()
      if cleaned:
        sentences.append(cleaned)
  return sentences


def count_words_in_sentence(sentence: str) -> int:
  """Count words in a sentence by splitting on whitespace and stripping punctuation.

  Args:
    sentence: Single masked sentence string.

  Returns:
    Number of non-empty word tokens.
  """
  words: List[str] = []
  for token in sentence.split():
    cleaned = token.strip(_PUNCTUATION_CHARS)
    if cleaned:
      words.append(cleaned)
  return len(words)


def _check_socratic_probe_questions(probe_str: str, scar_id: str) -> List[str]:
  """Validate Socratic probe ending, question count, and 20-word limit."""
  errors: List[str] = []
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


def validate_socratic_probe(
    probe: Any, scar_id: str = "<probe>"
) -> List[str]:
  """Validate a standalone ``socratic_probe`` against Simple English probe rules.

  Args:
    probe: Candidate ``socratic_probe`` value.
    scar_id: Optional scar identifier for diagnostic messages.

  Returns:
    List of validation error strings (empty when valid).
  """
  if not isinstance(probe, str) or not probe.strip():
    return [
        f"Scar '{scar_id}' field 'socratic_probe' must be a non-empty string."
    ]

  probe_str = probe.strip()
  errors: List[str] = []
  prose_only = _strip_protected_and_inline_code(probe_str)
  if ";" in prose_only:
    errors.append(
        f"Scar '{scar_id}' field 'socratic_probe' contains forbidden semicolon ';' in prose."
    )
  if "\u2014" in prose_only:
    errors.append(
        f"Scar '{scar_id}' field 'socratic_probe' contains forbidden em-dash '\u2014' in prose."
    )
  if _CONTRACTION_RE.search(prose_only):
    errors.append(
        f"Scar '{scar_id}' field 'socratic_probe' contains forbidden contraction in prose."
    )
  errors.extend(_check_socratic_probe_questions(probe_str, scar_id))
  return errors


class ScarCandidateValidator:
  """Validate synthetic scar prose against schema, spans, and Simple English rules."""

  def validate(
      self,
      candidate: Dict[str, Any],
      raw_data: Optional[Dict[str, Any]] = None,
  ) -> List[str]:
    """Validate candidate fields and return actionable diagnostic error strings.

    Args:
      candidate: Candidate dictionary containing transformed scar fields.
      raw_data: Optional dictionary supplying ``raw_*`` reference fields.

    Returns:
      List of diagnostic error strings (empty when candidate is valid).
    """
    errors: List[str] = []

    scar_id = str(candidate.get("id") or "").strip()
    if not scar_id:
      errors.append("Candidate missing or empty 'id'.")
      scar_id = "<unknown>"

    for req_field in ("wound", "trap", "reflex"):
      val = candidate.get(req_field)
      if not isinstance(val, str) or not val.strip():
        errors.append(
            f"Scar '{scar_id}' field '{req_field}' must be a non-empty string."
        )

    raw_source = raw_data if raw_data is not None else candidate
    raw_cb_raw = raw_source.get("raw_circuit_breaker")
    raw_cb_str = str(raw_cb_raw or "").strip() if raw_cb_raw is not None else ""
    trans_cb = candidate.get("circuit_breaker")

    if not raw_cb_str:
      if "circuit_breaker" not in candidate:
        trans_cb = ""
      if trans_cb is None or trans_cb != "":
        errors.append(
            f"Scar '{scar_id}' field 'circuit_breaker' must be '' (not None or non-empty) when raw circuit breaker is empty."
        )
    else:
      if not isinstance(trans_cb, str) or not trans_cb.strip():
        errors.append(
            f"Scar '{scar_id}' field 'circuit_breaker' must be non-empty when raw circuit breaker is non-empty."
        )

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
            f"Scar '{scar_id}' field '{prose_field}' contains forbidden em-dash '\u2014' in prose."
        )
      if _CONTRACTION_RE.search(prose_only):
        errors.append(
            f"Scar '{scar_id}' field '{prose_field}' contains forbidden contraction in prose."
        )

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

    probe_val = candidate.get("socratic_probe")
    if isinstance(probe_val, str) and probe_val.strip():
      errors.extend(
          _check_socratic_probe_questions(probe_val.strip(), scar_id)
      )

    return errors
