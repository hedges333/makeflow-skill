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
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from lib.base_script import BaseScript, SilentArgumentParser
from lib.commit_msg import validate_commit_message
from lib.reflex import emit_reflex

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

  Uses an ephemeral GIT_INDEX_FILE copy when unstaged or staged tracked changes
  exist so .git/index, HEAD, and .workflow/state/approved_tree.txt are never mutated.
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

  status_proc = _run_repo_git(repo_root, "status", "--porcelain")
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
    git_dir_proc = _run_repo_git(repo_root, "rev-parse", "--git-dir")
    git_dir_raw = (
        (git_dir_proc.stdout or "").strip()
        if git_dir_proc.returncode == 0
        else ".git"
    )
    git_dir = (
        git_dir_raw
        if os.path.isabs(git_dir_raw)
        else os.path.join(repo_root, git_dir_raw)
    )
    real_index = os.path.join(git_dir, "index")
    try:
      with tempfile.TemporaryDirectory() as tmp_idx_dir:
        tmp_index = os.path.join(tmp_idx_dir, "index")
        if os.path.isfile(real_index):
          shutil.copyfile(real_index, tmp_index)
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = tmp_index
        _run_repo_git(repo_root, "add", "-u", env=env)
        wt_proc = _run_repo_git(repo_root, "write-tree", env=env)
        wt_sha = (wt_proc.stdout or "").strip()
        if wt_proc.returncode == 0 and wt_sha:
          local_tree = wt_sha
    except OSError:
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


def generate_state_makefile(repo_root: str) -> str:
  """Returns the canonical content of .workflow/state/Makefile for repo_root."""
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
  return (
      "# Synthesized by workflow_gates.py init-makefile (SCAR-ARCH-06, SCAR-TEST-12)\n"
      "PYTHON ?= python3\n"
      "TICKET ?=\n"
      "ALLOW_BLOCKED ?=\n"
      "TEST_DIR =\n"
      "TEST_CMD =\n"
      "ALLOW_HEAD_MOVE =\n"
      "CITE =\n"
      "export TICKET ALLOW_BLOCKED\n"
      "export TEST_DIR TEST_CMD ALLOW_HEAD_MOVE CITE\n\n"
      ".PHONY: check-intake release-intake check-approved-tree check-commit-msg check-main-sync witness-red verify-green verify-citation ast-lint\n\n"
      "check-intake:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} intake $(if $(TICKET),--ticket $(TICKET),) $(if $(ALLOW_BLOCKED),--allow-blocked,)\n\n"
      "release-intake:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} release-intake $(if $(TICKET),--ticket $(TICKET),)\n\n"
      "check-approved-tree:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} approved-tree\n\n"
      "check-commit-msg:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} commit-msg\n\n"
      "check-main-sync:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} main-sync\n\n"
      "witness-red:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} witness-red --make-env\n\n"
      "verify-green:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-green --make-env\n\n"
      "verify-citation:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-citation --make-env\n\n"
      "ast-lint:\n"
      f"\t@$(PYTHON) {scrub_script} --src-dir lib --strict\n"
      f"\t@$(PYTHON) {scrub_script} --src-dir scripts --strict\n"
      f"\t@$(PYTHON) {scrub_script} --src-dir mcp --strict\n"
      f"\t@$(PYTHON) {scrub_script} --src-dir tests --strict\n"
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
    self.project: Optional[str] = None
    self.title: Optional[str] = None
    self.issue_tag: Optional[str] = None
    self.allow_blocked: bool = False
    self.no_fetch: bool = False
    self.fail_on_behind: bool = False

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

    p_title = subparsers.add_parser(
        "terminal-title",
        help="Update the interactive terminal title for the active ticket and project.",
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
        "--project",
        default=None,
        help="Project name to display in the terminal title.",
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
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit output or Vector 4 Reflex errors as JSON.",
    )

    ns = parser.parse_args(self.raw_args)
    if not ns.gate:
      parser.error(
          "Specify a gate subcommand: init-makefile, plugin-files, skill-budget, "
          "intake, approved-tree, commit-msg, main-sync, terminal-title, "
          "release-intake, or release-lock."
      )

    self.repo_root = os.path.abspath(ns.repo_root)
    self.lock_dir = getattr(ns, "sub_lock_dir", None) or getattr(ns, "lock_dir", None)
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))
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
    elif ns.gate in ("terminal-title", "intake", "release-intake", "release-lock"):
      self.ticket = getattr(ns, "ticket", None)
      self.project = getattr(ns, "project", None)
      self.title = getattr(ns, "title", None)
      self.issue_tag = getattr(ns, "issue_tag", None)
      self.allow_blocked = bool(getattr(ns, "allow_blocked", False))

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
      leftovers = sorted(
          fname
          for fname in os.listdir(state_dir)
          if _LEFTOVER_CARD_RE.match(fname)
      )
      if leftovers:
        return self._fail(
            scar_id="GIT-25",
            code="UNCLEAN_INTAKE_STATE",
            trigger=f"Leftover intermediate state file(s) in .workflow/state/: {', '.join(leftovers)}",
            rejected="Starting a new ticket while prior 10_..80_ state cards remain active.",
            reflex="Archive or unlink intermediate state files per state.completedCardAction before writing 10_ticket.md.",
        )

    try:
      from lib.terminal_title import set_terminal_title
      set_terminal_title(repo_root=self.repo_root)
    except (ImportError, OSError):
      pass

    ticket_id = self.ticket
    if not ticket_id:
      try:
        from lib.terminal_title import detect_ticket_id
        ticket_id = detect_ticket_id(repo_root=self.repo_root)
      except (ImportError, OSError, UnicodeDecodeError):
        ticket_id = ""

    if ticket_id:
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

    return 0

  def _release_intake(self) -> int:
    ticket_id = self.ticket
    if not ticket_id:
      try:
        from lib.terminal_title import detect_ticket_id
        ticket_id = detect_ticket_id(repo_root=self.repo_root)
      except (ImportError, OSError, UnicodeDecodeError):
        ticket_id = ""

    try:
      from lib.workspace_mutex import WorkspaceMutex
      mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
      if ticket_id:
        mutex.release(ticket_id)
      released_tickets = mutex.release_workspace(self.repo_root)
      if not ticket_id and released_tickets:
        ticket_id = released_tickets[0]
    except (ImportError, OSError):
      pass

    self._cleanup_tdd_receipt(ticket_id or "")

    try:
      from lib.terminal_title import (
          detect_project_name,
          is_temporary_directory_name,
          set_terminal_title,
      )
      proj = self.project or detect_project_name(repo_root=self.repo_root)
      if proj and not is_temporary_directory_name(proj):
        stream_to_use = getattr(self, "stderr", None)
        set_terminal_title(
            title=proj, repo_root=self.repo_root, stream=stream_to_use
        )
    except (ImportError, OSError):
      pass
    return 0

  def _cleanup_tdd_receipt(self, ticket_id: str) -> None:
    """Archives or unlinks 30_tdd_receipt.json and loaded_scars.json on intake release."""
    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    json_artifacts = ("30_tdd_receipt.json", "loaded_scars.json")
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
    if os.path.isfile(receipt_card):
      with open(receipt_card, "r", encoding="utf-8", errors="replace") as f:
        if "HOSTING_MODE: PURE_GIT" in f.read():
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
      if self.project:
        sub_args.extend(["--project", self.project])
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
    if self.gate == "terminal-title":
      return self._gate_terminal_title()
    if self.gate in ("release-intake", "release-lock"):
      return self._release_intake()
    raise ValueError(f"Unsupported gate: {self.gate}")
