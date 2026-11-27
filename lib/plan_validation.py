"""Canonical Target 20 implementation plan validator (GH-270, SCAR-PROC-160).

Parses and validates ``.workflow/state/20_plan.md`` against the canonical
eight-section schema while tracking CommonMark fenced code blocks
(SCAR-LOCAL-DATA-0001) so embedded examples never spoof or duplicate headings,
Given-When-Then lines, or Markdown tables.
"""

from dataclasses import dataclass
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lib.reflex import GateFailure

CANONICAL_PLAN_HEADINGS: Tuple[str, ...] = (
    "## 1. Problem Statement and Root Cause Summary",
    "## 2. BDD Discovery and Acceptance Criteria",
    "## 3. Target Subprojects, Planned File Edits, and Layer Posture",
    "## 4. OO Design Patterns, Module Cohesion, and Documentation Standard",
    "## 5. Testing Strategy, Commands, and Burden-Balance Analysis",
    "## 6. Red-Green-Refactor TDD Plan",
    "## 7. Socratic Invariant Audit Table",
    "## 8. Consulted Reference Manuals and Target 30 Scar Carry-Forward",
)

REQUIRED_SECTION3_SUBHEADINGS: Tuple[str, ...] = (
    "### Specific Edits to Existing Files",
    "### New Files to Create",
    "### Untouched Files and Scope Quarantine",
)

_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_H2_HEADING_RE = re.compile(r"^##\s+\S")
_H3_OR_HIGHER_HEADING_RE = re.compile(r"^#{1,3}\s+\S")
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_HORIZONTAL_RULE_RE = re.compile(r"^(?:[-*_]\s*){3,}$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
_TRAILING_BOUNDARY_PATTERN = r"(?![A-Za-z0-9_/\-]|\.[A-Za-z0-9_])"
_LEADING_REL_BOUNDARY_PATTERN = r"(?:^|(?<=[^A-Za-z0-9_./\-]))(?:\./)?"
_PLANNED_EDIT_SUBHEADINGS = frozenset(REQUIRED_SECTION3_SUBHEADINGS[:2])
_QUARANTINE_SUBHEADING = REQUIRED_SECTION3_SUBHEADINGS[2]
_GWT_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("Given", re.compile(r"(?i)^\s*(?:[-*+]\s+)?(?:\*\*|__)?Given(?:\*\*|__)?\b")),
    ("When", re.compile(r"(?i)^\s*(?:[-*+]\s+)?(?:\*\*|__)?When(?:\*\*|__)?\b")),
    ("Then", re.compile(r"(?i)^\s*(?:[-*+]\s+)?(?:\*\*|__)?Then(?:\*\*|__)?\b")),
)


@dataclass(frozen=True)
class PlanValidationResult:
  """Immutable validation result for a Target 20 implementation plan document.

  Attributes:
    valid: True when all eight canonical headings, required Section 3
      subheadings, Section 2 Given-When-Then criteria, Section 7 Markdown
      table, and non-empty body checks pass.
    errors: Tuple of deterministic validation diagnostic messages.
    headings_found: Tuple of matched canonical headings in document order.
  """

  valid: bool
  errors: Tuple[str, ...]
  headings_found: Tuple[str, ...] = ()

  def to_dict(self) -> Dict[str, Any]:
    """Serializes the plan validation result into a JSON-compatible dictionary.

    Returns:
      Dictionary containing ``valid``, ``errors``, and ``headings_found``.
    """
    return {
        "valid": self.valid,
        "errors": list(self.errors),
        "headings_found": list(self.headings_found),
    }


def _extract_unfenced_lines(text: str) -> List[Tuple[int, str]]:
  """Returns unfenced ``(line_index, raw_line)`` tuples tracking CommonMark fences.

  Tracks triple-or-more backtick and tilde fences per SCAR-LOCAL-DATA-0001 and
  CommonMark section 4.5 (backtick info strings containing another backtick
  stay ordinary prose).

  Args:
    text: Raw UTF-8 Markdown text.

  Returns:
    Ordered list of 0-based ``(line_index, raw_line)`` pairs outside code fences.
  """
  unfenced: List[Tuple[int, str]] = []
  fence_char: Optional[str] = None
  fence_len = 0

  for idx, raw_line in enumerate(text.splitlines()):
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
        continue
      unfenced.append((idx, raw_line))
    else:
      if (
          stripped
          and all(ch == fence_char for ch in stripped)
          and len(stripped) >= fence_len
      ):
        fence_char = None
        fence_len = 0

  return unfenced


