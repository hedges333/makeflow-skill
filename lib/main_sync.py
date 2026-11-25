"""Upstream origin/main divergence inspection and 3-way merge-tree gate (GH-274).

Provides non-destructive upstream sync inspection using an ephemeral Git index
(``lib.git_index.ephemeral_git_index``) and deterministic gate decision
evaluation for ``main-sync`` and state-machine transitions.
"""

from dataclasses import dataclass
import os
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib.git_index import ephemeral_git_index
from lib.reflex import GateFailure

_MERGE_TREE_STAGE_RE = re.compile(
    r"^  (base|our|their)\s+(\d+)\s+([0-9a-f]+)\s+(.+)$"
)
_MERGE_TREE_HEADER_RE = re.compile(
    r"^(?:changed in both|added in both|removed in local|removed in remote|"
    r"merged|added in local|added in remote|removed in both)$"
)


@dataclass(frozen=True)
class MainSyncDecision:
  """Structured decision produced by ``evaluate_main_sync_gate``.

  Attributes:
    ok: True when the gate passes (exit 0); False when a Vector 4 Reflex fails.
    sync: Raw upstream sync dictionary from ``inspect_main_sync``.
    notice: Optional human-readable stdout warning/notice line for text mode.
    failure: Populated ``GateFailure`` when ``ok`` is False; ``None`` otherwise.
  """

  ok: bool
  sync: Dict[str, Any]
  notice: str = ""
  failure: Optional[GateFailure] = None


def _no_remote_sync_result() -> Dict[str, Any]:
  """Returns the default sync payload when no upstream origin/main ref exists."""
  return {
      "status": "NO_REMOTE",
      "upstream_ref": None,
      "ahead_count": 0,
      "behind_count": 0,
      "has_conflicts": False,
      "conflicting_files": [],
      "working_tree_dirty": False,
  }


def _run_repo_git(
    repo_root: str,
    *git_args: str,
    timeout: int = 10,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
  """Runs a Git subprocess inside ``repo_root`` without raising on failure."""
  try:
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=timeout,
    )
  except (subprocess.SubprocessError, OSError) as exc:
    return subprocess.CompletedProcess(
        args=["git", *git_args],
        returncode=128,
        stdout="",
        stderr=str(exc),
    )


def _resolve_upstream_main_branch(repo_root: str) -> Optional[str]:
  """Resolves the default upstream branch name ('main' or 'master') on origin."""
  sym_proc = _run_repo_git(
      repo_root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"
  )
  if sym_proc.returncode == 0:
    sym_ref = (sym_proc.stdout or "").strip()
    if sym_ref.startswith("origin/"):
      branch = sym_ref.split("/", 1)[1].strip()
      if branch:
        return branch

  for candidate in ("main", "master"):
    ver_proc = _run_repo_git(
        repo_root, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{candidate}"
    )
    if ver_proc.returncode == 0:
      return candidate
  return None


def _parse_merge_tree_conflicts(merge_tree_stdout: str) -> List[str]:
  """Extracts sorted conflicting file paths from 3-way git merge-tree output."""
  conflicts: List[str] = []
  current_header = ""
  current_paths: List[str] = []
  current_stages: Dict[str, Tuple[str, str]] = {}
  current_has_conflict_marker = False

  def _flush_block() -> None:
    if not current_header or not current_paths:
      return
    base_entry = current_stages.get("base")
    our_entry = current_stages.get("our")
    their_entry = current_stages.get("their")
    is_delete_conflict = False
    if current_header == "removed in remote":
      is_delete_conflict = (
          base_entry is None or our_entry is None or our_entry != base_entry
      )
    elif current_header == "removed in local":
      is_delete_conflict = (
          base_entry is None or their_entry is None or their_entry != base_entry
      )
    elif current_header == "added in both":
      is_delete_conflict = (
          our_entry is None or their_entry is None or our_entry != their_entry
      )
    if current_has_conflict_marker or is_delete_conflict:
      for path in current_paths:
        if path not in conflicts:
          conflicts.append(path)

  for line in (merge_tree_stdout or "").splitlines():
    if _MERGE_TREE_HEADER_RE.match(line):
      _flush_block()
      current_header = line.strip()
      current_paths = []
      current_stages = {}
      current_has_conflict_marker = False
      continue

    m_stage = _MERGE_TREE_STAGE_RE.match(line)
    if m_stage:
      stage_role = m_stage.group(1)
      mode = m_stage.group(2)
      sha = m_stage.group(3)
      path = m_stage.group(4).strip()
      current_stages[stage_role] = (mode, sha)
      if path and path not in current_paths:
        current_paths.append(path)
      continue

    if line.startswith("+<<<<<<<") or "Cannot merge binary files" in line:
      current_has_conflict_marker = True

  _flush_block()
  return sorted(conflicts)


