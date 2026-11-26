"""Hermetic unit and architecture tests for GH-274 workflow_gates decomposition.

Covers direct domain behavior of all seven extracted modules under ``lib/``,
canonical backward-compatible re-export bijection and object identity parity
(SCAR-API-02, SCAR-GENUI-0008), acyclic layer separation (SCAR-ARCH-05,
SCAR-ARCH-08), Google-style PEP 257 docstrings (SCAR-PROC-159), and governance
registry membership (SCAR-TEST-12, SCAR-PROC-124).
"""

import ast
from dataclasses import FrozenInstanceError
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

NEW_EXTRACTED_MODULES = (
    "lib/main_sync.py",
    "lib/state_makefile.py",
    "lib/plan_validation.py",
    "lib/plugin_gates.py",
    "lib/intake_gates.py",
    "lib/commit_gates.py",
    "lib/parking_gates.py",
    "lib/workflow_gates_cli.py",
)

EXPECTED_REEXPORT_SYMBOLS = (
    "_DEFAULT_REPO_ROOT",
    "_SCRIPTS_DIR",
    "DEFAULT_SKILL_MAX_BYTES",
    "GateFailure",
    "REQUIRED_PLUGIN_FILES",
    "_LEFTOVER_CARD_RE",
    "_GH_CLOSE_RE",
    "_BARE_ISSUE_RE",
    "_MERGE_TREE_STAGE_RE",
    "_MERGE_TREE_HEADER_RE",
    "_no_remote_sync_result",
    "_run_repo_git",
    "_resolve_upstream_main_branch",
    "_parse_merge_tree_conflicts",
    "ephemeral_git_index",
    "inspect_main_sync",
    "fetch_and_inspect_main_sync",
    "ensure_git_exclude_workflow_dirs",
    "refresh_state_makefile_if_ignored",
    "_env_truthy",
    "_workflow_target_recipes",
    "generate_state_makefile",
    "WorkflowGatesScript",
)


def _completed(
    args,
    returncode=0,
    stdout="",
    stderr="",
):
  """Creates a CompletedProcess stub for hermetic fake git runners."""
  return subprocess.CompletedProcess(
      args=list(args),
      returncode=returncode,
      stdout=stdout,
      stderr=stderr,
  )


