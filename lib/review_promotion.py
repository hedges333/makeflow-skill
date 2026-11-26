"""Critic review validation and promotion library (GH-221, GH-281).

Prefers canonical JSON round reports (``40_critic_review_round<N>.json``),
validating via ``lib.state_reports``, promoting to
``.workflow/state/40_critic_review.json`` via ``safe_copy_file``, and
atomically rendering ``.workflow/state/40_critic_review.md``. When JSON is
absent, falls back to legacy Markdown validation (canonical Six-Pillar
headings, non-empty pillar prose allowing nested level 4-6 subheadings, and
binary verdict headers unfenced or inside trailing verdict-only code fences
after Pillar 6), promoting ``40_critic_review_round<N>.md`` to
``.workflow/state/40_critic_review.md`` via ``safe_copy_file`` and clearing
any stale canonical JSON (SCAR-SYS-05, SCAR-ARCH-05, SCAR-ARCH-07).
"""

from dataclasses import dataclass
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from lib.active_state import atomic_write_text
from lib.report_renderers import (
    CANONICAL_SIX_PILLAR_HEADINGS,
    render_critic_review_markdown,
)
from lib.safe_copy import safe_copy_file
from lib.state_reports import (
    CRITIC_REVIEW_JSON_FILENAME,
    CRITIC_REVIEW_MD_FILENAME,
    load_critic_review_validation,
)

_ROUND_ASCII_RE = re.compile(r"^[1-9][0-9]*$")
_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_VERDICT_FENCE_INFOS = frozenset({"", "text", "yaml", "yml"})
_SECTION_BOUNDARY_HEADING_RE = re.compile(r"^#{1,3}\s+\S")
_SUBHEADING_RE = re.compile(r"^#{4,6}\s+\S")
_HORIZONTAL_RULE_RE = re.compile(r"^(?:[-*_]\s*){3,}$")
_VERDICT_PREFIX_RE = re.compile(r"(?i)^\s*VERDICT\s*:")
_CANONICAL_VERDICT_RE = re.compile(
    r"^VERDICT:\s+(APPROVED|REJECTED)\s+\(([0-9]+)\s+BLOCKERS?\)$"
)
_SOFT_TOKEN_RE = re.compile(r"(?i)\b(?:REQUEST_CHANGES|NEEDS_WORK)\b")
_STANDALONE_SOFT_LINE_RE = re.compile(
    r"(?i)^\s*(?:REQUEST_CHANGES|NEEDS_WORK)(?:\s*\([0-9]+\s+BLOCKERS?\))?\s*$"
)
_BLOCKERS_SECTION_RE = re.compile(r"^\s*BLOCKERS\s*:")


@dataclass(frozen=True)
class _FencedBlock:
  """Internal representation of a Markdown fenced code block.

  Attributes:
    start_line: 0-based line index of the opening fence delimiter.
    end_line: 0-based line index of the closing fence delimiter (or EOF line).
    info: Lowercase first token of the fence info string (empty for plain fences).
    lines: Inner ``(line_index, raw_line)`` tuples excluding fence delimiters.
  """

  start_line: int
  end_line: int
  info: str
  lines: Tuple[Tuple[int, str], ...]


@dataclass(frozen=True)
class ReviewValidationResult:
  """Immutable validation result for a critic review Markdown document.

  Attributes:
    valid: True when all Six-Pillar headings, prose bodies, and binary verdict
      rules are satisfied.
    verdict: Canonical verdict string (``"APPROVED"`` or ``"REJECTED"``), or
      empty string when validation fails.
    blocker_count: Non-negative integer blocker count parsed from the verdict,
      or ``None`` when verdict validation fails.
    errors: Tuple of deterministic validation error messages.
  """

  valid: bool
  verdict: str
  blocker_count: Optional[int]
  errors: Tuple[str, ...]


