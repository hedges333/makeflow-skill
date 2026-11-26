"""Target 10 intake hygiene, release lifecycle, and TDD receipt cleanup (GH-274).

Validates Git working-tree cleanliness, ``.git/info/exclude`` rules, leftover
state-card hygiene, multi-workspace mutex locks, remote conflict and blocker
checks, structured ``10_ticket.json`` persistence (SCAR-ARCH-10), and receipt
archival/cleanup on release.
"""

from dataclasses import dataclass
import functools
import os
import re
import subprocess
from typing import Callable, Optional, TextIO

from lib.active_state import ActiveStateStore
from lib.main_sync import _run_repo_git
from lib.reflex import GateFailure
from lib.ticket_state import canonicalize_ticket_id

_LEFTOVER_CARD_RE = re.compile(r"^(?:[1-8][0-9]_.*|approved_tree\.txt$)")

GitRunner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class IntakeGateRequest:
  """Parameter object encapsulating Target 10 intake inputs (SCAR-ARCH-0014).

  Attributes:
    repo_root: Workspace Git repository root directory.
    ticket: Optional explicit ticket identifier (for example ``GH-274``).
    lock_dir: Optional override directory for multi-workspace mutex locks.
    allow_blocked: When True, permit intake despite open upstream blockers.
  """

  repo_root: str
  ticket: Optional[str] = None
  lock_dir: Optional[str] = None
  allow_blocked: bool = False


@dataclass(frozen=True)
class ReleaseIntakeRequest:
  """Parameter object encapsulating release-intake inputs (SCAR-ARCH-0014).

  Attributes:
    repo_root: Workspace Git repository root directory.
    ticket: Optional explicit ticket identifier to release.
    lock_dir: Optional override directory for multi-workspace mutex locks.
    directory: Optional terminal title directory name override.
    stderr: Optional writable stream for cleanup warnings and title output.
  """

  repo_root: str
  ticket: Optional[str] = None
  lock_dir: Optional[str] = None
  directory: Optional[str] = None
  stderr: Optional[TextIO] = None


