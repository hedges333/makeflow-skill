"""Target 70 session parking and resume gate helpers (GH-47, GH-274).

Executes deterministic ``park-session`` and ``resume-parked`` gates using frozen
parameter dataclasses (SCAR-ARCH-0014) and canonical ``GateFailure`` payloads
from ``lib.reflex`` without importing ``lib.workflow_gates`` (SCAR-ARCH-08).
"""

from dataclasses import dataclass, replace as dataclass_replace
import os
from typing import Any, Dict, List, Optional, Tuple

from lib.reflex import GateFailure


@dataclass(frozen=True)
class ParkSessionRequest:
  """Typed parameter object for the Target 70 park-session gate (SCAR-ARCH-0014).

  Attributes:
    repo_root: Workspace Git repository root directory.
    pr: Pull request number to poll or record.
    pr_url: Pull request URL recorded in explicit status mode.
    branch: Optional head branch override for parking state.
    reason: Optional human-readable parking reason override.
    ci_status: Explicit CI status string when skipping live polling.
    review_decision: Explicit review decision string when skipping live polling.
    approvals: Explicit approval count recorded in parking state.
    required_approvals: Explicit required approval count recorded in parking state.
    require_codeowners: True when CODEOWNERS approval is required.
    pending_reviewers: Comma-separated pending reviewer logins.
    changes_requested_by: Comma-separated logins of reviewers who requested changes.
    unresolved_threads: Count of unresolved review threads on the pull request.
    cron: Optional cron expression override for resume reminders.
    has_explicit_status: True when callers pass explicit status flags instead of live polling.
  """

  repo_root: str
  pr: int = 0
  pr_url: str = ""
  branch: str = ""
  reason: str = ""
  ci_status: str = ""
  review_decision: str = ""
  approvals: int = 0
  required_approvals: int = 0
  require_codeowners: bool = False
  pending_reviewers: str = ""
  changes_requested_by: str = ""
  unresolved_threads: int = 0
  cron: str = ""
  has_explicit_status: bool = False


@dataclass(frozen=True)
class ResumeParkedRequest:
  """Typed parameter object for the Target 70 resume-parked gate (SCAR-ARCH-0014).

  Attributes:
    repo_root: Workspace Git repository root directory.
    pr: Optional pull request number override.
    branch: Optional expected head branch override.
  """

  repo_root: str
  pr: int = 0
  branch: str = ""


@dataclass(frozen=True)
class ParkingGateOutcome:
  """Structured outcome returned by parking gate domain runners.

  Attributes:
    ok: True when the parking gate succeeds and False when it fails closed.
    json_payload: Optional JSON dictionary emitted in JSON output mode.
    text_output: Human-readable receipt or guidance emitted in text mode.
    failure: Populated ``GateFailure`` when ``ok`` is False without an advance error.
    advance_error: State-machine transition error forwarded to ``_emit_advance_reflex``.
  """

  ok: bool
  json_payload: Optional[Dict[str, Any]] = None
  text_output: str = ""
  failure: Optional[GateFailure] = None
  advance_error: str = ""


def _split_csv_tuple(raw: str) -> Tuple[str, ...]:
  """Splits a comma-separated string into a deduplicated tuple of tokens."""
  items: List[str] = []
  for part in (raw or "").split(","):
    cleaned = part.strip()
    if cleaned and cleaned not in items:
      items.append(cleaned)
  return tuple(items)


