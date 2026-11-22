#!/usr/bin/env python3
"""Hermetic unit and boundary tests for Target 30 full regression gate (GH-233)."""

import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

PRE_TOOL_SCRIPT = os.path.join(REPO_ROOT, "scripts", "pre_tool_guard.py")


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
  """Runs git in repo and returns the CompletedProcess."""
  return subprocess.run(
      ["git", *args],
      cwd=repo,
      capture_output=True,
      text=True,
      check=False,
  )


def _write_file(path: str, content: str) -> None:
  """Writes UTF-8 text to path, creating parent directories as needed."""
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w", encoding="utf-8") as f:
    f.write(content)


def _init_git_repo(repo: str, branch: str = "feat/233-test") -> None:
  """Initializes a hermetic Git repository with an initial commit."""
  _git(repo, "init", "-q", "-b", "main")
  _git(repo, "config", "user.name", "Test User")
  _git(repo, "config", "user.email", "test@example.com")
  _write_file(os.path.join(repo, "README.md"), "# Temp Repo\n")
  _git(repo, "add", "-A")
  _git(repo, "commit", "-q", "-m", "chore: initial commit")
  if branch and branch != "main":
    _git(repo, "checkout", "-q", "-b", branch)


def _lock_approved_tree(repo: str) -> str:
  """Stages all changes and writes git write-tree to approved_tree.txt."""
  _git(repo, "add", "-A")
  wt = _git(repo, "write-tree")
  tree_hash = (wt.stdout or "").strip()
  _write_file(
      os.path.join(repo, ".workflow", "state", "approved_tree.txt"),
      tree_hash + "\n",
  )
  return tree_hash


def _run_make(repo: str, *args: str) -> subprocess.CompletedProcess:
  """Runs GNU Make against .workflow/state/Makefile using current Python."""
  return subprocess.run(
      [
          "make",
          "-s",
          "-f",
          os.path.join(".workflow", "state", "Makefile"),
          f"PYTHON={sys.executable}",
          *args,
      ],
      cwd=repo,
      capture_output=True,
      text=True,
      check=False,
  )


def _load_pre_tool_guard():
  """Loads scripts/pre_tool_guard.py as a module without polluting sys.modules."""
  spec = importlib.util.spec_from_file_location(
      "pre_tool_guard_gh233", PRE_TOOL_SCRIPT
  )
  mod = importlib.util.module_from_spec(spec)
  assert spec.loader is not None
  spec.loader.exec_module(mod)
  return mod


def _seed_two_test_files(repo: str, beta_passes: bool = True) -> None:
  """Seeds a two-file unittest suite and a Makefile with a test: target."""
  _write_file(
      os.path.join(repo, "app.py"),
      "def add(a, b):\n  return a + b\n",
  )
  _write_file(
      os.path.join(repo, "tests", "test_alpha.py"),
      "import unittest\n"
      "from app import add\n\n"
      "class TestAlpha(unittest.TestCase):\n"
      "  def test_add(self):\n"
      "    self.assertEqual(add(1, 2), 3)\n",
  )
  beta_assert = (
      "    self.assertEqual(add(2, 3), 5)\n"
      if beta_passes
      else "    self.assertEqual(add(2, 3), 999)\n"
  )
  _write_file(
      os.path.join(repo, "tests", "test_beta.py"),
      "import unittest\n"
      "from app import add\n\n"
      "class TestBeta(unittest.TestCase):\n"
      "  def test_add_more(self):\n"
      f"{beta_assert}",
  )
  _write_file(
      os.path.join(repo, "Makefile"),
      f"test:\n\t@{sys.executable} -m unittest discover -s tests -p 'test_*.py'\n",
  )


