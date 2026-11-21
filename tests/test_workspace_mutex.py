#!/usr/bin/env python3
"""Unit tests for Multi-Workspace Ticket Mutual Exclusion (GH-100, SCAR-SYS-08).

Tests atomic directory locking, local session registry collision detection,
stale PID purging, remote conflict discovery, and Vector 4 Reflex gates.
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
  if _p not in sys.path:
    sys.path.insert(0, _p)

from lib.signal_trap import SignalTrap
from lib.workflow_gates import WorkflowGatesScript
from lib.workspace_mutex import WorkspaceMutex, WorkspaceMutexScript


class TestWorkspaceMutex(unittest.TestCase):
  """Comprehensive test suite for WorkspaceMutex and cross-workspace collision guards."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.repo_root = os.path.join(self.temp_dir.name, "active_repo")
    self.other_root = os.path.join(self.temp_dir.name, "other_repo")
    self.lock_dir = os.path.join(self.temp_dir.name, "locks")
    os.makedirs(self.repo_root, exist_ok=True)
    os.makedirs(self.lock_dir, exist_ok=True)

    # Initialize a mock Git repo in repo_root
    subprocess.run(["git", "init"], cwd=self.repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=self.repo_root, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=self.repo_root, capture_output=True, check=True
    )
    dummy_file = os.path.join(self.repo_root, "init.txt")
    with open(dummy_file, "w", encoding="utf-8") as f:
      f.write("initial commit\n")
    subprocess.run(["git", "add", "init.txt"], cwd=self.repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=self.repo_root, capture_output=True, check=True
    )

    # Clone repo_root into other_root so they share the initial commit hash
    subprocess.run(
        ["git", "clone", self.repo_root, self.other_root],
        capture_output=True,
        check=True,
    )

    # Reset SignalTrap singleton if present
    if hasattr(SignalTrap, "reset_instance"):
      SignalTrap.reset_instance()

  def tearDown(self):
    if hasattr(SignalTrap, "reset_instance"):
      SignalTrap.reset_instance()
    self.temp_dir.cleanup()

  def test_acquire_success(self):
    """TestCase 1: Acquire lock in empty lock directory successfully."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    res = mutex.acquire(
        ticket_id="GH-100",
        workspace_path=self.repo_root,
        branch_name="feat/100-mutex",
        pid=12345,
    )
    self.assertTrue(res["acquired"])
    self.assertTrue(os.path.isdir(res["lock_path"]))

    session_file = os.path.join(res["lock_path"], "session.json")
    self.assertTrue(os.path.isfile(session_file))
    with open(session_file, "r", encoding="utf-8") as f:
      data = json.load(f)

    self.assertEqual(data["ticket_id"], "GH-100")
    self.assertEqual(data["workspace_path"], self.repo_root)
    self.assertEqual(data["branch_name"], "feat/100-mutex")
    self.assertEqual(data["pid"], 12345)
    self.assertIn("repo_hash", data)
    self.assertIn("created_at_utc", data)

  def test_acquire_collision_active_pid(self):
    """TestCase 2: Collision detection when lock exists and recorded PID is alive."""
    mutex1 = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    acquire1 = mutex1.acquire(
        ticket_id="GH-100",
        workspace_path=self.repo_root,
        branch_name="feat/100-mutex",
        pid=99999,
    )
    self.assertTrue(acquire1["acquired"])

    # Simulate another workspace attempting intake on the same ticket while PID 99999 is alive
    mutex2 = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    with patch.object(WorkspaceMutex, "is_pid_alive", return_value=True):
      res = mutex2.acquire(
          ticket_id="GH-100",
          workspace_path=self.other_root,
          branch_name="feat/100-mutex-cloned",
          pid=88888,
      )

    self.assertFalse(res["acquired"])
    self.assertEqual(res["reason"], "COLLISION")
    self.assertEqual(res["session"]["workspace_path"], self.repo_root)
    self.assertEqual(res["session"]["pid"], 99999)

  def test_acquire_stale_pid_recovery(self):
    """TestCase 3: Stale lock recovery when recorded PID is dead."""
    mutex1 = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    acquire1 = mutex1.acquire(
        ticket_id="GH-100",
        workspace_path=self.repo_root,
        branch_name="feat/100-stale",
        pid=77777,
    )
    self.assertTrue(acquire1["acquired"])

    # Attempt acquire with dead PID (is_pid_alive returns False)
    mutex2 = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    with patch.object(WorkspaceMutex, "is_pid_alive", return_value=False):
      res = mutex2.acquire(
          ticket_id="GH-100",
          workspace_path=self.other_root,
          branch_name="feat/100-recovered",
          pid=66666,
      )

    self.assertTrue(res["acquired"])
    session_file = os.path.join(res["lock_path"], "session.json")
    with open(session_file, "r", encoding="utf-8") as f:
      data = json.load(f)
    self.assertEqual(data["pid"], 66666)
    self.assertEqual(data["workspace_path"], self.other_root)

  def test_release_lock(self):
    """TestCase 4: Release lock cleans up session.json and directory."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    res = mutex.acquire(ticket_id="GH-100")
    self.assertTrue(res["acquired"])
    self.assertTrue(os.path.isdir(res["lock_path"]))

    released = mutex.release("GH-100")
    self.assertTrue(released)
    self.assertFalse(os.path.exists(res["lock_path"]))

    # Releasing already released lock is idempotent and does not raise
    released_again = mutex.release("GH-100")
    self.assertFalse(released_again)

  def test_signal_trap_integration(self):
    """TestCase 5: SignalTrap integration unlinks lock directory upon cleanup."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    res = mutex.acquire(ticket_id="GH-100")
    lock_path = res["lock_path"]
    self.assertTrue(os.path.isdir(lock_path))

    mutex.register_signal_trap("GH-100")
    trap = SignalTrap.get_instance()
    self.assertIn(os.path.abspath(lock_path), trap._lock_dirs)

    # Execute cleanups simulates signal handling or process shutdown
    trap.execute_cleanups()
    self.assertFalse(os.path.exists(lock_path))

  def test_remote_github_assigned(self):
    """TestCase 6: Remote GitHub check flags existing assignees and in-progress labels."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)

    # 1. Assigned to another user
    mock_assigned = MagicMock(
        returncode=0,
        stdout=json.dumps({"assignees": [{"login": "alice"}], "labels": []}),
        stderr="",
    )
    with patch("subprocess.run", return_value=mock_assigned):
      check1 = mutex.check_remote("GH-100", hosting_mode="GITHUB")
      self.assertTrue(check1["conflict"])
      self.assertEqual(check1["assignees"], ["alice"])

    # 2. Labeled in-progress
    mock_labeled = MagicMock(
        returncode=0,
        stdout=json.dumps({"assignees": [], "labels": [{"name": "in-progress"}]}),
        stderr="",
    )
    with patch("subprocess.run", return_value=mock_labeled):
      check2 = mutex.check_remote("GH-100", hosting_mode="GITHUB")
      self.assertTrue(check2["conflict"])
      self.assertEqual(check2["labels"], ["in-progress"])

    # 3. Clean issue (no assignees, no in-progress label) retains last_remote_issue
    clean_payload = {
        "number": 100,
        "title": "[FEAT] Multi-workspace mutex",
        "url": "https://github.com/example/repo/issues/100",
        "assignees": [],
        "labels": [{"name": "bug"}],
    }
    mock_clean = MagicMock(
        returncode=0,
        stdout=json.dumps(clean_payload),
        stderr="",
    )
    with patch("subprocess.run", return_value=mock_clean) as mock_run:
      check3 = mutex.check_remote("GH-100", hosting_mode="GITHUB")
      self.assertFalse(check3["conflict"])
      self.assertEqual(mutex.last_remote_issue, clean_payload)
      called_cmd = mock_run.call_args[0][0]
      self.assertIn("number,title,url,assignees,labels", called_cmd)

    # 4. Failed check resets last_remote_issue to None
    mock_err = MagicMock(returncode=1, stdout="", stderr="network error")
    with patch("subprocess.run", return_value=mock_err):
      check4 = mutex.check_remote("GH-100", hosting_mode="GITHUB")
      self.assertFalse(check4["conflict"])
      self.assertIsNone(mutex.last_remote_issue)

    # 5. Non-dict JSON payload from gh degrades cleanly without AttributeError
    for raw_non_dict in ("[]", "null", '"unexpected"'):
      mock_non_dict = MagicMock(returncode=0, stdout=raw_non_dict, stderr="")
      with patch("subprocess.run", return_value=mock_non_dict):
        check5 = mutex.check_remote("GH-100", hosting_mode="GITHUB")
        self.assertFalse(check5["conflict"])
        self.assertEqual(check5["hosting_mode"], "GITHUB")
        self.assertIn("error", check5)
        self.assertIsNone(mutex.last_remote_issue)

  def test_remote_pure_git_branch_exists(self):
    """TestCase 7: Remote Pure Git check flags existing remote branches."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)

    # 1. Remote branch exists
    mock_refs = MagicMock(
        returncode=0,
        stdout="7a8b9c refs/heads/feat/LOCAL-10-feature\n",
        stderr="",
    )
    with patch("subprocess.run", return_value=mock_refs):
      check1 = mutex.check_remote("LOCAL-10", hosting_mode="PURE_GIT")
      self.assertTrue(check1["conflict"])
      self.assertIn("7a8b9c refs/heads/feat/LOCAL-10-feature", check1["refs"])

    # 2. Remote branch does not exist
    mock_empty = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_empty):
      check2 = mutex.check_remote("LOCAL-10", hosting_mode="PURE_GIT")
      self.assertFalse(check2["conflict"])

  def test_workflow_gates_intake_collision_reflex(self):
    """TestCase 8: workflow_gates intake detects ticket collision and emits Vector 4 Reflex."""
    # Pre-acquire lock for GH-100 under self.lock_dir by another workspace
    mutex = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    mutex.acquire(
        ticket_id="GH-100",
        workspace_path=self.other_root,
        branch_name="feat/100-worker",
        pid=99999,
    )

    # Prepare repo_root to pass basic intake checks (exclude .workflow dirs)
    subprocess.run(
        ["git", "check-ignore", "-q", ".workflow/state/"],
        cwd=self.repo_root,
    )
    exclude_file = os.path.join(self.repo_root, ".git", "info", "exclude")
    with open(exclude_file, "a", encoding="utf-8") as f:
      f.write(".workflow/state/\n.workflow/archive/\n")

    err_stream = io.StringIO()
    with patch.object(WorkspaceMutex, "is_pid_alive", return_value=True):
      exit_code = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              self.repo_root,
              "--lock-dir",
              self.lock_dir,
              "intake",
              "--ticket",
              "GH-100",
          ],
          stdout=io.StringIO(),
          stderr=err_stream,
      )

    self.assertEqual(exit_code, 2)
    err_output = err_stream.getvalue()
    self.assertIn("[SCAR-ID: SYS-08] TICKET_COLLISION_DETECTED", err_output)
    self.assertIn(self.other_root, err_output)
    self.assertIn("99999", err_output)

  def test_release_intake_gate(self):
    """TestCase 9: workflow_gates release-intake unlocks the ticket."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    res = mutex.acquire(ticket_id="GH-100")
    self.assertTrue(os.path.isdir(res["lock_path"]))

    exit_code = WorkflowGatesScript().execute(
        args=[
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "release-intake",
            "--ticket",
            "GH-100",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    self.assertEqual(exit_code, 0)
    self.assertFalse(os.path.exists(res["lock_path"]))

  def test_cli_interface(self):
    """TestCase 10: WorkspaceMutexScript CLI execution lifecycle."""
    script = WorkspaceMutexScript()
    out = io.StringIO()
    err = io.StringIO()

    # CLI acquire
    code_acq = script.execute(
        args=[
            "acquire",
            "--ticket",
            "GH-100",
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "--json",
        ],
        stdout=out,
        stderr=err,
    )
    self.assertEqual(code_acq, 0)
    data = json.loads(out.getvalue())
    self.assertTrue(data["acquired"])

    # CLI status
    out_stat = io.StringIO()
    code_stat = script.execute(
        args=[
            "status",
            "--ticket",
            "GH-100",
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "--json",
        ],
        stdout=out_stat,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_stat, 0)
    stat_data = json.loads(out_stat.getvalue())
    self.assertTrue(stat_data["locked"])

    # CLI release
    out_rel = io.StringIO()
    code_rel = script.execute(
        args=[
            "release",
            "--ticket",
            "GH-100",
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "--json",
        ],
        stdout=out_rel,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_rel, 0)
    rel_data = json.loads(out_rel.getvalue())
    self.assertTrue(rel_data["released"])

    # CLI check-dependencies
    out_dep = io.StringIO()
    payload = {
        "data": {
            "repository": {
                "issue": {
                    "issueDependenciesSummary": {"totalBlockedBy": 0},
                    "blockedBy": {"totalCount": 0, "nodes": []},
                }
            }
        }
    }
    mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
    with patch("subprocess.run", return_value=mock_proc):
      code_dep = script.execute(
          args=[
              "check-dependencies",
              "--ticket",
              "GH-100",
              "--repo-root",
              self.repo_root,
              "--json",
          ],
          stdout=out_dep,
          stderr=io.StringIO(),
      )
    self.assertEqual(code_dep, 0)
    dep_data = json.loads(out_dep.getvalue())
    self.assertFalse(dep_data["blocked"])

  def test_is_pid_alive_contract(self):
    """TestCase 11: is_pid_alive properly identifies alive and dead PIDs."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    # Current process PID must be alive
    self.assertTrue(mutex.is_pid_alive(os.getpid()))
    # PID <= 0 must return False
    self.assertFalse(mutex.is_pid_alive(0))
    self.assertFalse(mutex.is_pid_alive(-1))

    # ProcessLookupError indicates dead process
    with patch("os.kill", side_effect=ProcessLookupError):
      self.assertFalse(mutex.is_pid_alive(12345))

    # PermissionError indicates alive process owned by another user
    with patch("os.kill", side_effect=PermissionError):
      self.assertTrue(mutex.is_pid_alive(12345))

    # OSError with ESRCH indicates dead process
    err_srch = OSError()
    err_srch.errno = 3  # ESRCH
    with patch("os.kill", side_effect=err_srch):
      self.assertFalse(mutex.is_pid_alive(12345))

    # OSError with EPERM indicates alive process
    err_perm = OSError()
    err_perm.errno = 1  # EPERM
    with patch("os.kill", side_effect=err_perm):
      self.assertTrue(mutex.is_pid_alive(12345))

  def test_get_repo_hash_fallback(self):
    """TestCase 12: get_repo_hash falls back to SHA-256 when git is unavailable."""
    non_git_dir = os.path.join(self.temp_dir.name, "non_git")
    os.makedirs(non_git_dir, exist_ok=True)
    mutex = WorkspaceMutex(repo_root=non_git_dir, lock_dir=self.lock_dir)
    repo_hash = mutex.get_repo_hash()
    import hashlib
    expected_hash = hashlib.sha256(non_git_dir.encode("utf-8")).hexdigest()
    self.assertEqual(repo_hash, expected_hash)

  def test_acquire_workspace_reentrancy(self):
    """TestCase 13: Re-running intake within the same workspace is reentrant."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    res1 = mutex.acquire(
        ticket_id="GH-100",
        workspace_path=self.repo_root,
        branch_name="feat/100-mutex",
        pid=12345,
    )
    self.assertTrue(res1["acquired"])

    # Re-running acquire from the same workspace path succeeds with reentrant: True
    res2 = mutex.acquire(
        ticket_id="GH-100",
        workspace_path=self.repo_root,
        branch_name="feat/100-mutex",
        pid=12346,
    )
    self.assertTrue(res2["acquired"])
    self.assertTrue(res2.get("reentrant"))

  def test_acquire_collision_cross_workspace_active_ticket(self):
    """TestCase 14: Cross-workspace collision with active 10_ticket.json even if PID is dead."""
    mutex1 = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    acquire1 = mutex1.acquire(
        ticket_id="GH-100",
        workspace_path=self.other_root,
        branch_name="feat/100-worker",
        pid=99999,
    )
    self.assertTrue(acquire1["acquired"])

    # Create active .workflow/state/10_ticket.json in other_root
    other_state = os.path.join(self.other_root, ".workflow", "state")
    os.makedirs(other_state, exist_ok=True)
    with open(
        os.path.join(other_state, "10_ticket.json"), "w", encoding="utf-8"
    ) as f:
      json.dump({"ticket_id": "GH-100", "title": "Mutex test"}, f)
    with open(os.path.join(other_state, "99_next_action.md"), "w", encoding="utf-8") as f:
      f.write("# 99_next_action\nActive Target: Target 30\n")

    # In repo_root, attempt acquire while other_root PID 99999 is dead
    mutex2 = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    with patch.object(WorkspaceMutex, "is_pid_alive", return_value=False):
      res = mutex2.acquire(
          ticket_id="GH-100",
          workspace_path=self.repo_root,
          branch_name="feat/100-parallel",
          pid=11111,
      )

    self.assertFalse(res["acquired"])
    self.assertEqual(res["reason"], "COLLISION")
    self.assertEqual(res["session"]["workspace_path"], self.other_root)

  def test_acquire_stale_when_ticket_complete(self):
    """TestCase 15: Stale lock recovery when ticket state is COMPLETE in other workspace."""
    mutex1 = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    acquire1 = mutex1.acquire(
        ticket_id="GH-100",
        workspace_path=self.other_root,
        branch_name="feat/100-worker",
        pid=99999,
    )
    self.assertTrue(acquire1["acquired"])

    # Create completed .workflow/state/99_next_action.md in other_root
    other_state = os.path.join(self.other_root, ".workflow", "state")
    os.makedirs(other_state, exist_ok=True)
    with open(
        os.path.join(other_state, "10_ticket.json"), "w", encoding="utf-8"
    ) as f:
      json.dump({"ticket_id": "GH-100", "title": "Mutex test"}, f)
    with open(os.path.join(other_state, "99_next_action.md"), "w", encoding="utf-8") as f:
      f.write("# 99_next_action\nStatus: COMPLETE\n")

    # In repo_root, attempt acquire with dead PID
    mutex2 = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    with patch.object(WorkspaceMutex, "is_pid_alive", return_value=False):
      res = mutex2.acquire(
          ticket_id="GH-100",
          workspace_path=self.repo_root,
          branch_name="feat/100-fresh",
          pid=22222,
      )

    self.assertTrue(res["acquired"])
    self.assertEqual(res["session"]["workspace_path"], self.repo_root)

  def test_acquire_stale_when_workspace_dir_missing(self):
    """TestCase 16: Stale lock recovery when recorded workspace directory does not exist."""
    lp = os.path.join(self.lock_dir, "fakehash-GH-100.lock")
    os.makedirs(lp, exist_ok=True)
    with open(os.path.join(lp, "session.json"), "w", encoding="utf-8") as f:
      json.dump(
          {
              "repo_root": "/nonexistent/repo",
              "repo_hash": "fakehash",
              "ticket_id": "GH-100",
              "workspace_path": "/nonexistent/workspace/dir",
              "branch_name": "feat/100",
              "pid": 99999,
          },
          f,
      )
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    with patch.object(WorkspaceMutex, "get_repo_hash", return_value="fakehash"):
      with patch.object(WorkspaceMutex, "is_pid_alive", return_value=False):
        res = mutex.acquire(
            ticket_id="GH-100",
            workspace_path=self.repo_root,
            pid=33333,
        )
    self.assertTrue(res["acquired"])

  def test_claim_issue(self):
    """TestCase 17: claim_issue executes gh issue edit with assignee and in-progress label."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    mock_proc = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_proc) as mock_run:
      res = mutex.claim_issue("GH-100")
      self.assertTrue(res["claimed"])
      mock_run.assert_called_once()
      args = mock_run.call_args[0][0]
      self.assertEqual(args[:4], ["gh", "issue", "edit", "100"])
      self.assertIn("--add-assignee", args)
      self.assertIn("@me", args)
      self.assertIn("--add-label", args)
      self.assertIn("in-progress", args)

  def test_workflow_gates_intake_remote_collision_reflex(self):
    """TestCase 18: workflow_gates intake rejects remote conflict with Vector 4 Reflex."""
    exclude_file = os.path.join(self.repo_root, ".git", "info", "exclude")
    with open(exclude_file, "a", encoding="utf-8") as f:
      f.write(".workflow/state/\n.workflow/archive/\n")

    err_stream = io.StringIO()
    mock_remote = {
        "conflict": True,
        "hosting_mode": "GITHUB",
        "reason": "REMOTE_ASSIGNED_OR_IN_PROGRESS",
        "assignees": ["bob"],
        "labels": ["in-progress"],
    }
    with patch.object(WorkspaceMutex, "check_remote", return_value=mock_remote):
      exit_code = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              self.repo_root,
              "--lock-dir",
              self.lock_dir,
              "intake",
              "--ticket",
              "GH-100",
          ],
          stdout=io.StringIO(),
          stderr=err_stream,
      )

    self.assertEqual(exit_code, 2)
    err_output = err_stream.getvalue()
    self.assertIn("[SCAR-ID: SYS-08] REMOTE_TICKET_COLLISION", err_output)
    self.assertIn("bob", err_output)

  def test_workflow_gates_init_makefile_forwards_ticket(self):
    """TestCase 19: init-makefile declares TICKET ?= and forwards it to check-intake."""
    exit_code = WorkflowGatesScript().execute(
        args=["--repo-root", self.repo_root, "init-makefile"],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    self.assertEqual(exit_code, 0)
    mk_path = os.path.join(self.repo_root, ".workflow", "state", "Makefile")
    with open(mk_path, "r", encoding="utf-8") as f:
      content = f.read()
    self.assertIn("TICKET ?=", content)
    self.assertIn("ALLOW_BLOCKED ?=", content)
    self.assertIn("export TICKET ALLOW_BLOCKED", content)
    self.assertIn("intake --make-env", content)

  def test_acquire_toctou_delayed_session_json(self):
    """TestCase 20: acquire retries reading session.json before declaring lock stale."""
    mutex1 = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    lp = mutex1.lock_path("GH-100")
    os.makedirs(lp, exist_ok=True)

    real_read = mutex1._read_session
    calls = [0]
    valid_session = {
        "repo_root": self.other_root,
        "repo_hash": mutex1.get_repo_hash(),
        "ticket_id": "GH-100",
        "workspace_path": self.other_root,
        "branch_name": "feat/100-worker",
        "pid": 99999,
        "created_at_utc": "2026-11-07T00:00:00Z",
    }

    def mock_read(path):
      calls[0] += 1
      if calls[0] == 1:
        return {}  # Simulate directory created but session.json not yet flushed
      return valid_session

    mutex2 = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    with patch.object(WorkspaceMutex, "_read_session", side_effect=mock_read):
      with patch.object(WorkspaceMutex, "is_pid_alive", return_value=True):
        res = mutex2.acquire(
            ticket_id="GH-100",
            workspace_path=self.repo_root,
            pid=11111,
        )

    self.assertFalse(res["acquired"])
    self.assertEqual(res["reason"], "COLLISION")
    self.assertGreaterEqual(calls[0], 2)

  def test_check_dependencies_no_blockers(self):
    """TestCase 21: check_dependencies reports unblocked when issue has zero blockers."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    payload = {
        "data": {
            "repository": {
                "issue": {
                    "issueDependenciesSummary": {"totalBlockedBy": 0},
                    "blockedBy": {"totalCount": 0, "nodes": []},
                }
            }
        }
    }
    mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
    with patch("subprocess.run", return_value=mock_proc) as mock_run:
      res = mutex.check_dependencies("GH-100")
      self.assertFalse(res["blocked"])
      self.assertEqual(res["blocking_count"], 0)
      self.assertEqual(res["open_blockers"], [])
      self.assertEqual(res["hosting_mode"], "GITHUB")
      mock_run.assert_called_once()
      args = mock_run.call_args[0][0]
      self.assertEqual(args[:3], ["gh", "api", "graphql"])
      self.assertIn("-F", args)
      self.assertIn("issue=100", args)

  def test_check_dependencies_open_blockers(self):
    """TestCase 22: check_dependencies detects open blockers and returns blocker nodes."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    payload = {
        "data": {
            "repository": {
                "issue": {
                    "issueDependenciesSummary": {"totalBlockedBy": 1},
                    "blockedBy": {
                        "totalCount": 1,
                        "nodes": [
                            {"number": 42, "title": "Upstream feature", "state": "OPEN"}
                        ],
                    },
                }
            }
        }
    }
    mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
    with patch("subprocess.run", return_value=mock_proc):
      res = mutex.check_dependencies("GH-100")
      self.assertTrue(res["blocked"])
      self.assertEqual(res["blocking_count"], 1)
      self.assertEqual(len(res["open_blockers"]), 1)
      self.assertEqual(res["open_blockers"][0]["number"], 42)
      self.assertEqual(res["open_blockers"][0]["title"], "Upstream feature")

  def test_check_dependencies_closed_blockers(self):
    """TestCase 23: check_dependencies ignores closed blockers and reports unblocked."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    payload = {
        "data": {
            "repository": {
                "issue": {
                    "issueDependenciesSummary": {"totalBlockedBy": 1},
                    "blockedBy": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "number": 42,
                                "title": "Completed upstream feature",
                                "state": "CLOSED",
                            }
                        ],
                    },
                }
            }
        }
    }
    mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
    with patch("subprocess.run", return_value=mock_proc):
      res = mutex.check_dependencies("GH-100")
      self.assertFalse(res["blocked"])
      self.assertEqual(res["blocking_count"], 0)
      self.assertEqual(res["open_blockers"], [])

  def test_check_dependencies_pure_git(self):
    """TestCase 24: check_dependencies in PURE_GIT mode bypasses remote API calls."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    with patch("subprocess.run") as mock_run:
      res = mutex.check_dependencies("GH-100", hosting_mode="PURE_GIT")
      self.assertFalse(res["blocked"])
      self.assertEqual(res["blocking_count"], 0)
      self.assertEqual(res["open_blockers"], [])
      self.assertEqual(res["hosting_mode"], "PURE_GIT")
      mock_run.assert_not_called()

  def test_check_dependencies_graphql_error(self):
    """TestCase 25: check_dependencies handles subprocess failure and exceptions gracefully."""
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    mock_proc = MagicMock(
        returncode=1, stdout="", stderr="GraphQL error: Could not resolve to an Issue"
    )
    with patch("subprocess.run", return_value=mock_proc):
      res = mutex.check_dependencies("GH-100")
      self.assertFalse(res["blocked"])
      self.assertIn("error", res)

    with patch("subprocess.run", side_effect=subprocess.SubprocessError("network timeout")):
      res = mutex.check_dependencies("GH-100")
      self.assertFalse(res["blocked"])
      self.assertIn("error", res)

  def test_intake_gate_fails_when_blocked_without_allow_flag(self):
    """TestCase 26: workflow_gates intake fails when issue has open blockers without --allow-blocked."""
    exclude_file = os.path.join(self.repo_root, ".git", "info", "exclude")
    with open(exclude_file, "a", encoding="utf-8") as f:
      f.write(".workflow/state/\n.workflow/archive/\n")

    err_stream = io.StringIO()
    mock_remote = {
        "conflict": False,
        "hosting_mode": "GITHUB",
        "assignees": [],
        "labels": [],
    }
    mock_dep = {
        "blocked": True,
        "blocking_count": 1,
        "open_blockers": [
            {"number": 42, "title": "Upstream feature", "state": "OPEN"}
        ],
        "hosting_mode": "GITHUB",
    }
    with patch.object(WorkspaceMutex, "check_remote", return_value=mock_remote):
      with patch.object(WorkspaceMutex, "check_dependencies", return_value=mock_dep):
        exit_code = WorkflowGatesScript().execute(
            args=[
                "--repo-root",
                self.repo_root,
                "--lock-dir",
                self.lock_dir,
                "intake",
                "--ticket",
                "GH-100",
            ],
            stdout=io.StringIO(),
            stderr=err_stream,
        )

    self.assertEqual(exit_code, 2)
    err_output = err_stream.getvalue()
    self.assertIn("INTAKE_DEPENDENCY_BLOCKED", err_output)
    self.assertIn("42", err_output)

  def test_intake_gate_passes_when_blocked_with_allow_flag(self):
    """TestCase 27: workflow_gates intake passes when issue has open blockers with --allow-blocked."""
    exclude_file = os.path.join(self.repo_root, ".git", "info", "exclude")
    with open(exclude_file, "a", encoding="utf-8") as f:
      f.write(".workflow/state/\n.workflow/archive/\n")

    err_stream = io.StringIO()
    mock_remote = {
        "conflict": False,
        "hosting_mode": "GITHUB",
        "assignees": [],
        "labels": [],
    }
    mock_dep = {
        "blocked": True,
        "blocking_count": 1,
        "open_blockers": [
            {"number": 42, "title": "Upstream feature", "state": "OPEN"}
        ],
        "hosting_mode": "GITHUB",
    }
    with patch.object(WorkspaceMutex, "check_remote", return_value=mock_remote):
      with patch.object(WorkspaceMutex, "check_dependencies", return_value=mock_dep):
        with patch.object(WorkspaceMutex, "claim_issue", return_value={"claimed": True}):
          exit_code = WorkflowGatesScript().execute(
              args=[
                  "--repo-root",
                  self.repo_root,
                  "--lock-dir",
                  self.lock_dir,
                  "intake",
                  "--ticket",
                  "GH-100",
                  "--allow-blocked",
              ],
              stdout=io.StringIO(),
              stderr=err_stream,
          )

    self.assertEqual(exit_code, 0)

  def test_check_dependencies_local_ticket_open_and_closed_blockers(self):
    """TestCase 28: check_dependencies resolves local blocked_by tickets via LocalTicketStore."""
    from lib.local_tickets import LocalTicketStore

    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)
    store.create_ticket(title="Base parser", ticket_id="LOCAL-15")
    store.create_ticket(
        title="CLI command",
        blocked_by=["LOCAL-15"],
        ticket_id="LOCAL-16",
    )

    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    res_open = mutex.check_dependencies("LOCAL-16", hosting_mode="PURE_GIT")
    self.assertTrue(res_open["blocked"])
    self.assertEqual(res_open["blocking_count"], 1)
    self.assertEqual(res_open["open_blockers"][0]["number"], "LOCAL-15")
    self.assertEqual(res_open["open_blockers"][0]["title"], "Base parser")
    self.assertEqual(res_open["hosting_mode"], "PURE_GIT")

    store.close_ticket("LOCAL-15")
    res_closed = mutex.check_dependencies("LOCAL-16", hosting_mode="PURE_GIT")
    self.assertFalse(res_closed["blocked"])
    self.assertEqual(res_closed["blocking_count"], 0)
    self.assertEqual(res_closed["open_blockers"], [])


class TestWorkspaceMutexSubparserRepoRootGH243(unittest.TestCase):
  """Hermetic tests for subparser --repo-root and --lock-dir resolution (GH-243)."""

  def setUp(self):
    """Create isolated temporary workspace and lock directories."""
    self.temp_dir = tempfile.TemporaryDirectory()
    self.target_repo = os.path.join(self.temp_dir.name, "target_repo")
    self.post_repo = os.path.join(self.temp_dir.name, "post_target_repo")
    self.cwd_repo = os.path.join(self.temp_dir.name, "cwd_repo")
    self.other_repo = os.path.join(self.temp_dir.name, "other_repo")
    self.locks_dir = os.path.join(self.temp_dir.name, "locks")
    self.parent_locks_dir = os.path.join(self.temp_dir.name, "parent_locks")
    self.env_locks_dir = os.path.join(self.temp_dir.name, "env_locks")
    for directory in (
        self.target_repo,
        self.post_repo,
        self.cwd_repo,
        self.other_repo,
        self.locks_dir,
        self.parent_locks_dir,
        self.env_locks_dir,
    ):
      os.makedirs(directory, exist_ok=True)
    self.default_repo_hash = WorkspaceMutex(
        repo_root=_REPO_ROOT, lock_dir=self.locks_dir
    ).get_repo_hash()

  def tearDown(self):
    """Clean temporary directories."""
    self.temp_dir.cleanup()

  def _expected_repo_hash(self, repo_root):
    """Return expected SHA-256 fallback hash for a non-git temporary directory.

    Args:
      repo_root: Target workspace directory path.

    Returns:
      Hexadecimal SHA-256 digest of the absolute repository path.
    """
    return hashlib.sha256(os.path.abspath(repo_root).encode("utf-8")).hexdigest()

  def _read_session_json(self, lock_path):
    """Read session.json directly from a lock directory on disk.

    Args:
      lock_path: Path to the atomic directory lock.

    Returns:
      Parsed JSON session metadata dictionary.
    """
    session_file = os.path.join(lock_path, "session.json")
    self.assertTrue(
        os.path.isfile(session_file),
        f"Expected session.json on disk: {session_file}",
    )
    with open(session_file, "r", encoding="utf-8") as f:
      return json.load(f)

  def _assert_no_default_repo_locks(self, *lock_dirs):
    """Assert that zero lock directories match _REPO_ROOT in any lock_dir.

    Args:
      *lock_dirs: Lock directory paths to inspect for _REPO_ROOT leaks.
    """
    for lock_dir in lock_dirs:
      if not os.path.isdir(lock_dir):
        continue
      for entry in os.listdir(lock_dir):
        if not entry.endswith(".lock"):
          continue
        self.assertFalse(
            entry.startswith(f"{self.default_repo_hash}-"),
            f"Unexpected _DEFAULT_REPO_ROOT lock in {lock_dir}: {entry}",
        )
        sess = self._read_session_json(os.path.join(lock_dir, entry))
        self.assertNotEqual(
            sess.get("repo_root"),
            _REPO_ROOT,
            f"Unexpected _DEFAULT_REPO_ROOT session in {lock_dir}: {entry}",
        )

  def _run_cli(self, args, cwd=None, script=None):
    """Execute WorkspaceMutexScript in-process with patched cwd and WORKFLOW_LOCK_DIR.

    Args:
      args: CLI argument list passed to WorkspaceMutexScript.execute().
      cwd: Optional working directory path for os.getcwd mock.
      script: Optional existing WorkspaceMutexScript instance to reuse.

    Returns:
      Tuple of (exit_code, stdout_text, stderr_text, script_instance).
    """
    target_script = script if script is not None else WorkspaceMutexScript()
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch("os.getcwd", return_value=cwd or self.cwd_repo),
        patch.dict(os.environ, {"WORKFLOW_LOCK_DIR": self.env_locks_dir}),
    ):
      code = target_script.execute(args=args, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue(), target_script

  def _assert_lock_lifecycle(
      self,
      build_args,
      expected_repo,
      expected_lock_dir,
      ticket_id,
      branch_name="feat/gh243-test",
      untouched_lock_dirs=(),
  ):
    """Exercise acquire, status, and release against expected_repo and expected_lock_dir.

    Args:
      build_args: Callable accepting subcommand args and returning full CLI args.
      expected_repo: Expected resolved repository root path.
      expected_lock_dir: Expected resolved lock directory path.
      ticket_id: Ticket identifier used for the lock lifecycle.
      branch_name: Branch name passed during lock acquisition.
      untouched_lock_dirs: Lock directories that must not receive locks for expected_repo.
    """
    abs_repo = os.path.abspath(expected_repo)
    abs_lock_dir = os.path.abspath(expected_lock_dir)
    expected_hash = self._expected_repo_hash(abs_repo)
    expected_lock_path = os.path.join(
        abs_lock_dir, f"{expected_hash}-{ticket_id}.lock"
    )

    code_acq, out_acq, err_acq, _ = self._run_cli(
        build_args(
            [
                "acquire",
                "--ticket",
                ticket_id,
                "--branch",
                branch_name,
                "--pid",
                str(os.getpid()),
                "--json",
            ]
        )
    )
    self.assertEqual(code_acq, 0, err_acq)
    acq_data = json.loads(out_acq)
    self.assertTrue(acq_data["acquired"])
    self.assertIsNone(acq_data.get("reentrant"))
    self.assertEqual(acq_data["lock_path"], expected_lock_path)
    self.assertTrue(os.path.isdir(expected_lock_path))

    disk_session = self._read_session_json(expected_lock_path)
    self.assertEqual(disk_session["repo_root"], abs_repo)
    self.assertEqual(disk_session["workspace_path"], abs_repo)
    self.assertEqual(disk_session["repo_hash"], expected_hash)
    self.assertEqual(disk_session["ticket_id"], ticket_id)
    self.assertEqual(disk_session["branch_name"], branch_name)

    for untouched_dir in untouched_lock_dirs:
      self.assertFalse(
          os.path.exists(
              os.path.join(untouched_dir, f"{expected_hash}-{ticket_id}.lock")
          ),
          f"Unexpected lock leak in {untouched_dir}",
      )
    self._assert_no_default_repo_locks(abs_lock_dir, *untouched_lock_dirs)

    code_stat, out_stat, err_stat, _ = self._run_cli(
        build_args(["status", "--ticket", ticket_id, "--json"])
    )
    self.assertEqual(code_stat, 0, err_stat)
    stat_data = json.loads(out_stat)
    self.assertTrue(stat_data["locked"])
    self.assertEqual(stat_data["lock_path"], expected_lock_path)
    self.assertEqual(stat_data["session"]["repo_root"], abs_repo)
    self.assertEqual(stat_data["session"]["repo_hash"], expected_hash)
    self.assertEqual(stat_data["session"]["branch_name"], branch_name)

    code_rel, out_rel, err_rel, _ = self._run_cli(
        build_args(["release", "--ticket", ticket_id, "--json"])
    )
    self.assertEqual(code_rel, 0, err_rel)
    rel_data = json.loads(out_rel)
    self.assertTrue(rel_data["released"])
    self.assertFalse(os.path.exists(expected_lock_path))

  def _assert_remote_gh_subcommands(self, build_args, expected_repo):
    """Exercise check-remote, check-dependencies, and claim-issue with mocked subprocess.run.

    Args:
      build_args: Callable accepting subcommand args and returning full CLI args.
      expected_repo: Expected cwd passed to subprocess.run for gh commands.
    """
    abs_repo = os.path.abspath(expected_repo)

    remote_proc = MagicMock(
        returncode=0,
        stdout=json.dumps({"assignees": [], "labels": []}),
        stderr="",
    )
    with patch("subprocess.run", return_value=remote_proc) as mock_run:
      code_rem, out_rem, err_rem, _ = self._run_cli(
          build_args(["check-remote", "--ticket", "GH-243", "--json"])
      )
    self.assertEqual(code_rem, 0, err_rem)
    self.assertFalse(json.loads(out_rem)["conflict"])
    mock_run.assert_called_once()
    self.assertEqual(mock_run.call_args[0][0][:3], ["gh", "issue", "view"])
    self.assertEqual(os.path.abspath(mock_run.call_args[1]["cwd"]), abs_repo)

    dep_payload = {
        "data": {
            "repository": {
                "issue": {
                    "issueDependenciesSummary": {"totalBlockedBy": 0},
                    "blockedBy": {"totalCount": 0, "nodes": []},
                }
            }
        }
    }
    dep_proc = MagicMock(
        returncode=0, stdout=json.dumps(dep_payload), stderr=""
    )
    with patch("subprocess.run", return_value=dep_proc) as mock_run:
      code_dep, out_dep, err_dep, _ = self._run_cli(
          build_args(["check-dependencies", "--ticket", "GH-243", "--json"])
      )
    self.assertEqual(code_dep, 0, err_dep)
    self.assertFalse(json.loads(out_dep)["blocked"])
    mock_run.assert_called_once()
    self.assertEqual(mock_run.call_args[0][0][:3], ["gh", "api", "graphql"])
    self.assertEqual(os.path.abspath(mock_run.call_args[1]["cwd"]), abs_repo)

    claim_proc = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=claim_proc) as mock_run:
      code_claim, out_claim, err_claim, _ = self._run_cli(
          build_args(["claim-issue", "--ticket", "GH-243", "--json"])
      )
    self.assertEqual(code_claim, 0, err_claim)
    self.assertTrue(json.loads(out_claim)["claimed"])
    mock_run.assert_called_once()
    self.assertEqual(
        mock_run.call_args[0][0][:4], ["gh", "issue", "edit", "243"]
    )
    self.assertEqual(os.path.abspath(mock_run.call_args[1]["cwd"]), abs_repo)

  def _make_arg_builder(self, position, repo_root=None, lock_dir=None):
    """Return CLI arg builder placing optional flags before or after the subcommand.

    Args:
      position: Either 'pre' (before subcommand) or 'post' (after subcommand).
      repo_root: Optional repository root path flag value.
      lock_dir: Optional lock directory path flag value.

    Returns:
      Callable accepting subcommand arg list and returning full CLI args.
    """
    flags = []
    if repo_root is not None:
      flags.extend(["--repo-root", repo_root])
    if lock_dir is not None:
      flags.extend(["--lock-dir", lock_dir])

    def _build(sub_args):
      if position == "pre":
        return [*flags, *sub_args]
      return [*sub_args, *flags]

    return _build

  def test_initial_repo_root_matches_cwd_gh243(self):
    """WorkspaceMutexScript initializes self.repo_root from os.getcwd() (GH-243)."""
    with patch("os.getcwd", return_value=self.cwd_repo):
      script = WorkspaceMutexScript()
    self.assertEqual(script.repo_root, os.path.abspath(self.cwd_repo))
    self.assertNotEqual(script.repo_root, _REPO_ROOT)

  def test_pre_and_post_subcommand_repo_root_all_subcommands_gh243(self):
    """Pre- and post-subcommand --repo-root target the specified repo across all 6 subcommands (GH-243)."""
    cwd_sentinel = WorkspaceMutex(repo_root=self.cwd_repo, lock_dir=self.locks_dir)
    cwd_res = cwd_sentinel.acquire(
        ticket_id="GH-243",
        workspace_path=self.cwd_repo,
        branch_name="feat/cwd-sentinel",
        pid=os.getpid(),
    )
    self.assertTrue(cwd_res["acquired"])

    for mode, expected_repo in (
        ("pre", self.target_repo),
        ("post", self.post_repo),
    ):
      with self.subTest(mode=mode):
        self._assert_lock_lifecycle(
            build_args=self._make_arg_builder(
                mode, repo_root=expected_repo, lock_dir=self.locks_dir
            ),
            expected_repo=expected_repo,
            expected_lock_dir=self.locks_dir,
            ticket_id="GH-243",
            untouched_lock_dirs=(self.env_locks_dir,),
        )
        self._assert_remote_gh_subcommands(
            build_args=self._make_arg_builder(mode, repo_root=expected_repo),
            expected_repo=expected_repo,
        )
        self.assertTrue(os.path.isdir(cwd_res["lock_path"]))
        self.assertEqual(
            self._read_session_json(cwd_res["lock_path"])["repo_root"],
            os.path.abspath(self.cwd_repo),
        )
        self.assertEqual(os.listdir(self.env_locks_dir), [])

  def test_omitted_repo_root_defaults_to_cwd_all_subcommands_gh243(self):
    """Omitting --repo-root defaults to os.getcwd() across all 6 subcommands (GH-243)."""
    self._assert_lock_lifecycle(
        build_args=lambda sub: list(sub),
        expected_repo=self.cwd_repo,
        expected_lock_dir=self.env_locks_dir,
        ticket_id="GH-244",
        untouched_lock_dirs=(self.locks_dir,),
    )
    self._assert_remote_gh_subcommands(
        build_args=lambda sub: list(sub),
        expected_repo=self.cwd_repo,
    )

  def test_pre_and_post_subcommand_lock_dir_gh243(self):
    """Pre- and post-subcommand --lock-dir honor target lock directory over WORKFLOW_LOCK_DIR (GH-243)."""
    for mode in ("pre", "post"):
      with self.subTest(mode=mode):
        mode_repo = os.path.join(self.temp_dir.name, f"lock_{mode}_repo")
        mode_locks = os.path.join(self.temp_dir.name, f"lock_{mode}_dir")
        os.makedirs(mode_repo, exist_ok=True)
        os.makedirs(mode_locks, exist_ok=True)

        env_sentinel = WorkspaceMutex(
            repo_root=mode_repo, lock_dir=self.env_locks_dir
        )
        env_res = env_sentinel.acquire(
            ticket_id="GH-246",
            workspace_path=mode_repo,
            branch_name="env-sentinel",
            pid=os.getpid(),
        )
        self.assertTrue(env_res["acquired"])

        self._assert_lock_lifecycle(
            build_args=self._make_arg_builder(
                mode, repo_root=mode_repo, lock_dir=mode_locks
            ),
            expected_repo=mode_repo,
            expected_lock_dir=mode_locks,
            ticket_id="GH-246",
            branch_name=f"target-{mode}",
        )
        self.assertTrue(os.path.isdir(env_res["lock_path"]))
        self.assertEqual(
            self._read_session_json(env_res["lock_path"])["branch_name"],
            "env-sentinel",
        )
        env_sentinel.release("GH-246")

    mixed_repo = os.path.join(self.temp_dir.name, "lock_mixed_repo")
    mixed_locks = os.path.join(self.temp_dir.name, "lock_mixed_dir")
    os.makedirs(mixed_repo, exist_ok=True)
    os.makedirs(mixed_locks, exist_ok=True)
    self._assert_lock_lifecycle(
        build_args=lambda sub: [
            "--lock-dir",
            mixed_locks,
            *sub,
            "--repo-root",
            mixed_repo,
        ],
        expected_repo=mixed_repo,
        expected_lock_dir=mixed_locks,
        ticket_id="GH-246",
        branch_name="target-mixed",
        untouched_lock_dirs=(self.env_locks_dir,),
    )

  def test_subcommand_repo_root_and_lock_dir_precedence_gh243(self):
    """Subcommand --repo-root and --lock-dir override parent flags without clobbering (GH-243)."""
    parent_repo_sentinel = WorkspaceMutex(
        repo_root=self.other_repo, lock_dir=self.locks_dir
    ).acquire(
        ticket_id="GH-247",
        workspace_path=self.other_repo,
        branch_name="parent-repo-sentinel",
        pid=os.getpid(),
    )
    self.assertTrue(parent_repo_sentinel["acquired"])

    self._assert_lock_lifecycle(
        build_args=lambda sub: [
            "--repo-root",
            self.other_repo,
            *sub,
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.locks_dir,
        ],
        expected_repo=self.target_repo,
        expected_lock_dir=self.locks_dir,
        ticket_id="GH-247",
        branch_name="sub-repo-wins",
        untouched_lock_dirs=(self.env_locks_dir, self.parent_locks_dir),
    )
    self._assert_remote_gh_subcommands(
        build_args=lambda sub: [
            "--repo-root",
            self.other_repo,
            *sub,
            "--repo-root",
            self.target_repo,
        ],
        expected_repo=self.target_repo,
    )
    self.assertTrue(os.path.isdir(parent_repo_sentinel["lock_path"]))
    self.assertEqual(
        self._read_session_json(parent_repo_sentinel["lock_path"])[
            "branch_name"
        ],
        "parent-repo-sentinel",
    )

    parent_lock_sentinel = WorkspaceMutex(
        repo_root=self.target_repo, lock_dir=self.parent_locks_dir
    ).acquire(
        ticket_id="GH-248",
        workspace_path=self.target_repo,
        branch_name="parent-lock-sentinel",
        pid=os.getpid(),
    )
    self.assertTrue(parent_lock_sentinel["acquired"])

    self._assert_lock_lifecycle(
        build_args=lambda sub: [
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.parent_locks_dir,
            *sub,
            "--lock-dir",
            self.locks_dir,
        ],
        expected_repo=self.target_repo,
        expected_lock_dir=self.locks_dir,
        ticket_id="GH-248",
        branch_name="sub-lock-wins",
        untouched_lock_dirs=(self.env_locks_dir,),
    )
    self.assertTrue(os.path.isdir(parent_lock_sentinel["lock_path"]))
    self.assertEqual(
        self._read_session_json(parent_lock_sentinel["lock_path"])[
            "branch_name"
        ],
        "parent-lock-sentinel",
    )

    reused = WorkspaceMutexScript()
    code_first, out_first, err_first, _ = self._run_cli(
        [
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.locks_dir,
            "status",
            "--ticket",
            "GH-249",
            "--json",
        ],
        script=reused,
    )
    self.assertEqual(code_first, 0, err_first)
    self.assertEqual(
        reused.repo_root,
        os.path.abspath(self.target_repo),
    )
    self.assertNotIn(
        self.default_repo_hash, json.loads(out_first)["lock_path"]
    )

    code_second, out_second, err_second, _ = self._run_cli(
        ["status", "--ticket", "GH-249", "--json"],
        script=reused,
    )
    self.assertEqual(code_second, 0, err_second)
    self.assertEqual(reused.repo_root, os.path.abspath(self.cwd_repo))
    self.assertIsNone(reused.lock_dir)
    self.assertIn(
        self._expected_repo_hash(self.cwd_repo),
        json.loads(out_second)["lock_path"],
    )


if __name__ == "__main__":
  unittest.main()
