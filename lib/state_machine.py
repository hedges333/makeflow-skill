"""Shared Makefile workflow DAG state machine and readerboard renderer (GH-219).

Provides canonical target identifiers, transition tables, active target
persistence, deterministic 99_next_action.md rendering, and GNU Make subprocess
delegation shared by CLI gates, hooks, and the MCP server.
"""

from dataclasses import dataclass
import json
import math
import os
import re
import signal
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from lib.commit_msg import validate_head_commit
from lib.config import (
    is_ticket_active,
    resolve_workflow_config,
    sanitize_make_subprocess_env,
)
from lib.safe_copy import safe_copy_file
from lib.ticket_state import TICKET_JSON_FILENAME, TicketStateStore
from lib.turn_boundary import (
    ACTIVE_SUBAGENT_FILENAME,
    GATE_EVIDENCE_KEYS,
    PAUSE_GATE_TARGETS,
    TARGETS_CONFIG,
    VALID_TRANSITIONS,
    clear_gate_entry,
    read_gate_entry,
    record_gate_entry,
    verify_mcp_gate_exit,
)
from lib.workflow_gates import (
    _run_repo_git,
    fetch_and_inspect_main_sync,
    inspect_main_sync,
)
from lib.workspace_mutex import WorkspaceMutex

ACTIVE_TARGET_FILENAME = "active_target.json"
NEXT_ACTION_FILENAME = "99_next_action.md"

ALL_TARGETS = set(TARGETS_CONFIG.keys())

TARGET_ID_TO_NAME: Dict[str, str] = {
    "T00": "read-init-d",
    "T10": "ticket-assigned",
    "T15": "archaeology-verified",
    "T20": "approved-plan",
    "T30": "implementation-diff",
    "T40": "adversarial-review",
    "T42": "triaged-blockers",
    "T45": "approved-review",
    "T50": "commit",
    "T60": "push",
    "T70": "bot-quiescence",
    "T75": "ready-to-land",
    "T80": "land",
    "T90": "codified-scars-consolidated",
}

TARGET_NAME_TO_ID: Dict[str, str] = {
    name: tid for tid, name in TARGET_ID_TO_NAME.items()
}
TARGET_NAME_TO_ID["finish"] = "T90"

REQUIRED_STATE_CARDS = {
    ("T10", "T15"): "10_ticket.md",
    ("T15", "T20"): "15_archaeology_receipt.md",
    ("T20", "T30"): "20_plan_approved.md",
    ("T30", "T40"): "20_plan_approved.md",
    ("T40", "T45"): "40_critic_review.md",
    ("T40", "T42"): "40_critic_review.md",
    ("T45", "T50"): "45_review_approved.md",
    ("T70", "T75"): "70_bot_triage.md",
}

ALT_STATE_CARDS = {
    ("T10", "T15"): (TICKET_JSON_FILENAME,),
    ("T15", "T20"): ("20_plan.md", "20_plan_approved.md"),
    ("T40", "T45"): ("45_review_approved.md",),
}

_MILESTONE_ORDER: Tuple[Tuple[str, str, str], ...] = (
    ("T00", "read-init-d", ""),
    ("T10", "ticket-assigned", ""),
    ("T15", "archaeology-verified", ""),
    ("T20", "approved-plan", "Human Pause Gate"),
    ("T30", "implementation-diff", "Red-Green TDD"),
    ("T40", "adversarial-review", "Six Pillars"),
    ("T42", "triaged-blockers", "Human Pause Gate"),
    ("T45", "approved-review", "Human Pause Gate"),
    ("T50", "commit", "Human Pause Gate"),
    ("T60", "push", "Human Pause Gate"),
    ("T70", "bot-quiescence", "CI and Bot Poll"),
    ("T75", "ready-to-land", "Human Pause Gate"),
    ("T80", "land", "Merge and Cleanup"),
    ("T90", "finish", "History Ledger"),
)

_NEXT_ACTION_SUMMARIES: Dict[str, str] = {
    "T00": "Inspect existing state cards or start Target 10 intake",
    "T10": "Run Target 15 repository archaeology via archaeology-worker",
    "T15": "Draft Target 20 implementation plan and pause for human approval",
    "T20": "Await explicit human plan approval before Target 30 TDD",
    "T30": "Execute Red-Green-Refactor TDD via tdd-worker",
    "T40": "Run Six Pillars adversarial review via adversarial-critic",
    "T42": "Await human blocker triage decision before returning to TDD",
    "T45": "Await explicit human review approval before Target 50 commit",
    "T50": "Await explicit human commit approval before git commit",
    "T60": "Await explicit human push approval before git push",
    "T70": "Poll CI checks and bot comments until quiescence window passes",
    "T75": "Await explicit human landing approval before merging",
    "T80": "Merge approved branch, sync main, and enter Target 90",
    "T90": "Update history ledger and ./CHANGELOG.md before running make finish",
}

_ACTIVE_TARGET_LINE_RE = re.compile(
    r"(?im)^-\s*Active Target:\s*(?:Target\s*(\d{2})|(T\d{2}))\b"
)
_LAST_COMPLETED_LINE_RE = re.compile(
    r"(?im)^-\s*Last Completed Step:\s*Target\s*(\d{2})\b"
)
_NA_BRANCH_RE = re.compile(r"(?m)^-\s*Branch:\s*`?([^`\n]+)`?\s*$")
_LEFTOVER_CARD_RE = re.compile(r"^(?:[1-8][0-9]_.*|approved_tree\.txt$)")
_HOSTING_MODE_GITHUB_RE = re.compile(
    r"(?im)^(?:[-*]\s+)?(?:\*\*)?HOSTING_MODE(?:\*\*)?:\s*`?GITHUB`?\b"
)
_TARGET_ORDER_INDEX: Dict[str, int] = {
    tid: idx for idx, (tid, _, _) in enumerate(_MILESTONE_ORDER)
}


def list_git_remotes(repo_root: str) -> List[str]:
  """Lists configured Git remote names for repo_root.

  Args:
    repo_root: Git repository root directory.

  Returns:
    List of configured remote names, or empty list when none exist.
  """
  if not repo_root or not os.path.isdir(repo_root):
    return []
  proc = _run_repo_git(repo_root, "remote", timeout=5)
  if proc.returncode != 0:
    return []
  return [
      line.strip() for line in (proc.stdout or "").splitlines() if line.strip()
  ]


