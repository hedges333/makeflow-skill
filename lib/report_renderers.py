"""Pure deterministic Markdown renderers for canonical Makeflow JSON reports (GH-281).

Renders human-readable `.md` views from validated JSON payloads for Target 15
archaeology receipts, Target 30 TDD reports, Target 40 critic reviews, and
Target 70 bot triage reports while preserving fenced code blocks cleanly per
SCAR-LOCAL-DATA-0001. This is a pure leaf module with zero upward imports.
"""

import re
from typing import Any, List, Mapping, Optional, Tuple

CANONICAL_SIX_PILLAR_HEADINGS: Tuple[str, ...] = (
    "### 1. Intent",
    "### 2. Verification",
    "### 3. Architecture",
    "### 4. Blast Radius",
    "### 5. Reviewer Defense",
    "### 6. Immune Defenses & Crash Antibodies",
)

PILLAR_KEY_ORDER: Tuple[Tuple[str, str], ...] = (
    ("intent", "### 1. Intent"),
    ("verification", "### 2. Verification"),
    ("architecture", "### 3. Architecture"),
    ("blast_radius", "### 4. Blast Radius"),
    ("reviewer_defense", "### 5. Reviewer Defense"),
    ("immune_defenses", "### 6. Immune Defenses & Crash Antibodies"),
)

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


def ensure_balanced_fences(text: str) -> str:
  """Preserves fenced code blocks and closes any unterminated fence safely.

  Tracks active CommonMark fence character and length across lines
  (SCAR-LOCAL-DATA-0001). Indentation, blank lines, and embedded ``---`` or
  ``## `` lines inside valid fences remain verbatim. Closing fences must have
  at most three leading spaces per CommonMark 4.5. If prose leaves a fence
  unclosed, appends the matching closing fence so subsequent headings are
  never swallowed.

  Args:
    text: Raw Markdown prose string.

  Returns:
    Normalized prose string with balanced code fences and no trailing blank
    padding.
  """
  if not text:
    return ""
  normalized = text.replace("\r\n", "\n").strip("\n")
  if not normalized:
    return ""

  fence_char: Optional[str] = None
  fence_len = 0
  for line in normalized.splitlines():
    if fence_char is None:
      m_open = _FENCE_OPEN_RE.match(line)
      if m_open:
        marker = m_open.group(1)
        rest = m_open.group(2)
        if marker[0] == "`" and "`" in rest:
          continue
        fence_char = marker[0]
        fence_len = len(marker)
    else:
      m_close = _FENCE_CLOSE_RE.match(line)
      if (
          m_close
          and m_close.group(1)[0] == fence_char
          and len(m_close.group(1)) >= fence_len
      ):
        fence_char = None
        fence_len = 0

  if fence_char is not None:
    return f"{normalized}\n{fence_char * fence_len}"
  return normalized


def _single_line_cell(value: Any) -> str:
  """Formats a scalar value safely for single-line Markdown table cells.

  Args:
    value: Raw value to render inside a Markdown table cell.

  Returns:
    Single-line string with pipe characters escaped.
  """
  cleaned = " ".join(str(value or "").splitlines()).strip()
  return cleaned.replace("|", "\\|")


