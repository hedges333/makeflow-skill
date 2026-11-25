"""Merkle tree lock, Conventional Commit message, and post-commit gates (GH-274).

Verifies ``.workflow/state/approved_tree.txt`` against the staged Git index
(SCAR-GIT-23), validates prepared commit messages against Conventional Commits
1.0.0 and local/remote trailer rules, and revalidates ``HEAD`` after commit.
"""

from dataclasses import dataclass
import functools
import os
import re
import subprocess
from typing import Callable, Optional

from lib.commit_msg import (
    detect_commit_context,
    validate_commit_message,
    validate_head_commit,
)
from lib.main_sync import _run_repo_git
from lib.reflex import GateFailure

_GH_CLOSE_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#\d+\b"
)
_BARE_ISSUE_RE = re.compile(r"(?:^|[\s(\[])#\d+\b")

GitRunner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class PostCommitResult:
  """Structured outcome returned by ``check_post_commit``.

  Attributes:
    ok: True when HEAD commit message and tree match approved state.
    head_tree: Committed ``HEAD^{tree}`` SHA on success.
    expected_tree: Locked SHA from ``.workflow/state/approved_tree.txt``.
    failure: Populated ``GateFailure`` when ``ok`` is False.
  """

  ok: bool
  head_tree: str = ""
  expected_tree: str = ""
  failure: Optional[GateFailure] = None