def run_park_session_gate(request: ParkSessionRequest) -> ParkingGateOutcome:
  """Persists Target 70 PARKED state and prepares a Parking Receipt outcome.

  Args:
    request: Frozen ``ParkSessionRequest`` describing the workspace and PR
      metadata or explicit status overrides.

  Returns:
    ``ParkingGateOutcome`` containing either the persisted JSON payload and
    rendered Parking Receipt text, or a structured ``GateFailure``.
  """
  from lib.ci_polling import poll_ci_status
  from lib.config import resolve_workflow_config
  from lib.parking_state import (
      ParkingState,
      ParkingStateStore,
      _utc_now_iso,
      render_parked_triage_card,
      render_parking_receipt,
  )
  from lib.state_machine import (
      _detect_git_branch,
      _restore_active_session_state,
      infer_current_target,
  )

  state_dir = os.path.join(request.repo_root, ".workflow", "state")
  os.makedirs(state_dir, exist_ok=True)
  current = infer_current_target(state_dir)
  if current != "T70":
    return ParkingGateOutcome(
        ok=False,
        failure=GateFailure(
            scar_id="PROC-102",
            code="PARK_REQUIRES_T70",
            trigger=f"Cannot park session from active target {current}.",
            rejected="Entering PARKED sub-state outside Target 70 (bot-quiescence).",
            reflex="Advance to Target 70 (bot-quiescence) before running park-session.",
        ),
    )

  cfg = resolve_workflow_config(request.repo_root)
  ci_cfg = cfg.get("ci") if isinstance(cfg.get("ci"), dict) else {}
  cron_expr = request.cron or str(ci_cfg.get("parkingCronExpression") or "")
  head_branch = request.branch or _detect_git_branch(request.repo_root)
  now_iso = _utc_now_iso()
  existing = ParkingStateStore(state_dir).load()
  parked_at = (
      existing.parked_at
      if existing is not None and existing.parked_at
      else now_iso
  )
  eff_pr = request.pr or (existing.pr_number if existing is not None else 0)

  if eff_pr > 0 and not request.has_explicit_status:
    poll_res = poll_ci_status(
        pr_number=eff_pr,
        ws=request.repo_root,
        max_wait_sec=1,
        expected_branch=request.branch or None,
    )
    if poll_res.get("mode") == "github_error" or (
        str(poll_res.get("ciStatus") or "").upper() == "ERROR"
    ):
      return ParkingGateOutcome(
          ok=False,
          failure=GateFailure(
              scar_id="TEST-24",
              code="PARK_POLL_ERROR",
              trigger=str(
                  poll_res.get("message")
                  or "Failed to query PR status for parking."
              ),
              rejected="Writing incomplete parking state when PR status polling fails.",
              reflex=(
                  "Verify gh authentication, PR number, and branch match, or "
                  "supply explicit status flags before retrying park-session."
              ),
          ),
      )
    hydrated = ParkingState.from_poll_result(
        poll_res,
        pr_number=eff_pr,
        head_branch=head_branch,
        cron_expression=cron_expr,
        parked_at=parked_at,
        last_checked_at=now_iso,
    )
    overrides: Dict[str, Any] = {}
    if request.branch:
      overrides["head_branch"] = request.branch
    if request.reason:
      overrides["reason"] = request.reason
    state = dataclass_replace(hydrated, **overrides) if overrides else hydrated
  else:
    state = ParkingState(
        parked=True,
        pr_number=max(0, eff_pr),
        pr_url=request.pr_url,
        head_branch=head_branch,
        reason=request.reason or "Awaiting external peer review or CI completion",
        ci_status=request.ci_status or "PENDING",
        review_decision=request.review_decision,
        approvals_count=max(0, request.approvals),
        required_approvals=max(0, request.required_approvals),
        require_code_owner_reviews=request.require_codeowners,
        pending_reviewers=_split_csv_tuple(request.pending_reviewers),
        changes_requested_by=_split_csv_tuple(request.changes_requested_by),
        unresolved_threads_count=max(0, request.unresolved_threads),
        parked_at=parked_at,
        last_checked_at=now_iso,
        cron_expression=cron_expr,
    )

  store = ParkingStateStore(state_dir)
  store.save(state)
  with open(
      os.path.join(state_dir, "70_bot_triage.md"), "w", encoding="utf-8"
  ) as f:
    f.write(render_parked_triage_card(state))
  _restore_active_session_state(state_dir, "T70", request.repo_root)

  return ParkingGateOutcome(
      ok=True,
      json_payload=state.to_dict(),
      text_output=render_parking_receipt(state),
  )


