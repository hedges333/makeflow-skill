"""Target 75 ephemeral detached worktree trial-merge verification (GH-343).

Verifies semantic compatibility of BEHIND_CLEAN open pull requests against
upstream origin/main inside an ephemeral detached git worktree without mutating
the live workspace, PR branch, or .workflow/state/approved_tree.txt
(SCAR-GIT-0018, SCAR-GIT-20, SCAR-GIT-23, SCAR-GIT-25, SCAR-SYS-07).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
from typing import Any, Dict, Optional, Tuple

from lib.main_sync import (
    _run_repo_git,
    fetch_and_inspect_main_sync,
    inspect_main_sync,
)
from lib.reflex import GateFailure
from lib.regression_gate import (
    _resolve_timeout,
    _run_regression_shell,
    _single_line,
    _unlink_receipt,
    _write_receipt_atomic,
    resolve_regression_command,
    validate_subproject,
)
from lib.signal_trap import SignalTrap

DEFAULT_TRIAL_MERGE_RECEIPT_REL = os.path.join(
    ".workflow", "state", "75_trial_merge_receipt.json"
)
TRIAL_MERGE_RECEIPT_FILENAME = "75_trial_merge_receipt.json"
TRIAL_MERGE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TrialMergeDecision:
  """Structured decision produced by Target 75 trial-merge verification.

  Attributes:
    ok: True when trial-merge verification passed or was unnecessary.
    status: Machine-readable verification status (for example ``UP_TO_DATE``,
      ``PASSED``, ``CACHED``, ``SEMANTIC_CONFLICT``,
      ``MISSING_REGRESSION_COMMAND``, or ``WORKTREE_ERROR``).
    head_sha: Resolved Git HEAD SHA verified.
    upstream_main_sha: Resolved upstream main SHA verified.
    upstream_ref: Upstream remote tracking ref name (for example ``origin/main``).
    behind_count: Number of upstream commits ahead of HEAD.
    regression_cmd: Regression command executed against the combined tree.
    subproject: Normalized relative subproject path.
    exit_code: Regression subprocess exit code (0 on pass).
    upstream_delta: Output of ``git log --oneline --stat HEAD..<upstream_ref>``.
    receipt_path: Path to ``75_trial_merge_receipt.json`` when persisted.
    cached: True when satisfied by a fresh matching receipt without re-running.
    notice: Human-readable notice summary for stdout/transition messages.
    failure: Populated ``GateFailure`` when ``ok`` is False; ``None`` otherwise.
  """

  ok: bool
  status: str
  head_sha: Optional[str] = None
  upstream_main_sha: Optional[str] = None
  upstream_ref: Optional[str] = None
  behind_count: int = 0
  regression_cmd: str = ""
  subproject: str = "."
  exit_code: int = 0
  upstream_delta: str = ""
  receipt_path: str = ""
  cached: bool = False
  notice: str = ""
  failure: Optional[GateFailure] = None

  def to_dict(self) -> Dict[str, Any]:
    """Serialize the trial-merge decision into a JSON-compatible dictionary.

    Returns:
      Dictionary capturing verification status, SHAs, receipt metadata, delta
      summary, and optional structured failure details.
    """
    payload: Dict[str, Any] = {
        "ok": self.ok,
        "status": self.status,
        "head_sha": self.head_sha,
        "upstream_main_sha": self.upstream_main_sha,
        "upstream_ref": self.upstream_ref,
        "behind_count": self.behind_count,
        "regression_cmd": self.regression_cmd,
        "subproject": self.subproject,
        "exit_code": self.exit_code,
        "upstream_delta": self.upstream_delta,
        "receipt_path": self.receipt_path,
        "cached": self.cached,
        "notice": self.notice,
    }
    if self.failure is not None:
      payload["failure"] = {
          "scar_id": self.failure.scar_id,
          "code": self.failure.code,
          "trigger": self.failure.trigger,
          "rejected": self.failure.rejected,
          "reflex": self.failure.reflex,
      }
    return payload


def _resolve_receipt_path(
    repo_root: str, state_dir: Optional[str] = None
) -> str:
  """Resolve the canonical path for ``75_trial_merge_receipt.json``.

  Args:
    repo_root: Repository root directory.
    state_dir: Optional ``.workflow/state`` directory override.

  Returns:
    Absolute path to ``75_trial_merge_receipt.json``.
  """
  if state_dir:
    return os.path.join(os.path.abspath(state_dir), TRIAL_MERGE_RECEIPT_FILENAME)
  return os.path.join(os.path.abspath(repo_root), DEFAULT_TRIAL_MERGE_RECEIPT_REL)


def format_upstream_delta_summary(
    repo_root: str, upstream_ref: str = "origin/main"
) -> str:
  """Return ``git log --oneline --stat HEAD..<upstream_ref>`` summary text.

  Args:
    repo_root: Repository root directory.
    upstream_ref: Upstream remote tracking ref name.

  Returns:
    Trimmed commit log and diffstat string, or empty string when unavailable.
  """
  if not repo_root or not os.path.isdir(repo_root) or not upstream_ref:
    return ""
  proc = _run_repo_git(
      repo_root,
      "log",
      "--oneline",
      "--stat",
      f"HEAD..{upstream_ref}",
      timeout=15,
  )
  if proc.returncode != 0:
    return ""
  return (proc.stdout or "").strip()


def load_trial_merge_receipt(
    repo_root: str, state_dir: Optional[str] = None
) -> Optional[Dict[str, Any]]:
  """Load ``75_trial_merge_receipt.json`` if present and valid JSON.

  Args:
    repo_root: Repository root directory.
    state_dir: Optional ``.workflow/state`` directory override.

  Returns:
    Parsed receipt dictionary, or ``None`` when absent or malformed.
  """
  receipt_path = _resolve_receipt_path(repo_root, state_dir=state_dir)
  if not os.path.isfile(receipt_path):
    return None
  try:
    with open(receipt_path, "r", encoding="utf-8") as fh:
      data = json.load(fh)
    return data if isinstance(data, dict) else None
  except (OSError, ValueError, TypeError):
    return None


def _resolve_shas(
    repo_root: str,
    sync: Dict[str, Any],
    upstream_ref: str,
) -> Tuple[Optional[str], Optional[str]]:
  """Resolve ``(head_sha, upstream_sha)`` from sync payload with git fallback.

  Args:
    repo_root: Repository root directory.
    sync: Sync dictionary from ``inspect_main_sync``.
    upstream_ref: Upstream tracking branch name.

  Returns:
    Tuple of ``(head_sha, upstream_sha)``.
  """
  head_sha = sync.get("head_sha")
  upstream_sha = sync.get("upstream_sha")
  if not head_sha:
    proc_head = _run_repo_git(repo_root, "rev-parse", "--verify", "-q", "HEAD")
    if proc_head.returncode == 0:
      head_sha = (proc_head.stdout or "").strip() or None
  if not upstream_sha and upstream_ref:
    proc_up = _run_repo_git(
        repo_root, "rev-parse", "--verify", "-q", upstream_ref
    )
    if proc_up.returncode == 0:
      upstream_sha = (proc_up.stdout or "").strip() or None
  return head_sha, upstream_sha


def _is_receipt_fresh(
    receipt: Optional[Dict[str, Any]],
    *,
    head_sha: Optional[str],
    upstream_main_sha: Optional[str],
    subproject: str,
    explicit_cmd: str = "",
) -> bool:
  """Check whether a persisted trial-merge receipt matches current SHAs and scope.

  Args:
    receipt: Loaded receipt dictionary or ``None``.
    head_sha: Current Git HEAD commit SHA.
    upstream_main_sha: Current upstream main commit SHA.
    subproject: Normalized relative subproject path.
    explicit_cmd: Optional explicit regression command override.

  Returns:
    True when the receipt is verified and matches all active coordinates.
  """
  if not isinstance(receipt, dict) or not head_sha or not upstream_main_sha:
    return False
  if receipt.get("verified") is not True or receipt.get("exit_code") != 0:
    return False
  if receipt.get("head_sha") != head_sha:
    return False
  if receipt.get("upstream_main_sha") != upstream_main_sha:
    return False
  if str(receipt.get("subproject") or ".") != subproject:
    return False
  cleaned_explicit = (explicit_cmd or "").strip()
  if cleaned_explicit and receipt.get("regression_cmd") != cleaned_explicit:
    return False
  return True


def _build_conflict_decision(
    *,
    upstream_ref: str,
    behind_count: int,
    head_sha: Optional[str],
    upstream_sha: Optional[str],
    conflicting_files: Any,
    upstream_delta: str,
) -> TrialMergeDecision:
  """Build a failing ``TrialMergeDecision`` for textual 3-way merge conflicts.

  Args:
    upstream_ref: Upstream tracking branch name.
    behind_count: Number of commits behind upstream.
    head_sha: Current HEAD commit SHA.
    upstream_sha: Current upstream commit SHA.
    conflicting_files: Iterable of conflicting file paths.
    upstream_delta: Output of ``git log --oneline --stat HEAD..<upstream_ref>``.

  Returns:
    Failing ``TrialMergeDecision`` with ``UPSTREAM_MAIN_CONFLICT``.
  """
  files_str = ", ".join(conflicting_files or []) or "unknown files"
  return TrialMergeDecision(
      ok=False,
      status="TEXTUAL_CONFLICT",
      head_sha=head_sha,
      upstream_main_sha=upstream_sha,
      upstream_ref=upstream_ref,
      behind_count=behind_count,
      exit_code=1,
      upstream_delta=upstream_delta,
      failure=GateFailure(
          scar_id="GIT-20",
          code="UPSTREAM_MAIN_CONFLICT",
          trigger=(
              f"Branch has textual merge conflicts with {upstream_ref} "
              f"(behind by {behind_count} commit(s); files: {files_str})."
          ),
          rejected=(
              "Landing a pull request with unresolved textual merge conflicts "
              "against upstream main."
          ),
          reflex=(
              "Stop landing without altering Git state (SCAR-GIT-25), report "
              f"conflicting files ({files_str}) to the operator, and request "
              f"approval to run 'git fetch origin main && git merge {upstream_ref}' "
              "and re-verify in Target 30/40."
          ),
      ),
  )


def run_trial_merge_verification(
    repo_root: str,
    *,
    explicit_cmd: str = "",
    subproject: str = ".",
    timeout_seconds: Optional[float] = None,
    no_fetch: bool = False,
    state_dir: Optional[str] = None,
    sync: Optional[Dict[str, Any]] = None,
) -> TrialMergeDecision:
  """Run ephemeral detached worktree trial-merge verification against origin/main.

  Creates an isolated detached worktree inside ``tempfile.TemporaryDirectory()``,
  merges ``upstream_sha`` into ``head_sha``, runs the subproject regression
  command, removes the worktree via ``SignalTrap`` and ``try/finally``, and
  persists ``75_trial_merge_receipt.json`` on success. Never mutates the live
  workspace, branch ref, or ``approved_tree.txt``.

  Args:
    repo_root: Repository root directory.
    explicit_cmd: Optional explicit regression command override.
    subproject: Relative subproject path inside ``repo_root``.
    timeout_seconds: Optional positive timeout in seconds.
    no_fetch: When True, inspect local refs without fetching ``origin``.
    state_dir: Optional ``.workflow/state`` directory override.
    sync: Optional pre-computed dictionary from ``inspect_main_sync``.

  Returns:
    ``TrialMergeDecision`` recording pass/fail status, SHAs, delta summary,
    receipt metadata, and optional ``GateFailure``.
  """
  abs_repo = os.path.abspath(repo_root)
  receipt_path = _resolve_receipt_path(abs_repo, state_dir=state_dir)

  eff_sync = (
      sync
      if sync is not None
      else (
          inspect_main_sync(abs_repo)
          if no_fetch
          else fetch_and_inspect_main_sync(abs_repo)
      )
  )
  status = str(eff_sync.get("status") or "NO_REMOTE")
  upstream_ref = str(eff_sync.get("upstream_ref") or "origin/main")
  behind_count = int(eff_sync.get("behind_count") or 0)
  head_sha, upstream_sha = _resolve_shas(abs_repo, eff_sync, upstream_ref)

  if status in ("NO_REMOTE", "UP_TO_DATE") or behind_count == 0:
    return TrialMergeDecision(
        ok=True,
        status=status,
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        upstream_ref=eff_sync.get("upstream_ref"),
        behind_count=behind_count,
        notice=(
            f"TRIAL_MERGE_NOT_NEEDED: Branch is {status} against {upstream_ref}."
        ),
    )

  upstream_delta = format_upstream_delta_summary(abs_repo, upstream_ref)

  if status == "CONFLICT":
    _unlink_receipt(receipt_path)
    return _build_conflict_decision(
        upstream_ref=upstream_ref,
        behind_count=behind_count,
        head_sha=head_sha,
        upstream_sha=upstream_sha,
        conflicting_files=eff_sync.get("conflicting_files"),
        upstream_delta=upstream_delta,
    )

  try:
    _, rel_sub, resolved_cmd = resolve_regression_command(
        abs_repo, subproject=subproject, explicit_cmd=explicit_cmd
    )
  except ValueError as exc:
    _unlink_receipt(receipt_path)
    return TrialMergeDecision(
        ok=False,
        status="INVALID_SUBPROJECT",
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        upstream_ref=upstream_ref,
        behind_count=behind_count,
        upstream_delta=upstream_delta,
        exit_code=1,
        failure=GateFailure(
            scar_id="TEST-12",
            code="INVALID_SUBPROJECT",
            trigger=str(exc),
            rejected="Running trial-merge tests outside a valid subproject inside the repository.",
            reflex="Pass a valid relative subproject path via SUBPROJECT or --subproject.",
        ),
    )

  if not resolved_cmd:
    _unlink_receipt(receipt_path)
    return TrialMergeDecision(
        ok=False,
        status="MISSING_REGRESSION_COMMAND",
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        upstream_ref=upstream_ref,
        behind_count=behind_count,
        subproject=rel_sub,
        upstream_delta=upstream_delta,
        exit_code=1,
        failure=GateFailure(
            scar_id="GIT-18",
            code="MISSING_REGRESSION_COMMAND",
            trigger=(
                f"Cannot verify BEHIND_CLEAN combined tree ({upstream_ref} "
                f"ahead by {behind_count} commit(s)): no regression command "
                f"configured for subproject '{rel_sub}'."
            ),
            rejected=(
                "Landing a BEHIND_CLEAN pull request without executing "
                "combined-tree regression tests in an ephemeral worktree."
            ),
            reflex=(
                "Configure testing.regressionCommand in .workflow/config.json, "
                "add a Makefile test: target, or pass REGRESSION_CMD='<cmd>' "
                "to verify-trial-merge."
            ),
        ),
    )

  eff_timeout, to_err = _resolve_timeout(abs_repo, timeout_seconds)
  if eff_timeout is None or not head_sha or not upstream_sha:
    _unlink_receipt(receipt_path)
    detail = to_err or "Could not resolve HEAD or upstream main commit SHA."
    return TrialMergeDecision(
        ok=False,
        status="WORKTREE_ERROR",
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        upstream_ref=upstream_ref,
        behind_count=behind_count,
        regression_cmd=resolved_cmd,
        subproject=rel_sub,
        upstream_delta=upstream_delta,
        exit_code=1,
        failure=GateFailure(
            scar_id="GIT-18",
            code="TRIAL_MERGE_WORKTREE_ERROR",
            trigger=detail,
            rejected="Proceeding with Target 75 landing without valid trial-merge inputs.",
            reflex="Verify Git refs and testing.regressionTimeoutSeconds, then re-run verify-trial-merge.",
        ),
    )

  with tempfile.TemporaryDirectory(prefix="makeflow-trial-merge-") as tmp_root:
    worktree_dir = os.path.join(tmp_root, "wt")
    worktree_added = False
    cleaned_up = False

    def _cleanup_worktree() -> None:
      nonlocal cleaned_up
      if cleaned_up:
        return
      cleaned_up = True
      if worktree_added:
        _run_repo_git(
            abs_repo, "worktree", "remove", "--force", worktree_dir, timeout=30
        )
        _run_repo_git(abs_repo, "worktree", "prune", timeout=15)
      if os.path.isdir(worktree_dir):
        shutil.rmtree(worktree_dir, ignore_errors=True)

    # exit_on_signal=True makes SIGINT/SIGTERM raise SystemExit so the regression
    # process tree is killed and no verified receipt is written after an interrupt.
    with SignalTrap(exit_on_signal=True, cleanup_on_exit=True) as trap:
      trap.register_cleanup(_cleanup_worktree)
      try:
        add_proc = _run_repo_git(
            abs_repo,
            "worktree",
            "add",
            "--detach",
            worktree_dir,
            head_sha,
            timeout=30,
        )
        if add_proc.returncode != 0:
          _unlink_receipt(receipt_path)
          err = _single_line(
              add_proc.stderr or add_proc.stdout or "git worktree add failed"
          )
          return TrialMergeDecision(
              ok=False,
              status="WORKTREE_ERROR",
              head_sha=head_sha,
              upstream_main_sha=upstream_sha,
              upstream_ref=upstream_ref,
              behind_count=behind_count,
              regression_cmd=resolved_cmd,
              subproject=rel_sub,
              upstream_delta=upstream_delta,
              exit_code=add_proc.returncode,
              failure=GateFailure(
                  scar_id="GIT-18",
                  code="TRIAL_MERGE_WORKTREE_ERROR",
                  trigger=f"Failed to create ephemeral detached worktree: {err}",
                  rejected="Landing a BEHIND_CLEAN branch when trial-merge worktree setup failed.",
                  reflex="Inspect git worktree state and permissions, then re-run verify-trial-merge.",
              ),
          )
        worktree_added = True

        merge_proc = _run_repo_git(
            worktree_dir,
            "-c",
            "user.name=Makeflow Trial Merge",
            "-c",
            "user.email=trial-merge@makeflow.local",
            "merge",
            "--no-edit",
            upstream_sha,
            timeout=30,
        )
        if merge_proc.returncode != 0:
          _run_repo_git(worktree_dir, "merge", "--abort", timeout=15)
          _unlink_receipt(receipt_path)
          err = _single_line(
              merge_proc.stderr or merge_proc.stdout or "git merge failed"
          )
          return TrialMergeDecision(
              ok=False,
              status="WORKTREE_ERROR",
              head_sha=head_sha,
              upstream_main_sha=upstream_sha,
              upstream_ref=upstream_ref,
              behind_count=behind_count,
              regression_cmd=resolved_cmd,
              subproject=rel_sub,
              upstream_delta=upstream_delta,
              exit_code=merge_proc.returncode,
              failure=GateFailure(
                  scar_id="GIT-18",
                  code="TRIAL_MERGE_WORKTREE_ERROR",
                  trigger=f"Ephemeral trial-merge with {upstream_ref} failed: {err}",
                  rejected="Landing a branch when the combined HEAD + origin/main merge failed.",
                  reflex=(
                      "Stop landing without altering Git state (SCAR-GIT-25), "
                      "request operator approval for 'git fetch origin main && "
                      "git merge origin/main', resolve conflicts, and re-run Target 30/40."
                  ),
              ),
          )

        wt_sub_dir = (
            worktree_dir
            if rel_sub == "."
            else os.path.join(worktree_dir, rel_sub)
        )
        if not os.path.isdir(wt_sub_dir):
          _unlink_receipt(receipt_path)
          return TrialMergeDecision(
              ok=False,
              status="WORKTREE_ERROR",
              head_sha=head_sha,
              upstream_main_sha=upstream_sha,
              upstream_ref=upstream_ref,
              behind_count=behind_count,
              regression_cmd=resolved_cmd,
              subproject=rel_sub,
              upstream_delta=upstream_delta,
              exit_code=1,
              failure=GateFailure(
                  scar_id="GIT-18",
                  code="TRIAL_MERGE_WORKTREE_ERROR",
                  trigger=(
                      f"Subproject '{rel_sub}' does not exist in combined "
                      f"HEAD + {upstream_ref} trial-merge worktree."
                  ),
                  rejected="Landing when subproject layout diverged against upstream main.",
                  reflex="Inspect upstream delta and request operator approval to merge origin/main.",
              ),
          )

        proc, exec_err = _run_regression_shell(
            resolved_cmd, wt_sub_dir, eff_timeout
        )
      finally:
        _cleanup_worktree()

  if proc is None or proc.returncode != 0:
    _unlink_receipt(receipt_path)
    exit_code = proc.returncode if proc is not None else 124
    raw_tail = (
        exec_err
        if proc is None
        else ((proc.stderr or proc.stdout or "")[-280:])
    )
    tail = _single_line(raw_tail)
    detail = f" Output tail: {tail}" if tail else ""
    return TrialMergeDecision(
        ok=False,
        status="SEMANTIC_CONFLICT",
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        upstream_ref=upstream_ref,
        behind_count=behind_count,
        regression_cmd=resolved_cmd,
        subproject=rel_sub,
        upstream_delta=upstream_delta,
        exit_code=exit_code,
        failure=GateFailure(
            scar_id="GIT-18",
            code="TRIAL_MERGE_SEMANTIC_CONFLICT",
            trigger=(
                f"Ephemeral trial-merge regression command '{resolved_cmd}' "
                f"failed (exit {exit_code}) on combined HEAD ({head_sha[:8]}) "
                f"+ {upstream_ref} ({upstream_sha[:8]}).{detail}"
            ),
            rejected=(
                "Landing a BEHIND_CLEAN pull request whose combined tree with "
                "origin/main fails regression tests (semantic conflict)."
            ),
            reflex=(
                "Stop landing without altering Git state (SCAR-GIT-25), show "
                "the operator the upstream delta and failing test output, "
                "request explicit approval to run 'git fetch origin main && "
                "git merge origin/main', fix the semantic conflict in Target "
                "30, and re-run Target 40 adversarial review."
            ),
        ),
    )

  receipt: Dict[str, Any] = {
      "schema_version": TRIAL_MERGE_SCHEMA_VERSION,
      "verified": True,
      "exit_code": 0,
      "head_sha": head_sha,
      "upstream_main_sha": upstream_sha,
      "upstream_ref": upstream_ref,
      "behind_count": behind_count,
      "subproject": rel_sub,
      "regression_cmd": resolved_cmd,
      "verified_at": datetime.now(timezone.utc).isoformat(),
  }
  write_err = _write_receipt_atomic(receipt_path, receipt)
  if write_err is not None:
    return TrialMergeDecision(
        ok=False,
        status="WORKTREE_ERROR",
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        upstream_ref=upstream_ref,
        behind_count=behind_count,
        regression_cmd=resolved_cmd,
        subproject=rel_sub,
        upstream_delta=upstream_delta,
        exit_code=1,
        failure=GateFailure(
            scar_id="GIT-18",
            code="TRIAL_MERGE_WORKTREE_ERROR",
            trigger=f"Could not write trial-merge receipt to {receipt_path}: {write_err}",
            rejected="Proceeding without a persisted trial-merge verification receipt.",
            reflex="Check write permissions on .workflow/state/ and re-run verify-trial-merge.",
        ),
    )

  return TrialMergeDecision(
      ok=True,
      status="PASSED",
      head_sha=head_sha,
      upstream_main_sha=upstream_sha,
      upstream_ref=upstream_ref,
      behind_count=behind_count,
      regression_cmd=resolved_cmd,
      subproject=rel_sub,
      exit_code=0,
      upstream_delta=upstream_delta,
      receipt_path=receipt_path,
      cached=False,
      notice=(
          f"TRIAL_MERGE_VERIFIED: Ephemeral worktree merge of HEAD ({head_sha[:8]}) "
          f"+ {upstream_ref} ({upstream_sha[:8]}) passed '{resolved_cmd}'."
      ),
  )


def ensure_trial_merge_verified(
    repo_root: str,
    *,
    explicit_cmd: str = "",
    subproject: str = ".",
    timeout_seconds: Optional[float] = None,
    no_fetch: bool = False,
    state_dir: Optional[str] = None,
    sync: Optional[Dict[str, Any]] = None,
) -> TrialMergeDecision:
  """Verify a BEHIND_CLEAN branch using a fresh cached receipt or a new trial-merge.

  Args:
    repo_root: Repository root directory.
    explicit_cmd: Optional explicit regression command override.
    subproject: Relative subproject path inside ``repo_root``.
    timeout_seconds: Optional positive timeout in seconds.
    no_fetch: When True, inspect local refs without fetching ``origin``.
    state_dir: Optional ``.workflow/state`` directory override.
    sync: Optional pre-computed dictionary from ``inspect_main_sync``.

  Returns:
    ``TrialMergeDecision`` indicating cached pass, fresh pass, not-needed pass,
    or fail-closed error.
  """
  abs_repo = os.path.abspath(repo_root)
  eff_sync = (
      sync
      if sync is not None
      else (
          inspect_main_sync(abs_repo)
          if no_fetch
          else fetch_and_inspect_main_sync(abs_repo)
      )
  )
  status = str(eff_sync.get("status") or "NO_REMOTE")
  upstream_ref = str(eff_sync.get("upstream_ref") or "origin/main")
  behind_count = int(eff_sync.get("behind_count") or 0)
  head_sha, upstream_sha = _resolve_shas(abs_repo, eff_sync, upstream_ref)

  if status in ("NO_REMOTE", "UP_TO_DATE") or behind_count == 0:
    return TrialMergeDecision(
        ok=True,
        status=status,
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        upstream_ref=eff_sync.get("upstream_ref"),
        behind_count=behind_count,
        notice=(
            f"TRIAL_MERGE_NOT_NEEDED: Branch is {status} against {upstream_ref}."
        ),
    )

  if status == "BEHIND_CLEAN":
    try:
      _, rel_sub = validate_subproject(abs_repo, subproject)
    except ValueError:
      rel_sub = "."
    receipt_path = _resolve_receipt_path(abs_repo, state_dir=state_dir)
    existing = load_trial_merge_receipt(abs_repo, state_dir=state_dir)
    if _is_receipt_fresh(
        existing,
        head_sha=head_sha,
        upstream_main_sha=upstream_sha,
        subproject=rel_sub,
        explicit_cmd=explicit_cmd,
    ):
      upstream_delta = format_upstream_delta_summary(abs_repo, upstream_ref)
      rec_cmd = str((existing or {}).get("regression_cmd") or "")
      return TrialMergeDecision(
          ok=True,
          status="CACHED",
          head_sha=head_sha,
          upstream_main_sha=upstream_sha,
          upstream_ref=upstream_ref,
          behind_count=behind_count,
          regression_cmd=rec_cmd,
          subproject=rel_sub,
          exit_code=0,
          upstream_delta=upstream_delta,
          receipt_path=receipt_path,
          cached=True,
          notice=(
              f"TRIAL_MERGE_VERIFIED (cached): HEAD ({head_sha[:8]}) + "
              f"{upstream_ref} ({upstream_sha[:8]}) already passed '{rec_cmd}'."
          ),
      )

  return run_trial_merge_verification(
      abs_repo,
      explicit_cmd=explicit_cmd,
      subproject=subproject,
      timeout_seconds=timeout_seconds,
      no_fetch=no_fetch,
      state_dir=state_dir,
      sync=eff_sync,
  )


def verify_trial_merge_gate(script: Any) -> Optional[GateFailure]:
  """Run the ``verify-trial-merge`` gate for the CLI facade and print its notice.

  Args:
    script: ``WorkflowGatesScript`` supplying ``repo_root``, ``regression_cmd``,
      ``subproject``, ``regression_timeout``, ``as_json``, and ``stdout``.

  Returns:
    The structured ``GateFailure`` when verification blocks, else ``None``.
  """
  decision = ensure_trial_merge_verified(
      script.repo_root,
      explicit_cmd=script.regression_cmd,
      subproject=script.subproject or ".",
      timeout_seconds=script.regression_timeout,
  )
  if decision.failure is not None:
    return decision.failure
  text = json.dumps(decision.to_dict()) if script.as_json else decision.notice
  script.stdout.write(text + "\n")
  return None


def failure_from_blocked_message(message: str) -> Optional[GateFailure]:
  """Map a blocked Target 75 landing message to its ``GateFailure``.

  Covers the trial-merge codes and ``UPSTREAM_MAIN_CONFLICT``.

  Args:
    message: Blocked transition message from the state machine.

  Returns:
    A ``GateFailure`` when the message names a landing code, else ``None``.
  """
  for code in (
      "TRIAL_MERGE_SEMANTIC_CONFLICT",
      "TRIAL_MERGE_WORKTREE_ERROR",
      "MISSING_REGRESSION_COMMAND",
  ):
    if code in message:
      return GateFailure(
          scar_id="GIT-18",
          code=code,
          trigger=message,
          rejected=(
              "Landing a BEHIND_CLEAN pull request without a passing"
              " combined-tree trial-merge verification."
          ),
          reflex=(
              "Stop without altering Git state (SCAR-GIT-25), ask the operator"
              " to approve 'git merge origin/main', then rewind T30 -> T40."
          ),
      )
  if "UPSTREAM_MAIN_CONFLICT" in message:
    return GateFailure(
        scar_id="GIT-20",
        code="UPSTREAM_MAIN_CONFLICT",
        trigger=message,
        rejected="Advancing a branch with unresolved 3-way merge conflicts against upstream main.",
        reflex="Stop tool calls without altering Git state (SCAR-GIT-25) and ask the operator before rebasing or merging.",
    )
  return None
