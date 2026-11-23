"""Structured JSON parking state modeling, persistence, and renderers (GH-47).

Provides the immutable ParkingState value object, the ParkingStateStore
repository for .workflow/state/70_parking_state.json, and pure renderers for
70_bot_triage.md and chat Parking Receipts without scraping Markdown prose.
"""

from dataclasses import dataclass
import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

PARKING_STATE_FILENAME = "70_parking_state.json"


def _utc_now_iso() -> str:
  """Returns the current UTC timestamp formatted as ISO-8601 ending in Z."""
  return (
      datetime.datetime.now(datetime.timezone.utc)
      .replace(microsecond=0)
      .isoformat()
      .replace("+00:00", "Z")
  )


def _clean_str(value: Any) -> str:
  """Coerces a non-null scalar field to a stripped string.

  Args:
    value: Raw field value from a parsed dictionary.

  Returns:
    Trimmed string representation, or empty string when value is None.
  """
  if value is None:
    return ""
  return str(value).strip()


def _clean_non_neg_int(value: Any) -> int:
  """Coerces a non-boolean integer or ASCII digit string >= 0, defaulting to 0.

  Args:
    value: Raw field value from a parsed dictionary.

  Returns:
    Non-negative integer, or 0 when invalid.
  """
  if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
    return value
  if isinstance(value, str):
    stripped = value.strip()
    if stripped.isascii() and stripped.isdigit():
      return int(stripped)
  return 0


def _clean_str_tuple(raw_items: Any) -> Tuple[str, ...]:
  """Normalizes a list or tuple of strings into a deduplicated tuple.

  Args:
    raw_items: Candidate sequence of strings.

  Returns:
    Tuple of unique non-empty stripped strings preserving order.
  """
  if not isinstance(raw_items, (list, tuple)):
    return ()
  seen: List[str] = []
  for item in raw_items:
    text = _clean_str(item) if isinstance(item, str) else ""
    if text and text not in seen:
      seen.append(text)
  return tuple(seen)


def _sanitize_no_complete(text: str) -> str:
  """Sanitizes single-line status text so uppercase COMPLETE never appears.

  Args:
    text: Raw status string.

  Returns:
    Single-line string with uppercase COMPLETE replaced by Complete.
  """
  cleaned = re.sub(r"[\r\n\t]+", " ", text or "").strip()
  return cleaned.replace("COMPLETE", "Complete")