class TestRegressionGateCoreAndMake(unittest.TestCase):
  """Tests Target 30 regression sealing, verification, drift, and tampering."""

  def test_scenario_1_clean_regression_pass_and_index_untouched(self) -> None:
    """Scenario 1: clean suite seals receipt without mutating real .git/index."""
    from lib.regression_gate import (
        DEFAULT_REGRESSION_RECEIPT_REL,
        compute_working_tree_hash,
    )
    from lib.state_machine import read_active_target
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _seed_two_test_files(repo, beta_passes=True)
      _git(repo, "add", "-A")
      _git(repo, "commit", "-q", "-m", "feat: add app and tests")

      # Modify tracked file without staging to confirm .git/index stays untouched.
      _write_file(os.path.join(repo, "app.py"), "def add(a, b):\n  return a + b + 0\n")
      status_before = _git(repo, "status", "--porcelain").stdout

      rc_init = WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(rc_init, 0)

      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")

      # Run verify-regression via GNU Make (auto-resolves `make test`).
      res_reg = _run_make(repo, "verify-regression")
      self.assertEqual(res_reg.returncode, 0, res_reg.stderr + res_reg.stdout)

      # Verify real .git/index was not mutated by compute_working_tree_hash.
      status_after = _git(repo, "status", "--porcelain").stdout
      self.assertEqual(status_before, status_after)

      receipt_path = os.path.join(repo, DEFAULT_REGRESSION_RECEIPT_REL)
      self.assertTrue(os.path.isfile(receipt_path))
      with open(receipt_path, "r", encoding="utf-8") as f:
        receipt = json.load(f)

      self.assertTrue(receipt["verified"])
      self.assertEqual(receipt["exit_code"], 0)
      self.assertEqual(receipt["regression_cmd"], "make test")
      expected_tree = compute_working_tree_hash(repo)
      self.assertEqual(receipt["tree_hash"], expected_tree)

      locked_tree = _lock_approved_tree(repo)
      self.assertEqual(receipt["tree_hash"], locked_tree)

      res_adv = _run_make(repo, "adversarial-review", "NO_FETCH=1")
      self.assertEqual(res_adv.returncode, 0, res_adv.stderr + res_adv.stdout)
      self.assertEqual(read_active_target(state_dir)["target"], "T40")

  def test_one_step_explicit_regression_cmd_via_make_and_mcp(self) -> None:
    """Passing REGRESSION_CMD or MCP evidence.regressionCmd verifies and advances."""
    from lib.state_machine import read_active_target
    from lib.workflow_gates import WorkflowGatesScript
    import mcp.server as mcp_server

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _seed_two_test_files(repo, beta_passes=True)
      os.unlink(os.path.join(repo, "Makefile"))
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")
      _lock_approved_tree(repo)

      cmd = f"{sys.executable} -m unittest discover -s tests -p 'test_*.py'"
      resp = mcp_server.handle_tools_call(
          10,
          "workflow_advance_target",
          {
              "currentTarget": "T30",
              "nextTarget": "T40",
              "workspaceDir": repo,
              "evidence": {
                  "skipMainFetch": True,
                  "regressionCmd": cmd,
                  "subproject": ".",
              },
          },
      )
      self.assertFalse(resp["result"].get("isError"), resp)
      self.assertEqual(read_active_target(state_dir)["target"], "T40")
      receipt_path = os.path.join(state_dir, "30_regression_receipt.json")
      self.assertTrue(os.path.isfile(receipt_path))
      with open(receipt_path, "r", encoding="utf-8") as f:
        receipt = json.load(f)
      self.assertTrue(receipt["verified"])
      self.assertEqual(receipt["regression_cmd"], cmd)

  def test_scenario_2_cross_file_regression_blocked_and_stale_unlinked(self) -> None:
    """Scenario 2: failing second test file blocks verify-regression and T30 -> T40."""
    from lib.regression_gate import DEFAULT_REGRESSION_RECEIPT_REL
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _seed_two_test_files(repo, beta_passes=True)
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")

      # First seal a passing receipt so we can prove stale receipt removal.
      res_ok = _run_make(repo, "verify-regression")
      self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
      receipt_path = os.path.join(repo, DEFAULT_REGRESSION_RECEIPT_REL)
      self.assertTrue(os.path.isfile(receipt_path))

      # Introduce cross-file regression in test_beta.py while test_alpha.py passes.
      _seed_two_test_files(repo, beta_passes=False)
      res_fail = _run_make(repo, "verify-regression")
      self.assertEqual(res_fail.returncode, 2)
      self.assertIn("REGRESSION_SUITE_FAILED", res_fail.stderr)
      self.assertIn("SCAR-ID: TEST-12", res_fail.stderr)
      self.assertFalse(os.path.isfile(receipt_path))

      _lock_approved_tree(repo)
      res_adv = _run_make(repo, "adversarial-review", "NO_FETCH=1")
      self.assertEqual(res_adv.returncode, 2)
      self.assertIn("MISSING_REGRESSION_RECEIPT", res_adv.stderr)

  def test_scenario_3_post_verification_code_drift_rejected(self) -> None:
    """Scenario 3: source edit after verify-regression fails with REGRESSION_TREE_DRIFT."""
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _seed_two_test_files(repo, beta_passes=True)
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")

      res_ok = _run_make(repo, "verify-regression")
      self.assertEqual(res_ok.returncode, 0, res_ok.stderr)

      # Mutate production file after regression verification and re-lock approved_tree.
      _write_file(os.path.join(repo, "app.py"), "def add(a, b):\n  return a - b\n")
      _lock_approved_tree(repo)

      res_chk = _run_make(repo, "check-regression")
      self.assertEqual(res_chk.returncode, 2)
      self.assertIn("REGRESSION_TREE_DRIFT", res_chk.stderr)

      res_adv = _run_make(repo, "adversarial-review", "NO_FETCH=1")
      self.assertEqual(res_adv.returncode, 2)
      self.assertIn("REGRESSION_TREE_DRIFT", res_adv.stderr)

  def test_scenario_4_tampered_or_forged_receipt_rejected(self) -> None:
    """Scenario 4: altering sealed fields, seal, toolchain, or Makefile fails."""
    from lib.regression_gate import DEFAULT_REGRESSION_RECEIPT_REL
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _seed_two_test_files(repo, beta_passes=True)
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      res_ok = _run_make(repo, "verify-regression")
      self.assertEqual(res_ok.returncode, 0, res_ok.stderr)

      receipt_path = os.path.join(repo, DEFAULT_REGRESSION_RECEIPT_REL)
      with open(receipt_path, "r", encoding="utf-8") as f:
        clean_receipt = json.load(f)

      mutations = [
          ("exit_code", 1),
          ("regression_cmd", "true"),
          ("tree_hash", "0" * 40),
          ("receipt_seal", "f" * 64),
          ("verified", False),
          ("toolchain_hash", "a" * 64),
      ]
      for field, bad_val in mutations:
        mutated = dict(clean_receipt)
        mutated[field] = bad_val
        _write_file(receipt_path, json.dumps(mutated))
        err_buf = io.StringIO()
        rc = WorkflowGatesScript().execute(
            args=["--repo-root", repo, "check-regression"],
            stdout=io.StringIO(),
            stderr=err_buf,
        )
        self.assertEqual(rc, 2, f"Mutation of {field} should exit 2")
        self.assertIn(
            "REGRESSION_RECEIPT_TAMPERED",
            err_buf.getvalue(),
            f"Mutation of {field} should report REGRESSION_RECEIPT_TAMPERED",
        )

      # Corrupt JSON payload
      _write_file(receipt_path, "{broken json")
      err_corrupt = io.StringIO()
      rc_corrupt = WorkflowGatesScript().execute(
          args=["--repo-root", repo, "check-regression"],
          stdout=io.StringIO(),
          stderr=err_corrupt,
      )
      self.assertEqual(rc_corrupt, 2)
      self.assertIn("REGRESSION_RECEIPT_TAMPERED", err_corrupt.getvalue())

      # Restore valid receipt and tamper .workflow/state/Makefile
      _write_file(receipt_path, json.dumps(clean_receipt))
      mk_path = os.path.join(repo, ".workflow", "state", "Makefile")
      with open(mk_path, "a", encoding="utf-8") as f:
        f.write("\n# tampered\n")
      err_mk = io.StringIO()
      rc_mk = WorkflowGatesScript().execute(
          args=["--repo-root", repo, "check-regression"],
          stdout=io.StringIO(),
          stderr=err_mk,
      )
      self.assertEqual(rc_mk, 2)
      self.assertIn("REGRESSION_RECEIPT_TAMPERED", err_mk.getvalue())

  def test_scenario_5_workspace_mutation_timeout_and_subproject_escape(self) -> None:
    """Scenario 5: test workspace mutation, timeout, and subproject escape rejected."""
    from lib.regression_gate import (
        DEFAULT_REGRESSION_RECEIPT_REL,
        resolve_regression_command,
        run_verify_regression,
    )
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _seed_two_test_files(repo, beta_passes=True)
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      receipt_path = os.path.join(repo, DEFAULT_REGRESSION_RECEIPT_REL)
      res_ok = _run_make(repo, "verify-regression")
      self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
      self.assertTrue(os.path.isfile(receipt_path))

      # Non-hermetic command that writes an untracked file into repo
      mutating_cmd = (
          f"{sys.executable} -c "
          "\"open('leaked_artifact.txt', 'w').write('dirty')\""
      )
      res_mut = _run_make(
          repo, "verify-regression", f"REGRESSION_CMD={mutating_cmd}"
      )
      self.assertEqual(res_mut.returncode, 2)
      self.assertIn("REGRESSION_WORKSPACE_MUTATED", res_mut.stderr)
      self.assertFalse(os.path.isfile(receipt_path))

      # Clean leaked file and test timeout handling
      os.unlink(os.path.join(repo, "leaked_artifact.txt"))
      ok_to, payload_to = run_verify_regression(
          repo,
          explicit_cmd=f"{sys.executable} -c \"import time; time.sleep(5)\"",
          timeout_seconds=0.2,
      )
      self.assertFalse(ok_to)
      self.assertEqual(payload_to["code"], "REGRESSION_SUITE_FAILED")
      self.assertFalse(os.path.isfile(receipt_path))

      # Subproject traversal rejection
      with self.assertRaises(ValueError):
        resolve_regression_command(repo, subproject="../outside", explicit_cmd="true")

      # Symlink escape rejection
      with tempfile.TemporaryDirectory() as outside:
        link_path = os.path.join(repo, "linked_sub")
        os.symlink(outside, link_path)
        with self.assertRaises(ValueError):
          resolve_regression_command(
              repo, subproject="linked_sub", explicit_cmd="true"
          )


