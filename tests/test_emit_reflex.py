#!/usr/bin/env python3
"""Hermetic unit and boundary tests for Vector 4 Reflex gates (GH-57, SCAR-ARCH-06)."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.base_script import BaseScript
from lib.reflex import EmitReflexScript
from lib.workflow_gates import WorkflowGatesScript

# we factored it down, and it's okay if it grows a little, but that should be the hard limit.
# if it fails, it's time to reassess and refactor.
BASELINE_SKILL_BYTES = 24576


def _init_git_repo(repo_dir: str) -> None:
  """Initializes an isolated temporary Git repository with one commit."""
  subprocess.run(
      ["git", "init", "-q"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  subprocess.run(
      ["git", "config", "user.email", "test@example.com"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  subprocess.run(
      ["git", "config", "user.name", "Makeflow Test"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  seed_path = os.path.join(repo_dir, "README.md")
  with open(seed_path, "w", encoding="utf-8") as f:
    f.write("# Isolated Test Repo\n")
  subprocess.run(
      ["git", "add", "README.md"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  subprocess.run(
      ["git", "commit", "-q", "-m", "chore: initial seed"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )


def _exclude_workflow_dirs(repo_dir: str) -> None:
  """Adds .workflow/state/ and .workflow/archive/ to .git/info/exclude."""
  exclude_path = os.path.join(repo_dir, ".git", "info", "exclude")
  os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
  with open(exclude_path, "a", encoding="utf-8") as f:
    f.write("\n.workflow/state/\n.workflow/archive/\n")


class TestEmitReflexScript(unittest.TestCase):
  """In-memory unit tests for EmitReflexScript (SCAR-ARCH-05, SCAR-ARCH-06)."""

  def test_inherits_base_script(self):
    self.assertTrue(issubclass(EmitReflexScript, BaseScript))

  def test_text_format_output_and_exit_code_two(self):
    script = EmitReflexScript()
    out = io.StringIO()
    err = io.StringIO()

    code = script.execute(
        args=[
            "--scar-id",
            "PROC-99",
            "--code",
            "SKILL_BUDGET_EXCEEDED",
            "--trigger",
            "SKILL.md is 25000 bytes.",
            "--rejected",
            "Packing static negative prose into SKILL.md.",
            "--reflex",
            "Move detailed target prose into references/.",
        ],
        stdout=out,
        stderr=err,
    )

    self.assertEqual(code, 2)
    self.assertEqual(out.getvalue(), "")
    rendered = err.getvalue()
    lines = rendered.strip().splitlines()
    self.assertEqual(lines[0], "=" * 80)
    self.assertEqual(lines[1], "[SCAR-ID: PROC-99] SKILL_BUDGET_EXCEEDED")
    self.assertEqual(lines[2], "-" * 80)
    self.assertIn("TRIGGER (THIS):", rendered)
    self.assertIn("SKILL.md is 25000 bytes.", rendered)
    self.assertIn("REJECTED (TRAP):", rendered)
    self.assertIn("Packing static negative prose into SKILL.md.", rendered)
    self.assertIn("REFLEX (ACTION):", rendered)
    self.assertIn("Move detailed target prose into references/.", rendered)
    self.assertEqual(lines[-1], "=" * 80)

  def test_scar_id_normalization_avoids_duplicate_prefix(self):
    for raw_id in ("PROC-99", "SCAR-PROC-99", "SCAR-SCAR-PROC-99"):
      out = io.StringIO()
      err = io.StringIO()
      code = EmitReflexScript().execute(
          args=[
              "--scar-id",
              raw_id,
              "--code",
              "SKILL_BUDGET_EXCEEDED",
              "--trigger",
              "Trigger",
              "--rejected",
              "Trap",
              "--reflex",
              "Action",
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      self.assertIn("[SCAR-ID: PROC-99] SKILL_BUDGET_EXCEEDED", err.getvalue())
      self.assertNotIn("SCAR-SCAR-", err.getvalue())

  def test_positional_arguments_supported(self):
    out = io.StringIO()
    err = io.StringIO()
    code = EmitReflexScript().execute(
        args=[
            "SCAR-GIT-25",
            "UNCLEAN_INTAKE_STATE",
            "Working tree has uncommitted changes.",
            "Starting intake on a dirty checkout.",
            "Commit or stash changes before Target 10.",
        ],
        stdout=out,
        stderr=err,
    )
    self.assertEqual(code, 2)
    self.assertEqual(out.getvalue(), "")
    self.assertIn("[SCAR-ID: GIT-25] UNCLEAN_INTAKE_STATE", err.getvalue())

  def test_json_mode_serialization_and_exit_code_two(self):
    out = io.StringIO()
    err = io.StringIO()
    code = EmitReflexScript().execute(
        args=[
            "--scar-id",
            "PROC-99",
            "--code",
            "SKILL_BUDGET_EXCEEDED",
            "--trigger",
            "SKILL.md exceeded budget.",
            "--rejected",
            "Oversized skill prompt.",
            "--reflex",
            "Trim SKILL.md below ceiling.",
            "--json",
        ],
        stdout=out,
        stderr=err,
    )
    self.assertEqual(code, 2)
    self.assertEqual(out.getvalue(), "")
    payload = json.loads(err.getvalue())
    self.assertEqual(payload["scar_id"], "SCAR-PROC-99")
    self.assertEqual(payload["short_id"], "PROC-99")
    self.assertEqual(payload["violation_code"], "SKILL_BUDGET_EXCEEDED")
    self.assertEqual(payload["trigger"], "SKILL.md exceeded budget.")
    self.assertEqual(payload["rejected"], "Oversized skill prompt.")
    self.assertEqual(payload["reflex"], "Trim SKILL.md below ceiling.")
    self.assertEqual(payload["exit_code"], 2)

  def test_invalid_or_missing_arguments_return_one_without_system_exit(self):
    invalid_arg_sets = [
        [],
        ["--scar-id", "PROC-99"],
        ["ONLY_ONE_POSITIONAL"],
        ["A", "B", "C"],
        ["--unknown-flag"],
        [
            "--scar-id",
            "SCAR-",
            "--code",
            "CODE",
            "--trigger",
            "T",
            "--rejected",
            "R",
            "--reflex",
            "A",
        ],
    ]
    for bad_args in invalid_arg_sets:
      out = io.StringIO()
      err = io.StringIO()
      code = EmitReflexScript().execute(args=bad_args, stdout=out, stderr=err)
      self.assertEqual(code, 1, f"Expected exit code 1 for args={bad_args}")
      self.assertEqual(out.getvalue(), "")
      self.assertIn("Error:", err.getvalue())


class TestWorkflowGatesScript(unittest.TestCase):
  """In-memory hermetic tests for WorkflowGatesScript across all gates."""

  def test_inherits_base_script(self):
    self.assertTrue(issubclass(WorkflowGatesScript, BaseScript))

  def test_plugin_files_gate(self):
    required = [
        "plugin.json",
        "hooks.json",
        "mcp_config.json",
        "default_config.json",
        "skills/makeflow/SKILL.md",
        "rules/AGENTS.md",
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
      for rel in required:
        full = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
          f.write("{}\n")

      out = io.StringIO()
      err = io.StringIO()
      code = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "plugin-files"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0)
      self.assertEqual(err.getvalue(), "")

      os.remove(os.path.join(tmpdir, "rules/AGENTS.md"))
      err_fail = io.StringIO()
      code_fail = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "plugin-files"],
          stdout=io.StringIO(),
          stderr=err_fail,
      )
      self.assertEqual(code_fail, 2)
      self.assertIn(
          "[SCAR-ID: DOC-16] MISSING_REQUIRED_PLUGIN_FILE",
          err_fail.getvalue(),
      )
      self.assertIn("rules/AGENTS.md", err_fail.getvalue())

  def test_skill_budget_gate(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      skill_path = os.path.join(tmpdir, "skills", "makeflow", "SKILL.md")
      os.makedirs(os.path.dirname(skill_path), exist_ok=True)
      with open(skill_path, "w", encoding="utf-8") as f:
        f.write("x" * 512)

      err_ok = io.StringIO()
      code_ok = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "skill-budget", "--max-bytes", "1024"],
          stdout=io.StringIO(),
          stderr=err_ok,
      )
      self.assertEqual(code_ok, 0)
      self.assertEqual(err_ok.getvalue(), "")

      err_over = io.StringIO()
      code_over = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "skill-budget", "--max-bytes", "256"],
          stdout=io.StringIO(),
          stderr=err_over,
      )
      self.assertEqual(code_over, 2)
      self.assertIn(
          "[SCAR-ID: PROC-99] SKILL_CONTEXT_CEILING_EXCEEDED",
          err_over.getvalue(),
      )

  def test_intake_gate(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_git_repo(tmpdir)

      # 1. Fails when .workflow/state/ and .workflow/archive/ are not excluded
      err_unexcluded = io.StringIO()
      code_unexcluded = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_unexcluded,
      )
      self.assertEqual(code_unexcluded, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNCLEAN_INTAKE_STATE", err_unexcluded.getvalue()
      )

      # 2. Passes when clean and excluded (including 00_ and 99_ cards)
      _exclude_workflow_dirs(tmpdir)
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      for allowed_card in ("00_governance.md", "99_next_action.md"):
        with open(
            os.path.join(state_dir, allowed_card), "w", encoding="utf-8"
        ) as f:
          f.write("# allowed\n")

      err_clean = io.StringIO()
      code_clean = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_clean,
      )
      self.assertEqual(code_clean, 0, err_clean.getvalue())
      self.assertEqual(err_clean.getvalue(), "")

      # 3. Fails when leftover 10_..80_ cards exist
      leftover = os.path.join(state_dir, "10_ticket.md")
      with open(leftover, "w", encoding="utf-8") as f:
        f.write("# stale ticket\n")
      err_leftover = io.StringIO()
      code_leftover = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_leftover,
      )
      self.assertEqual(code_leftover, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNCLEAN_INTAKE_STATE", err_leftover.getvalue()
      )
      os.remove(leftover)

      # 4. Fails when leftover approved_tree.txt exists
      tree_leftover = os.path.join(state_dir, "approved_tree.txt")
      with open(tree_leftover, "w", encoding="utf-8") as f:
        f.write("4b825dc642cb6eb9a060e54bf8d69288fbee4904\n")
      err_tree = io.StringIO()
      code_tree = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_tree,
      )
      self.assertEqual(code_tree, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNCLEAN_INTAKE_STATE", err_tree.getvalue()
      )
      self.assertIn("approved_tree.txt", err_tree.getvalue())
      os.remove(tree_leftover)

      err_post_tree = io.StringIO()
      code_post_tree = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_post_tree,
      )
      self.assertEqual(code_post_tree, 0, err_post_tree.getvalue())
      self.assertEqual(err_post_tree.getvalue(), "")

      # 5. Fails when dirty working tree exists
      with open(os.path.join(tmpdir, "dirty.txt"), "w", encoding="utf-8") as f:
        f.write("dirty\n")
      err_dirty = io.StringIO()
      code_dirty = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_dirty,
      )
      self.assertEqual(code_dirty, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNCLEAN_INTAKE_STATE", err_dirty.getvalue()
      )

  def test_approved_tree_gate(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      tree_file = os.path.join(state_dir, "approved_tree.txt")

      # 1. Fails when approved_tree.txt is missing
      err_missing = io.StringIO()
      code_missing = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "approved-tree"],
          stdout=io.StringIO(),
          stderr=err_missing,
      )
      self.assertEqual(code_missing, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNAPPROVED_TREE_DRIFT", err_missing.getvalue()
      )

      # 2. Stage a change, lock approved_tree.txt -> passes cleanly
      feature_file = os.path.join(tmpdir, "feature.py")
      with open(feature_file, "w", encoding="utf-8") as f:
        f.write("VALUE = 1\n")
      subprocess.run(
          ["git", "add", "-A"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      wt_proc = subprocess.run(
          ["git", "write-tree"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(tree_file, "w", encoding="utf-8") as f:
        f.write(wt_proc.stdout.strip() + "\n")

      err_ok = io.StringIO()
      code_ok = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "approved-tree"],
          stdout=io.StringIO(),
          stderr=err_ok,
      )
      self.assertEqual(code_ok, 0, err_ok.getvalue())

      # 3. Fails when unstaged modification exists
      with open(feature_file, "w", encoding="utf-8") as f:
        f.write("VALUE = 2\n")
      err_unstaged = io.StringIO()
      code_unstaged = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "approved-tree"],
          stdout=io.StringIO(),
          stderr=err_unstaged,
      )
      self.assertEqual(code_unstaged, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNAPPROVED_TREE_DRIFT", err_unstaged.getvalue()
      )

      # Restore staged content, then test untracked drift
      with open(feature_file, "w", encoding="utf-8") as f:
        f.write("VALUE = 1\n")
      untracked_file = os.path.join(tmpdir, "untracked.py")
      with open(untracked_file, "w", encoding="utf-8") as f:
        f.write("EXTRA = True\n")
      err_untracked = io.StringIO()
      code_untracked = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "approved-tree"],
          stdout=io.StringIO(),
          stderr=err_untracked,
      )
      self.assertEqual(code_untracked, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNAPPROVED_TREE_DRIFT", err_untracked.getvalue()
      )
      os.remove(untracked_file)

      # 4. Fails when staged index tree mismatches approved_tree.txt
      with open(feature_file, "w", encoding="utf-8") as f:
        f.write("VALUE = 3\n")
      subprocess.run(
          ["git", "add", "feature.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      err_mismatch = io.StringIO()
      code_mismatch = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "approved-tree"],
          stdout=io.StringIO(),
          stderr=err_mismatch,
      )
      self.assertEqual(code_mismatch, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNAPPROVED_TREE_DRIFT", err_mismatch.getvalue()
      )

  def test_commit_msg_gate(self):
    # Part A: GitHub-hosted repository (BLOCKER-2 zero prose heuristics)
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)
      subprocess.run(
          [
              "git",
              "remote",
              "add",
              "origin",
              "https://github.com/example/demo.git",
          ],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      msg_file = os.path.join(state_dir, "50_commit_msg.txt")
      ticket_file = os.path.join(state_dir, "10_ticket.md")

      # 1. Missing commit message file -> returns 2
      err_missing = io.StringIO()
      code_missing = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "commit-msg"],
          stdout=io.StringIO(),
          stderr=err_missing,
      )
      self.assertEqual(code_missing, 2)
      self.assertIn(
          "[SCAR-ID: GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT",
          err_missing.getvalue(),
      )

      # 2. GitHub branch feat/57-vector-4-reflexes mentioning LOCAL-1 and LOCAL-7
      #    in both 10_ticket.md and 50_commit_msg.txt alongside Closes #57 -> returns 0!
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/57-vector-4-reflexes"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(ticket_file, "w", encoding="utf-8") as f:
        f.write(
            "- Ticket ID: `GH-57`\n"
            "- Notes: Enforces rules on LOCAL-1 and LOCAL-7 tasks.\n"
        )
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write(
            "feat: add vector 4 reflex gates\n\n"
            "Validates LOCAL-1 and LOCAL-7 commit trailers deterministically.\n\n"
            "Closes #57\n"
        )
      err_gh_prose = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", tmpdir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_gh_prose,
          ),
          0,
          err_gh_prose.getvalue(),
      )

      # 3. Switch to feat/local-7-demo in GitHub-origin repo
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/local-7-demo"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      # 3a. LOCAL-7 containing 'closes #57' -> returns 2
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("feat: local demo\n\ncloses #57\nTicket: LOCAL-7\n")
      err_closes = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", tmpdir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_closes,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT",
          err_closes.getvalue(),
      )

      # 3b. LOCAL-7 containing bare '#57' -> returns 2
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("feat: local demo (#57)\n\nTicket: LOCAL-7\n")
      err_bare = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", tmpdir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_bare,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT",
          err_bare.getvalue(),
      )

      # 3c. LOCAL-7 omitting 'Ticket: LOCAL-7' -> returns 2
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("feat: local demo\n\nClean body without ticket trailer.\n")
      err_no_trailer = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", tmpdir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_no_trailer,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT",
          err_no_trailer.getvalue(),
      )

      # 3d. Valid LOCAL-7 commit message -> returns 0
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("feat: local demo\n\nTicket: LOCAL-7\n")
      err_valid_local = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", tmpdir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_valid_local,
          ),
          0,
          err_valid_local.getvalue(),
      )

      # 4. Pure Git via 15_archaeology_receipt.md (HOSTING_MODE: PURE_GIT) even with GitHub origin
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/my-change"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      receipt_file = os.path.join(state_dir, "15_archaeology_receipt.md")
      with open(receipt_file, "w", encoding="utf-8") as f:
        f.write("- Hosting Mode: `HOSTING_MODE: PURE_GIT`\n")

      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("feat: pure git change\n\ncloses #57\nTicket: LOCAL-1\n")
      err_pure_closes = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", tmpdir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_pure_closes,
          ),
          2,
      )
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("feat: pure git change\n\nTicket: LOCAL-1\n")
      err_pure_ok = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", tmpdir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_pure_ok,
          ),
          0,
          err_pure_ok.getvalue(),
      )

    # Part B: Pure Git repository with NO origin remote on branch feat/my-change
    with tempfile.TemporaryDirectory() as pure_dir:
      _init_git_repo(pure_dir)
      _exclude_workflow_dirs(pure_dir)
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/my-change"],
          cwd=pure_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      pure_state = os.path.join(pure_dir, ".workflow", "state")
      os.makedirs(pure_state, exist_ok=True)
      pure_msg = os.path.join(pure_state, "50_commit_msg.txt")

      with open(pure_msg, "w", encoding="utf-8") as f:
        f.write("feat: offline change\n\ncloses #57\n")
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", pure_dir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          2,
      )

      with open(pure_msg, "w", encoding="utf-8") as f:
        f.write("feat: offline change\n\nTicket: LOCAL-1\n")
      err_offline_ok = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", pure_dir, "commit-msg"],
              stdout=io.StringIO(),
              stderr=err_offline_ok,
          ),
          0,
          err_offline_ok.getvalue(),
      )

  def test_git_error_timeout_and_non_utf8_resilience(self):
    # 1. Missing repo_root directory returns exit code 2 (not unhandled OSError exit 1)
    with tempfile.TemporaryDirectory() as base_dir:
      missing_root = os.path.join(base_dir, "does_not_exist")
      err_missing = io.StringIO()
      code_missing = WorkflowGatesScript().execute(
          args=["--repo-root", missing_root, "intake"],
          stdout=io.StringIO(),
          stderr=err_missing,
      )
      self.assertEqual(code_missing, 2)
      self.assertIn("[SCAR-ID: GIT-25]", err_missing.getvalue())

      err_init_missing = io.StringIO()
      code_init_missing = WorkflowGatesScript().execute(
          args=["--repo-root", missing_root, "init-makefile"],
          stdout=io.StringIO(),
          stderr=err_init_missing,
      )
      self.assertEqual(code_init_missing, 2)
      self.assertIn("[SCAR-ID: GIT-25]", err_init_missing.getvalue())

    # 2. Subprocess TimeoutExpired in _git() returns exit code 2 with Vector 4 Reflex
    with tempfile.TemporaryDirectory() as tmpdir:
      with mock.patch(
          "lib.workflow_gates.subprocess.run",
          side_effect=subprocess.TimeoutExpired(
              cmd=["git", "status"], timeout=15
          ),
      ):
        err_timeout = io.StringIO()
        code_timeout = WorkflowGatesScript().execute(
            args=["--repo-root", tmpdir, "intake"],
            stdout=io.StringIO(),
            stderr=err_timeout,
        )
        self.assertEqual(code_timeout, 2)
        self.assertIn("[SCAR-ID: GIT-25]", err_timeout.getvalue())

    # 3. Non-UTF-8 bytes in 50_commit_msg.txt and approved_tree.txt do not raise UnicodeDecodeError
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/local-7-demo"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      msg_path = os.path.join(state_dir, "50_commit_msg.txt")
      with open(msg_path, "wb") as f:
        f.write(b"feat: test \xff\xfe bytes\n\nTicket: LOCAL-7\n")

      err_msg = io.StringIO()
      code_msg = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "commit-msg"],
          stdout=io.StringIO(),
          stderr=err_msg,
      )
      self.assertEqual(code_msg, 0, err_msg.getvalue())

      tree_path = os.path.join(state_dir, "approved_tree.txt")
      with open(tree_path, "wb") as f:
        f.write(b"deadbeef\xff\xfe\n")
      err_tree = io.StringIO()
      code_tree = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "approved-tree"],
          stdout=io.StringIO(),
          stderr=err_tree,
      )
      self.assertEqual(code_tree, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNAPPROVED_TREE_DRIFT", err_tree.getvalue()
      )

  def test_release_intake_unlinks_tdd_receipt_and_unblocks_intake(self):
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as lockdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      receipt_path = os.path.join(state_dir, "30_tdd_receipt.json")
      with open(receipt_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ticket": "GH-171", "red_exit": 1}))

      err_blocked = io.StringIO()
      code_blocked = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_blocked,
      )
      self.assertEqual(code_blocked, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNCLEAN_INTAKE_STATE", err_blocked.getvalue()
      )
      self.assertIn("30_tdd_receipt.json", err_blocked.getvalue())

      err_release = io.StringIO()
      code_release = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "--lock-dir",
              lockdir,
              "release-intake",
              "--ticket",
              "GH-171",
          ],
          stdout=io.StringIO(),
          stderr=err_release,
      )
      self.assertEqual(code_release, 0, err_release.getvalue())
      self.assertFalse(os.path.exists(receipt_path))

      err_unblocked = io.StringIO()
      code_unblocked = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_unblocked,
      )
      self.assertEqual(code_unblocked, 0, err_unblocked.getvalue())

  def test_release_intake_archives_tdd_receipt_when_configured(self):
    from lib.workspace_mutex import WorkspaceMutex

    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as lockdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)
      exclude_path = os.path.join(tmpdir, ".git", "info", "exclude")
      with open(exclude_path, "a", encoding="utf-8") as f:
        f.write(".workflow/config.json\n")

      cfg_dir = os.path.join(tmpdir, ".workflow")
      os.makedirs(cfg_dir, exist_ok=True)
      with open(os.path.join(cfg_dir, "config.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"state": {"completedCardAction": "archive"}}))

      state_dir = os.path.join(cfg_dir, "state")
      os.makedirs(state_dir, exist_ok=True)
      receipt_path = os.path.join(state_dir, "30_tdd_receipt.json")
      receipt_payload = json.dumps({"ticket": "GH-171", "red_exit": 1}, indent=2)

      # 1. Explicit --ticket GH-171
      with open(receipt_path, "w", encoding="utf-8") as f:
        f.write(receipt_payload)

      err_rel = io.StringIO()
      code_rel = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "--lock-dir",
              lockdir,
              "release-intake",
              "--ticket",
              "GH-171",
          ],
          stdout=io.StringIO(),
          stderr=err_rel,
      )
      self.assertEqual(code_rel, 0, err_rel.getvalue())
      self.assertFalse(os.path.exists(receipt_path))
      archived_path = os.path.join(
          cfg_dir, "archive", "GH-171", "30_tdd_receipt.json"
      )
      self.assertTrue(os.path.isfile(archived_path))
      with open(archived_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), receipt_payload)

      err_intake = io.StringIO()
      code_intake = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_intake,
      )
      self.assertEqual(code_intake, 0, err_intake.getvalue())

      # 2. Omitted --ticket detected from active workspace mutex
      os.remove(archived_path)
      mutex = WorkspaceMutex(repo_root=tmpdir, lock_dir=lockdir)
      acq = mutex.acquire("GH-171", branch_name="main")
      self.assertTrue(acq.get("acquired"))
      with open(receipt_path, "w", encoding="utf-8") as f:
        f.write(receipt_payload)

      err_rel_auto = io.StringIO()
      code_rel_auto = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "--lock-dir",
              lockdir,
              "release-intake",
          ],
          stdout=io.StringIO(),
          stderr=err_rel_auto,
      )
      self.assertEqual(code_rel_auto, 0, err_rel_auto.getvalue())
      self.assertFalse(os.path.exists(receipt_path))
      self.assertTrue(os.path.isfile(archived_path))
      with open(archived_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), receipt_payload)

  def test_release_intake_is_idempotent_when_tdd_receipt_absent(self):
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as lockdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)
      exclude_path = os.path.join(tmpdir, ".git", "info", "exclude")
      with open(exclude_path, "a", encoding="utf-8") as f:
        f.write(".workflow/config.json\n")

      cfg_dir = os.path.join(tmpdir, ".workflow")
      os.makedirs(cfg_dir, exist_ok=True)

      for action in ("unlink", "archive"):
        with open(
            os.path.join(cfg_dir, "config.json"), "w", encoding="utf-8"
        ) as f:
          f.write(json.dumps({"state": {"completedCardAction": action}}))

        err_rel = io.StringIO()
        code_rel = WorkflowGatesScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "--lock-dir",
                lockdir,
                "release-intake",
                "--ticket",
                "GH-171",
            ],
            stdout=io.StringIO(),
            stderr=err_rel,
        )
        self.assertEqual(code_rel, 0, err_rel.getvalue())
        self.assertFalse(
            os.path.exists(
                os.path.join(cfg_dir, "archive", "GH-171", "30_tdd_receipt.json")
            )
        )

  def test_release_intake_warns_on_tdd_receipt_cleanup_oserror(self):
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as lockdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)

      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      receipt_path = os.path.join(state_dir, "30_tdd_receipt.json")
      with open(receipt_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ticket_id": "GH-171", "green_exit": 0}))

      err = io.StringIO()
      with mock.patch("os.unlink", side_effect=OSError("Permission denied")):
        code = WorkflowGatesScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "--lock-dir",
                lockdir,
                "release-intake",
                "--ticket",
                "GH-171",
            ],
            stdout=io.StringIO(),
            stderr=err,
        )

      self.assertEqual(code, 0, err.getvalue())
      self.assertTrue(os.path.isfile(receipt_path))
      err_output = err.getvalue()
      self.assertIn("Warning:", err_output)
      self.assertIn("30_tdd_receipt.json", err_output)
      self.assertIn("Permission denied", err_output)


class TestCliAndMakefileIntegration(unittest.TestCase):
  """Boundary tests for CLI wrapper, consumer Makefile synthesis, and exit code 2."""

  def test_emit_reflex_cli_wrapper_exits_two(self):
    emit_reflex_path = os.path.join(_SCRIPTS_DIR, "emit_reflex.py")
    proc = subprocess.run(
        [
            sys.executable,
            emit_reflex_path,
            "--scar-id",
            "PROC-99",
            "--code",
            "SKILL_BUDGET_EXCEEDED",
            "--trigger",
            "CLI test trigger",
            "--rejected",
            "CLI test trap",
            "--reflex",
            "CLI test action",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    self.assertEqual(proc.returncode, 2)
    self.assertEqual(proc.stdout, "")
    self.assertIn("[SCAR-ID: PROC-99] SKILL_BUDGET_EXCEEDED", proc.stderr)

  def test_makefile_check_plugin_files_gate_emits_reflex_and_exits_two(self):
    repo_makefile = os.path.join(_REPO_ROOT, "Makefile")
    with tempfile.TemporaryDirectory() as empty_dir:
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              repo_makefile,
              "check-plugin-files",
              f"REPO_ROOT={empty_dir}",
              f"PYTHON={sys.executable}",
          ],
          cwd=empty_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 2, proc.stderr)
      self.assertIn(
          "[SCAR-ID: DOC-16] MISSING_REQUIRED_PLUGIN_FILE", proc.stderr
      )

  def test_consumer_workspace_init_makefile_and_isolated_gates(self):
    consumer_makefile_bytes = b"build:\n\t@echo consumer-build\n"
    with tempfile.TemporaryDirectory() as base_tmp:
      consumer_dir = os.path.join(base_tmp, "consumer repo")
      os.makedirs(consumer_dir, exist_ok=True)
      _init_git_repo(consumer_dir)
      consumer_mk = os.path.join(consumer_dir, "Makefile")
      with open(consumer_mk, "wb") as f:
        f.write(consumer_makefile_bytes)
      subprocess.run(
          ["git", "add", "Makefile"],
          cwd=consumer_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: add consumer Makefile"],
          cwd=consumer_dir,
          check=True,
          capture_output=True,
          text=True,
      )

      # Synthesize .workflow/state/Makefile via init-makefile
      out_init = io.StringIO()
      err_init = io.StringIO()
      code_init = WorkflowGatesScript().execute(
          args=["--repo-root", consumer_dir, "init-makefile"],
          stdout=out_init,
          stderr=err_init,
      )
      self.assertEqual(code_init, 0, err_init.getvalue())
      synth_mk = os.path.join(consumer_dir, ".workflow", "state", "Makefile")
      self.assertTrue(os.path.isfile(synth_mk))
      with open(synth_mk, "r", encoding="utf-8") as f:
        synth_mk_text = f.read()
      for scan_dir in ("lib", "scripts", "mcp", "tests"):
        self.assertIn(f"--src-dir {scan_dir} --strict", synth_mk_text)

      # 1. check-intake fails (exit 2) before .workflow/state/ is git-excluded
      p_intake_fail = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "check-intake",
              f"PYTHON={sys.executable}",
          ],
          cwd=consumer_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_intake_fail.returncode, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNCLEAN_INTAKE_STATE", p_intake_fail.stderr
      )

      # Exclude workflow dirs -> check-intake exits 0
      _exclude_workflow_dirs(consumer_dir)
      p_intake_ok = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "check-intake",
              f"PYTHON={sys.executable}",
          ],
          cwd=consumer_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_intake_ok.returncode, 0, p_intake_ok.stderr)

      # 2. check-approved-tree fails (exit 2) before locking tree, exits 0 when locked
      p_tree_fail = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "check-approved-tree",
              f"PYTHON={sys.executable}",
          ],
          cwd=consumer_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_tree_fail.returncode, 2)
      self.assertIn(
          "[SCAR-ID: GIT-25] UNAPPROVED_TREE_DRIFT", p_tree_fail.stderr
      )

      wt_proc = subprocess.run(
          ["git", "write-tree"],
          cwd=consumer_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(
          os.path.join(consumer_dir, ".workflow", "state", "approved_tree.txt"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write(wt_proc.stdout.strip() + "\n")

      p_tree_ok = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "check-approved-tree",
              f"PYTHON={sys.executable}",
          ],
          cwd=consumer_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_tree_ok.returncode, 0, p_tree_ok.stderr)

      # 3. check-commit-msg fails (exit 2) when missing, exits 0 when valid
      p_msg_fail = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "check-commit-msg",
              f"PYTHON={sys.executable}",
          ],
          cwd=consumer_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_msg_fail.returncode, 2)
      self.assertIn(
          "[SCAR-ID: GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT",
          p_msg_fail.stderr,
      )

      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/local-3-consumer"],
          cwd=consumer_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(
          os.path.join(consumer_dir, ".workflow", "state", "50_commit_msg.txt"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("feat: consumer feature\n\nTicket: LOCAL-3\n")

      p_msg_ok = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "check-commit-msg",
              f"PYTHON={sys.executable}",
          ],
          cwd=consumer_dir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_msg_ok.returncode, 0, p_msg_ok.stderr)

      # Consumer root Makefile must remain byte-for-byte untouched
      with open(consumer_mk, "rb") as f:
        self.assertEqual(f.read(), consumer_makefile_bytes)

  def _setup_gh_182_install_fixture(self, base_dir: str, with_agy: bool):
    """Creates a hermetic source repo, HOME, TMPDIR, and isolated PATH fixture."""
    home_dir = os.path.join(base_dir, "home")
    tmp_dir = os.path.join(base_dir, "tmp")
    bin_dir = os.path.join(base_dir, "bin")
    ext_dir = os.path.join(base_dir, "external_runtime")
    src_dir = os.path.join(base_dir, "src_repo")
    install_dir = os.path.join(
        home_dir, ".gemini", "config", "plugins", "makeflow"
    )
    for d in (home_dir, tmp_dir, bin_dir, ext_dir, src_dir):
      os.makedirs(d, exist_ok=True)

    ext_python = os.path.join(ext_dir, "python3.13")
    with open(ext_python, "w", encoding="utf-8") as f:
      f.write("#!/bin/sh\nexit 0\n")
    os.chmod(ext_python, 0o755)

    files_to_create = {
        "plugin.json": '{"name": "makeflow"}\n',
        "skills/makeflow/SKILL.md": "# Makeflow Skill\n",
        ".workflow/config.json.example": "{}\n",
        ".git/HEAD": "ref: refs/heads/main\n",
        ".tmp/scratch.txt": "scratch\n",
        ".workflow/state/10_ticket.md": "# active ticket\n",
        ".workflow/archive/GH-1/10_ticket.md": "# archived ticket\n",
        ".workflow/config.json": '{"local": true}\n',
        ".workflow/plan.md": "# local plan\n",
        ".workflow/commit_msg.txt": "feat: local commit\n",
    }
    for rel_path, content in files_to_create.items():
      full_path = os.path.join(src_dir, rel_path)
      os.makedirs(os.path.dirname(full_path), exist_ok=True)
      with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    venv_bin = os.path.join(src_dir, ".venv", "bin")
    os.makedirs(venv_bin, exist_ok=True)
    os.symlink(ext_python, os.path.join(venv_bin, "python"))

    for tool in (
        "sh",
        "bash",
        "tar",
        "mkdir",
        "rm",
        "mktemp",
        "true",
        "make",
        "cat",
        "cp",
        "dirname",
        "basename",
        "env",
    ):
      resolved = shutil.which(tool)
      if resolved:
        os.symlink(resolved, os.path.join(bin_dir, tool))

    agy_log = os.path.join(base_dir, "agy_install_target.txt")
    if with_agy:
      agy_path = os.path.join(bin_dir, "agy")
      mock_agy_code = f"""#!{sys.executable}
