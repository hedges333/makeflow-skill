"""Deterministic workflow gate facade emitting Vector 4 Reflexes (GH-57, GH-274, SCAR-ARCH-06).

Coordinates all 19 workflow gate subcommands via ``WorkflowGatesScript`` while
delegating domain logic to cohesive modules under ``lib/`` and preserving
canonical backward-compatible re-exports and monkeypatch targets (SCAR-API-02).
"""

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from lib.base_script import BaseScript
from lib.commit_gates import (
    _BARE_ISSUE_RE as _BARE_ISSUE_RE,
    _GH_CLOSE_RE as _GH_CLOSE_RE,
    check_approved_tree,
    check_commit_msg,
    check_post_commit,
)
from lib.git_index import ephemeral_git_index
from lib.intake_gates import (
    _LEFTOVER_CARD_RE as _LEFTOVER_CARD_RE,
    IntakeGateRequest,
    ReleaseIntakeRequest,
    cleanup_tdd_receipt,
    run_intake_gate,
    run_release_intake_gate,
)
from lib.main_sync import (
    _MERGE_TREE_HEADER_RE as _MERGE_TREE_HEADER_RE,
    _MERGE_TREE_STAGE_RE as _MERGE_TREE_STAGE_RE,
    _no_remote_sync_result as _no_remote_sync_result,
    _parse_merge_tree_conflicts as _parse_merge_tree_conflicts,
    _resolve_upstream_main_branch as _resolve_upstream_main_branch,
    _run_repo_git as _run_repo_git,
    evaluate_main_sync_gate,
    fetch_and_inspect_main_sync as _main_sync_fetch_and_inspect,
    inspect_main_sync as _main_sync_inspect,
)
from lib.parking_gates import (
    ParkSessionRequest,
    ParkingGateOutcome,
    ResumeParkedRequest,
    run_park_session_gate,
    run_resume_parked_gate,
)
from lib.plugin_gates import (
    DEFAULT_SKILL_MAX_BYTES as DEFAULT_SKILL_MAX_BYTES,
    REQUIRED_PLUGIN_FILES as REQUIRED_PLUGIN_FILES,
    check_plugin_files,
    check_skill_budget,
)
from lib.reflex import GateFailure, emit_reflex
from lib.repo_tree import (
    DEFAULT_REPO_TREE_THRESHOLD,
    RepoTreeCollector,
    RepoTreeError,
)
from lib.state_makefile import (
    _DEFAULT_REPO_ROOT as _DEFAULT_REPO_ROOT,
    _SCRIPTS_DIR as _SCRIPTS_DIR,
    _workflow_target_recipes as _workflow_target_recipes,
    ensure_git_exclude_workflow_dirs as ensure_git_exclude_workflow_dirs,
    generate_state_makefile as generate_state_makefile,
    refresh_state_makefile_if_ignored as refresh_state_makefile_if_ignored,
)
from lib.workflow_gates_cli import (
    _env_truthy as _env_truthy,
    hydrate_from_make_env,
    parse_workflow_gates_args,
)

__all__ = [
    "_BARE_ISSUE_RE",
    "_DEFAULT_REPO_ROOT",
    "_GH_CLOSE_RE",
    "_LEFTOVER_CARD_RE",
    "_MERGE_TREE_HEADER_RE",
    "_MERGE_TREE_STAGE_RE",
    "_SCRIPTS_DIR",
    "_env_truthy",
    "_no_remote_sync_result",
    "_parse_merge_tree_conflicts",
    "_resolve_upstream_main_branch",
    "_run_repo_git",
    "_workflow_target_recipes",
    "DEFAULT_SKILL_MAX_BYTES",
    "GateFailure",
    "REQUIRED_PLUGIN_FILES",
    "WorkflowGatesScript",
    "ensure_git_exclude_workflow_dirs",
    "ephemeral_git_index",
    "fetch_and_inspect_main_sync",
    "generate_state_makefile",
    "inspect_main_sync",
    "refresh_state_makefile_if_ignored",
]