@dataclass(frozen=True)
class ParkingState:
  """Immutable structured state for a parked Target 70 workflow session.

  Attributes:
    parked: True when the session is actively parked awaiting external events.
    pr_number: Pull request number associated with the parked session.
    pr_url: Optional pull request URL.
    head_branch: Git head branch name of the pull request.
    reason: Concise human-readable reason explaining why the session parked.
    ci_status: Latest CI status (for example SUCCESS, PENDING, or TIMEOUT).
    review_decision: Latest GitHub reviewDecision value.
    approvals_count: Number of valid non-stale human approvals.
    required_approvals: Effective required approval count.
    require_code_owner_reviews: True when CODEOWNERS reviews are required.
    pending_reviewers: Tuple of pending requested reviewer logins or team slugs.
    changes_requested_by: Tuple of reviewer logins requesting changes.
    unresolved_threads_count: Count of unresolved PR review threads.
    parked_at: UTC ISO-8601 timestamp when the session first entered PARKED.
    last_checked_at: UTC ISO-8601 timestamp of the latest PR check.
    cron_expression: Optional opt-in background polling cron schedule.
  """

  parked: bool = False
  pr_number: int = 0
  pr_url: str = ""
  head_branch: str = ""
  reason: str = ""
  ci_status: str = ""
  review_decision: str = ""
  approvals_count: int = 0
  required_approvals: int = 0
  require_code_owner_reviews: bool = False
  pending_reviewers: Tuple[str, ...] = ()
  changes_requested_by: Tuple[str, ...] = ()
  unresolved_threads_count: int = 0
  parked_at: str = ""
  last_checked_at: str = ""
  cron_expression: str = ""

  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "ParkingState":
    """Constructs a normalized ParkingState instance from a dictionary.

    Args:
      data: Mapping of raw parking state fields.

    Returns:
      Normalized immutable ParkingState instance.

    Raises:
      TypeError: If data is not a dictionary.
    """
    if not isinstance(data, dict):
      raise TypeError("ParkingState.from_dict expects a dict.")
    return cls(
        parked=bool(data.get("parked", False)),
        pr_number=_clean_non_neg_int(data.get("pr_number")),
        pr_url=_clean_str(data.get("pr_url")),
        head_branch=_clean_str(data.get("head_branch")),
        reason=_clean_str(data.get("reason")),
        ci_status=_clean_str(data.get("ci_status")),
        review_decision=_clean_str(data.get("review_decision")),
        approvals_count=_clean_non_neg_int(data.get("approvals_count")),
        required_approvals=_clean_non_neg_int(data.get("required_approvals")),
        require_code_owner_reviews=bool(
            data.get("require_code_owner_reviews", False)
        ),
        pending_reviewers=_clean_str_tuple(data.get("pending_reviewers")),
        changes_requested_by=_clean_str_tuple(data.get("changes_requested_by")),
        unresolved_threads_count=_clean_non_neg_int(
            data.get("unresolved_threads_count")
        ),
        parked_at=_clean_str(data.get("parked_at")),
        last_checked_at=_clean_str(data.get("last_checked_at")),
        cron_expression=_clean_str(data.get("cron_expression")),
    )

  @classmethod
  def from_poll_result(
      cls,
      result: Dict[str, Any],
      *,
      pr_number: int = 0,
      head_branch: str = "",
      cron_expression: str = "",
      parked_at: str = "",
      last_checked_at: str = "",
  ) -> "ParkingState":
    """Constructs a parked ParkingState from a poll_ci_status result dictionary.

    Args:
      result: Dictionary returned by ``lib.ci_polling.poll_ci_status``.
      pr_number: Pull request number to associate with the state.
      head_branch: Optional fallback head branch name when absent from result.
      cron_expression: Optional cron schedule expression.
      parked_at: Optional preserved parked_at timestamp.
      last_checked_at: Optional last_checked_at timestamp (defaults to UTC now).

    Returns:
      Normalized immutable ParkingState instance with ``parked=True``.
    """
    now_iso = last_checked_at or _utc_now_iso()
    first_parked = parked_at or now_iso
    resolved_branch = _clean_str(result.get("headBranch")) or _clean_str(
        head_branch
    )
    resolved_reason = _clean_str(result.get("parkingReason")) or _clean_str(
        result.get("message")
    )
    return cls(
        parked=True,
        pr_number=_clean_non_neg_int(pr_number),
        pr_url=_clean_str(result.get("prUrl")),
        head_branch=resolved_branch,
        reason=resolved_reason,
        ci_status=_clean_str(result.get("ciStatus")),
        review_decision=_clean_str(result.get("reviewDecision")),
        approvals_count=_clean_non_neg_int(result.get("approvalsCount")),
        required_approvals=_clean_non_neg_int(result.get("requiredApprovals")),
        require_code_owner_reviews=bool(
            result.get("requireCodeOwnerReviews", False)
        ),
        pending_reviewers=_clean_str_tuple(result.get("pendingReviewers")),
        changes_requested_by=_clean_str_tuple(result.get("changesRequestedBy")),
        unresolved_threads_count=_clean_non_neg_int(
            result.get("unresolvedThreadsCount")
        ),
        parked_at=first_parked,
        last_checked_at=now_iso,
        cron_expression=_clean_str(cron_expression),
    )

  def to_dict(self) -> Dict[str, Any]:
    """Serializes this parking state into a JSON-compatible dictionary.

    Returns:
      Dictionary with canonical snake_case keys and lists for tuples.
    """
    return {
        "parked": self.parked,
        "pr_number": self.pr_number,
        "pr_url": self.pr_url,
        "head_branch": self.head_branch,
        "reason": self.reason,
        "ci_status": self.ci_status,
        "review_decision": self.review_decision,
        "approvals_count": self.approvals_count,
        "required_approvals": self.required_approvals,
        "require_code_owner_reviews": self.require_code_owner_reviews,
        "pending_reviewers": list(self.pending_reviewers),
        "changes_requested_by": list(self.changes_requested_by),
        "unresolved_threads_count": self.unresolved_threads_count,
        "parked_at": self.parked_at,
        "last_checked_at": self.last_checked_at,
        "cron_expression": self.cron_expression,
    }