@dataclass(frozen=True)
class ReviewPromotionOutcome:
  """Immutable outcome of validating and promoting a critic review round.

  Attributes:
    promoted: True when the source round file passed validation and was promoted
      to canonical ``40_critic_review.json`` / ``40_critic_review.md``.
    round_number: Validated positive integer round number.
    source_path: Absolute path to ``40_critic_review_round<N>.json`` (canonical)
      or ``40_critic_review_round<N>.md`` (legacy fallback).
    destination_path: Absolute path to promoted ``40_critic_review.json``
      (canonical) or ``40_critic_review.md`` (legacy fallback).
    validation: Structured ``ReviewValidationResult`` for the round document.
  """

  promoted: bool
  round_number: int
  source_path: str
  destination_path: str
  validation: ReviewValidationResult

  def to_dict(self) -> Dict[str, Any]:
    """Serializes the promotion outcome into a JSON-compatible dictionary.

    Returns:
      Dictionary containing promotion status, paths, verdict, blocker count,
      and any validation error messages.
    """
    return {
        "promoted": self.promoted,
        "round_number": self.round_number,
        "source_path": self.source_path,
        "destination_path": self.destination_path,
        "valid": self.validation.valid,
        "verdict": self.validation.verdict,
        "blocker_count": self.validation.blocker_count,
        "errors": list(self.validation.errors),
    }


def parse_round_number(raw_round: Any) -> int:
  """Parses and validates a strictly positive ASCII integer review round.

  Args:
    raw_round: Raw round value supplied by CLI arguments or Make macros.

  Returns:
    Validated positive integer round number (``>= 1``).

  Raises:
    ValueError: If ``raw_round`` is missing, non-integer, non-ASCII, zero,
      negative, has leading zeros, or contains path traversal characters.
  """
  if isinstance(raw_round, bool):
    raise ValueError("ROUND must be a positive integer (>= 1), not a boolean.")
  if isinstance(raw_round, int):
    if raw_round < 1:
      raise ValueError("ROUND must be a positive integer (>= 1).")
    return raw_round
  if not isinstance(raw_round, str):
    raise ValueError("ROUND must be a positive integer (>= 1).")

  cleaned = raw_round.strip()
  if not cleaned or not cleaned.isascii() or not _ROUND_ASCII_RE.match(cleaned):
    raise ValueError(
        f"Invalid ROUND value {raw_round!r}: expected positive ASCII integer (>= 1)."
    )
  return int(cleaned)


def _parse_markdown_blocks(
    text: str,
) -> Tuple[List[Tuple[int, str]], List[_FencedBlock]]:
  """Splits Markdown text into unfenced lines and structured fenced code blocks.

  Enforces CommonMark section 4.5 for backtick fences: if the info string after
  a backtick run contains another backtick, the line is treated as ordinary
  unfenced prose rather than a fence opener.

  Args:
    text: Raw Markdown document text.

  Returns:
    Tuple ``(unfenced_lines, fenced_blocks)`` where ``unfenced_lines`` holds
    0-based ``(line_index, raw_line)`` pairs outside code fences and
    ``fenced_blocks`` holds ordered ``_FencedBlock`` records.
  """
  unfenced: List[Tuple[int, str]] = []
  fenced_blocks: List[_FencedBlock] = []
  fence_char: Optional[str] = None
  fence_len: int = 0
  fence_start: int = 0
  fence_info: str = ""
  fence_lines: List[Tuple[int, str]] = []
  raw_lines = text.splitlines()

  for idx, raw_line in enumerate(raw_lines):
    stripped = raw_line.strip()
    if fence_char is None:
      m_open = _FENCE_OPEN_RE.match(raw_line)
      if m_open:
        marker = m_open.group(1)
        candidate_char = marker[0]
        rest = raw_line[m_open.end() :]
        if candidate_char == "`" and "`" in rest:
          unfenced.append((idx, raw_line))
          continue
        fence_char = candidate_char
        fence_len = len(marker)
        fence_start = idx
        info_tokens = rest.strip().split()
        fence_info = info_tokens[0].lower() if info_tokens else ""
        fence_lines = []
        continue
      unfenced.append((idx, raw_line))
    else:
      if (
          stripped
          and all(ch == fence_char for ch in stripped)
          and len(stripped) >= fence_len
      ):
        fenced_blocks.append(
            _FencedBlock(
                start_line=fence_start,
                end_line=idx,
                info=fence_info,
                lines=tuple(fence_lines),
            )
        )
        fence_char = None
        fence_len = 0
        fence_info = ""
        fence_lines = []
      else:
        fence_lines.append((idx, raw_line))

  if fence_char is not None:
    fenced_blocks.append(
        _FencedBlock(
            start_line=fence_start,
            end_line=max(fence_start, len(raw_lines) - 1),
            info=fence_info,
            lines=tuple(fence_lines),
        )
    )

  return unfenced, fenced_blocks