def run_resume_parked_gate(request: ResumeParkedRequest) -> ParkingGateOutcome:
  """Re-checks a parked PR once and advances to T75, guides T30, or stays parked.

  Args:
    request: Frozen ``ResumeParkedRequest`` holding the workspace root and
      optional PR number or branch overrides.

  Returns:
    ``ParkingGateOutcome`` containing the JSON payload and human-readable
    guidance, a structured ``GateFailure``, or an ``advance_error`` string.
  """
  from lib.ci_polling import poll_ci_status
  from lib.parking_state import (
      ParkingState,
      ParkingStateStore,
      _utc_now_iso,
      render_parked_triage_card,
      render_parking_receipt,
  )
  from lib.state_machine import (
      _restore_active_session_state,
      advance_workflow_target,
      infer_current_target,
  )

  state_dir = os.path.join(request.repo_root, ".workflow", "state")
  current = infer_current_target(state_dir)
  if current != "T70":
    return ParkingGateOutcome(
        ok=False,
        failure=GateFailure(
            scar_id="PROC-102",
            code="RESUME_REQUIRES_T70",
            trigger=f"Cannot resume parked session from active target {current}.",
            rejected="Running resume-parked outside Target 70 (bot-quiescence).",
            reflex=(
                "Inspect active target via 'make -f .workflow/state/Makefile "
                "read-init-d' before running resume-parked."
            ),
        ),
    )

  store = ParkingStateStore(state_dir)
  existing = store.load()
  if existing is None or not existing.parked:
    return ParkingGateOutcome(
        ok=False,
        failure=GateFailure(
            scar_id="PROC-102",
            code="NOT_PARKED",
            trigger="No active PARKED state found in .workflow/state/70_parking_state.json.",
            rejected="Running resume-parked when the workflow session is not parked.",
            reflex="Run 'make -f .workflow/state/Makefile read-init-d' to inspect active workflow state.",
        ),
    )

  pr_num = request.pr or existing.pr_number
  expected_branch = request.branch or existing.head_branch or None
  poll_res = poll_ci_status(
      pr_number=pr_num,
      ws=request.repo_root,
      max_wait_sec=1,
      expected_branch=expected_branch,
  )
  ci_status = str(poll_res.get("ciStatus") or "").upper()
  is_transient_error = poll_res.get("mode") == "github_error" or (
      ci_status == "ERROR" and not bool(poll_res.get("branchMismatch"))
  )
  if is_transient_error:
    return ParkingGateOutcome(
        ok=False,
        failure=GateFailure(
            scar_id="TEST-24",
            code="PARKED_POLL_ERROR",
            trigger=str(
                poll_res.get("message")
                or "Transient error polling parked PR status."
            ),
            rejected="Overwriting persisted 70_parking_state.json during a transient polling failure.",
            reflex=(
                "Check GitHub CLI authentication or network connectivity, then "
                "retry 'make -f .workflow/state/Makefile resume-parked'."
            ),
        ),
    )

  triage_path = os.path.join(state_dir, "70_bot_triage.md")

  pr_state = str(poll_res.get("prState") or "").upper()
  if pr_state in ("MERGED", "CLOSED"):
    store.clear()
    triage_md = (
        "# Target 70: Bot Quiescence and Peer Review Triage\n\n"
        f"- Status: PR_{pr_state}\n"
        f"- Pull Request: #{pr_num}\n"
        f"- PR State: `{pr_state}`\n"
        f"- Summary: {poll_res.get('message', f'Pull request #{pr_num} is {pr_state}.')}\n"
    )
    with open(triage_path, "w", encoding="utf-8") as f:
      f.write(triage_md)
    _restore_active_session_state(state_dir, "T70", request.repo_root)
    guidance = (
        f"PR_{pr_state}: Parking cleared because PR #{pr_num} is already {pr_state}. "
        + (
            "Sync main and complete Target 80/90 cleanup without re-merging."
            if pr_state == "MERGED"
            else "Inspect the closed PR or return to Target 30 before continuing."
        )
    )
    return ParkingGateOutcome(
        ok=True,
        json_payload={
            "action": f"PR_{pr_state}",
            "message": guidance,
            "poll": poll_res,
        },
        text_output=guidance + "\n",
    )

  if poll_res.get("readyToLand"):
    store.clear()
    approvals = poll_res.get("approvalsCount", 0)
    req_approvals = poll_res.get("requiredApprovals", 0)
    triage_md = (
        "# Target 70: Bot Quiescence and Peer Review Triage\n\n"
        "- Status: READY_FOR_LANDING_GATE\n"
        f"- Pull Request: #{pr_num}\n"
        f"- CI Status: `{poll_res.get('ciStatus', 'SUCCESS')}`\n"
        f"- Approvals: {approvals}/{req_approvals}\n"
        f"- Review Decision: `{poll_res.get('reviewDecision') or 'NONE'}`\n"
        f"- Summary: {poll_res.get('message', 'Ready for Target 75.')}\n"
    )
    with open(triage_path, "w", encoding="utf-8") as f:
      f.write(triage_md)
    ok, adv_msg = advance_workflow_target(
        "T70",
        "T75",
        evidence={},
        workspace_dir=request.repo_root,
        state_dir=state_dir,
    )
    if not ok:
      return ParkingGateOutcome(ok=False, advance_error=adv_msg)
    return ParkingGateOutcome(
        ok=True,
        json_payload={
            "action": "ADVANCED_T75",
            "message": adv_msg,
            "poll": poll_res,
        },
        text_output=(
            f"{adv_msg}\n"
            "Session resumed from PARKED -> Target 75 (ready-to-land). "
            "Pause for explicit human-landing-approval before Target 80.\n"
        ),
    )

  session_status = str(poll_res.get("sessionStatus") or "").upper()
  is_remediation = (
      session_status == "REMEDIATION_REQUIRED"
      or ci_status == "FAILURE"
      or bool(poll_res.get("branchMismatch"))
  )
  if is_remediation:
    store.clear()
    triage_md = (
        "# Target 70: Bot Quiescence and Peer Review Triage\n\n"
        "- Status: REMEDIATION_REQUIRED\n"
        f"- Pull Request: #{pr_num}\n"
        f"- CI Status: `{ci_status}`\n"
        f"- Review Decision: `{poll_res.get('reviewDecision') or 'NONE'}`\n"
        f"- Changes Requested By: {poll_res.get('changesRequestedBy') or []}\n"
        f"- Failed Checks: {poll_res.get('failedChecks') or []}\n"
        f"- Summary: {poll_res.get('message', 'Remediation required.')}\n"
    )
    with open(triage_path, "w", encoding="utf-8") as f:
      f.write(triage_md)
    _restore_active_session_state(state_dir, "T70", request.repo_root)
    guidance = (
        "REMEDIATION_REQUIRED: Parking cleared. "
        f"Reason: {poll_res.get('message', 'Changes or fixes required.')} "
        "Run `make -f .workflow/state/Makefile implementation-diff FROM=T70` "
        "to return to Target 30."
    )
    return ParkingGateOutcome(
        ok=True,
        json_payload={
            "action": "REMEDIATION_REQUIRED",
            "message": guidance,
            "poll": poll_res,
        },
        text_output=guidance + "\n",
    )

  refreshed = ParkingState.from_poll_result(
      poll_res,
      pr_number=pr_num,
      head_branch=expected_branch or existing.head_branch,
      cron_expression=existing.cron_expression,
      parked_at=existing.parked_at,
      last_checked_at=_utc_now_iso(),
  )
  store.save(refreshed)
  with open(triage_path, "w", encoding="utf-8") as f:
    f.write(render_parked_triage_card(refreshed))
  _restore_active_session_state(state_dir, "T70", request.repo_root)
  return ParkingGateOutcome(
      ok=True,
      json_payload={
          "action": "REMAIN_PARKED",
          "parkingState": refreshed.to_dict(),
          "poll": poll_res,
      },
      text_output=render_parking_receipt(refreshed),
  )
