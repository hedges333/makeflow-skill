#!/usr/bin/env python3
"""Hermetic unit and GNU Make integration tests for lib/state_machine.py (GH-219)."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

SESSION_START_SCRIPT = os.path.join(REPO_ROOT, "scripts", "session_start.py")
PRE_TOOL_SCRIPT = os.path.join(REPO_ROOT, "scripts", "pre_tool_guard.py")
WORKFLOW_GATES_SCRIPT = os.path.join(REPO_ROOT, "scripts", "workflow_gates.py")


def _load_script_module(name: str, path: str):
  """Loads a script file as an isolated Python module."""
  spec = importlib.util.spec_from_file_location(name, path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
  """Runs a local git command inside cwd."""
  return subprocess.run(
      ["git", *args],
      cwd=cwd,
      capture_output=True,
      text=True,
      check=True,
  )


def _init_git_repo(repo_dir: str, branch: str = "feat/219-test") -> None:
  """Initializes a hermetic local Git repository with one initial commit."""
  _git(repo_dir, "init", "-q")
  _git(repo_dir, "config", "user.email", "test@example.com")
  _git(repo_dir, "config", "user.name", "Makeflow Test")
  _git(repo_dir, "branch", "-M", "main")
  readme = os.path.join(repo_dir, "README.md")
  with open(readme, "w", encoding="utf-8") as f:
    f.write("# Test Repo\n")
  _git(repo_dir, "add", "README.md")
  _git(repo_dir, "commit", "-q", "-m", "chore: initial commit")
  if branch != "main":
    _git(repo_dir, "checkout", "-q", "-b", branch)


def _run_make(
    repo_dir: str, *make_args: str
) -> subprocess.CompletedProcess:
  """Executes GNU Make against .workflow/state/Makefile in repo_dir."""
  mk_path = os.path.join(repo_dir, ".workflow", "state", "Makefile")
  return subprocess.run(
      ["make", "-s", "-f", mk_path, f"PYTHON={sys.executable}", *make_args],
      cwd=repo_dir,
      capture_output=True,
      text=True,
      check=False,
  )


def _write_file(path: str, content: str) -> None:
  """Writes UTF-8 text to path, creating parent directories if needed."""
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w", encoding="utf-8") as f:
    f.write(content)


def _lock_approved_tree(repo_dir: str) -> str:
  """Stages tracked files and writes git write-tree to approved_tree.txt."""
  _git(repo_dir, "add", "-A")
  wt = _git(repo_dir, "write-tree").stdout.strip()
  _write_file(
      os.path.join(repo_dir, ".workflow", "state", "approved_tree.txt"),
      wt + "\n",
  )
  return wt


def _append_transcript_step(transcript_path: str, step: dict) -> None:
  """Appends one JSONL step to transcript_path."""
  os.makedirs(os.path.dirname(transcript_path), exist_ok=True)
  with open(transcript_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(step) + "\n")


class TestStateMachineArchitectureAndBootstrap(unittest.TestCase):
  """Tests lib/state_machine.py exports, init-makefile exclude, and SessionStart."""

  def test_state_machine_constants_and_library_hygiene(self) -> None:
    """Verifies canonical DAG constants, zero shebang, and MCP re-exports."""
    sm_path = os.path.join(REPO_ROOT, "lib", "state_machine.py")
    self.assertTrue(os.path.isfile(sm_path))
    with open(sm_path, "r", encoding="utf-8") as f:
      first_line = f.readline()
    self.assertFalse(first_line.startswith("#!"))

    from lib import state_machine
    import mcp.server as mcp_server

    self.assertEqual(len(state_machine.ALL_TARGETS), 14)
    self.assertEqual(
        state_machine.TARGET_ID_TO_NAME["T90"],
        "codified-scars-consolidated",
    )
    self.assertEqual(state_machine.TARGET_NAME_TO_ID["finish"], "T90")
    self.assertEqual(
        state_machine.VALID_TRANSITIONS["T60"], {"T70", "T40", "T30"}
    )
    self.assertIs(mcp_server.VALID_TRANSITIONS, state_machine.VALID_TRANSITIONS)
    self.assertIs(mcp_server.ALL_TARGETS, state_machine.ALL_TARGETS)

  def test_init_makefile_configures_exclude_in_repo_and_worktree(self) -> None:
    """Verifies one-step init-makefile excludes workflow dirs and passes intake."""
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "main_repo")
      os.makedirs(repo)
      _init_git_repo(repo)
      lock_dir = os.path.join(tmp, "locks")

      rc1 = WorkflowGatesScript().execute(
          ["--repo-root", repo, "init-makefile"]
      )
      self.assertEqual(rc1, 0)
      rc2 = WorkflowGatesScript().execute(
          ["--repo-root", repo, "init-makefile"]
      )
      self.assertEqual(rc2, 0)

      excl_path = os.path.join(repo, ".git", "info", "exclude")
      with open(excl_path, "r", encoding="utf-8") as f:
        excl_lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
      self.assertEqual(excl_lines.count(".workflow/state/"), 1)
      self.assertEqual(excl_lines.count(".workflow/archive/"), 1)
      self.assertEqual(_git(repo, "status", "--porcelain").stdout.strip(), "")

      intake_proc = subprocess.run(
          [
              sys.executable,
              WORKFLOW_GATES_SCRIPT,
              "--repo-root",
              repo,
              "--lock-dir",
              lock_dir,
              "intake",
              "--ticket",
              "LOCAL-1",
          ],
          cwd=repo,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(intake_proc.returncode, 0, intake_proc.stderr)
      WorkflowGatesScript().execute(
          ["--repo-root", repo, "--lock-dir", lock_dir, "release-intake"]
      )

      wt_dir = os.path.join(tmp, "linked_wt")
      _git(repo, "worktree", "add", "-q", "-b", "feat/local-2-wt", wt_dir, "main")
      self.assertTrue(os.path.isfile(os.path.join(wt_dir, ".git")))
      wt_excl_proc = _git(wt_dir, "rev-parse", "--git-path", "info/exclude")
      self.assertEqual(wt_excl_proc.returncode, 0)
      wt_excl_raw = wt_excl_proc.stdout.strip()
      wt_excl_path = (
          wt_excl_raw
          if os.path.isabs(wt_excl_raw)
          else os.path.join(wt_dir, wt_excl_raw)
      )
      with open(wt_excl_path, "w", encoding="utf-8") as f:
        f.write("")
      ign_before = subprocess.run(
          ["git", "check-ignore", "-q", ".workflow/state/"],
          cwd=wt_dir,
          check=False,
      )
      self.assertNotEqual(ign_before.returncode, 0)

      rc_wt = WorkflowGatesScript().execute(
          ["--repo-root", wt_dir, "init-makefile"]
      )
      self.assertEqual(rc_wt, 0)
      self.assertEqual(_git(wt_dir, "status", "--porcelain").stdout.strip(), "")
      for wf_rel in (".workflow/state/", ".workflow/archive/"):
        ign = subprocess.run(
            ["git", "check-ignore", "-q", wf_rel],
            cwd=wt_dir,
            check=False,
        )
        self.assertEqual(ign.returncode, 0, wf_rel)

  def test_session_start_refreshes_only_ignored_makeflow_workspaces(
      self,
  ) -> None:
    """Verifies SessionStart writes Makefile only when .workflow/state/ is ignored."""
    from lib.workflow_gates import (
        ensure_git_exclude_workflow_dirs,
        refresh_state_makefile_if_ignored,
    )

    with tempfile.TemporaryDirectory() as tmp:
      plain_repo = os.path.join(tmp, "plain")
      os.makedirs(plain_repo)
      _init_git_repo(plain_repo)
      self.assertFalse(refresh_state_makefile_if_ignored(plain_repo))
      subprocess.run(
          [sys.executable, SESSION_START_SCRIPT],
          input=json.dumps({"workspacePaths": [plain_repo]}),
          text=True,
          capture_output=True,
          check=True,
      )
      self.assertFalse(os.path.exists(os.path.join(plain_repo, ".workflow")))
      self.assertEqual(
          _git(plain_repo, "status", "--porcelain").stdout.strip(), ""
      )

      unignored_repo = os.path.join(tmp, "unignored")
      os.makedirs(unignored_repo)
      _init_git_repo(unignored_repo)
      os.makedirs(os.path.join(unignored_repo, ".workflow"))
      self.assertFalse(refresh_state_makefile_if_ignored(unignored_repo))
      self.assertFalse(
          os.path.exists(
              os.path.join(unignored_repo, ".workflow", "state", "Makefile")
          )
      )

      mf_repo = os.path.join(tmp, "mf_repo")
      os.makedirs(mf_repo)
      _init_git_repo(mf_repo)
      os.makedirs(os.path.join(mf_repo, ".workflow"))
      ensure_git_exclude_workflow_dirs(mf_repo)
      mk_file = os.path.join(mf_repo, ".workflow", "state", "Makefile")
      _write_file(mk_file, "# stale makefile\n")

      subprocess.run(
          [sys.executable, SESSION_START_SCRIPT],
          input=json.dumps({"workspacePaths": [mf_repo]}),
          text=True,
          capture_output=True,
          check=True,
      )
      with open(mk_file, "r", encoding="utf-8") as f:
        mk_content = f.read()
      self.assertIn("codified-scars-consolidated:", mk_content)
      self.assertIn("adversarial-review: check-approved-tree", mk_content)
      self.assertEqual(
          _git(mf_repo, "status", "--porcelain").stdout.strip(), ""
      )


class TestFullMakefileDagLifecycleAndReaders(unittest.TestCase):
  """Tests end-to-end GNU Make execution across all 15 targets and readers."""

  def _prepare_ticket_cards(self, state_dir: str) -> None:
    """Writes initial 10_ticket.md containing uppercase COMPLETE to test sanitization."""
    _write_file(
        os.path.join(state_dir, "10_ticket.md"),
        "# 10_ticket: GH-219\n\n"
        "- **Ticket ID**: GH-219\n"
        "- **Title**: [FEAT] Execute COMPLETE Makefile DAG transitions\n"
        "- **URL**: https://github.com/hedges333/makeflow-skill/issues/219\n",
    )

  def _assert_active_readers(self, repo: str, state_dir: str) -> str:
    """Verifies 99_next_action.md has zero COMPLETE and active session/ticket."""
    from lib.config import is_ticket_active
    from lib.terminal_title import detect_issue_tag, detect_ticket_id
    from lib.workspace_mutex import WorkspaceMutex

    na_path = os.path.join(state_dir, "99_next_action.md")
    with open(na_path, "r", encoding="utf-8") as f:
      content = f.read()
    self.assertNotIn("COMPLETE", content)
    self.assertTrue(is_ticket_active(state_dir))
    self.assertTrue(
        WorkspaceMutex(repo).is_session_active(
            {"pid": None, "ticket_id": "GH-219", "workspace_path": repo}
        )
    )
    self.assertEqual(detect_ticket_id(repo_root=repo), "GH-219")
    self.assertTrue(bool(detect_issue_tag(repo_root=repo)))
    return content

  def test_full_15_target_make_execution_and_archive_finish(self) -> None:
    """Runs all 15 Makefile targets end-to-end with archive card cleanup."""
    from lib.config import is_ticket_active
    from lib.state_machine import read_active_target
    from lib.target90_guard import is_target_90_state_dir
    from lib.workflow_gates import WorkflowGatesScript
    from lib.workspace_mutex import WorkspaceMutex

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "repo")
      os.makedirs(repo)
      remote = os.path.join(tmp, "github.com", "hedges333", "repo.git")
      os.makedirs(remote)
      _git(remote, "init", "--bare", "-q", "-b", "main")
      _init_git_repo(repo, branch="main")
      _git(repo, "remote", "add", "origin", remote)
      _git(repo, "push", "-q", "-u", "origin", "main")
      _git(repo, "checkout", "-q", "-b", "feat/219-makefile-dag")
      _write_file(
          os.path.join(repo, ".workflow", "config.json"),
          json.dumps({"state": {"completedCardAction": "archive"}}),
      )
      _git(repo, "add", ".workflow/config.json")
      _git(repo, "commit", "-q", "-m", "chore: add workflow config")

      self.assertEqual(
          WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"]),
          0,
      )
      state_dir = os.path.join(repo, ".workflow", "state")
      mk_path = os.path.join(state_dir, "Makefile")
      with open(mk_path, "rb") as f:
        initial_mk_bytes = f.read()

      transcript = os.path.join(tmp, "transcript.jsonl")
      step_no = 1

      def human_turn(text: str = "Approved.") -> None:
        nonlocal step_no
        _append_transcript_step(
            transcript,
            {"step_index": step_no, "type": "USER_INPUT", "content": text},
        )
        step_no += 1

      human_turn("Start workflow")
      self.assertEqual(_run_make(repo, "read-init-d").returncode, 0)
      self._prepare_ticket_cards(state_dir)
      self.assertEqual(_run_make(repo, "ticket-assigned").returncode, 0)
      self._assert_active_readers(repo, state_dir)

      _write_file(
          os.path.join(state_dir, "15_archaeology_receipt.md"),
          "# Receipt\nHOSTING_MODE: GITHUB\n",
      )
      self.assertEqual(_run_make(repo, "archaeology-verified").returncode, 0)
      _write_file(os.path.join(state_dir, "20_plan.md"), "# Plan\n")
      self.assertEqual(
          _run_make(repo, "approved-plan", f"TRANSCRIPT={transcript}").returncode,
          0,
      )
      na_t20 = self._assert_active_readers(repo, state_dir)
      self.assertIn("Gate Status: AWAITING_DECLARATION", na_t20)

      human_turn("Plan approved")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")
      self.assertEqual(
          _run_make(
              repo,
              "implementation-diff",
              "APPROVED=1",
              f"TRANSCRIPT={transcript}",
          ).returncode,
          0,
      )

      _write_file(os.path.join(repo, "feature.py"), "VALUE = 1\n")
      self.assertEqual(
          _run_make(repo, "verify-regression", "REGRESSION_CMD=true").returncode,
          0,
      )
      _lock_approved_tree(repo)
      self.assertEqual(_run_make(repo, "adversarial-review").returncode, 0)

      _write_file(os.path.join(state_dir, "40_critic_review.md"), "BLOCKER 1\n")
      self.assertEqual(
          _run_make(
              repo,
              "triaged-blockers",
              "BLOCKERS=1",
              f"TRANSCRIPT={transcript}",
          ).returncode,
          0,
      )
      human_turn("Fix blocker")
      self.assertEqual(
          _run_make(
              repo,
              "implementation-diff",
              "APPROVED=1",
              f"TRANSCRIPT={transcript}",
          ).returncode,
          0,
      )

      _write_file(os.path.join(repo, "feature.py"), "VALUE = 2\n")
      self.assertEqual(
          _run_make(repo, "verify-regression", "REGRESSION_CMD=true").returncode,
          0,
      )
      _lock_approved_tree(repo)
      self.assertEqual(_run_make(repo, "adversarial-review").returncode, 0)
      _write_file(os.path.join(state_dir, "40_critic_review.md"), "0 blockers\n")
      self.assertEqual(
          _run_make(
              repo,
              "approved-review",
              "BLOCKERS=0",
              f"TRANSCRIPT={transcript}",
          ).returncode,
          0,
      )

      human_turn("Review approved")
      _write_file(
          os.path.join(state_dir, "45_review_approved.md"), "APPROVED\n"
      )
      _write_file(
          os.path.join(state_dir, "50_commit_msg.txt"),
          "feat(state): execute real Makefile DAG transitions\n\n"
          "- feature.py: add state transition constant.\n\n"
          "Closes #219\n",
      )
      r_commit = _run_make(
          repo, "commit", "APPROVED=1", f"TRANSCRIPT={transcript}"
      )
      self.assertEqual(r_commit.returncode, 0, r_commit.stderr)

      human_turn("Commit approved")
      _git(repo, "commit", "-q", "-F", os.path.join(state_dir, "50_commit_msg.txt"))
      r_push = _run_make(
          repo, "push", "APPROVED=1", "NO_FETCH=1", f"TRANSCRIPT={transcript}"
      )
      self.assertEqual(r_push.returncode, 0, r_push.stderr)

      human_turn("Push approved")
      self.assertEqual(
          _run_make(
              repo, "bot-quiescence", "APPROVED=1", f"TRANSCRIPT={transcript}"
          ).returncode,
          0,
      )
      _write_file(os.path.join(state_dir, "70_bot_triage.md"), "QUIESCENT\n")
      self.assertEqual(
          _run_make(repo, "ready-to-land", f"TRANSCRIPT={transcript}").returncode,
          0,
      )

      human_turn("Landing approved")
      self.assertEqual(
          _run_make(
              repo, "land", "APPROVED=1", f"TRANSCRIPT={transcript}"
          ).returncode,
          0,
      )

      self.assertEqual(
          _run_make(repo, "codified-scars-consolidated").returncode, 0
      )
      self._assert_active_readers(repo, state_dir)
      self.assertTrue(is_target_90_state_dir(state_dir))
      self.assertEqual(read_active_target(state_dir)["target"], "T90")

      _write_file(os.path.join(state_dir, "30_tdd_receipt.json"), "{}\n")
      _write_file(os.path.join(state_dir, "loaded_scars.json"), "[]\n")
      r_finish = _run_make(repo, "finish")
      self.assertEqual(r_finish.returncode, 0, r_finish.stderr)

      with open(mk_path, "rb") as f:
        self.assertEqual(f.read(), initial_mk_bytes)
      self.assertFalse(is_ticket_active(state_dir))
      self.assertFalse(
          WorkspaceMutex(repo).is_session_active(
              {"pid": None, "ticket_id": "GH-219", "workspace_path": repo}
          )
      )
      self.assertIsNone(read_active_target(state_dir))
      self.assertFalse(os.path.exists(os.path.join(state_dir, "10_ticket.md")))
      self.assertFalse(
          os.path.exists(os.path.join(state_dir, "approved_tree.txt"))
      )
      self.assertTrue(
          os.path.isfile(
              os.path.join(repo, ".workflow", "archive", "GH-219", "10_ticket.md")
          )
      )

  def test_local_ticket_without_url_and_delete_card_action(self) -> None:
    """Verifies no-URL ticket formatting and unlink/delete card cleanup on finish."""
    from lib.state_machine import advance_workflow_target
    from lib.terminal_title import detect_issue_tag, detect_ticket_id

    with tempfile.TemporaryDirectory() as tmp:
      _init_git_repo(tmp, branch="feat/local-7-cleanup")
      state_dir = os.path.join(tmp, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "10_ticket.md"),
          "# 10_ticket: LOCAL-7\n\n"
          "- **Ticket ID**: LOCAL-7\n"
          "- **Title**: Local offline cleanup task\n",
      )
      ok, msg = advance_workflow_target(
          "T00",
          "T80",
          workspace_dir=tmp,
          state_dir=state_dir,
          evidence={"skipMainFetch": True},
      )
      self.assertTrue(ok, msg)
      self.assertEqual(detect_ticket_id(repo_root=tmp), "LOCAL-7")
      self.assertIn("Local", detect_issue_tag(repo_root=tmp))

      ok_t90, _ = advance_workflow_target(
          "T80",
          "T90",
          workspace_dir=tmp,
          state_dir=state_dir,
          evidence={"skipMainFetch": True},
      )
      self.assertTrue(ok_t90)
      ok_fin, _ = advance_workflow_target(
          "T90",
          "T90",
          workspace_dir=tmp,
          state_dir=state_dir,
          evidence={"complete": True, "skipMainFetch": True},
      )
      self.assertTrue(ok_fin)
      self.assertFalse(os.path.exists(os.path.join(state_dir, "10_ticket.md")))
      self.assertFalse(os.path.exists(os.path.join(tmp, ".workflow", "archive")))

  def test_finalize_completed_ticket_gh235_resets_terminal_title(self) -> None:
    """GH-235: _finalize_completed_ticket resets terminal title for non-temp workspaces."""
    from lib.state_machine import _finalize_completed_ticket

    parent = tempfile.mkdtemp(prefix="makeflow_sm_finish_")
    self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
    ws = os.path.join(parent, "my-app")
    state_dir = os.path.join(ws, ".workflow", "state")
    _write_file(
        os.path.join(state_dir, "10_ticket.md"),
        "# 10_ticket: GH-235\n\n- **Ticket ID**: `GH-235`\n",
    )

    with mock.patch(
        "lib.terminal_title.set_terminal_title", return_value=(True, "my-app/")
    ) as mock_set:
      _finalize_completed_ticket(state_dir, ws, "T80")

    mock_set.assert_called_once_with(title="my-app/", repo_root=ws)
    with open(
        os.path.join(state_dir, "99_next_action.md"), "r", encoding="utf-8"
    ) as f:
      self.assertIn("Status: COMPLETE", f.read())

  def test_finalize_completed_ticket_gh235_skips_temporary_workspace(
      self,
  ) -> None:
    """GH-235: _finalize_completed_ticket never updates terminal title for temp workspaces."""
    from lib.state_machine import _finalize_completed_ticket

    with tempfile.TemporaryDirectory() as tmp:
      ws = os.path.join(tmp, "repo")
      state_dir = os.path.join(ws, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "10_ticket.md"),
          "# 10_ticket: GH-235\n\n- **Ticket ID**: `GH-235`\n",
      )

      with mock.patch(
          "lib.terminal_title.set_terminal_title"
      ) as mock_set:
        _finalize_completed_ticket(state_dir, ws, "T80")

      mock_set.assert_not_called()

  def test_finalize_completed_ticket_gh235_error_boundaries(self) -> None:
    """GH-235 / SCAR-TEST-24 / SCAR-AST-01: tolerates ImportError/OSError; propagates fatal bugs."""
    from lib.state_machine import _finalize_completed_ticket

    parent = tempfile.mkdtemp(prefix="makeflow_sm_err_")
    self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
    ws = os.path.join(parent, "my-app")
    state_dir = os.path.join(ws, ".workflow", "state")
    _write_file(
        os.path.join(state_dir, "10_ticket.md"),
        "# 10_ticket: GH-235\n\n- **Ticket ID**: `GH-235`\n",
    )

    # 1. Tolerates ImportError
    with mock.patch.dict(sys.modules, {"lib.terminal_title": None}):
      _finalize_completed_ticket(state_dir, ws, "T80")

    # 2. Tolerates OSError
    with mock.patch(
        "lib.terminal_title.set_terminal_title",
        side_effect=OSError("TTY unavailable"),
    ):
      _finalize_completed_ticket(state_dir, ws, "T80")

    # 3. Propagates TypeError and RuntimeError
    for fatal_exc in (
        TypeError("Synthetic TypeError"),
        RuntimeError("Synthetic RuntimeError"),
    ):
      with mock.patch(
          "lib.terminal_title.set_terminal_title",
          side_effect=fatal_exc,
      ):
        with self.assertRaises(type(fatal_exc)):
          _finalize_completed_ticket(state_dir, ws, "T80")

  def test_backtick_ticket_id_bracket_title_roundtrip_and_release_intake(
      self,
  ) -> None:
    """Verifies backticked IDs, bracketed titles, URL round-trip, and NBO-1."""
    from lib.state_machine import advance_workflow_target, read_active_target
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "repo")
      os.makedirs(repo)
      _init_git_repo(repo, branch="feat/219-roundtrip")
      state_dir = os.path.join(repo, ".workflow", "state")
      lock_dir = os.path.join(tmp, "locks")
      url = "https://github.com/hedges333/makeflow-skill/issues/219"
      _write_file(
          os.path.join(state_dir, "10_ticket.md"),
          "# 10_ticket: `GH-219`\n\n"
          "- **Ticket ID**: `GH-219`\n"
          "- **Title**: [FEAT] Execute [DAG] transitions\n"
          f"- **URL**: {url}\n",
      )
      ok1, msg1 = advance_workflow_target(
          "T00",
          "T10",
          workspace_dir=repo,
          state_dir=state_dir,
          evidence={"skipMainFetch": True},
      )
      self.assertTrue(ok1, msg1)
      expected_bullet = (
          f"- Ticket: [GH-219: [FEAT] Execute [DAG] transitions]({url})"
          " (In Progress)"
      )
      with open(
          os.path.join(state_dir, "99_next_action.md"), "r", encoding="utf-8"
      ) as f:
        self.assertIn(expected_bullet, f.read())

      # Remove 10_ticket.md while keeping an active card so _NA_TICKET_RE round-trips
      os.unlink(os.path.join(state_dir, "10_ticket.md"))
      _write_file(
          os.path.join(state_dir, "15_archaeology_receipt.md"), "# Receipt\n"
      )
      _write_file(os.path.join(state_dir, "20_plan.md"), "# Plan\n")
      ok2, msg2 = advance_workflow_target(
          "T15",
          "T20",
          workspace_dir=repo,
          state_dir=state_dir,
          evidence={"skipMainFetch": True},
      )
      self.assertTrue(ok2, msg2)
      with open(
          os.path.join(state_dir, "99_next_action.md"), "r", encoding="utf-8"
      ) as f:
        self.assertIn(expected_bullet, f.read())

      # NBO-1: release-intake preserves active_target.json while ticket is active
      self.assertEqual(
          WorkflowGatesScript().execute(
              ["--repo-root", repo, "--lock-dir", lock_dir, "release-intake"]
          ),
          0,
      )
      self.assertEqual(read_active_target(state_dir)["target"], "T20")


class TestFalsificationPrerequisitesMcpAndHooks(unittest.TestCase):
  """Falsification tests for Make prerequisites, MCP delegation, and PreToolUse."""

  def test_makefile_and_generic_advance_prerequisite_reflexes(self) -> None:
    """Verifies exit code 2 and Vector 4 Reflex probes on gate violations."""
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmp:
      _init_git_repo(tmp)
      WorkflowGatesScript().execute(["--repo-root", tmp, "init-makefile"])
      state_dir = os.path.join(tmp, ".workflow", "state")

      bad_jump = _run_make(tmp, "advance", "FROM=T10", "TO=T50")
      self.assertEqual(bad_jump.returncode, 2)
      self.assertIn("?", bad_jump.stderr)

      no_card = _run_make(tmp, "archaeology-verified", "FROM=T10")
      self.assertEqual(no_card.returncode, 2)
      self.assertIn("10_ticket.md", no_card.stderr)

      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")
      no_tree_named = _run_make(tmp, "adversarial-review", "FROM=T30")
      self.assertEqual(no_tree_named.returncode, 2)
      self.assertIn("UNAPPROVED_TREE_DRIFT", no_tree_named.stderr)

      no_tree_generic = _run_make(tmp, "advance", "FROM=T30", "TO=T40")
      self.assertEqual(no_tree_generic.returncode, 2)
      self.assertIn("UNAPPROVED_TREE_DRIFT", no_tree_generic.stderr)

      _lock_approved_tree(tmp)
      _write_file(os.path.join(state_dir, "40_critic_review.md"), "Blockers\n")
      bad_t45 = _run_make(tmp, "approved-review", "FROM=T40", "BLOCKERS=2")
      self.assertEqual(bad_t45.returncode, 2)
      bad_t45_default = _run_make(tmp, "advance", "FROM=T40", "TO=T45")
      self.assertEqual(bad_t45_default.returncode, 2)
      bad_t42 = _run_make(tmp, "triaged-blockers", "FROM=T40", "BLOCKERS=0")
      self.assertEqual(bad_t42.returncode, 2)

      _write_file(os.path.join(state_dir, "45_review_approved.md"), "OK\n")
      _lock_approved_tree(tmp)
      no_msg = _run_make(tmp, "commit", "FROM=T45", "APPROVED=1")
      self.assertEqual(no_msg.returncode, 2)
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", no_msg.stderr)

      no_msg_generic = _run_make(
          tmp, "advance", "FROM=T45", "TO=T50", "APPROVED=1"
      )
      self.assertEqual(no_msg_generic.returncode, 2)
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", no_msg_generic.stderr)

      _write_file(os.path.join(state_dir, "15_archaeology_receipt.md"), "OK\n")
      _write_file(os.path.join(state_dir, "20_plan.md"), "Plan\n")
      self.assertEqual(
          _run_make(tmp, "approved-plan", "FROM=T15").returncode, 0
      )
      same_turn = _run_make(
          tmp, "implementation-diff", "FROM=T20", "APPROVED=1"
      )
      self.assertEqual(same_turn.returncode, 2)
      self.assertIn("SCAR-PROC-102", same_turn.stderr)

  def test_push_upstream_conflict_and_allow_main_conflict_override(
      self,
  ) -> None:
    """Verifies push blocks on upstream main conflict unless ALLOW_MAIN_CONFLICT=1."""
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmp:
      bare = os.path.join(tmp, "origin.git")
      clone1 = os.path.join(tmp, "clone1")
      clone2 = os.path.join(tmp, "clone2")
      os.makedirs(bare)
      os.makedirs(clone1)
      _git(bare, "init", "--bare", "-q")
      _init_git_repo(clone1, branch="main")
      _git(clone1, "remote", "add", "origin", bare)
      _git(clone1, "push", "-q", "-u", "origin", "main")
      _git(clone1, "checkout", "-q", "-b", "feat/219-conflict")

      _git(tmp, "clone", "-q", "-b", "main", bare, clone2)
      _git(clone2, "config", "user.email", "c2@example.com")
      _git(clone2, "config", "user.name", "Clone Two")
      _write_file(os.path.join(clone2, "README.md"), "# Upstream Conflict\n")
      _git(clone2, "add", "README.md")
      _git(clone2, "commit", "-q", "-m", "feat: upstream edit")
      _git(clone2, "push", "-q", "origin", "main")

      _write_file(os.path.join(clone1, "README.md"), "# Local Conflict\n")
      _git(clone1, "add", "README.md")
      _git(clone1, "commit", "-q", "-m", "feat: local edit")
      _git(clone1, "fetch", "-q", "origin", "main")

      WorkflowGatesScript().execute(["--repo-root", clone1, "init-makefile"])
      blocked_push = _run_make(
          clone1, "push", "FROM=T50", "APPROVED=1", "NO_FETCH=1"
      )
      self.assertEqual(blocked_push.returncode, 2)
      self.assertIn("UPSTREAM_MAIN_CONFLICT", blocked_push.stderr)

      override_push = _run_make(
          clone1,
          "push",
          "FROM=T50",
          "APPROVED=1",
          "NO_FETCH=1",
          "ALLOW_MAIN_CONFLICT=1",
      )
      self.assertEqual(override_push.returncode, 0, override_push.stderr)

  def test_mcp_workflow_advance_target_executes_real_make_when_present(
      self,
  ) -> None:
    """Verifies MCP workflow_advance_target runs GNU Make prerequisites on disk."""
    from lib.state_machine import read_active_target
    from lib.workflow_gates import WorkflowGatesScript
    import mcp.server as mcp_server

    with tempfile.TemporaryDirectory() as tmp:
      _init_git_repo(tmp, branch="feat/219-mcp-make")
      WorkflowGatesScript().execute(["--repo-root", tmp, "init-makefile"])
      state_dir = os.path.join(tmp, ".workflow", "state")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")

      blocked_t40 = mcp_server.handle_tools_call(
          1,
          "workflow_advance_target",
          {
              "currentTarget": "T30",
              "nextTarget": "T40",
              "workspaceDir": tmp,
          },
      )
      self.assertTrue(blocked_t40["result"].get("isError"))
      self.assertIn(
          "UNAPPROVED_TREE_DRIFT", blocked_t40["result"]["content"][0]["text"]
      )

      _lock_approved_tree(tmp)
      blocked_t40_reg = mcp_server.handle_tools_call(
          2,
          "workflow_advance_target",
          {
              "currentTarget": "T30",
              "nextTarget": "T40",
              "workspaceDir": tmp,
          },
      )
      self.assertTrue(blocked_t40_reg["result"].get("isError"))
      self.assertIn(
          "MISSING_REGRESSION_RECEIPT",
          blocked_t40_reg["result"]["content"][0]["text"],
      )

      r_reg = _run_make(tmp, "verify-regression", "REGRESSION_CMD=true")
      self.assertEqual(r_reg.returncode, 0, r_reg.stderr)

      ok_t40 = mcp_server.handle_tools_call(
          2,
          "workflow_advance_target",
          {
              "currentTarget": "T30",
              "nextTarget": "T40",
              "workspaceDir": tmp,
          },
      )
      self.assertFalse(ok_t40["result"].get("isError"), ok_t40)
      self.assertEqual(read_active_target(state_dir)["target"], "T40")
      self.assertTrue(
          os.path.isfile(os.path.join(state_dir, "99_next_action.md"))
      )

      _write_file(os.path.join(state_dir, "45_review_approved.md"), "OK\n")
      _lock_approved_tree(tmp)
      blocked_t50 = mcp_server.handle_tools_call(
          3,
          "workflow_advance_target",
          {
              "currentTarget": "T45",
              "nextTarget": "T50",
              "workspaceDir": tmp,
              "evidence": {"humanReviewApproved": True},
          },
      )
      self.assertTrue(blocked_t50["result"].get("isError"))
      self.assertIn(
          "INVALID_COMMIT_MESSAGE_CONTRACT",
          blocked_t50["result"]["content"][0]["text"],
      )

      _write_file(
          os.path.join(state_dir, "50_commit_msg.txt"),
          "feat(mcp): delegate advance to Makefile\n\n"
          "- mcp/server.py: invoke GNU Make when Makefile exists.\n\n"
          "Ticket: LOCAL-9\n",
      )
      _lock_approved_tree(tmp)
      ok_t50 = mcp_server.handle_tools_call(
          4,
          "workflow_advance_target",
          {
              "currentTarget": "T45",
              "nextTarget": "T50",
              "workspaceDir": tmp,
              "evidence": {"humanReviewApproved": True},
          },
      )
      self.assertFalse(ok_t50["result"].get("isError"), ok_t50)
      self.assertEqual(read_active_target(state_dir)["target"], "T50")

  def test_pre_tool_guard_shell_pause_gates_and_governance_protection(
      self,
  ) -> None:
    """Verifies PreToolUse guards Make advance commands and governance files."""
    from lib.turn_boundary import read_gate_entry, record_gate_entry
    guard = _load_script_module("pre_tool_guard_sm", PRE_TOOL_SCRIPT)

    self.assertIn("lib/state_machine.py", guard.GOVERNANCE_FILE_RELPATHS)
    self.assertIn(
        ".workflow/state/active_target.json", guard.GOVERNANCE_FILE_RELPATHS
    )

    for bad_shell in (
        "echo '{}' > .workflow/state/active_target.json",
        "rm -f lib/state_machine.py",
    ):
      safe, _ = guard.evaluate_self_modification_safety(bad_shell)
      self.assertFalse(safe, bad_shell)

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir)
      for gov_rel in (
          ".workflow/state/active_target.json",
          ".workflow/state/Makefile",
      ):
        wf_res = guard.evaluate_tool_call({
            "workspacePaths": [ws],
            "toolCall": {
                "name": "write_file",
                "args": {
                    "Path": os.path.join(ws, gov_rel),
                    "Content": "{}",
                },
            },
        })
        self.assertEqual(wf_res["decision"], "deny", gov_rel)

      transcript = os.path.join(ws, "transcript.jsonl")
      _append_transcript_step(
          transcript,
          {"step_index": 1, "type": "USER_INPUT", "content": "Plan this"},
      )

      bundled = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile approved-plan && "
                      "make -f .workflow/state/Makefile implementation-diff APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(bundled["decision"], "deny")
      self.assertIn("SCAR-PROC-102", bundled["reason"])

      bundled_git = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile commit FROM=T45 APPROVED=1 && "
                      "git commit -F .workflow/state/50_commit_msg.txt"
                  )
              },
          },
      })
      self.assertEqual(bundled_git["decision"], "deny")
      self.assertIn("SCAR-PROC-102", bundled_git["reason"])

      enter_res = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile approved-plan FROM=T15"
                  )
              },
          },
      })
      self.assertEqual(enter_res["decision"], "allow")
      entry_before = read_gate_entry(state_dir)
      self.assertEqual(entry_before["gate"], "T20")
      self.assertEqual(entry_before["transcriptPath"], transcript)

      record_gate_entry(state_dir, "T15", "T20", transcript_path="")
      entry_after_child = read_gate_entry(state_dir)
      self.assertEqual(entry_after_child["transcriptPath"], transcript)
      self.assertEqual(
          entry_after_child["userInputCountAtEntry"],
          entry_before["userInputCountAtEntry"],
      )

      same_turn_exit = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile implementation-diff APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(same_turn_exit["decision"], "deny")
      self.assertIn("SCAR-PROC-102", same_turn_exit["reason"])

      _append_transcript_step(
          transcript,
          {
              "step_index": 2,
              "type": "PLANNER_RESPONSE",
              "content": "Presenting plan",
              "tool_calls": [{
                  "name": "run_command",
                  "args": {
                      "CommandLine": (
                          "make -f .workflow/state/Makefile approved-plan FROM=T15"
                      )
                  },
              }],
          },
      )
      _append_transcript_step(
          transcript,
          {"step_index": 3, "type": "USER_INPUT", "content": "Approved"},
      )
      next_turn_exit = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile implementation-diff APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(next_turn_exit["decision"], "allow", next_turn_exit)

  def test_direct_file_governance_across_checkout_cache_and_consumer(
      self,
  ) -> None:
    """Verifies BLOCKER-1 write_file governance across clone, cache, and consumer."""
    guard = _load_script_module("pre_tool_guard_b1", PRE_TOOL_SCRIPT)

    with tempfile.TemporaryDirectory() as tmp:
      # 1. Standard .git-directory makeflow clone: deny inactive, allow active
      clone_dir = os.path.join(tmp, "makeflow_clone")
      os.makedirs(clone_dir)
      _init_git_repo(clone_dir)
      _write_file(
          os.path.join(clone_dir, "plugin.json"),
          json.dumps({"name": "makeflow", "version": "1.0.0"}),
      )
      inactive_res = guard.evaluate_tool_call({
          "workspacePaths": [clone_dir],
          "toolCall": {
              "name": "write_file",
              "args": {"Path": "lib/state_machine.py", "Content": "# x\n"},
          },
      })
      self.assertEqual(inactive_res["decision"], "deny")

      _write_file(
          os.path.join(clone_dir, ".workflow", "state", "10_ticket.md"),
          "# 10_ticket: GH-219\n",
      )
      active_res = guard.evaluate_tool_call({
          "workspacePaths": [clone_dir],
          "toolCall": {
              "name": "write_file",
              "args": {"Path": "lib/state_machine.py", "Content": "# x\n"},
          },
      })
      self.assertEqual(active_res["decision"], "allow", active_res)
      active_mk = guard.evaluate_tool_call({
          "workspacePaths": [clone_dir],
          "toolCall": {
              "name": "write_file",
              "args": {"Path": ".workflow/state/Makefile", "Content": "all:\n"},
          },
      })
      self.assertEqual(active_mk["decision"], "deny")

      # 2. Installed read-only plugin cache (no .git): always deny even if state active
      cache_dir = os.path.join(tmp, "plugin_cache")
      _write_file(
          os.path.join(cache_dir, "plugin.json"),
          json.dumps({"name": "makeflow", "version": "1.0.0"}),
      )
      _write_file(
          os.path.join(cache_dir, ".workflow", "state", "10_ticket.md"),
          "# 10_ticket: GH-219\n",
      )
      cache_res = guard.evaluate_tool_call({
          "workspacePaths": [cache_dir],
          "toolCall": {
              "name": "edit_file",
              "args": {
                  "Path": os.path.join(cache_dir, "lib", "state_machine.py")
              },
          },
      })
      self.assertEqual(cache_res["decision"], "deny")

      # 3. External consumer repo with .git/ directory: allow its own lib/config.py etc.
      consumer = os.path.join(tmp, "consumer_app")
      os.makedirs(consumer)
      _init_git_repo(consumer)
      for rel in ("lib/config.py", "lib/state_machine.py", "hooks.json"):
        cons_res = guard.evaluate_tool_call({
            "workspacePaths": [consumer],
            "toolCall": {
                "name": "write_file",
                "args": {"Path": rel, "Content": "# app\n"},
            },
        })
        self.assertEqual(cons_res["decision"], "allow", (rel, cons_res))

  def test_transcript_spaces_and_shell_metacharacters_not_executed(
      self,
  ) -> None:
    """Verifies BLOCKER-2 passes transcript paths safely via environment."""
    from lib.workflow_gates import WorkflowGatesScript, generate_state_makefile
    from mcp.server import handle_tools_call

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "repo")
      os.makedirs(repo)
      _init_git_repo(repo, branch="feat/219-env")
      WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"])
      mk_text = generate_state_makefile(repo)
      self.assertNotIn("--transcript $(TRANSCRIPT)", mk_text)
      self.assertNotIn("--ticket $(TICKET)", mk_text)

      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "15_archaeology_receipt.md"), "# Receipt\n"
      )
      _write_file(os.path.join(state_dir, "20_plan.md"), "# Plan\n")
      _write_file(os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n")

      pwned = os.path.join(tmp, "pwned_marker")
      evil_dir = os.path.join(
          tmp, f"dir with spaces $(touch {pwned}); echo hi"
      )
      transcript = os.path.join(evil_dir, "transcript.jsonl")
      _append_transcript_step(
          transcript,
          {"step_index": 1, "type": "USER_INPUT", "content": "Start"},
      )
      r_t20 = handle_tools_call(
          1,
          "workflow_advance_target",
          {
              "currentTarget": "T15",
              "nextTarget": "T20",
              "workspaceDir": repo,
              "transcriptPath": transcript,
              "evidence": {"skipMainFetch": True},
          },
      )
      self.assertFalse(r_t20["result"].get("isError"), r_t20)
      self.assertFalse(os.path.exists(pwned))

      _append_transcript_step(
          transcript,
          {"step_index": 2, "type": "USER_INPUT", "content": "Approved"},
      )
      r_t30 = handle_tools_call(
          2,
          "workflow_advance_target",
          {
              "currentTarget": "T20",
              "nextTarget": "T30",
              "workspaceDir": repo,
              "transcriptPath": transcript,
              "evidence": {"humanPlanApproved": True, "skipMainFetch": True},
          },
      )
      self.assertFalse(r_t30["result"].get("isError"), r_t30)
      self.assertFalse(os.path.exists(pwned))

  def test_strict_blockers_count_and_invalid_targets_never_raise(self) -> None:
    """Verifies BLOCKER-3 strict blockersCount and target validation without raising."""
    from lib.state_machine import advance_workflow_target, run_makefile_advance
    from lib.workflow_gates import WorkflowGatesScript
    from mcp.server import handle_tools_call

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "repo")
      os.makedirs(repo)
      _init_git_repo(repo, branch="feat/219-strict")
      WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"])
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "40_critic_review.md"), "1 blocker\n"
      )
      _write_file(os.path.join(repo, "app.py"), "X = 1\n")
      _lock_approved_tree(repo)

      for bad_val in (-1, False, True, None, "0", 1.5):
        for next_t in ("T45", "T42"):
          ok_adv, msg_adv = advance_workflow_target(
              "T40",
              next_t,
              evidence={"blockersCount": bad_val, "skipMainFetch": True},
              workspace_dir=repo,
              state_dir=state_dir,
          )
          self.assertFalse(ok_adv, (bad_val, next_t, msg_adv))
          self.assertIn("BLOCKED:", msg_adv)

          ok_mk, msg_mk = run_makefile_advance(
              "T40",
              next_t,
              evidence={"blockersCount": bad_val, "skipMainFetch": True},
              workspace_dir=repo,
              state_dir=state_dir,
          )
          self.assertFalse(ok_mk, (bad_val, next_t, msg_mk))
          self.assertIn("BLOCKED:", msg_mk)

      for bad_curr, bad_next in (("T99", "T10"), ("T10", "T99")):
        ok_bad, msg_bad = run_makefile_advance(
            bad_curr,
            bad_next,
            workspace_dir=repo,
            state_dir=state_dir,
        )
        self.assertFalse(ok_bad)
        self.assertIn("BLOCKED:", msg_bad)
        mcp_bad = handle_tools_call(
            3,
            "workflow_advance_target",
            {
                "currentTarget": bad_curr,
                "nextTarget": bad_next,
                "workspaceDir": repo,
            },
        )
        self.assertTrue(mcp_bad["result"].get("isError"))

      for cli_val in ("-1", "abc"):
        cli_proc = _run_make(
            repo, "approved-review", "FROM=T40", f"BLOCKERS={cli_val}"
        )
        self.assertEqual(cli_proc.returncode, 2, cli_proc.stderr)
        self.assertIn("[SCAR-ID:", cli_proc.stderr)

  def test_prefix_env_make_pause_gate_bundling_and_turn_boundary(self) -> None:
    """Verifies BLOCKER-4 prefix KEY=VALUE make detection and override precedence."""
    from lib.turn_boundary import read_gate_entry
    guard = _load_script_module("pre_tool_guard_b4", PRE_TOOL_SCRIPT)

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir)
      transcript = os.path.join(ws, "transcript.jsonl")
      _append_transcript_step(
          transcript,
          {"step_index": 1, "type": "USER_INPUT", "content": "Plan"},
      )

      for bundled_cmd in (
          "TO=T20 make -f .workflow/state/Makefile advance && "
          "TO=T30 APPROVED=1 make -f .workflow/state/Makefile advance",
          "TO=T50 FROM=T45 APPROVED=1 make -f .workflow/state/Makefile advance"
          ' && git commit -m "x"',
      ):
        res = guard.evaluate_tool_call({
            "transcriptPath": transcript,
            "workspacePaths": [ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": bundled_cmd},
            },
        })
        self.assertEqual(res["decision"], "deny", bundled_cmd)
        self.assertIn("SCAR-PROC-102", res["reason"])

      # Trailing Make argument overrides prefix env variable
      enter_cmd = "TO=T15 FROM=T15 make -f .workflow/state/Makefile advance TO=T20"
      enter_res = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {"name": "run_command", "args": {"CommandLine": enter_cmd}},
      })
      self.assertEqual(enter_res["decision"], "allow")
      self.assertEqual(read_gate_entry(state_dir)["gate"], "T20")
      self.assertEqual(read_gate_entry(state_dir)["transcriptPath"], transcript)

      same_turn = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "TO=T30 APPROVED=1 make -f .workflow/state/Makefile advance"
                  )
              },
          },
      })
      self.assertEqual(same_turn["decision"], "deny")

      _append_transcript_step(
          transcript,
          {"step_index": 2, "type": "USER_INPUT", "content": "Approved"},
      )
      next_turn = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "TO=T30 APPROVED=1 make -f .workflow/state/Makefile advance"
                  )
              },
          },
      })
      self.assertEqual(next_turn["decision"], "allow", next_turn)

  def test_brand_new_repo_intake_and_card_only_transitions_without_read_init_d(
      self,
  ) -> None:
    """Verifies Round 2 BLOCKER-1 card-only transitions without self-collision."""
    from lib.state_machine import infer_current_target, read_active_target
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "fresh_repo")
      os.makedirs(repo)
      _init_git_repo(repo, branch="main")
      remote = os.path.join(tmp, "github.com", "hedges333", "fresh.git")
      os.makedirs(remote)
      _git(remote, "init", "--bare", "-q", "-b", "main")
      _git(repo, "remote", "add", "origin", remote)
      _git(repo, "push", "-q", "-u", "origin", "main")

      self.assertEqual(
          WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"]),
          0,
      )
      _git(repo, "checkout", "-q", "-b", "feat/219-first-intake")
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "10_ticket.md"),
          "# 10_ticket: `GH-219`\n\n"
          "- **Ticket ID**: `GH-219`\n"
          "- **Title**: First intake without read-init-d\n",
      )
      self.assertEqual(infer_current_target(state_dir), "T10")
      r_t10 = _run_make(repo, "ticket-assigned", "NO_FETCH=1")
      self.assertEqual(r_t10.returncode, 0, r_t10.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T10")

      def strip_state_tracking() -> None:
        for fname in (
            "active_target.json",
            "99_next_action.md",
            "gate_entry.json",
        ):
          fpath = os.path.join(state_dir, fname)
          if os.path.exists(fpath):
            os.unlink(fpath)

      # T15 -> T20 with 20_plan.md already written on disk
      strip_state_tracking()
      _write_file(
          os.path.join(state_dir, "15_archaeology_receipt.md"), "# Receipt\n"
      )
      _write_file(os.path.join(state_dir, "20_plan.md"), "# Plan\n")
      self.assertEqual(infer_current_target(state_dir), "T20")
      r_t20 = _run_make(repo, "approved-plan", "NO_FETCH=1")
      self.assertEqual(r_t20.returncode, 0, r_t20.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T20")

      # T20 -> T30 with 20_plan_approved.md already written on disk
      strip_state_tracking()
      _write_file(
          os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n"
      )
      self.assertEqual(infer_current_target(state_dir), "T30")
      r_t30 = _run_make(
          repo, "implementation-diff", "APPROVED=1", "NO_FETCH=1"
      )
      self.assertEqual(r_t30.returncode, 0, r_t30.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T30")

      # T30 -> T40 even when prior round 40_critic_review.md exists
      strip_state_tracking()
      _write_file(
          os.path.join(state_dir, "40_critic_review.md"), "Round 1\n"
      )
      _write_file(os.path.join(repo, "mod.py"), "VAL = 1\n")
      self.assertEqual(
          _run_make(repo, "verify-regression", "REGRESSION_CMD=true").returncode,
          0,
      )
      _lock_approved_tree(repo)
      r_t40 = _run_make(repo, "adversarial-review", "NO_FETCH=1")
      self.assertEqual(r_t40.returncode, 0, r_t40.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T40")

      # T45 -> T50 with 45_review_approved.md and 50_commit_msg.txt on disk
      strip_state_tracking()
      _write_file(
          os.path.join(state_dir, "45_review_approved.md"), "APPROVED\n"
      )
      _write_file(
          os.path.join(state_dir, "50_commit_msg.txt"),
          "feat(state): support card-only inference\n\n"
          "- mod.py: add module constant.\n\n"
          "Closes #219\n",
      )
      self.assertEqual(infer_current_target(state_dir), "T50")
      r_t50 = _run_make(repo, "commit", "APPROVED=1", "NO_FETCH=1")
      self.assertEqual(r_t50.returncode, 0, r_t50.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T50")

  def test_denied_pre_tool_guard_never_mutates_gate_entry_on_disk(
      self,
  ) -> None:
    """Verifies Round 2 BLOCKER-2 Part A zero disk mutation on denied commands."""
    from lib.turn_boundary import record_gate_entry
    guard = _load_script_module("pre_tool_guard_r2a", PRE_TOOL_SCRIPT)

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir)
      transcript = os.path.join(ws, "transcript.jsonl")
      _append_transcript_step(
          transcript,
          {"step_index": 1, "type": "USER_INPUT", "content": "Start"},
      )

      # 1. Denied command when gate_entry.json does not exist must not create it
      res_reset = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile approved-plan FROM=T15"
                      " && git reset --hard"
                  )
              },
          },
      })
      self.assertEqual(res_reset["decision"], "deny")
      gate_file = os.path.join(state_dir, "gate_entry.json")
      self.assertFalse(os.path.exists(gate_file))

      # 2. Denied bundled git commit after T45 approval must leave gate_entry.json intact
      _write_file(
          os.path.join(state_dir, "45_review_approved.md"), "APPROVED\n"
      )
      record_gate_entry(state_dir, "T40", "T45", transcript_path=transcript)
      _append_transcript_step(
          transcript,
          {"step_index": 2, "type": "USER_INPUT", "content": "Review approved"},
      )
      with open(gate_file, "rb") as f:
        before_bytes = f.read()

      for bad_cmd in (
          "make -f .workflow/state/Makefile commit FROM=T45 APPROVED=1 && "
          "git commit -F .workflow/state/50_commit_msg.txt",
          "make -f .workflow/state/Makefile commit FROM=T45 APPROVED=1 && "
          "git reset --hard",
      ):
        denied = guard.evaluate_tool_call({
            "transcriptPath": transcript,
            "workspacePaths": [ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": bad_cmd},
            },
        })
        self.assertEqual(denied["decision"], "deny", bad_cmd)
        with open(gate_file, "rb") as f:
          self.assertEqual(f.read(), before_bytes, bad_cmd)

  def test_consecutive_pause_gates_pre_tool_guard_then_real_make_without_active_target(
      self,
  ) -> None:
    """Verifies Round 2 BLOCKER-2 Part B T45 -> T50 -> T60 PreToolUse + make."""
    from lib.state_machine import (
        RenderCardSpec,
        read_active_target,
        render_next_action_card,
    )
    from lib.turn_boundary import record_gate_entry
    from lib.workflow_gates import WorkflowGatesScript

    guard = _load_script_module("pre_tool_guard_r2b", PRE_TOOL_SCRIPT)

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "repo")
      os.makedirs(repo)
      remote = os.path.join(tmp, "github.com", "hedges333", "r2.git")
      os.makedirs(remote)
      _git(remote, "init", "--bare", "-q", "-b", "main")
      _init_git_repo(repo, branch="main")
      _git(repo, "remote", "add", "origin", remote)
      _git(repo, "push", "-q", "-u", "origin", "main")
      _git(repo, "checkout", "-q", "-b", "feat/219-r2-gates")

      WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"])
      state_dir = os.path.join(repo, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "10_ticket.md"),
          "# 10_ticket: `GH-219`\n\n- **Ticket ID**: `GH-219`\n",
      )
      _write_file(
          os.path.join(state_dir, "40_critic_review.md"), "0 blockers\n"
      )
      _write_file(
          os.path.join(state_dir, "45_review_approved.md"), "APPROVED\n"
      )
      _write_file(
          os.path.join(state_dir, "50_commit_msg.txt"),
          "feat(state): consecutive pause gate verification\n\n"
          "- app.py: add application constant.\n\n"
          "Closes #219\n",
      )
      _write_file(os.path.join(repo, "app.py"), "READY = True\n")
      _lock_approved_tree(repo)

      # Simulate upgraded in-flight workspace at T45 without active_target.json
      render_next_action_card(
          RenderCardSpec(
              state_dir=state_dir,
              active_target="T45",
              previous_target="T40",
              workspace_dir=repo,
          )
      )
      self.assertIsNone(read_active_target(state_dir))

      transcript = os.path.join(tmp, "transcript.jsonl")
      _append_transcript_step(
          transcript,
          {"step_index": 1, "type": "USER_INPUT", "content": "Start"},
      )
      _append_transcript_step(
          transcript,
          {
              "step_index": 2,
              "type": "PLANNER_RESPONSE",
              "tool_calls": [{
                  "name": "run_command",
                  "args": {
                      "CommandLine": (
                          "make -f .workflow/state/Makefile approved-review"
                          " BLOCKERS=0"
                      )
                  },
              }],
          },
      )
      record_gate_entry(state_dir, "T40", "T45", transcript_path=transcript)

      # Human approves T45, model emits T45 -> T50 command without FROM=
      _append_transcript_step(
          transcript,
          {"step_index": 3, "type": "USER_INPUT", "content": "Review approved"},
      )
      cmd_t50 = "make -f .workflow/state/Makefile commit APPROVED=1"
      _append_transcript_step(
          transcript,
          {
              "step_index": 4,
              "type": "PLANNER_RESPONSE",
              "tool_calls": [
                  {"name": "run_command", "args": {"CommandLine": cmd_t50}}
              ],
          },
      )
      g_t50 = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [repo],
          "toolCall": {"name": "run_command", "args": {"CommandLine": cmd_t50}},
      })
      self.assertEqual(g_t50["decision"], "allow", g_t50)
      r_t50 = _run_make(repo, "commit", "APPROVED=1", "NO_FETCH=1")
      self.assertEqual(r_t50.returncode, 0, r_t50.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T50")

      # Human approves T50, commit runs, then T50 -> T60 runs without FROM=
      _append_transcript_step(
          transcript,
          {"step_index": 5, "type": "USER_INPUT", "content": "Commit approved"},
      )
      _git(repo, "commit", "-q", "-F", os.path.join(state_dir, "50_commit_msg.txt"))
      cmd_t60 = "make -f .workflow/state/Makefile push APPROVED=1 NO_FETCH=1"
      _append_transcript_step(
          transcript,
          {
              "step_index": 6,
              "type": "PLANNER_RESPONSE",
              "tool_calls": [
                  {"name": "run_command", "args": {"CommandLine": cmd_t60}}
              ],
          },
      )
      g_t60 = guard.evaluate_tool_call({
          "transcriptPath": transcript,
          "workspacePaths": [repo],
          "toolCall": {"name": "run_command", "args": {"CommandLine": cmd_t60}},
      })
      self.assertEqual(g_t60["decision"], "allow", g_t60)
      r_t60 = _run_make(repo, "push", "APPROVED=1", "NO_FETCH=1")
      self.assertEqual(r_t60.returncode, 0, r_t60.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T60")

  def test_read_init_d_restores_active_session_and_rejects_explicit_from_reset(
      self,
  ) -> None:
    """Verifies Round 3 BLOCKER-1 non-destructive read-init-d on active state."""
    from lib.state_machine import read_active_target
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "repo")
      os.makedirs(repo)
      _init_git_repo(repo, branch="feat/219-restore")
      WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"])
      state_dir = os.path.join(repo, ".workflow", "state")
      self._prepare_ticket_cards_standalone(state_dir)

      self.assertEqual(_run_make(repo, "ticket-assigned", "NO_FETCH=1").returncode, 0)
      _write_file(
          os.path.join(state_dir, "15_archaeology_receipt.md"), "# Receipt\n"
      )
      self.assertEqual(
          _run_make(repo, "archaeology-verified", "NO_FETCH=1").returncode, 0
      )
      os.unlink(os.path.join(state_dir, "active_target.json"))

      r_restore_t15 = _run_make(repo, "read-init-d", "NO_FETCH=1")
      self.assertEqual(r_restore_t15.returncode, 0, r_restore_t15.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T15")
      with open(
          os.path.join(state_dir, "99_next_action.md"), "r", encoding="utf-8"
      ) as f:
        self.assertIn("Active Target: Target 15", f.read())

      transcript = os.path.join(tmp, "transcript.jsonl")
      _append_transcript_step(
          transcript,
          {"step_index": 1, "type": "USER_INPUT", "content": "Plan"},
      )
      _write_file(os.path.join(state_dir, "20_plan.md"), "# Plan\n")
      self.assertEqual(
          _run_make(
              repo, "approved-plan", "NO_FETCH=1", f"TRANSCRIPT={transcript}"
          ).returncode,
          0,
      )
      gate_path = os.path.join(state_dir, "gate_entry.json")
      with open(gate_path, "rb") as f:
        gate_bytes = f.read()
      os.unlink(os.path.join(state_dir, "active_target.json"))

      r_restore_t20 = _run_make(repo, "read-init-d", "NO_FETCH=1")
      self.assertEqual(r_restore_t20.returncode, 0, r_restore_t20.stderr)
      self.assertEqual(read_active_target(state_dir)["target"], "T20")
      with open(gate_path, "rb") as f:
        self.assertEqual(f.read(), gate_bytes)

      _write_file(
          os.path.join(state_dir, "20_plan_approved.md"), "APPROVED\n"
      )
      same_turn = _run_make(
          repo,
          "implementation-diff",
          "APPROVED=1",
          "NO_FETCH=1",
          f"TRANSCRIPT={transcript}",
      )
      self.assertEqual(same_turn.returncode, 2)

      explicit_from = _run_make(
          repo, "read-init-d", "FROM=T20", "NO_FETCH=1"
      )
      self.assertEqual(explicit_from.returncode, 2)

  @staticmethod
  def _prepare_ticket_cards_standalone(state_dir: str) -> None:
    """Writes a minimal 10_ticket.md card into state_dir."""
    _write_file(
        os.path.join(state_dir, "10_ticket.md"),
        "# 10_ticket: `GH-219`\n\n"
        "- **Ticket ID**: `GH-219`\n"
        "- **Title**: Active restore verification\n",
    )

  def test_pre_tool_guard_invalid_edges_cli_variants_and_unwitnessed_entry(
      self,
  ) -> None:
    """Verifies Round 3 BLOCKER-2 gate_entry protection and CLI extraction."""
    from lib.turn_boundary import read_gate_entry, record_gate_entry
    guard = _load_script_module("pre_tool_guard_r3b2", PRE_TOOL_SCRIPT)

    with tempfile.TemporaryDirectory() as tmp:
      # 1. Invalid edges and unapproved exits must not mutate gate_entry.json
      ws1 = os.path.join(tmp, "ws1")
      st1 = os.path.join(ws1, ".workflow", "state")
      os.makedirs(st1)
      tr1 = os.path.join(ws1, "transcript.jsonl")
      _append_transcript_step(
          tr1, {"step_index": 1, "type": "USER_INPUT", "content": "Go"}
      )
      record_gate_entry(st1, "T15", "T20", transcript_path=tr1)
      gate_file1 = os.path.join(st1, "gate_entry.json")
      with open(gate_file1, "rb") as f:
        t20_bytes = f.read()

      for noop_cmd in (
          "make -f .workflow/state/Makefile ticket-assigned",
          "make -f .workflow/state/Makefile approved-review",
          "make -f .workflow/state/Makefile read-init-d",
      ):
        guard.evaluate_tool_call({
            "transcriptPath": tr1,
            "workspacePaths": [ws1],
            "toolCall": {"name": "run_command", "args": {"CommandLine": noop_cmd}},
        })
        with open(gate_file1, "rb") as f:
          self.assertEqual(f.read(), t20_bytes, noop_cmd)

      record_gate_entry(st1, "T40", "T45", transcript_path=tr1)
      _append_transcript_step(
          tr1, {"step_index": 2, "type": "USER_INPUT", "content": "Approved"}
      )
      with open(gate_file1, "rb") as f:
        t45_bytes = f.read()
      guard.evaluate_tool_call({
          "transcriptPath": tr1,
          "workspacePaths": [ws1],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "make -f .workflow/state/Makefile commit"},
          },
      })
      with open(gate_file1, "rb") as f:
        self.assertEqual(f.read(), t45_bytes)

      # 2. --flag=value, -m scripts.workflow_gates --make-env, and .workflow/./state/Makefile
      for idx, enter_t60_cmd in enumerate((
          f"{sys.executable} scripts/workflow_gates.py --repo-root=. advance"
          " --from=T50 --to=T60 --approved=1",
          f"TO=T60 FROM=T50 APPROVED=1 {sys.executable} -m"
          " scripts.workflow_gates advance --make-env",
          "make -f .workflow/./state/Makefile push FROM=T50 APPROVED=1",
      )):
        ws_v = os.path.join(tmp, f"ws_var_{idx}")
        st_v = os.path.join(ws_v, ".workflow", "state")
        os.makedirs(st_v)
        tr_v = os.path.join(ws_v, "transcript.jsonl")
        _append_transcript_step(
            tr_v, {"step_index": 1, "type": "USER_INPUT", "content": "Commit ok"}
        )
        res_enter = guard.evaluate_tool_call({
            "transcriptPath": tr_v,
            "workspacePaths": [ws_v],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": enter_t60_cmd},
            },
        })
        self.assertEqual(res_enter["decision"], "allow", enter_t60_cmd)
        entry_v = read_gate_entry(st_v)
        self.assertIsNotNone(entry_v, enter_t60_cmd)
        self.assertEqual(entry_v["gate"], "T60")
        self.assertEqual(entry_v["transcriptPath"], tr_v)
        self.assertGreaterEqual(entry_v["entryStepIndex"], 0)

        for blocked_next in (
            "make -f .workflow/state/Makefile bot-quiescence APPROVED=1",
            "git push -u origin feat/219",
        ):
          res_blk = guard.evaluate_tool_call({
              "transcriptPath": tr_v,
              "workspacePaths": [ws_v],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": blocked_next},
              },
          })
          self.assertEqual(
              res_blk["decision"], "deny", (enter_t60_cmd, blocked_next)
          )

      # 3. Unwitnessed gate_entry.json (entryStepIndex == -1) must block same-turn exit
      ws_unw = os.path.join(tmp, "ws_unw")
      st_unw = os.path.join(ws_unw, ".workflow", "state")
      os.makedirs(st_unw)
      tr_unw = os.path.join(ws_unw, "transcript.jsonl")
      _append_transcript_step(
          tr_unw, {"step_index": 5, "type": "USER_INPUT", "content": "Earlier"}
      )
      record_gate_entry(st_unw, "T15", "T20", transcript_path="")
      unw_exit = guard.evaluate_tool_call({
          "transcriptPath": tr_unw,
          "workspacePaths": [ws_unw],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile implementation-diff"
                      " APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(unw_exit["decision"], "deny", unw_exit)

  def test_ambient_cmd_and_scars_env_do_not_leak_into_verify_green(
      self,
  ) -> None:
    """Verifies Round 3 BLOCKER-3 Chesterton's Fence against CMD/SCARS leakage."""
    from lib.workflow_gates import WorkflowGatesScript

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "tdd_repo")
      os.makedirs(repo)
      _init_git_repo(repo)
      _write_file(
          os.path.join(repo, "tests", "test_add.py"),
          "import unittest\nimport calc\n\n"
          "class T(unittest.TestCase):\n"
          "  def test_a(self):\n"
          "    self.assertEqual(calc.add(2, 3), 5)\n\n"
          "if __name__ == '__main__':\n  unittest.main()\n",
      )
      _git(repo, "add", "tests/test_add.py")
      _git(repo, "commit", "-q", "-m", "test: add failing calc test")
      WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"])

      test_cmd = f"{sys.executable} -m unittest tests/test_add.py"
      r_red = _run_make(
          repo, "witness-red", "TEST_DIR=tests", f"TEST_CMD={test_cmd}"
      )
      self.assertEqual(r_red.returncode, 0, r_red.stderr)

      _write_file(
          os.path.join(repo, "calc.py"), "def add(a, b):\n  return a + b\n"
      )
      polluted_env = dict(os.environ)
      polluted_env["CMD"] = "/bin/false"
      polluted_env["SCARS"] = "SCAR-BOGUS-99"
      r_green = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "verify-green",
              f"PYTHON={sys.executable}",
          ],
          cwd=repo,
          env=polluted_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(r_green.returncode, 0, r_green.stderr)

  def _assert_seven_consumers(
      self,
      repo: str,
      state_dir: str,
      lock_dir: str,
      expected: dict,
  ) -> None:
    """Asserts all 7 state consumers match expected lifecycle contract."""
    from lib.config import is_ticket_active
    from lib.target90_guard import is_target_90_state_dir
    from lib.terminal_title import detect_issue_tag, detect_ticket_id
    from lib.workspace_mutex import WorkspaceMutex
    from mcp.server import handle_tools_call

    guard = _load_script_module("pre_tool_guard_matrix", PRE_TOOL_SCRIPT)
    self.assertEqual(is_ticket_active(state_dir), expected["active"])
    self.assertEqual(
        WorkspaceMutex(repo, lock_dir=lock_dir).is_session_active(
            {"pid": None, "ticket_id": "GH-219", "workspace_path": repo}
        ),
        expected["session_active"],
    )
    self.assertEqual(is_target_90_state_dir(state_dir), expected["t90"])
    self.assertEqual(detect_ticket_id(repo_root=repo), expected["ticket_id"])
    self.assertEqual(
        bool(detect_issue_tag(repo_root=repo)), expected["has_tag"]
    )

    ss_proc = subprocess.run(
        [sys.executable, SESSION_START_SCRIPT],
        input=json.dumps({"workspacePaths": [repo]}),
        text=True,
        capture_output=True,
        check=False,
    )
    self.assertIn(expected["session_substr"], ss_proc.stdout)

    mcp_state = handle_tools_call(
        99, "workflow_get_state", {"workspaceDir": repo}
    )
    self.assertIn(
        expected["mcp_substr"], mcp_state["result"]["content"][0]["text"]
    )

    gov_res = guard.evaluate_tool_call({
        "workspacePaths": [repo],
        "toolCall": {
            "name": "write_file",
            "args": {"Path": "lib/state_machine.py", "Content": "# edit\n"},
        },
    })
    self.assertEqual(gov_res["decision"], expected["gov_decision"], gov_res)

  def test_two_ticket_lifecycle_matrix_across_all_readers_and_gates(
      self,
  ) -> None:
    """Verifies Round 4 2-ticket x 4-state x 7-consumer lifecycle matrix."""
    from lib.config import is_ticket_active
    from lib.workflow_gates import WorkflowGatesScript
    from mcp.server import handle_tools_call

    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "makeflow_repo")
      os.makedirs(repo)
      remote = os.path.join(tmp, "github.com", "hedges333", "matrix.git")
      os.makedirs(remote)
      _git(remote, "init", "--bare", "-q", "-b", "main")
      _init_git_repo(repo, branch="main")
      _write_file(
          os.path.join(repo, "plugin.json"),
          json.dumps({"name": "makeflow", "version": "1.0.0"}),
      )
      _git(repo, "add", "plugin.json")
      _git(repo, "commit", "-q", "-m", "chore: add makeflow plugin manifest")
      _git(repo, "remote", "add", "origin", remote)
      _git(repo, "push", "-q", "-u", "origin", "main")

      lock_dir = os.path.join(tmp, "locks")
      bin_dir = os.path.join(tmp, "bin")
      os.makedirs(bin_dir)
      fake_gh = os.path.join(bin_dir, "gh")
      _write_file(
          fake_gh,
          "#!/bin/sh\n"
          'if echo "$*" | grep -q "219"; then\n'
          '  echo \'{"assignees":[{"login":"alice"}],"labels":[{"name":"in-progress"}]}\'\n'
          "  exit 0\n"
          "fi\n"
          "exit 0\n",
      )
      os.chmod(fake_gh, 0o755)

      WorkflowGatesScript().execute(["--repo-root", repo, "init-makefile"])
      state_dir = os.path.join(repo, ".workflow", "state")

      # State 1: UNINITIALIZED (0-card clean workspace)
      r_init = _run_make(repo, "read-init-d", "NO_FETCH=1")
      self.assertEqual(r_init.returncode, 0, r_init.stderr)
      self.assertEqual(
          [f for f in os.listdir(state_dir) if f.endswith(".md")], []
      )
      self._assert_seven_consumers(
          repo,
          state_dir,
          lock_dir,
          {
              "active": False,
              "session_active": False,
              "t90": False,
              "ticket_id": "",
              "has_tag": False,
              "session_substr": "Workspace is clean and ready for Target 10 intake.",
              "mcp_substr": "is clean (0 state cards). Ready for Target 10 intake.",
              "gov_decision": "deny",
          },
      )
      self.assertEqual(
          _run_make(repo, "read-init-d", "FROM=T20").returncode, 2
      )
      for src_t in ("T00", "T20"):
        bad_mcp = handle_tools_call(
            101,
            "workflow_advance_target",
            {
                "currentTarget": src_t,
                "nextTarget": "T00",
                "workspaceDir": repo,
                "evidence": {"allowInit": True, "explicitFrom": False},
            },
        )
        self.assertTrue(bad_mcp["result"].get("isError"), (src_t, bad_mcp))

      # Defensive check: unassigned Target 00 99_next_action.md is inactive
      unassigned_na = os.path.join(state_dir, "99_next_action.md")
      _write_file(
          unassigned_na,
          "# 99_next_action\n\n- Ticket: Unassigned (Ready for Intake)\n"
          "- Active Target: Target 00 (`read-init-d`)\n",
      )
      self.assertFalse(is_ticket_active(state_dir))
      os.unlink(unassigned_na)

      # State 2: ACTIVE_T10_T80 (Ticket #1 GH-219)
      self.assertEqual(_run_make(repo, "check-intake").returncode, 0)
      _git(repo, "checkout", "-q", "-b", "feat/219-first")
      _write_file(
          os.path.join(state_dir, "10_ticket.md"),
          "# 10_ticket: `GH-219`\n\n"
          "- **Ticket ID**: `GH-219`\n"
          "- **Title**: [FEAT] Lifecycle matrix verification\n"
          "- **URL**: https://github.com/hedges333/makeflow-skill/issues/219\n",
      )
      self.assertEqual(
          _run_make(repo, "ticket-assigned", "NO_FETCH=1").returncode, 0
      )
      self._assert_seven_consumers(
          repo,
          state_dir,
          lock_dir,
          {
              "active": True,
              "session_active": True,
              "t90": False,
              "ticket_id": "GH-219",
              "has_tag": True,
              "session_substr": "found 2 state card(s)",
              "mcp_substr": "Found 2 state card(s)",
              "gov_decision": "allow",
          },
      )

      transcript = os.path.join(tmp, "matrix_transcript.jsonl")
      _append_transcript_step(
          transcript, {"step_index": 1, "type": "USER_INPUT", "content": "Go"}
      )
      _write_file(os.path.join(state_dir, "70_bot_triage.md"), "GREEN\n")
      self.assertEqual(
          _run_make(
              repo,
              "ready-to-land",
              "FROM=T70",
              "NO_FETCH=1",
              f"TRANSCRIPT={transcript}",
          ).returncode,
          0,
      )
      _append_transcript_step(
          transcript,
          {"step_index": 2, "type": "USER_INPUT", "content": "Land"},
      )
      self.assertEqual(
          _run_make(
              repo,
              "land",
              "APPROVED=1",
              "NO_FETCH=1",
              f"TRANSCRIPT={transcript}",
          ).returncode,
          0,
      )

      # State 3: ACTIVE_T90 on main (merged, before finish)
      _git(repo, "checkout", "-q", "main")
      _git(repo, "branch", "-D", "-q", "feat/219-first")
      self.assertEqual(
          _run_make(
              repo, "codified-scars-consolidated", "NO_FETCH=1"
          ).returncode,
          0,
      )
      self._assert_seven_consumers(
          repo,
          state_dir,
          lock_dir,
          {
              "active": True,
              "session_active": True,
              "t90": True,
              "ticket_id": "GH-219",
              "has_tag": True,
              "session_substr": "found 3 state card(s)",
              "mcp_substr": "Found 3 state card(s)",
              "gov_decision": "allow",
          },
      )

      # State 4: COMPLETED -> read-init-d -> Ticket #2 check-intake -> Ticket #2 ACTIVE
      self.assertEqual(_run_make(repo, "finish", "NO_FETCH=1").returncode, 0)
      self._assert_seven_consumers(
          repo,
          state_dir,
          lock_dir,
          {
              "active": False,
              "session_active": False,
              "t90": False,
              "ticket_id": "",
              "has_tag": False,
              "session_substr": "Workspace is clean and ready for Target 10 intake.",
              "mcp_substr": "Previous ticket is complete",
              "gov_decision": "deny",
          },
      )

      r_init_after_fin = _run_make(repo, "read-init-d", "NO_FETCH=1")
      self.assertEqual(r_init_after_fin.returncode, 0, r_init_after_fin.stderr)
      with open(
          os.path.join(state_dir, "99_next_action.md"), "r", encoding="utf-8"
      ) as f:
        fin_na = f.read()
      self.assertIn("# Target 90 Finished: COMPLETE", fin_na)
      self.assertNotIn("(In Progress)", fin_na)
      self.assertFalse(is_ticket_active(state_dir))
      self.assertEqual(
          _run_make(repo, "read-init-d", "FROM=T20").returncode, 2
      )

      # Next ticket check-intake on main with fake gh that would reject GH-219
      gh_env = dict(os.environ)
      gh_env["PATH"] = f"{bin_dir}:{gh_env.get('PATH', '')}"
      intake2 = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "check-intake",
              f"PYTHON={sys.executable}",
          ],
          cwd=repo,
          env=gh_env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(intake2.returncode, 0, intake2.stderr)

      # Ticket #2 starts cleanly
      _git(repo, "checkout", "-q", "-b", "feat/local-2-second")
      _write_file(
          os.path.join(state_dir, "10_ticket.md"),
          "# 10_ticket: `LOCAL-2`\n\n"
          "- **Ticket ID**: `LOCAL-2`\n"
          "- **Title**: Second ticket after finish\n",
      )
      self.assertEqual(
          _run_make(repo, "ticket-assigned", "NO_FETCH=1").returncode, 0
      )
      self.assertTrue(is_ticket_active(state_dir))


if __name__ == "__main__":
  unittest.main()