def has_git_remote(repo_root: str) -> bool:
  """Returns True when repo_root has one or more configured Git remotes.

  Args:
    repo_root: Git repository root directory.

  Returns:
    True if at least one remote is configured, otherwise False.
  """
  return bool(list_git_remotes(repo_root))


def _resolve_effective_workspace(
    workspace_dir: str,
    state_dir: str = "",
    *,
    require_git: bool = True,
) -> str:
  """Resolves the effective workspace root from explicit path or state_dir.

  Args:
    workspace_dir: Explicit repository root directory, or empty string.
    state_dir: Optional .workflow/state directory path.
    require_git: When True, requires .git existence on state_dir ancestor.

  Returns:
    Resolved workspace directory path, or empty string when unavailable.
  """
  if workspace_dir:
    return workspace_dir
  if not state_dir:
    return ""
  cand_ws = os.path.dirname(os.path.dirname(os.path.abspath(state_dir)))
  if not require_git or os.path.exists(os.path.join(cand_ws, ".git")):
    return cand_ws
  return ""


def _receipt_declares_github_mode(state_dir: str) -> bool:
  """Returns True when 15_archaeology_receipt.md explicitly declares GITHUB mode.

  Args:
    state_dir: Path to .workflow/state directory.

  Returns:
    True when 15_archaeology_receipt.md declares HOSTING_MODE: GITHUB.
  """
  if not state_dir or not os.path.isdir(state_dir):
    return False
  receipt_path = os.path.join(state_dir, "15_archaeology_receipt.md")
  if not os.path.isfile(receipt_path):
    return False
  try:
    with open(receipt_path, "r", encoding="utf-8", errors="replace") as f:
      content = f.read()
  except OSError:
    return False
  return bool(_HOSTING_MODE_GITHUB_RE.search(content))


@dataclass(frozen=True)
class RenderCardSpec:
  """Configuration for deterministic 99_next_action.md rendering.

  Attributes:
    state_dir: Path to .workflow/state directory.
    active_target: Canonical target ID (T00..T90).
    previous_target: Prior target ID before transition.
    workspace_dir: Optional Git repository root directory.
    complete: True when rendering final Target 90 completion state.
    skipped_targets: Tuple of skipped target IDs (such as T60 in Pure Git Mode).
  """

  state_dir: str
  active_target: str
  previous_target: str = ""
  workspace_dir: str = ""
  complete: bool = False
  skipped_targets: Tuple[str, ...] = ()


def _sanitize_active_text(value: str) -> str:
  """Sanitizes text so uppercase COMPLETE never appears before finish.

  Args:
    value: Raw single-line metadata text.

  Returns:
    Cleaned single-line text without uppercase COMPLETE.
  """
  cleaned = re.sub(r"[\r\n\t]+", " ", value or "").strip()
  return cleaned.replace("COMPLETE", "Complete")


def read_active_target(state_dir: str) -> Optional[Dict[str, Any]]:
  """Reads .workflow/state/active_target.json if present and valid.

  Args:
    state_dir: Path to the workflow state directory.

  Returns:
    Parsed active target dictionary, or None when absent or unreadable.
  """
  if not state_dir or not isinstance(state_dir, str):
    return None
  path = os.path.join(state_dir, ACTIVE_TARGET_FILENAME)
  if not os.path.isfile(path):
    return None
  try:
    with open(path, "r", encoding="utf-8") as f:
      data = json.load(f)
    return data if isinstance(data, dict) else None
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
    return None


def write_active_target(state_dir: str, payload: Dict[str, Any]) -> None:
  """Writes .workflow/state/active_target.json deterministically.

  Args:
    state_dir: Path to the workflow state directory.
    payload: Serializable dictionary containing active target metadata.
  """
  if not state_dir or not isinstance(state_dir, str):
    return
  os.makedirs(state_dir, exist_ok=True)
  path = os.path.join(state_dir, ACTIVE_TARGET_FILENAME)
  with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, sort_keys=True)
    f.write("\n")


_PREREQ_CARDS_FOR_TARGET: Dict[str, Tuple[str, ...]] = {
    "T10": ("10_ticket.md", TICKET_JSON_FILENAME),
    "T15": ("15_archaeology_receipt.md",),
    "T20": ("20_plan.md",),
    "T40": ("40_critic_review.md",),
    "T45": ("45_review_approved.md",),
    "T50": ("50_commit_msg.txt",),
    "T70": ("70_bot_triage.md",),
}


def _infer_t30_source_from_cards(filtered: set) -> str:
  """Infers the source target preceding T30 from remaining state cards."""
  for card_name, target_id in (
      ("70_bot_triage.md", "T70"),
      ("50_commit_msg.txt", "T50"),
      ("45_review_approved.md", "T45"),
      ("40_critic_review.md", "T42"),
  ):
    if card_name in filtered:
      return target_id
  if "20_plan_approved.md" in filtered or "20_plan.md" in filtered:
    return "T20"
  if "15_archaeology_receipt.md" in filtered:
    return "T15"
  return (
      "T10"
      if ("10_ticket.md" in filtered or TICKET_JSON_FILENAME in filtered)
      else "T00"
  )


def _infer_target_from_cards(state_dir: str, next_target: str = "") -> str:
  """Infers current target from state card filenames on disk.

  Args:
    state_dir: Existing .workflow/state directory path.
    next_target: Optional destination target ID to avoid prerequisite collision.

  Returns:
    Canonical target ID from T00 through T80.
  """
  try:
    names = set(os.listdir(state_dir))
  except OSError:
    return "T00"
  if next_target != "T80" and any(n.startswith("80_") for n in names):
    return "T80"

  filtered = names - set(_PREREQ_CARDS_FOR_TARGET.get(next_target, ()))
  if next_target == "T30":
    return _infer_t30_source_from_cards(filtered)
  if next_target == "T45" and (
      "40_critic_review.md" in names or "45_review_approved.md" in names
  ):
    return "T40"

  for card_name, target_id in (
      ("70_bot_triage.md", "T70"),
      ("50_commit_msg.txt", "T50"),
      ("45_review_approved.md", "T45"),
      ("40_critic_review.md", "T40"),
      ("20_plan_approved.md", "T30"),
      ("20_plan.md", "T20"),
      ("15_archaeology_receipt.md", "T15"),
      ("10_ticket.md", "T10"),
      (TICKET_JSON_FILENAME, "T10"),
  ):
    if card_name in filtered:
      return target_id
  return "T00"


