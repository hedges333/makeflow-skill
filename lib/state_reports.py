"""Canonical JSON subagent report registry, strict schema validators, and atomic dual writer (GH-281).

Implements the Strategy + Registry + Repository architecture for Makeflow
subagent reports (``archaeology``, ``tdd_report``, ``critic_review``,
``bot_triage``). Validates full payloads before touching disk, persists
pretty-printed ``indent=2`` JSON atomically alongside deterministic rendered
Markdown views, and exposes shared JSON-first readers so downstream modules
never scrape Markdown prose (SCAR-ARCH-10, SCAR-TOOL-13, SCAR-API-0006).
"""

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from lib.active_state import stage_atomic_text
from lib.report_renderers import (
    PILLAR_KEY_ORDER,
    render_archaeology_markdown,
    render_bot_triage_markdown,
    render_critic_review_markdown,
    render_tdd_report_markdown,
)

ARCHAEOLOGY_JSON_FILENAME = "15_archaeology_receipt.json"
ARCHAEOLOGY_MD_FILENAME = "15_archaeology_receipt.md"
TDD_REPORT_JSON_FILENAME = "30_tdd_report.json"
TDD_REPORT_MD_FILENAME = "30_tdd_report.md"
CRITIC_REVIEW_JSON_FILENAME = "40_critic_review.json"
CRITIC_REVIEW_MD_FILENAME = "40_critic_review.md"
BOT_TRIAGE_JSON_FILENAME = "70_bot_triage.json"
BOT_TRIAGE_MD_FILENAME = "70_bot_triage.md"

VALID_HOSTING_MODES: FrozenSet[str] = frozenset({"GITHUB", "PURE_GIT"})
VALID_BELNAP_STATES: FrozenSet[str] = frozenset({
    "ESTABLISHED",
    "CONTRADICTED",
    "UNKNOWN",
    "RETRACTED",
})
VALID_TDD_PHASES: FrozenSet[str] = frozenset({
    "red",
    "green",
    "refactor",
    "regression",
})
VALID_CRITIC_VERDICTS: FrozenSet[str] = frozenset({"APPROVED", "REJECTED"})
VALID_CI_STATUSES: FrozenSet[str] = frozenset({
    "PASSING",
    "FAILING",
    "PENDING",
})
VALID_TRIAGE_ACTIONS: FrozenSet[str] = frozenset({
    "Accept",
    "Defend",
    "Defer",
    "Acknowledge",
})

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_SUBHEADING_OR_RULE_RE = re.compile(r"^(?:#{4,6}\s+\S|(?:[-*_]\s*){3,})$")


@dataclass(frozen=True)
class ReportValidationResult:
  """Immutable result of validating a structured subagent report payload.

  Attributes:
    valid: True when the payload satisfies all schema and semantic invariants.
    errors: Tuple of deterministic validation error messages.
    normalized_payload: Validated JSON-serializable dictionary when valid, or
      None when validation fails.
  """

  valid: bool
  errors: Tuple[str, ...] = ()
  normalized_payload: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ReportSpec:
  """Strategy object describing a registered Makeflow report type.

  Attributes:
    report_type: Canonical report type identifier (for example ``"archaeology"``).
    json_filename_template: Target JSON filename (may include ``{round}``).
    md_filename_template: Target rendered Markdown filename (may include ``{round}``).
    validator: Pure schema validation callable returning ``ReportValidationResult``.
    renderer: Pure Markdown rendering callable accepting the normalized payload.
    requires_round: True when filenames interpolate ``payload["round"]``.
  """

  report_type: str
  json_filename_template: str
  md_filename_template: str
  validator: Callable[[Dict[str, Any]], ReportValidationResult]
  renderer: Callable[[Dict[str, Any]], str]
  requires_round: bool = False

  def resolve_filenames(
      self, payload: Mapping[str, Any]
  ) -> Tuple[str, str]:
    """Resolves concrete ``(json_filename, md_filename)`` for a validated payload.

    Args:
      payload: Validated report payload dictionary.

    Returns:
      Tuple of ``(json_filename, md_filename)``.
    """
    if self.requires_round:
      round_num = int(payload["round"])
      return (
          self.json_filename_template.format(round=round_num),
          self.md_filename_template.format(round=round_num),
      )
    return self.json_filename_template, self.md_filename_template


