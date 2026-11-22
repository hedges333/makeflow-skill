"""Deterministic workflow gate checks emitting Vector 4 Reflexes (GH-57, SCAR-ARCH-06).

Replaces verbose negative prose in SKILL.md with fast, hermetic runtime checks
that exit 0 when invariants hold or emit a structured Vector 4 Reflex on stderr
and exit 2 when violated.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from lib.base_script import BaseScript, ReflexArgumentError, SilentArgumentParser
from lib.commit_msg import receipt_declares_pure_git, validate_commit_message
from lib.git_index import ephemeral_git_index
from lib.reflex import emit_reflex
from lib.repo_tree import (
    DEFAULT_REPO_TREE_THRESHOLD,
    RepoTreeCollector,
    RepoTreeError,
)
from lib.ticket_state import canonicalize_ticket_id

_DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_DEFAULT_REPO_ROOT, "scripts")
DEFAULT_SKILL_MAX_BYTES = 24576

REQUIRED_PLUGIN_FILES = (
    "plugin.json",
    "hooks.json",
    "mcp_config.json",
    "default_config.json",
    "skills/makeflow/SKILL.md",
    "rules/AGENTS.md",
)

_LEFTOVER_CARD_RE = re.compile(r"^(?:[1-8][0-9]_.*|approved_tree\.txt$)")
_LOCAL_BRANCH_RE = re.compile(
    r"^(?:feat|fix|ticket)/local-(?:([1-9][0-9]*)(?:-|$)|.+$)"
)
_GH_CLOSE_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#\d+\b"
)
_BARE_ISSUE_RE = re.compile(r"(?:^|[\s(\[])#\d+\b")
_MERGE_TREE_STAGE_RE = re.compile(
    r"^  (base|our|their)\s+(\d+)\s+([0-9a-f]+)\s+(.+)$"
)
_MERGE_TREE_HEADER_RE = re.compile(
    r"^(?:changed in both|added in both|removed in local|removed in remote|"
    r"merged|added in local|added in remote|removed in both)$"
)


def _no_remote_sync_result() -> Dict[str, Any]:
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


def inspect_main_sync(repo_root: str) -> Dict[str, Any]:
  """Inspects local upstream origin/main divergence and 3-way merge-tree conflicts without fetching.

  Uses an ephemeral GIT_INDEX_FILE seeded via native Git plumbing when unstaged
  or staged tracked changes exist so .git/index, .git/sharedindex.*, HEAD, and
  .workflow/state/approved_tree.txt are never mutated.
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

  local_tree = "HEAD"
  if has_tracked_changes:
    try:
      with ephemeral_git_index(repo_root, timeout=10) as env:
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


def fetch_and_inspect_main_sync(repo_root: str) -> Dict[str, Any]:
  """Fetches origin's default branch and inspects upstream divergence and 3-way merge conflicts."""
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

  return inspect_main_sync(repo_root)


def ensure_git_exclude_workflow_dirs(repo_root: str) -> bool:
  """Ensures .workflow/state/ and .workflow/archive/ are in Git info/exclude.

  Args:
    repo_root: Repository or linked worktree root directory.

  Returns:
    True when inside a Git repository and exclude entries are present.
  """
  if not repo_root or not os.path.isdir(repo_root):
    return False
  proc = _run_repo_git(repo_root, "rev-parse", "--git-path", "info/exclude")
  if proc.returncode != 0:
    return False
  raw_excl = (proc.stdout or "").strip()
  if not raw_excl:
    return False
  excl_path = (
      raw_excl
      if os.path.isabs(raw_excl)
      else os.path.join(repo_root, raw_excl)
  )
  try:
    os.makedirs(os.path.dirname(excl_path), exist_ok=True)
    existing_text = ""
    if os.path.isfile(excl_path):
      with open(excl_path, "r", encoding="utf-8", errors="replace") as f:
        existing_text = f.read()
    existing_lines = {
        ln.strip() for ln in existing_text.splitlines() if ln.strip()
    }
    missing = [
        entry
        for entry in (".workflow/state/", ".workflow/archive/")
        if entry not in existing_lines
    ]
    if missing:
      prefix = (
          ""
          if (not existing_text or existing_text.endswith("\n"))
          else "\n"
      )
      with open(excl_path, "a", encoding="utf-8") as f:
        f.write(prefix + "\n".join(missing) + "\n")
    return True
  except OSError:
    return False


def refresh_state_makefile_if_ignored(repo_root: str) -> bool:
  """Writes or updates .workflow/state/Makefile only when .workflow/state/ is ignored.

  Args:
    repo_root: Candidate workspace root directory.

  Returns:
    True when .workflow/ exists, .workflow/state/ is Git-ignored, and Makefile
    is up to date; False otherwise without touching disk.
  """
  if not repo_root or not os.path.isdir(os.path.join(repo_root, ".workflow")):
    return False
  ign_proc = _run_repo_git(repo_root, "check-ignore", "-q", ".workflow/state/")
  if ign_proc.returncode != 0:
    return False

  state_dir = os.path.join(repo_root, ".workflow", "state")
  mk_path = os.path.join(state_dir, "Makefile")
  expected = generate_state_makefile(repo_root)
  try:
    if os.path.isfile(mk_path):
      with open(mk_path, "r", encoding="utf-8") as f:
        if f.read() == expected:
          return True
    os.makedirs(state_dir, exist_ok=True)
    with open(mk_path, "w", encoding="utf-8") as f:
      f.write(expected)
    return True
  except OSError:
    return False


def _env_truthy(name: str) -> bool:
  """Returns True when environment variable name has a non-falsey value."""
  return os.environ.get(name, "").strip().lower() not in (
      "",
      "0",
      "false",
      "no",
  )