def inspect_main_sync(repo_root: str) -> Dict[str, Any]:
  """Inspects local upstream origin/main divergence and 3-way merge conflicts without fetching.

  Forwards ``ephemeral_git_index`` from this module so callers patching
  ``lib.workflow_gates.ephemeral_git_index`` continue to intercept calls
  (SCAR-API-02).

  Args:
    repo_root: Workspace Git repository root directory.

  Returns:
    Upstream sync status dictionary from ``lib.main_sync.inspect_main_sync``.
  """
  return _main_sync_inspect(repo_root, ephemeral_index_fn=ephemeral_git_index)


def fetch_and_inspect_main_sync(repo_root: str) -> Dict[str, Any]:
  """Fetches origin's default branch and inspects upstream divergence and conflicts.

  Args:
    repo_root: Workspace Git repository root directory.

  Returns:
    Upstream sync status dictionary from ``inspect_main_sync``.
  """
  return _main_sync_fetch_and_inspect(repo_root, inspect_fn=inspect_main_sync)


class WorkflowGatesScript(BaseScript):
  """BaseScript orchestration facade for deterministic Makefile workflow gates."""

  def __init__(self) -> None:
    super().__init__()
    self.repo_root: str = _DEFAULT_REPO_ROOT
    self.lock_dir: Optional[str] = None
    self.as_json: bool = False
    self.gate: str = ""
    self.max_bytes: int = DEFAULT_SKILL_MAX_BYTES
    self.commit_msg_file: Optional[str] = None
    self.ticket: Optional[str] = None
    self.directory: Optional[str] = None
    self.title: Optional[str] = None
    self.issue_tag: Optional[str] = None
    self.allow_blocked: bool = False
    self.no_fetch: bool = False
    self.fail_on_behind: bool = False
    self.allow_conflict: bool = False
    self.adv_from: str = ""
    self.adv_to: str = ""
    self.adv_approved: Optional[str] = None
    self.adv_blockers: Optional[Any] = None
    self.allow_main_conflict: bool = False
    self.adv_transcript: str = ""
    self.adv_complete: bool = False
    self.make_env: bool = False
    self.regression_cmd: str = ""
    self.subproject: str = ""
    self.regression_timeout: Optional[float] = None
    self.tree_prefix: str = ""
    self.tree_approved_limit: Optional[int] = None
    self.tree_count_only: bool = False
    self.tree_no_limit: bool = False
    self.tree_threshold: int = DEFAULT_REPO_TREE_THRESHOLD
    self.review_round: str = ""
    self.park_request: Optional[ParkSessionRequest] = None
    self.resume_request: Optional[ResumeParkedRequest] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parses CLI arguments and hydrates Make environment variables."""
    super().parse_args(args)
    parse_workflow_gates_args(self, self.raw_args, _DEFAULT_REPO_ROOT)

  def _hydrate_from_make_env(self) -> None:
    """Populates unset gate fields from exported GNU Make environment variables."""
    hydrate_from_make_env(self)

  def _fail(
      self, scar_id: str, code: str, trigger: str, rejected: str, reflex: str
  ) -> int:
    return emit_reflex(
        stderr=self.stderr,
        scar_id=scar_id,
        violation_code=code,
        trigger=trigger,
        rejected=rejected,
        reflex=reflex,
        as_json=self.as_json,
    )

  def _fail_from(self, failure: GateFailure) -> int:
    return self._fail(
        scar_id=failure.scar_id,
        code=failure.code,
        trigger=failure.trigger,
        rejected=failure.rejected,
        reflex=failure.reflex,
    )

  def _git(self, *git_args: str) -> subprocess.CompletedProcess:
    try:
      return subprocess.run(
          ["git", *git_args],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
          timeout=15,
      )
    except (subprocess.SubprocessError, OSError) as exc:
      return subprocess.CompletedProcess(
          args=["git", *git_args],
          returncode=128,
          stdout="",
          stderr=str(exc),
      )

  def _init_makefile(self) -> int:
    if not os.path.isdir(self.repo_root):
      return self._fail(
          scar_id="GIT-25",
          code="INVALID_WORKSPACE_ROOT",
          trigger=f"Workspace directory does not exist: {self.repo_root}",
          rejected="Synthesizing .workflow/state/Makefile outside an existing workspace directory.",
          reflex="Pass an existing Git repository path via --repo-root before running init-makefile.",
      )

    ensure_git_exclude_workflow_dirs(self.repo_root)
    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    mk_path = os.path.join(state_dir, "Makefile")
    content = generate_state_makefile(self.repo_root)
    try:
      os.makedirs(state_dir, exist_ok=True)
      with open(mk_path, "w", encoding="utf-8") as f:
        f.write(content)
    except OSError as exc:
      return self._fail(
          scar_id="GIT-25",
          code="STATE_MAKEFILE_WRITE_FAILED",
          trigger=f"Could not write {mk_path}: {exc}",
          rejected="Proceeding without deterministic .workflow/state/Makefile gates.",
          reflex="Check filesystem permissions on .workflow/state/ and re-run init-makefile.",
      )
    return 0

  def _check_plugin_files(self) -> int:
    failure = check_plugin_files(self.repo_root)
    return self._fail_from(failure) if failure is not None else 0

  def _check_skill_budget(self) -> int:
    failure = check_skill_budget(self.repo_root, max_bytes=self.max_bytes)
    return self._fail_from(failure) if failure is not None else 0

  def _check_intake(self) -> int:
    request = IntakeGateRequest(
        repo_root=self.repo_root,
        ticket=self.ticket,
        lock_dir=self.lock_dir,
        allow_blocked=self.allow_blocked,
    )
    failure = run_intake_gate(request, git_runner=self._git)
    return self._fail_from(failure) if failure is not None else 0

  def _release_intake(self) -> int:
    request = ReleaseIntakeRequest(
        repo_root=self.repo_root,
        ticket=self.ticket,
        lock_dir=self.lock_dir,
        directory=self.directory,
        stderr=getattr(self, "stderr", None),
    )
    return run_release_intake_gate(request)

  def _cleanup_tdd_receipt(self, ticket_id: str) -> None:
    cleanup_tdd_receipt(
        self.repo_root, ticket_id, stderr=getattr(self, "stderr", None)
    )

  def _check_approved_tree(self) -> int:
    failure = check_approved_tree(self.repo_root, git_runner=self._git)
    return self._fail_from(failure) if failure is not None else 0

  def _check_commit_msg(self) -> int:
    failure = check_commit_msg(
        self.repo_root, commit_msg_file=self.commit_msg_file
    )
    return self._fail_from(failure) if failure is not None else 0

  def _check_post_commit(self) -> int:
    res = check_post_commit(self.repo_root, commit_msg_file=self.commit_msg_file)
    if not res.ok:
      if res.failure is not None:
        return self._fail_from(res.failure)
      return self._fail(
          scar_id="GIT-20",
          code="POST_COMMIT_GATE_FAILED",
          trigger="Post-commit validation failed without a structured GateFailure payload.",
          rejected="Returning exit 0 when post-commit validation reported ok=False.",
          reflex="Inspect HEAD commit message and tree against .workflow/state/approved_tree.txt and re-run check-post-commit.",
      )
    if self.as_json:
      payload = {"valid": True, "head_tree": res.head_tree, "expected_tree": res.expected_tree}
      self.stdout.write(json.dumps(payload) + "\n")
    return 0

  def _gate_terminal_title(self) -> int:
    try:
      from lib.terminal_title import TerminalTitleScript
      sub_args = ["set", "--repo-root", self.repo_root]
      if self.ticket:
        sub_args.extend(["--ticket", self.ticket])
      if self.directory is not None:
        sub_args.extend(["--directory", self.directory])
      if self.title:
        sub_args.extend(["--title", self.title])
      if self.issue_tag:
        sub_args.extend(["--issue-tag", self.issue_tag])
      if self.as_json:
        sub_args.append("--json")
      return TerminalTitleScript().execute(
          args=sub_args,
          stdout=self.stdout,
          stderr=self.stderr,
      )
    except (ImportError, OSError, ValueError):
      return 1

  def _check_main_sync(self) -> int:
    sync = (
        inspect_main_sync(self.repo_root)
        if self.no_fetch
        else fetch_and_inspect_main_sync(self.repo_root)
    )
    decision = evaluate_main_sync_gate(
        sync, fail_on_behind=self.fail_on_behind, allow_conflict=self.allow_conflict
    )
    if not decision.ok:
      if decision.failure is not None:
        return self._fail_from(decision.failure)
      return self._fail(
          scar_id="GIT-20",
          code="MAIN_SYNC_GATE_FAILED",
          trigger="Main sync gate failed without a structured GateFailure payload.",
          rejected="Returning exit 0 when main sync gate reported ok=False.",
          reflex="Inspect upstream origin/main sync state and re-run check-main-sync.",
      )
    if self.as_json:
      self.stdout.write(json.dumps(decision.sync) + "\n")
    elif decision.notice:
      self.stdout.write(decision.notice + "\n")
    return 0

  def _emit_advance_reflex(self, msg: str) -> int:
    """Emits a Vector 4 Reflex with exit code 2 for a blocked transition."""
    if "SCAR-PROC-102" in msg:
      return self._fail(
          scar_id="PROC-102",
          code="PAUSE_GATE_TURN_BOUNDARY_VIOLATION",
          trigger=msg,
          rejected="Advancing past a human pause gate without explicit human approval in a new turn.",
          reflex="Stop tool calls, present the proposal in chat, and wait for a human USER_INPUT turn.",
      )
    if "UPSTREAM_MAIN_CONFLICT" in msg:
      return self._fail(
          scar_id="GIT-20",
          code="UPSTREAM_MAIN_CONFLICT",
          trigger=msg,
          rejected="Advancing a branch with unresolved 3-way merge conflicts against upstream main.",
          reflex="Stop tool calls without altering Git state (SCAR-GIT-25) and ask the operator before rebasing or merging.",
      )
    if "UNAPPROVED_TREE_DRIFT" in msg:
      return self._fail(
          scar_id="GIT-25",
          code="UNAPPROVED_TREE_DRIFT",
          trigger=msg,
          rejected="Advancing past Target 50 when committed HEAD^{tree} does not match approved_tree.txt.",
          reflex="Verify committed files against approved_tree.txt or return to Target 40 for a fresh review.",
      )
    if "COMMIT_MESSAGE_DRIFT" in msg:
      return self._fail(
          scar_id="GIT-20",
          code="COMMIT_MESSAGE_DRIFT",
          trigger=msg,
          rejected="Advancing past Target 50 when HEAD commit message differs from .workflow/state/50_commit_msg.txt.",
          reflex="Amend HEAD with 'git commit --amend -F .workflow/state/50_commit_msg.txt' and retry.",
      )
    if "INVALID_COMMIT_MESSAGE_CONTRACT" in msg:
      return self._fail(
          scar_id="GIT-20",
          code="INVALID_COMMIT_MESSAGE_CONTRACT",
          trigger=msg,
          rejected="Advancing past Target 50 when HEAD commit message violates Conventional Commits 1.0.0 or trailer rules.",
          reflex="Fix .workflow/state/50_commit_msg.txt per 05_pre_commit_hygiene.md, amend HEAD with -F, and retry.",
      )
    if "Required state card" in msg:
      return self._fail(
          scar_id="ARCH-06",
          code="MISSING_REQUIRED_STATE_CARD",
          trigger=msg,
          rejected="Advancing to the next Makefile target before writing its prerequisite state card.",
          reflex="Write the required prerequisite state card in .workflow/state/ and re-run the target.",
      )
    return self._fail(
        scar_id="ARCH-06",
        code="INVALID_TARGET_TRANSITION",
        trigger=msg,
        rejected="Executing an out-of-order or unapproved transition in .workflow/state/Makefile.",
        reflex="Inspect .workflow/state/99_next_action.md and run a valid target transition with required approvals.",
    )

  def _build_advance_evidence(
      self, current: str, next_t: str, complete: bool
  ) -> Dict[str, Any]:
    """Constructs the transition evidence dictionary from CLI flags."""
    from lib.state_machine import GATE_EVIDENCE_KEYS

    ev: Dict[str, Any] = {}
    if complete:
      ev["complete"] = True
    gate_key = GATE_EVIDENCE_KEYS.get((current, next_t))
    approved_str = (self.adv_approved or "").strip().lower()
    if gate_key and approved_str in ("1", "true", "yes"):
      ev[gate_key] = True
    if self.adv_blockers is not None:
      if isinstance(self.adv_blockers, str):
        raw_b = self.adv_blockers.strip()
        ev["blockersCount"] = (
            int(raw_b) if re.fullmatch(r"[+-]?\d+", raw_b) else raw_b
        )
      else:
        ev["blockersCount"] = self.adv_blockers
    if self.allow_main_conflict:
      ev["allowMainConflict"] = True
    if self.no_fetch:
      ev["skipMainFetch"] = True
    if self.adv_transcript:
      ev["transcriptPath"] = self.adv_transcript
    return ev

  def _verify_regression(self) -> int:
    """Runs the active subproject full regression suite and seals the receipt."""
    from lib.regression_gate import run_verify_regression

    ok, payload = run_verify_regression(
        self.repo_root,
        explicit_cmd=self.regression_cmd,
        subproject=self.subproject or ".",
        timeout_seconds=self.regression_timeout,
    )
    if not ok:
      return self._fail(
          scar_id=payload["scar_id"],
          code=payload["code"],
          trigger=payload["trigger"],
          rejected=payload["rejected"],
          reflex=payload["reflex"],
      )
    if self.as_json:
      self.stdout.write(json.dumps(payload) + "\n")
    return 0

  def _check_regression(self, *, emit_stdout: bool = True) -> int:
    """Verifies 30_regression_receipt.json, running verify-regression on explicit command."""
    from lib.regression_gate import (
        run_verify_regression,
        validate_subproject,
        verify_regression_receipt,
    )

    expected_sub: Optional[str] = None
    if self.subproject:
      try:
        _, expected_sub = validate_subproject(self.repo_root, self.subproject)
      except ValueError as exc:
        return self._fail(
            scar_id="TEST-12",
            code="INVALID_SUBPROJECT",
            trigger=str(exc),
            rejected="Checking regression state against an invalid or escaping subproject path.",
            reflex="Pass a valid subproject path inside the repository via SUBPROJECT or --subproject.",
        )

    if self.regression_cmd:
      target_sub = expected_sub if expected_sub is not None else "."
      existing_ok, existing_payload = verify_regression_receipt(
          self.repo_root,
          require_approved_tree_match=True,
          expected_subproject=target_sub,
      )
      if (
          existing_ok
          and existing_payload.get("regression_cmd") == self.regression_cmd
          and existing_payload.get("subproject") == target_sub
      ):
        if emit_stdout and self.as_json:
          self.stdout.write(json.dumps(existing_payload) + "\n")
        return 0
      ok_run, run_payload = run_verify_regression(
          self.repo_root,
          explicit_cmd=self.regression_cmd,
          subproject=target_sub,
          timeout_seconds=self.regression_timeout,
      )
      if not ok_run:
        return self._fail(
            scar_id=run_payload["scar_id"],
            code=run_payload["code"],
            trigger=run_payload["trigger"],
            rejected=run_payload["rejected"],
            reflex=run_payload["reflex"],
        )

    ok, payload = verify_regression_receipt(
        self.repo_root,
        require_approved_tree_match=True,
        expected_subproject=expected_sub,
    )
    if not ok:
      return self._fail(
          scar_id=payload["scar_id"],
          code=payload["code"],
          trigger=payload["trigger"],
          rejected=payload["rejected"],
          reflex=payload["reflex"],
      )
    if emit_stdout and self.as_json:
      self.stdout.write(json.dumps(payload) + "\n")
    return 0

  def _advance(self) -> int:
    """Executes the advance subcommand and renders 99_next_action.md."""
    from lib.state_machine import (
        TARGET_NAME_TO_ID,
        advance_workflow_target,
        infer_current_target,
    )

    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)

    raw_to = self.adv_to
    complete = self.adv_complete or (raw_to == "finish")
    next_t = TARGET_NAME_TO_ID.get(raw_to, raw_to)
    raw_from = self.adv_from
    current = (
        TARGET_NAME_TO_ID.get(raw_from, raw_from)
        if raw_from
        else infer_current_target(state_dir, next_target=next_t)
    )

    if next_t in ("T40", "T45", "T50"):
      rc_tree = self._check_approved_tree()
      if rc_tree != 0:
        return rc_tree
    if next_t == "T40":
      rc_reg = self._check_regression(emit_stdout=False)
      if rc_reg != 0:
        return rc_reg
    if next_t == "T50":
      rc_msg = self._check_commit_msg()
      if rc_msg != 0:
        return rc_msg

    ev = self._build_advance_evidence(current, next_t, complete)
    ok, msg = advance_workflow_target(
        current,
        next_t,
        evidence=ev,
        workspace_dir=self.repo_root,
        state_dir=state_dir,
    )
    if not ok:
      return self._emit_advance_reflex(msg)
    self.stdout.write(msg + "\n")
    return 0

  def _read_init_d(self) -> int:
    """Executes the Target 00 read-init-d inspection and reconciliation gate."""
    from lib.state_machine import TARGET_NAME_TO_ID, restore_session_state

    if self.adv_from:
      src = TARGET_NAME_TO_ID.get(self.adv_from, self.adv_from)
      if src != "T00":
        return self._emit_advance_reflex(
            f"BLOCKED: Transition {src} -> T00 is not valid in the Makefile DAG."
        )
    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    ok, msg = restore_session_state(state_dir, workspace_dir=self.repo_root)
    if not ok:
      return self._emit_advance_reflex(msg)
    self.stdout.write(msg + "\n")
    return 0

  def _repo_tree(self) -> int:
    """Collects and formats the Git file tree index with SCAR-TOOL-16 volume triage."""
    try:
      index = RepoTreeCollector(self.repo_root).collect(prefix=self.tree_prefix)
    except RepoTreeError as exc:
      return self._fail(
          scar_id="GIT-25",
          code="REPO_TREE_COLLECTION_FAILED",
          trigger=str(exc),
          rejected=(
              "Collecting Git file tree index outside a valid Git repository "
              "or with an invalid path prefix."
          ),
          reflex=(
              "Run repo-tree inside a valid Git repository with a "
              "repository-relative PATH_PREFIX."
          ),
      )

    if (
        index.exceeds_threshold(self.tree_threshold)
        and not self.tree_no_limit
        and self.tree_approved_limit is None
        and not self.tree_count_only
    ):
      if self.as_json:
        self.stdout.write(
            json.dumps(index.to_dict(threshold=self.tree_threshold)) + "\n"
        )
      else:
        self.stdout.write(
            index.format_triage_summary(threshold=self.tree_threshold) + "\n"
        )
      return self._fail(
          scar_id="TOOL-16",
          code="REPO_TREE_VOLUME_TRIAGE_REQUIRED",
          trigger=(
              f"Repository file tree has {index.filtered_count} of "
              f"{index.total_count} files, exceeding threshold {self.tree_threshold}."
          ),
          rejected=(
              "Returning an unbounded file tree or silently truncating paths "
              "without user triage."
          ),
          reflex=(
              "Report the file counts and top-level directory breakdown to the "
              "user and interview them across the 4 SCAR-TOOL-16 paths: "
              "(1) revise filter criteria (--prefix <dir> / PATH_PREFIX=<dir>), "
              "(2) impose user-approved limit (--approved-limit <N> / TREE_LIMIT=<N>), "
              "(3) count files only (--count-only / COUNT_ONLY=1), or "
              "(4) proceed without limits (--no-limit / NO_LIMIT=1)."
          ),
      )

    if self.as_json:
      payload = index.to_dict(
          approved_limit=self.tree_approved_limit,
          threshold=self.tree_threshold,
          count_only=self.tree_count_only,
          no_limit=self.tree_no_limit,
      )
      self.stdout.write(json.dumps(payload) + "\n")
      return 0

    if self.tree_count_only:
      self.stdout.write(index.format_count_summary() + "\n")
      return 0

    self.stdout.write(
        index.format_index(approved_limit=self.tree_approved_limit) + "\n"
    )
    return 0

  def _promote_review(self) -> int:
    """Validates and promotes a critic review round via lib/review_promotion.py."""
    from lib.review_promotion import promote_review_round

    try:
      outcome = promote_review_round(self.repo_root, self.review_round)
    except ValueError as exc:
      return self._fail(
          scar_id="PROC-89",
          code="INVALID_REVIEW_ROUND",
          trigger=str(exc),
          rejected="Promoting a critic review round without a valid positive integer ROUND.",
          reflex="Run make -f .workflow/state/Makefile promote-review ROUND=<N> with a positive integer N (>= 1).",
      )
    except FileNotFoundError as exc:
      return self._fail(
          scar_id="PROC-89",
          code="MISSING_REVIEW_ROUND_FILE",
          trigger=str(exc),
          rejected="Promoting a critic review round whose source file does not exist.",
          reflex=(
              "Write .workflow/state/40_critic_review_round<N>.md once from the "
              "critic output, then re-run promote-review ROUND=<N>."
          ),
      )
    except OSError as exc:
      return self._fail(
          scar_id="SYS-05",
          code="REVIEW_PROMOTION_IO_ERROR",
          trigger=str(exc),
          rejected="Failed to read or copy the critic review artifact.",
          reflex="Check file permissions in .workflow/state/ and re-run promote-review ROUND=<N>.",
      )

    if not outcome.promoted:
      return self._fail(
          scar_id="PROC-89",
          code="INVALID_CRITIC_REVIEW",
          trigger="; ".join(outcome.validation.errors),
          rejected="Promoting a critic review missing canonical Six-Pillar headings, prose bodies, or a valid binary VERDICT.",
          reflex=(
              "Ensure .workflow/state/40_critic_review_round<N>.md contains all "
              "six canonical headings in order with prose bodies and one exact "
              "VERDICT header, then re-run promote-review ROUND=<N>."
          ),
      )

    if self.as_json:
      self.stdout.write(json.dumps(outcome.to_dict()) + "\n")
    else:
      self.stdout.write(
          f"Promoted {outcome.source_path} -> {outcome.destination_path} "
          f"({outcome.validation.verdict}, {outcome.validation.blocker_count} blockers)\n"
      )
    return 0

  def _emit_parking_outcome(self, outcome: ParkingGateOutcome) -> int:
    """Emits JSON or text output for a parking gate outcome, failing closed on error."""
    if not outcome.ok:
      if outcome.advance_error:
        return self._emit_advance_reflex(outcome.advance_error)
      if outcome.failure is not None:
        return self._fail_from(outcome.failure)
      return self._fail(
          scar_id="PROC-102",
          code="PARKING_GATE_FAILED",
          trigger="Parking gate failed without a structured GateFailure payload.",
          rejected="Returning exit 0 when parking gate reported ok=False.",
          reflex="Inspect .workflow/state/70_parking_state.json and retry the parking gate.",
      )
    if self.as_json:
      self.stdout.write(json.dumps(outcome.json_payload) + "\n")
    elif outcome.text_output:
      self.stdout.write(outcome.text_output)
    return 0

  def _park_session(self) -> int:
    """Delegates Target 70 park-session execution to lib/parking_gates.py."""
    req = self.park_request or ParkSessionRequest(repo_root=self.repo_root)
    return self._emit_parking_outcome(run_park_session_gate(req))

  def _resume_parked(self) -> int:
    """Delegates Target 70 resume-parked execution to lib/parking_gates.py."""
    req = self.resume_request or ResumeParkedRequest(repo_root=self.repo_root)
    return self._emit_parking_outcome(run_resume_parked_gate(req))

  def run(self) -> int:
    """Dispatches the parsed workflow gate subcommand and returns exit code."""
    handlers = {
        "init-makefile": self._init_makefile,
        "plugin-files": self._check_plugin_files,
        "skill-budget": self._check_skill_budget,
        "intake": self._check_intake,
        "approved-tree": self._check_approved_tree,
        "commit-msg": self._check_commit_msg,
        "post-commit": self._check_post_commit,
        "main-sync": self._check_main_sync,
        "verify-regression": self._verify_regression,
        "check-regression": self._check_regression,
        "repo-tree": self._repo_tree,
        "promote-review": self._promote_review,
        "park-session": self._park_session,
        "resume-parked": self._resume_parked,
        "terminal-title": self._gate_terminal_title,
        "release-intake": self._release_intake,
        "release-lock": self._release_intake,
        "read-init-d": self._read_init_d,
        "advance": self._advance,
    }
    handler = handlers.get(self.gate)
    if handler is None:
      raise ValueError(f"Unsupported gate: {self.gate}")
    return handler()