@dataclass(frozen=True)
class ReportSubmissionResult:
  """Immutable outcome of validating and atomically writing a subagent report.

  Attributes:
    ok: True when validation passed and both `.json` and `.md` files were saved.
    report_type: Requested report type identifier.
    json_path: Absolute path to the persisted canonical `.json` file on success.
    md_path: Absolute path to the rendered `.md` view on success.
    errors: Tuple of validation or I/O error strings on failure.
  """

  ok: bool
  report_type: str
  json_path: str = ""
  md_path: str = ""
  errors: Tuple[str, ...] = ()

  def to_dict(self) -> Dict[str, Any]:
    """Serializes the submission result for MCP tool responses.

    Returns:
      JSON-compatible dictionary with camelCase paths and status fields.
    """
    return {
        "ok": self.ok,
        "reportType": self.report_type,
        "jsonPath": self.json_path,
        "mdPath": self.md_path,
        "errors": list(self.errors),
    }


def _is_non_empty_str(value: Any) -> bool:
  """Returns True when value is a non-empty, non-whitespace string."""
  return isinstance(value, str) and bool(value.strip())


def _is_non_bool_int(value: Any) -> bool:
  """Returns True when value is an integer and not a bool."""
  return isinstance(value, int) and not isinstance(value, bool)


def _has_unfenced_alnum_prose(text: str) -> bool:
  """Checks whether Markdown text contains alphanumeric prose outside code fences.

  Args:
    text: Candidate pillar prose string.

  Returns:
    True when at least one unfenced non-subheading line has alphanumeric text.
  """
  fence_char: Optional[str] = None
  fence_len = 0
  for raw_line in text.splitlines():
    stripped = raw_line.strip()
    if fence_char is None:
      m_open = _FENCE_OPEN_RE.match(raw_line)
      if m_open:
        marker = m_open.group(1)
        rest = m_open.group(2)
        if not (marker[0] == "`" and "`" in rest):
          fence_char = marker[0]
          fence_len = len(marker)
          continue
      if not stripped or _SUBHEADING_OR_RULE_RE.match(stripped):
        continue
      if any(ch.isalnum() for ch in stripped):
        return True
    else:
      m_close = _FENCE_CLOSE_RE.match(raw_line)
      if (
          m_close
          and m_close.group(1)[0] == fence_char
          and len(m_close.group(1)) >= fence_len
      ):
        fence_char = None
        fence_len = 0
  return False


def _validate_str_list(
    raw: Any, field_name: str, errors: List[str]
) -> List[str]:
  """Validates a required list of non-empty strings."""
  if not isinstance(raw, list):
    errors.append(f"Field '{field_name}' must be a list of strings.")
    return []
  cleaned: List[str] = []
  for idx, item in enumerate(raw):
    if not _is_non_empty_str(item):
      errors.append(
          f"Field '{field_name}[{idx}]' must be a non-empty string."
      )
    else:
      cleaned.append(item.strip())
  return cleaned