def _init_minimal_git_repo(repo_dir):
  """Initializes an isolated temporary Git repository."""
  subprocess.run(
      ["git", "init", "-q"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
  )


class TestMainSyncModule(unittest.TestCase):
  """Direct unit tests for lib/main_sync.py."""

  def test_no_remote_and_merge_tree_conflict_parsing(self):
    """Verifies NO_REMOTE defaults and 3-way merge-tree conflict parsing."""
    from lib.main_sync import (
        _no_remote_sync_result,
        _parse_merge_tree_conflicts,
    )

    no_rem = _no_remote_sync_result()
    self.assertEqual(no_rem["status"], "NO_REMOTE")
    self.assertIsNone(no_rem["upstream_ref"])
    self.assertFalse(no_rem["has_conflicts"])
    self.assertEqual(no_rem["conflicting_files"], [])

    sample_output = (
        "changed in both\n"
        "  base   100644 aaaaaaa src/a.py\n"
        "  our    100644 bbbbbbb src/a.py\n"
        "  their  100644 ccccccc src/a.py\n"
        "+<<<<<<< .our\n"
        "removed in remote\n"
        "  base   100644 1111111 src/deleted.py\n"
        "  our    100644 2222222 src/deleted.py\n"
        "added in both\n"
        "  our    100644 3333333 src/same.py\n"
        "  their  100644 3333333 src/same.py\n"
        "added in both\n"
        "  our    100644 4444444 src/diff.py\n"
        "  their  100644 5555555 src/diff.py\n"
        "changed in both\n"
        "  base   100644 6666666 assets/img.png\n"
        "  our    100644 7777777 assets/img.png\n"
        "  their  100644 8888888 assets/img.png\n"
        "Cannot merge binary files: assets/img.png\n"
        "removed in local\n"
        "  base   100644 9999999 src/local_del_conflict.py\n"
        "  their  100644 aaaaaab src/local_del_conflict.py\n"
        "removed in local\n"
        "  base   100644 bbbbbbc src/local_del_clean.py\n"
        "  their  100644 bbbbbbc src/local_del_clean.py\n"
    )
    parsed = _parse_merge_tree_conflicts(sample_output)
    self.assertEqual(
        parsed,
        [
            "assets/img.png",
            "src/a.py",
            "src/deleted.py",
            "src/diff.py",
            "src/local_del_conflict.py",
        ],
    )

  def test_evaluate_main_sync_gate_decisions(self):
    """Verifies evaluate_main_sync_gate across all upstream sync statuses."""
    from lib.main_sync import evaluate_main_sync_gate
    from lib.reflex import GateFailure

    up_to_date = {
        "status": "UP_TO_DATE",
        "upstream_ref": "origin/main",
        "ahead_count": 1,
        "behind_count": 0,
        "has_conflicts": False,
        "conflicting_files": [],
        "working_tree_dirty": False,
    }
    res_ok = evaluate_main_sync_gate(up_to_date)
    self.assertTrue(res_ok.ok)
    self.assertEqual(res_ok.notice, "")
    self.assertIsNone(res_ok.failure)
    self.assertFalse(hasattr(res_ok, "scar_id"))

    behind = {
        **up_to_date,
        "status": "BEHIND_CLEAN",
        "behind_count": 2,
    }
    res_behind_notice = evaluate_main_sync_gate(behind, fail_on_behind=False)
    self.assertTrue(res_behind_notice.ok)
    self.assertIsNone(res_behind_notice.failure)
    self.assertIn("MAIN_SYNC_NOTICE:", res_behind_notice.notice)

    res_behind_fail = evaluate_main_sync_gate(behind, fail_on_behind=True)
    self.assertFalse(res_behind_fail.ok)
    self.assertIsInstance(res_behind_fail.failure, GateFailure)
    self.assertEqual(res_behind_fail.failure.scar_id, "GIT-20")
    self.assertEqual(res_behind_fail.failure.code, "UPSTREAM_MAIN_BEHIND")

    conflict = {
        **behind,
        "status": "CONFLICT",
        "has_conflicts": True,
        "conflicting_files": ["lib/a.py"],
    }
    res_conflict_fail = evaluate_main_sync_gate(conflict, allow_conflict=False)
    self.assertFalse(res_conflict_fail.ok)
    self.assertIsInstance(res_conflict_fail.failure, GateFailure)
    self.assertEqual(res_conflict_fail.failure.scar_id, "GIT-20")
    self.assertEqual(res_conflict_fail.failure.code, "UPSTREAM_MAIN_CONFLICT")
    self.assertIn("lib/a.py", res_conflict_fail.failure.trigger)

    res_conflict_override = evaluate_main_sync_gate(
        conflict, allow_conflict=True
    )
    self.assertTrue(res_conflict_override.ok)
    self.assertIsNone(res_conflict_override.failure)
    self.assertIn("MAIN_SYNC_WARNING:", res_conflict_override.notice)

  def test_inspect_and_fetch_main_sync_injectable_hooks(self):
    """Verifies non-existent path returns NO_REMOTE and inspect_fn injection works."""
    from lib.main_sync import (
        fetch_and_inspect_main_sync,
        inspect_main_sync,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
      missing = os.path.join(tmpdir, "does_not_exist")
      self.assertEqual(inspect_main_sync(missing)["status"], "NO_REMOTE")
      self.assertEqual(
          fetch_and_inspect_main_sync(missing)["status"], "NO_REMOTE"
      )

      _init_minimal_git_repo(tmpdir)
      called = []

      def _fake_inspect(root):
        called.append(root)
        return {"status": "UP_TO_DATE", "upstream_ref": "origin/main"}

      with mock.patch("lib.main_sync._run_repo_git") as mock_git:
        mock_git.side_effect = [
            _completed(["git", "remote"], 0, stdout="https://example/repo.git\n"),
            _completed(["git", "symbolic-ref"], 0, stdout="origin/main\n"),
            _completed(["git", "fetch"], 0),
        ]
        result = fetch_and_inspect_main_sync(tmpdir, inspect_fn=_fake_inspect)
      self.assertEqual(called, [tmpdir])
      self.assertEqual(result["status"], "UP_TO_DATE")


class TestStateMakefileModule(unittest.TestCase):
  """Direct unit tests for lib/state_makefile.py."""

  def test_generate_state_makefile_and_exclude_refresh(self):
    """Verifies Makefile synthesis, .git/info/exclude update, and ignored refresh."""
    from lib.state_makefile import (
        ensure_git_exclude_workflow_dirs,
        generate_state_makefile,
        refresh_state_makefile_if_ignored,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
      mk_text = generate_state_makefile(tmpdir)
      self.assertIn(".PHONY:", mk_text)
      self.assertIn("check-intake:", mk_text)
      self.assertIn("check-plan:", mk_text)
      self.assertIn("approved-plan: check-plan", mk_text)
      self.assertIn("check-approved-tree:", mk_text)
      self.assertIn("park-session:", mk_text)
      self.assertIn("resume-parked:", mk_text)
      self.assertIn("PR ?=", mk_text)
      self.assertIn("BRANCH ?=", mk_text)
      self.assertIn("REASON ?=", mk_text)
      self.assertIn("CRON ?=", mk_text)
      self.assertIn("witness-red:", mk_text)
      self.assertIn("verify-green:", mk_text)
      self.assertIn("ast-lint:", mk_text)

      # Non-git directory returns False without creating files.
      self.assertFalse(ensure_git_exclude_workflow_dirs(tmpdir))
      self.assertFalse(refresh_state_makefile_if_ignored(tmpdir))

      _init_minimal_git_repo(tmpdir)
      os.makedirs(os.path.join(tmpdir, ".workflow"), exist_ok=True)
      self.assertFalse(refresh_state_makefile_if_ignored(tmpdir))

      self.assertTrue(ensure_git_exclude_workflow_dirs(tmpdir))
      # Second call is idempotent.
      self.assertTrue(ensure_git_exclude_workflow_dirs(tmpdir))
      self.assertTrue(refresh_state_makefile_if_ignored(tmpdir))
      mk_path = os.path.join(tmpdir, ".workflow", "state", "Makefile")
      self.assertTrue(os.path.isfile(mk_path))
      with open(mk_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), mk_text)
      # Second refresh is idempotent.
      self.assertTrue(refresh_state_makefile_if_ignored(tmpdir))


class TestPluginGatesModule(unittest.TestCase):
  """Direct unit tests for lib/plugin_gates.py."""

  def test_plugin_files_and_skill_budget_checks(self):
    """Verifies REQUIRED_PLUGIN_FILES completeness and SKILL.md byte ceiling."""
    from lib.plugin_gates import (
        DEFAULT_SKILL_MAX_BYTES,
        REQUIRED_PLUGIN_FILES,
        check_plugin_files,
        check_skill_budget,
    )

    self.assertEqual(DEFAULT_SKILL_MAX_BYTES, 24576)
    self.assertEqual(len(REQUIRED_PLUGIN_FILES), 6)

    with tempfile.TemporaryDirectory() as tmpdir:
      missing_fail = check_plugin_files(tmpdir)
      self.assertIsNotNone(missing_fail)
      self.assertEqual(missing_fail.scar_id, "DOC-16")
      self.assertEqual(missing_fail.code, "MISSING_REQUIRED_PLUGIN_FILE")

      budget_missing = check_skill_budget(tmpdir)
      self.assertIsNotNone(budget_missing)
      self.assertEqual(budget_missing.code, "MISSING_REQUIRED_PLUGIN_FILE")

      for rel in REQUIRED_PLUGIN_FILES:
        full_path = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
          f.write("ok\n")

      self.assertIsNone(check_plugin_files(tmpdir))
      self.assertIsNone(check_skill_budget(tmpdir, max_bytes=100))

      skill_path = os.path.join(tmpdir, "skills", "makeflow", "SKILL.md")
      with open(skill_path, "wb") as f:
        f.write(b"x" * 100)
      self.assertIsNone(check_skill_budget(tmpdir, max_bytes=100))

      with open(skill_path, "wb") as f:
        f.write(b"x" * 101)
      off_by_one_fail = check_skill_budget(tmpdir, max_bytes=100)
      self.assertIsNotNone(off_by_one_fail)
      self.assertEqual(off_by_one_fail.scar_id, "PROC-99")
      self.assertEqual(off_by_one_fail.code, "SKILL_CONTEXT_CEILING_EXCEEDED")

      with open(skill_path, "w", encoding="utf-8") as f:
        f.write("x" * 200)
      budget_fail = check_skill_budget(tmpdir, max_bytes=100)
      self.assertIsNotNone(budget_fail)
      self.assertEqual(budget_fail.scar_id, "PROC-99")
      self.assertEqual(budget_fail.code, "SKILL_CONTEXT_CEILING_EXCEEDED")


class TestIntakeGatesModule(unittest.TestCase):
  """Direct unit tests for lib/intake_gates.py."""

  def test_request_dataclasses_are_frozen(self):
    """Verifies SCAR-ARCH-0014 frozen parameter objects reject mutation."""
    from lib.intake_gates import IntakeGateRequest, ReleaseIntakeRequest

    req = IntakeGateRequest(repo_root="/nonexistent/example", ticket="GH-274")
    with self.assertRaises(FrozenInstanceError):
      req.ticket = "GH-999"

    rel_req = ReleaseIntakeRequest(repo_root="/nonexistent/example", ticket="GH-274")
    with self.assertRaises(FrozenInstanceError):
      rel_req.ticket = "GH-999"

  def test_run_intake_gate_and_cleanup_with_injected_git_runner(self):
    """Verifies intake hygiene checks, stale subagent removal, and receipt cleanup."""
    from lib.intake_gates import (
        IntakeGateRequest,
        ReleaseIntakeRequest,
        cleanup_tdd_receipt,
        run_intake_gate,
        run_release_intake_gate,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)

      # 1. Dirty tree rejected
      dirty_fail = run_intake_gate(
          IntakeGateRequest(repo_root=tmpdir),
          git_runner=lambda *args: _completed(["git", *args], 0, stdout=" M a.py\n"),
      )
      self.assertIsNotNone(dirty_fail)
      self.assertEqual(dirty_fail.code, "UNCLEAN_INTAKE_STATE")

      # 2. Unexcluded workflow dirs rejected
      def _unexcluded_git(*args):
        if args[:2] == ("status", "--porcelain"):
          return _completed(["git", *args], 0, stdout="")
        return _completed(["git", *args], 1, stdout="")

      excl_fail = run_intake_gate(
          IntakeGateRequest(repo_root=tmpdir),
          git_runner=_unexcluded_git,
      )
      self.assertIsNotNone(excl_fail)
      self.assertIn(".workflow/state/", excl_fail.trigger)

      # 3. Leftover card rejected
      leftover_path = os.path.join(state_dir, "20_plan.md")
      with open(leftover_path, "w", encoding="utf-8") as f:
        f.write("stale\n")

      def _clean_git(*args):
        return _completed(["git", *args], 0, stdout="")

      leftover_fail = run_intake_gate(
          IntakeGateRequest(repo_root=tmpdir),
          git_runner=_clean_git,
      )
      self.assertIsNotNone(leftover_fail)
      self.assertIn("20_plan.md", leftover_fail.trigger)
      os.unlink(leftover_path)

      # 4. Clean intake removes stale active_subagent.json and succeeds
      stale_subagent = os.path.join(state_dir, "active_subagent.json")
      with open(stale_subagent, "w", encoding="utf-8") as f:
        f.write("{}\n")
      with mock.patch("lib.terminal_title.set_terminal_title"):
        self.assertIsNone(
            run_intake_gate(
                IntakeGateRequest(repo_root=tmpdir),
                git_runner=_clean_git,
            )
        )
      self.assertFalse(os.path.exists(stale_subagent))

      # 5. cleanup_tdd_receipt and run_release_intake_gate unlink receipts
      receipt = os.path.join(state_dir, "30_tdd_receipt.json")
      with open(receipt, "w", encoding="utf-8") as f:
        f.write("{}\n")
      cleanup_tdd_receipt(tmpdir, "LOCAL-1", stderr=io.StringIO())
      self.assertFalse(os.path.exists(receipt))

      with mock.patch("lib.terminal_title.set_terminal_title"):
        rc = run_release_intake_gate(
            ReleaseIntakeRequest(
                repo_root=tmpdir,
                ticket="LOCAL-1",
                lock_dir=os.path.join(tmpdir, "locks"),
                stderr=io.StringIO(),
            )
        )
      self.assertEqual(rc, 0)


class TestCommitGatesModule(unittest.TestCase):
  """Direct unit tests for lib/commit_gates.py."""

  def test_check_approved_tree_with_injected_git_runner(self):
    """Verifies approved_tree.txt missing, drift, mismatch, and exact match cases."""
    from lib.commit_gates import check_approved_tree

    with tempfile.TemporaryDirectory() as tmpdir:
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)

      missing_fail = check_approved_tree(tmpdir, git_runner=lambda *a: _completed(a, 0))
      self.assertIsNotNone(missing_fail)
      self.assertEqual(missing_fail.code, "UNAPPROVED_TREE_DRIFT")

      tree_file = os.path.join(state_dir, "approved_tree.txt")
      with open(tree_file, "w", encoding="utf-8") as f:
        f.write("abc123\n")

      drift_fail = check_approved_tree(
          tmpdir,
          git_runner=lambda *a: _completed(a, 0, stdout=" M lib/x.py\n"),
      )
      self.assertIsNotNone(drift_fail)
      self.assertIn("drift", drift_fail.trigger.lower())

      def _mismatch_git(*args):
        if args == ("status", "--porcelain"):
          return _completed(args, 0, stdout="M  lib/x.py\n")
        return _completed(args, 0, stdout="deadbeef\n")

      mismatch_fail = check_approved_tree(tmpdir, git_runner=_mismatch_git)
      self.assertIsNotNone(mismatch_fail)
      self.assertIn("deadbeef", mismatch_fail.trigger)

      def _match_git(*args):
        if args == ("status", "--porcelain"):
          return _completed(args, 0, stdout="M  lib/x.py\n")
        return _completed(args, 0, stdout="abc123\n")

      self.assertIsNone(check_approved_tree(tmpdir, git_runner=_match_git))

  def test_check_commit_msg_and_post_commit(self):
    """Verifies commit-msg validation for local tasks and post-commit failure reporting."""
    from lib.commit_gates import check_commit_msg, check_post_commit

    with tempfile.TemporaryDirectory() as tmpdir:
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      self.assertIsNotNone(check_commit_msg(tmpdir))

      msg_path = os.path.join(state_dir, "50_commit_msg.txt")
      with open(msg_path, "w", encoding="utf-8") as f:
        f.write("feat(core): demo\n\nCloses #12\n")
      with mock.patch(
          "lib.commit_gates.detect_commit_context",
          return_value=(True, "LOCAL-7"),
      ):
        local_fail = check_commit_msg(tmpdir)
      self.assertIsNotNone(local_fail)
      self.assertEqual(local_fail.code, "INVALID_COMMIT_MESSAGE_CONTRACT")
      self.assertIn("closes #<N>", local_fail.trigger)

      with open(msg_path, "w", encoding="utf-8") as f:
        f.write(
            "feat(core): demo local change\n\n"
            "Summary body line.\n\n"
            "Ticket: LOCAL-7\n"
        )
      with mock.patch(
          "lib.commit_gates.detect_commit_context",
          return_value=(True, "LOCAL-7"),
      ):
        self.assertIsNone(check_commit_msg(tmpdir))

      post_res = check_post_commit(tmpdir)
      self.assertFalse(post_res.ok)
      self.assertIsNotNone(post_res.failure)


class TestParkingGatesModule(unittest.TestCase):
  """Direct unit tests for lib/parking_gates.py."""

  def test_request_and_outcome_dataclasses_are_frozen(self):
    """Verifies SCAR-ARCH-0014 frozen dataclasses reject mutation."""
    from lib.parking_gates import (
        ParkSessionRequest,
        ParkingGateOutcome,
        ResumeParkedRequest,
    )

    park_req = ParkSessionRequest(repo_root="/nonexistent/repo", pr=47)
    with self.assertRaises(FrozenInstanceError):
      park_req.pr = 99

    resume_req = ResumeParkedRequest(repo_root="/nonexistent/repo", pr=47)
    with self.assertRaises(FrozenInstanceError):
      resume_req.pr = 99

    outcome = ParkingGateOutcome(ok=True, text_output="ok\n")
    with self.assertRaises(FrozenInstanceError):
      outcome.ok = False

  def test_park_and_resume_gate_behaviors_and_errors(self):
    """Verifies direct execution of run_park_session_gate and run_resume_parked_gate."""
    from lib.parking_gates import (
        ParkSessionRequest,
        ResumeParkedRequest,
        run_park_session_gate,
        run_resume_parked_gate,
    )
    from lib.parking_state import ParkingStateStore
    from lib.reflex import GateFailure
    from lib.state_machine import write_active_target

    with tempfile.TemporaryDirectory() as tmpdir:
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      write_active_target(
          state_dir,
          {
              "previousTarget": "T20",
              "target": "T30",
              "targetName": "implementation-diff",
          },
      )

      non_t70_park = run_park_session_gate(
          ParkSessionRequest(repo_root=tmpdir, pr=47)
      )
      self.assertFalse(non_t70_park.ok)
      self.assertIsInstance(non_t70_park.failure, GateFailure)
      self.assertEqual(non_t70_park.failure.code, "PARK_REQUIRES_T70")

      non_t70_resume = run_resume_parked_gate(
          ResumeParkedRequest(repo_root=tmpdir, pr=47)
      )
      self.assertFalse(non_t70_resume.ok)
      self.assertIsInstance(non_t70_resume.failure, GateFailure)
      self.assertEqual(non_t70_resume.failure.code, "RESUME_REQUIRES_T70")

      write_active_target(
          state_dir,
          {
              "previousTarget": "T60",
              "target": "T70",
              "targetName": "bot-quiescence",
          },
      )

      not_parked = run_resume_parked_gate(ResumeParkedRequest(repo_root=tmpdir))
      self.assertFalse(not_parked.ok)
      self.assertEqual(not_parked.failure.code, "NOT_PARKED")

      parked = run_park_session_gate(
          ParkSessionRequest(
              repo_root=tmpdir,
              pr=47,
              branch="feat/47-test",
              reason="Awaiting peer review",
              ci_status="SUCCESS",
              review_decision="REVIEW_REQUIRED",
              approvals=1,
              required_approvals=2,
              pending_reviewers="alice,bob,alice",
              has_explicit_status=True,
          )
      )
      self.assertTrue(parked.ok)
      self.assertEqual(parked.json_payload["pr_number"], 47)
      self.assertEqual(parked.json_payload["pending_reviewers"], ["alice", "bob"])
      self.assertIn("PARKED", parked.text_output)
      self.assertTrue(ParkingStateStore(state_dir).is_parked())

  def test_park_session_returns_park_poll_error_without_mutating_state(self):
    """Verifies PARK_POLL_ERROR fails closed without creating parking artifacts."""
    from lib.parking_gates import (
        ParkSessionRequest,
        run_park_session_gate,
    )
    from lib.reflex import GateFailure
    from lib.state_machine import write_active_target

    with tempfile.TemporaryDirectory() as tmpdir:
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      write_active_target(
          state_dir,
          {
              "previousTarget": "T60",
              "target": "T70",
              "targetName": "bot-quiescence",
          },
      )

      poll_error = {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "message": "gh api graphql failed: network timeout",
      }
      with mock.patch(
          "lib.ci_polling.poll_ci_status", return_value=poll_error
      ) as mock_poll:
        outcome = run_park_session_gate(
            ParkSessionRequest(
                repo_root=tmpdir,
                pr=47,
                branch="feat/47-poll-error",
                has_explicit_status=False,
            )
        )

      mock_poll.assert_called_once()
      self.assertFalse(outcome.ok)
      self.assertIsInstance(outcome.failure, GateFailure)
      self.assertEqual(outcome.failure.scar_id, "TEST-24")
      self.assertEqual(outcome.failure.code, "PARK_POLL_ERROR")
      self.assertIn("network timeout", outcome.failure.trigger)
      self.assertFalse(
          os.path.exists(os.path.join(state_dir, "70_parking_state.json"))
      )
      self.assertFalse(
          os.path.exists(os.path.join(state_dir, "70_bot_triage.md"))
      )

  def test_emit_parking_outcome_forwards_advance_error_to_advance_reflex(self):
    """Verifies _emit_parking_outcome forwards advance_error via _emit_advance_reflex."""
    from lib.parking_gates import ParkingGateOutcome
    import lib.workflow_gates as wg

    script = wg.WorkflowGatesScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err

    advance_msg = "BLOCKED: Transition T70 -> T75 is not valid in the Makefile DAG."
    outcome = ParkingGateOutcome(ok=False, advance_error=advance_msg)

    with mock.patch.object(
        script,
        "_emit_advance_reflex",
        wraps=script._emit_advance_reflex,
    ) as spy_advance_reflex:
      rc = script._emit_parking_outcome(outcome)

    self.assertEqual(rc, 2)
    spy_advance_reflex.assert_called_once_with(advance_msg)
    self.assertEqual(out.getvalue(), "")
    err_text = err.getvalue()
    self.assertIn("[SCAR-ID: ARCH-06]", err_text)
    self.assertIn("INVALID_TARGET_TRANSITION", err_text)
    self.assertIn(advance_msg, err_text)
    self.assertIn("REFLEX (ACTION):", err_text)


class TestWorkflowGatesCliModule(unittest.TestCase):
  """Direct unit tests for lib/workflow_gates_cli.py."""

  def test_parser_supports_all_20_subcommands_and_env_hydration(self):
    """Verifies all 20 subcommands parse and Make/parking env hydration validates env."""
    from lib.base_script import ReflexArgumentError
    from lib.workflow_gates_cli import (
        _env_truthy,
        build_workflow_gates_parser,
        hydrate_from_make_env,
        hydrate_parking_from_env,
    )

    self.assertFalse(_env_truthy("FLAG", environ={"FLAG": "0"}))
    self.assertFalse(_env_truthy("FLAG", environ={"FLAG": "false"}))
    self.assertTrue(_env_truthy("FLAG", environ={"FLAG": "1"}))

    parser = build_workflow_gates_parser("/nonexistent/repo")
    for subcmd in (
        "init-makefile",
        "plugin-files",
        "skill-budget",
        "intake",
        "release-intake",
        "release-lock",
        "check-plan",
        "approved-tree",
        "commit-msg",
        "post-commit",
        "terminal-title",
        "main-sync",
        "advance",
        "read-init-d",
        "verify-regression",
        "check-regression",
        "repo-tree",
        "promote-review",
        "park-session",
        "resume-parked",
    ):
      ns = parser.parse_args([subcmd])
      self.assertEqual(ns.gate, subcmd)

    pr, branch, reason, cron = hydrate_parking_from_env(
        environ={
            "MF_PARK_PR": "47",
            "MF_PARK_BRANCH": "feat/47-test",
            "MF_PARK_REASON": "Waiting for review",
            "MF_PARK_CRON": "*/30 * * * *",
        }
    )
    self.assertEqual((pr, branch, reason, cron), (47, "feat/47-test", "Waiting for review", "*/30 * * * *"))

    with self.assertRaises(ReflexArgumentError):
      hydrate_parking_from_env(environ={"MF_PARK_PR": "not-an-int"})

    class _Target:
      ticket = None
      allow_blocked = False
      commit_msg_file = None
      no_fetch = False
      allow_main_conflict = False
      allow_conflict = False
      adv_from = ""
      adv_to = ""
      adv_approved = None
      adv_blockers = None
      adv_transcript = ""
      regression_cmd = ""
      subproject = ""
      tree_prefix = ""
      tree_approved_limit = None
      tree_no_limit = False
      tree_count_only = False

    target = _Target()
    hydrate_from_make_env(
        target,
        environ={
            "TICKET": "GH-274",
            "ALLOW_BLOCKED": "1",
            "ALLOW_MAIN_CONFLICT": "true",
            "TREE_LIMIT": "25",
            "COUNT_ONLY": "yes",
        },
    )
    self.assertEqual(target.ticket, "GH-274")
    self.assertTrue(target.allow_blocked)
    self.assertTrue(target.allow_main_conflict)
    self.assertTrue(target.allow_conflict)
    self.assertEqual(target.tree_approved_limit, 25)
    self.assertTrue(target.tree_count_only)

    bad_target = _Target()
    with self.assertRaises(ReflexArgumentError):
      hydrate_from_make_env(bad_target, environ={"TREE_LIMIT": "abc"})


class TestWorkflowGatesCompatibilityAndArchitecture(unittest.TestCase):
  """Verifies re-export bijection, identity, acyclic imports, docstrings, and registries."""

  def test_reexport_bijection_and_object_identity_parity(self):
    """SCAR-API-02 / SCAR-GENUI-0008: asserts 1-to-1 re-exports and identity."""
    import lib.commit_gates as commit_gates
    import lib.intake_gates as intake_gates
    import lib.main_sync as main_sync
    import lib.plugin_gates as plugin_gates
    import lib.state_makefile as state_makefile
    import lib.workflow_gates as wg
    import lib.workflow_gates_cli as wg_cli

    missing = [sym for sym in EXPECTED_REEXPORT_SYMBOLS if not hasattr(wg, sym)]
    self.assertEqual(missing, [], f"Missing re-exports on lib.workflow_gates: {missing}")

    self.assertIs(
        wg.refresh_state_makefile_if_ignored,
        state_makefile.refresh_state_makefile_if_ignored,
    )
    self.assertIs(wg.generate_state_makefile, state_makefile.generate_state_makefile)
    self.assertIs(
        wg.ensure_git_exclude_workflow_dirs,
        state_makefile.ensure_git_exclude_workflow_dirs,
    )
    self.assertIs(wg.REQUIRED_PLUGIN_FILES, plugin_gates.REQUIRED_PLUGIN_FILES)
    self.assertIs(wg.DEFAULT_SKILL_MAX_BYTES, plugin_gates.DEFAULT_SKILL_MAX_BYTES)
    self.assertIs(wg._run_repo_git, main_sync._run_repo_git)
    self.assertIs(
        wg._parse_merge_tree_conflicts, main_sync._parse_merge_tree_conflicts
    )
    self.assertIs(wg._LEFTOVER_CARD_RE, intake_gates._LEFTOVER_CARD_RE)
    self.assertIs(wg._GH_CLOSE_RE, commit_gates._GH_CLOSE_RE)
    self.assertIs(wg._BARE_ISSUE_RE, commit_gates._BARE_ISSUE_RE)
    self.assertIs(wg._env_truthy, wg_cli._env_truthy)

  def test_canonical_gate_failure_identity_typing_and_git_runner_dedup(self):
    """BLOCKER-1 / NBO-1: single canonical GateFailure in lib.reflex and consolidated git runner."""
    from typing import get_type_hints

    import lib.commit_gates as commit_gates
    import lib.intake_gates as intake_gates
    import lib.main_sync as main_sync
    import lib.parking_gates as parking_gates
    import lib.plugin_gates as plugin_gates
    import lib.reflex as reflex
    import lib.workflow_gates as wg

    self.assertIs(reflex.GateFailure, plugin_gates.GateFailure)
    self.assertIs(reflex.GateFailure, commit_gates.GateFailure)
    self.assertIs(reflex.GateFailure, intake_gates.GateFailure)
    self.assertIs(reflex.GateFailure, main_sync.GateFailure)
    self.assertIs(reflex.GateFailure, parking_gates.GateFailure)
    self.assertIs(reflex.GateFailure, wg.GateFailure)
    self.assertIn("GateFailure", wg.__all__)

    sample = reflex.GateFailure(
        scar_id="ARCH-06",
        code="DEMO_CODE",
        trigger="demo trigger",
        rejected="demo rejected",
        reflex="demo reflex",
    )
    with self.assertRaises(FrozenInstanceError):
      sample.code = "MUTATED"

    hints = get_type_hints(wg.WorkflowGatesScript._fail_from)
    self.assertIs(hints.get("failure"), reflex.GateFailure)
    self.assertIs(hints.get("return"), int)

    self.assertFalse(hasattr(commit_gates, "_default_git_runner"))
    self.assertFalse(hasattr(intake_gates, "_default_git_runner"))

  def test_post_commit_and_main_sync_fail_closed_when_failure_is_none(self):
    """NBO-2 / BLOCKER-1: ok=False with failure=None must fail closed with exit 2 and Reflex."""
    from lib.commit_gates import PostCommitResult
    from lib.main_sync import MainSyncDecision
    import lib.workflow_gates as wg

    with tempfile.TemporaryDirectory() as tmpdir:
      out = io.StringIO()
      err = io.StringIO()
      with mock.patch(
          "lib.workflow_gates.check_post_commit",
          return_value=PostCommitResult(ok=False, failure=None),
      ):
        rc = wg.WorkflowGatesScript().execute(
            args=["--repo-root", tmpdir, "post-commit"],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(rc, 2)
      self.assertEqual(out.getvalue(), "")
      self.assertIn("[SCAR-ID:", err.getvalue())
      self.assertIn("REFLEX (ACTION):", err.getvalue())

      out_ms = io.StringIO()
      err_ms = io.StringIO()
      with mock.patch(
          "lib.workflow_gates.inspect_main_sync",
          return_value={"status": "CONFLICT"},
      ), mock.patch(
          "lib.workflow_gates.evaluate_main_sync_gate",
          return_value=MainSyncDecision(
              ok=False, sync={"status": "CONFLICT"}, failure=None
          ),
      ):
        rc_ms = wg.WorkflowGatesScript().execute(
            args=["--repo-root", tmpdir, "main-sync", "--no-fetch"],
            stdout=out_ms,
            stderr=err_ms,
        )
      self.assertEqual(rc_ms, 2)
      self.assertEqual(out_ms.getvalue(), "")
      self.assertIn("[SCAR-ID:", err_ms.getvalue())
      self.assertIn("REFLEX (ACTION):", err_ms.getvalue())

      from lib.parking_gates import ParkingGateOutcome

      out_pk = io.StringIO()
      err_pk = io.StringIO()
      with mock.patch(
          "lib.workflow_gates.run_park_session_gate",
          return_value=ParkingGateOutcome(ok=False, failure=None),
      ):
        rc_pk = wg.WorkflowGatesScript().execute(
            args=["--repo-root", tmpdir, "park-session"],
            stdout=out_pk,
            stderr=err_pk,
        )
      self.assertEqual(rc_pk, 2)
      self.assertEqual(out_pk.getvalue(), "")
      self.assertIn("[SCAR-ID:", err_pk.getvalue())
      self.assertIn("REFLEX (ACTION):", err_pk.getvalue())

  def test_monkeypatch_compatibility_on_workflow_gates(self):
    """SCAR-API-02: mock.patch on lib.workflow_gates.subprocess.run and ephemeral_git_index still works."""
    import lib.workflow_gates as wg

    with tempfile.TemporaryDirectory() as tmpdir:
      with mock.patch(
          "lib.workflow_gates.subprocess.run",
          side_effect=subprocess.TimeoutExpired(cmd=["git", "status"], timeout=15),
      ):
        err = io.StringIO()
        code = wg.WorkflowGatesScript().execute(
            args=["--repo-root", tmpdir, "intake"],
            stdout=io.StringIO(),
            stderr=err,
        )
        self.assertEqual(code, 2)
        self.assertIn("[SCAR-ID: GIT-25]", err.getvalue())

    sentinel = object()
    captured = {}

    def _fake_main_sync_inspect(repo_root, ephemeral_index_fn=None):
      captured["repo_root"] = repo_root
      captured["ephemeral_index_fn"] = ephemeral_index_fn
      return {"status": "NO_REMOTE"}

    with mock.patch("lib.workflow_gates.ephemeral_git_index", sentinel):
      with mock.patch(
          "lib.workflow_gates._main_sync_inspect",
          side_effect=_fake_main_sync_inspect,
      ):
        wg.inspect_main_sync("/nonexistent/demo")
    self.assertIs(captured.get("ephemeral_index_fn"), sentinel)

  def test_zero_shebangs_and_google_docstrings(self):
    """SCAR-ARCH-05 / SCAR-PROC-159: zero shebangs and PEP 257 docstrings on new modules."""
    for rel_path in NEW_EXTRACTED_MODULES:
      abs_path = os.path.join(_REPO_ROOT, rel_path)
      self.assertTrue(os.path.isfile(abs_path), f"Missing module: {rel_path}")
      with open(abs_path, "r", encoding="utf-8") as f:
        source = f.read()
      self.assertFalse(
          source.startswith("#!"),
          f"Library module {rel_path} must not have a shebang header",
      )
      tree = ast.parse(source, filename=rel_path)
      self.assertTrue(
          ast.get_docstring(tree),
          f"Module {rel_path} is missing a module docstring",
      )
      for node in tree.body:
        if isinstance(node, ast.ClassDef):
          class_doc = ast.get_docstring(node)
          self.assertTrue(
              class_doc,
              f"Class {node.name} in {rel_path} is missing a docstring",
          )
          is_dataclass = any(
              (
                  isinstance(dec, ast.Name) and dec.id == "dataclass"
              )
              or (
                  isinstance(dec, ast.Call)
                  and isinstance(dec.func, ast.Name)
                  and dec.func.id == "dataclass"
              )
              for dec in node.decorator_list
          )
          if is_dataclass:
            self.assertIn(
                "Attributes:",
                class_doc,
                f"Dataclass {node.name} in {rel_path} missing Google Attributes: section",
            )
            for stmt in node.body:
              if isinstance(stmt, ast.AnnAssign) and isinstance(
                  stmt.target, ast.Name
              ):
                self.assertIn(
                    f"{stmt.target.id}:",
                    class_doc,
                    f"Dataclass {node.name} in {rel_path} missing Attributes entry for {stmt.target.id}",
                )
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
          doc = ast.get_docstring(node)
          self.assertTrue(
              doc,
              f"Public function {node.name} in {rel_path} is missing a docstring",
          )
          if node.args.args or node.args.kwonlyargs:
            self.assertIn(
                "Args:",
                doc,
                f"Public function {node.name} in {rel_path} missing Google Args: section",
            )
          self.assertIn(
              "Returns:",
              doc,
              f"Public function {node.name} in {rel_path} missing Google Returns: section",
          )

  def test_acyclic_imports_and_state_machine_decoupled(self):
    """SCAR-ARCH-08: state_machine and extracted modules never import workflow_gates."""
    def _imported_modules(rel_path):
      abs_path = os.path.join(_REPO_ROOT, rel_path)
      with open(abs_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=rel_path)
      mods = set()
      for node in ast.walk(tree):
        if isinstance(node, ast.Import):
          for alias in node.names:
            mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
          mods.add(node.module)
      return mods

    sm_imports = _imported_modules("lib/state_machine.py")
    self.assertNotIn("lib.workflow_gates", sm_imports)
    self.assertIn("lib.main_sync", sm_imports)

    for rel_path in NEW_EXTRACTED_MODULES:
      imports = _imported_modules(rel_path)
      self.assertNotIn(
          "lib.workflow_gates",
          imports,
          f"{rel_path} must not import lib.workflow_gates (SCAR-ARCH-08)",
      )
      for mod in imports:
        self.assertFalse(
            mod.startswith("scripts."),
            f"{rel_path} must not import entrypoint module {mod}",
        )

  def test_governance_registries_include_all_extracted_modules(self):
    """SCAR-TEST-12 / SCAR-PROC-124: all seven extracted modules are governance-protected."""
    from lib.harness_boundary import HARNESS_INTERNAL_RELPATHS
    from lib.pre_tool_guard import GOVERNANCE_FILE_RELPATHS
    from lib.tdd_guard import CORE_GOVERNANCE_FILES

    for rel_path in NEW_EXTRACTED_MODULES:
      self.assertIn(rel_path, GOVERNANCE_FILE_RELPATHS)
      self.assertIn(rel_path, CORE_GOVERNANCE_FILES)
      self.assertIn(rel_path, HARNESS_INTERNAL_RELPATHS)

  def test_workflow_gates_facade_size_reduced(self):
    """Verifies lib/workflow_gates.py is slimmed down to a concise facade."""
    wg_path = os.path.join(_REPO_ROOT, "lib", "workflow_gates.py")
    with open(wg_path, "r", encoding="utf-8") as f:
      line_count = sum(1 for _ in f)
    self.assertLess(
        line_count,
        800,
        f"Expected slimmed lib/workflow_gates.py (< 800 lines), got {line_count}",
    )


if __name__ == "__main__":
  unittest.main()