class TestRegressionGateSecurityConfigAndLifecycle(unittest.TestCase):
  """Tests PreToolUse protection, config normalization, and cleanup/archive."""

  def test_scenario_6_direct_agent_write_denied(self) -> None:
    """Scenario 6: PreToolUse blocks direct writes to receipt and regression_gate.py."""
    guard = _load_pre_tool_guard()

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      receipt_target = os.path.join(state_dir, "30_regression_receipt.json")

      for tool_name in ("write_file", "edit_file"):
        res = guard.evaluate_tool_call({
            "workspacePaths": [ws],
            "toolCall": {"name": tool_name, "args": {"Path": receipt_target}},
        })
        self.assertEqual(res["decision"], "deny", f"{tool_name} must be denied")

      for shell_cmd in (
          "echo '{}' > .workflow/state/30_regression_receipt.json",
          "rm -f .workflow/state/30_regression_receipt.json",
          "echo '# hack' > lib/regression_gate.py",
      ):
        res_sh = guard.evaluate_tool_call({
            "workspacePaths": [ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": shell_cmd},
            },
        })
        self.assertEqual(
            res_sh["decision"], "deny", f"Shell '{shell_cmd}' must be denied"
        )

      # Direct write_file to lib/regression_gate.py in a makeflow repo without active ticket
      fake_plugin = os.path.join(ws, "makeflow_checkout")
      os.makedirs(os.path.join(fake_plugin, ".git"), exist_ok=True)
      _write_file(
          os.path.join(fake_plugin, "plugin.json"),
          json.dumps({"name": "makeflow", "version": "1.0.0"}),
      )
      res_lib = guard.evaluate_tool_call({
          "workspacePaths": [fake_plugin],
          "toolCall": {
              "name": "write_file",
              "args": {
                  "Path": os.path.join(fake_plugin, "lib", "regression_gate.py")
              },
          },
      })
      self.assertEqual(res_lib["decision"], "deny")

  def test_scenario_7_config_defaults_and_normalization(self) -> None:
    """Scenario 7: default_config.json and resolve_workflow_config normalize fields."""
    from lib.config import resolve_workflow_config
    from lib.regression_gate import resolve_regression_command

    with tempfile.TemporaryDirectory() as ws:
      cfg_default = resolve_workflow_config(workspace_dir=ws)
      self.assertIs(cfg_default["review"]["allowCriticHermeticTests"], False)
      self.assertEqual(cfg_default["testing"]["regressionCommand"], "")
      self.assertEqual(cfg_default["testing"]["regressionTimeoutSeconds"], 300)

      # Valid workspace overrides
      _write_file(
          os.path.join(ws, ".workflow", "config.json"),
          json.dumps({
              "review": {"allowCriticHermeticTests": True},
              "testing": {
                  "regressionCommand": "pytest -q",
                  "regressionTimeoutSeconds": 45.5,
              },
          }),
      )
      cfg_override = resolve_workflow_config(workspace_dir=ws)
      self.assertIs(cfg_override["review"]["allowCriticHermeticTests"], True)
      self.assertEqual(cfg_override["testing"]["regressionCommand"], "pytest -q")
      self.assertEqual(cfg_override["testing"]["regressionTimeoutSeconds"], 45.5)

      # Verify resolution priority: explicit > config > Makefile test:
      _write_file(os.path.join(ws, "Makefile"), "test:\n\t@true\n")
      _, _, cmd_cfg = resolve_regression_command(ws)
      self.assertEqual(cmd_cfg, "pytest -q")
      _, _, cmd_exp = resolve_regression_command(ws, explicit_cmd="make custom")
      self.assertEqual(cmd_exp, "make custom")

      # Invalid workspace overrides fall back safely
      _write_file(
          os.path.join(ws, ".workflow", "config.json"),
          json.dumps({
              "review": {"allowCriticHermeticTests": "yes"},
              "testing": {
                  "regressionCommand": 12345,
                  "regressionTimeoutSeconds": -10,
              },
          }),
      )
      cfg_bad = resolve_workflow_config(workspace_dir=ws)
      self.assertIs(cfg_bad["review"]["allowCriticHermeticTests"], False)
      self.assertEqual(cfg_bad["testing"]["regressionCommand"], "")
      self.assertEqual(cfg_bad["testing"]["regressionTimeoutSeconds"], 300)

      _write_file(
          os.path.join(ws, ".workflow", "config.json"),
          json.dumps({"testing": {"regressionTimeoutSeconds": True}}),
      )
      cfg_bool = resolve_workflow_config(workspace_dir=ws)
      self.assertEqual(cfg_bool["testing"]["regressionTimeoutSeconds"], 300)

  def test_scenario_8_cleanup_and_archive_lifecycle(self) -> None:
    """Scenario 8: release-intake and finish unlink or archive 30_regression_receipt.json."""
    from lib.regression_gate import DEFAULT_REGRESSION_RECEIPT_REL
    from lib.state_machine import advance_workflow_target
    from lib.workflow_gates import WorkflowGatesScript

    for action in ("unlink", "archive"):
      # A. release-intake path
      with tempfile.TemporaryDirectory() as repo:
        _init_git_repo(repo)
        _write_file(
            os.path.join(repo, ".workflow", "config.json"),
            json.dumps({"state": {"completedCardAction": action}}),
        )
        receipt_path = os.path.join(repo, DEFAULT_REGRESSION_RECEIPT_REL)
        _write_file(receipt_path, json.dumps({"verified": True}) + "\n")

        rc = WorkflowGatesScript().execute(
            args=[
                "--repo-root",
                repo,
                "--lock-dir",
                os.path.join(repo, ".locks"),
                "release-intake",
                "--ticket",
                "GH-233",
            ],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.isfile(receipt_path))
        archived = os.path.join(
            repo, ".workflow", "archive", "GH-233", "30_regression_receipt.json"
        )
        if action == "archive":
          self.assertTrue(os.path.isfile(archived))
        else:
          self.assertFalse(os.path.isfile(archived))

      # B. finish (T80 -> T90 complete) path
      with tempfile.TemporaryDirectory() as repo:
        _init_git_repo(repo)
        _write_file(
            os.path.join(repo, ".workflow", "config.json"),
            json.dumps({"state": {"completedCardAction": action}}),
        )
        state_dir = os.path.join(repo, ".workflow", "state")
        _write_file(
            os.path.join(state_dir, "10_ticket.md"),
            "# 10_ticket: GH-233\n- **Ticket ID**: `GH-233`\n",
        )
        receipt_path = os.path.join(repo, DEFAULT_REGRESSION_RECEIPT_REL)
        _write_file(receipt_path, json.dumps({"verified": True}) + "\n")

        ok, msg = advance_workflow_target(
            "T80",
            "T90",
            evidence={"complete": True, "skipMainFetch": True},
            workspace_dir=repo,
            state_dir=state_dir,
        )
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.isfile(receipt_path))
        archived = os.path.join(
            repo, ".workflow", "archive", "GH-233", "30_regression_receipt.json"
        )
        if action == "archive":
          self.assertTrue(os.path.isfile(archived))
        else:
          self.assertFalse(os.path.isfile(archived))