def validate_archaeology_payload(
    payload: Dict[str, Any],
) -> ReportValidationResult:
  """Validates a Target 15 ``archaeology`` JSON report payload.

  Args:
    payload: Raw candidate dictionary for ``15_archaeology_receipt.json``.

  Returns:
    ``ReportValidationResult`` with normalized payload or field errors.
  """
  if not isinstance(payload, dict):
    return ReportValidationResult(
        valid=False, errors=("Payload must be a JSON object.",)
    )
  errors: List[str] = []

  ticket_id = payload.get("ticket_id")
  if not _is_non_empty_str(ticket_id):
    errors.append("Field 'ticket_id' must be a non-empty string.")

  hosting_mode = payload.get("hosting_mode")
  if not isinstance(hosting_mode, str) or hosting_mode.strip() not in VALID_HOSTING_MODES:
    errors.append(
        "Field 'hosting_mode' must be 'GITHUB' or 'PURE_GIT'."
    )

  subprojects = _validate_str_list(
      payload.get("subprojects"), "subprojects", errors
  )

  raw_facts = payload.get("belnap_facts")
  belnap_facts: List[Dict[str, str]] = []
  if not isinstance(raw_facts, list):
    errors.append("Field 'belnap_facts' must be a list of fact objects.")
  else:
    for idx, item in enumerate(raw_facts):
      if not isinstance(item, dict):
        errors.append(f"Field 'belnap_facts[{idx}]' must be an object.")
        continue
      claim = item.get("claim")
      truth_state = item.get("truth_state")
      evidence = item.get("evidence")
      if not _is_non_empty_str(claim):
        errors.append(
            f"Field 'belnap_facts[{idx}].claim' must be a non-empty string."
        )
      if (
          not isinstance(truth_state, str)
          or truth_state.strip() not in VALID_BELNAP_STATES
      ):
        errors.append(
            f"Field 'belnap_facts[{idx}].truth_state' must be one of "
            f"{sorted(VALID_BELNAP_STATES)}."
        )
      if not _is_non_empty_str(evidence):
        errors.append(
            f"Field 'belnap_facts[{idx}].evidence' must be a non-empty string."
        )
      if (
          _is_non_empty_str(claim)
          and isinstance(truth_state, str)
          and truth_state.strip() in VALID_BELNAP_STATES
          and _is_non_empty_str(evidence)
      ):
        belnap_facts.append({
            "claim": claim.strip(),
            "truth_state": truth_state.strip(),
            "evidence": evidence.strip(),
        })

  raw_strategy = payload.get("testing_strategy")
  norm_strategy: Dict[str, Any] = {}
  if not isinstance(raw_strategy, dict):
    errors.append("Field 'testing_strategy' must be an object.")
  else:
    fm = raw_strategy.get("failure_mechanism")
    bb = raw_strategy.get("burden_balance")
    if not _is_non_empty_str(fm):
      errors.append(
          "Field 'testing_strategy.failure_mechanism' must be a non-empty string."
      )
    if not _is_non_empty_str(bb):
      errors.append(
          "Field 'testing_strategy.burden_balance' must be a non-empty string."
      )
    scenarios = _validate_str_list(
        raw_strategy.get("scenarios"), "testing_strategy.scenarios", errors
    )
    if _is_non_empty_str(fm) and _is_non_empty_str(bb):
      norm_strategy = {
          "failure_mechanism": fm.strip(),
          "burden_balance": bb.strip(),
          "scenarios": scenarios,
      }

  consulted_refs = _validate_str_list(
      payload.get("consulted_references"), "consulted_references", errors
  )

  raw_scars = payload.get("applicable_scars")
  applicable_scars: List[Dict[str, str]] = []
  if not isinstance(raw_scars, list):
    errors.append("Field 'applicable_scars' must be a list of scar objects.")
  else:
    for idx, item in enumerate(raw_scars):
      if not isinstance(item, dict):
        errors.append(f"Field 'applicable_scars[{idx}]' must be an object.")
        continue
      scar_id = item.get("id")
      probe = item.get("probe")
      if not _is_non_empty_str(scar_id):
        errors.append(
            f"Field 'applicable_scars[{idx}].id' must be a non-empty string."
        )
      if not _is_non_empty_str(probe):
        errors.append(
            f"Field 'applicable_scars[{idx}].probe' must be a non-empty string."
        )
      if _is_non_empty_str(scar_id) and _is_non_empty_str(probe):
        applicable_scars.append({
            "id": scar_id.strip(),
            "probe": probe.strip(),
        })

  raw_notes = payload.get("notes", "")
  if not isinstance(raw_notes, str):
    errors.append("Field 'notes' must be a string when provided.")

  if errors:
    return ReportValidationResult(valid=False, errors=tuple(errors))

  return ReportValidationResult(
      valid=True,
      normalized_payload={
          "ticket_id": ticket_id.strip(),
          "hosting_mode": hosting_mode.strip(),
          "subprojects": subprojects,
          "belnap_facts": belnap_facts,
          "testing_strategy": norm_strategy,
          "consulted_references": consulted_refs,
          "applicable_scars": applicable_scars,
          "notes": raw_notes.strip(),
      },
  )


def validate_tdd_report_payload(
    payload: Dict[str, Any],
) -> ReportValidationResult:
  """Validates a Target 30 ``tdd_report`` JSON report payload.

  Args:
    payload: Raw candidate dictionary for ``30_tdd_report.json``.

  Returns:
    ``ReportValidationResult`` with normalized payload or field errors.
  """
  if not isinstance(payload, dict):
    return ReportValidationResult(
        valid=False, errors=("Payload must be a JSON object.",)
    )
  errors: List[str] = []

  ticket_id = payload.get("ticket_id")
  if not _is_non_empty_str(ticket_id):
    errors.append("Field 'ticket_id' must be a non-empty string.")

  changed_files = _validate_str_list(
      payload.get("changed_files"), "changed_files", errors
  )

  raw_cmds = payload.get("test_commands_run")
  norm_cmds: List[Dict[str, Any]] = []
  if not isinstance(raw_cmds, list):
    errors.append("Field 'test_commands_run' must be a list of command entries.")
  else:
    for idx, item in enumerate(raw_cmds):
      if not isinstance(item, dict):
        errors.append(f"Field 'test_commands_run[{idx}]' must be an object.")
        continue
      cmd = item.get("command")
      exit_code = item.get("exit_code")
      phase = item.get("phase")
      if not _is_non_empty_str(cmd):
        errors.append(
            f"Field 'test_commands_run[{idx}].command' must be a non-empty string."
        )
      if not _is_non_bool_int(exit_code):
        errors.append(
            f"Field 'test_commands_run[{idx}].exit_code' must be an integer."
        )
      if not isinstance(phase, str) or phase.strip() not in VALID_TDD_PHASES:
        errors.append(
            f"Field 'test_commands_run[{idx}].phase' must be one of "
            f"{sorted(VALID_TDD_PHASES)}."
        )
      if (
          _is_non_empty_str(cmd)
          and _is_non_bool_int(exit_code)
          and isinstance(phase, str)
          and phase.strip() in VALID_TDD_PHASES
      ):
        norm_cmds.append({
            "command": cmd.strip(),
            "exit_code": exit_code,
            "phase": phase.strip(),
        })

  applied_scars = _validate_str_list(
      payload.get("applied_scars"), "applied_scars", errors
  )

  summary = payload.get("summary")
  if not _is_non_empty_str(summary):
    errors.append("Field 'summary' must be a non-empty string.")

  if errors:
    return ReportValidationResult(valid=False, errors=tuple(errors))

  return ReportValidationResult(
      valid=True,
      normalized_payload={
          "ticket_id": ticket_id.strip(),
          "changed_files": changed_files,
          "test_commands_run": norm_cmds,
          "applied_scars": applied_scars,
          "summary": summary.strip(),
      },
  )