def infer_current_target(state_dir: str, next_target: str = "") -> str:
  """Infers active workflow target from active_target.json, readerboard, or cards.

  Args:
    state_dir: Path to .workflow/state directory.
    next_target: Optional destination target ID when inferring a transition source.

  Returns:
    Canonical target identifier (T00..T90).
  """
  if not state_dir or not os.path.isdir(state_dir):
    return "T00"
  active_data = read_active_target(state_dir)
  if active_data:
    recorded = str(active_data.get("target") or "").strip()
    if recorded in ALL_TARGETS:
      return recorded

  na_path = os.path.join(state_dir, NEXT_ACTION_FILENAME)
  if os.path.isfile(na_path):
    if not is_ticket_active(state_dir):
      return "T00"
    try:
      with open(na_path, "r", encoding="utf-8") as f:
        na_text = f.read()
      match = _ACTIVE_TARGET_LINE_RE.search(na_text)
      if match:
        candidate = f"T{match.group(1)}" if match.group(1) else match.group(2)
        if candidate in ALL_TARGETS:
          return candidate
    except (OSError, UnicodeDecodeError):
      pass

  entry = read_gate_entry(state_dir)
  if entry:
    gate_id = str(entry.get("gate") or "").strip()
    from_id = str(entry.get("fromTarget") or "").strip()
    if next_target and gate_id == next_target and from_id in ALL_TARGETS:
      return from_id
    if gate_id in ALL_TARGETS and gate_id != next_target:
      return gate_id

  return _infer_target_from_cards(state_dir, next_target=next_target)


def _detect_git_branch(workspace_dir: str) -> str:
  """Detects current Git branch name in workspace_dir when available.

  Args:
    workspace_dir: Repository root directory.

  Returns:
    Branch name string, or empty string if unavailable.
  """
  if not workspace_dir or not os.path.isdir(workspace_dir):
    return ""
  try:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if proc.returncode == 0:
      return (proc.stdout or "").strip()
  except (OSError, subprocess.SubprocessError):
    return ""
  return ""


def _parse_metadata_from_files(
    state_dir: str, workspace_dir: str = ""
) -> Dict[str, str]:
  """Loads ticket ID, title, URL, and branch from 10_ticket.json with Git fallback.

  Args:
    state_dir: Path to .workflow/state directory.
    workspace_dir: Optional repository root directory for Git branch fallback.

  Returns:
    Dictionary with keys ticket_id, title, url, and branch.
  """
  meta = {"ticket_id": "", "title": "", "url": "", "branch": ""}
  ticket = TicketStateStore(state_dir).load()
  if ticket is not None:
    meta["ticket_id"] = ticket.ticket_id
    meta["title"] = ticket.title
    meta["url"] = ticket.url
    meta["branch"] = ticket.branch

  eff_ws = _resolve_effective_workspace(workspace_dir, state_dir)
  git_branch = _detect_git_branch(eff_ws) if eff_ws else ""
  if not meta["branch"] and git_branch:
    meta["branch"] = git_branch

  if not meta["branch"]:
    na_path = os.path.join(state_dir, NEXT_ACTION_FILENAME)
    if os.path.isfile(na_path) and is_ticket_active(state_dir):
      try:
        with open(na_path, "r", encoding="utf-8") as f:
          na_text = f.read()
        br_m = _NA_BRANCH_RE.search(na_text)
        if br_m:
          meta["branch"] = br_m.group(1).strip()
      except (OSError, UnicodeDecodeError):
        pass

  if not meta["ticket_id"] and meta["branch"]:
    try:
      from lib.terminal_title import detect_ticket_id

      meta["ticket_id"] = detect_ticket_id(
          repo_root=eff_ws or state_dir, branch=meta["branch"]
      )
    except (ImportError, OSError):
      pass

  return meta


def _format_ticket_bullet(
    meta: Dict[str, str], complete: bool
) -> str:
  """Formats the ticket metadata bullet for 99_next_action.md.

  Args:
    meta: Ticket metadata dictionary.
    complete: True when ticket execution has finished.

  Returns:
    Formatted markdown bullet string without repeating ticket_id as title.
  """
  ticket_id = _sanitize_active_text(meta.get("ticket_id", ""))
  raw_title = meta.get("title") or ""
  title = (
      re.sub(r"[\r\n\t]+", " ", raw_title).strip()
      if complete
      else _sanitize_active_text(raw_title)
  )
  has_distinct_title = bool(title) and title.lower() != ticket_id.lower()
  url = _sanitize_active_text(meta.get("url", ""))
  if complete:
    if not ticket_id:
      return "- Completed Ticket: Unassigned (Completed)"
    label = f"{ticket_id}: {title}" if has_distinct_title else ticket_id
    if url:
      return f"- Completed Ticket: [{label}]({url}) (Completed)"
    return f"- Completed Ticket: [{label}] (Completed)"
  if not ticket_id:
    return "- Ticket: Unassigned (Ready for Intake)"
  label = f"{ticket_id}: {title}" if has_distinct_title else ticket_id
  if url:
    return f"- Ticket: [{label}]({url}) (In Progress)"
  return f"- Ticket: [{label}] (In Progress)"