def run_intake_gate(
    request: IntakeGateRequest,
    git_runner: Optional[GitRunner] = None,
) -> Optional[GateFailure]:
  """Validates intake preconditions, acquires canonical mutex lock, and persists 10_ticket.json.

  Args:
    request: Frozen ``IntakeGateRequest`` with workspace and ticket options.
    git_runner: Optional callable executing ``git *args`` inside
      ``request.repo_root``. Defaults to a local subprocess runner.

  Returns:
    ``None`` when intake succeeds; otherwise a ``GateFailure`` describing the
    precondition, collision, or blocker failure.
  """
  run_git = (
      git_runner
      if git_runner is not None
      else functools.partial(_run_repo_git, request.repo_root, timeout=15)
  )
  repo_root = request.repo_root

  status_proc = run_git("status", "--porcelain")
  if status_proc.returncode != 0:
    return GateFailure(
        scar_id="GIT-25",
        code="UNCLEAN_INTAKE_STATE",
        trigger=f"git status failed in {repo_root}: {(status_proc.stderr or '').strip()}",
        rejected="Starting Target 10 intake outside a valid Git working tree.",
        reflex="Open a valid Git repository checkout before running make -f .workflow/state/Makefile check-intake.",
    )
  dirty_lines = [ln for ln in status_proc.stdout.splitlines() if ln.strip()]
  if dirty_lines:
    return GateFailure(
        scar_id="GIT-25",
        code="UNCLEAN_INTAKE_STATE",
        trigger=f"Working tree has uncommitted or untracked files ({len(dirty_lines)} item(s)).",
        rejected="Creating a new ticket branch on a dirty working tree.",
        reflex="Inspect git status --short and commit or stash changes without destructive git commands before Target 10.",
    )

  unexcluded = []
  for rel_dir in (".workflow/state/", ".workflow/archive/"):
    ign_proc = run_git("check-ignore", "-q", rel_dir)
    if ign_proc.returncode != 0:
      unexcluded.append(rel_dir)
  if unexcluded:
    missing_excl = ", ".join(unexcluded)
    return GateFailure(
        scar_id="GIT-25",
        code="UNCLEAN_INTAKE_STATE",
        trigger=f"Local workflow path(s) not excluded in Git: {missing_excl}",
        rejected="Allowing Memento state cards or archives to leak into Git commits.",
        reflex="Append .workflow/state/ and .workflow/archive/ to .git/info/exclude and re-run make -f .workflow/state/Makefile check-intake.",
    )

  state_dir = os.path.join(repo_root, ".workflow", "state")
  if os.path.isdir(state_dir):
    from lib.ticket_state import TICKET_JSON_FILENAME, TicketStateStore

    leftovers = sorted(
        fname
        for fname in os.listdir(state_dir)
        if _LEFTOVER_CARD_RE.match(fname)
    )
    if leftovers == [TICKET_JSON_FILENAME] and request.ticket:
      existing_meta = TicketStateStore(state_dir).load()
      requested_ticket_id = canonicalize_ticket_id(request.ticket)
      existing_ticket_id = (
          canonicalize_ticket_id(existing_meta.ticket_id)
          if existing_meta is not None
          else ""
      )
      if (
          requested_ticket_id
          and existing_ticket_id
          and existing_ticket_id.lower() == requested_ticket_id.lower()
      ):
        leftovers = []
    if leftovers:
      return GateFailure(
          scar_id="GIT-25",
          code="UNCLEAN_INTAKE_STATE",
          trigger=f"Leftover intermediate state file(s) in .workflow/state/: {', '.join(leftovers)}",
          rejected="Starting a new ticket while prior 10_..80_ state cards remain active.",
          reflex="Archive or unlink intermediate state files per state.completedCardAction before saving 10_ticket.json.",
      )

  ticket_id = request.ticket
  if not ticket_id:
    try:
      from lib.terminal_title import detect_ticket_id
      ticket_id = detect_ticket_id(repo_root=repo_root)
    except (ImportError, OSError, UnicodeDecodeError):
      ticket_id = ""
  ticket_id = canonicalize_ticket_id(ticket_id)

  if ticket_id:
    from lib.ticket_state import TicketMetadata, TicketStateStore
    from lib.workspace_mutex import WorkspaceMutex

    mutex = WorkspaceMutex(repo_root=repo_root, lock_dir=request.lock_dir)
    branch_proc = run_git("branch", "--show-current")
    branch_name = (
        branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""
    )
    res = mutex.acquire(
        ticket_id=ticket_id,
        workspace_path=repo_root,
        branch_name=branch_name,
    )
    if not res.get("acquired"):
      sess = res.get("session") or {}
      colliding_workspace = sess.get("workspace_path", "unknown")
      colliding_pid = sess.get("pid", "unknown")
      return GateFailure(
          scar_id="SYS-08",
          code="TICKET_COLLISION_DETECTED",
          trigger=(
              f"Ticket {ticket_id} is actively held by PID {colliding_pid} "
              f"in {colliding_workspace}."
          ),
          rejected=f"Concurrent intake on ticket {ticket_id} across multiple workspaces.",
          reflex=(
              "Coordinate with the engineer in the active workspace or wait "
              "for session completion before proceeding."
          ),
      )

    if not res.get("reentrant"):
      hosting_mode = "GITHUB"
      if ticket_id.startswith("LOCAL-"):
        hosting_mode = "PURE_GIT"
      else:
        origin_proc = run_git("remote", "get-url", "origin")
        if (
            origin_proc.returncode != 0
            or "github.com" not in (origin_proc.stdout or "").lower()
        ):
          hosting_mode = "PURE_GIT"

      rem = mutex.check_remote(ticket_id=ticket_id, hosting_mode=hosting_mode)
      if rem.get("conflict"):
        mutex.release(ticket_id)
        reason = rem.get("reason", "REMOTE_CONFLICT")
        if reason == "REMOTE_ASSIGNED_OR_IN_PROGRESS":
          assignees = ", ".join(rem.get("assignees", [])) or "none"
          labels = ", ".join(rem.get("labels", [])) or "none"
          trigger_msg = (
              f"Remote ticket {ticket_id} has active assignments or labels: "
              f"assignees=[{assignees}], labels=[{labels}]."
          )
        elif reason == "REMOTE_BRANCH_EXISTS":
          refs = ", ".join(rem.get("refs", []))
          trigger_msg = f"Remote branch exists for ticket {ticket_id}: {refs}."
        else:
          trigger_msg = (
              f"Remote conflict detected for ticket {ticket_id}: {reason}."
          )

        return GateFailure(
            scar_id="SYS-08",
            code="REMOTE_TICKET_COLLISION",
            trigger=trigger_msg,
            rejected=f"Concurrent intake on ticket {ticket_id} with remote assignment or branch.",
            reflex="Coordinate with the ticket assignee or remove remote conflict before intake.",
        )

      dep = mutex.check_dependencies(
          ticket_id=ticket_id, hosting_mode=hosting_mode
      )
      if dep.get("blocked") and not request.allow_blocked:
        mutex.release(ticket_id)
        blockers = dep.get("open_blockers", [])
        if blockers:
          details = ", ".join(
              f"#{b.get('number')} ({b.get('title')})" for b in blockers
          )
        else:
          details = f"{dep.get('blocking_count', 0)} open issue(s)"
        return GateFailure(
            scar_id="SYS-08",
            code="INTAKE_DEPENDENCY_BLOCKED",
            trigger=f"Ticket {ticket_id} is blocked by open upstream issue(s): {details}.",
            rejected=f"Starting intake on ticket {ticket_id} with open blocking dependencies.",
            reflex=(
                "Resolve upstream blocking issues before intake, or pass "
                "--allow-blocked (or ALLOW_BLOCKED=1 in Makefile) after conducting "
                "an operator interview to investigate dependency coupling."
            ),
        )

      if hosting_mode == "GITHUB" and not ticket_id.startswith("LOCAL-"):
        try:
          mutex.claim_issue(ticket_id)
        except (OSError, subprocess.SubprocessError):
          pass

    mutex.register_signal_trap(ticket_id)

    ticket_store = TicketStateStore.from_repo_root(repo_root)
    existing_ticket = ticket_store.load() if res.get("reentrant") else None
    if (
        res.get("reentrant")
        and existing_ticket is not None
        and existing_ticket.ticket_id.strip().lower()
        == ticket_id.strip().lower()
    ):
      ticket_payload = None
    elif ticket_id.upper().startswith("LOCAL-"):
      from lib.local_tickets import LocalTicketStore

      local_store = LocalTicketStore(
          repo_root=repo_root, lock_dir=request.lock_dir
      )
      local_record = local_store.get_ticket(ticket_id)
      if isinstance(local_record, dict):
        ticket_payload = dict(local_record)
        ticket_payload.setdefault("ticket_id", ticket_id)
        if branch_name and not ticket_payload.get("branch"):
          ticket_payload["branch"] = branch_name
        ticket_payload.setdefault("source", "local")
      else:
        ticket_payload = TicketMetadata(
            ticket_id=ticket_id,
            branch=branch_name,
            source="local",
        )
    elif isinstance(mutex.last_remote_issue, dict) and mutex.last_remote_issue:
      ticket_payload = dict(mutex.last_remote_issue)
      ticket_payload.setdefault("ticket_id", ticket_id)
      if branch_name and not ticket_payload.get("branch"):
        ticket_payload["branch"] = branch_name
      ticket_payload.setdefault("source", "github")
    else:
      ticket_payload = TicketMetadata(
          ticket_id=ticket_id,
          branch=branch_name,
          source="fallback",
      )

    if ticket_payload is not None:
      try:
        ticket_store.save(ticket_payload)
      except OSError:
        pass

  if os.path.isdir(state_dir):
    stale_subagent = os.path.join(state_dir, "active_subagent.json")
    if os.path.isfile(stale_subagent):
      try:
        os.unlink(stale_subagent)
      except OSError:
        pass

  try:
    from lib.terminal_title import set_terminal_title
    set_terminal_title(repo_root=repo_root)
  except (ImportError, OSError):
    pass
  return None