def _is_verdict_fence(block: _FencedBlock) -> bool:
  """Returns True when a fenced block is a verdict-only block (plain/text/yaml/yml).

  Args:
    block: Parsed Markdown fenced code block appearing after Pillar 6.

  Returns:
    True if ``block.info`` is an allowed verdict info string and the first
    non-empty line begins with ``VERDICT:``, ``BLOCKERS:``, or a standalone
    soft verdict token.
  """
  if block.info not in _VERDICT_FENCE_INFOS:
    return False
  for _line_idx, raw_line in block.lines:
    stripped = raw_line.strip()
    if not stripped:
      continue
    return bool(
        _VERDICT_PREFIX_RE.match(stripped)
        or _STANDALONE_SOFT_LINE_RE.match(stripped)
        or _BLOCKERS_SECTION_RE.match(stripped)
    )
  return False


def _has_prose_body(
    unfenced_lines: List[Tuple[int, str]],
    heading_pos: int,
    stop_before_line: Optional[int] = None,
) -> bool:
  """Checks whether a pillar heading has substantive prose before the next section.

  Stops scanning on peer or higher-level headings (levels 1-3), verdict lines,
  ``BLOCKERS:``, standalone soft tokens, or ``stop_before_line`` (the start of
  a trailing fenced verdict block). Deeper subheadings (levels 4-6) and
  horizontal rules are skipped as structural markers rather than prose.

  Args:
    unfenced_lines: Ordered list of ``(line_index, raw_line)`` outside fences.
    heading_pos: Index inside ``unfenced_lines`` of the pillar heading.
    stop_before_line: Optional 0-based source line index terminating the scan
      (such as the opening line of a trailing fenced verdict block).

  Returns:
    True when at least one non-empty line with alphanumeric prose appears
    before the enclosing level-3 pillar ends.
  """
  for line_idx, raw_line in unfenced_lines[heading_pos + 1 :]:
    if stop_before_line is not None and line_idx >= stop_before_line:
      break
    stripped = raw_line.strip()
    if not stripped:
      continue
    if (
        _SECTION_BOUNDARY_HEADING_RE.match(stripped)
        or _VERDICT_PREFIX_RE.match(stripped)
        or _BLOCKERS_SECTION_RE.match(stripped)
        or _STANDALONE_SOFT_LINE_RE.match(stripped)
    ):
      break
    if _SUBHEADING_RE.match(stripped) or _HORIZONTAL_RULE_RE.match(stripped):
      continue
    if any(ch.isalnum() for ch in stripped):
      return True
  return False