def _resolve_skipped_targets(
    state_dir: str,
    active_target: str,
    previous_target: str = "",
) -> Tuple[str, ...]:
  """Computes persisted skipped target IDs for a transition or state restore.

  Args:
    state_dir: Path to .workflow/state directory.
    active_target: Current or destination canonical target ID.
    previous_target: Optional prior target ID.

  Returns:
    Ordered tuple of skipped canonical target IDs.
  """
  if (previous_target, active_target) == ("T50", "T70"):
    return ("T60",)
  t60_idx = _TARGET_ORDER_INDEX.get("T60", 9)
  active_idx = _TARGET_ORDER_INDEX.get(active_target, -1)
  if 0 <= active_idx <= t60_idx:
    return ()

  existing = read_active_target(state_dir)
  if existing and isinstance(existing.get("skippedTargets"), list):
    seen: List[str] = []
    for item in existing["skippedTargets"]:
      if isinstance(item, str) and item in ALL_TARGETS and item not in seen:
        seen.append(item)
    return tuple(seen)

  na_path = (
      os.path.join(state_dir, NEXT_ACTION_FILENAME) if state_dir else ""
  )
  if na_path and os.path.isfile(na_path):
    try:
      with open(na_path, "r", encoding="utf-8", errors="replace") as f:
        if "- [-] T60:" in f.read():
          return ("T60",)
    except OSError:
      pass
  return ()


def _build_milestone_lines(
    active_target: str,
    previous_target: str,
    complete: bool,
    skipped_targets: Tuple[str, ...] = (),
) -> List[str]:
  """Builds ordered milestone checklist lines for 99_next_action.md.

  Args:
    active_target: Canonical target ID (T00..T90).
    previous_target: Prior target ID.
    complete: True when all milestones are complete.
    skipped_targets: Optional tuple of skipped target IDs.

  Returns:
    List of formatted markdown checklist lines.
  """
  include_t42 = active_target == "T42" or previous_target == "T42"
  items = [
      item
      for item in _MILESTONE_ORDER
      if item[0] != "T42" or include_t42
  ]
  active_idx = next(
      (i for i, item in enumerate(items) if item[0] == active_target), 0
  )
  skipped_set = set(skipped_targets or ())
  lines: List[str] = []
  for idx, (tid, label, note) in enumerate(items):
    if tid in skipped_set:
      lines.append(f"- [-] {tid}: {label} (Skipped in Pure Git Mode)")
      continue
    suffix = f" ({note})" if note else ""
    if complete or idx < active_idx:
      mark = "x"
    elif idx == active_idx:
      mark = ">"
    else:
      mark = " "
    lines.append(f"- [{mark}] {tid}: {label}{suffix}")
  return lines


def _render_active_lines(
    spec: RenderCardSpec,
    meta: Dict[str, str],
    branch: str,
    t_label: str,
) -> List[str]:
  """Builds markdown lines for an active (non-completed) 99_next_action.md."""
  prev_id = spec.previous_target if spec.previous_target in ALL_TARGETS else "T00"
  prev_label = TARGET_ID_TO_NAME.get(prev_id, prev_id)
  lines = [
      "# 99_next_action: Live Memento Readerboard",
      "",
      _format_ticket_bullet(meta, complete=False),
      f"- Branch: `{branch}`",
      f"- Active Target: Target {spec.active_target[1:]} (`{t_label}`)",
      f"- Last Completed Step: Target {prev_id[1:]} (`{prev_label}`)",
  ]
  if spec.active_target in PAUSE_GATE_TARGETS:
    lines.append(
        "- Gate Status: AWAITING_DECLARATION"
        " (Stop tool calls and wait for explicit human approval)"
    )
  is_pure_git_t70 = spec.active_target == "T70" and (
      "T60" in (spec.skipped_targets or ())
      or (
          bool(spec.workspace_dir)
          and os.path.exists(os.path.join(spec.workspace_dir, ".git"))
          and not has_git_remote(spec.workspace_dir)
      )
  )
  next_summary = (
      "Run local verification suite (make all) and write 70_bot_triage.md"
      if is_pure_git_t70
      else _NEXT_ACTION_SUMMARIES.get(spec.active_target, "Continue workflow")
  )
  lines.extend([
      f"- Next Action: {_sanitize_active_text(next_summary)}",
      "",
      "## Milestone Progress",
      *_build_milestone_lines(
          spec.active_target,
          spec.previous_target,
          complete=False,
          skipped_targets=spec.skipped_targets,
      ),
  ])
  return lines


def render_next_action_card(
    spec: RenderCardSpec,
    ticket_meta: Optional[Dict[str, str]] = None,
) -> str:
  """Renders and writes .workflow/state/99_next_action.md deterministically.

  Args:
    spec: Rendering specification with target and directory context.
    ticket_meta: Optional pre-parsed ticket metadata dictionary.

  Returns:
    Rendered markdown content written to 99_next_action.md.
  """
  meta = dict(
      ticket_meta
      or _parse_metadata_from_files(spec.state_dir, workspace_dir=spec.workspace_dir)
  )
  git_branch = _detect_git_branch(spec.workspace_dir)
  branch = _sanitize_active_text(git_branch or meta.get("branch") or "HEAD")
  t_label = (
      "finish"
      if spec.active_target == "T90"
      else TARGET_ID_TO_NAME.get(spec.active_target, spec.active_target)
  )
  if spec.complete:
    lines = [
        "# Target 90 Finished: COMPLETE",
        "",
        "- Status: COMPLETE (make finish satisfied)",
        _format_ticket_bullet(meta, complete=True),
        f"- Branch: `{branch}`",
        "- Active Target: None (Ready for Target 10 intake)",
        "",
        "## Milestone Progress",
        *_build_milestone_lines(
            "T90",
            spec.previous_target,
            complete=True,
            skipped_targets=spec.skipped_targets,
        ),
    ]
  else:
    lines = _render_active_lines(spec, meta, branch, t_label)

  content = "\n".join(lines) + "\n"
  os.makedirs(spec.state_dir, exist_ok=True)
  with open(
      os.path.join(spec.state_dir, NEXT_ACTION_FILENAME), "w", encoding="utf-8"
  ) as f:
    f.write(content)
  return content