def validate_critic_review_payload(
    payload: Dict[str, Any],
) -> ReportValidationResult:
  """Validates a Target 40 ``critic_review`` JSON report payload.

  Enforces positive integer ``round >= 1``, binary ``verdict``
  (``"APPROVED"`` or ``"REJECTED"``), non-empty unfenced prose across all six
  canonical pillars, structured blockers/observations, and exact blocker-count
  parity with ``verdict``.

  Args:
    payload: Raw candidate dictionary for ``40_critic_review_round<N>.json``.

  Returns:
    ``ReportValidationResult`` with normalized payload or field errors.
  """
  if not isinstance(payload, dict):
    return ReportValidationResult(
        valid=False, errors=("Payload must be a JSON object.",)
    )
  errors: List[str] = []

  round_num = payload.get("round")
  if not _is_non_bool_int(round_num) or round_num < 1:
    errors.append("Field 'round' must be a positive integer (>= 1).")

  verdict = payload.get("verdict")
  if not isinstance(verdict, str) or verdict.strip() not in VALID_CRITIC_VERDICTS:
    errors.append(
        "Field 'verdict' must be binary 'APPROVED' or 'REJECTED' "
        "(soft verdict tokens are prohibited)."
    )
  clean_verdict = verdict.strip() if isinstance(verdict, str) else ""

  raw_pillars = payload.get("pillars")
  norm_pillars: Dict[str, str] = {}
  if not isinstance(raw_pillars, dict):
    errors.append("Field 'pillars' must be an object with all 6 canonical pillars.")
  else:
    for key, heading in PILLAR_KEY_ORDER:
      val = raw_pillars.get(key)
      if not _is_non_empty_str(val):
        errors.append(
            f"Field 'pillars.{key}' ({heading}) must be a non-empty prose string."
        )
      elif not _has_unfenced_alnum_prose(val):
        errors.append(
            f"Field 'pillars.{key}' ({heading}) must contain non-empty unfenced prose."
        )
      else:
        norm_pillars[key] = val.strip()

  raw_summaries = payload.get("pillar_summaries")
  norm_summaries: Dict[str, str] = {}
  if raw_summaries is not None:
    if not isinstance(raw_summaries, dict):
      errors.append("Field 'pillar_summaries' must be an object when provided.")
    else:
      for k, v in raw_summaries.items():
        if not _is_non_empty_str(k) or not _is_non_empty_str(v):
          errors.append(
              "Entries in 'pillar_summaries' must map non-empty strings to non-empty strings."
          )
        else:
          norm_summaries[k.strip()] = v.strip()

  raw_blockers = payload.get("blockers")
  norm_blockers: List[Dict[str, str]] = []
  if not isinstance(raw_blockers, list):
    errors.append("Field 'blockers' must be a list of blocker objects.")
  else:
    req_blocker_fields = (
        "id",
        "pillar",
        "location",
        "wound",
        "trap",
        "diagnostic_invariant",
    )
    for idx, item in enumerate(raw_blockers):
      if not isinstance(item, dict):
        errors.append(f"Field 'blockers[{idx}]' must be an object.")
        continue
      entry: Dict[str, str] = {}
      entry_ok = True
      for fname in req_blocker_fields:
        fval = item.get(fname)
        if not _is_non_empty_str(fval):
          errors.append(
              f"Field 'blockers[{idx}].{fname}' must be a non-empty string."
          )
          entry_ok = False
        else:
          entry[fname] = fval.strip()
      if entry_ok:
        norm_blockers.append(entry)

  raw_nbos = payload.get("non_blocking_observations")
  norm_nbos: List[Dict[str, str]] = []
  if not isinstance(raw_nbos, list):
    errors.append(
        "Field 'non_blocking_observations' must be a list of observation objects."
    )
  else:
    req_nbo_fields = (
        "id",
        "pillar",
        "location",
        "relationship",
        "disposition",
        "summary",
    )
    for idx, item in enumerate(raw_nbos):
      if not isinstance(item, dict):
        errors.append(
            f"Field 'non_blocking_observations[{idx}]' must be an object."
        )
        continue
      entry = {}
      entry_ok = True
      for fname in req_nbo_fields:
        fval = item.get(fname)
        if not _is_non_empty_str(fval):
          errors.append(
              f"Field 'non_blocking_observations[{idx}].{fname}' must be a non-empty string."
          )
          entry_ok = False
        else:
          entry[fname] = fval.strip()
      if entry_ok:
        norm_nbos.append(entry)

  if clean_verdict in VALID_CRITIC_VERDICTS and isinstance(raw_blockers, list):
    if clean_verdict == "APPROVED" and len(raw_blockers) != 0:
      errors.append(
          f"Contradictory verdict: APPROVED requires 0 blockers, got {len(raw_blockers)}."
      )
    elif clean_verdict == "REJECTED" and len(raw_blockers) < 1:
      errors.append(
          "Contradictory verdict: REJECTED requires at least 1 blocker."
      )

  if errors:
    return ReportValidationResult(valid=False, errors=tuple(errors))

  normalized: Dict[str, Any] = {
      "round": round_num,
      "verdict": clean_verdict,
      "pillars": norm_pillars,
      "blockers": norm_blockers,
      "non_blocking_observations": norm_nbos,
  }
  if norm_summaries:
    normalized["pillar_summaries"] = norm_summaries
  return ReportValidationResult(valid=True, normalized_payload=normalized)