def inspect_main_sync(
    repo_root: str,
    ephemeral_index_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
  """Inspects local upstream origin/main divergence and 3-way merge-tree conflicts without fetching.

  Uses an ephemeral ``GIT_INDEX_FILE`` seeded via native Git plumbing when
  unstaged or staged tracked changes exist so ``.git/index``,
  ``.git/sharedindex.*``, ``HEAD``, and ``.workflow/state/approved_tree.txt``
  are never mutated.

  Args:
    repo_root: Workspace Git repository root directory.
    ephemeral_index_fn: Optional context-manager factory overriding
      ``ephemeral_git_index`` for dependency injection and monkeypatch parity.

  Returns:
    Dictionary describing upstream sync status, ahead/behind counts, conflict
    state, conflicting file paths, and working tree cleanliness.
  """
  if not repo_root or not os.path.isdir(repo_root):
    return _no_remote_sync_result()

  origin_proc = _run_repo_git(repo_root, "remote", "get-url", "origin")
  if origin_proc.returncode != 0 or not (origin_proc.stdout or "").strip():
    return _no_remote_sync_result()

  branch = _resolve_upstream_main_branch(repo_root)
  if not branch:
    return _no_remote_sync_result()

  upstream_ref = f"origin/{branch}"
  ver_upstream = _run_repo_git(
      repo_root, "rev-parse", "--verify", "-q", f"refs/remotes/{upstream_ref}"
  )
  if ver_upstream.returncode != 0:
    return _no_remote_sync_result()

  ver_head = _run_repo_git(repo_root, "rev-parse", "--verify", "-q", "HEAD")
  if ver_head.returncode != 0:
    return _no_remote_sync_result()

  status_proc = _run_repo_git(
      repo_root, "--no-optional-locks", "status", "--porcelain"
  )
  status_lines = [
      ln for ln in (status_proc.stdout or "").splitlines() if ln.strip()
  ]
  working_tree_dirty = bool(status_lines)
  has_tracked_changes = any(not ln.startswith("??") for ln in status_lines)

  rev_proc = _run_repo_git(
      repo_root, "rev-list", "--left-right", "--count", f"HEAD...{upstream_ref}"
  )
  ahead_count = 0
  behind_count = 0
  if rev_proc.returncode == 0:
    parts = (rev_proc.stdout or "").strip().split()
    if len(parts) == 2:
      try:
        ahead_count = int(parts[0])
        behind_count = int(parts[1])
      except ValueError:
        ahead_count = 0
        behind_count = 0

  if behind_count == 0:
    return {
        "status": "UP_TO_DATE",
        "upstream_ref": upstream_ref,
        "ahead_count": ahead_count,
        "behind_count": 0,
        "has_conflicts": False,
        "conflicting_files": [],
        "working_tree_dirty": working_tree_dirty,
    }

  mb_proc = _run_repo_git(repo_root, "merge-base", "HEAD", upstream_ref)
  merge_base = (mb_proc.stdout or "").strip() if mb_proc.returncode == 0 else ""
  if not merge_base:
    return {
        "status": "BEHIND_CLEAN",
        "upstream_ref": upstream_ref,
        "ahead_count": ahead_count,
        "behind_count": behind_count,
        "has_conflicts": False,
        "conflicting_files": [],
        "working_tree_dirty": working_tree_dirty,
    }

  index_ctx_factory = (
      ephemeral_index_fn if ephemeral_index_fn is not None else ephemeral_git_index
  )
  local_tree = "HEAD"
  if has_tracked_changes:
    try:
      with index_ctx_factory(repo_root, timeout=10) as env:
        add_proc = _run_repo_git(
            repo_root, "-c", "core.splitIndex=false", "add", "-u", env=env
        )
        if add_proc.returncode == 0:
          wt_proc = _run_repo_git(
              repo_root, "-c", "core.splitIndex=false", "write-tree", env=env
          )
          wt_sha = (wt_proc.stdout or "").strip()
          if wt_proc.returncode == 0 and wt_sha:
            local_tree = wt_sha
    except (RuntimeError, OSError):
      local_tree = "HEAD"

  mt_proc = _run_repo_git(
      repo_root, "merge-tree", merge_base, local_tree, upstream_ref
  )
  conflicting_files = _parse_merge_tree_conflicts(mt_proc.stdout or "")
  has_conflicts = bool(conflicting_files)
  return {
      "status": "CONFLICT" if has_conflicts else "BEHIND_CLEAN",
      "upstream_ref": upstream_ref,
      "ahead_count": ahead_count,
      "behind_count": behind_count,
      "has_conflicts": has_conflicts,
      "conflicting_files": conflicting_files,
      "working_tree_dirty": working_tree_dirty,
  }


def fetch_and_inspect_main_sync(
    repo_root: str,
    inspect_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
  """Fetches origin's default branch and inspects upstream divergence and 3-way merge conflicts.

  Args:
    repo_root: Workspace Git repository root directory.
    inspect_fn: Optional inspection callable overriding ``inspect_main_sync``.

  Returns:
    Dictionary returned by ``inspect_fn`` (or ``inspect_main_sync``) after
    refreshing the remote-tracking ref.
  """
  if not repo_root or not os.path.isdir(repo_root):
    return _no_remote_sync_result()

  origin_proc = _run_repo_git(repo_root, "remote", "get-url", "origin")
  if origin_proc.returncode != 0 or not (origin_proc.stdout or "").strip():
    return _no_remote_sync_result()

  branch = _resolve_upstream_main_branch(repo_root)
  if branch:
    _run_repo_git(repo_root, "fetch", "-q", "origin", branch, timeout=10)
  else:
    for candidate in ("main", "master"):
      fetch_proc = _run_repo_git(
          repo_root, "fetch", "-q", "origin", candidate, timeout=10
      )
      if fetch_proc.returncode == 0:
        break

  runner = inspect_fn if inspect_fn is not None else inspect_main_sync
  return runner(repo_root)


def evaluate_main_sync_gate(
    sync: Dict[str, Any],
    *,
    fail_on_behind: bool = False,
    allow_conflict: bool = False,
) -> MainSyncDecision:
  """Evaluates an upstream main sync payload against gate policy flags.

  Args:
    sync: Sync dictionary returned by ``inspect_main_sync`` or
      ``fetch_and_inspect_main_sync``.
    fail_on_behind: When True, fail with ``UPSTREAM_MAIN_BEHIND`` if the branch
      is behind upstream main even without merge conflicts.
    allow_conflict: When True, permit ``CONFLICT`` status with a warning line
      instead of failing.

  Returns:
    A ``MainSyncDecision`` capturing pass/fail state, optional stdout notice,
    and a populated ``GateFailure`` when ``ok`` is False.
  """
  status = sync.get("status", "NO_REMOTE")
  upstream_ref = sync.get("upstream_ref") or "origin/main"
  behind_count = sync.get("behind_count", 0)
  conflicting_files = sync.get("conflicting_files") or []

  if status == "CONFLICT":
    files_str = ", ".join(conflicting_files) or "unknown files"
    if allow_conflict:
      return MainSyncDecision(
          ok=True,
          sync=sync,
          notice=(
              f"MAIN_SYNC_WARNING: Proceeding with operator conflict override "
              f"against {upstream_ref} ({files_str})."
          ),
      )
    return MainSyncDecision(
        ok=False,
        sync=sync,
        failure=GateFailure(
            scar_id="GIT-20",
            code="UPSTREAM_MAIN_CONFLICT",
            trigger=(
                f"Branch conflicts with {upstream_ref} (behind by {behind_count} commit(s); "
                f"conflicting files: {files_str})."
            ),
            rejected=(
                "Advancing or pushing a branch with unresolved 3-way merge conflicts "
                "against upstream main."
            ),
            reflex=(
                "Stop tool calls without altering Git state (SCAR-GIT-25), report conflicting "
                "files to the operator, and request approval to rebase onto origin/main and "
                "re-run Target 40 review."
            ),
        ),
    )

  if status == "BEHIND_CLEAN":
    if fail_on_behind:
      return MainSyncDecision(
          ok=False,
          sync=sync,
          failure=GateFailure(
              scar_id="GIT-20",
              code="UPSTREAM_MAIN_BEHIND",
              trigger=(
                  f"Branch is behind {upstream_ref} by {behind_count} commit(s) "
                  "with 0 merge conflicts."
              ),
              rejected="Proceeding on a stale branch base when strict upstream sync is required.",
              reflex=(
                  "Ask the human operator whether to rebase onto origin/main (SCAR-GIT-25) "
                  "before continuing."
              ),
          ),
      )
    return MainSyncDecision(
        ok=True,
        sync=sync,
        notice=(
            f"MAIN_SYNC_NOTICE: Branch is behind {upstream_ref} by {behind_count} "
            "commit(s) with 0 conflicts (BEHIND_CLEAN)."
        ),
    )

  return MainSyncDecision(ok=True, sync=sync)