class ParkingStateStore:
  """Repository for loading and persisting .workflow/state/70_parking_state.json.

  Attributes:
    state_dir: Path to the workflow state directory.
  """

  def __init__(self, state_dir: str) -> None:
    """Initializes the store for a specific workflow state directory.

    Args:
      state_dir: Path to the ``.workflow/state`` directory.
    """
    self.state_dir: str = state_dir

  @classmethod
  def from_repo_root(cls, repo_root: str) -> "ParkingStateStore":
    """Constructs a store anchored at ``<repo_root>/.workflow/state``.

    Args:
      repo_root: Git repository root directory path.

    Returns:
      ParkingStateStore pointing to the repository state directory.
    """
    return cls(os.path.join(os.path.abspath(repo_root), ".workflow", "state"))

  @property
  def path(self) -> str:
    """Returns the full path to ``70_parking_state.json`` inside ``state_dir``."""
    return os.path.join(self.state_dir, PARKING_STATE_FILENAME)

  def exists(self) -> bool:
    """Returns True when ``70_parking_state.json`` exists as a regular file."""
    return bool(self.state_dir) and os.path.isfile(self.path)

  def load(self) -> Optional[ParkingState]:
    """Loads and normalizes ParkingState from ``70_parking_state.json``.

    Returns:
      ParkingState when the file exists and contains a valid JSON object,
      otherwise None on missing, unreadable, non-UTF8, or corrupt JSON.
    """
    if not self.exists():
      return None
    try:
      with open(self.path, "r", encoding="utf-8") as f:
        data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
      return None
    if not isinstance(data, dict):
      return None
    return ParkingState.from_dict(data)

  def is_parked(self) -> bool:
    """Returns True when a valid persisted ParkingState has ``parked=True``."""
    state = self.load()
    return state is not None and state.parked

  def save(self, state: Union[ParkingState, Dict[str, Any]]) -> str:
    """Persists normalized ParkingState deterministically to disk.

    Args:
      state: ParkingState instance or raw dictionary to normalize and save.

    Returns:
      Path to the written ``70_parking_state.json`` file.

    Raises:
      TypeError: If state is neither ParkingState nor dict.
    """
    if isinstance(state, ParkingState):
      record = state
    elif isinstance(state, dict):
      record = ParkingState.from_dict(state)
    else:
      raise TypeError("ParkingStateStore.save expects ParkingState or dict.")
    os.makedirs(self.state_dir, exist_ok=True)
    with open(self.path, "w", encoding="utf-8") as f:
      json.dump(record.to_dict(), f, indent=2)
      f.write("\n")
    return self.path

  def clear(self) -> bool:
    """Removes ``70_parking_state.json`` if present.

    Returns:
      True when the file was removed, or False when it did not exist.
    """
    if not self.exists():
      return False
    try:
      os.unlink(self.path)
      return True
    except FileNotFoundError:
      return False


def format_parking_status_label(state: ParkingState) -> str:
  """Formats a concise single-line PARKED status label without uppercase COMPLETE.

  Args:
    state: Active ParkingState value object.

  Returns:
    Formatted ``PARKED (...)`` label summarizing the primary wait condition.
  """
  ci_upper = (state.ci_status or "").upper()
  if ci_upper in ("PENDING", "TIMEOUT"):
    detail = state.reason or "Awaiting long-running CI pipeline"
    return f"PARKED ({_sanitize_no_complete(detail)})"
  if (
      state.required_approvals > 0
      and state.approvals_count < state.required_approvals
  ):
    return (
        f"PARKED (Awaiting peer review approvals: "
        f"{state.approvals_count}/{state.required_approvals})"
    )
  if state.pending_reviewers and (
      state.require_code_owner_reviews or state.review_decision == "REVIEW_REQUIRED"
  ):
    reviewers = ", ".join(state.pending_reviewers)
    return f"PARKED (Awaiting CODEOWNERS / peer review: {_sanitize_no_complete(reviewers)})"
  if state.unresolved_threads_count > 0:
    return (
        f"PARKED (Awaiting resolution of {state.unresolved_threads_count} "
        "review thread(s))"
    )
  detail = state.reason or "Awaiting external PR review or CI completion"
  return f"PARKED ({_sanitize_no_complete(detail)})"