def validate_bot_triage_payload(
    payload: Dict[str, Any],
) -> ReportValidationResult:
  """Validates a Target 70 ``bot_triage`` JSON report payload.

  Enforces ``ready_to_land == true`` only when ``ci_status == "PASSING"``,
  ``quiescence_satisfied == true``, and zero unresolved ``"Accept"`` findings
  exist.

  Args:
    payload: Raw candidate dictionary for ``70_bot_triage.json``.

  Returns:
    ``ReportValidationResult`` with normalized payload or field errors.
  """
  if not isinstance(payload, dict):
    return ReportValidationResult(
        valid=False, errors=("Payload must be a JSON object.",)
    )
  errors: List[str] = []

  hosting_mode = payload.get("hosting_mode")
  if not isinstance(hosting_mode, str) or hosting_mode.strip() not in VALID_HOSTING_MODES:
    errors.append("Field 'hosting_mode' must be 'GITHUB' or 'PURE_GIT'.")

  pr_number = payload.get("pr_number")
  if pr_number is not None and (not _is_non_bool_int(pr_number) or pr_number < 1):
    errors.append("Field 'pr_number' must be a positive integer (>= 1) or null.")

  ci_status = payload.get("ci_status")
  if not isinstance(ci_status, str) or ci_status.strip() not in VALID_CI_STATUSES:
    errors.append(
        f"Field 'ci_status' must be one of {sorted(VALID_CI_STATUSES)}."
    )
  clean_ci = ci_status.strip() if isinstance(ci_status, str) else ""

  quiescence = payload.get("quiescence_satisfied")
  if not isinstance(quiescence, bool):
    errors.append("Field 'quiescence_satisfied' must be a boolean.")

  ready_to_land = payload.get("ready_to_land")
  if not isinstance(ready_to_land, bool):
    errors.append("Field 'ready_to_land' must be a boolean.")

  raw_checks = payload.get("checks")
  norm_checks: List[Dict[str, str]] = []
  if not isinstance(raw_checks, list):
    errors.append("Field 'checks' must be a list of check objects.")
  else:
    for idx, item in enumerate(raw_checks):
      if not isinstance(item, dict):
        errors.append(f"Field 'checks[{idx}]' must be an object.")
        continue
      name = item.get("name")
      status = item.get("status")
      details = item.get("details", "")
      if not _is_non_empty_str(name):
        errors.append(f"Field 'checks[{idx}].name' must be a non-empty string.")
      if not _is_non_empty_str(status):
        errors.append(
            f"Field 'checks[{idx}].status' must be a non-empty string."
        )
      if not isinstance(details, str):
        errors.append(f"Field 'checks[{idx}].details' must be a string.")
      if (
          _is_non_empty_str(name)
          and _is_non_empty_str(status)
          and isinstance(details, str)
      ):
        norm_checks.append({
            "name": name.strip(),
            "status": status.strip(),
            "details": details.strip(),
        })

  raw_findings = payload.get("findings")
  norm_findings: List[Dict[str, Any]] = []
  if not isinstance(raw_findings, list):
    errors.append("Field 'findings' must be a list of finding objects.")
  else:
    for idx, item in enumerate(raw_findings):
      if not isinstance(item, dict):
        errors.append(f"Field 'findings[{idx}]' must be an object.")
        continue
      source = item.get("source")
      location = item.get("location")
      summary = item.get("summary")
      action = item.get("action")
      rationale = item.get("rationale")
      resolved = item.get("resolved", False)
      entry_ok = True
      for fname, fval in (
          ("source", source),
          ("location", location),
          ("summary", summary),
          ("rationale", rationale),
      ):
        if not _is_non_empty_str(fval):
          errors.append(
              f"Field 'findings[{idx}].{fname}' must be a non-empty string."
          )
          entry_ok = False
      if not isinstance(action, str) or action.strip() not in VALID_TRIAGE_ACTIONS:
        errors.append(
            f"Field 'findings[{idx}].action' must be one of "
            f"{sorted(VALID_TRIAGE_ACTIONS)}."
        )
        entry_ok = False
      if not isinstance(resolved, bool):
        errors.append(f"Field 'findings[{idx}].resolved' must be a boolean.")
        entry_ok = False
      if entry_ok:
        norm_findings.append({
            "source": source.strip(),
            "location": location.strip(),
            "summary": summary.strip(),
            "action": action.strip(),
            "rationale": rationale.strip(),
            "resolved": resolved,
        })

  if ready_to_land is True:
    if clean_ci != "PASSING":
      errors.append(
          f"Contradictory ready_to_land: true requires ci_status == 'PASSING', got '{clean_ci}'."
      )
    if quiescence is not True:
      errors.append(
          "Contradictory ready_to_land: true requires quiescence_satisfied == true."
      )
    unresolved_accepts = [
        f
        for f in norm_findings
        if f["action"] == "Accept" and not f["resolved"]
    ]
    if unresolved_accepts:
      errors.append(
          f"Contradictory ready_to_land: {len(unresolved_accepts)} unresolved "
          "'Accept' finding(s) remain."
      )

  if errors:
    return ReportValidationResult(valid=False, errors=tuple(errors))

  return ReportValidationResult(
      valid=True,
      normalized_payload={
          "hosting_mode": hosting_mode.strip(),
          "pr_number": pr_number,
          "ci_status": clean_ci,
          "quiescence_satisfied": quiescence,
          "checks": norm_checks,
          "findings": norm_findings,
          "ready_to_land": ready_to_land,
      },
  )