def _evaluate_upstream_sync(
    current: str,
    next_t: str,
    workspace_dir: str,
    evidence: Dict[str, Any],
) -> Tuple[bool, str]:
  """Evaluates upstream origin/main sync status for a target transition.

  Args:
    current: Source target ID.
    next_t: Destination target ID.
    workspace_dir: Git repository root directory.
    evidence: Transition evidence dictionary.

  Returns:
    Tuple of (ok, error_or_notice_string).
  """
  if not workspace_dir or not os.path.isdir(workspace_dir):
    return True, ""
  sync = (
      inspect_main_sync(workspace_dir)
      if evidence.get("skipMainFetch")
      else fetch_and_inspect_main_sync(workspace_dir)
  )
  status = sync.get("status", "NO_REMOTE")
  upstream_ref = sync.get("upstream_ref") or "origin/main"
  behind = sync.get("behind_count", 0)
  conflicts = sync.get("conflicting_files") or []

  if status == "CONFLICT" and not evidence.get("allowMainConflict"):
    files_str = ", ".join(conflicts) or "unknown"
    if current in ("T70", "T75", "T80") or next_t in ("T70", "T75", "T80"):
      guidance = (
          "Stop tool calls without altering Git state (SCAR-GIT-25), report"
          f" conflicting files ({files_str}) to the operator, and request"
          f" approval to merge {upstream_ref} (git fetch origin main &&"
          " git merge origin/main) and return to T30/T40 for re-verification."
      )
    else:
      guidance = (
          "Stop tool calls without altering Git state (SCAR-GIT-25), report"
          f" conflicting files ({files_str}) to the operator, and request"
          f" approval to run git rebase {upstream_ref} (and re-lock"
          " approved_tree.txt + re-run T40 critic review if already past T40)."
      )
    return (
        False,
        f"BLOCKED: [SCAR-GIT-20] UPSTREAM_MAIN_CONFLICT on transition"
        f" {current} -> {next_t}: branch is behind {upstream_ref} by"
        f" {behind} commit(s) with merge conflicts in [{files_str}]. {guidance}",
    )

  if status != "BEHIND_CLEAN":
    return True, ""
  return True, _format_behind_clean_notice(current, next_t, upstream_ref, behind)


def _format_behind_clean_notice(
    current: str, next_t: str, upstream_ref: str, behind: int
) -> str:
  """Formats MAIN_SYNC_NOTICE guidance when branch is cleanly behind main.

  Args:
    current: Source target ID.
    next_t: Destination target ID.
    upstream_ref: Remote tracking branch name.
    behind: Number of upstream commits ahead of merge-base.

  Returns:
    Leading-space MAIN_SYNC_NOTICE message string.
  """
  prefix = (
      f" MAIN_SYNC_NOTICE: {upstream_ref} advanced by {behind}"
      " commit(s) with 0 conflicts (BEHIND_CLEAN)."
  )
  if next_t in ("T15", "T20", "T30", "T40"):
    return (
        f"{prefix} You may offer an operator-approved 'git rebase"
        f" {upstream_ref}' (SCAR-GIT-25) now while in pre-review targets"
        f" ({current} -> {next_t}) before locking approved_tree.txt at T40."
    )
  if next_t in ("T42", "T45", "T50"):
    return (
        f"{prefix} Preserve .workflow/state/approved_tree.txt (SCAR-GIT-23);"
        " do not rebase during locked-tree gates unless returning to T40 for"
        " critic re-audit."
    )
  if next_t == "T60":
    return (
        f"{prefix} Branch merges cleanly; rebasing now would invalidate"
        " approved_tree.txt and require T60 -> T40 critic re-audit."
    )
  return (
      f"{prefix} Per SCAR-GIT-18 and SCAR-GIT-25, do not rebase or force-push"
      " an open pull request when mergeable is CLEAN."
  )


def _check_required_card(
    current: str, next_t: str, state_dir: str
) -> Optional[str]:
  """Checks required or alternate state cards on disk when state_dir exists."""
  if not state_dir or not os.path.isdir(state_dir):
    return None
  req_card = REQUIRED_STATE_CARDS.get((current, next_t))
  alt_cards = ALT_STATE_CARDS.get((current, next_t), ())
  if (
      req_card
      and not os.path.isfile(os.path.join(state_dir, req_card))
      and not any(
          os.path.isfile(os.path.join(state_dir, ac)) for ac in alt_cards
      )
  ):
    return (
        f"BLOCKED: Required state card '{req_card}' does not exist in"
        f" '{state_dir}' for transition {current} -> {next_t}."
    )
  return None


def _validate_blockers_count(
    current: str, next_t: str, ev: Dict[str, Any]
) -> Optional[str]:
  """Validates blockersCount type, range, and T40 -> T42/T45 routing rules."""
  needs_check = (current, next_t) in (
      ("T40", "T45"),
      ("T40", "T42"),
  ) or "blockersCount" in ev
  if not needs_check:
    return None
  raw = ev.get("blockersCount", 1)
  if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
    return (
        f"BLOCKED: Invalid blockersCount '{raw}'. Must be a non-negative"
        " integer."
    )
  if (current, next_t) == ("T40", "T45") and raw > 0:
    return (
        f"BLOCKED: Critic reported {raw} blocker(s). Route to T42"
        " (triaged-blockers) before T45."
    )
  if (current, next_t) == ("T40", "T42") and raw == 0:
    return (
        "BLOCKED: Critic reported 0 blockers. Route to T45"
        " (approved-review) instead of T42."
    )
  return None