def _slice_section_lines(
    unfenced: Sequence[Tuple[int, str]], start_pos: int
) -> List[str]:
  """Slices unfenced lines belonging to an H2 section until the next H2 heading.

  Args:
    unfenced: Ordered unfenced ``(line_index, raw_line)`` pairs.
    start_pos: Position in ``unfenced`` of the section's H2 heading.

  Returns:
    List of raw unfenced lines inside the section body.
  """
  body: List[str] = []
  for _line_idx, raw_line in unfenced[start_pos + 1 :]:
    stripped = raw_line.strip()
    if _H2_HEADING_RE.match(stripped):
      break
    body.append(raw_line)
  return body


def _has_substantive_prose(lines: Sequence[str]) -> bool:
  """Returns True when lines contain at least one non-heading alphanumeric line.

  Args:
    lines: Sequence of unfenced Markdown lines inside a section or subsection.

  Returns:
    True if substantive prose or table content is present, otherwise False.
  """
  for raw_line in lines:
    stripped = raw_line.strip()
    if not stripped:
      continue
    if _ANY_HEADING_RE.match(stripped) or _HORIZONTAL_RULE_RE.match(stripped):
      continue
    if any(ch.isalnum() for ch in stripped):
      return True
  return False


def _validate_section2_bdd(section_lines: Sequence[str]) -> List[str]:
  """Validates that Section 2 contains unfenced Given, When, and Then lines.

  Args:
    section_lines: Unfenced lines belonging to Section 2.

  Returns:
    List of validation errors (empty when valid).
  """
  missing_clauses: List[str] = []
  for keyword, pattern in _GWT_PATTERNS:
    if not any(pattern.match(line) for line in section_lines):
      missing_clauses.append(keyword)
  if missing_clauses:
    joined = ", ".join(missing_clauses)
    return [
        f"Section 2 ('{CANONICAL_PLAN_HEADINGS[1]}') must contain unfenced "
        f"Given-When-Then acceptance criteria (missing: {joined})."
    ]
  return []


def _validate_section3_subheadings(section_lines: Sequence[str]) -> List[str]:
  """Validates required Section 3 H3 subheadings and their non-empty bodies.

  Args:
    section_lines: Unfenced lines belonging to Section 3.

  Returns:
    List of validation errors (empty when valid).
  """
  errors: List[str] = []
  required_set = set(REQUIRED_SECTION3_SUBHEADINGS)
  positions: Dict[str, List[int]] = {sub: [] for sub in REQUIRED_SECTION3_SUBHEADINGS}

  for idx, raw_line in enumerate(section_lines):
    stripped = raw_line.strip()
    if stripped in required_set:
      positions[stripped].append(idx)

  for sub in REQUIRED_SECTION3_SUBHEADINGS:
    occ = positions[sub]
    if not occ:
      errors.append(
          f"Section 3 ('{CANONICAL_PLAN_HEADINGS[2]}') missing required "
          f"subheading '{sub}'."
      )
      continue
    if len(occ) > 1:
      errors.append(
          f"Section 3 duplicate required subheading '{sub}' found {len(occ)} times."
      )
    sub_body: List[str] = []
    for raw_line in section_lines[occ[0] + 1 :]:
      if _H3_OR_HIGHER_HEADING_RE.match(raw_line.strip()):
        break
      sub_body.append(raw_line)
    if not _has_substantive_prose(sub_body):
      errors.append(
          f"Section 3 subheading '{sub}' must contain non-empty body content."
      )
  return errors