class ReportRegistry:
  """Extensible registry mapping report type names to ``ReportSpec`` strategies.

  Keeps the core validation/persistence spine minimal so future tickets (such
  as GH-270 ``"plan"``) register new ``ReportSpec`` entries at the periphery
  without modifying ``StateReportStore`` or ``mcp/server.py`` (SCAR-API-0006).
  """

  def __init__(self, specs: Optional[Tuple[ReportSpec, ...]] = None) -> None:
    """Initializes the registry with optional initial report specifications.

    Args:
      specs: Optional tuple of ``ReportSpec`` entries to register.
    """
    self._specs: Dict[str, ReportSpec] = {}
    for spec in specs or ():
      self.register(spec)

  def register(self, spec: ReportSpec) -> None:
    """Registers a new ``ReportSpec`` under its ``report_type`` key.

    Args:
      spec: Frozen ``ReportSpec`` describing validation and rendering.

    Raises:
      ValueError: If ``spec.report_type`` is empty or already registered.
    """
    key = (spec.report_type or "").strip()
    if not key:
      raise ValueError("ReportSpec.report_type must be a non-empty string.")
    if key in self._specs:
      raise ValueError(f"Report type {key!r} is already registered.")
    self._specs[key] = spec

  def get(self, report_type: str) -> Optional[ReportSpec]:
    """Looks up a registered ``ReportSpec`` by report type name.

    Args:
      report_type: Canonical report type identifier.

    Returns:
      Matching ``ReportSpec`` or None if unregistered.
    """
    if not isinstance(report_type, str):
      return None
    return self._specs.get(report_type.strip())

  def supported_types(self) -> Tuple[str, ...]:
    """Returns registered report type identifiers in registration order.

    Returns:
      Tuple of canonical report type names.
    """
    return tuple(self._specs.keys())