def _validate_transition_inputs(
    current: str,
    next_t: str,
    ev: Dict[str, Any],
    state_dir: str,
    workspace_dir: str = "",
) -> Optional[str]:
  """Validates target IDs, DAG edges, evidence keys, and required state cards.

  Args:
    current: Source target ID.
    next_t: Destination target ID.
    ev: Transition evidence dictionary.
    state_dir: Path to .workflow/state directory.
    workspace_dir: Optional repository root directory for Pure Git remote checks.

  Returns:
    BLOCKED error string when invalid, or None when valid.
  """
  if current not in VALID_TRANSITIONS:
    return f"BLOCKED: Unknown currentTarget '{current}'. Use T00 through T90."
  if next_t not in ALL_TARGETS:
    return f"BLOCKED: Unknown nextTarget '{next_t}'. Use T00 through T90."

  is_finish = (
      next_t == "T90"
      and bool(ev.get("complete"))
      and current in ("T80", "T90")
  )
  if not (is_finish or next_t in VALID_TRANSITIONS[current]):
    allowed = sorted(VALID_TRANSITIONS[current])
    return (
        f"BLOCKED: Transition {current} -> {next_t} is not valid in the"
        f" Makefile DAG. Allowed next targets from {current}: {allowed}."
    )

  if (current, next_t) == ("T50", "T70"):
    eff_ws = _resolve_effective_workspace(workspace_dir, state_dir)
    eff_state = state_dir or (
        os.path.join(eff_ws, ".workflow", "state") if eff_ws else ""
    )
    if eff_ws and os.path.isdir(eff_ws):
      remotes = list_git_remotes(eff_ws)
      if remotes:
        remotes_str = ", ".join(remotes)
        return (
            f"BLOCKED: [SCAR-GIT-30] Transition T50 -> T70 is only allowed in"
            f" remote-less Pure Git Mode. Repository has configured Git"
            f" remote(s) [{remotes_str}]; advance T50 -> T60 (push) instead."
        )
    if _receipt_declares_github_mode(eff_state):
      return (
          "BLOCKED: [SCAR-GIT-30] Transition T50 -> T70 is blocked because"
          " 15_archaeology_receipt.md declares HOSTING_MODE: GITHUB. Advance"
          " T50 -> T60 (push) instead."
      )

  gate_key = GATE_EVIDENCE_KEYS.get((current, next_t))
  if gate_key and not ev.get(gate_key):
    return f"BLOCKED: Cannot move {current} -> {next_t} without {gate_key} = true."
  blockers_err = _validate_blockers_count(current, next_t, ev)
  if blockers_err is not None:
    return blockers_err
  return _check_required_card(current, next_t, state_dir)


def _finalize_completed_ticket(
    state_dir: str,
    workspace_dir: str,
    previous_target: str,
) -> None:
  """Archives or deletes intermediate state cards and releases locks on finish.

  Args:
    state_dir: Existing .workflow/state directory path.
    workspace_dir: Optional Git repository root directory.
    previous_target: Target preceding finish.
  """
  ws = _resolve_effective_workspace(
      workspace_dir, state_dir, require_git=False
  )
  skipped_targets = _resolve_skipped_targets(state_dir, "T90", previous_target)
  meta = _parse_metadata_from_files(state_dir, workspace_dir=ws)
  ticket_id = meta.get("ticket_id", "")
  cfg = resolve_workflow_config(workspace_dir=ws if os.path.isdir(ws) else None)
  action = cfg.get("state", {}).get("completedCardAction", "unlink")
  archive_slug = (
      re.sub(r"[^A-Za-z0-9._-]", "_", ticket_id).strip("._") or "unknown"
  )

  removable = [
      fname
      for fname in os.listdir(state_dir)
      if _LEFTOVER_CARD_RE.match(fname)
      or fname
      in (
          "30_tdd_receipt.json",
          "30_regression_receipt.json",
          "loaded_scars.json",
      )
  ]
  for fname in sorted(removable):
    src = os.path.join(state_dir, fname)
    if not os.path.isfile(src):
      continue
    try:
      if action == "archive":
        dst = os.path.join(ws, ".workflow", "archive", archive_slug, fname)
        safe_copy_file(src, dst)
      os.unlink(src)
    except OSError:
      pass

  for transient in (
      "gate_entry.json",
      ACTIVE_TARGET_FILENAME,
      ACTIVE_SUBAGENT_FILENAME,
  ):
    t_path = os.path.join(state_dir, transient)
    if os.path.isfile(t_path):
      try:
        os.unlink(t_path)
      except OSError:
        pass

  if os.path.isdir(ws):
    try:
      mutex = WorkspaceMutex(repo_root=ws)
      if ticket_id:
        mutex.release(ticket_id)
      mutex.release_workspace(ws)
    except OSError:
      pass

  render_next_action_card(
      RenderCardSpec(
          state_dir=state_dir,
          active_target="T90",
          previous_target=previous_target,
          workspace_dir=ws,
          complete=True,
          skipped_targets=skipped_targets,
      ),
      ticket_meta=meta,
  )

  if ws and os.path.isdir(ws):
    try:
      from lib.terminal_title import (
          detect_directory_name,
          format_terminal_title,
          is_temporary_directory_name,
          set_terminal_title,
      )

      if not is_temporary_directory_name(ws):
        dir_name = detect_directory_name(repo_root=ws)
        if dir_name and not is_temporary_directory_name(dir_name):
          restored_title = format_terminal_title("", dir_name)
          if restored_title:
            set_terminal_title(title=restored_title, repo_root=ws)
    except (ImportError, OSError):
      pass


def _build_active_target_payload(
    previous_target: str,
    active_target: str,
    skipped_targets: Tuple[str, ...] = (),
) -> Dict[str, Any]:
  """Builds the serialized payload written to active_target.json.

  Args:
    previous_target: Prior canonical target ID.
    active_target: Current canonical target ID.
    skipped_targets: Optional tuple of skipped canonical target IDs.

  Returns:
    Dictionary ready for write_active_target.
  """
  payload: Dict[str, Any] = {
      "previousTarget": previous_target,
      "target": active_target,
      "targetName": TARGET_ID_TO_NAME[active_target],
  }
  if skipped_targets:
    payload["skippedTargets"] = list(skipped_targets)
  return payload


def _persist_active_transition(
    spec: RenderCardSpec,
    gate_key: Optional[str],
    transcript_path: str,
) -> None:
  """Persists gate_entry.json, active_target.json, and active readerboard."""
  if spec.active_target in PAUSE_GATE_TARGETS:
    record_gate_entry(
        spec.state_dir,
        spec.previous_target,
        spec.active_target,
        transcript_path=transcript_path,
    )
  elif spec.previous_target in PAUSE_GATE_TARGETS or gate_key:
    clear_gate_entry(spec.state_dir, spec.previous_target)
  write_active_target(
      spec.state_dir,
      _build_active_target_payload(
          spec.previous_target, spec.active_target, spec.skipped_targets
      ),
  )
  render_next_action_card(spec)
  if (
      spec.active_target == "T10"
      and spec.workspace_dir
      and os.path.isdir(spec.workspace_dir)
  ):
    try:
      from lib.terminal_title import (
          is_temporary_directory_name,
          set_terminal_title,
      )

      if not is_temporary_directory_name(spec.workspace_dir):
        set_terminal_title(repo_root=spec.workspace_dir)
    except (ImportError, OSError):
      pass