import os
import shutil
import sys

EXCLUDED = (
    ".git",
    ".tmp",
    ".venv",
    ".workflow/state",
    ".workflow/archive",
    ".workflow/config.json",
    ".workflow/plan.md",
    ".workflow/commit_msg.txt",
)

args = sys.argv[1:]
if args[:2] == ["plugin", "validate"]:
  sys.exit(0)
if args[:2] == ["plugin", "install"]:
  target = os.path.abspath(args[2])
  target_real = os.path.realpath(target)
  log_file = os.environ.get("AGY_INSTALL_TARGET_LOG", "")
  if log_file:
    with open(log_file, "w", encoding="utf-8") as lf:
      lf.write(target)
  for root, dirs, files in os.walk(target):
    for entry in list(dirs) + list(files):
      full = os.path.join(root, entry)
      if os.path.islink(full):
        resolved = os.path.realpath(full)
        if os.path.commonpath([target_real, resolved]) != target_real:
          sys.stderr.write(
              f"Error: failed to copy plugin: symlink {{full}} points outside {{target}}, to {{resolved}}\\n"
          )
          sys.exit(1)
  for rel in EXCLUDED:
    candidate = os.path.join(target, rel)
    if os.path.lexists(candidate):
      sys.stderr.write(f"Error: excluded path present in staged tree: {{rel}}\\n")
      sys.exit(1)
  dest = os.path.join(
      os.environ["HOME"], ".gemini", "config", "plugins", "makeflow"
  )
  if os.path.lexists(dest):
    shutil.rmtree(dest)
  os.makedirs(os.path.dirname(dest), exist_ok=True)
  shutil.copytree(target, dest, symlinks=True)
  sys.exit(0)