def _validate_section7_table(section_lines: Sequence[str]) -> List[str]:
  """Validates that Section 7 contains an unfenced Markdown audit table.

  Args:
    section_lines: Unfenced lines belonging to Section 7.

  Returns:
    List of validation errors (empty when a valid Markdown table exists).
  """
  for idx in range(1, len(section_lines)):
    if not _TABLE_SEP_RE.match(section_lines[idx]):
      continue
    header_line = section_lines[idx - 1].strip()
    if "|" not in header_line or not any(ch.isalnum() for ch in header_line):
      continue
    for data_line in section_lines[idx + 1 :]:
      stripped = data_line.strip()
      if not stripped:
        break
      if "|" in stripped and any(ch.isalnum() for ch in stripped):
        return []
  return [
      f"Section 7 ('{CANONICAL_PLAN_HEADINGS[6]}') must contain an unfenced "
      "Markdown Socratic Invariant Audit Table with header, separator, and data row."
  ]


def validate_plan_markdown(text: str) -> PlanValidationResult:
  """Validates Target 20 plan Markdown text against the canonical 8-section spec.

  Args:
    text: Full UTF-8 contents of ``.workflow/state/20_plan.md``.

  Returns:
    Immutable ``PlanValidationResult`` describing validity, diagnostics, and
    discovered canonical headings.
  """
  if not isinstance(text, str) or not text.strip():
    return PlanValidationResult(
        valid=False,
        errors=("Plan file is empty.",),
        headings_found=(),
    )

  unfenced = _extract_unfenced_lines(text)
  canonical_set = set(CANONICAL_PLAN_HEADINGS)
  found_headings: List[Tuple[str, int]] = []
  heading_counts: Dict[str, int] = {h: 0 for h in CANONICAL_PLAN_HEADINGS}
  first_pos_by_heading: Dict[str, int] = {}

  for pos, (_line_idx, raw_line) in enumerate(unfenced):
    stripped = raw_line.strip()
    if stripped in canonical_set:
      found_headings.append((stripped, pos))
      heading_counts[stripped] += 1
      if stripped not in first_pos_by_heading:
        first_pos_by_heading[stripped] = pos

  errors: List[str] = []
  for heading in CANONICAL_PLAN_HEADINGS:
    count = heading_counts[heading]
    if count == 0:
      errors.append(f"Missing canonical plan heading: '{heading}'.")
    elif count > 1:
      errors.append(
          f"Duplicate canonical plan heading '{heading}' found {count} times."
      )

  found_order = tuple(h for h, _pos in found_headings)
  if all(heading_counts[h] == 1 for h in CANONICAL_PLAN_HEADINGS):
    if found_order != CANONICAL_PLAN_HEADINGS:
      errors.append(
          "Canonical plan headings are out of order; expected sections 1 through 8 "
          "in canonical sequence."
      )

  for heading in CANONICAL_PLAN_HEADINGS:
    if heading not in first_pos_by_heading:
      continue
    sec_lines = _slice_section_lines(unfenced, first_pos_by_heading[heading])
    if not _has_substantive_prose(sec_lines):
      errors.append(
          f"Canonical plan section '{heading}' must contain non-empty body content."
      )
      continue
    if heading == CANONICAL_PLAN_HEADINGS[1]:
      errors.extend(_validate_section2_bdd(sec_lines))
    elif heading == CANONICAL_PLAN_HEADINGS[2]:
      errors.extend(_validate_section3_subheadings(sec_lines))
    elif heading == CANONICAL_PLAN_HEADINGS[6]:
      errors.extend(_validate_section7_table(sec_lines))

  return PlanValidationResult(
      valid=(len(errors) == 0),
      errors=tuple(errors),
      headings_found=found_order,
  )


def validate_plan_file(plan_path: str) -> PlanValidationResult:
  """Reads and validates a Target 20 plan Markdown file on disk.

  Args:
    plan_path: Path to ``.workflow/state/20_plan.md``.

  Returns:
    Immutable ``PlanValidationResult`` failing closed when the file is missing,
    unreadable, or violates the canonical plan specification.
  """
  if not plan_path or not isinstance(plan_path, str):
    return PlanValidationResult(
        valid=False,
        errors=("Plan file path is missing.",),
        headings_found=(),
    )
  if not os.path.isfile(plan_path):
    return PlanValidationResult(
        valid=False,
        errors=(f"Plan file not found: {plan_path}",),
        headings_found=(),
    )
  try:
    with open(plan_path, "r", encoding="utf-8", errors="replace") as fh:
      text = fh.read()
  except OSError as exc:
    return PlanValidationResult(
        valid=False,
        errors=(f"Failed to read plan file '{plan_path}': {exc}",),
        headings_found=(),
    )
  return validate_plan_markdown(text)