def _restore_active_session_state(
    state_dir: str, current: str, workspace_dir: str
) -> None:
  """Reconciles active_target.json and 99_next_action.md without resetting T00."""
  prev_t = "T00"
  existing = read_active_target(state_dir)
  if (
      existing
      and existing.get("target") == current
      and existing.get("previousTarget") in ALL_TARGETS
  ):
    prev_t = str(existing["previousTarget"])
  else:
    entry = read_gate_entry(state_dir)
    if (
        entry
        and entry.get("gate") == current
        and entry.get("fromTarget") in ALL_TARGETS
    ):
      prev_t = str(entry["fromTarget"])
    else:
      na_path = os.path.join(state_dir, NEXT_ACTION_FILENAME)
      if os.path.isfile(na_path):
        try:
          with open(na_path, "r", encoding="utf-8") as f:
            m_prev = _LAST_COMPLETED_LINE_RE.search(f.read())
          if m_prev and f"T{m_prev.group(1)}" in ALL_TARGETS:
            prev_t = f"T{m_prev.group(1)}"
        except (OSError, UnicodeDecodeError):
          pass

  skipped_targets = _resolve_skipped_targets(state_dir, current, prev_t)
  write_active_target(
      state_dir,
      _build_active_target_payload(prev_t, current, skipped_targets),
  )
  render_next_action_card(
      RenderCardSpec(
          state_dir=state_dir,
          active_target=current,
          previous_target=prev_t,
          workspace_dir=workspace_dir,
          complete=False,
          skipped_targets=skipped_targets,
      )
  )


def restore_session_state(
    state_dir: str, workspace_dir: str = ""
) -> Tuple[bool, str]:
  """Inspects and reconciles Target 00 (read-init-d) session state on disk.

  Leaves uninitialized (0-card) and completed (COMPLETE) workspaces completely
  untouched so is_ticket_active remains False, and reconciles active_target.json
  and 99_next_action.md when an active mid-flight ticket is present.

  Args:
    state_dir: Path to .workflow/state directory.
    workspace_dir: Optional Git repository root directory.

  Returns:
    Tuple of (True, status_message).
  """
  if not state_dir or not os.path.isdir(state_dir) or not is_ticket_active(
      state_dir
  ):
    if state_dir and os.path.isdir(state_dir):
      stale_active = os.path.join(state_dir, ACTIVE_TARGET_FILENAME)
      if os.path.isfile(stale_active):
        try:
          os.unlink(stale_active)
        except OSError:
          pass
    return True, "SUCCESS: State directory is clean and ready for Target 10 intake."

  current = infer_current_target(state_dir)
  if current == "T00":
    return True, "SUCCESS: State directory is clean and ready for Target 10 intake."

  eff_ws = _resolve_effective_workspace(workspace_dir, state_dir)
  _restore_active_session_state(state_dir, current, eff_ws)
  return True, f"SUCCESS: Target 00 (read-init-d) restored active target {current}."


def advance_workflow_target(
    current: str,
    next_t: str,
    evidence: Optional[Dict[str, Any]] = None,
    workspace_dir: str = "",
    state_dir: str = "",
) -> Tuple[bool, str]:
  """Validates and executes a Makefile DAG target transition.

  Args:
    current: Current canonical target ID (T00..T90).
    next_t: Requested next canonical target ID (T10..T90).
    evidence: Optional dictionary of gate approvals, blocker counts, and flags.
    workspace_dir: Optional repository root directory for git/sync checks.
    state_dir: Optional .workflow/state directory for persistence.

  Returns:
    Tuple of (ok, status_or_error_message).
  """
  ev = dict(evidence) if isinstance(evidence, dict) else {}
  err = _validate_transition_inputs(
      current, next_t, ev, state_dir, workspace_dir=workspace_dir
  )
  if err is not None:
    return False, err

  eff_ws = _resolve_effective_workspace(workspace_dir, state_dir)

  sync_ok, sync_msg = _evaluate_upstream_sync(current, next_t, eff_ws, ev)
  if not sync_ok:
    return False, sync_msg

  transcript_path = str(ev.get("transcriptPath") or "").strip()
  gate_key = GATE_EVIDENCE_KEYS.get((current, next_t))
  if gate_key:
    gate_ok, gate_reason = verify_mcp_gate_exit(
        state_dir, current, next_t, transcript_path=transcript_path
    )
    if not gate_ok:
      return False, gate_reason

  if (
      current == "T50"
      and next_t in ("T60", "T70")
      and state_dir
      and os.path.isdir(state_dir)
      and eff_ws
      and os.path.isdir(eff_ws)
      and os.path.exists(os.path.join(eff_ws, ".git"))
  ):
    post_res = validate_head_commit(eff_ws, state_dir=state_dir)
    if not post_res.ok:
      return (
          False,
          f"BLOCKED: [SCAR-{post_res.scar_id}] {post_res.violation_code} on"
          f" transition {current} -> {next_t}: {post_res.reason}",
      )

  if state_dir and os.path.isdir(state_dir):
    if bool(ev.get("complete")) and next_t == "T90":
      _finalize_completed_ticket(state_dir, eff_ws, current)
    else:
      skipped_targets = _resolve_skipped_targets(state_dir, next_t, current)
      _persist_active_transition(
          RenderCardSpec(
              state_dir=state_dir,
              active_target=next_t,
              previous_target=current,
              workspace_dir=eff_ws,
              skipped_targets=skipped_targets,
          ),
          gate_key,
          transcript_path,
      )

  return True, f"SUCCESS: Target transition {current} -> {next_t} passed.{sync_msg}"