def create_default_registry() -> ReportRegistry:
  """Creates a fresh ``ReportRegistry`` populated with canonical GH-281 specs.

  Returns:
    ``ReportRegistry`` containing ``archaeology``, ``tdd_report``,
    ``critic_review``, and ``bot_triage``.
  """
  return ReportRegistry((
      ReportSpec(
          report_type="archaeology",
          json_filename_template=ARCHAEOLOGY_JSON_FILENAME,
          md_filename_template=ARCHAEOLOGY_MD_FILENAME,
          validator=validate_archaeology_payload,
          renderer=render_archaeology_markdown,
      ),
      ReportSpec(
          report_type="tdd_report",
          json_filename_template=TDD_REPORT_JSON_FILENAME,
          md_filename_template=TDD_REPORT_MD_FILENAME,
          validator=validate_tdd_report_payload,
          renderer=render_tdd_report_markdown,
      ),
      ReportSpec(
          report_type="critic_review",
          json_filename_template="40_critic_review_round{round}.json",
          md_filename_template="40_critic_review_round{round}.md",
          validator=validate_critic_review_payload,
          renderer=render_critic_review_markdown,
          requires_round=True,
      ),
      ReportSpec(
          report_type="bot_triage",
          json_filename_template=BOT_TRIAGE_JSON_FILENAME,
          md_filename_template=BOT_TRIAGE_MD_FILENAME,
          validator=validate_bot_triage_payload,
          renderer=render_bot_triage_markdown,
      ),
  ))


DEFAULT_REPORT_REGISTRY: ReportRegistry = create_default_registry()


class StateReportStore:
  """Atomic dual-writer repository for canonical JSON reports and `.md` views.

  Attributes:
    state_dir: Path to the ``.workflow/state`` directory.
    registry: ``ReportRegistry`` providing validation and rendering strategies.
  """

  def __init__(
      self,
      state_dir: str,
      registry: Optional[ReportRegistry] = None,
  ) -> None:
    """Initializes the report store for a workflow state directory.

    Args:
      state_dir: Path to ``.workflow/state``.
      registry: Optional ``ReportRegistry`` (defaults to ``DEFAULT_REPORT_REGISTRY``).
    """
    self.state_dir: str = state_dir
    self.registry: ReportRegistry = (
        registry if registry is not None else DEFAULT_REPORT_REGISTRY
    )

  @classmethod
  def from_repo_root(
      cls,
      repo_root: str,
      registry: Optional[ReportRegistry] = None,
  ) -> "StateReportStore":
    """Constructs a report store anchored at ``<repo_root>/.workflow/state``.

    Args:
      repo_root: Repository root directory path.
      registry: Optional ``ReportRegistry`` override.

    Returns:
      ``StateReportStore`` pointing to the repository state directory.
    """
    return cls(
        os.path.join(os.path.abspath(repo_root), ".workflow", "state"),
        registry=registry,
    )

  def submit(
      self, report_type: str, payload: Dict[str, Any]
  ) -> ReportSubmissionResult:
    """Validates a report payload and atomically writes `.json` and `.md` files.

    Rejects unknown report types or invalid/contradictory payloads before
    creating directories or writing any bytes to disk.

    Args:
      report_type: Registered report type name.
      payload: Structured JSON report dictionary.

    Returns:
      ``ReportSubmissionResult`` with written paths on success or errors on failure.
    """
    spec = self.registry.get(report_type)
    if spec is None:
      supported = ", ".join(self.registry.supported_types())
      return ReportSubmissionResult(
          ok=False,
          report_type=str(report_type or ""),
          errors=(
              f"Unsupported reportType {report_type!r}. Supported: {supported}.",
          ),
      )

    validation = spec.validator(payload)
    if not validation.valid or validation.normalized_payload is None:
      return ReportSubmissionResult(
          ok=False,
          report_type=spec.report_type,
          errors=validation.errors or ("Report validation failed.",),
      )

    normalized = validation.normalized_payload
    json_name, md_name = spec.resolve_filenames(normalized)
    json_text = json.dumps(normalized, indent=2) + "\n"
    md_text = spec.renderer(normalized)

    json_path = os.path.abspath(os.path.join(self.state_dir, json_name))
    md_path = os.path.abspath(os.path.join(self.state_dir, md_name))

    json_tmp: Optional[str] = None
    md_tmp: Optional[str] = None
    json_replaced = False
    md_replaced = False
    try:
      json_tmp = stage_atomic_text(
          json_path, json_text, prefix=".tmp_report_", suffix=".json"
      )
      md_tmp = stage_atomic_text(
          md_path, md_text, prefix=".tmp_report_", suffix=".md"
      )
      os.replace(json_tmp, json_path)
      json_replaced = True
      os.replace(md_tmp, md_path)
      md_replaced = True
    except (OSError, UnicodeEncodeError) as exc:
      return ReportSubmissionResult(
          ok=False,
          report_type=spec.report_type,
          errors=(f"Failed to persist report atomically: {exc}",),
      )
    finally:
      if json_tmp is not None and not json_replaced:
        try:
          os.unlink(json_tmp)
        except OSError:
          pass
      if md_tmp is not None and not md_replaced:
        try:
          os.unlink(md_tmp)
        except OSError:
          pass

    return ReportSubmissionResult(
        ok=True,
        report_type=spec.report_type,
        json_path=json_path,
        md_path=md_path,
    )