def render_parked_triage_card(state: ParkingState) -> str:
  """Renders `.workflow/state/70_bot_triage.md` for a parked Target 70 session.

  Args:
    state: Active ParkingState value object.

  Returns:
    Markdown content for ``70_bot_triage.md`` containing zero uppercase COMPLETE.
  """
  status_label = format_parking_status_label(state)
  pr_ref = (
      f"[#{state.pr_number}]({state.pr_url})"
      if state.pr_url and state.pr_number
      else (f"#{state.pr_number}" if state.pr_number else "Unassigned")
  )
  reviewers_str = (
      ", ".join(state.pending_reviewers) if state.pending_reviewers else "None"
  )
  cron_str = (
      f"`{state.cron_expression}`"
      if state.cron_expression
      else "On-demand (Target 00 resumption)"
  )
  lines = [
      "# Target 70: Bot Quiescence and Peer Review Triage",
      "",
      f"- Status: {status_label}",
      f"- Pull Request: {pr_ref}",
      f"- Head Branch: `{_sanitize_no_complete(state.head_branch or 'unknown')}`",
      f"- CI Status: `{_sanitize_no_complete(state.ci_status or 'UNKNOWN')}`",
      f"- Review Decision: `{_sanitize_no_complete(state.review_decision or 'NONE')}`",
      f"- Approvals: {state.approvals_count}/{state.required_approvals}",
      f"- CODEOWNERS Required: {'yes' if state.require_code_owner_reviews else 'no'}",
      f"- Pending Reviewers: {_sanitize_no_complete(reviewers_str)}",
      f"- Unresolved Review Threads: {state.unresolved_threads_count}",
      f"- Parked At: `{_sanitize_no_complete(state.parked_at or 'unknown')}`",
      f"- Last Checked: `{_sanitize_no_complete(state.last_checked_at or 'unknown')}`",
      f"- Background Poll Schedule: {cron_str}",
      "- Resume Command: `make -f .workflow/state/Makefile resume-parked`",
      "",
  ]
  return "\n".join(lines)


def render_parking_receipt(state: ParkingState) -> str:
  """Renders a concise chat Parking Receipt summarizing wait conditions and resume steps.

  Args:
    state: Active ParkingState value object.

  Returns:
    Human-readable Parking Receipt string containing zero uppercase COMPLETE.
  """
  status_label = format_parking_status_label(state)
  pr_line = (
      f"PR #{state.pr_number} ({state.pr_url})"
      if state.pr_url and state.pr_number
      else (f"PR #{state.pr_number}" if state.pr_number else "PR unknown")
  )
  conditions: List[str] = []
  if (state.ci_status or "").upper() in ("PENDING", "TIMEOUT"):
    conditions.append(f"CI status is {state.ci_status}")
  if (
      state.required_approvals > 0
      and state.approvals_count < state.required_approvals
  ):
    conditions.append(
        f"Approvals {state.approvals_count}/{state.required_approvals}"
    )
  if state.pending_reviewers:
    conditions.append(
        "Pending reviewers: " + ", ".join(state.pending_reviewers)
    )
  if state.unresolved_threads_count > 0:
    conditions.append(
        f"Unresolved review threads: {state.unresolved_threads_count}"
    )
  if not conditions and state.reason:
    conditions.append(_sanitize_no_complete(state.reason))

  lines = [
      f"Session Parking Receipt: {status_label}",
      f"- Pull Request: {_sanitize_no_complete(pr_line)}",
      f"- Branch: `{_sanitize_no_complete(state.head_branch or 'unknown')}`",
      f"- Waiting On: {_sanitize_no_complete('; '.join(conditions) or 'External review or CI')}",
  ]
  if state.cron_expression:
    lines.append(
        f"- Optional Cron Schedule: `{_sanitize_no_complete(state.cron_expression)}`"
    )
  lines.append(
      "- Resume Action: Run `make -f .workflow/state/Makefile resume-parked` "
      "or return to Target 00 when ready."
  )
  return "\n".join(lines) + "\n"
