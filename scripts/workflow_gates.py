#!/usr/bin/env python3
"""Deterministic workflow gate checks emitting Vector 4 Reflexes (GH-57, SCAR-ARCH-06).

Replaces verbose negative prose in SKILL.md with fast, hermetic runtime checks
that exit 0 when invariants hold or emit a structured Vector 4 Reflex on stderr
and exit 2 when violated.
"""

import os
import re
import shlex
import subprocess
import sys
from typing import List, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)

from base_script import BaseScript
from emit_reflex import SilentArgumentParser, emit_reflex

_DEFAULT_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
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

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(prog="workflow_gates.py", add_help=False)
    parser.add_argument("--repo-root", default=_DEFAULT_REPO_ROOT)
    parser.add_argument("--lock-dir", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")

    subparsers = parser.add_subparsers(
        dest="gate", parser_class=SilentArgumentParser
    )

    p_init = subparsers.add_parser("init-makefile", add_help=False)
    p_init.add_argument("--json", action="store_true", dest="sub_json")

    p_files = subparsers.add_parser("plugin-files", add_help=False)
    p_files.add_argument("--json", action="store_true", dest="sub_json")

    p_budget = subparsers.add_parser("skill-budget", add_help=False)
    p_budget.add_argument(
        "--max-bytes", type=int, default=DEFAULT_SKILL_MAX_BYTES
    )
    p_budget.add_argument("--json", action="store_true", dest="sub_json")

    p_intake = subparsers.add_parser("intake", add_help=False)
    p_intake.add_argument("--ticket", default=None)
    p_intake.add_argument("--lock-dir", default=None, dest="sub_lock_dir")
    p_intake.add_argument("--json", action="store_true", dest="sub_json")

    p_rel = subparsers.add_parser("release-intake", add_help=False)
    p_rel.add_argument("--ticket", default=None)
    p_rel.add_argument("--lock-dir", default=None, dest="sub_lock_dir")
    p_rel.add_argument("--json", action="store_true", dest="sub_json")

    p_rel_lock = subparsers.add_parser("release-lock", add_help=False)
    p_rel_lock.add_argument("--ticket", default=None)
    p_rel_lock.add_argument("--lock-dir", default=None, dest="sub_lock_dir")
    p_rel_lock.add_argument("--json", action="store_true", dest="sub_json")

    p_tree = subparsers.add_parser("approved-tree", add_help=False)
    p_tree.add_argument("--json", action="store_true", dest="sub_json")

    p_msg = subparsers.add_parser("commit-msg", add_help=False)
    p_msg.add_argument("--file", dest="commit_msg_file", default=None)
    p_msg.add_argument("--json", action="store_true", dest="sub_json")

    p_title = subparsers.add_parser("terminal-title", add_help=False)
    p_title.add_argument("--ticket", default=None)
    p_title.add_argument("--project", default=None)
    p_title.add_argument("--title", default=None)
    p_title.add_argument("--json", action="store_true", dest="sub_json")

    ns = parser.parse_args(self.raw_args)
    if not ns.gate:
      parser.error(
          "Specify a gate subcommand: init-makefile, plugin-files, skill-budget, "
          "intake, approved-tree, commit-msg, terminal-title, release-intake, or release-lock."
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
    elif ns.gate in ("terminal-title", "intake", "release-intake", "release-lock"):
      self.ticket = getattr(ns, "ticket", None)
      self.project = getattr(ns, "project", None)
      self.title = getattr(ns, "title", None)

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
    gates_script = shlex.quote(os.path.abspath(__file__)).replace("$", "$$")
    guard_script = shlex.quote(
        os.path.join(_SCRIPTS_DIR, "tdd_guard.py")
    ).replace("$", "$$")
    scrub_script = shlex.quote(
        os.path.join(_SCRIPTS_DIR, "ast_scrub.py")
    ).replace("$", "$$")
    quoted_root = shlex.quote(self.repo_root).replace("$", "$$")
    content = (
        "# Synthesized by workflow_gates.py init-makefile (SCAR-ARCH-06, SCAR-TEST-12)\n"
        "PYTHON ?= python3\n"
        "TICKET ?=\n"
        "TEST_DIR =\n"
        "TEST_CMD =\n"
        "ALLOW_HEAD_MOVE =\n"
        "CITE =\n"
        "export TICKET\n"
        "export TEST_DIR TEST_CMD ALLOW_HEAD_MOVE CITE\n\n"
        ".PHONY: check-intake release-intake check-approved-tree check-commit-msg witness-red verify-green verify-citation ast-lint\n\n"
        "check-intake:\n"
        f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} intake $(if $(TICKET),--ticket $(TICKET),)\n\n"
        "release-intake:\n"
        f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} release-intake $(if $(TICKET),--ticket $(TICKET),)\n\n"
        "check-approved-tree:\n"
        f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} approved-tree\n\n"
        "check-commit-msg:\n"
        f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} commit-msg\n\n"
        "witness-red:\n"
        f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} witness-red --make-env\n\n"
        "verify-green:\n"
        f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-green --make-env\n\n"
        "verify-citation:\n"
        f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-citation --make-env\n\n"
        "ast-lint:\n"
        f"\t@$(PYTHON) {scrub_script} --src-dir scripts --strict\n"
    )
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
      from terminal_title import set_terminal_title
      set_terminal_title(repo_root=self.repo_root)
    except (ImportError, OSError):
      pass

    ticket_id = self.ticket
    if not ticket_id:
      try:
        from terminal_title import detect_ticket_id
        ticket_id = detect_ticket_id(repo_root=self.repo_root)
      except Exception:
        ticket_id = ""

    if ticket_id:
      from workspace_mutex import WorkspaceMutex
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
        from terminal_title import detect_ticket_id
        ticket_id = detect_ticket_id(repo_root=self.repo_root)
      except Exception:
        ticket_id = ""

    try:
      from workspace_mutex import WorkspaceMutex
      mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
      if ticket_id:
        mutex.release(ticket_id)
      mutex.release_workspace(self.repo_root)
    except (ImportError, OSError):
      pass
    return 0

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

    return 0

  def _gate_terminal_title(self) -> int:
    try:
      from terminal_title import TerminalTitleScript
      sub_args = ["set", "--repo-root", self.repo_root]
      if self.ticket:
        sub_args.extend(["--ticket", self.ticket])
      if self.project:
        sub_args.extend(["--project", self.project])
      if self.title:
        sub_args.extend(["--title", self.title])
      if self.as_json:
        sub_args.append("--json")
      return TerminalTitleScript().execute(
          args=sub_args,
          stdout=self.stdout,
          stderr=self.stderr,
      )
    except Exception as exc:
      return 1

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
    if self.gate == "terminal-title":
      return self._gate_terminal_title()
    if self.gate in ("release-intake", "release-lock"):
      return self._release_intake()
    raise ValueError(f"Unsupported gate: {self.gate}")


if __name__ == "__main__":
  sys.exit(WorkflowGatesScript().execute())