def render_archaeology_markdown(payload: Mapping[str, Any]) -> str:
  """Renders ``15_archaeology_receipt.md`` from a validated archaeology payload.

  Args:
    payload: Validated Target 15 archaeology JSON dictionary.

  Returns:
    Deterministic UTF-8 Markdown ending with a single newline.
  """
  ticket_id = str(payload.get("ticket_id") or "").strip()
  hosting_mode = str(payload.get("hosting_mode") or "").strip()
  subprojects = payload.get("subprojects") or []
  belnap_facts = payload.get("belnap_facts") or []
  testing_strategy = payload.get("testing_strategy") or {}
  consulted_refs = payload.get("consulted_references") or []
  applicable_scars = payload.get("applicable_scars") or []
  notes = ensure_balanced_fences(str(payload.get("notes") or ""))

  lines: List[str] = [
      "# Target 15: Repository Archaeology Receipt",
      "",
      f"- **Ticket**: `{ticket_id}`",
      f"- **HOSTING_MODE**: `{hosting_mode}`",
      "",
      "## Subprojects",
      "",
  ]
  if subprojects:
    for sub in subprojects:
      lines.append(f"- `{sub}`")
  else:
    lines.append("- None")

  lines.extend([
      "",
      "## Belnap Epistemic Facts",
      "",
  ])
  if belnap_facts:
    lines.extend([
        "| Truth State | Claim | Evidence |",
        "| :--- | :--- | :--- |",
    ])
    for fact in belnap_facts:
      lines.append(
          f"| `{_single_line_cell(fact.get('truth_state'))}` "
          f"| {_single_line_cell(fact.get('claim'))} "
          f"| `{_single_line_cell(fact.get('evidence'))}` |"
      )
  else:
    lines.append("- None")

  lines.extend([
      "",
      "## Testing Strategy",
      "",
      f"- **Failure Mechanism**: {_single_line_cell(testing_strategy.get('failure_mechanism'))}",
      f"- **Burden Balance**: {_single_line_cell(testing_strategy.get('burden_balance'))}",
      "",
      "### Given-When-Then Scenarios",
      "",
  ])
  scenarios = testing_strategy.get("scenarios") or []
  if scenarios:
    for idx, scenario in enumerate(scenarios, start=1):
      lines.append(f"{idx}. {_single_line_cell(scenario)}")
  else:
    lines.append("- None")

  lines.extend([
      "",
      "## Consulted References",
      "",
  ])
  if consulted_refs:
    for ref in consulted_refs:
      lines.append(f"- `{ref}`")
  else:
    lines.append("- None")

  lines.extend([
      "",
      "## Applicable Synthetic Scars",
      "",
  ])
  if applicable_scars:
    for scar in applicable_scars:
      lines.append(
          f"- **`{scar.get('id')}`**: {_single_line_cell(scar.get('probe'))}"
      )
  else:
    lines.append("- None")

  if notes:
    lines.extend(["", "## Notes", "", notes])

  return "\n".join(lines) + "\n"


def render_tdd_report_markdown(payload: Mapping[str, Any]) -> str:
  """Renders ``30_tdd_report.md`` from a validated Target 30 TDD payload.

  Args:
    payload: Validated Target 30 TDD report JSON dictionary.

  Returns:
    Deterministic UTF-8 Markdown ending with a single newline.
  """
  ticket_id = str(payload.get("ticket_id") or "").strip()
  changed_files = payload.get("changed_files") or []
  test_commands = payload.get("test_commands_run") or []
  applied_scars = payload.get("applied_scars") or []
  summary = ensure_balanced_fences(str(payload.get("summary") or ""))

  lines: List[str] = [
      "# Target 30: TDD Implementation Report",
      "",
      f"- **Ticket**: `{ticket_id}`",
      "",
      "## Summary",
      "",
      summary,
      "",
      "## Changed Files",
      "",
  ]
  if changed_files:
    for path in changed_files:
      lines.append(f"- `{path}`")
  else:
    lines.append("- None")

  lines.extend([
      "",
      "## Test Commands Run",
      "",
  ])
  if test_commands:
    lines.extend([
        "| Phase | Exit Code | Command |",
        "| :--- | :--- | :--- |",
    ])
    for entry in test_commands:
      lines.append(
          f"| `{_single_line_cell(entry.get('phase'))}` "
          f"| `{entry.get('exit_code')}` "
          f"| `{_single_line_cell(entry.get('command'))}` |"
      )
  else:
    lines.append("- None")

  lines.extend([
      "",
      "## Applied Synthetic Scars",
      "",
  ])
  if applied_scars:
    for scar_id in applied_scars:
      lines.append(f"- `{scar_id}`")
  else:
    lines.append("- None")

  return "\n".join(lines) + "\n"