def validate_critic_review_text(text: str) -> ReviewValidationResult:
  """Validates Six-Pillar headings, prose bodies, and binary verdict syntax.

  Ignores quoted snippets inside Pillar 1-6 prose code fences while accepting
  both unfenced binary ``VERDICT:`` lines and trailing verdict-only code fences
  (plain, ``text``, ``yaml``, or ``yml``) after Pillar 6.

  Args:
    text: Full UTF-8 text of ``40_critic_review_round<N>.md``.

  Returns:
    Immutable ``ReviewValidationResult`` capturing validity, parsed verdict,
    blocker count, and any deterministic error messages.
  """
  errors: List[str] = []
  if not isinstance(text, str) or not text.strip():
    return ReviewValidationResult(
        valid=False,
        verdict="",
        blocker_count=None,
        errors=("Critic review file is empty.",),
    )

  unfenced, fenced_blocks = _parse_markdown_blocks(text)
  canonical_set = set(CANONICAL_SIX_PILLAR_HEADINGS)
  pillar6_heading = CANONICAL_SIX_PILLAR_HEADINGS[-1]

  found_headings: List[Tuple[str, int, int]] = []
  heading_counts: Dict[str, int] = {h: 0 for h in CANONICAL_SIX_PILLAR_HEADINGS}
  verdict_lines: List[str] = []
  standalone_soft_lines: List[str] = []
  pillar6_line_idx: Optional[int] = None

  for pos, (line_idx, raw_line) in enumerate(unfenced):
    stripped = raw_line.strip()
    if stripped in canonical_set:
      found_headings.append((stripped, pos, line_idx))
      heading_counts[stripped] += 1
      if stripped == pillar6_heading and pillar6_line_idx is None:
        pillar6_line_idx = line_idx
    if _VERDICT_PREFIX_RE.match(stripped):
      verdict_lines.append(stripped)
    elif _STANDALONE_SOFT_LINE_RE.match(stripped):
      standalone_soft_lines.append(stripped)

  first_verdict_fence_line: Optional[int] = None
  if pillar6_line_idx is not None:
    for block in fenced_blocks:
      if block.start_line <= pillar6_line_idx:
        continue
      if not _is_verdict_fence(block):
        continue
      if first_verdict_fence_line is None:
        first_verdict_fence_line = block.start_line
      for _bline_idx, braw_line in block.lines:
        bstripped = braw_line.strip()
        if _VERDICT_PREFIX_RE.match(bstripped):
          verdict_lines.append(bstripped)
        elif _STANDALONE_SOFT_LINE_RE.match(bstripped):
          standalone_soft_lines.append(bstripped)

  for heading in CANONICAL_SIX_PILLAR_HEADINGS:
    count = heading_counts[heading]
    if count == 0:
      errors.append(f"Missing canonical pillar heading: '{heading}'.")
    elif count > 1:
      errors.append(
          f"Duplicate canonical pillar heading '{heading}' found {count} times."
      )

  if all(heading_counts[h] == 1 for h in CANONICAL_SIX_PILLAR_HEADINGS):
    actual_order = tuple(h for h, _pos, _line_idx in found_headings)
    if actual_order != CANONICAL_SIX_PILLAR_HEADINGS:
      errors.append(
          "Canonical Six-Pillar headings are out of order; expected "
          + ", ".join(CANONICAL_SIX_PILLAR_HEADINGS)
          + "."
      )

  checked_empty = set()
  for heading, pos, line_idx in found_headings:
    if heading in checked_empty:
      continue
    checked_empty.add(heading)
    stop_line = (
        first_verdict_fence_line
        if (
            first_verdict_fence_line is not None
            and line_idx < first_verdict_fence_line
        )
        else None
    )
    if not _has_prose_body(unfenced, pos, stop_before_line=stop_line):
      errors.append(
          f"Canonical pillar heading '{heading}' must contain non-empty prose."
      )

  for soft_line in standalone_soft_lines:
    errors.append(
        f"Prohibited soft verdict token '{soft_line}' found; use binary VERDICT header."
    )

  parsed_verdict = ""
  parsed_blockers: Optional[int] = None
  if not verdict_lines:
    errors.append(
        "Missing mandatory binary verdict header "
        "('VERDICT: APPROVED (0 BLOCKERS)' or 'VERDICT: REJECTED (<N> BLOCKERS)')."
    )
  elif len(verdict_lines) > 1:
    errors.append(
        f"Found {len(verdict_lines)} VERDICT headers; expected exactly one binary verdict."
    )
  else:
    v_line = verdict_lines[0]
    if _SOFT_TOKEN_RE.search(v_line):
      errors.append(
          f"Prohibited soft verdict token in '{v_line}'; use APPROVED or REJECTED."
      )
    else:
      m_verdict = _CANONICAL_VERDICT_RE.match(v_line)
      if not m_verdict:
        errors.append(
            f"Malformed verdict header '{v_line}'; expected "
            "'VERDICT: APPROVED (0 BLOCKERS)' or 'VERDICT: REJECTED (<N> BLOCKERS)'."
        )
      else:
        verdict_word = m_verdict.group(1)
        count_val = int(m_verdict.group(2))
        if verdict_word == "APPROVED" and count_val != 0:
          errors.append(
              f"Contradictory verdict '{v_line}': APPROVED requires 0 blockers."
          )
        elif verdict_word == "REJECTED" and count_val < 1:
          errors.append(
              f"Contradictory verdict '{v_line}': REJECTED requires at least 1 blocker."
          )
        else:
          parsed_verdict = verdict_word
          parsed_blockers = count_val

  if errors:
    return ReviewValidationResult(
        valid=False,
        verdict="",
        blocker_count=None,
        errors=tuple(errors),
    )

  return ReviewValidationResult(
      valid=True,
      verdict=parsed_verdict,
      blocker_count=parsed_blockers,
      errors=(),
  )