def cleanup_tdd_receipt(
    repo_root: str,
    ticket_id: str,
    stderr: Optional[TextIO] = None,
) -> None:
  """Archives or unlinks TDD/regression receipts and transient lock files on intake release.

  Args:
    repo_root: Workspace Git repository root directory.
    ticket_id: Active ticket identifier used as the archive subdirectory slug.
    stderr: Optional writable stream for non-fatal cleanup warnings.

  Returns:
    ``None`` after archiving or unlinking matching state artifacts.
  """
  from lib.config import is_ticket_active

  state_dir = os.path.join(repo_root, ".workflow", "state")
  ticket_active = is_ticket_active(state_dir)
  for transient in ("gate_entry.json", "active_subagent.json"):
    transient_path = os.path.join(state_dir, transient)
    if os.path.isfile(transient_path):
      try:
        os.unlink(transient_path)
      except OSError:
        pass
  if not ticket_active:
    try:
      ActiveStateStore(state_dir).clear()
    except OSError:
      pass

  json_artifacts = (
      "30_tdd_receipt.json",
      "30_regression_receipt.json",
      "loaded_scars.json",
  )
  existing = [
      name
      for name in json_artifacts
      if os.path.isfile(os.path.join(state_dir, name))
  ]
  if not existing:
    return

  completed_action = "unlink"
  try:
    from lib.config import resolve_workflow_config
    cfg = resolve_workflow_config(workspace_dir=repo_root)
    completed_action = (
        cfg.get("state", {}).get("completedCardAction", "unlink")
        if isinstance(cfg, dict)
        else "unlink"
    )
  except (ImportError, OSError):
    completed_action = "unlink"

  archive_ticket = (
      re.sub(r"[^A-Za-z0-9._-]", "_", ticket_id or "").strip("._") or "unknown"
  )

  for artifact_name in existing:
    artifact_path = os.path.join(state_dir, artifact_name)
    try:
      if completed_action == "archive":
        from lib.safe_copy import safe_copy_file
        archive_dst = os.path.join(
            repo_root,
            ".workflow",
            "archive",
            archive_ticket,
            artifact_name,
        )
        safe_copy_file(artifact_path, archive_dst)
      os.unlink(artifact_path)
    except (ImportError, OSError) as exc:
      if stderr is not None:
        stderr.write(f"Warning: failed to clean up {artifact_path}: {exc}\n")