def build_plan_spec_failure(trigger: str) -> GateFailure:
  """Constructs the canonical SCAR-PROC-160 GateFailure for invalid plan files.

  Args:
    trigger: Concise validation failure message describing plan defects.

  Returns:
    Immutable ``GateFailure`` carrying ``PROC-160`` and ``INVALID_PLAN_SPEC``.
  """
  return GateFailure(
      scar_id="PROC-160",
      code="INVALID_PLAN_SPEC",
      trigger=trigger,
      rejected=(
          "Entering Target 20 with an incomplete or non-canonical "
          ".workflow/state/20_plan.md."
      ),
      reflex=(
          "Update .workflow/state/20_plan.md with all 8 canonical headings in "
          "order, the 3 Section 3 subheadings, Section 2 Given-When-Then "
          "criteria, and the Section 7 Socratic Invariant Audit Table per "
          "02_archaeology_and_planning.md, then re-run check-plan."
      ),
  )


def plan_mentions_relpath(
    plan_text: str, rel_path: str, plugin_root: Optional[str] = None
) -> bool:
  """Checks whether ``plan_text`` explicitly references ``rel_path`` at path boundaries.

  Matches backticked paths, ``./`` prefixed paths, Markdown table cells, and
  ``file://`` or absolute repository paths while rejecting substring traps such
  as ``other/lib/config.py`` or ``lib/config.py.bak``.

  Args:
    plan_text: Raw UTF-8 text of ``.workflow/state/20_plan.md`` or an extracted
      section body.
    rel_path: POSIX repository-relative path to search for.
    plugin_root: Optional Makeflow repository root for exact absolute and
      ``file://`` URL matching.

  Returns:
    True when ``plan_text`` explicitly references ``rel_path``, else False.
  """
  if not plan_text or not rel_path:
    return False
  norm_rel = rel_path.replace("\\", "/")
  while norm_rel.startswith("./"):
    norm_rel = norm_rel[2:]
  norm_rel = norm_rel.lstrip("/")
  if not norm_rel:
    return False
  escaped_rel = re.escape(norm_rel)

  rel_pattern = re.compile(
      _LEADING_REL_BOUNDARY_PATTERN + escaped_rel + _TRAILING_BOUNDARY_PATTERN
  )
  if rel_pattern.search(plan_text):
    return True

  if plugin_root:
    roots = {
        os.path.abspath(plugin_root).replace("\\", "/").rstrip("/"),
        os.path.realpath(plugin_root).replace("\\", "/").rstrip("/"),
    }
    for root in roots:
      abs_posix = f"{root}/{norm_rel}"
      abs_pat = re.compile(
          r"(?:^|(?<=[^A-Za-z0-9_./\-]))(?:file://)?"
          + re.escape(abs_posix)
          + _TRAILING_BOUNDARY_PATTERN
      )
      if abs_pat.search(plan_text):
        return True
  else:
    file_url_pat = re.compile(
        r"file://[^\s`'\"<>|)]+/" + escaped_rel + _TRAILING_BOUNDARY_PATTERN
    )
    if file_url_pat.search(plan_text):
      return True

  return False