sys.exit(1)
"""
      with open(agy_path, "w", encoding="utf-8") as f:
        f.write(mock_agy_code)
      os.chmod(agy_path, 0o755)

    env = {
        "HOME": home_dir,
        "PATH": bin_dir,
        "TMPDIR": tmp_dir,
        "AGY_INSTALL_TARGET_LOG": agy_log,
    }
    return {
        "home_dir": home_dir,
        "tmp_dir": tmp_dir,
        "bin_dir": bin_dir,
        "src_dir": src_dir,
        "install_dir": install_dir,
        "agy_log": agy_log,
        "env": env,
    }

  def _assert_gh_182_clean_install(
      self, fixture: dict, expect_agy_staged: bool
  ) -> None:
    install_dir = fixture["install_dir"]
    for included in (
        "plugin.json",
        "skills/makeflow/SKILL.md",
        ".workflow/config.json.example",
    ):
      self.assertTrue(
          os.path.isfile(os.path.join(install_dir, included)),
          f"Expected installed file missing: {included}",
      )
    for excluded in (
        ".git",
        ".git/HEAD",
        ".tmp",
        ".tmp/scratch.txt",
        ".venv",
        ".venv/bin/python",
        ".workflow/state",
        ".workflow/state/10_ticket.md",
        ".workflow/archive",
        ".workflow/archive/GH-1/10_ticket.md",
        ".workflow/config.json",
        ".workflow/plan.md",
        ".workflow/commit_msg.txt",
    ):
      self.assertFalse(
          os.path.lexists(os.path.join(install_dir, excluded)),
          f"Excluded path leaked into install_dir: {excluded}",
      )
    self.assertEqual(
        os.listdir(fixture["tmp_dir"]),
        [],
        "Temporary staging directory was not cleaned up on exit",
    )
    if expect_agy_staged:
      self.assertTrue(os.path.isfile(fixture["agy_log"]))
      with open(fixture["agy_log"], "r", encoding="utf-8") as f:
        staged_target = f.read().strip()
      self.assertNotEqual(
          os.path.realpath(staged_target),
          os.path.realpath(fixture["src_dir"]),
      )
      self.assertEqual(os.path.basename(staged_target), "makeflow")
      self.assertFalse(os.path.exists(staged_target))

  def test_gh_182_makefile_install_with_agy_stages_clean_tree_excluding_venv_symlinks(
      self,
  ):
    repo_makefile = os.path.join(_REPO_ROOT, "Makefile")
    with tempfile.TemporaryDirectory() as base_dir:
      fx = self._setup_gh_182_install_fixture(base_dir, with_agy=True)
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              repo_makefile,
              "install",
              f"MKFILE_DIR={fx['src_dir']}",
              f"INSTALL_DIR={fx['install_dir']}",
              "PYTHON=true",
          ],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      self._assert_gh_182_clean_install(fx, expect_agy_staged=True)

  def test_gh_182_makefile_install_fallback_without_agy_excludes_venv_and_local_state(
      self,
  ):
    repo_makefile = os.path.join(_REPO_ROOT, "Makefile")
    with tempfile.TemporaryDirectory() as base_dir:
      fx = self._setup_gh_182_install_fixture(base_dir, with_agy=False)
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              repo_makefile,
              "install",
              f"MKFILE_DIR={fx['src_dir']}",
              f"INSTALL_DIR={fx['install_dir']}",
              "PYTHON=true",
          ],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      self._assert_gh_182_clean_install(fx, expect_agy_staged=False)

  def test_gh_182_install_sh_with_agy_stages_clean_tree_excluding_venv_symlinks(
      self,
  ):
    install_sh = os.path.join(_REPO_ROOT, "install.sh")
    with tempfile.TemporaryDirectory() as base_dir:
      fx = self._setup_gh_182_install_fixture(base_dir, with_agy=True)
      proc = subprocess.run(
          ["bash", install_sh],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      self._assert_gh_182_clean_install(fx, expect_agy_staged=True)

  def test_gh_182_install_sh_fallback_without_agy_excludes_venv_and_archive(
      self,
  ):
    install_sh = os.path.join(_REPO_ROOT, "install.sh")
    with tempfile.TemporaryDirectory() as base_dir:
      fx = self._setup_gh_182_install_fixture(base_dir, with_agy=False)
      proc = subprocess.run(
          ["bash", install_sh],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      self._assert_gh_182_clean_install(fx, expect_agy_staged=False)


class TestSkillContextReduction(unittest.TestCase):
  """Verifies GH-57 static context reduction and core safety invariants on SKILL.md."""

  def test_skill_md_at_less_than_baseline(self):
    skill_md = os.path.join(_REPO_ROOT, "skills", "makeflow", "SKILL.md")
    self.assertTrue(os.path.isfile(skill_md))
    actual_bytes = os.path.getsize(skill_md)
    self.assertLessEqual(
        actual_bytes,
        BASELINE_SKILL_BYTES,
        f"Expected skills/makeflow/SKILL.md <= {BASELINE_SKILL_BYTES} bytes "
        f"(<= {BASELINE_SKILL_BYTES}), got {actual_bytes} bytes: must re-engineer and refactor",
    )

  def test_skill_md_preserves_custom_workflow_pause_gate_floor(self):
    skill_md = os.path.join(_REPO_ROOT, "skills", "makeflow", "SKILL.md")
    with open(skill_md, "r", encoding="utf-8") as f:
      text = f.read()
    sections = text.split("\n---\n")
    # sections[0] has frontmatter + Core Rules (or sections[1] if frontmatter ends with ---)
    core_rules = sections[1] if text.startswith("---\n") else sections[0]
    self.assertIn("AGENTS.md", core_rules)
    self.assertIn("CLAUDE.md", core_rules)
    self.assertIn("SCAR-PROC-102", core_rules)
    self.assertIn("disable", core_rules.lower())


if __name__ == "__main__":
  unittest.main()