def run_release_intake_gate(request: ReleaseIntakeRequest) -> int:
  """Releases canonical workspace mutex locks, cleans up receipts, and restores terminal title.

  Args:
    request: Frozen ``ReleaseIntakeRequest`` describing workspace and lock
      settings.

  Returns:
    Exit code ``0`` after releasing intake resources.
  """
  repo_root = request.repo_root
  ticket_id = request.ticket
  if not ticket_id:
    try:
      from lib.terminal_title import detect_ticket_id
      ticket_id = detect_ticket_id(repo_root=repo_root)
    except (ImportError, OSError, UnicodeDecodeError):
      ticket_id = ""
  ticket_id = canonicalize_ticket_id(ticket_id)

  try:
    from lib.workspace_mutex import WorkspaceMutex
    mutex = WorkspaceMutex(repo_root=repo_root, lock_dir=request.lock_dir)
    if ticket_id:
      mutex.release(ticket_id)
    released_tickets = mutex.release_workspace(repo_root)
    if not ticket_id and released_tickets:
      ticket_id = canonicalize_ticket_id(released_tickets[0])
  except (ImportError, OSError):
    pass

  cleanup_tdd_receipt(repo_root, ticket_id or "", stderr=request.stderr)

  try:
    from lib.terminal_title import (
        detect_directory_name,
        format_terminal_title,
        is_temporary_directory_name,
        set_terminal_title,
    )
    dir_name = (
        request.directory
        if request.directory is not None
        else detect_directory_name(repo_root=repo_root)
    )
    if dir_name and not is_temporary_directory_name(dir_name):
      restored_title = format_terminal_title("", dir_name)
      if restored_title:
        set_terminal_title(
            title=restored_title, repo_root=repo_root, stream=request.stderr
        )
  except (ImportError, OSError):
    pass
  return 0