def extract_planned_edit_text(plan_text: str) -> str:
  """Extracts unfenced planned-edit Markdown text while excluding quarantine sections.

  Tracks CommonMark fenced code blocks via ``_extract_unfenced_lines``
  (SCAR-LOCAL-DATA-0001). When canonical Section 3 edit subheadings
  (``### Specific Edits to Existing Files`` or ``### New Files to Create``)
  appear outside code fences, returns only the unfenced lines beneath those
  subheadings (including nested ``####`` subsections) and stops at the next
  H1-H3 heading. For compact plans lacking canonical edit subheadings, falls
  back to all unfenced lines outside ``### Untouched Files and Scope Quarantine``.

  Args:
    plan_text: Raw UTF-8 contents of ``.workflow/state/20_plan.md``.

  Returns:
    Newline-joined unfenced planned-edit text, or ``""`` when empty.
  """
  if not isinstance(plan_text, str) or not plan_text:
    return ""

  unfenced = _extract_unfenced_lines(plan_text)
  has_canonical_edit_header = any(
      raw_line.strip() in _PLANNED_EDIT_SUBHEADINGS
      for _idx, raw_line in unfenced
  )

  collected: List[str] = []
  if has_canonical_edit_header:
    in_planned_section = False
    for _idx, raw_line in unfenced:
      stripped = raw_line.strip()
      if stripped in _PLANNED_EDIT_SUBHEADINGS:
        in_planned_section = True
        continue
      if _H3_OR_HIGHER_HEADING_RE.match(stripped):
        in_planned_section = False
        continue
      if in_planned_section:
        collected.append(raw_line)
    return "\n".join(collected)

  in_quarantine = False
  for _idx, raw_line in unfenced:
    stripped = raw_line.strip()
    if stripped == _QUARANTINE_SUBHEADING:
      in_quarantine = True
      continue
    if in_quarantine and _H3_OR_HIGHER_HEADING_RE.match(stripped):
      in_quarantine = False
    if not in_quarantine:
      collected.append(raw_line)
  return "\n".join(collected)


def plan_allows_edit_relpath(
    plan_text: str, rel_path: str, plugin_root: Optional[str] = None
) -> bool:
  """Checks whether ``plan_text`` authorizes editing ``rel_path``.

  Evaluates only unfenced planned-edit text extracted by
  ``extract_planned_edit_text`` so fenced code examples and
  ``### Untouched Files and Scope Quarantine`` entries never authorize edits.

  Args:
    plan_text: Raw UTF-8 contents of ``.workflow/state/20_plan.md``.
    rel_path: POSIX repository-relative path to check.
    plugin_root: Optional Makeflow repository root for exact absolute and
      ``file://`` URL matching.

  Returns:
    True when ``rel_path`` is referenced in the plan's unfenced planned-edit
    text, otherwise False.
  """
  edit_text = extract_planned_edit_text(plan_text)
  if not edit_text:
    return False
  return plan_mentions_relpath(edit_text, rel_path, plugin_root=plugin_root)


def resolve_approved_planned_governance_files(
    repo_root: str, candidate_relpaths: Sequence[str]
) -> Tuple[str, ...]:
  """Resolves sorted governance file paths authorized by an approved Target 20 plan.

  Requires a Git-backed repository (``.git`` file or directory) containing both
  ``.workflow/state/20_plan.md`` and ``.workflow/state/20_plan_approved.md``.

  Args:
    repo_root: Repository root directory to inspect.
    candidate_relpaths: Candidate POSIX relative paths (for example
      ``CORE_GOVERNANCE_FILES``) to match against the approved plan.

  Returns:
    Deterministically sorted tuple of matched POSIX relative paths, or an empty
    tuple when any git, plan, or approval prerequisite is absent.
  """
  if not repo_root or not isinstance(repo_root, str):
    return ()
  abs_repo = os.path.abspath(repo_root)
  if not os.path.exists(os.path.join(abs_repo, ".git")):
    return ()
  state_dir = os.path.join(abs_repo, ".workflow", "state")
  plan_path = os.path.join(state_dir, "20_plan.md")
  approved_path = os.path.join(state_dir, "20_plan_approved.md")
  if not (os.path.isfile(plan_path) and os.path.isfile(approved_path)):
    return ()
  try:
    with open(plan_path, "r", encoding="utf-8", errors="replace") as fh:
      plan_text = fh.read()
  except OSError:
    return ()

  matched = sorted({
      rel.replace("\\", "/")
      for rel in candidate_relpaths
      if isinstance(rel, str)
      and plan_allows_edit_relpath(plan_text, rel, plugin_root=abs_repo)
  })
  return tuple(matched)