class TestRegressionGateRemediationBlockers(unittest.TestCase):
  """Regression tests for Target 40 Round 1 blockers and NBOs #1 and #4."""

  def test_blocker1_mcp_make_advance_timeout_and_process_group_cleanup(self) -> None:
    """BLOCKER-1: run_makefile_advance aligns timeout and kills child process groups."""
    from lib.state_machine import (
        _resolve_make_advance_timeout,
        run_makefile_advance,
    )
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as ext:
      _init_git_repo(repo)
      _write_file(
          os.path.join(repo, ".workflow", "config.json"),
          json.dumps({"testing": {"regressionTimeoutSeconds": 90}}),
      )
      self.assertEqual(
          _resolve_make_advance_timeout(
              "T40", {"regressionCmd": "make test"}, repo
          ),
          95.0,
      )
      self.assertEqual(
          _resolve_make_advance_timeout("T40", {}, repo),
          30.0,
      )
      self.assertEqual(
          _resolve_make_advance_timeout(
              "T15", {"regressionCmd": "make test"}, repo
          ),
          30.0,
      )

      # Spawn a child subprocess that writes its PID to ext/child.pid and sleeps.
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")
      _lock_approved_tree(repo)

      pid_file = os.path.join(ext, "child.pid")
      spawner = os.path.join(ext, "spawn_orphan.py")
      _write_file(
          spawner,
          "import subprocess, sys, time\n"
          f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
          f"open({pid_file!r}, 'w', encoding='utf-8').write(str(p.pid))\n"
          "p.wait()\n",
      )

      with mock.patch(
          "lib.state_machine._resolve_make_advance_timeout", return_value=0.6
      ):
        ok, msg = run_makefile_advance(
            "T30",
            "T40",
            evidence={
                "skipMainFetch": True,
                "regressionCmd": f"{sys.executable} {spawner}",
            },
            workspace_dir=repo,
            state_dir=state_dir,
        )

      self.assertFalse(ok)
      self.assertIn("BLOCKED:", msg)
      self.assertTrue(os.path.isfile(pid_file), "Child PID file should have been written")
      with open(pid_file, "r", encoding="utf-8") as f:
        child_pid = int(f.read().strip())

      # Wait briefly for OS reaping and assert the child process is dead.
      dead = False
      for _ in range(20):
        try:
          os.kill(child_pid, 0)
        except ProcessLookupError:
          dead = True
          break
        except PermissionError:
          break
        time.sleep(0.05)
      self.assertTrue(dead, f"Orphan child PID {child_pid} survived timeout")

  def test_blocker2_mid_phase_head_drift_rejected(self) -> None:
    """BLOCKER-2: moving Git HEAD without changing tree fails with REGRESSION_HEAD_DRIFT."""
    from lib.regression_gate import verify_regression_receipt
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _seed_two_test_files(repo, beta_passes=True)
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")

      res_ok = _run_make(repo, "verify-regression")
      self.assertEqual(res_ok.returncode, 0, res_ok.stderr)

      # Commit the exact same working tree so tree_hash stays identical while HEAD moves.
      _git(repo, "add", "-A")
      _git(repo, "commit", "-q", "-m", "chore: mid-phase commit")
      _lock_approved_tree(repo)

      ok, payload = verify_regression_receipt(repo)
      self.assertFalse(ok)
      self.assertEqual(payload["code"], "REGRESSION_HEAD_DRIFT")

      res_chk = _run_make(repo, "check-regression")
      self.assertEqual(res_chk.returncode, 2)
      self.assertIn("REGRESSION_HEAD_DRIFT", res_chk.stderr)

      res_adv = _run_make(repo, "adversarial-review", "NO_FETCH=1")
      self.assertEqual(res_adv.returncode, 2)
      self.assertIn("REGRESSION_HEAD_DRIFT", res_adv.stderr)

  def test_blocker3_subproject_validation_dedup_and_clean_json(self) -> None:
    """BLOCKER-3: validates SUBPROJECT, deduplicates absolute paths, and emits clean JSON."""
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as ext:
      _init_git_repo(repo)
      os.makedirs(os.path.join(repo, "backend"), exist_ok=True)
      os.makedirs(os.path.join(repo, "frontend"), exist_ok=True)
      _write_file(os.path.join(repo, "backend", "svc.py"), "X = 1\n")
      _write_file(os.path.join(repo, "frontend", "ui.py"), "Y = 2\n")
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")

      # 1. Seal backend, then check mismatched frontend and escaping subproject.
      res_seal = _run_make(
          repo, "verify-regression", "SUBPROJECT=backend", "REGRESSION_CMD=true"
      )
      self.assertEqual(res_seal.returncode, 0, res_seal.stderr)

      res_mismatch = _run_make(repo, "check-regression", "SUBPROJECT=frontend")
      self.assertEqual(res_mismatch.returncode, 2)
      self.assertIn("REGRESSION_SUBPROJECT_MISMATCH", res_mismatch.stderr)

      res_escape = _run_make(repo, "check-regression", "SUBPROJECT=../escape")
      self.assertEqual(res_escape.returncode, 2)
      self.assertIn("INVALID_SUBPROJECT", res_escape.stderr)

      # Omitting SUBPROJECT still accepts valid sealed backend receipt.
      res_default = _run_make(repo, "check-regression")
      self.assertEqual(res_default.returncode, 0, res_default.stderr)

      # 2. Absolute repo-internal SUBPROJECT must execute regression cmd EXACTLY ONCE.
      counter_file = os.path.join(ext, "run_count.log")
      counter_script = os.path.join(ext, "count_run.py")
      _write_file(
          counter_script,
          f"open({counter_file!r}, 'a', encoding='utf-8').write('run\\n')\n",
      )
      abs_backend = os.path.join(repo, "backend")
      _lock_approved_tree(repo)
      res_adv = _run_make(
          repo,
          "adversarial-review",
          "NO_FETCH=1",
          f"SUBPROJECT={abs_backend}",
          f"REGRESSION_CMD={sys.executable} {counter_script}",
      )
      self.assertEqual(res_adv.returncode, 0, res_adv.stderr + res_adv.stdout)
      with open(counter_file, "r", encoding="utf-8") as f:
        run_lines = [ln for ln in f.read().splitlines() if ln.strip()]
      self.assertEqual(len(run_lines), 1, f"Expected 1 run, got {len(run_lines)}")

      # 3. check-regression --regression-cmd --json emits exactly ONE JSON document.
      out_json = io.StringIO()
      err_json = io.StringIO()
      rc_json = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              repo,
              "check-regression",
              "--regression-cmd",
              "true",
              "--subproject",
              "frontend",
              "--json",
          ],
          stdout=out_json,
          stderr=err_json,
      )
      self.assertEqual(rc_json, 0, err_json.getvalue())
      parsed = json.loads(out_json.getvalue())
      self.assertIsInstance(parsed, dict)
      self.assertEqual(parsed["subproject"], "frontend")

      # 4. advance --to T40 --json must not mix receipt JSON with SUCCESS: output.
      _write_file(
          os.path.join(state_dir, "active_target.json"),
          json.dumps({"previousTarget": "T20", "target": "T30", "targetName": "implementation-diff"}),
      )
      adv_out = io.StringIO()
      adv_err = io.StringIO()
      rc_adv = WorkflowGatesScript().execute(
          args=["--repo-root", repo, "advance", "--to", "T40", "--no-fetch", "--json"],
          stdout=adv_out,
          stderr=adv_err,
      )
      self.assertEqual(rc_adv, 0, adv_err.getvalue())
      self.assertIn("SUCCESS:", adv_out.getvalue())
      self.assertNotIn("receipt_seal", adv_out.getvalue())

  def test_nbo1_malformed_review_config_preserves_defaults(self) -> None:
    """NBO #1: non-dict review config preserves maxReviewRounds and strictSixPillars."""
    from lib.config import resolve_workflow_config

    with tempfile.TemporaryDirectory() as ws:
      _write_file(
          os.path.join(ws, ".workflow", "config.json"),
          json.dumps({"review": "invalid"}),
      )
      cfg = resolve_workflow_config(workspace_dir=ws)
      self.assertEqual(cfg["review"]["maxReviewRounds"], 3)
      self.assertIs(cfg["review"]["strictSixPillars"], True)
      self.assertIs(cfg["review"]["allowCriticHermeticTests"], False)

  def test_nbo4_missing_regression_command_and_stale_approved_tree(self) -> None:
    """NBO #4: tests MISSING_REGRESSION_COMMAND and stale approved_tree.txt mismatch."""
    from lib.regression_gate import verify_regression_receipt
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )

      # 1. Bare repo with no Makefile test:, no config command, no REGRESSION_CMD
      res_missing = _run_make(repo, "verify-regression")
      self.assertEqual(res_missing.returncode, 2)
      self.assertIn("MISSING_REGRESSION_COMMAND", res_missing.stderr)

      # 2. Valid receipt matching live tree, but approved_tree.txt holds a stale hash
      res_ok = _run_make(repo, "verify-regression", "REGRESSION_CMD=true")
      self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
      _write_file(
          os.path.join(repo, ".workflow", "state", "approved_tree.txt"),
          "1" * 40 + "\n",
      )
      ok, payload = verify_regression_receipt(
          repo, require_approved_tree_match=True
      )
      self.assertFalse(ok)
      self.assertEqual(payload["code"], "REGRESSION_TREE_DRIFT")