def render_critic_review_markdown(payload: Mapping[str, Any]) -> str:
  """Renders ``40_critic_review_round<N>.md`` / ``40_critic_review.md`` from JSON.

  Emits the exact six canonical H3 headings in order with fence-safe prose
  bodies followed by the exact binary ``VERDICT:`` header required by
  ``lib.review_promotion.validate_critic_review_text``.

  Args:
    payload: Validated Target 40 critic review JSON dictionary.

  Returns:
    Deterministic UTF-8 Markdown ending with a single newline.
  """
  round_num = int(payload["round"])
  verdict = str(payload["verdict"]).strip()
  pillars = payload["pillars"]
  blockers = payload.get("blockers") or []
  nbos = payload.get("non_blocking_observations") or []
  pillar_summaries = payload.get("pillar_summaries") or {}

  lines: List[str] = [
      f"# Adversarial Code Review - Round {round_num}",
      "",
  ]
  for key, heading in PILLAR_KEY_ORDER:
    prose = ensure_balanced_fences(str(pillars[key]))
    lines.append(heading)
    lines.append("")
    lines.append(prose)
    lines.append("")

  blocker_count = len(blockers)
  if verdict == "APPROVED":
    verdict_line = "VERDICT: APPROVED (0 BLOCKERS)"
  else:
    unit = "BLOCKER" if blocker_count == 1 else "BLOCKERS"
    verdict_line = f"VERDICT: REJECTED ({blocker_count} {unit})"
  lines.append(verdict_line)

  if blockers:
    lines.extend(["", "## Blockers", ""])
    for blocker in blockers:
      lines.extend([
          f"- **ID**: `{_single_line_cell(blocker.get('id'))}`",
          f"  - **Pillar**: {_single_line_cell(blocker.get('pillar'))}",
          f"  - **Location**: `{_single_line_cell(blocker.get('location'))}`",
          f"  - **Wound**: {_single_line_cell(blocker.get('wound'))}",
          f"  - **Trap**: {_single_line_cell(blocker.get('trap'))}",
          f"  - **Diagnostic Invariant**: {_single_line_cell(blocker.get('diagnostic_invariant'))}",
      ])

  if nbos:
    lines.extend([
        "",
        "## Non-Blocking Observations (SCAR-PROC-108)",
        "",
        "| ID | Pillar | Location | Scope Relationship | Disposition | Summary |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    for nbo in nbos:
      lines.append(
          f"| `{_single_line_cell(nbo.get('id'))}` "
          f"| {_single_line_cell(nbo.get('pillar'))} "
          f"| `{_single_line_cell(nbo.get('location'))}` "
          f"| {_single_line_cell(nbo.get('relationship'))} "
          f"| `{_single_line_cell(nbo.get('disposition'))}` "
          f"| {_single_line_cell(nbo.get('summary'))} |"
      )

  if isinstance(pillar_summaries, Mapping) and pillar_summaries:
    lines.extend(["", "## Pillar Summaries", ""])
    for key, heading in PILLAR_KEY_ORDER:
      summary_val = pillar_summaries.get(key)
      if isinstance(summary_val, str) and summary_val.strip():
        lines.append(
            f"- **{heading.replace('### ', '')}**: {_single_line_cell(summary_val)}"
        )

  return "\n".join(lines) + "\n"


def render_bot_triage_markdown(payload: Mapping[str, Any]) -> str:
  """Renders ``70_bot_triage.md`` from a validated Target 70 triage payload.

  Args:
    payload: Validated Target 70 bot triage JSON dictionary.

  Returns:
    Deterministic UTF-8 Markdown ending with a single newline.
  """
  hosting_mode = str(payload.get("hosting_mode") or "").strip()
  pr_number = payload.get("pr_number")
  ci_status = str(payload.get("ci_status") or "").strip()
  quiescence = bool(payload.get("quiescence_satisfied", False))
  ready = bool(payload.get("ready_to_land", False))
  checks = payload.get("checks") or []
  findings = payload.get("findings") or []

  pr_display = f"#{pr_number}" if pr_number is not None else "None"
  lines: List[str] = [
      "# Target 70: Bot Quiescence and Triage Report",
      "",
      f"- **Hosting Mode**: `{hosting_mode}`",
      f"- **Pull Request**: {pr_display}",
      f"- **CI Status**: `{ci_status}`",
      f"- **Quiescence Satisfied**: `{'true' if quiescence else 'false'}`",
      f"- **Ready to Land**: `{'true' if ready else 'false'}`",
      "",
      "## Checks",
      "",
  ]
  if checks:
    lines.extend([
        "| Check | Status | Details |",
        "| :--- | :--- | :--- |",
    ])
    for chk in checks:
      lines.append(
          f"| {_single_line_cell(chk.get('name'))} "
          f"| `{_single_line_cell(chk.get('status'))}` "
          f"| {_single_line_cell(chk.get('details', ''))} |"
      )
  else:
    lines.append("- None")

  lines.extend([
      "",
      "## Findings",
      "",
  ])
  if findings:
    lines.extend([
        "| Source | Location | Action | Resolved | Summary | Rationale |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    for finding in findings:
      resolved_str = "true" if finding.get("resolved", False) else "false"
      lines.append(
          f"| {_single_line_cell(finding.get('source'))} "
          f"| `{_single_line_cell(finding.get('location'))}` "
          f"| `{_single_line_cell(finding.get('action'))}` "
          f"| `{resolved_str}` "
          f"| {_single_line_cell(finding.get('summary'))} "
          f"| {_single_line_cell(finding.get('rationale'))} |"
      )
  else:
    lines.append("- None")

  return "\n".join(lines) + "\n"