def check_approved_tree(
    repo_root: str,
    git_runner: Optional[GitRunner] = None,
) -> Optional[GateFailure]:
  """Verifies that the staged Git index matches .workflow/state/approved_tree.txt.

  Args:
    repo_root: Workspace Git repository root directory.
    git_runner: Optional callable executing ``git *args`` inside ``repo_root``.
      Defaults to a local subprocess runner.

  Returns:
    ``None`` when the staged index is clean and matches ``approved_tree.txt``;
    otherwise a ``GateFailure`` describing drift or missing lock state.
  """
  run_git = (
      git_runner
      if git_runner is not None
      else functools.partial(_run_repo_git, repo_root, timeout=15)
  )
  tree_path = os.path.join(
      repo_root, ".workflow", "state", "approved_tree.txt"
  )
  if not os.path.isfile(tree_path):
    return GateFailure(
        scar_id="GIT-25",
        code="UNAPPROVED_TREE_DRIFT",
        trigger="Missing .workflow/state/approved_tree.txt.",
        rejected="Advancing past Target 40 without a locked Git Merkle tree hash.",
        reflex="Run git add -A && git write-tree > .workflow/state/approved_tree.txt and complete Target 40 review.",
    )
  with open(tree_path, "r", encoding="utf-8", errors="replace") as f:
    expected_tree = f.read().strip()
  if not expected_tree:
    return GateFailure(
        scar_id="GIT-25",
        code="UNAPPROVED_TREE_DRIFT",
        trigger=".workflow/state/approved_tree.txt is empty.",
        rejected="Verifying review sign-off against an empty Merkle tree hash.",
        reflex="Lock the staged tree with git add -A && git write-tree > .workflow/state/approved_tree.txt and re-run Target 40.",
    )

  status_proc = run_git("status", "--porcelain")
  if status_proc.returncode != 0:
    return GateFailure(
        scar_id="GIT-25",
        code="UNAPPROVED_TREE_DRIFT",
        trigger=f"git status failed in {repo_root}: {(status_proc.stderr or '').strip()}",
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
    return GateFailure(
        scar_id="GIT-25",
        code="UNAPPROVED_TREE_DRIFT",
        trigger=f"Unstaged or untracked working-tree drift detected: {', '.join(drift_items)}",
        rejected="Committing or signing off when unstaged edits or untracked files bypass the audited index.",
        reflex="Stage intended edits with git add -A, update approved_tree.txt, and re-run Target 40 adversarial review.",
    )

  wt_proc = run_git("write-tree")
  actual_tree = (wt_proc.stdout or "").strip()
  if wt_proc.returncode != 0 or actual_tree != expected_tree:
    return GateFailure(
        scar_id="GIT-25",
        code="UNAPPROVED_TREE_DRIFT",
        trigger=f"Staged git write-tree ({actual_tree or 'error'}) does not match approved_tree.txt ({expected_tree}).",
        rejected="Committing code modifications that drifted after Target 40 review.",
        reflex="Re-lock git add -A && git write-tree > .workflow/state/approved_tree.txt and run a fresh Target 40 review.",
    )

  return None


def check_commit_msg(
    repo_root: str,
    commit_msg_file: Optional[str] = None,
) -> Optional[GateFailure]:
  """Validates the prepared commit message file against Conventional Commits and trailers.

  Args:
    repo_root: Workspace Git repository root directory.
    commit_msg_file: Optional path to the commit message file. Defaults to
      ``.workflow/state/50_commit_msg.txt`` under ``repo_root``.

  Returns:
    ``None`` when the commit message passes validation; otherwise a
    ``GateFailure`` describing the contract violation.
  """
  if commit_msg_file:
    msg_path = (
        commit_msg_file
        if os.path.isabs(commit_msg_file)
        else os.path.join(repo_root, commit_msg_file)
    )
  else:
    msg_path = os.path.join(
        repo_root, ".workflow", "state", "50_commit_msg.txt"
    )

  if not os.path.isfile(msg_path):
    return GateFailure(
        scar_id="GIT-20",
        code="INVALID_COMMIT_MESSAGE_CONTRACT",
        trigger=f"Commit message file not found: {msg_path}",
        rejected="Running Target 50 commit without a prepared commit message file.",
        reflex="Write the proposed commit message to .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
    )

  with open(msg_path, "r", encoding="utf-8", errors="replace") as f:
    msg_text = f.read()
  if not msg_text.strip():
    return GateFailure(
        scar_id="GIT-20",
        code="INVALID_COMMIT_MESSAGE_CONTRACT",
        trigger=f"Commit message file is empty: {msg_path}",
        rejected="Committing with an empty commit message.",
        reflex="Write a non-empty subject and body in .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
    )

  state_dir = os.path.join(repo_root, ".workflow", "state")
  is_local, ticket_id = detect_commit_context(repo_root, state_dir=state_dir)
  local_num: Optional[str] = None
  if ticket_id and ticket_id.upper().startswith("LOCAL-"):
    suffix = ticket_id[6:]
    if suffix.isdigit():
      local_num = suffix

  validation = validate_commit_message(
      msg_text, is_local=is_local, ticket_id=ticket_id
  )

  if not validation.valid:
    if is_local:
      if _GH_CLOSE_RE.search(msg_text) or _BARE_ISSUE_RE.search(msg_text):
        return GateFailure(
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
        return GateFailure(
            scar_id="GIT-20",
            code="INVALID_COMMIT_MESSAGE_CONTRACT",
            trigger=f"Local session commit message is missing required 'Ticket: {shown_id}' line.",
            rejected="Committing a local task without explicit LOCAL-<N> disambiguation.",
            reflex=f"Add 'Ticket: {shown_id}' on its own line in .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
        )

      return GateFailure(
          scar_id="GIT-20",
          code="INVALID_COMMIT_MESSAGE_CONTRACT",
          trigger=validation.errors[0],
          rejected="Committing a local task with invalid Conventional Commits or changed file format.",
          reflex="Correct commit message formatting in .workflow/state/50_commit_msg.txt and re-run make -f .workflow/state/Makefile check-commit-msg.",
      )
    return GateFailure(
        scar_id="GIT-20",
        code="INVALID_COMMIT_MESSAGE_CONTRACT",
        trigger=validation.errors[0],
        rejected="Committing a message that violates Conventional Commits 1.0.0 or trailer conventions.",
        reflex="Format .workflow/state/50_commit_msg.txt using Conventional Commits and ensure issue reference trailer exists per 05_pre_commit_hygiene.md.",
    )

  return None


def check_post_commit(
    repo_root: str,
    commit_msg_file: Optional[str] = None,
) -> PostCommitResult:
  """Revalidates HEAD commit message, 50_commit_msg.txt match, and HEAD^{tree}.

  Args:
    repo_root: Workspace Git repository root directory.
    commit_msg_file: Optional override path for the approved commit message file.

  Returns:
    A ``PostCommitResult`` indicating success with tree SHAs or failure with a
    populated ``GateFailure``.
  """
  state_dir = os.path.join(repo_root, ".workflow", "state")
  res = validate_head_commit(
      repo_root,
      state_dir=state_dir,
      commit_msg_file=commit_msg_file,
  )
  if not res.ok:
    if res.violation_code == "UNAPPROVED_TREE_DRIFT":
      rejected = (
          "Exiting Target 50 when committed HEAD^{tree} drifted from"
          " .workflow/state/approved_tree.txt."
      )
      reflex = (
          "Stage the intended tree, re-lock approved_tree.txt, and return"
          " to Target 40 adversarial review before committing."
      )
    elif res.violation_code == "COMMIT_MESSAGE_DRIFT":
      rejected = (
          "Exiting Target 50 when HEAD commit message differs from"
          " approved .workflow/state/50_commit_msg.txt."
      )
      reflex = (
          "Amend HEAD using 'git commit --amend -F"
          " .workflow/state/50_commit_msg.txt' after operator confirmation"
          " and re-run check-post-commit."
      )
    else:
      rejected = (
          "Exiting Target 50 when HEAD commit message violates"
          " Conventional Commits 1.0.0 or trailer invariants."
      )
      reflex = (
          "Format .workflow/state/50_commit_msg.txt per"
          " 05_pre_commit_hygiene.md, amend HEAD with '-F"
          " .workflow/state/50_commit_msg.txt', and re-run check-post-commit."
      )
    return PostCommitResult(
        ok=False,
        failure=GateFailure(
            scar_id=res.scar_id or "GIT-20",
            code=res.violation_code or "INVALID_COMMIT_MESSAGE_CONTRACT",
            trigger=res.reason,
            rejected=rejected,
            reflex=reflex,
        ),
    )
  return PostCommitResult(
      ok=True,
      head_tree=res.head_tree,
      expected_tree=res.expected_tree,
  )