def _workflow_target_recipes(gates_script: str, quoted_root: str) -> str:
  """Builds the read-init-d gate, 14 DAG target recipes, and generic advance."""
  base = f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} advance"
  specs = (
      ("ticket-assigned", "", "--to T10"),
      ("archaeology-verified", "", "--to T15"),
      ("approved-plan", "", "--to T20"),
      ("implementation-diff", "", "--to T30"),
      ("adversarial-review", " check-approved-tree check-regression", "--to T40"),
      ("triaged-blockers", "", "--to T42"),
      ("approved-review", " check-approved-tree", "--to T45"),
      ("commit", " check-approved-tree check-commit-msg", "--to T50"),
      ("push", " check-main-sync", "--to T60"),
      ("bot-quiescence", "", "--to T70"),
      ("ready-to-land", "", "--to T75"),
      ("land", "", "--to T80"),
      ("codified-scars-consolidated", "", "--to T90"),
      ("finish", "", "--to T90 --complete"),
  )
  blocks = [
      f"read-init-d:\n\t@$(PYTHON) {gates_script} --repo-root {quoted_root} read-init-d --make-env\n"
  ]
  blocks.extend(
      f"{name}:{prereqs}\n{base} {flag} --make-env\n"
      for name, prereqs, flag in specs
  )
  blocks.append(f"advance:\n{base} --make-env\n")
  return "\n".join(blocks)


def generate_state_makefile(repo_root: str) -> str:
  """Returns the canonical content of .workflow/state/Makefile for repo_root.

  Args:
    repo_root: Workspace repository root directory.

  Returns:
    Synthesized GNU Makefile contents for workflow gates and ``ast-lint``.
  """
  abs_root = os.path.abspath(repo_root)
  gates_script = shlex.quote(
      os.path.abspath(os.path.join(_SCRIPTS_DIR, "workflow_gates.py"))
  ).replace("$", "$$")
  guard_script = shlex.quote(
      os.path.abspath(os.path.join(_SCRIPTS_DIR, "tdd_guard.py"))
  ).replace("$", "$$")
  scrub_script = shlex.quote(
      os.path.abspath(os.path.join(_SCRIPTS_DIR, "ast_scrub.py"))
  ).replace("$", "$$")
  quoted_root = shlex.quote(abs_root).replace("$", "$$")
  phony = (
      "read-init-d ticket-assigned archaeology-verified approved-plan "
      "implementation-diff adversarial-review triaged-blockers approved-review "
      "commit push bot-quiescence ready-to-land land "
      "codified-scars-consolidated finish advance "
      "check-intake release-intake check-approved-tree check-commit-msg "
      "check-main-sync verify-regression check-regression repo-tree "
      "witness-red verify-green verify-citation ast-lint"
  )
  recipes = _workflow_target_recipes(gates_script, quoted_root)
  return (
      "# Synthesized by workflow_gates.py init-makefile (SCAR-ARCH-06, SCAR-TEST-12)\n"
      "PYTHON ?= python3\n"
      "FROM ?=\n"
      "TO ?=\n"
      "APPROVED ?=\n"
      "BLOCKERS ?=\n"
      "ALLOW_MAIN_CONFLICT ?=\n"
      "NO_FETCH ?=\n"
      "TRANSCRIPT ?=\n"
      "TICKET ?=\n"
      "ALLOW_BLOCKED ?=\n"
      "FILE ?=\n"
      "TEST_DIR =\n"
      "TEST_CMD =\n"
      "ALLOW_HEAD_MOVE =\n"
      "CITE =\n"
      "REGRESSION_CMD ?=\n"
      "SUBPROJECT ?=\n"
      "PATH_PREFIX ?=\n"
      "TREE_LIMIT ?=\n"
      "NO_LIMIT ?=\n"
      "COUNT_ONLY ?=\n"
      "SRC_DIR ?=\n"
      "export TICKET ALLOW_BLOCKED FROM TO APPROVED BLOCKERS ALLOW_MAIN_CONFLICT NO_FETCH TRANSCRIPT FILE\n"
      "export TEST_DIR TEST_CMD ALLOW_HEAD_MOVE CITE\n"
      "export REGRESSION_CMD SUBPROJECT\n"
      "export PATH_PREFIX TREE_LIMIT NO_LIMIT COUNT_ONLY\n\n"
      f".PHONY: {phony}\n\n"
      f"{recipes}\n"
      "check-intake:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} intake --make-env\n\n"
      "release-intake:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} release-intake --make-env\n\n"
      "check-approved-tree:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} approved-tree\n\n"
      "check-commit-msg:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} commit-msg --make-env\n\n"
      "check-main-sync:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} main-sync --make-env\n\n"
      "verify-regression:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} verify-regression --make-env\n\n"
      "check-regression:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} check-regression --make-env\n\n"
      "repo-tree:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} repo-tree --make-env\n\n"
      "witness-red:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} witness-red --make-env\n\n"
      "verify-green:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-green --make-env\n\n"
      "verify-citation:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-citation --make-env\n\n"
      "ast-lint:\n"
      f'\t@$(PYTHON) {scrub_script} --repo-root {quoted_root}$(if $(SRC_DIR), --src-dir "$(SRC_DIR)",) --strict\n'
  )


