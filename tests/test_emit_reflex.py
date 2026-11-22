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
    self.assertIn("SOCRATIC PROBE:", rendered)
    self.assertIn("?", rendered)
    self.assertEqual(lines[-1], "=" * 80)

  def test_emit_reflex_includes_socratic_probe_for_all_core_gate_scars(self):
    from lib.reflex import emit_reflex, resolve_core_scar_probe

    core_gate_scar_ids = [
        "PROC-99",
        "PROC-102",
        "PROC-124",
        "TEST-08",
        "TEST-10",
        "TEST-12",
        "AST-01",
        "GIT-20",
        "GIT-23",
        "GIT-24",
        "GIT-25",
        "SYS-08",
        "DB-09",
        "SEC-01",
        "ARCH-06",
        "ARCH-08",
        "ARCH-09",
        "ARCH-10",
    ]
    for scar_id in core_gate_scar_ids:
      probe = resolve_core_scar_probe(scar_id, repo_root=_REPO_ROOT)
      self.assertTrue(
          probe.strip().endswith("?"),
          f"Expected non-empty Socratic Probe ending with '?' for {scar_id}, got: {probe!r}",
      )

    err_txt = io.StringIO()
    emit_reflex(
        scar_id="TEST-12",
        code="RED_RECEIPT_MISSING",
        trigger="Missing receipt.",
        rejected="Fake red phase.",
        reflex="Run witness-red.",
        output_json=False,
        stream=err_txt,
    )
    self.assertIn("SOCRATIC PROBE:", err_txt.getvalue())

    out_json = io.StringIO()
    emit_reflex(
        scar_id="TEST-12",
        code="RED_RECEIPT_MISSING",
        trigger="Missing receipt.",
        rejected="Fake red phase.",
        reflex="Run witness-red.",
        output_json=True,
        stream=out_json,
    )
    payload = json.loads(out_json.getvalue())
    self.assertIn("socratic_probe", payload)
    self.assertTrue(payload["socratic_probe"].endswith("?"))

  def test_emit_reflex_arch_09_upstream_root_cause_probe(self):
    """Verifies ARCH-09 Socratic Probe resolution, Simple English compliance, and emit_reflex output (GH-232)."""
    from lib.reflex import emit_reflex, resolve_core_scar_probe
    from lib.scar_candidate import validate_socratic_probe

    probe = resolve_core_scar_probe("ARCH-09", repo_root=_REPO_ROOT)
    self.assertEqual(
        validate_socratic_probe(probe, scar_id="SCAR-ARCH-09"),
        [],
        f"Expected valid Socratic Probe for SCAR-ARCH-09, got: {probe!r}",
    )

    err_txt = io.StringIO()
    exit_code = emit_reflex(
        scar_id="ARCH-09",
        code="DOWNSTREAM_WORKAROUND",
        trigger="Patched symptom downstream.",
        rejected="Stacking defensive workarounds downstream.",
        reflex="Fix the root cause upstream.",
        output_json=False,
        stream=err_txt,
    )
    self.assertEqual(exit_code, 2)
    rendered = err_txt.getvalue()
    self.assertIn("[SCAR-ID: ARCH-09] DOWNSTREAM_WORKAROUND", rendered)
    self.assertIn("SOCRATIC PROBE:", rendered)
    self.assertIn(probe, rendered)

    out_json = io.StringIO()
    json_code = emit_reflex(
        scar_id="ARCH-09",
        code="DOWNSTREAM_WORKAROUND",
        trigger="Patched symptom downstream.",
        rejected="Stacking defensive workarounds downstream.",
        reflex="Fix the root cause upstream.",
        output_json=True,
        stream=out_json,
    )
    self.assertEqual(json_code, 2)
    payload = json.loads(out_json.getvalue())
    self.assertEqual(payload["scar_id"], "SCAR-ARCH-09")
    self.assertEqual(payload["short_id"], "ARCH-09")
    self.assertEqual(payload["socratic_probe"], probe)
    self.assertEqual(payload["exit_code"], 2)

  def test_emit_reflex_arch_10_structured_data_probe(self):
    """Verifies ARCH-10 Socratic Probe resolution, Simple English compliance, and emit_reflex output (GH-240)."""
    from lib.reflex import emit_reflex, resolve_core_scar_probe
    from lib.scar_candidate import validate_socratic_probe

    probe = resolve_core_scar_probe("ARCH-10", repo_root=_REPO_ROOT)
    self.assertEqual(
        validate_socratic_probe(probe, scar_id="SCAR-ARCH-10"),
        [],
        f"Expected valid Socratic Probe for SCAR-ARCH-10, got: {probe!r}",
    )

    err_txt = io.StringIO()
    exit_code = emit_reflex(
        scar_id="ARCH-10",
        code="LOSSY_PROSE_ROUNDTRIP",
        trigger="Scraped Markdown prose for structured JSON ticket fields.",
        rejected="Using regex parsers over prose instead of persisted JSON.",
        reflex="Read structured ticket fields from lib/ticket_state.py.",
        output_json=False,
        stream=err_txt,
    )
    self.assertEqual(exit_code, 2)
    rendered = err_txt.getvalue()
    self.assertIn("[SCAR-ID: ARCH-10] LOSSY_PROSE_ROUNDTRIP", rendered)
    self.assertIn("SOCRATIC PROBE:", rendered)
    self.assertIn(probe, rendered)

    out_json = io.StringIO()
    json_code = emit_reflex(
        scar_id="ARCH-10",
        code="LOSSY_PROSE_ROUNDTRIP",
        trigger="Scraped Markdown prose for structured JSON ticket fields.",
        rejected="Using regex parsers over prose instead of persisted JSON.",
        reflex="Read structured ticket fields from lib/ticket_state.py.",
        output_json=True,
        stream=out_json,
    )
    self.assertEqual(json_code, 2)
    payload = json.loads(out_json.getvalue())
    self.assertEqual(payload["scar_id"], "SCAR-ARCH-10")
    self.assertEqual(payload["short_id"], "ARCH-10")
    self.assertEqual(payload["socratic_probe"], probe)
    self.assertEqual(payload["exit_code"], 2)

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
      loaded_scars_path = os.path.join(state_dir, "loaded_scars.json")
      with open(loaded_scars_path, "w", encoding="utf-8") as f:
        f.write(
            json.dumps({"ticketId": "GH-171", "loadedIds": ["SCAR-ARCH-05"]})
        )

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
      self.assertFalse(os.path.exists(loaded_scars_path))

      err_unblocked = io.StringIO()
      code_unblocked = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_unblocked,
      )
      self.assertEqual(code_unblocked, 0, err_unblocked.getvalue())

  def test_stale_active_subagent_json_cleaned_on_intake_release_and_finish(
      self,
  ):
    """Verifies stale active_subagent.json is unlinked cleanly across intake, release-intake, and finish (GH-220)."""
    from lib.state_machine import write_active_target

    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as lockdir:
      _init_git_repo(tmpdir)
      _exclude_workflow_dirs(tmpdir)
      exclude_path = os.path.join(tmpdir, ".git", "info", "exclude")
      with open(exclude_path, "a", encoding="utf-8") as f:
        f.write(".workflow/config.json\n")

      cfg_dir = os.path.join(tmpdir, ".workflow")
      os.makedirs(cfg_dir, exist_ok=True)
      with open(
          os.path.join(cfg_dir, "config.json"), "w", encoding="utf-8"
      ) as f:
        f.write(json.dumps({"state": {"completedCardAction": "archive"}}))

      state_dir = os.path.join(cfg_dir, "state")
      os.makedirs(state_dir, exist_ok=True)
      lock_path = os.path.join(state_dir, "active_subagent.json")

      # 1a. When a prior 10_ticket.md card still exists, intake fails and preserves active_subagent.json
      with open(
          os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Active ticket\n")
      with open(lock_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"typeName": "archaeology-worker"}))
      code_blocked = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(code_blocked, 2)
      self.assertTrue(os.path.isfile(lock_path))

      # 1b. Clean intake (zero leftover 10_..80_ cards) unlinks stale active_subagent.json
      os.unlink(os.path.join(state_dir, "10_ticket.md"))
      err_intake = io.StringIO()
      code_intake = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "intake"],
          stdout=io.StringIO(),
          stderr=err_intake,
      )
      self.assertEqual(code_intake, 0, err_intake.getvalue())
      self.assertFalse(os.path.exists(lock_path))

      # 2. release-intake unlinks active_subagent.json (with or without active ticket) without archiving it
      for with_active_ticket in (True, False):
        if with_active_ticket:
          with open(
              os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
          ) as f:
            f.write("# Active ticket\n")
        elif os.path.exists(os.path.join(state_dir, "10_ticket.md")):
          os.unlink(os.path.join(state_dir, "10_ticket.md"))

        with open(lock_path, "w", encoding="utf-8") as f:
          f.write(json.dumps({"typeName": "tdd-worker"}))
        err_rel = io.StringIO()
        code_rel = WorkflowGatesScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "--lock-dir",
                lockdir,
                "release-intake",
                "--ticket",
                "GH-220",
            ],
            stdout=io.StringIO(),
            stderr=err_rel,
        )
        self.assertEqual(code_rel, 0, err_rel.getvalue())
        self.assertFalse(os.path.exists(lock_path))
        self.assertFalse(
            os.path.exists(
                os.path.join(cfg_dir, "archive", "GH-220", "active_subagent.json")
            )
        )

      if os.path.exists(os.path.join(state_dir, "10_ticket.md")):
        os.unlink(os.path.join(state_dir, "10_ticket.md"))

      # 3. finish (_finalize_completed_ticket) unlinks active_subagent.json
      with open(
          os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Ticket\n\n- **Ticket ID**: `GH-220`\n")
      write_active_target(
          state_dir,
          {
              "previousTarget": "T80",
              "target": "T90",
              "targetName": "codified-scars-consolidated",
          },
      )
      with open(lock_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"typeName": "triage-worker"}))

      err_fin = io.StringIO()
      code_fin = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "advance",
              "--from",
              "T90",
              "--to",
              "T90",
              "--complete",
              "--no-fetch",
          ],
          stdout=io.StringIO(),
          stderr=err_fin,
      )
      self.assertEqual(code_fin, 0, err_fin.getvalue())
      self.assertFalse(os.path.exists(lock_path))

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
      loaded_scars_path = os.path.join(state_dir, "loaded_scars.json")
      loaded_scars_payload = json.dumps(
          {"ticketId": "GH-171", "loadedIds": ["SCAR-ARCH-05"]}, indent=2
      )
      with open(loaded_scars_path, "w", encoding="utf-8") as f:
        f.write(loaded_scars_payload)

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
      self.assertFalse(os.path.exists(loaded_scars_path))
      archived_path = os.path.join(
          cfg_dir, "archive", "GH-171", "30_tdd_receipt.json"
      )
      self.assertTrue(os.path.isfile(archived_path))
      with open(archived_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), receipt_payload)
      archived_loaded_path = os.path.join(
          cfg_dir, "archive", "GH-171", "loaded_scars.json"
      )
      self.assertTrue(os.path.isfile(archived_loaded_path))
      with open(archived_loaded_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), loaded_scars_payload)

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

  def test_main_sync_gate_and_inspection(self):
    import hashlib
    from lib.workflow_gates import (
        fetch_and_inspect_main_sync,
        generate_state_makefile,
        inspect_main_sync,
    )

    with tempfile.TemporaryDirectory() as base_dir:
      # 0. Verify generate_state_makefile includes check-main-sync
      mk_text = generate_state_makefile(base_dir)
      self.assertIn("check-main-sync", mk_text)
      self.assertIn("check-main-sync:", mk_text)

      # 1. NO_REMOTE: repo without origin remote
      no_rem_dir = os.path.join(base_dir, "no_remote")
      os.makedirs(no_rem_dir, exist_ok=True)
      _init_git_repo(no_rem_dir)
      _exclude_workflow_dirs(no_rem_dir)

      no_rem_sync = inspect_main_sync(no_rem_dir)
      self.assertEqual(
          no_rem_sync,
          {
              "status": "NO_REMOTE",
              "upstream_ref": None,
              "ahead_count": 0,
              "behind_count": 0,
              "has_conflicts": False,
              "conflicting_files": [],
              "working_tree_dirty": False,
          },
      )
      out_no_rem = io.StringIO()
      err_no_rem = io.StringIO()
      self.assertEqual(
          WorkflowGatesScript().execute(
              args=["--repo-root", no_rem_dir, "main-sync"],
              stdout=out_no_rem,
              stderr=err_no_rem,
          ),
          0,
          err_no_rem.getvalue(),
      )

      # 2. UP_TO_DATE: local bare origin remote with matching main
      bare_dir = os.path.join(base_dir, "origin.git")
      os.makedirs(bare_dir, exist_ok=True)
      subprocess.run(
          ["git", "init", "--bare", "-q"],
          cwd=bare_dir,
          check=True,
          capture_output=True,
          text=True,
      )

      clone1 = os.path.join(base_dir, "clone1")
      os.makedirs(clone1, exist_ok=True)
      _init_git_repo(clone1)
      _exclude_workflow_dirs(clone1)
      subprocess.run(
          ["git", "branch", "-M", "main"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(os.path.join(clone1, "shared.txt"), "w", encoding="utf-8") as f:
        f.write("line 1\n")
      subprocess.run(
          ["git", "add", "shared.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: add shared.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "remote", "add", "origin", bare_dir],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "-u", "origin", "main"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/195-sync-test"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )

      up_sync = inspect_main_sync(clone1)
      self.assertEqual(up_sync["status"], "UP_TO_DATE")
      self.assertEqual(up_sync["upstream_ref"], "origin/main")
      self.assertEqual(up_sync["ahead_count"], 0)
      self.assertEqual(up_sync["behind_count"], 0)
      self.assertFalse(up_sync["has_conflicts"])
      self.assertEqual(up_sync["conflicting_files"], [])
      self.assertFalse(up_sync["working_tree_dirty"])

      # 3. BEHIND_CLEAN: clone2 pushes a non-conflicting commit to origin/main
      clone2 = os.path.join(base_dir, "clone2")
      subprocess.run(
          ["git", "clone", "-q", "-b", "main", bare_dir, clone2],
          cwd=base_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "config", "user.email", "clone2@example.com"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "config", "user.name", "Clone Two"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(os.path.join(clone2, "other.txt"), "w", encoding="utf-8") as f:
        f.write("non-conflicting upstream change\n")
      subprocess.run(
          ["git", "add", "other.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: add other.txt on main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )

      # Before fetch, inspect_main_sync still sees UP_TO_DATE
      self.assertEqual(inspect_main_sync(clone1)["status"], "UP_TO_DATE")

      # fetch_and_inspect_main_sync fetches origin and sees BEHIND_CLEAN
      behind_sync = fetch_and_inspect_main_sync(clone1)
      self.assertEqual(behind_sync["status"], "BEHIND_CLEAN")
      self.assertEqual(behind_sync["behind_count"], 1)
      self.assertFalse(behind_sync["has_conflicts"])
      self.assertEqual(behind_sync["conflicting_files"], [])

      out_behind = io.StringIO()
      err_behind = io.StringIO()
      code_behind = WorkflowGatesScript().execute(
          args=["--repo-root", clone1, "main-sync", "--no-fetch"],
          stdout=out_behind,
          stderr=err_behind,
      )
      self.assertEqual(code_behind, 0, err_behind.getvalue())
      self.assertIn("MAIN_SYNC_NOTICE", out_behind.getvalue())

      out_fail_behind = io.StringIO()
      err_fail_behind = io.StringIO()
      code_fail_behind = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              clone1,
              "main-sync",
              "--no-fetch",
              "--fail-on-behind",
          ],
          stdout=out_fail_behind,
          stderr=err_fail_behind,
      )
      self.assertEqual(code_fail_behind, 2)
      self.assertIn(
          "[SCAR-ID: GIT-20] UPSTREAM_MAIN_BEHIND", err_fail_behind.getvalue()
      )

      # 4. CONFLICT (uncommitted working tree at T30/T40):
      # clone2 pushes a conflicting change to shared.txt on origin/main
      with open(os.path.join(clone2, "shared.txt"), "w", encoding="utf-8") as f:
        f.write("upstream conflicting line 1\n")
      subprocess.run(
          ["git", "add", "shared.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: update shared.txt on main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )

      # In clone1, modify shared.txt in the working tree WITHOUT staging or committing
      with open(os.path.join(clone1, "shared.txt"), "w", encoding="utf-8") as f:
        f.write("local uncommitted conflicting line 1\n")
      state_dir = os.path.join(clone1, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      tree_path = os.path.join(state_dir, "approved_tree.txt")
      with open(tree_path, "w", encoding="utf-8") as f:
        f.write("locked-tree-sentinel\n")
      index_path = os.path.join(clone1, ".git", "index")
      with open(index_path, "rb") as f:
        index_sha_before = hashlib.sha256(f.read()).hexdigest()

      wt_conflict_sync = fetch_and_inspect_main_sync(clone1)
      self.assertEqual(wt_conflict_sync["status"], "CONFLICT")
      self.assertEqual(wt_conflict_sync["behind_count"], 2)
      self.assertTrue(wt_conflict_sync["has_conflicts"])
      self.assertIn("shared.txt", wt_conflict_sync["conflicting_files"])
      self.assertTrue(wt_conflict_sync["working_tree_dirty"])

      # Verify .git/index and approved_tree.txt were NOT mutated by ephemeral index check
      with open(index_path, "rb") as f:
        index_sha_after = hashlib.sha256(f.read()).hexdigest()
      self.assertEqual(index_sha_before, index_sha_after)
      with open(tree_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), "locked-tree-sentinel\n")

      # 5. CONFLICT (committed on feature branch):
      subprocess.run(
          ["git", "add", "shared.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: local shared.txt change"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      head_before = subprocess.run(
          ["git", "rev-parse", "HEAD"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      ).stdout.strip()
      with open(index_path, "rb") as f:
        committed_index_sha = hashlib.sha256(f.read()).hexdigest()

      out_conf = io.StringIO()
      err_conf = io.StringIO()
      code_conf = WorkflowGatesScript().execute(
          args=["--repo-root", clone1, "main-sync", "--no-fetch"],
          stdout=out_conf,
          stderr=err_conf,
      )
      self.assertEqual(code_conf, 2)
      self.assertIn(
          "[SCAR-ID: GIT-20] UPSTREAM_MAIN_CONFLICT", err_conf.getvalue()
      )
      self.assertIn("shared.txt", err_conf.getvalue())

      head_after = subprocess.run(
          ["git", "rev-parse", "HEAD"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      ).stdout.strip()
      self.assertEqual(head_before, head_after)
      with open(index_path, "rb") as f:
        self.assertEqual(
            committed_index_sha, hashlib.sha256(f.read()).hexdigest()
        )

      # 6. Clean deletions vs true modify/delete conflicts (BLOCKER-1):
      for fname in (
          "to_delete.txt",
          "mod_delete.txt",
          "local_del_conflict.txt",
      ):
        with open(os.path.join(clone2, fname), "w", encoding="utf-8") as f:
          f.write(f"initial {fname}\n")
      subprocess.run(
          [
              "git",
              "add",
              "to_delete.txt",
              "mod_delete.txt",
              "local_del_conflict.txt",
          ],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: seed deletion test files"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "fetch", "-q", "origin", "main"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "checkout", "-q", "-B", "feat/195-delete-sync", "origin/main"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )

      # 6a. Clean upstream deletion on origin/main (removed in remote, our == base)
      subprocess.run(
          ["git", "rm", "-q", "to_delete.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: delete to_delete.txt on main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      clean_remote_del = fetch_and_inspect_main_sync(clone1)
      self.assertEqual(clean_remote_del["status"], "BEHIND_CLEAN")
      self.assertEqual(clean_remote_del["behind_count"], 1)
      self.assertFalse(clean_remote_del["has_conflicts"])
      self.assertEqual(clean_remote_del["conflicting_files"], [])

      # 6b. Clean local deletion on feature branch (removed in local, their == base)
      subprocess.run(
          ["git", "rm", "-q", "shared.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: delete shared.txt locally"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      clean_local_del = inspect_main_sync(clone1)
      self.assertEqual(clean_local_del["status"], "BEHIND_CLEAN")
      self.assertFalse(clean_local_del["has_conflicts"])
      self.assertEqual(clean_local_del["conflicting_files"], [])

      # 6c. True modify/delete conflict: remote deletes mod_delete.txt, local modifies it
      subprocess.run(
          ["git", "rm", "-q", "mod_delete.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: delete mod_delete.txt on main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(os.path.join(clone1, "mod_delete.txt"), "w", encoding="utf-8") as f:
        f.write("local modification conflicting with remote delete\n")
      subprocess.run(
          ["git", "add", "mod_delete.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: modify mod_delete.txt locally"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      mod_del_sync = fetch_and_inspect_main_sync(clone1)
      self.assertEqual(mod_del_sync["status"], "CONFLICT")
      self.assertTrue(mod_del_sync["has_conflicts"])
      self.assertEqual(mod_del_sync["conflicting_files"], ["mod_delete.txt"])

      # 6d. True delete/modify conflict: local deletes local_del_conflict.txt, remote modifies it
      subprocess.run(
          [
              "git",
              "checkout",
              "-q",
              "-B",
              "feat/195-local-del-conflict",
              "origin/main",
          ],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      with open(
          os.path.join(clone2, "local_del_conflict.txt"), "w", encoding="utf-8"
      ) as f:
        f.write("remote modification conflicting with local delete\n")
      subprocess.run(
          ["git", "add", "local_del_conflict.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          [
              "git",
              "commit",
              "-q",
              "-m",
              "feat: modify local_del_conflict.txt on main",
          ],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "rm", "-q", "local_del_conflict.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          [
              "git",
              "commit",
              "-q",
              "-m",
              "chore: delete local_del_conflict.txt locally",
          ],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      del_mod_sync = fetch_and_inspect_main_sync(clone1)
      self.assertEqual(del_mod_sync["status"], "CONFLICT")
      self.assertTrue(del_mod_sync["has_conflicts"])
      self.assertEqual(
          del_mod_sync["conflicting_files"], ["local_del_conflict.txt"]
      )


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

  def test_synthesized_ast_lint_handles_consumer_layouts_src_dir_cwd_and_non_python(self):
    """Synthesized .workflow/state/Makefile ast-lint supports consumer layouts, SRC_DIR, -C, and non-Python repos (GH-245)."""
    clean_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("MAKEFLAGS", "GNUMAKEFLAGS", "MFLAGS", "MAKELEVEL", "SRC_DIR")
    }

    def _write(path: str, content: str) -> None:
      os.makedirs(os.path.dirname(path), exist_ok=True)
      with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    def _init_consumer(repo_dir: str, files: dict[str, str]) -> str:
      os.makedirs(repo_dir, exist_ok=True)
      _init_git_repo(repo_dir)
      for rel, body in files.items():
        _write(os.path.join(repo_dir, rel), body)
      subprocess.run(
          ["git", "add", "."],
          cwd=repo_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: seed consumer files"],
          cwd=repo_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      out_i = io.StringIO()
      err_i = io.StringIO()
      rc = WorkflowGatesScript().execute(
          args=["--repo-root", repo_dir, "init-makefile"],
          stdout=out_i,
          stderr=err_i,
      )
      self.assertEqual(rc, 0, err_i.getvalue())
      return os.path.join(repo_dir, ".workflow", "state", "Makefile")

    # 1. Consumer repo with src/ silent exception fails with exit 2
    with tempfile.TemporaryDirectory() as base_tmp:
      repo_src_bad = os.path.join(base_tmp, "src_bad_repo")
      mk_src_bad = _init_consumer(
          repo_src_bad, {"src/handler.py": "def ok() -> int:\n    return 1\n"}
      )
      _write(
          os.path.join(repo_src_bad, "src", "handler.py"),
          "def bad():\n    try:\n        work()\n    except Exception:\n        pass\n",
      )
      p_src_bad = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              mk_src_bad,
              "ast-lint",
              f"PYTHON={sys.executable}",
          ],
          cwd=repo_src_bad,
          env=clean_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_src_bad.returncode, 2, p_src_bad.stdout + p_src_bad.stderr)
      self.assertIn("SILENT_EXCEPTION_HANDLER", p_src_bad.stderr)

    # 2. Clean app/ + root .py with untracked .venv/ and .workflow/ passes with exit 0
    with tempfile.TemporaryDirectory() as base_tmp:
      repo_clean = os.path.join(base_tmp, "app_root_clean")
      mk_clean = _init_consumer(
          repo_clean,
          {
              "app/views.py": "def view() -> str:\n    return 'ok'\n",
              "manage.py": "def main() -> int:\n    return 0\n",
          },
      )
      _write(
          os.path.join(repo_clean, "app", "views.py"),
          "def view() -> str:\n    return 'updated'\n",
      )
      _write(
          os.path.join(repo_clean, "manage.py"),
          "def main() -> int:\n    return 1\n",
      )
      _write(
          os.path.join(repo_clean, ".venv", "lib", "bad.py"),
          "try:\n    x()\nexcept:\n    pass\n",
      )
      _write(
          os.path.join(repo_clean, ".workflow", "scratch.py"),
          "try:\n    x()\nexcept:\n    pass\n",
      )
      p_clean = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              mk_clean,
              "ast-lint",
              f"PYTHON={sys.executable}",
          ],
          cwd=repo_clean,
          env=clean_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_clean.returncode, 0, p_clean.stderr)
      self.assertIn("scanned 2 files", p_clean.stdout)
      self.assertEqual(p_clean.stderr, "")

    # 3 & 4. SRC_DIR override and -C <other_dir> arbitrary CWD execution
    with tempfile.TemporaryDirectory() as base_tmp:
      repo_override = os.path.join(base_tmp, "override repo")
      other_cwd = os.path.join(base_tmp, "outside_cwd")
      os.makedirs(other_cwd, exist_ok=True)
      mk_override = _init_consumer(
          repo_override,
          {
              "src/clean.py": "def clean() -> int:\n    return 1\n",
              "app/broken.py": "def broken() -> int:\n    return 1\n",
          },
      )
      _write(
          os.path.join(repo_override, "src", "clean.py"),
          "def clean() -> int:\n    return 2\n",
      )
      _write(
          os.path.join(repo_override, "app", "broken.py"),
          "def broken():\n    try:\n        fetch()\n    except Exception:\n        return None\n",
      )

      p_src_only = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "ast-lint",
              "SRC_DIR=src",
              f"PYTHON={sys.executable}",
          ],
          cwd=repo_override,
          env=clean_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_src_only.returncode, 0, p_src_only.stderr)
      self.assertIn("scanned 1 files", p_src_only.stdout)

      p_app_only = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "ast-lint",
              "SRC_DIR=app",
              f"PYTHON={sys.executable}",
          ],
          cwd=repo_override,
          env=clean_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_app_only.returncode, 2)
      self.assertIn("TRIVIAL_FALLBACK_RETURN", p_app_only.stderr)

      # Arbitrary CWD via -C <other_dir> still resolves --repo-root correctly
      p_cwd_default = subprocess.run(
          [
              "make",
              "-s",
              "-C",
              other_cwd,
              "-f",
              mk_override,
              "ast-lint",
              f"PYTHON={sys.executable}",
          ],
          cwd=other_cwd,
          env=clean_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_cwd_default.returncode, 2)
      self.assertIn("TRIVIAL_FALLBACK_RETURN", p_cwd_default.stderr)

      p_cwd_src = subprocess.run(
          [
              "make",
              "-s",
              "-C",
              other_cwd,
              "-f",
              mk_override,
              "ast-lint",
              "SRC_DIR=src",
              f"PYTHON={sys.executable}",
          ],
          cwd=other_cwd,
          env=clean_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_cwd_src.returncode, 0, p_cwd_src.stderr)
      self.assertIn("scanned 1 files", p_cwd_src.stdout)

    # 5. Non-Python repo exits 0
    with tempfile.TemporaryDirectory() as base_tmp:
      repo_non_py = os.path.join(base_tmp, "js_consumer")
      mk_non_py = _init_consumer(
          repo_non_py,
          {"src/index.js": "export const x = 1;\n", "package.json": "{}\n"},
      )
      _write(
          os.path.join(repo_non_py, "src", "index.js"),
          "export const x = 2;\n",
      )
      _write(
          os.path.join(repo_non_py, "node_modules", "dep", "bad.py"),
          "try:\n    x()\nexcept:\n    pass\n",
      )
      p_non_py = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              mk_non_py,
              "ast-lint",
              f"PYTHON={sys.executable}",
          ],
          cwd=repo_non_py,
          env=clean_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_non_py.returncode, 0, p_non_py.stderr)
      self.assertEqual(p_non_py.stderr, "")

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
      self.assertIn("SRC_DIR ?=", synth_mk_text)
      self.assertIn("ast-lint:", synth_mk_text)
      for line in synth_mk_text.splitlines():
        if line.strip().startswith("export "):
          self.assertNotIn("SRC_DIR", line)
      ast_lint_block = synth_mk_text.split("ast-lint:", 1)[1].split("\n\n", 1)[0]
      self.assertIn("--repo-root", ast_lint_block)
      self.assertIn("--strict", ast_lint_block)
      self.assertIn('$(if $(SRC_DIR), --src-dir "$(SRC_DIR)",)', ast_lint_block)
      for legacy_dir in ("lib", "scripts", "mcp", "tests"):
        self.assertNotIn(f"--src-dir {legacy_dir} --strict", synth_mk_text)

      # 1. init-makefile auto-populates .git/info/exclude so check-intake exits 0 immediately
      p_intake_auto = subprocess.run(
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
      self.assertEqual(p_intake_auto.returncode, 0, p_intake_auto.stderr)

      # Clearing .git/info/exclude still triggers UNCLEAN_INTAKE_STATE (exit 2)
      excl_file = os.path.join(consumer_dir, ".git", "info", "exclude")
      with open(excl_file, "w", encoding="utf-8") as f:
        f.write("")
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

      # Re-exclude workflow dirs -> check-intake exits 0
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
        "hooks.json": '{"makeflow-guard": {}}\n',
        "mcp_config.json": '{"mcpServers": {}}\n',
        "default_config.json": '{"models": {}}\n',
        "LICENSE": "MIT\n",
        "README.md": "# Makeflow\n",
        "assets/logo.svg": "<svg></svg>\n",
        "agents/tdd-worker.md": "---\nname: tdd-worker\n---\n",
        "lib/__init__.py": '"""Lib package."""\n',
        "mcp/server.py": '"""MCP server."""\n',
        "rules/AGENTS.md": "# Rules\n",
        "skills/makeflow/SKILL.md": "# Makeflow Skill\n",
        "scripts/session_start.py": "#!/usr/bin/env python3\n",
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
    ".workflow",
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
        "hooks.json",
        "mcp_config.json",
        "default_config.json",
        "LICENSE",
        "README.md",
        "assets/logo.svg",
        "agents/tdd-worker.md",
        "lib/__init__.py",
        "mcp/server.py",
        "rules/AGENTS.md",
        "skills/makeflow/SKILL.md",
        "scripts/session_start.py",
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
        ".workflow",
        ".workflow/config.json.example",
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
