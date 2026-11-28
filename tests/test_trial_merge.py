#!/usr/bin/env python3
"""Hermetic unit and Git worktree tests for Target 75 trial-merge verification (GH-343).

Covers lib/main_sync.py SHA reporting and lib/trial_merge.py ephemeral detached
git worktree verification, receipt caching and staleness invalidation, semantic
conflict detection on BEHIND_CLEAN branches, fail-closed missing regression
command handling, SignalTrap/exception worktree cleanup, and live workspace
immutability (SCAR-GIT-0018, SCAR-GIT-20, SCAR-GIT-23, SCAR-GIT-25,
SCAR-SYS-07, SCAR-TEST-08, SCAR-TEST-45).
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.main_sync import inspect_main_sync
import lib.trial_merge as trial_merge_mod
from lib.trial_merge import (
    DEFAULT_TRIAL_MERGE_RECEIPT_REL,
    TrialMergeDecision,
    ensure_trial_merge_verified,
    format_upstream_delta_summary,
    load_trial_merge_receipt,
    run_trial_merge_verification,
)


def _git(cwd: str, *args: str) -> str:
  """Run a git command inside cwd, raising AssertionError on failure."""
  proc = subprocess.run(
      ["git", *args],
      cwd=cwd,
      capture_output=True,
      text=True,
      check=False,
  )
  if proc.returncode != 0:
    raise AssertionError(
        f"git {' '.join(args)} failed in {cwd} (rc={proc.returncode}): "
        f"{proc.stderr or proc.stdout}"
    )
  return (proc.stdout or "").strip()


def _configure_git_identity(repo_dir: str) -> None:
  """Configure local git user identity inside an ephemeral test repo."""
  _git(repo_dir, "config", "user.name", "Test Operator")
  _git(repo_dir, "config", "user.email", "test@makeflow.local")


def _list_worktree_paths(repo_dir: str) -> list:
  """Return all worktree paths reported by git worktree list --porcelain."""
  out = _git(repo_dir, "worktree", "list", "--porcelain")
  paths = []
  for line in out.splitlines():
    if line.startswith("worktree "):
      paths.append(os.path.realpath(line[len("worktree ") :].strip()))
  return paths


def _create_bare_and_clone_repo(
    tmp_root: str,
    *,
    regression_cmd: str = "python3 -m unittest discover -s . -p 'test_*.py'",
) -> dict:
  """Create an isolated bare remote, seed clone, and working clone on a PR branch."""
  os.makedirs(tmp_root, exist_ok=True)
  bare_dir = os.path.join(tmp_root, "origin.git")
  seed_dir = os.path.join(tmp_root, "seed")
  work_dir = os.path.join(tmp_root, "work")

  _git(tmp_root, "init", "--bare", "-b", "main", bare_dir)
  _git(tmp_root, "clone", bare_dir, seed_dir)
  _configure_git_identity(seed_dir)

  with open(os.path.join(seed_dir, "shared.py"), "w", encoding="utf-8") as fh:
    fh.write("def compute(x):\n  return x + 1\n")
  with open(os.path.join(seed_dir, "test_app.py"), "w", encoding="utf-8") as fh:
    fh.write(
        "import unittest\n"
        "from shared import compute\n"
        "class TestApp(unittest.TestCase):\n"
        "  def test_base(self):\n"
        "    self.assertEqual(compute(1), 2)\n"
    )
  _git(seed_dir, "add", "-A")
  _git(seed_dir, "commit", "-m", "chore: initial main commit")
  _git(seed_dir, "push", "-u", "origin", "main")

  _git(tmp_root, "clone", bare_dir, work_dir)
  _configure_git_identity(work_dir)
  _git(work_dir, "checkout", "-b", "feat/pr-branch")

  with open(os.path.join(work_dir, "feature.py"), "w", encoding="utf-8") as fh:
    fh.write(
        "from shared import compute\n"
        "def run_feature():\n"
        "  return compute(5)\n"
    )
  with open(
      os.path.join(work_dir, "test_feature.py"), "w", encoding="utf-8"
  ) as fh:
    fh.write(
        "import unittest\n"
        "from feature import run_feature\n"
        "class TestFeature(unittest.TestCase):\n"
        "  def test_feature(self):\n"
        "    self.assertEqual(run_feature(), 6)\n"
    )
  _git(work_dir, "add", "-A")
  _git(work_dir, "commit", "-m", "feat: add feature caller")

  if regression_cmd:
    cfg_dir = os.path.join(work_dir, ".workflow")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(
        os.path.join(cfg_dir, "config.json"), "w", encoding="utf-8"
    ) as fh:
      json.dump({"testing": {"regressionCommand": regression_cmd}}, fh)

  return {"bare": bare_dir, "seed": seed_dir, "work": work_dir}


class TestTrialMergeEngine(unittest.TestCase):
  """Hermetic Git worktree tests for Target 75 BEHIND_CLEAN trial-merge engine (GH-343)."""

  def test_main_sync_reports_head_and_upstream_shas(self) -> None:
    """Verify inspect_main_sync exposes head_sha and upstream_sha while keeping existing keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
      # No-remote repo returns None for head_sha and upstream_sha.
      no_remote = os.path.join(tmpdir, "no_remote")
      os.makedirs(no_remote, exist_ok=True)
      _git(no_remote, "init", "-b", "main")
      _configure_git_identity(no_remote)
      with open(os.path.join(no_remote, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("a\n")
      _git(no_remote, "add", "-A")
      _git(no_remote, "commit", "-m", "chore: init")
      sync_none = inspect_main_sync(no_remote)
      self.assertEqual(sync_none["status"], "NO_REMOTE")
      self.assertIsNone(sync_none["head_sha"])
      self.assertIsNone(sync_none["upstream_sha"])

      # Cloned repo returns exact SHAs on UP_TO_DATE and BEHIND_CLEAN.
      repos = _create_bare_and_clone_repo(os.path.join(tmpdir, "with_remote"))
      head_sha = _git(repos["work"], "rev-parse", "HEAD")
      main_sha = _git(repos["work"], "rev-parse", "origin/main")
      sync_up = inspect_main_sync(repos["work"])
      self.assertEqual(sync_up["status"], "UP_TO_DATE")
      self.assertEqual(sync_up["head_sha"], head_sha)
      self.assertEqual(sync_up["upstream_sha"], main_sha)

      with open(
          os.path.join(repos["seed"], "extra.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write("EXTRA = 1\n")
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "chore: advance main")
      _git(repos["seed"], "push", "origin", "main")
      _git(repos["work"], "fetch", "origin", "main")

      new_main_sha = _git(repos["work"], "rev-parse", "origin/main")
      sync_behind = inspect_main_sync(repos["work"])
      self.assertEqual(sync_behind["status"], "BEHIND_CLEAN")
      self.assertEqual(sync_behind["head_sha"], head_sha)
      self.assertEqual(sync_behind["upstream_sha"], new_main_sha)

  def test_up_to_date_and_no_remote_skip_worktree_execution(self) -> None:
    """Verify UP_TO_DATE and NO_REMOTE pass without creating worktrees or receipts."""
    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir, regression_cmd="")
      decision = ensure_trial_merge_verified(repos["work"])
      self.assertIsInstance(decision, TrialMergeDecision)
      self.assertTrue(decision.ok)
      self.assertEqual(decision.status, "UP_TO_DATE")
      self.assertIsNone(load_trial_merge_receipt(repos["work"]))

      no_remote = os.path.join(tmpdir, "plain")
      os.makedirs(no_remote, exist_ok=True)
      _git(no_remote, "init", "-b", "main")
      decision_nr = ensure_trial_merge_verified(no_remote)
      self.assertTrue(decision_nr.ok)
      self.assertEqual(decision_nr.status, "NO_REMOTE")

  def test_behind_clean_passing_trial_merge_writes_receipt_surfaces_delta_and_preserves_workspace(
      self,
  ) -> None:
    """Verify passing BEHIND_CLEAN trial-merge writes receipt, caches cleanly, surfaces delta, and keeps live tree untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir)
      work = repos["work"]

      # Advance origin/main with a non-conflicting compatible commit.
      with open(
          os.path.join(repos["seed"], "main_note.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write("NOTE = 'compatible'\n")
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "docs: add compatible main note")
      _git(repos["seed"], "push", "origin", "main")

      # Seed approved_tree.txt and an uncommitted local marker to prove immutability.
      state_dir = os.path.join(work, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      approved_path = os.path.join(state_dir, "approved_tree.txt")
      with open(approved_path, "w", encoding="utf-8") as fh:
        fh.write("deadbeef1234567890\n")
      untracked_marker = os.path.join(work, "local_scratch.txt")
      with open(untracked_marker, "w", encoding="utf-8") as fh:
        fh.write("must remain untouched\n")

      head_before = _git(work, "rev-parse", "HEAD")
      branch_before = _git(work, "rev-parse", "--abbrev-ref", "HEAD")

      decision = ensure_trial_merge_verified(work)
      self.assertTrue(decision.ok, f"Unexpected failure: {decision.failure}")
      self.assertEqual(decision.status, "PASSED")
      self.assertFalse(decision.cached)
      self.assertEqual(decision.head_sha, head_before)
      upstream_sha = _git(work, "rev-parse", "origin/main")
      self.assertEqual(decision.upstream_main_sha, upstream_sha)
      self.assertIn("docs: add compatible main note", decision.upstream_delta)
      self.assertIn("main_note.py", decision.upstream_delta)

      # Canonical receipt written and bound to (head_sha, upstream_main_sha).
      receipt = load_trial_merge_receipt(work)
      self.assertIsNotNone(receipt)
      self.assertTrue(receipt["verified"])
      self.assertEqual(receipt["exit_code"], 0)
      self.assertEqual(receipt["head_sha"], head_before)
      self.assertEqual(receipt["upstream_main_sha"], upstream_sha)
      self.assertTrue(
          os.path.isfile(os.path.join(work, DEFAULT_TRIAL_MERGE_RECEIPT_REL))
      )

      # Live workspace, branch, HEAD, approved_tree.txt, and worktree list untouched.
      self.assertEqual(_git(work, "rev-parse", "HEAD"), head_before)
      self.assertEqual(_git(work, "rev-parse", "--abbrev-ref", "HEAD"), branch_before)
      self.assertFalse(os.path.exists(os.path.join(work, "main_note.py")))
      with open(approved_path, "r", encoding="utf-8") as fh:
        self.assertEqual(fh.read(), "deadbeef1234567890\n")
      with open(untracked_marker, "r", encoding="utf-8") as fh:
        self.assertEqual(fh.read(), "must remain untouched\n")
      self.assertEqual(_list_worktree_paths(work), [os.path.realpath(work)])

      # Standalone delta summary helper matches.
      delta_text = format_upstream_delta_summary(work, "origin/main")
      self.assertIn("main_note.py", delta_text)

      # Second call hits cached receipt without re-running worktree tests.
      with patch.object(
          trial_merge_mod,
          "run_trial_merge_verification",
          side_effect=AssertionError("Should use fresh cached receipt"),
      ):
        cached_decision = ensure_trial_merge_verified(work)
      self.assertTrue(cached_decision.ok)
      self.assertEqual(cached_decision.status, "CACHED")
      self.assertTrue(cached_decision.cached)
      self.assertIn("main_note.py", cached_decision.upstream_delta)

  def test_behind_clean_semantic_conflict_fails_closed_and_cleans_worktree(
      self,
  ) -> None:
    """Verify non-overlapping semantic breakage on origin/main blocks with TRIAL_MERGE_SEMANTIC_CONFLICT."""
    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir)
      work = repos["work"]

      # origin/main changes compute(x) -> compute(x, y) without touching feature.py (BEHIND_CLEAN!).
      with open(
          os.path.join(repos["seed"], "shared.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write("def compute(x, y):\n  return x + y\n")
      with open(
          os.path.join(repos["seed"], "test_app.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write(
            "import unittest\n"
            "from shared import compute\n"
            "class TestApp(unittest.TestCase):\n"
            "  def test_base(self):\n"
            "    self.assertEqual(compute(1, 2), 3)\n"
        )
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "refactor: add required y parameter")
      _git(repos["seed"], "push", "origin", "main")

      # Pre-seed a stale receipt to verify failure removes it.
      receipt_path = os.path.join(work, DEFAULT_TRIAL_MERGE_RECEIPT_REL)
      os.makedirs(os.path.dirname(receipt_path), exist_ok=True)
      with open(receipt_path, "w", encoding="utf-8") as fh:
        json.dump({"verified": True, "head_sha": "old", "upstream_main_sha": "old"}, fh)

      head_before = _git(work, "rev-parse", "HEAD")
      decision = ensure_trial_merge_verified(work)

      self.assertFalse(decision.ok)
      self.assertEqual(decision.status, "SEMANTIC_CONFLICT")
      self.assertIsNotNone(decision.failure)
      self.assertEqual(decision.failure.code, "TRIAL_MERGE_SEMANTIC_CONFLICT")
      self.assertEqual(decision.failure.scar_id, "GIT-18")
      self.assertIn("git merge origin/main", decision.failure.reflex)
      self.assertIn("shared.py", decision.upstream_delta)
      self.assertFalse(os.path.exists(receipt_path))
      self.assertEqual(_git(work, "rev-parse", "HEAD"), head_before)
      self.assertEqual(_list_worktree_paths(work), [os.path.realpath(work)])

  def test_missing_regression_command_fails_closed(self) -> None:
    """Verify BEHIND_CLEAN fails closed with MISSING_REGRESSION_COMMAND when no test command is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir, regression_cmd="")
      with open(
          os.path.join(repos["seed"], "note.txt"), "w", encoding="utf-8"
      ) as fh:
        fh.write("advance main\n")
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "chore: advance main")
      _git(repos["seed"], "push", "origin", "main")

      decision = ensure_trial_merge_verified(repos["work"])
      self.assertFalse(decision.ok)
      self.assertEqual(decision.status, "MISSING_REGRESSION_COMMAND")
      self.assertIsNotNone(decision.failure)
      self.assertEqual(decision.failure.code, "MISSING_REGRESSION_COMMAND")
      self.assertEqual(
          _list_worktree_paths(repos["work"]),
          [os.path.realpath(repos["work"])],
      )

  def test_stale_receipt_invalidates_when_origin_main_advances(self) -> None:
    """Verify cached receipt invalidates and re-runs verification when origin/main advances."""
    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir)
      work = repos["work"]

      # First advance: compatible
      with open(
          os.path.join(repos["seed"], "note1.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write("N1 = 1\n")
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "chore: first main advance")
      _git(repos["seed"], "push", "origin", "main")

      first = ensure_trial_merge_verified(work)
      self.assertTrue(first.ok)
      sha1 = first.upstream_main_sha

      # Second advance: still compatible, new upstream_main_sha
      with open(
          os.path.join(repos["seed"], "note2.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write("N2 = 2\n")
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "chore: second main advance")
      _git(repos["seed"], "push", "origin", "main")

      second = ensure_trial_merge_verified(work)
      self.assertTrue(second.ok)
      self.assertEqual(second.status, "PASSED")
      self.assertFalse(second.cached)
      self.assertNotEqual(second.upstream_main_sha, sha1)
      receipt = load_trial_merge_receipt(work)
      self.assertEqual(receipt["upstream_main_sha"], second.upstream_main_sha)

  def test_signal_trap_and_exception_cleanup_leaves_zero_orphaned_worktrees(
      self,
  ) -> None:
    """Verify interrupted or raising trial-merge runs always remove ephemeral worktrees (SCAR-SYS-07)."""
    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir)
      work = repos["work"]
      with open(
          os.path.join(repos["seed"], "note.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write("X = 1\n")
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "chore: advance main")
      _git(repos["seed"], "push", "origin", "main")

      observed_cwds = []

      def _boom_shell(cmd, cwd, timeout_seconds):
        observed_cwds.append(cwd)
        raise KeyboardInterrupt("simulated SIGINT during trial-merge test run")

      with patch.object(
          trial_merge_mod, "_run_regression_shell", side_effect=_boom_shell
      ):
        with self.assertRaises(KeyboardInterrupt):
          run_trial_merge_verification(work)

      self.assertEqual(len(observed_cwds), 1)
      self.assertFalse(os.path.exists(observed_cwds[0]))
      self.assertEqual(_list_worktree_paths(work), [os.path.realpath(work)])

  def test_real_sigint_during_regression_blocks_receipt_and_kills_child(
      self,
  ) -> None:
    """Verify a real SIGINT never yields a verified receipt or a live child (SCAR-SYS-07)."""
    with tempfile.TemporaryDirectory() as tmpdir:
      pid_file = os.path.join(tmpdir, "regression.pid")
      repos = _create_bare_and_clone_repo(
          tmpdir,
          regression_cmd=f"echo $$ > '{pid_file}'; exec sleep 60",
      )
      work = repos["work"]
      with open(
          os.path.join(repos["seed"], "note.py"), "w", encoding="utf-8"
      ) as fh:
        fh.write("X = 1\n")
      _git(repos["seed"], "add", "-A")
      _git(repos["seed"], "commit", "-m", "chore: advance main")
      _git(repos["seed"], "push", "origin", "main")

      helper = subprocess.Popen(
          [
              sys.executable,
              "-c",
              "import sys; sys.path.insert(0, sys.argv[1]);"
              "from lib.trial_merge import run_trial_merge_verification;"
              "run_trial_merge_verification(sys.argv[2])",
              _REPO_ROOT,
              work,
          ],
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
      )
      try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
          if os.path.isfile(pid_file) and os.path.getsize(pid_file) > 0:
            break
          time.sleep(0.05)
        self.assertTrue(os.path.isfile(pid_file), "regression never started")
        with open(pid_file, encoding="utf-8") as fh:
          child_pid = int(fh.read().strip())
        helper.send_signal(signal.SIGINT)
        helper.wait(timeout=30)
      finally:
        if helper.poll() is None:
          helper.kill()
          helper.wait()

      child_alive = True
      for _ in range(100):
        try:
          os.kill(child_pid, 0)
        except ProcessLookupError:
          child_alive = False
          break
        time.sleep(0.05)
      if child_alive:
        os.kill(child_pid, signal.SIGKILL)
      self.assertFalse(child_alive, "regression child survived SIGINT")
      self.assertIsNone(load_trial_merge_receipt(work))
      self.assertEqual(_list_worktree_paths(work), [os.path.realpath(work)])


def _advance_main(repos: dict, name: str, content: str, message: str) -> None:
  """Commit and push one file to origin/main from the seed clone."""
  with open(os.path.join(repos["seed"], name), "w", encoding="utf-8") as fh:
    fh.write(content)
  _git(repos["seed"], "add", "-A")
  _git(repos["seed"], "commit", "-m", message)
  _git(repos["seed"], "push", "origin", "main")


def _run_gate(work: str, *args: str) -> subprocess.CompletedProcess:
  """Run scripts/workflow_gates.py against a temporary repo root."""
  return subprocess.run(
      [
          sys.executable,
          os.path.join(_REPO_ROOT, "scripts", "workflow_gates.py"),
          "--repo-root",
          work,
          *args,
      ],
      capture_output=True,
      text=True,
      check=False,
  )


class TestTrialMergeTransitionWiring(unittest.TestCase):
  """Target 75 state machine, CLI, Makefile, and governance wiring (GH-343)."""

  def test_ready_to_land_and_land_pass_with_trial_merge_receipt(self) -> None:
    """Verify T70->T75 and T75->T80 pass on BEHIND_CLEAN with a passing trial-merge."""
    from lib.state_machine import advance_workflow_target

    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir)
      work = repos["work"]
      _advance_main(repos, "ok.py", "OK = 1\n", "chore: compatible advance")
      head_before = _git(work, "rev-parse", "HEAD")
      ev = {"skipMainFetch": True}
      _git(work, "fetch", "origin", "main")
      ok, msg = advance_workflow_target("T70", "T75", ev, workspace_dir=work)
      self.assertTrue(ok, msg)
      self.assertIn("MAIN_SYNC_NOTICE", msg)
      self.assertIn("TRIAL_MERGE_VERIFIED", msg)
      self.assertIn("compatible advance", msg)
      self.assertTrue(
          os.path.isfile(os.path.join(work, DEFAULT_TRIAL_MERGE_RECEIPT_REL))
      )
      ok2, msg2 = advance_workflow_target(
          "T75", "T80", {**ev, "humanLandingApproved": True}, workspace_dir=work
      )
      self.assertTrue(ok2, msg2)
      self.assertIn("TRIAL_MERGE_VERIFIED", msg2)
      self.assertEqual(_git(work, "rev-parse", "HEAD"), head_before)
      self.assertEqual(_list_worktree_paths(work), [os.path.realpath(work)])

  def test_ready_to_land_blocks_on_semantic_conflict(self) -> None:
    """Verify T70->T75 fails closed with TRIAL_MERGE_SEMANTIC_CONFLICT after a semantic break."""
    from lib.state_machine import advance_workflow_target

    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir)
      work = repos["work"]
      _advance_main(
          repos, "shared.py", "def compute(x):\n  return x + 100\n",
          "fix: change compute contract",
      )
      _git(work, "fetch", "origin", "main")
      ok, msg = advance_workflow_target(
          "T70", "T75", {"skipMainFetch": True}, workspace_dir=work
      )
      self.assertFalse(ok)
      self.assertIn("TRIAL_MERGE_SEMANTIC_CONFLICT", msg)
      self.assertEqual(_list_worktree_paths(work), [os.path.realpath(work)])

  def test_land_blocks_with_missing_regression_command(self) -> None:
    """Verify T75->T80 fails closed with MISSING_REGRESSION_COMMAND when none is configured."""
    from lib.state_machine import advance_workflow_target

    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir, regression_cmd="")
      work = repos["work"]
      _advance_main(repos, "ok.py", "OK = 1\n", "chore: advance")
      _git(work, "fetch", "origin", "main")
      ok, msg = advance_workflow_target(
          "T75",
          "T80",
          {"skipMainFetch": True, "humanLandingApproved": True},
          workspace_dir=work,
      )
      self.assertFalse(ok)
      self.assertIn("MISSING_REGRESSION_COMMAND", msg)

  def test_behind_clean_notice_keeps_scar_git_18_reference(self) -> None:
    """Verify the open-PR notice mentions the trial-merge and keeps SCAR-GIT-18."""
    from lib.state_machine import _format_behind_clean_notice

    text = _format_behind_clean_notice("T70", "T75", "origin/main", 2)
    self.assertIn("SCAR-GIT-18", text)
    self.assertIn("trial-merge", text)

  def test_make_advance_timeout_for_t75_and_t80_uses_regression_timeout(
      self,
  ) -> None:
    """Verify T75 and T80 Make timeouts add 5 seconds to the regression timeout."""
    from lib.state_machine import _resolve_make_advance_timeout

    with tempfile.TemporaryDirectory() as tmpdir:
      self.assertEqual(_resolve_make_advance_timeout("T75", {}, tmpdir), 305.0)
      self.assertEqual(_resolve_make_advance_timeout("T80", {}, tmpdir), 305.0)
      self.assertEqual(_resolve_make_advance_timeout("T45", {}, tmpdir), 30.0)

  def test_verify_trial_merge_cli_pass_and_semantic_fail_exit_codes(self) -> None:
    """Verify the verify-trial-merge gate exits 0 on pass and 2 with a Reflex on failure."""
    with tempfile.TemporaryDirectory() as tmpdir:
      repos = _create_bare_and_clone_repo(tmpdir)
      work = repos["work"]
      _advance_main(repos, "ok.py", "OK = 1\n", "chore: advance")
      passed = _run_gate(work, "verify-trial-merge", "--json")
      self.assertEqual(passed.returncode, 0, passed.stderr)
      self.assertEqual(json.loads(passed.stdout)["status"], "PASSED")
      _advance_main(
          repos, "shared.py", "def compute(x):\n  return 0\n", "fix: break"
      )
      failed = _run_gate(work, "verify-trial-merge")
      self.assertEqual(failed.returncode, 2, failed.stderr)
      self.assertIn("TRIAL_MERGE_SEMANTIC_CONFLICT", failed.stdout + failed.stderr)

  def test_makefile_and_receipt_cleanup_and_governance_registration(self) -> None:
    """Verify Makefile recipe, intake cleanup, and governance tables know the trial-merge."""
    from lib.intake_gates import cleanup_tdd_receipt
    from lib.state_makefile import generate_state_makefile
    from lib.harness_boundary import HARNESS_INTERNAL_RELPATHS
    from lib.pre_tool_guard import (
        GOVERNANCE_FILE_RELPATHS,
        STATE_GOVERNANCE_RELPATHS,
    )
    from lib.tdd_guard import CORE_GOVERNANCE_FILES

    with tempfile.TemporaryDirectory() as tmpdir:
      self.assertIn("verify-trial-merge:", generate_state_makefile(tmpdir))
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir)
      receipt = os.path.join(state_dir, "75_trial_merge_receipt.json")
      with open(receipt, "w", encoding="utf-8") as fh:
        fh.write("{}")
      cleanup_tdd_receipt(tmpdir, "LOCAL-1")
      self.assertFalse(os.path.exists(receipt))
    self.assertIn("lib/trial_merge.py", CORE_GOVERNANCE_FILES)
    self.assertIn("lib/trial_merge.py", GOVERNANCE_FILE_RELPATHS)
    self.assertIn("lib/trial_merge.py", HARNESS_INTERNAL_RELPATHS)
    self.assertIn(
        ".workflow/state/75_trial_merge_receipt.json", STATE_GOVERNANCE_RELPATHS
    )


if __name__ == "__main__":
  unittest.main()