class TestMakeSubprocessEnvSanitization(unittest.TestCase):
  """Tests GNU Make recursion and Makeflow export sanitization (GH-258)."""

  _MAKE_RECURSION_VARS = (
      "MAKEFLAGS",
      "MFLAGS",
      "MAKEOVERRIDES",
      "MAKELEVEL",
      "GNUMAKEFLAGS",
  )
  _MAKEFLOW_EXPORTS = (
      "TICKET",
      "ALLOW_BLOCKED",
      "FROM",
      "TO",
      "APPROVED",
      "BLOCKERS",
      "ALLOW_MAIN_CONFLICT",
      "NO_FETCH",
      "TRANSCRIPT",
      "FILE",
      "TEST_DIR",
      "TEST_CMD",
      "ALLOW_HEAD_MOVE",
      "CITE",
      "REGRESSION_CMD",
      "SUBPROJECT",
      "PATH_PREFIX",
      "TREE_LIMIT",
      "NO_LIMIT",
      "COUNT_ONLY",
  )

  def _polluted_vars(self) -> dict:
    """Returns a mapping polluting all 5 Make recursion and 20 Makeflow variables."""
    polluted = {k: f"polluted_{k}" for k in self._MAKE_RECURSION_VARS}
    for k in self._MAKEFLOW_EXPORTS:
      polluted[k] = f"polluted_{k}"
    polluted["MAKEFLAGS"] = "s -- REGRESSION_CMD=make\\ broken TEST_CMD=false"
    polluted["MAKELEVEL"] = "3"
    polluted["MY_APP_ENV"] = "staging"
    polluted["CUSTOM_USER_FLAG"] = "keep-me"
    return polluted

  def test_sanitize_helper_strips_make_and_makeflow_vars_preserving_user_env(
      self,
  ) -> None:
    """sanitize_make_subprocess_env removes all 25 Make/Makeflow vars without mutating source (GH-258)."""
    from lib.config import (
        MAKE_RECURSION_ENV_VARS,
        MAKEFLOW_EXPORTED_ENV_VARS,
        sanitize_make_subprocess_env,
    )

    self.assertEqual(
        set(MAKE_RECURSION_ENV_VARS), set(self._MAKE_RECURSION_VARS)
    )
    self.assertEqual(set(MAKEFLOW_EXPORTED_ENV_VARS), set(self._MAKEFLOW_EXPORTS))

    polluted = self._polluted_vars()
    polluted["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    snapshot = dict(polluted)

    cleaned = sanitize_make_subprocess_env(polluted)
    self.assertIsNot(cleaned, polluted)
    self.assertEqual(polluted, snapshot)
    for var in self._MAKE_RECURSION_VARS + self._MAKEFLOW_EXPORTS:
      self.assertNotIn(var, cleaned)
    self.assertEqual(cleaned["MY_APP_ENV"], "staging")
    self.assertEqual(cleaned["CUSTOM_USER_FLAG"], "keep-me")
    self.assertEqual(cleaned["PATH"], snapshot["PATH"])

    with mock.patch.dict(os.environ, polluted, clear=False):
      cleaned_default = sanitize_make_subprocess_env()
      for var in self._MAKE_RECURSION_VARS + self._MAKEFLOW_EXPORTS:
        self.assertNotIn(var, cleaned_default)
      self.assertEqual(cleaned_default["MY_APP_ENV"], "staging")
      self.assertEqual(os.environ.get("MAKEFLAGS"), polluted["MAKEFLAGS"])

  def test_makefile_export_contract_covers_all_workflow_exports(self) -> None:
    """MAKEFLOW_EXPORTED_ENV_VARS matches every export directive in generate_state_makefile (GH-258)."""
    from lib.config import MAKEFLOW_EXPORTED_ENV_VARS
    from lib.workflow_gates import generate_state_makefile

    makefile_text = generate_state_makefile(REPO_ROOT)
    exported_vars = set()
    for line in makefile_text.splitlines():
      stripped = line.strip()
      if stripped.startswith("export "):
        exported_vars.update(stripped[len("export "):].split())

    self.assertEqual(len(exported_vars), 20)
    self.assertEqual(set(MAKEFLOW_EXPORTED_ENV_VARS), exported_vars)

  def test_regression_shell_strips_make_recursion_and_makeflow_exports(
      self,
  ) -> None:
    """_run_regression_shell strips all 5 Make recursion and 20 Makeflow exports while keeping user vars (GH-258)."""
    from lib.regression_gate import _run_regression_shell

    polluted = self._polluted_vars()
    with mock.patch.dict(os.environ, polluted, clear=False):
      os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
      with mock.patch("lib.regression_gate.subprocess.Popen") as popen_mock:
        dummy_proc = mock.MagicMock()
        dummy_proc.communicate.return_value = ("ok", "")
        dummy_proc.returncode = 0
        dummy_proc.poll.return_value = 0
        dummy_proc.stdout = None
        dummy_proc.stderr = None
        popen_mock.return_value = dummy_proc

        proc, err = _run_regression_shell("true", REPO_ROOT, 5.0)

    self.assertIsNotNone(proc)
    self.assertEqual(err, "")
    passed_env = popen_mock.call_args.kwargs["env"]
    for var in self._MAKE_RECURSION_VARS + self._MAKEFLOW_EXPORTS:
      self.assertNotIn(var, passed_env, f"{var} leaked into child shell env")
    self.assertEqual(passed_env["MY_APP_ENV"], "staging")
    self.assertEqual(passed_env["CUSTOM_USER_FLAG"], "keep-me")
    self.assertEqual(passed_env["PYTHONDONTWRITEBYTECODE"], "1")

  def test_verify_regression_make_test_isolates_nested_mcp_advance(
      self,
  ) -> None:
    """Real GNU Make verify-regression REGRESSION_CMD='make test' isolates nested workflow_advance_target (GH-258)."""
    from lib.state_machine import read_active_target
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmpdir:
      outer_repo = os.path.join(tmpdir, "outer_repo")
      inner_repo = os.path.join(tmpdir, "inner_repo")
      os.makedirs(outer_repo)
      os.makedirs(inner_repo)

      # Seed inner repo without a root Makefile (reproducing GH-258).
      _init_git_repo(inner_repo)
      _seed_two_test_files(inner_repo, beta_passes=True)
      os.unlink(os.path.join(inner_repo, "Makefile"))
      WorkflowGatesScript().execute(
          args=["--repo-root", inner_repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      inner_state_dir = os.path.join(inner_repo, ".workflow", "state")
      _write_file(
          os.path.join(inner_state_dir, "20_plan_approved.md"), "APPROVED\n"
      )
      _lock_approved_tree(inner_repo)
      inner_cmd = (
          f"{shlex.quote(sys.executable)} -m unittest discover"
          " -s tests -p 'test_*.py'"
      )

      # Seed outer repo whose `make test` invokes nested MCP workflow_advance_target.
      _init_git_repo(outer_repo)
      nested_test_code = (
          "import json\n"
          "import os\n"
          "import sys\n"
          "import unittest\n\n"
          f"REPO_ROOT = {repr(REPO_ROOT)}\n"
          "if REPO_ROOT not in sys.path:\n"
          "  sys.path.insert(0, REPO_ROOT)\n\n"
          "from lib.state_machine import read_active_target\n"
          "import mcp.server as mcp_server\n\n"
          "class TestNestedMcpAdvance(unittest.TestCase):\n"
          "  def test_nested_advance_succeeds_under_outer_make(self):\n"
          "    self.assertNotIn('REGRESSION_CMD', os.environ)\n"
          "    self.assertNotIn('REGRESSION_CMD', os.environ.get('MAKEFLAGS', ''))\n"
          "    self.assertNotIn('MAKEOVERRIDES', os.environ)\n"
          "    self.assertNotIn('GNUMAKEFLAGS', os.environ)\n"
          "    self.assertEqual(os.environ.get('MAKELEVEL'), '1')\n"
          f"    inner_repo = {repr(inner_repo)}\n"
          f"    inner_cmd = {repr(inner_cmd)}\n"
          "    resp = mcp_server.handle_tools_call(\n"
          "        10,\n"
          "        'workflow_advance_target',\n"
          "        {\n"
          "            'currentTarget': 'T30',\n"
          "            'nextTarget': 'T40',\n"
          "            'workspaceDir': inner_repo,\n"
          "            'evidence': {\n"
          "                'skipMainFetch': True,\n"
          "                'regressionCmd': inner_cmd,\n"
          "                'subproject': '.',\n"
          "            },\n"
          "        },\n"
          "    )\n"
          "    self.assertFalse(resp['result'].get('isError'), resp)\n"
          "    inner_state = os.path.join(inner_repo, '.workflow', 'state')\n"
          "    self.assertEqual(read_active_target(inner_state)['target'], 'T40')\n"
          "    receipt_path = os.path.join(inner_state, '30_regression_receipt.json')\n"
          "    self.assertTrue(os.path.isfile(receipt_path))\n"
          "    with open(receipt_path, 'r', encoding='utf-8') as f:\n"
          "      receipt = json.load(f)\n"
          "    self.assertTrue(receipt['verified'])\n"
          "    self.assertEqual(receipt['regression_cmd'], inner_cmd)\n\n"
          "if __name__ == '__main__':\n"
          "  unittest.main()\n"
      )
      _write_file(
          os.path.join(outer_repo, "tests", "test_nested_advance.py"),
          nested_test_code,
      )
      _write_file(
          os.path.join(outer_repo, "Makefile"),
          f"test:\n\t@{shlex.quote(sys.executable)} -m unittest discover -s tests -p 'test_*.py'\n",
      )
      _git(outer_repo, "add", "-A")
      _git(outer_repo, "commit", "-q", "-m", "test: add nested MCP advance suite")
      WorkflowGatesScript().execute(
          args=["--repo-root", outer_repo, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )

      polluted_env = dict(os.environ)
      polluted_env.update({
          "MAKELEVEL": "4",
          "GNUMAKEFLAGS": "--no-print-directory",
          "TEST_CMD": "false",
          "APPROVED": "1",
          "TICKET": "GH-258",
          "FROM": "T10",
          "TO": "T15",
          "TREE_LIMIT": "25",
          "SUBPROJECT": ".",
      })
      res = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              os.path.join(".workflow", "state", "Makefile"),
              f"PYTHON={sys.executable}",
              "verify-regression",
              "REGRESSION_CMD=make test",
          ],
          cwd=outer_repo,
          env=polluted_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

      outer_receipt_path = os.path.join(
          outer_repo, ".workflow", "state", "30_regression_receipt.json"
      )
      self.assertTrue(os.path.isfile(outer_receipt_path))
      with open(outer_receipt_path, "r", encoding="utf-8") as f:
        outer_receipt = json.load(f)
      self.assertTrue(outer_receipt["verified"])
      self.assertEqual(outer_receipt["regression_cmd"], "make test")
      self.assertEqual(read_active_target(inner_state_dir)["target"], "T40")


class TestNativeGitIndexPlumbing(unittest.TestCase):
  """Hermetic boundary tests for native Git index plumbing (GH-249, SCAR-GIT-23)."""

  def _file_sha256(self, path: str) -> str:
    """Returns the SHA-256 hex digest of a file's raw bytes."""
    with open(path, "rb") as f:
      return hashlib.sha256(f.read()).hexdigest()

  def _sharedindex_snapshot(self, repo: str) -> dict:
    """Returns a mapping of .git/sharedindex.* filenames to SHA-256 digests."""
    git_dir = os.path.join(repo, ".git")
    snapshot = {}
    for entry in sorted(os.listdir(git_dir)):
      if entry.startswith("sharedindex."):
        snapshot[entry] = self._file_sha256(os.path.join(git_dir, entry))
    return snapshot

  def test_working_tree_hash_detects_racy_git_same_size_rewrite_across_second_rollover(
      self,
  ) -> None:
    """GH-249: Same-size rewrite with matching mtime across second rollover updates tree hash."""
    from lib.regression_gate import compute_working_tree_hash

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _git(repo, "config", "core.trustctime", "false")
      _git(repo, "config", "core.checkstat", "minimal")

      t0 = int(time.time()) - 5
      feature_path = os.path.join(repo, "feature.py")
      _write_file(feature_path, "VALUE = 1\n")
      os.utime(feature_path, (t0, t0))
      _git(repo, "add", "feature.py")
      _git(repo, "commit", "-q", "-m", "feat: add feature")

      # Stamp .git/index to t0 so entry.mtime == index.mtime == t0 (authentic racy-git state).
      index_path = os.path.join(repo, ".git", "index")
      os.utime(index_path, (t0, t0))
      hash_before = compute_working_tree_hash(repo)

      # Overwrite in-place with an identical 10-byte payload at the same timestamp t0.
      _write_file(feature_path, "VALUE = 2\n")
      os.utime(feature_path, (t0, t0))
      index_sha_before = self._file_sha256(index_path)

      hash_after = compute_working_tree_hash(repo)
      self.assertEqual(index_sha_before, self._file_sha256(index_path))

      # Ground truth via real staging must match hash_after and differ from hash_before.
      _git(repo, "add", "-A")
      expected_tree = _git(repo, "write-tree").stdout.strip()
      self.assertNotEqual(hash_after, hash_before)
      self.assertEqual(hash_after, expected_tree)

  def test_working_tree_hash_preserves_force_added_tracked_but_ignored_files(
      self,
  ) -> None:
    """GH-249: Force-added tracked-but-ignored files stay hashed and reflect edits."""
    from lib.regression_gate import compute_working_tree_hash

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _write_file(os.path.join(repo, ".gitignore"), "*.ignored\n")
      _git(repo, "add", ".gitignore")
      _git(repo, "commit", "-q", "-m", "chore: ignore pattern")

      ignored_path = os.path.join(repo, "secret.ignored")
      _write_file(ignored_path, "v1\n")
      _git(repo, "add", "-f", "secret.ignored")
      _git(repo, "commit", "-q", "-m", "chore: force-add ignored file")

      _write_file(ignored_path, "v2\n")
      index_path = os.path.join(repo, ".git", "index")
      status_before = _git(repo, "status", "--porcelain").stdout
      index_sha_before = self._file_sha256(index_path)

      tree_hash = compute_working_tree_hash(repo)

      self.assertEqual(index_sha_before, self._file_sha256(index_path))
      self.assertEqual(status_before, _git(repo, "status", "--porcelain").stdout)
      blob = _git(repo, "cat-file", "-p", f"{tree_hash}:secret.ignored")
      self.assertEqual(blob.returncode, 0, blob.stderr)
      self.assertEqual(blob.stdout, "v2\n")

  def test_working_tree_hash_matches_symlinks_and_executable_bits(self) -> None:
    """GH-249: Symlinks (120000) and executable mode bits (100755) match native git write-tree."""
    from lib.regression_gate import compute_working_tree_hash

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      script_path = os.path.join(repo, "bin", "run.sh")
      _write_file(script_path, "#!/bin/sh\necho v1\n")
      os.chmod(script_path, 0o755)
      link_path = os.path.join(repo, "readme_link")
      os.symlink("README.md", link_path)
      _git(repo, "add", "-A")
      _git(repo, "commit", "-q", "-m", "feat: add executable and symlink")

      # Edit executable and add a second untracked symlink without staging.
      _write_file(script_path, "#!/bin/sh\necho v2\n")
      os.chmod(script_path, 0o755)
      os.symlink("bin/run.sh", os.path.join(repo, "run_link"))

      index_path = os.path.join(repo, ".git", "index")
      index_sha_before = self._file_sha256(index_path)
      tree_hash = compute_working_tree_hash(repo)
      self.assertEqual(index_sha_before, self._file_sha256(index_path))

      _git(repo, "add", "-A")
      expected_tree = _git(repo, "write-tree").stdout.strip()
      self.assertEqual(tree_hash, expected_tree)

      ls_tree = _git(repo, "ls-tree", "-r", tree_hash).stdout
      self.assertIn("100755 blob", ls_tree)
      self.assertIn("120000 blob", ls_tree)

  def test_working_tree_hash_handles_unmerged_conflict_stages_and_intent_to_add(
      self,
  ) -> None:
    """GH-249: Unmerged conflict stages (1/2/3) and git add -N seed cleanly without mutating .git/index."""
    from lib.regression_gate import compute_working_tree_hash

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo, branch="main")
      conflict_path = os.path.join(repo, "conflict.txt")
      _write_file(conflict_path, "base line\n")
      _git(repo, "add", "conflict.txt")
      _git(repo, "commit", "-q", "-m", "chore: seed conflict.txt")

      _git(repo, "checkout", "-q", "-b", "branch-a")
      _write_file(conflict_path, "branch a line\n")
      _git(repo, "add", "conflict.txt")
      _git(repo, "commit", "-q", "-m", "feat: branch-a edit")

      _git(repo, "checkout", "-q", "main")
      _git(repo, "checkout", "-q", "-b", "branch-b")
      _write_file(conflict_path, "branch b line\n")
      _git(repo, "add", "conflict.txt")
      _git(repo, "commit", "-q", "-m", "feat: branch-b edit")

      merge_proc = _git(repo, "merge", "branch-a")
      self.assertNotEqual(merge_proc.returncode, 0)
      unmerged_before = _git(repo, "ls-files", "--unmerged").stdout.strip()
      self.assertTrue(unmerged_before)

      # Also add an intent-to-add entry (git add -N).
      intent_path = os.path.join(repo, "intent.txt")
      _write_file(intent_path, "intent payload\n")
      self.assertEqual(_git(repo, "add", "-N", "intent.txt").returncode, 0)

      index_path = os.path.join(repo, ".git", "index")
      index_sha_before = self._file_sha256(index_path)
      status_before = _git(repo, "status", "--porcelain").stdout

      tree_hash = compute_working_tree_hash(repo)
      self.assertEqual(len(tree_hash), 40)
      self.assertEqual(index_sha_before, self._file_sha256(index_path))
      self.assertEqual(status_before, _git(repo, "status", "--porcelain").stdout)
      self.assertEqual(
          unmerged_before, _git(repo, "ls-files", "--unmerged").stdout.strip()
      )

      intent_blob = _git(repo, "cat-file", "-p", f"{tree_hash}:intent.txt")
      self.assertEqual(intent_blob.returncode, 0, intent_blob.stderr)
      self.assertEqual(intent_blob.stdout, "intent payload\n")

      with open(conflict_path, "r", encoding="utf-8") as f:
        working_conflict = f.read()
      conflict_blob = _git(repo, "cat-file", "-p", f"{tree_hash}:conflict.txt")
      self.assertEqual(conflict_blob.returncode, 0, conflict_blob.stderr)
      self.assertEqual(conflict_blob.stdout, working_conflict)

  def test_working_tree_hash_handles_empty_repository_and_untracked_only_files(
      self,
  ) -> None:
    """GH-249: Empty repo returns empty tree SHA and hashes untracked-only files without creating .git/index."""
    from lib.regression_gate import compute_working_tree_hash

    empty_tree_sha = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    with tempfile.TemporaryDirectory() as repo:
      _git(repo, "init", "-q", "-b", "main")
      _git(repo, "config", "user.name", "Test User")
      _git(repo, "config", "user.email", "test@example.com")
      index_path = os.path.join(repo, ".git", "index")
      self.assertFalse(os.path.exists(index_path))

      self.assertEqual(compute_working_tree_hash(repo), empty_tree_sha)
      self.assertFalse(os.path.exists(index_path))

      _write_file(os.path.join(repo, "untracked.txt"), "hello world\n")
      untracked_hash = compute_working_tree_hash(repo)
      self.assertFalse(os.path.exists(index_path))

      _git(repo, "add", "-A")
      expected_tree = _git(repo, "write-tree").stdout.strip()
      self.assertEqual(untracked_hash, expected_tree)

  def test_working_tree_hash_split_index_creates_no_sharedindex_side_effects(
      self,
  ) -> None:
    """GH-249: core.splitIndex=true hashes accurately with zero .git/index or .git/sharedindex.* side effects."""
    from lib.regression_gate import compute_working_tree_hash

    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      _git(repo, "config", "core.splitIndex", "true")
      _git(repo, "config", "splitIndex.maxPercentChange", "0")
      _write_file(os.path.join(repo, "tracked.txt"), "initial\n")
      _git(repo, "add", "tracked.txt")
      _git(repo, "commit", "-q", "-m", "chore: seed split-index repo")
      self.assertEqual(_git(repo, "update-index", "--split-index").returncode, 0)

      # Verify split index is active before testing.
      shared_before = self._sharedindex_snapshot(repo)
      self.assertTrue(shared_before)

      _write_file(os.path.join(repo, "tracked.txt"), "updated\n")
      _write_file(os.path.join(repo, "new_file.txt"), "untracked\n")
      index_path = os.path.join(repo, ".git", "index")
      index_sha_before = self._file_sha256(index_path)

      tree_hash = compute_working_tree_hash(repo)

      self.assertEqual(index_sha_before, self._file_sha256(index_path))
      self.assertEqual(shared_before, self._sharedindex_snapshot(repo))

      _git(repo, "add", "-A")
      expected_tree = _git(repo, "write-tree").stdout.strip()
      self.assertEqual(tree_hash, expected_tree)

  def test_working_tree_hash_raises_runtime_error_on_invalid_or_non_git_directory(
      self,
  ) -> None:
    """GH-249: Nonexistent or non-Git directories raise RuntimeError."""
    from lib.regression_gate import compute_working_tree_hash

    with tempfile.TemporaryDirectory() as tmp:
      missing_dir = os.path.join(tmp, "does-not-exist")
      with self.assertRaises(RuntimeError):
        compute_working_tree_hash(missing_dir)

      plain_dir = os.path.join(tmp, "plain-non-git")
      os.makedirs(plain_dir)
      with self.assertRaises(RuntimeError):
        compute_working_tree_hash(plain_dir)

  def test_git_index_governance_registration_and_module_hygiene(self) -> None:
    """GH-249 / SCAR-TEST-12 / SCAR-ARCH-08: lib/git_index.py has no shebang and is registered in governance guards."""
    from lib.tdd_guard import CORE_GOVERNANCE_FILES

    guard = _load_pre_tool_guard()
    self.assertIn("lib/git_index.py", CORE_GOVERNANCE_FILES)
    self.assertIn("lib/git_index.py", guard.GOVERNANCE_FILE_RELPATHS)

    mod_path = os.path.join(REPO_ROOT, "lib", "git_index.py")
    self.assertTrue(os.path.isfile(mod_path))
    with open(mod_path, "r", encoding="utf-8") as f:
      self.assertFalse(f.readline().startswith("#!"))

    with tempfile.TemporaryDirectory() as ws:
      res_sh = guard.evaluate_tool_call({
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "echo '# hack' > lib/git_index.py"},
          },
      })
      self.assertEqual(res_sh["decision"], "deny")


if __name__ == "__main__":
  unittest.main()