def promote_review_round(
    repo_root: str,
    raw_round: Any,
) -> ReviewPromotionOutcome:
  """Validates round ``<N>`` critic review and promotes it to canonical state files.

  Prefers canonical ``40_critic_review_round<N>.json`` when present: validates
  the JSON schema via ``lib.state_reports``, promotes ``.json`` to
  ``40_critic_review.json`` via ``safe_copy_file`` (SCAR-SYS-05), and
  atomically renders ``40_critic_review.md`` (SCAR-TOOL-13). Falls back to
  legacy ``40_critic_review_round<N>.md`` promotion when ``.json`` is absent.
  If validation fails, any existing canonical review files stay untouched.

  Args:
    repo_root: Workspace repository root directory.
    raw_round: Positive integer round number (string or int).

  Returns:
    ``ReviewPromotionOutcome`` describing validation and copy results.

  Raises:
    ValueError: If ``raw_round`` is not a valid positive ASCII integer.
    FileNotFoundError: If neither ``40_critic_review_round<N>.json`` nor
      ``40_critic_review_round<N>.md`` exists in ``.workflow/state/``.
    OSError: If reading the source file or promoting bytes fails.
  """
  round_num = parse_round_number(raw_round)
  state_dir = os.path.join(os.path.abspath(repo_root), ".workflow", "state")
  src_json = os.path.join(state_dir, f"40_critic_review_round{round_num}.json")
  dst_json = os.path.join(state_dir, CRITIC_REVIEW_JSON_FILENAME)
  src_path = os.path.join(state_dir, f"40_critic_review_round{round_num}.md")
  dst_path = os.path.join(state_dir, CRITIC_REVIEW_MD_FILENAME)

  if os.path.isfile(src_json):
    json_val = load_critic_review_validation(state_dir, round_number=round_num)
    if (
        json_val is None
        or not json_val.valid
        or json_val.normalized_payload is None
    ):
      validation = ReviewValidationResult(
          valid=False,
          verdict="",
          blocker_count=None,
          errors=(
              json_val.errors
              if json_val is not None and json_val.errors
              else ("Invalid critic review JSON payload.",)
          ),
      )
      return ReviewPromotionOutcome(
          promoted=False,
          round_number=round_num,
          source_path=src_json,
          destination_path=dst_json,
          validation=validation,
      )
    normalized = json_val.normalized_payload
    rendered_md = render_critic_review_markdown(normalized)
    safe_copy_file(src_json, dst_json)
    atomic_write_text(
        dst_path, rendered_md, prefix=".tmp_critic_review_", suffix=".md"
    )
    validation = ReviewValidationResult(
        valid=True,
        verdict=str(normalized["verdict"]),
        blocker_count=len(normalized["blockers"]),
        errors=(),
    )
    return ReviewPromotionOutcome(
        promoted=True,
        round_number=round_num,
        source_path=src_json,
        destination_path=dst_json,
        validation=validation,
    )

  if not os.path.isfile(src_path):
    raise FileNotFoundError(
        f"Critic review round file not found: {src_path}"
    )

  with open(src_path, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

  validation = validate_critic_review_text(text)
  if not validation.valid:
    return ReviewPromotionOutcome(
        promoted=False,
        round_number=round_num,
        source_path=src_path,
        destination_path=dst_path,
        validation=validation,
    )

  safe_copy_file(src_path, dst_path)
  try:
    os.unlink(dst_json)
  except FileNotFoundError:
    pass
  return ReviewPromotionOutcome(
      promoted=True,
      round_number=round_num,
      source_path=src_path,
      destination_path=dst_path,
      validation=validation,
  )