def load_json_report(
    state_dir: str, filename: str
) -> Optional[Dict[str, Any]]:
  """Loads a JSON object report from ``state_dir/filename`` safely.

  Args:
    state_dir: Path to ``.workflow/state``.
    filename: Report JSON filename inside ``state_dir``.

  Returns:
    Parsed dictionary when file exists and is valid JSON object, else None.
  """
  if not state_dir or not filename:
    return None
  target = os.path.join(state_dir, filename)
  if not os.path.isfile(target):
    return None
  try:
    with open(target, "r", encoding="utf-8") as f:
      data = json.load(f)
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
    return None
  return data if isinstance(data, dict) else None


def load_archaeology_hosting_mode(state_dir: str) -> Optional[str]:
  """Reads canonical ``hosting_mode`` from ``15_archaeology_receipt.json``.

  Args:
    state_dir: Path to ``.workflow/state``.

  Returns:
    ``"GITHUB"`` or ``"PURE_GIT"`` when present and valid in canonical JSON,
    otherwise None.
  """
  data = load_json_report(state_dir, ARCHAEOLOGY_JSON_FILENAME)
  if data is None:
    return None
  mode = data.get("hosting_mode")
  if isinstance(mode, str):
    upper = mode.strip().upper()
    if upper in VALID_HOSTING_MODES:
      return upper
  return None


def load_critic_review_validation(
    state_dir: str, round_number: Optional[int] = None
) -> Optional[ReportValidationResult]:
  """Loads and validates a canonical or round critic review JSON file.

  Args:
    state_dir: Path to ``.workflow/state``.
    round_number: Optional positive round number. When None, reads
      ``40_critic_review.json``; otherwise ``40_critic_review_round<N>.json``.

  Returns:
    None when the target JSON file does not exist; otherwise a
    ``ReportValidationResult`` capturing validity, normalized dict, or exact
    parse/schema errors.
  """
  if not state_dir:
    return None
  filename = (
      f"40_critic_review_round{round_number}.json"
      if round_number is not None
      else CRITIC_REVIEW_JSON_FILENAME
  )
  target = os.path.join(state_dir, filename)
  if not os.path.isfile(target):
    return None
  try:
    with open(target, "r", encoding="utf-8") as f:
      data = json.load(f)
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
    return ReportValidationResult(
        valid=False,
        errors=(f"Malformed JSON in {filename}: {exc}",),
    )
  if not isinstance(data, dict):
    return ReportValidationResult(
        valid=False,
        errors=(f"Root JSON value in {filename} must be an object.",),
    )
  val = validate_critic_review_payload(data)
  if (
      val.valid
      and val.normalized_payload is not None
      and round_number is not None
      and val.normalized_payload.get("round") != round_number
  ):
    return ReportValidationResult(
        valid=False,
        errors=(
            f"Round mismatch in {filename}: payload round "
            f"{val.normalized_payload.get('round')} does not match requested round {round_number}.",
        ),
    )
  return val


def load_critic_review(
    state_dir: str, round_number: Optional[int] = None
) -> Optional[Dict[str, Any]]:
  """Loads and validates a critic review JSON report, returning its dict on success.

  Args:
    state_dir: Path to ``.workflow/state``.
    round_number: Optional round number (defaults to canonical
      ``40_critic_review.json``).

  Returns:
    Validated normalized critic review dictionary, or None when absent or invalid.
  """
  res = load_critic_review_validation(state_dir, round_number=round_number)
  if res is None or not res.valid:
    return None
  return res.normalized_payload