class WorkflowGatesScript(BaseScript):
  """BaseScript runner for deterministic Makefile workflow gates."""

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

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="workflow_gates.py",
        description="Deterministic Makefile workflow gate checks emitting Vector 4 Reflexes (SCAR-ARCH-06).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/workflow_gates.py init-makefile\n"
            "  python3 scripts/workflow_gates.py intake --ticket GH-170\n"
            "  python3 scripts/workflow_gates.py approved-tree\n"
            "  python3 scripts/workflow_gates.py commit-msg --file .workflow/state/50_commit_msg.txt"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        default=_DEFAULT_REPO_ROOT,
        help="Repository root directory to check.",
    )
    parser.add_argument(
        "--lock-dir",
        default=None,
        help="Optional directory for multi-workspace mutual exclusion locks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit Vector 4 Reflex errors as JSON.",
    )
    parser.add_argument(
        "--make-env",
        action="store_true",
        dest="make_env",
        help="Hydrate gate arguments from exported GNU Make environment variables.",
    )

    subparsers = parser.add_subparsers(
        dest="gate",
        parser_class=SilentArgumentParser,
        help="Workflow gate subcommand to execute.",
    )

    p_init = subparsers.add_parser(
        "init-makefile",
        help="Synthesize .workflow/state/Makefile for the target workspace.",
        description="Synthesize deterministic .workflow/state/Makefile gate targets for the workspace.",
        epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . init-makefile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_init.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )

    p_files = subparsers.add_parser(
        "plugin-files",
        help="Verify all required Makeflow plugin manifests and core skill files exist.",
        description="Verify that required plugin manifests, hooks, MCP config, and SKILL.md exist.",
        epilog="Example:\n  python3 scripts/workflow_gates.py plugin-files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_files.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )

    p_budget = subparsers.add_parser(
        "skill-budget",
        help="Verify skills/makeflow/SKILL.md stays within the context byte budget.",
        description="Check that skills/makeflow/SKILL.md does not exceed the byte budget ceiling.",
        epilog="Example:\n  python3 scripts/workflow_gates.py skill-budget --max-bytes 24576",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_budget.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_SKILL_MAX_BYTES,
        help="Maximum allowed size in bytes for SKILL.md (default 24576).",
    )
    p_budget.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )

    p_intake = subparsers.add_parser(
        "intake",
        help="Run Target 10 intake hygiene, git exclude, and ticket collision checks.",
        description="Verify clean git status, .git/info/exclude rules, and multi-workspace ticket lock at Target 10.",
        epilog="Example:\n  python3 scripts/workflow_gates.py intake --ticket GH-170",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_intake.add_argument(
        "--ticket",
        default=None,
        help="Ticket identifier to acquire and check (for example GH-170 or LOCAL-1).",
    )
    p_intake.add_argument(
        "--lock-dir",
        default=None,
        dest="sub_lock_dir",
        help="Directory for workspace mutual exclusion locks.",
    )
    p_intake.add_argument(
        "--allow-blocked",
        action="store_true",
        dest="allow_blocked",
        help="Allow intake even when upstream blocking issues are open.",
    )
    p_intake.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )
    p_intake.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate TICKET and ALLOW_BLOCKED from environment.",
    )

    p_rel = subparsers.add_parser(
        "release-intake",
        help="Release active ticket lock and reset terminal title upon completion.",
        description="Release the workspace mutex lock for the active ticket and reset the terminal title.",
        epilog="Example:\n  python3 scripts/workflow_gates.py release-intake --ticket GH-170",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_rel.add_argument(
        "--ticket",
        default=None,
        help="Ticket identifier whose lock should be released.",
    )
    p_rel.add_argument(
        "--lock-dir",
        default=None,
        dest="sub_lock_dir",
        help="Directory for workspace mutual exclusion locks.",
    )
    p_rel.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )
    p_rel.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate TICKET from environment.",
    )

    p_rel_lock = subparsers.add_parser(
        "release-lock",
        help="Alias for release-intake to release active ticket and workspace locks.",
        description="Alias for release-intake to release active ticket and workspace locks.",
        epilog="Example:\n  python3 scripts/workflow_gates.py release-lock --ticket GH-170",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_rel_lock.add_argument(
        "--ticket",
        default=None,
        help="Ticket identifier whose lock should be released.",
    )
    p_rel_lock.add_argument(
        "--lock-dir",
        default=None,
        dest="sub_lock_dir",
        help="Directory for workspace mutual exclusion locks.",
    )
    p_rel_lock.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )
    p_rel_lock.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate TICKET from environment.",
    )

    p_tree = subparsers.add_parser(
        "approved-tree",
        help="Verify staged git write-tree matches .workflow/state/approved_tree.txt.",
        description="Verify that the current git index matches the locked Merkle tree hash from Target 40.",
        epilog="Example:\n  python3 scripts/workflow_gates.py approved-tree",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_tree.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )

    p_msg = subparsers.add_parser(
        "commit-msg",
        help="Validate .workflow/state/50_commit_msg.txt against Conventional Commits 1.0.0.",
        description="Validate the prepared commit message file for Target 50 against Conventional Commits and trailer rules.",
        epilog="Example:\n  python3 scripts/workflow_gates.py commit-msg --file .workflow/state/50_commit_msg.txt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_msg.add_argument(
        "--file",
        dest="commit_msg_file",
        default=None,
        help="Path to commit message file (defaults to .workflow/state/50_commit_msg.txt).",
    )
    p_msg.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors as JSON.",
    )
    p_msg.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate FILE from environment.",
    )

    p_title = subparsers.add_parser(
        "terminal-title",
        help="Update the interactive terminal title for the active ticket and directory.",
        description="Detect terminal capability and set the terminal window title for the active ticket.",
        epilog="Example:\n  python3 scripts/workflow_gates.py terminal-title --ticket GH-170",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_title.add_argument(
        "--ticket",
        default=None,
        help="Ticket ID to display in the terminal title.",
    )
    p_title.add_argument(
        "--directory",
        default=None,
        help="Directory name to display in the terminal title.",
    )
    p_title.add_argument(
        "--title",
        default=None,
        help="Explicit full terminal title string override.",
    )
    p_title.add_argument(
        "--issue-tag",
        default=None,
        help="Short issue description tag for the terminal title.",
    )
    p_title.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit output as JSON.",
    )

    p_sync = subparsers.add_parser(
        "main-sync",
        help="Check upstream origin/main advancement and 3-way merge-tree conflicts.",
        description="Fetch origin/main (unless --no-fetch is set) and check for upstream commits and 3-way merge conflicts.",
        epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . main-sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sync.add_argument(
        "--no-fetch",
        action="store_true",
        dest="no_fetch",
        help="Inspect local remote-tracking ref without running git fetch.",
    )
    p_sync.add_argument(
        "--fail-on-behind",
        action="store_true",
        dest="fail_on_behind",
        help="Exit 2 with UPSTREAM_MAIN_BEHIND when origin/main is ahead even without conflicts.",
    )
    p_sync.add_argument(
        "--allow-conflict",
        action="store_true",
        dest="allow_conflict",
        help="Exit 0 with warning when operator explicitly overrides upstream main conflicts.",
    )
    p_sync.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit output or Vector 4 Reflex errors as JSON.",
    )
    p_sync.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate NO_FETCH and ALLOW_MAIN_CONFLICT from environment.",
    )

    self._register_advance_subparser(subparsers)
    self._register_read_init_subparser(subparsers)
    self._register_regression_subparsers(subparsers)
    self._register_repo_tree_subparser(subparsers)

    ns = parser.parse_args(self.raw_args)
    if not ns.gate:
      parser.error(
          "Specify a gate subcommand: init-makefile, plugin-files, skill-budget, "
          "intake, approved-tree, commit-msg, main-sync, verify-regression, "
          "check-regression, repo-tree, terminal-title, release-intake, "
          "release-lock, read-init-d, or advance."
      )

    self.repo_root = os.path.abspath(ns.repo_root)
    self.lock_dir = getattr(ns, "sub_lock_dir", None) or getattr(ns, "lock_dir", None)
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))
    self.make_env = bool(
        getattr(ns, "make_env", False) or getattr(ns, "sub_make_env", False)
    )
    self.gate = ns.gate
    if ns.gate == "skill-budget":
      if ns.max_bytes <= 0:
        parser.error("--max-bytes must be a positive integer.")
      self.max_bytes = ns.max_bytes
    elif ns.gate == "commit-msg":
      self.commit_msg_file = ns.commit_msg_file
    elif ns.gate == "main-sync":
      self.no_fetch = bool(getattr(ns, "no_fetch", False))
      self.fail_on_behind = bool(getattr(ns, "fail_on_behind", False))
      self.allow_conflict = bool(getattr(ns, "allow_conflict", False))
    elif ns.gate in ("verify-regression", "check-regression"):
      self.regression_cmd = str(getattr(ns, "regression_cmd", "") or "").strip()
      raw_sub = getattr(ns, "subproject", None)
      self.subproject = str(raw_sub).strip() if raw_sub is not None else ""
      self.regression_timeout = getattr(ns, "timeout", None)
    elif ns.gate == "repo-tree":
      if ns.tree_threshold <= 0:
        parser.error("--threshold must be a positive integer.")
      if ns.tree_approved_limit is not None and ns.tree_approved_limit <= 0:
        parser.error("--approved-limit must be a positive integer.")
      self.tree_prefix = str(getattr(ns, "tree_prefix", "") or "").strip()
      self.tree_approved_limit = ns.tree_approved_limit
      self.tree_count_only = bool(getattr(ns, "tree_count_only", False))
      self.tree_no_limit = bool(getattr(ns, "tree_no_limit", False))
      self.tree_threshold = ns.tree_threshold
    elif ns.gate == "read-init-d":
      self.adv_from = str(getattr(ns, "adv_from", "") or "").strip()
    elif ns.gate == "advance":
      self.adv_from = str(getattr(ns, "adv_from", "") or "").strip()
      self.adv_to = str(getattr(ns, "adv_to", "") or "").strip()
      self.adv_approved = getattr(ns, "adv_approved", None)
      self.adv_blockers = getattr(ns, "adv_blockers", None)
      self.allow_main_conflict = bool(getattr(ns, "allow_main_conflict", False))
      self.no_fetch = bool(getattr(ns, "no_fetch", False))
      self.adv_transcript = str(getattr(ns, "adv_transcript", "") or "").strip()
      self.adv_complete = bool(getattr(ns, "adv_complete", False))
      self.subproject = ""
    elif ns.gate in ("terminal-title", "intake", "release-intake", "release-lock"):
      self.ticket = getattr(ns, "ticket", None)
      self.directory = getattr(ns, "directory", None)
      self.title = getattr(ns, "title", None)
      self.issue_tag = getattr(ns, "issue_tag", None)
      self.allow_blocked = bool(getattr(ns, "allow_blocked", False))

    if self.make_env:
      self._hydrate_from_make_env()

    if self.gate == "repo-tree":
      if self.tree_approved_limit is not None and self.tree_no_limit:
        parser.error("--approved-limit and --no-limit cannot be used together.")
      if self.tree_count_only and (
          self.tree_approved_limit is not None or self.tree_no_limit
      ):
        parser.error(
            "--count-only cannot be combined with --approved-limit or --no-limit."
        )

  def _hydrate_from_make_env(self) -> None:
    """Populates unset gate fields from exported GNU Make environment variables."""
    if not self.ticket and os.environ.get("TICKET", "").strip():
      self.ticket = os.environ["TICKET"].strip()
    if not self.allow_blocked and _env_truthy("ALLOW_BLOCKED"):
      self.allow_blocked = True
    if not self.commit_msg_file and os.environ.get("FILE", "").strip():
      self.commit_msg_file = os.environ["FILE"].strip()
    if not self.no_fetch and _env_truthy("NO_FETCH"):
      self.no_fetch = True
    if _env_truthy("ALLOW_MAIN_CONFLICT"):
      self.allow_main_conflict = True
      self.allow_conflict = True
    if not self.adv_from and os.environ.get("FROM", "").strip():
      self.adv_from = os.environ["FROM"].strip()
    if not self.adv_to and os.environ.get("TO", "").strip():
      self.adv_to = os.environ["TO"].strip()
    if self.adv_approved is None and os.environ.get("APPROVED", "").strip():
      self.adv_approved = os.environ["APPROVED"].strip()
    if self.adv_blockers is None and os.environ.get("BLOCKERS", "").strip():
      self.adv_blockers = os.environ["BLOCKERS"].strip()
    if not self.adv_transcript and os.environ.get("TRANSCRIPT", "").strip():
      self.adv_transcript = os.environ["TRANSCRIPT"].strip()
    if not self.regression_cmd and os.environ.get("REGRESSION_CMD", "").strip():
      self.regression_cmd = os.environ["REGRESSION_CMD"].strip()
    if not self.subproject and os.environ.get("SUBPROJECT", "").strip():
      self.subproject = os.environ["SUBPROJECT"].strip()
    if not self.tree_prefix and os.environ.get("PATH_PREFIX", "").strip():
      self.tree_prefix = os.environ["PATH_PREFIX"].strip()
    if (
        self.tree_approved_limit is None
        and os.environ.get("TREE_LIMIT", "").strip()
    ):
      raw_limit = os.environ["TREE_LIMIT"].strip()
      if not raw_limit.isascii() or not raw_limit.isdigit() or int(raw_limit) <= 0:
        raise ReflexArgumentError("TREE_LIMIT must be a positive integer.")
      self.tree_approved_limit = int(raw_limit)
    if not self.tree_no_limit and _env_truthy("NO_LIMIT"):
      self.tree_no_limit = True
    if not self.tree_count_only and _env_truthy("COUNT_ONLY"):
      self.tree_count_only = True

  @staticmethod
  def _register_repo_tree_subparser(subparsers: Any) -> None:
    """Registers the repo-tree subparser for Target 15 Git file tree indexing."""
    p_tree_idx = subparsers.add_parser(
        "repo-tree",
        help="List repository Git file tree index with SCAR-TOOL-16 volume triage.",
        description="Collect tracked and non-ignored untracked Git paths for Target 15 archaeology dispatch.",
        epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . repo-tree",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_tree_idx.add_argument(
        "--prefix",
        dest="tree_prefix",
        default="",
        help="Optional repository-relative path prefix filter.",
    )
    p_tree_idx.add_argument(
        "--approved-limit",
        dest="tree_approved_limit",
        type=int,
        default=None,
        help="Explicit user-approved maximum number of file paths to display.",
    )
    p_tree_idx.add_argument(
        "--count-only",
        action="store_true",
        dest="tree_count_only",
        help="Print file counts and top-level directory breakdown without listing file paths.",
    )
    p_tree_idx.add_argument(
        "--no-limit",
        action="store_true",
        dest="tree_no_limit",
        help="Return the full file tree index when the user explicitly approves unbounded output.",
    )
    p_tree_idx.add_argument(
        "--threshold",
        dest="tree_threshold",
        type=int,
        default=DEFAULT_REPO_TREE_THRESHOLD,
        help=f"Volume triage file threshold (default {DEFAULT_REPO_TREE_THRESHOLD}).",
    )
    p_tree_idx.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit output or Vector 4 Reflex errors as JSON.",
    )
    p_tree_idx.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate PATH_PREFIX, TREE_LIMIT, NO_LIMIT, and COUNT_ONLY from environment.",
    )

  @staticmethod
  def _register_regression_subparsers(subparsers: Any) -> None:
    """Registers verify-regression and check-regression subparsers."""
    specs = (
        (
            "verify-regression",
            "Run the active subproject full regression suite and seal 30_regression_receipt.json.",
            "Example:\n  python3 scripts/workflow_gates.py --repo-root . verify-regression",
        ),
        (
            "check-regression",
            "Verify .workflow/state/30_regression_receipt.json against live tree and approved_tree.txt.",
            "Example:\n  python3 scripts/workflow_gates.py --repo-root . check-regression",
        ),
    )
    for name, desc, epilog in specs:
      p_reg = subparsers.add_parser(
          name,
          help=desc,
          description=desc,
          epilog=epilog,
          formatter_class=argparse.RawDescriptionHelpFormatter,
      )
      p_reg.add_argument(
          "--regression-cmd",
          dest="regression_cmd",
          default="",
          help="Explicit full subproject regression test command.",
      )
      p_reg.add_argument(
          "--subproject",
          dest="subproject",
          default=None,
          help="Relative path to active subproject directory (default '.').",
      )
      p_reg.add_argument(
          "--timeout",
          dest="timeout",
          type=float,
          default=None,
          help="Regression command timeout in seconds.",
      )
      p_reg.add_argument(
          "--json",
          action="store_true",
          dest="sub_json",
          help="Emit output or Vector 4 Reflex errors as JSON.",
      )
      p_reg.add_argument(
          "--make-env",
          action="store_true",
          dest="sub_make_env",
          help="Hydrate REGRESSION_CMD and SUBPROJECT from environment.",
      )

  @staticmethod
  def _register_read_init_subparser(subparsers: Any) -> None:
    """Registers the Target 00 read-init-d inspection/reconciliation subparser."""
    p_init_d = subparsers.add_parser(
        "read-init-d",
        help="Inspect and reconcile Target 00 session state on disk.",
        description="Reconcile active session state without mutating clean or completed workspaces.",
        epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . read-init-d",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_init_d.add_argument("--from", dest="adv_from", default="", help="Optional source target ID.")
    p_init_d.add_argument("--json", action="store_true", dest="sub_json", help="Emit errors as JSON.")
    p_init_d.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate FROM from environment.",
    )

  @staticmethod
  def _register_advance_subparser(subparsers: Any) -> None:
    """Registers the advance subcommand parser on subparsers."""
    p_adv = subparsers.add_parser(
        "advance",
        help="Validate and execute a Makefile DAG target transition.",
        description="Execute a deterministic Makefile DAG target transition and update 99_next_action.md.",
        epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . advance --to T15",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_adv.add_argument("--from", dest="adv_from", default="", help="Source target ID.")
    p_adv.add_argument("--to", dest="adv_to", default="", help="Destination target ID.")
    p_adv.add_argument(
        "--approved",
        dest="adv_approved",
        nargs="?",
        const="1",
        default=None,
        help="Human pause-gate approval flag (1/true or 0/false).",
    )
    p_adv.add_argument(
        "--blockers", dest="adv_blockers", default=None, help="Critic blocker count."
    )
    p_adv.add_argument(
        "--allow-main-conflict",
        action="store_true",
        dest="allow_main_conflict",
        help="Allow advancing despite upstream main conflicts.",
    )
    p_adv.add_argument(
        "--no-fetch",
        action="store_true",
        dest="no_fetch",
        help="Inspect local remote-tracking ref without running git fetch.",
    )
    p_adv.add_argument(
        "--transcript", dest="adv_transcript", default="", help="Transcript JSONL path."
    )
    p_adv.add_argument(
        "--complete",
        action="store_true",
        dest="adv_complete",
        help="Complete Target 90 (finish) and clean intermediate state cards.",
    )
    p_adv.add_argument(
        "--json", action="store_true", dest="sub_json", help="Emit errors as JSON."
    )
    p_adv.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate FROM, TO, APPROVED, BLOCKERS, and TRANSCRIPT from environment.",
    )

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
    missing = [
        rel
        for rel in REQUIRED_PLUGIN_FILES
        if not os.path.isfile(os.path.join(self.repo_root, rel))
    ]
    if not missing:
      return 0
    joined = ", ".join(missing)
    return self._fail(
        scar_id="DOC-16",
        code="MISSING_REQUIRED_PLUGIN_FILE",
        trigger=f"Missing required plugin file(s) in {self.repo_root}: {joined}",
        rejected="Packaging or testing an incomplete plugin manifest, skill, or rule ring.",
        reflex=f"Restore {joined} in {self.repo_root} and re-run make check-plugin-files.",
    )

  def _check_skill_budget(self) -> int:
    skill_rel = "skills/makeflow/SKILL.md"
    skill_path = os.path.join(self.repo_root, skill_rel)
    if not os.path.isfile(skill_path):
      return self._fail(
          scar_id="DOC-16",
          code="MISSING_REQUIRED_PLUGIN_FILE",
          trigger=f"Missing {skill_rel} in {self.repo_root}.",
          rejected="Evaluating skill context budget without skills/makeflow/SKILL.md.",
          reflex=f"Restore {skill_rel} and re-run make check-skill-budget.",
      )
    actual_bytes = os.path.getsize(skill_path)
    if actual_bytes <= self.max_bytes:
      return 0
    return self._fail(
        scar_id="PROC-99",
        code="SKILL_CONTEXT_CEILING_EXCEEDED",
        trigger=f"{skill_rel} is {actual_bytes} bytes, exceeding ceiling of {self.max_bytes} bytes.",
        rejected="Packing verbose procedures or negative guardrails into static SKILL.md prose.",
        reflex="Move detailed rules into skills/makeflow/references/ or deterministic gates, then re-run make check-skill-budget.",
    )

  def _check_intake(self) -> int:
    """Validates intake preconditions, acquires canonical mutex lock, and persists 10_ticket.json.

    Returns:
      Exit code 0 when intake succeeds, or 2 with a Vector 4 Reflex on stderr.
    """
    status_proc = self._git("status", "--porcelain")
    if status_proc.returncode != 0:
      return self._fail(
          scar_id="GIT-25",
          code="UNCLEAN_INTAKE_STATE",
          trigger=f"git status failed in {self.repo_root}: {(status_proc.stderr or '').strip()}",
          rejected="Starting Target 10 intake outside a valid Git working tree.",
          reflex="Open a valid Git repository checkout before running make -f .workflow/state/Makefile check-intake.",
      )
    dirty_lines = [
        ln for ln in status_proc.stdout.splitlines() if ln.strip()
    ]
    if dirty_lines:
      return self._fail(
          scar_id="GIT-25",
          code="UNCLEAN_INTAKE_STATE",
          trigger=f"Working tree has uncommitted or untracked files ({len(dirty_lines)} item(s)).",
          rejected="Creating a new ticket branch on a dirty working tree.",
          reflex="Inspect git status --short and commit or stash changes without destructive git commands before Target 10.",
      )

    unexcluded = []
    for rel_dir in (".workflow/state/", ".workflow/archive/"):
      ign_proc = self._git("check-ignore", "-q", rel_dir)
      if ign_proc.returncode != 0:
        unexcluded.append(rel_dir)
    if unexcluded:
      missing_excl = ", ".join(unexcluded)
      return self._fail(
          scar_id="GIT-25",
          code="UNCLEAN_INTAKE_STATE",
          trigger=f"Local workflow path(s) not excluded in Git: {missing_excl}",
          rejected="Allowing Memento state cards or archives to leak into Git commits.",
          reflex="Append .workflow/state/ and .workflow/archive/ to .git/info/exclude and re-run make -f .workflow/state/Makefile check-intake.",
      )

    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    if os.path.isdir(state_dir):
      from lib.ticket_state import TICKET_JSON_FILENAME, TicketStateStore

      leftovers = sorted(
          fname
          for fname in os.listdir(state_dir)
          if _LEFTOVER_CARD_RE.match(fname)
      )
      if leftovers == [TICKET_JSON_FILENAME] and self.ticket:
        existing_meta = TicketStateStore(state_dir).load()
        requested_ticket_id = canonicalize_ticket_id(self.ticket)
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
        return self._fail(
            scar_id="GIT-25",
            code="UNCLEAN_INTAKE_STATE",
            trigger=f"Leftover intermediate state file(s) in .workflow/state/: {', '.join(leftovers)}",
            rejected="Starting a new ticket while prior 10_..80_ state cards remain active.",
            reflex="Archive or unlink intermediate state files per state.completedCardAction before saving 10_ticket.json.",
        )

    ticket_id = self.ticket
    if not ticket_id:
      try:
        from lib.terminal_title import detect_ticket_id
        ticket_id = detect_ticket_id(repo_root=self.repo_root)
      except (ImportError, OSError, UnicodeDecodeError):
        ticket_id = ""
    ticket_id = canonicalize_ticket_id(ticket_id)

    if ticket_id:
      from lib.ticket_state import TicketMetadata, TicketStateStore
      from lib.workspace_mutex import WorkspaceMutex
      mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
      branch_proc = self._git("branch", "--show-current")
      branch_name = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""
      res = mutex.acquire(
          ticket_id=ticket_id,
          workspace_path=self.repo_root,
          branch_name=branch_name,
      )
      if not res.get("acquired"):
        sess = res.get("session") or {}
        colliding_workspace = sess.get("workspace_path", "unknown")
        colliding_pid = sess.get("pid", "unknown")
        return self._fail(
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
          origin_proc = self._git("remote", "get-url", "origin")
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
            trigger_msg = f"Remote conflict detected for ticket {ticket_id}: {reason}."

          return self._fail(
              scar_id="SYS-08",
              code="REMOTE_TICKET_COLLISION",
              trigger=trigger_msg,
              rejected=f"Concurrent intake on ticket {ticket_id} with remote assignment or branch.",
              reflex="Coordinate with the ticket assignee or remove remote conflict before intake.",
          )

        dep = mutex.check_dependencies(ticket_id=ticket_id, hosting_mode=hosting_mode)
        if dep.get("blocked") and not self.allow_blocked:
          mutex.release(ticket_id)
          blockers = dep.get("open_blockers", [])
          if blockers:
            details = ", ".join(
                f"#{b.get('number')} ({b.get('title')})" for b in blockers
            )
          else:
            details = f"{dep.get('blocking_count', 0)} open issue(s)"
          return self._fail(
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

      ticket_store = TicketStateStore.from_repo_root(self.repo_root)
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
            repo_root=self.repo_root, lock_dir=self.lock_dir
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
      set_terminal_title(repo_root=self.repo_root)
    except (ImportError, OSError):
      pass
    return 0

  def _release_intake(self) -> int:
    """Releases canonical workspace mutex locks, cleans up receipts, and restores terminal title.

    Returns:
      Exit code 0 after releasing intake resources.
    """
    ticket_id = self.ticket
    if not ticket_id:
      try:
        from lib.terminal_title import detect_ticket_id
        ticket_id = detect_ticket_id(repo_root=self.repo_root)
      except (ImportError, OSError, UnicodeDecodeError):
        ticket_id = ""
    ticket_id = canonicalize_ticket_id(ticket_id)

    try:
      from lib.workspace_mutex import WorkspaceMutex
      mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
      if ticket_id:
        mutex.release(ticket_id)
      released_tickets = mutex.release_workspace(self.repo_root)
      if not ticket_id and released_tickets:
        ticket_id = canonicalize_ticket_id(released_tickets[0])
    except (ImportError, OSError):
      pass

    self._cleanup_tdd_receipt(ticket_id or "")

    try:
      from lib.terminal_title import (
          detect_directory_name,
          format_terminal_title,
          is_temporary_directory_name,
          set_terminal_title,
      )
      dir_name = (
          self.directory
          if self.directory is not None
          else detect_directory_name(repo_root=self.repo_root)
      )
      if dir_name and not is_temporary_directory_name(dir_name):
        restored_title = format_terminal_title("", dir_name)
        if restored_title:
          stream_to_use = getattr(self, "stderr", None)
          set_terminal_title(
              title=restored_title, repo_root=self.repo_root, stream=stream_to_use
          )
    except (ImportError, OSError):
      pass
    return 0

  def _cleanup_tdd_receipt(self, ticket_id: str) -> None:
    """Archives or unlinks 30_tdd_receipt.json, 30_regression_receipt.json, and loaded_scars.json on intake release."""
    from lib.config import is_ticket_active

    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    transients = (
        ("gate_entry.json", "active_subagent.json")
        if is_ticket_active(state_dir)
        else ("gate_entry.json", "active_target.json", "active_subagent.json")
    )
    for transient in transients:
      transient_path = os.path.join(state_dir, transient)
      if os.path.isfile(transient_path):
        try:
          os.unlink(transient_path)
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
      cfg = resolve_workflow_config(workspace_dir=self.repo_root)
      completed_action = (
          cfg.get("state", {}).get("completedCardAction", "unlink")
          if isinstance(cfg, dict)
          else "unlink"
      )
    except (ImportError, OSError):
      completed_action = "unlink"

    archive_ticket = (
        re.sub(r"[^A-Za-z0-9._-]", "_", ticket_id or "").strip("._")
        or "unknown"
    )

    for artifact_name in existing:
      artifact_path = os.path.join(state_dir, artifact_name)
      try:
        if completed_action == "archive":
          from lib.safe_copy import safe_copy_file
          archive_dst = os.path.join(
              self.repo_root,
              ".workflow",
              "archive",
              archive_ticket,
              artifact_name,
          )
          safe_copy_file(artifact_path, archive_dst)
        os.unlink(artifact_path)
      except (ImportError, OSError) as exc:
        stream = getattr(self, "stderr", None)
        if stream is not None:
          stream.write(
              f"Warning: failed to clean up {artifact_path}: {exc}\n"
          )

  def _check_approved_tree(self) -> int:
    tree_path = os.path.join(
        self.repo_root, ".workflow", "state", "approved_tree.txt"
    )
    if not os.path.isfile(tree_path):
      return self._fail(
          scar_id="GIT-25",
          code="UNAPPROVED_TREE_DRIFT",
          trigger="Missing .workflow/state/approved_tree.txt.",
          rejected="Advancing past Target 40 without a locked Git Merkle tree hash.",
          reflex="Run git add -A && git write-tree > .workflow/state/approved_tree.txt and complete Target 40 review.",
      )
    with open(tree_path, "r", encoding="utf-8", errors="replace") as f:
      expected_tree = f.read().strip()
    if not expected_tree:
      return self._fail(
          scar_id="GIT-25",
          code="UNAPPROVED_TREE_DRIFT",
          trigger=".workflow/state/approved_tree.txt is empty.",
          rejected="Verifying review sign-off against an empty Merkle tree hash.",
          reflex="Lock the staged tree with git add -A && git write-tree > .workflow/state/approved_tree.txt and re-run Target 40.",
      )

    status_proc = self._git("status", "--porcelain")
    if status_proc.returncode != 0:
      return self._fail(
          scar_id="GIT-25",
          code="UNAPPROVED_TREE_DRIFT",
          trigger=f"git status failed in {self.repo_root}: {(status_proc.stderr or '').strip()}",
          rejected="Verifying approved tree outside a Git repository.",
          reflex="Run make -f .workflow/state/Makefile check-approved-tree from a valid Git repository.",
      )

    drift_items = []
    for line in status_proc.stdout.splitlines():
      if len(line) < 3:
        continue
      idx_flag, wt_flag = line[0], line[1]
      if line.startswith("??") or wt_flag != " " or idx_flag == "U":
        drift_items.append(line.strip())
    if drift_items:
      return self._fail(
          scar_id="GIT-25",
          code="UNAPPROVED_TREE_DRIFT",
          trigger=f"Unstaged or untracked working-tree drift detected: {', '.join(drift_items)}",
          rejected="Committing or signing off when unstaged edits or untracked files bypass the audited index.",
          reflex="Stage intended edits with git add -A, update approved_tree.txt, and re-run Target 40 adversarial review.",
      )

    wt_proc = self._git("write-tree")
    actual_tree = (wt_proc.stdout or "").strip()
    if wt_proc.returncode != 0 or actual_tree != expected_tree:
      return self._fail(
          scar_id="GIT-25",
          code="UNAPPROVED_TREE_DRIFT",
          trigger=f"Staged git write-tree ({actual_tree or 'error'}) does not match approved_tree.txt ({expected_tree}).",
          rejected="Committing code modifications that drifted after Target 40 review.",
          reflex="Re-lock git add -A && git write-tree > .workflow/state/approved_tree.txt and run a fresh Target 40 review.",
      )

    return 0

  def _check_commit_msg(self) -> int:
    if self.commit_msg_file:
      msg_path = (
          self.commit_msg_file
          if os.path.isabs(self.commit_msg_file)
          else os.path.join(self.repo_root, self.commit_msg_file)
      )
    else:
      msg_path = os.path.join(
          self.repo_root, ".workflow", "state", "50_commit_msg.txt"
      )

    if not os.path.isfile(msg_path):
      return self._fail(
          scar_id="GIT-20",
          code="INVALID_COMMIT_MESSAGE_CONTRACT",
          trigger=f"Commit message file not found: {msg_path}",
          rejected="Running Target 50 commit without a prepared commit message file.",
          reflex="Write the proposed commit message to .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
      )

    with open(msg_path, "r", encoding="utf-8", errors="replace") as f:
      msg_text = f.read()
    if not msg_text.strip():
      return self._fail(
          scar_id="GIT-20",
          code="INVALID_COMMIT_MESSAGE_CONTRACT",
          trigger=f"Commit message file is empty: {msg_path}",
          rejected="Committing with an empty commit message.",
          reflex="Write a non-empty subject and body in .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
      )

    is_local = False
    local_num: Optional[str] = None

    branch_proc = self._git("rev-parse", "--abbrev-ref", "HEAD")
    if branch_proc.returncode == 0:
      branch_name = branch_proc.stdout.strip()
      m_branch = _LOCAL_BRANCH_RE.match(branch_name)
      if m_branch:
        is_local = True
        if m_branch.group(1):
          local_num = m_branch.group(1)

    remote_proc = self._git("remote", "get-url", "origin")
    if (
        remote_proc.returncode != 0
        or "github.com" not in (remote_proc.stdout or "").lower()
    ):
      is_local = True

    receipt_card = os.path.join(
        self.repo_root, ".workflow", "state", "15_archaeology_receipt.md"
    )
    if receipt_declares_pure_git(receipt_card):
      is_local = True

    ticket_id = f"LOCAL-{local_num}" if local_num else None
    validation = validate_commit_message(
        msg_text, is_local=is_local, ticket_id=ticket_id
    )

    if not validation.valid:
      if is_local:
        if _GH_CLOSE_RE.search(msg_text) or _BARE_ISSUE_RE.search(msg_text):
          return self._fail(
              scar_id="GIT-20",
              code="INVALID_COMMIT_MESSAGE_CONTRACT",
              trigger="Local session commit message contains 'closes #<N>' or bare '#<N>' GitHub issue syntax.",
              rejected="Closing or linking unrelated remote GitHub issues from a LOCAL-<N> task.",
              reflex="Remove all 'closes #<N>' and '#<N>' references from 50_commit_msg.txt and use 'Ticket: LOCAL-<N>'.",
          )

        expected_id = f"LOCAL-{local_num}" if local_num else r"LOCAL-[1-9][0-9]*"
        trailer_re = re.compile(rf"(?m)^Ticket:\s*{expected_id}\s*$")
        if not trailer_re.search(msg_text):
          shown_id = f"LOCAL-{local_num}" if local_num else "LOCAL-<N>"
          return self._fail(
              scar_id="GIT-20",
              code="INVALID_COMMIT_MESSAGE_CONTRACT",
              trigger=f"Local session commit message is missing required 'Ticket: {shown_id}' line.",
              rejected="Committing a local task without explicit LOCAL-<N> disambiguation.",
              reflex=f"Add 'Ticket: {shown_id}' on its own line in .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
          )

        return self._fail(
            scar_id="GIT-20",
            code="INVALID_COMMIT_MESSAGE_CONTRACT",
            trigger=validation.errors[0],
            rejected="Committing a local task with invalid Conventional Commits or changed file format.",
            reflex="Correct commit message formatting in .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
        )
      else:
        return self._fail(
            scar_id="GIT-20",
            code="INVALID_COMMIT_MESSAGE_CONTRACT",
            trigger=validation.errors[0],
            rejected="Committing a message that violates Conventional Commits 1.0.0 or trailer conventions.",
            reflex="Format .workflow/state/50_commit_msg.txt using Conventional Commits and ensure issue reference trailer exists per 05_pre_commit_hygiene.md.",
        )

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
    status = sync.get("status", "NO_REMOTE")
    upstream_ref = sync.get("upstream_ref") or "origin/main"
    behind_count = sync.get("behind_count", 0)
    conflicting_files = sync.get("conflicting_files") or []

    if status == "CONFLICT":
      files_str = ", ".join(conflicting_files) or "unknown files"
      if self.allow_conflict:
        if self.as_json:
          self.stdout.write(json.dumps(sync) + "\n")
        else:
          self.stdout.write(
              f"MAIN_SYNC_WARNING: Proceeding with operator conflict override "
              f"against {upstream_ref} ({files_str}).\n"
          )
        return 0
      return self._fail(
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
      )

    if status == "BEHIND_CLEAN":
      if self.fail_on_behind:
        return self._fail(
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
        )
      if self.as_json:
        self.stdout.write(json.dumps(sync) + "\n")
      else:
        self.stdout.write(
            f"MAIN_SYNC_NOTICE: Branch is behind {upstream_ref} by {behind_count} "
            "commit(s) with 0 conflicts (BEHIND_CLEAN).\n"
        )
      return 0

    if self.as_json:
      self.stdout.write(json.dumps(sync) + "\n")
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

  def run(self) -> int:
    if self.gate == "init-makefile":
      return self._init_makefile()
    if self.gate == "plugin-files":
      return self._check_plugin_files()
    if self.gate == "skill-budget":
      return self._check_skill_budget()
    if self.gate == "intake":
      return self._check_intake()
    if self.gate == "approved-tree":
      return self._check_approved_tree()
    if self.gate == "commit-msg":
      return self._check_commit_msg()
    if self.gate == "main-sync":
      return self._check_main_sync()
    if self.gate == "verify-regression":
      return self._verify_regression()
    if self.gate == "check-regression":
      return self._check_regression()
    if self.gate == "repo-tree":
      return self._repo_tree()
    if self.gate == "terminal-title":
      return self._gate_terminal_title()
    if self.gate in ("release-intake", "release-lock"):
      return self._release_intake()
    if self.gate == "read-init-d":
      return self._read_init_d()
    if self.gate == "advance":
      return self._advance()
    raise ValueError(f"Unsupported gate: {self.gate}")