def _build_make_advance_env(
    current: str,
    next_t: str,
    ev: Dict[str, Any],
) -> Dict[str, str]:
  """Builds sanitized process environment for GNU Make target execution.

  Args:
    current: Source target ID (T00..T90).
    next_t: Destination target ID (T00..T90).
    ev: Transition evidence dictionary.

  Returns:
    Copied environment stripped of Make recursion and stale Makeflow
    variables, populated with the active transition variables.
  """
  env = sanitize_make_subprocess_env(os.environ)

  gate_key = GATE_EVIDENCE_KEYS.get((current, next_t))
  env["PYTHON"] = sys.executable
  env["FROM"] = current
  env["TO"] = next_t
  env["APPROVED"] = "1" if (gate_key and ev.get(gate_key)) else "0"
  if (current, next_t) in (("T40", "T45"), ("T40", "T42")) or (
      "blockersCount" in ev
  ):
    env["BLOCKERS"] = str(ev.get("blockersCount", 1))
  if ev.get("allowMainConflict"):
    env["ALLOW_MAIN_CONFLICT"] = "1"
  if ev.get("skipMainFetch"):
    env["NO_FETCH"] = "1"
  transcript = str(ev.get("transcriptPath") or "").strip()
  if transcript:
    env["TRANSCRIPT"] = transcript
  reg_cmd = str(ev.get("regressionCmd") or "").strip()
  if reg_cmd:
    env["REGRESSION_CMD"] = reg_cmd
  subproject = str(ev.get("subproject") or "").strip()
  if subproject:
    env["SUBPROJECT"] = subproject
  return env


def _resolve_make_advance_timeout(
    next_t: str,
    ev: Dict[str, Any],
    workspace_dir: str,
) -> float:
  """Resolves GNU Make subprocess timeout aligned with regression timeout config.

  Args:
    next_t: Destination target ID (T00..T90).
    ev: Transition evidence dictionary.
    workspace_dir: Repository root directory for loading workflow config.

  Returns:
    Timeout in seconds (30.0 by default, or regressionTimeoutSeconds + 5.0 when
    running an explicit regression command during T30 -> T40).
  """
  if next_t == "T40" and str(ev.get("regressionCmd") or "").strip():
    ws = workspace_dir if workspace_dir and os.path.isdir(workspace_dir) else None
    cfg = resolve_workflow_config(workspace_dir=ws)
    testing_cfg = cfg.get("testing") if isinstance(cfg, dict) else None
    raw_to = (
        testing_cfg.get("regressionTimeoutSeconds")
        if isinstance(testing_cfg, dict)
        else None
    )
    if (
        not isinstance(raw_to, bool)
        and isinstance(raw_to, (int, float))
        and math.isfinite(float(raw_to))
        and float(raw_to) >= 1
    ):
      return float(raw_to) + 5.0
    return 305.0
  return 30.0


def _terminate_make_process_group(proc: subprocess.Popen) -> None:
  """Terminates a GNU Make process group with SIGTERM then SIGKILL escalation.

  Args:
    proc: Running subprocess.Popen instance started in its own session.
  """
  if os.name != "nt":
    try:
      os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
      pass
    try:
      proc.wait(timeout=0.5)
      return
    except (subprocess.TimeoutExpired, OSError):
      pass
    try:
      os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
      pass
  try:
    proc.kill()
  except (ProcessLookupError, OSError):
    pass


def run_makefile_advance(
    current: str,
    next_t: str,
    evidence: Optional[Dict[str, Any]] = None,
    workspace_dir: str = "",
    state_dir: str = "",
) -> Tuple[bool, str]:
  """Executes a real GNU Make target transition against <state_dir>/Makefile.

  Args:
    current: Source target ID (T00..T90).
    next_t: Destination target ID (T00..T90).
    evidence: Optional dictionary of gate approvals, blocker counts, and flags.
    workspace_dir: Repository root working directory for make execution.
    state_dir: Directory containing the synthesized Makefile.

  Returns:
    Tuple of (ok, status_or_blocked_message).
  """
  ev = dict(evidence) if isinstance(evidence, dict) else {}
  err = _validate_transition_inputs(
      current, next_t, ev, state_dir, workspace_dir=workspace_dir
  )
  if err is not None:
    return False, err

  makefile_path = os.path.join(state_dir, "Makefile") if state_dir else ""
  if not state_dir or not os.path.isfile(makefile_path):
    return advance_workflow_target(
        current,
        next_t,
        evidence=ev,
        workspace_dir=workspace_dir,
        state_dir=state_dir,
    )

  ws = _resolve_effective_workspace(
      workspace_dir, state_dir, require_git=False
  )
  target_name = (
      "finish"
      if next_t == "T90" and ev.get("complete")
      else TARGET_ID_TO_NAME[next_t]
  )
  cmd = [
      "make",
      "-s",
      "-f",
      makefile_path,
      target_name,
      f"PYTHON={sys.executable}",
  ]
  env = _build_make_advance_env(current, next_t, ev)
  timeout_seconds = _resolve_make_advance_timeout(next_t, ev, ws)
  proc: Optional[subprocess.Popen] = None
  try:
    proc = subprocess.Popen(
        cmd,
        cwd=ws if os.path.isdir(ws) else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=(os.name != "nt"),
    )
    try:
      stdout_data, stderr_data = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
      _terminate_make_process_group(proc)
      try:
        proc.communicate(timeout=2)
      except (subprocess.SubprocessError, OSError):
        pass
      return (
          False,
          f"BLOCKED: GNU Make target '{target_name}' timed out after"
          f" {timeout_seconds:.1f}s.",
      )
  except (OSError, subprocess.SubprocessError) as exc:
    return False, f"BLOCKED: GNU Make execution failed: {exc}"
  finally:
    if proc is not None:
      if proc.poll() is None:
        _terminate_make_process_group(proc)
        try:
          proc.wait(timeout=2)
        except (subprocess.SubprocessError, OSError):
          pass
      for stream in (proc.stdout, proc.stderr):
        if stream is not None:
          try:
            stream.close()
          except OSError:
            pass

  if proc.returncode == 0:
    for line in reversed((stdout_data or "").splitlines()):
      if line.strip().startswith("SUCCESS:"):
        return True, line.strip()
    return True, f"SUCCESS: Target transition {current} -> {next_t} passed."

  combined = "\n".join(
      part.strip()
      for part in (stderr_data or "", stdout_data or "")
      if part and part.strip()
  )
  if not combined:
    combined = f"GNU Make target failed with exit code {proc.returncode}."
  if not combined.startswith("BLOCKED:"):
    combined = f"BLOCKED: {combined}"
  return False, combined
